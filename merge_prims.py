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
# use_slot=True (slot_NN Scope, 원본 포함 전부 슬롯):
#   aplane/
#       slot_01/이하a...  ← dest(prim1) 원본도 첫 슬롯에 들어감 (visible)
#       slot_02/이하a...  ← prim2 (invisible)
#   - 경계 서브트리를 이름 그대로 통째로 slot_NN 아래로 복사 → 리맵이 단순
#     prefix 치환으로 끝남(이름 충돌/변형 없음).
#   - 주의: dest 레퍼런스를 지우므로 '경계 밖' 콘텐츠는 사라진다.
#     경계 위 조상(scene/vec 등)의 변환도 보존하지 않는다(조직용 스코프 가정).
#
# - 기록은 루트 레이어에만 (Usd.EditContext).
# - 병합 후 prim_paths[1:] 는 통째로 삭제.
#
# 사용 예:
#   BOUNDARIES = ["scene/vec/aplane", "scene/vec/bplane"]
#   count, merged = merge_into_first([p1, p2], BOUNDARIES)               # suffix 방식
#   count, merged = merge_into_first([p1, p2], BOUNDARIES, use_slot=True) # slot 방식
#   set_slot_visible_all(merged, 2, BOUNDARIES)      # 슬롯 2만 보이기 (둘 다 동일 API)
#
#   # IntSlider 연결:
#   slider.model.add_value_changed_fn(
#       lambda m: set_slot_visible_all(merged, m.get_value_as_int(), BOUNDARIES))

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


def _ensure_slot_skeleton(root_layer, dest, rel):
    """dest~경계 사이 조상 경로를 root layer 에 def Scope 로 확보한다.
    dest 레퍼런스를 지운 뒤에도 경계 경로가 살아있게 하기 위함
    (경계 위 조상의 변환은 보존하지 않는다)."""
    cur = dest.rstrip("/")
    for part in rel.strip("/").split("/"):
        cur = f"{cur}/{part}"
        spec = Sdf.CreatePrimInLayer(root_layer, cur)
        if spec.specifier != Sdf.SpecifierDef:
            spec.specifier = Sdf.SpecifierDef
        if not spec.typeName:
            spec.typeName = "Scope"


def _author_visibility(root_layer, prim_path, visible):
    spec = root_layer.GetPrimAtPath(prim_path)
    if spec is None:
        return
    vis = spec.attributes.get(UsdGeom.Tokens.visibility)
    if vis is None:
        vis = Sdf.AttributeSpec(spec, UsdGeom.Tokens.visibility,
                                Sdf.ValueTypeNames.Token)
    vis.default = UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible


def _copy_boundary_into_slots(flat, root_layer, dest, rel, prim_paths):
    """경계 서브트리를 소스별 slot_NN Scope 로 통째 복사. 원본(prim1)=slot_01,
    prim2=slot_02 ... slot_01 만 보이고 나머지는 invisible."""
    _ensure_slot_skeleton(root_layer, dest, rel)
    for i, root in enumerate(prim_paths, start=1):
        src_boundary = Sdf.Path(f"{root.rstrip('/')}/{rel}")
        if flat.GetPrimAtPath(src_boundary) is None:
            print(f"[merge] flat에 없음: {src_boundary}")
            continue
        slot_path = Sdf.Path(f"{dest}/{rel}/{_slot_name(i)}")
        Sdf.CreatePrimInLayer(root_layer, slot_path)
        Sdf.CopySpec(flat, src_boundary, root_layer, slot_path)   # 경계 노드+자식 통째
        _remap_subtree_paths(root_layer, slot_path,
                             _make_prefix_remap(src_boundary, slot_path))
        _author_visibility(root_layer, slot_path, visible=(i == 1))


def merge_into_first(prim_paths, boundaries, stage=None, delete_rest=True,
                     use_slot=False):
    """반환: (소스 개수, dest 경로). 실패 시 (0, None).

    use_slot=False(기본): 경계 자식을 dest 경계 아래 name_slot_NN 으로 복제,
      dest 원본은 제자리 slot 1. dest 레퍼런스/경계 밖 콘텐츠는 그대로 둔다.
    use_slot=True: 경계 서브트리를 원본 포함 전부 slot_NN Scope 로 처박는다
      (원본=slot_01). dest 레퍼런스를 지우므로 경계 밖 콘텐츠는 사라진다."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    if stage is None or not prim_paths:
        return 0, None

    dest = prim_paths[0].rstrip("/")

    # reference 합성 콘텐츠는 로컬 레이어에 spec이 없어 그대로 복사가 안 됨
    # → 합성 결과를 구운 flat 스냅샷에서 CopySpec으로 가져온다
    flat = stage.Flatten()
    root_layer = stage.GetRootLayer()

    with Usd.EditContext(stage, Usd.EditTarget(root_layer)):
        for rel in [b.strip("/") for b in boundaries]:
            if not stage.GetPrimAtPath(f"{dest}/{rel}").IsValid():
                print(f"[merge] 경계 없음: {dest}/{rel}")
                continue

            if use_slot:
                # 원본 포함 전 소스를 slot_NN Scope 하위로 통째 복사.
                _copy_boundary_into_slots(flat, root_layer, dest, rel, prim_paths)
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

        # use_slot: 원본 직속 자식은 이미 slot_01 로 복사됨 → dest 레퍼런스 제거
        if use_slot:
            dp = stage.GetPrimAtPath(dest)
            if dp.IsValid():
                dp.GetReferences().ClearReferences()
                dp.GetPayloads().ClearPayloads()

        if delete_rest:
            for p in dict.fromkeys(x.rstrip("/") for x in prim_paths[1:]):
                if p != dest and stage.GetPrimAtPath(p).IsValid():
                    stage.RemovePrim(p)

    return len(prim_paths), dest


# ----------------------------------------------------------------------
# 가시성 (idx 슬롯만 on / suffix 없는 원본 = slot 1)
# ----------------------------------------------------------------------

def set_slot_visible(container_path, idx, stage=None):
    """container(경계) 자식 중 idx 슬롯만 visible.
    slot_NN Scope(use_slot=True)면 그 인덱스로, 아니면 name_slot_NN suffix로
    판정한다(suffix 없는 자식은 slot 1로 취급)."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    container = stage.GetPrimAtPath(container_path)
    if not container.IsValid():
        return
    children = list(container.GetChildren())
    # slot_NN Scope 자식이 하나라도 있으면 slot 모드로 취급.
    slot_mode = any(_slot_prefix_index(c.GetName()) is not None for c in children)
    for child in children:
        imageable = UsdGeom.Imageable(child)
        if not imageable:
            continue
        if slot_mode:
            slot_idx = _slot_prefix_index(child.GetName())
            if slot_idx is None:
                continue                        # slot_NN 아닌 잔여물은 안 건드림
        else:
            slot_idx = _slot_suffix_index(child.GetName())
            if slot_idx is None:
                slot_idx = 1                    # 원본 = slot 1 (suffix 모드)
        vis = (UsdGeom.Tokens.inherited if slot_idx == idx
               else UsdGeom.Tokens.invisible)
        imageable.GetVisibilityAttr().Set(vis)


def set_slot_visible_all(merged_root, idx, boundaries, stage=None):
    merged_root = merged_root.rstrip("/")
    for rel in boundaries:
        set_slot_visible(f"{merged_root}/{rel.strip('/')}", idx, stage)
