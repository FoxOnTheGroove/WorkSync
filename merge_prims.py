# merge_prims.py — reference로 로드된 N개 소스를 1번 프림으로 병합
#
# 구조 가정:
#   /World/Space/LoadPrim/
#       prim1   ← AddReference(assetA)  → scene/vec/aplane/... 합성
#       prim2   ← AddReference(assetB)  → scene/vec/aplane/... 합성
#       ...
#
# 동작:
#   - 경계(boundary) 아래의 "자식들"만 복사 대상. 경계 밖은 일절 건드리지 않는다.
#   - 각 소스 i의 경계 자식들을 dest(prim_paths[0])의 같은 경계 바로 아래에
#     "{원래이름}_slot_{i:02d}" 이름으로 삽입한다. (슬롯 컨테이너 없음)
#       aplane/
#           이하a_slot_01   ← prim1의 이하a (원본 자식은 deactivate)
#           이하a_slot_02   ← prim2의 이하a
#   - dest의 reference는 유지. dest의 원본 경계 자식은 _slot_01 복사 후
#     deactivate 한다 (reference 콘텐츠라 rename 불가).
#   - 복제는 Kit 네이티브(CopyPrim, combine_layers)라 내부 경로 자동 리매핑.
#   - 삽입 전에 visibility를 author하여 _slot_01만 on, 나머지는 off로 들어간다.
#   - 병합 후 prim_paths[1:] 은 통째로 삭제한다.
#   - 모든 기록은 stage 루트(원본) 레이어에만 한다.
#
# 사용 예:
#   BOUNDARIES = ["scene/vec/aplane", "scene/vec/bplane",
#                 "scene/vec/cplane", "scene/line/subline"]
#   count, merged_path = merge_into_first(
#       ["/World/Space/LoadPrim/prim1",
#        "/World/Space/LoadPrim/prim2"],
#       BOUNDARIES,
#   )   # → (2, "/World/Space/LoadPrim/prim1")
#   set_slot_visible_all("/World/Space/LoadPrim/prim1", 2, BOUNDARIES)

import omni.usd
import omni.kit.commands
from pxr import Usd, UsdGeom

SLOT_SUFFIX = "_slot_"       # 자식 이름 뒤에 붙는 소스 식별자: name_slot_01

_MERGING = False             # 재진입 가드 (merge가 일으킨 stage 변화가
                             #  로드감지 로직을 다시 깨워 merge가 또 불리는 것 방지)


def _slot_child_name(name: str, i: int) -> str:
    return f"{name}{SLOT_SUFFIX}{i:02d}"      # ("이하a", 1) → "이하a_slot_01"


def _slot_suffix_index(name: str):
    """이름 끝의 _slot_NN에서 인덱스 추출. 슬롯 복사본이 아니면 None."""
    base, sep, tail = name.rpartition(SLOT_SUFFIX)
    if sep and base and tail.isdigit():
        return int(tail)
    return None


# ----------------------------------------------------------------------
# 병합
# ----------------------------------------------------------------------

def merge_into_first(prim_paths, boundaries, stage=None, delete_rest=True):
    """
    prim_paths  : 소스 prim 경로 리스트 (list[str]). [0]이 병합 목적지.
    boundaries  : 각 prim 기준 경계 노드의 상대경로 (list[str])
    delete_rest : True면 병합 후 prim_paths[1:] 삭제
    반환        : (병합된 소스 개수, 목적지 prim path)  실패 시 (0, None)
    """
    global _MERGING
    if _MERGING:
        print("[merge] 이미 병합 진행 중 — 재진입 차단")
        return 0, None

    if stage is None:
        stage = omni.usd.get_context().get_stage()
    if stage is None:
        print("[merge] stage 없음")
        return 0, None
    if not prim_paths:
        print("[merge] prim_paths 비어있음")
        return 0, None

    dest = prim_paths[0].rstrip("/")

    # dest가 이미 병합된 상태면 스킵 (로드감지 로직이 다시 불러도 안전)
    if _already_merged(stage, dest, boundaries):
        print(f"[merge] 이미 병합됨, 스킵: {dest}")
        return 0, dest

    _MERGING = True
    prev_target = stage.GetEditTarget()
    stage.SetEditTarget(Usd.EditTarget(stage.GetRootLayer()))   # 원본(루트) 레이어에만 기록
    try:
        print(f"[merge] 시작: {len(prim_paths)}개 소스, {len(boundaries)}개 경계")
        result = _do_merge(stage, prim_paths, boundaries, dest, delete_rest)
        print(f"[merge] 완료: {result}")
        return result
    finally:
        stage.SetEditTarget(prev_target)
        _MERGING = False


def _already_merged(stage, dest, boundaries):
    """dest의 경계 아래에 _slot_ 복사본이 하나라도 있으면 병합 완료로 본다."""
    for rel in boundaries:
        boundary = stage.GetPrimAtPath(f"{dest}/{rel.strip('/')}")
        if not boundary.IsValid():
            continue
        for child in boundary.GetChildren():
            if _slot_suffix_index(child.GetName()) is not None:
                return True
    return False


def _do_merge(stage, prim_paths, boundaries, dest, delete_rest):
    # 숨겨진 임시 스코프 (복제본 대기 장소 — invisible이라 화면에 안 비침)
    tmp_root = _unique_path(stage, f"{dest}_mergetmp")
    tmp_xf = UsdGeom.Xform.Define(stage, tmp_root)
    UsdGeom.Imageable(tmp_xf.GetPrim()).GetVisibilityAttr().Set(
        UsdGeom.Tokens.invisible)

    # 1) 복제 — 소스가 살아있는 동안, 각 경계의 "자식들"을 임시 스코프로 복제
    copied = []                                  # (rel, i, child_name, tmp_path)
    for bi, rel in enumerate(boundaries):
        rel = rel.strip("/")
        for i, root in enumerate(prim_paths, start=1):
            boundary = stage.GetPrimAtPath(f"{root.rstrip('/')}/{rel}")
            if not boundary.IsValid():
                print(f"[merge] 경계 없음, 건너뜀: {root}/{rel}")
                continue
            for child in boundary.GetChildren():
                name = child.GetName()
                if _slot_suffix_index(name) is not None:
                    continue                     # 이미 슬롯 복사본이면 제외 (재실행 대비)
                tmp = f"{tmp_root}/b{bi:02d}_s{i:02d}_{name}"
                if _kit_copy(stage, child.GetPath().pathString, tmp):
                    copied.append((rel, i, name, tmp))
                else:
                    print(f"[merge] 복제 실패, 건너뜀: {child.GetPath()}")

    # 2) dest의 원본 경계 자식 deactivate (_slot_01 복사본이 대신한다)
    for rel in boundaries:
        boundary = stage.GetPrimAtPath(f"{dest}/{rel.strip('/')}")
        if not boundary.IsValid():
            continue
        for child in boundary.GetChildren():
            if _slot_suffix_index(child.GetName()) is None and child.IsActive():
                child.SetActive(False)

    # 3) 나머지 소스 통째 삭제 (경계 밖 포함 전부 — dest만 남긴다)
    if delete_rest:
        seen = {dest}                            # dest는 지우면 안 됨 (중복 경로 대비)
        for root in prim_paths[1:]:
            p = root.rstrip("/")
            if p in seen:
                continue
            seen.add(p)
            if stage.GetPrimAtPath(p).IsValid():
                stage.RemovePrim(p)

    # 4) 임시 복제본 → dest 경계 아래로 이동 (visibility 먼저 author)
    for rel, i, name, tmp in copied:
        prim = stage.GetPrimAtPath(tmp)
        if not prim.IsValid():
            continue
        vis = UsdGeom.Tokens.inherited if i == 1 else UsdGeom.Tokens.invisible
        UsdGeom.Imageable(prim).GetVisibilityAttr().Set(vis)   # 1번만 on 상태로 삽입
        _kit_move(tmp, f"{dest}/{rel}/{_slot_child_name(name, i)}")

    # 5) 임시 스코프 정리
    if stage.GetPrimAtPath(tmp_root).IsValid():
        stage.RemovePrim(tmp_root)

    print(f"[merge] 삽입된 슬롯 복사본 {len(copied)}개")
    return len(prim_paths), dest


# ----------------------------------------------------------------------
# 가시성 (idx 슬롯만 on)
# ----------------------------------------------------------------------

def set_slot_visible(container_path, idx, stage=None):
    """container(경계)의 자식 중 _slot_NN 복사본만 대상으로,
    idx만 visible, 나머지는 invisible. suffix 없는 자식은 건드리지 않는다."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    container = stage.GetPrimAtPath(container_path)
    if not container.IsValid():
        print(f"[merge] 컨테이너 없음: {container_path}")
        return
    for child in container.GetChildren():
        slot_idx = _slot_suffix_index(child.GetName())
        if slot_idx is None:
            continue
        vis = (UsdGeom.Tokens.inherited if slot_idx == idx
               else UsdGeom.Tokens.invisible)
        UsdGeom.Imageable(child).GetVisibilityAttr().Set(vis)


def set_slot_visible_all(merged_root, idx, boundaries, stage=None):
    """merged_root 아래 모든 경계의 슬롯에 대해 idx만 on."""
    merged_root = merged_root.rstrip("/")
    for rel in boundaries:
        set_slot_visible(f"{merged_root}/{rel.strip('/')}", idx, stage)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _unique_path(stage, base):
    """base, base_1, base_2 ... 중 비어있는 경로 반환."""
    path, n = base, 0
    while stage.GetPrimAtPath(path).IsValid():
        n += 1
        path = f"{base}_{n}"
    return path


def _kit_copy(stage, path_from, path_to) -> bool:
    """Kit 네이티브 복제. 합성 결과를 굽고 내부 경로를 리매핑한다."""
    try:
        ok, _ = omni.kit.commands.execute(
            "CopyPrim",
            path_from=path_from,
            path_to=path_to,
            exclusive_select=False,
            combine_layers=True,        # 모든 레이어/reference 합성 결과를 하나로
        )
        if ok:
            return True
    except Exception as e:
        print(f"[merge] CopyPrim 실패({e}), duplicate_prim으로 폴백")
    try:
        return bool(omni.usd.duplicate_prim(stage, path_from, path_to))
    except Exception as e:
        print(f"[merge] duplicate_prim 실패: {e}")
        return False


def _kit_move(path_from, path_to) -> bool:
    try:
        ok, _ = omni.kit.commands.execute(
            "MovePrim",
            path_from=path_from,
            path_to=path_to,
            keep_world_transform=False,
        )
        return bool(ok)
    except Exception as e:
        print(f"[merge] MovePrim 실패: {e}")
        return False
