# merge_prims.py — reference로 로드된 N개 소스를 1번 프림으로 병합
#
# use_slot=False (기본, name_slot_NN suffix):
#   aplane/
#       이하a            ← dest(prim1) 원본 = slot 1 (그대로 둠)
#       이하a_slot_02    ← prim2의 이하a (invisible로 삽입)
#       이하a_slot_03    ← prim3의 이하a (invisible로 삽입)
#   - 경계 아래 자식들만 대상. 경계 밖/ dest 레퍼런스는 안 건드림.
#   - dest 원본 자식은 복사/개명 없이 그대로 slot 1.
#
# use_slot=True (slot_NN Xform 컨테이너):
#   aplane/
#       이하a            ← dest(prim1) 원본 = slot 1 (그대로 둠)
#       slot_02/이하a    ← prim2의 이하a (invisible Xform 하위)
#       slot_03/이하a    ← prim3의 이하a (invisible Xform 하위)
#   - suffix 방식과 흐름/부작용 완전 동일. 복사본을 name_slot_NN(형제) 대신
#     slot_NN Xform 컨테이너 아래에 넣기만 한다(이름 안 바꿈 → 리맵은 prefix 치환).
#   - dest 레퍼런스/경계 스켈레톤 안 건드림 → Fabric 안전. 원본은 제자리 slot 1.
#
# - 기록은 루트 레이어에만 (Usd.EditContext).
# - eager 병합은 prim_paths[1:] 를 통째로 삭제.
#
# 지연 로딩 (SlotLoader) — N개 한꺼번에 로드가 무거울 때:
#   메인 프림(=slot 1)만 로드해 두고, 2..N 은 USD 경로만 받아 둔다. 슬라이더
#   committed(k) 때 slot k 가 아직이면 {main}/__slotload_NN 에 비동기 로드하고
#   각 경계를 slot_k 로 복사(계층 분리) 후 임시 로드본 삭제. 이미 있으면 토글만.
#   구조/부작용은 use_slot 과 동일(검증된 복사 경로) → Fabric 안전.
#     loader = SlotLoader(main_prim, usd_paths, BOUNDARIES,
#                         load_fn=myclass.usdload, busy_fn=set_slider_enabled)
#     slider.on_change_committed(loader.show_slot)     # k=1..N
#
# 사용 예 (eager):
#   BOUNDARIES = ["scene/vec/aplane", "scene/vec/bplane"]
#   count, merged = merge_into_first([p1, p2], BOUNDARIES)               # suffix 방식
#   count, merged = merge_into_first([p1, p2], BOUNDARIES, use_slot=True) # slot 방식
#   set_slot_visible_all(merged, 2, BOUNDARIES)      # 슬롯 2만 보이기 (둘 다 동일 API)
#
#   # IntSlider 연결:
#   slider.model.add_value_changed_fn(
#       lambda m: set_slot_visible_all(merged, m.get_value_as_int(), BOUNDARIES))

import asyncio

import omni.usd
from pxr import Usd, UsdGeom, Sdf

SLOT_SUFFIX = "_slot_"       # 자식 이름 뒤 소스 식별자: name_slot_02


def _slot_suffix_index(name: str):
    base, sep, tail = name.rpartition(SLOT_SUFFIX)
    if sep and base and tail.isdigit():
        return int(tail)
    return None


def _make_boundary_remap(src_boundary, dst_boundary, slot_idx):
    """경계(boundary) 하위를 가리키는 target 경로를 dst 경계 하위 대응 경로로
    바꾸는 함수를 만든다.

    CopySpec은 스펙만 복사하고 relationship/connection target 의 절대경로는
    그대로 둔다. mesh 의 material:binding 은 보통 형제 스코프(예:
    world/원본/경계/Looks/Mat)를 가리키는데, 각 경계 직속 자식은 복사 시
    이름에 _slot_NN 이 붙어(Looks → Looks_slot_02) 옮겨진다. 따라서 단순 prefix
    치환이 아니라, 경계 바로 아래 첫 컴포넌트에 그 suffix 를 끼워 재작성해야
    바인딩이 유지된다(안 그러면 언바인딩되어 회색으로 나옴).

        world/원본/경계/Looks/Mat  →  dest/경계/Looks_slot_02/Mat
    """
    src_str = src_boundary.pathString

    def _remap(path):
        if not path.HasPrefix(src_boundary):
            return path
        if path == src_boundary:
            return dst_boundary
        # 경계 바로 아래 첫 컴포넌트 이름(형제 스코프명, 예: Looks) 추출.
        # 프로퍼티 target 도 있으므로 prim 경로 기준으로 자른다.
        remainder = path.GetPrimPath().pathString[len(src_str) + 1:]
        if not remainder:
            return path
        first     = remainder.split("/", 1)[0]
        src_child = src_boundary.AppendChild(first)
        dst_child = dst_boundary.AppendChild(f"{first}{SLOT_SUFFIX}{slot_idx:02d}")
        return path.ReplacePrefix(src_child, dst_child)

    return _remap


def _remap_subtree_paths(layer, prim_path, remap):
    """dst 서브트리를 순회하며 relationship target 과 attribute connection 을
    remap 함수로 재작성한다."""
    prim_spec = layer.GetPrimAtPath(prim_path)
    if prim_spec is None:
        return

    for rel_spec in prim_spec.relationships.values():
        items = rel_spec.targetPathList.explicitItems
        remapped = [remap(p) for p in items]
        if remapped != list(items):
            rel_spec.targetPathList.explicitItems[:] = remapped

    for attr_spec in prim_spec.attributes.values():
        items = attr_spec.connectionPathList.explicitItems
        remapped = [remap(p) for p in items]
        if remapped != list(items):
            attr_spec.connectionPathList.explicitItems[:] = remapped

    for child in prim_spec.nameChildren:
        _remap_subtree_paths(layer, child.path, remap)


# ----------------------------------------------------------------------
# use_slot 모드 헬퍼: 경계 서브트리를 통째로 slot_NN Scope 하위로 복사
# ----------------------------------------------------------------------

SLOT_PREFIX = "slot_"        # slot Scope 이름: slot_01, slot_02 ...


def _slot_name(i: int) -> str:
    return f"{SLOT_PREFIX}{i:02d}"


def _slot_prefix_index(name: str):
    if name.startswith(SLOT_PREFIX):
        tail = name[len(SLOT_PREFIX):]
        if tail.isdigit():
            return int(tail)
    return None


def _make_prefix_remap(src_prefix, dst_prefix):
    """src_prefix 하위 target 을 dst_prefix 하위로 단순 치환하는 함수.
    경계 서브트리를 이름 그대로 slot_NN 아래로 옮기므로, 이름 변형이 없어
    prefix 치환 하나로 형제 스코프·중첩·프로퍼티 target 까지 균일 처리된다."""
    def _remap(path):
        return path.ReplacePrefix(src_prefix, dst_prefix) if path.HasPrefix(src_prefix) else path
    return _remap


def _fill_slot(stage, flat, root_layer, dest, rel, i, src_root):
    """src_root 아래 경계(rel) 자식들을 dest 경계 아래 slot_i Xform 하위로 복사한다
    (slot_i 는 invisible). 이름 그대로 통째로 내리므로 리맵은 prefix 치환.
    반환: 복사 대상 경계가 있었는지(True/False)."""
    src_boundary = Sdf.Path(f"{src_root.rstrip('/')}/{rel}")
    boundary = stage.GetPrimAtPath(src_boundary)
    if not boundary.IsValid():
        return False
    slot_path = Sdf.Path(f"{dest}/{rel}/{_slot_name(i)}")
    slot_prim = UsdGeom.Xform.Define(stage, slot_path)              # 컨테이너
    UsdGeom.Imageable(slot_prim).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    remap = _make_prefix_remap(src_boundary, slot_path)
    for child in list(boundary.GetChildren()):
        name = child.GetName()
        if _slot_prefix_index(name) is not None:
            continue
        src = str(child.GetPath())
        if flat.GetPrimAtPath(src) is None:
            print(f"[merge] flat에 없음: {src}")
            continue
        dst = f"{slot_path}/{name}"
        Sdf.CreatePrimInLayer(root_layer, dst)
        Sdf.CopySpec(flat, src, root_layer, dst)
        _remap_subtree_paths(root_layer, dst, remap)
    return True


def _merge_slots(stage, flat, root_layer, dest, prim_paths, rels, delete_rest):
    """use_slot=True(eager) 경로. 복사본을 name_slot_NN(형제)이 아니라 slot_NN
    Xform 컨테이너 아래에 넣는다. dest 원본은 제자리 = slot 1, prim2..N=slot_02..NN.
    dest 레퍼런스/경계 스켈레톤 안 건드림(Fabric 안전)."""
    with Usd.EditContext(stage, Usd.EditTarget(root_layer)):
        for rel in rels:
            if not stage.GetPrimAtPath(f"{dest}/{rel}").IsValid():
                print(f"[merge] 경계 없음: {dest}/{rel}")
                continue
            for i, root in enumerate(prim_paths[1:], start=2):
                _fill_slot(stage, flat, root_layer, dest, rel, i, root)

        if delete_rest:
            for p in dict.fromkeys(x.rstrip("/") for x in prim_paths[1:]):
                if p != dest and stage.GetPrimAtPath(p).IsValid():
                    stage.RemovePrim(p)
    return len(prim_paths), dest


def merge_into_first(prim_paths, boundaries, stage=None, delete_rest=True,
                     use_slot=False):
    """반환: (소스 개수, dest 경로). 실패 시 (0, None).

    use_slot=False(기본): 경계 자식을 dest 경계 아래 name_slot_NN(형제)으로 복제.
    use_slot=True: 같은 걸 slot_NN Xform 컨테이너 아래에 넣는다(이름 안 바꿈).
    둘 다 dest 원본은 제자리 slot 1, dest 레퍼런스/경계 밖은 안 건드린다."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    if stage is None or not prim_paths:
        return 0, None

    dest = prim_paths[0].rstrip("/")

    # reference 합성 콘텐츠는 로컬 레이어에 spec이 없어 그대로 복사가 안 됨
    # → 합성 결과를 구운 flat 스냅샷에서 CopySpec으로 가져온다
    flat = stage.Flatten()
    root_layer = stage.GetRootLayer()
    rels = [b.strip("/") for b in boundaries]

    if use_slot:
        return _merge_slots(stage, flat, root_layer, dest, prim_paths, rels,
                            delete_rest)

    with Usd.EditContext(stage, Usd.EditTarget(root_layer)):
        for rel in rels:
            if not stage.GetPrimAtPath(f"{dest}/{rel}").IsValid():
                print(f"[merge] 경계 없음: {dest}/{rel}")
                continue

            # 2번째 소스부터: 경계 자식 → dest 경계 아래 name_slot_NN 으로 복제
            for i, root in enumerate(prim_paths[1:], start=2):
                src_boundary = Sdf.Path(f"{root.rstrip('/')}/{rel}")
                dst_boundary = Sdf.Path(f"{dest}/{rel}")
                boundary = stage.GetPrimAtPath(src_boundary)
                if not boundary.IsValid():
                    continue
                # 경계 하위 target(형제 Looks 등 포함)을 dst 경계 하위로 리매핑.
                remap = _make_boundary_remap(src_boundary, dst_boundary, i)
                for child in list(boundary.GetChildren()):
                    name = child.GetName()
                    if _slot_suffix_index(name) is not None:
                        continue
                    src = str(child.GetPath())
                    if flat.GetPrimAtPath(src) is None:
                        print(f"[merge] flat에 없음: {src}")
                        continue
                    dst = f"{dest}/{rel}/{name}{SLOT_SUFFIX}{i:02d}"
                    Sdf.CreatePrimInLayer(root_layer, dst)
                    Sdf.CopySpec(flat, src, root_layer, dst)
                    _remap_subtree_paths(root_layer, dst, remap)
                    UsdGeom.Imageable(
                        stage.GetPrimAtPath(dst)).GetVisibilityAttr().Set(
                        UsdGeom.Tokens.invisible)          # 삽입 시 off, 원본(slot 1)만 보임

        if delete_rest:
            for p in dict.fromkeys(x.rstrip("/") for x in prim_paths[1:]):
                if p != dest and stage.GetPrimAtPath(p).IsValid():
                    stage.RemovePrim(p)

    return len(prim_paths), dest


# ----------------------------------------------------------------------
# 가시성 (idx 슬롯만 on / suffix 없는 원본 = slot 1)
# ----------------------------------------------------------------------

def _child_slot_index(name: str):
    """자식 이름에서 슬롯 번호. slot_NN Xform(use_slot=True) / name_slot_NN
    suffix(기본) 둘 다 인식. 어느 쪽도 아니면 None(=원본, slot 1)."""
    idx = _slot_prefix_index(name)              # slot_NN Xform
    return idx if idx is not None else _slot_suffix_index(name)   # name_slot_NN


def set_slot_visible(container_path, idx, stage=None):
    """container(경계) 자식 중 idx 슬롯만 visible.
    slot_NN Xform / name_slot_NN suffix 둘 다 인식하고, 어느 쪽도 아닌 원본
    자식은 slot 1로 취급한다."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    container = stage.GetPrimAtPath(container_path)
    if not container.IsValid():
        return
    for child in container.GetChildren():
        imageable = UsdGeom.Imageable(child)
        if not imageable:
            continue
        slot_idx = _child_slot_index(child.GetName())
        if slot_idx is None:
            slot_idx = 1                        # 원본 = slot 1
        vis = (UsdGeom.Tokens.inherited if slot_idx == idx
               else UsdGeom.Tokens.invisible)
        imageable.GetVisibilityAttr().Set(vis)


def set_slot_visible_all(merged_root, idx, boundaries, stage=None):
    merged_root = merged_root.rstrip("/")
    for rel in boundaries:
        set_slot_visible(f"{merged_root}/{rel.strip('/')}", idx, stage)


# ----------------------------------------------------------------------
# 지연 로딩 슬롯 (N개 한꺼번에 로드가 무거워, 메인 프림만 두고 2..N은 온디맨드)
# ----------------------------------------------------------------------

class SlotLoader:
    """슬라이더 change_committed(동기)에 show_slot(k) 를 연결해 쓴다.

      main_prim  : 1번 메인 프림 경로. 이미 로드됨 = slot 1 (제자리, 복사 안 함).
      usd_paths  : 1..N USD 경로 리스트 (usd_paths[0]=slot1은 이미 로드라 안 씀).
      boundaries : 경계 상대경로 리스트.
      load_fn    : async load_fn(usd_path, target_path) — target_path 에 USD 로드(await).
                   예) myclass.usdload.  로드만 하면 됨(반환값 안 씀).
      busy_fn    : (선택) busy_fn(bool) — 로드 시작 True / 끝 False.
                   UI가 로드 중 슬라이더를 잠그는 용도.

    슬롯 번호(1-based): 1 = main_prim, k(>=2) = usd_paths[k-1].
    slot k 첫 방문 시 {main_prim}/__slotload_NN 에 로드 → 각 경계를 slot_k 로 복사 →
    임시 로드본 삭제. 이후엔 visibility 토글만. 로드 진행 중엔 새 요청을 무시(차단)."""

    def __init__(self, main_prim, usd_paths, boundaries, load_fn,
                 busy_fn=None, stage=None):
        self._dest = main_prim.rstrip("/")
        self._usd_paths = list(usd_paths)
        self._boundaries = [b.strip("/") for b in boundaries]
        self._load_fn = load_fn
        self._busy_fn = busy_fn
        self._stage = stage or omni.usd.get_context().get_stage()
        self._loaded = {1}          # slot 1 = main_prim 이미 존재
        self._busy = False
        self._prebuild_slots()

    @property
    def slot_count(self):
        return len(self._usd_paths)

    def is_loaded(self, k) -> bool:
        return k in self._loaded

    @property
    def busy(self) -> bool:
        return self._busy

    def _prebuild_slots(self):
        # 빈 slot_NN Xform(invisible)을 각 경계 아래 미리 만들어 둔다(2..N).
        stage = self._stage
        if stage is None:
            return
        root_layer = stage.GetRootLayer()
        with Usd.EditContext(stage, Usd.EditTarget(root_layer)):
            for rel in self._boundaries:
                if not stage.GetPrimAtPath(f"{self._dest}/{rel}").IsValid():
                    continue
                for k in range(2, self.slot_count + 1):
                    slot_path = f"{self._dest}/{rel}/{_slot_name(k)}"
                    if not stage.GetPrimAtPath(slot_path).IsValid():
                        p = UsdGeom.Xform.Define(stage, slot_path)
                        UsdGeom.Imageable(p).GetVisibilityAttr().Set(
                            UsdGeom.Tokens.invisible)

    def show_slot(self, k):
        """슬라이더 committed 콜백(동기). 로드 중이면 무시(차단).
        이미 로드된 슬롯은 즉시 토글, 아니면 비동기 로드 후 토글."""
        if self._busy:
            return
        if k in self._loaded:
            set_slot_visible_all(self._dest, k, self._boundaries, self._stage)
            return
        if k < 2 or k > self.slot_count:
            return
        asyncio.ensure_future(self._load_and_show(k))

    async def _load_and_show(self, k):
        self._busy = True
        if self._busy_fn:
            self._busy_fn(True)
        try:
            target = f"{self._dest}/__slotload_{k:02d}"
            await self._load_fn(self._usd_paths[k - 1], target)   # 외부 async 로드
            self._fill_from_target(k, target)
            self._loaded.add(k)
            set_slot_visible_all(self._dest, k, self._boundaries, self._stage)
        except Exception as e:
            import traceback
            print(f"[SlotLoader] slot {k} 로드/복사 실패: {e}")
            traceback.print_exc()          # 정확한 파일:줄 확인용
        finally:
            self._busy = False
            if self._busy_fn:
                self._busy_fn(False)

    def _fill_from_target(self, k, target):
        # target 아래 각 경계를 slot_k 로 복사한 뒤 target(임시 로드본) 삭제.
        stage = self._stage
        flat = stage.Flatten()                       # 방금 로드된 target 포함해 굽기
        root_layer = stage.GetRootLayer()
        with Usd.EditContext(stage, Usd.EditTarget(root_layer)):
            for rel in self._boundaries:
                if not stage.GetPrimAtPath(f"{self._dest}/{rel}").IsValid():
                    print(f"[SlotLoader] 경계 없음: {self._dest}/{rel}")
                    continue
                _fill_slot(stage, flat, root_layer, self._dest, rel, k, target)
            if stage.GetPrimAtPath(target).IsValid():
                stage.RemovePrim(target)             # 복사 후 임시 원본 삭제
