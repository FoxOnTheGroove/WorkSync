# merge_prims.py — reference로 로드된 N개 소스를 1번 프림으로 병합
#
#   aplane/
#       이하a            ← dest 원본 (deactivate)
#       이하a_slot_01    ← prim1의 이하a
#       이하a_slot_02    ← prim2의 이하a
#
# - 경계(boundary) 아래의 자식들만 대상. 경계 밖은 건드리지 않는다.
# - 기록은 루트 레이어에만 (Usd.EditContext).
# - 병합 후 prim_paths[1:] 는 통째로 삭제.
#
# 사용 예:
#   BOUNDARIES = ["scene/vec/aplane", "scene/vec/bplane"]
#   count, merged = merge_into_first([p1, p2], BOUNDARIES)
#   set_slot_visible_all(merged, 2, BOUNDARIES)

import omni.usd
from pxr import Usd, UsdGeom

SLOT_SUFFIX = "_slot_"       # 자식 이름 뒤 소스 식별자: name_slot_01


def _slot_suffix_index(name: str):
    base, sep, tail = name.rpartition(SLOT_SUFFIX)
    if sep and base and tail.isdigit():
        return int(tail)
    return None


def merge_into_first(prim_paths, boundaries, stage=None, delete_rest=True):
    """반환: (소스 개수, dest 경로). 실패 시 (0, None)."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    if stage is None or not prim_paths:
        return 0, None

    dest = prim_paths[0].rstrip("/")

    with Usd.EditContext(stage, Usd.EditTarget(stage.GetRootLayer())):
        for rel in [b.strip("/") for b in boundaries]:
            dest_boundary = stage.GetPrimAtPath(f"{dest}/{rel}")
            if not dest_boundary.IsValid():
                print(f"[merge] 경계 없음: {dest}/{rel}")
                continue
            originals = list(dest_boundary.GetChildren())     # 복사 전 스냅샷

            # 각 소스의 경계 자식 → dest 경계 아래 name_slot_NN 으로 복제
            for i, root in enumerate(prim_paths, start=1):
                boundary = stage.GetPrimAtPath(f"{root.rstrip('/')}/{rel}")
                if not boundary.IsValid():
                    continue
                for child in list(boundary.GetChildren()):
                    name = child.GetName()
                    if _slot_suffix_index(name) is not None:
                        continue
                    dst = f"{dest}/{rel}/{name}{SLOT_SUFFIX}{i:02d}"
                    if not omni.usd.duplicate_prim(
                            stage, str(child.GetPath()), dst):
                        print(f"[merge] 복제 실패: {child.GetPath()}")
                        continue
                    vis = (UsdGeom.Tokens.inherited if i == 1
                           else UsdGeom.Tokens.invisible)
                    UsdGeom.Imageable(
                        stage.GetPrimAtPath(dst)).GetVisibilityAttr().Set(vis)

            # dest 원본 자식은 deactivate (_slot_01 복사본이 대신)
            for child in originals:
                if _slot_suffix_index(child.GetName()) is None:
                    child.SetActive(False)

        if delete_rest:
            for p in dict.fromkeys(x.rstrip("/") for x in prim_paths[1:]):
                if p != dest and stage.GetPrimAtPath(p).IsValid():
                    stage.RemovePrim(p)

    return len(prim_paths), dest


# ----------------------------------------------------------------------
# 가시성 (idx 슬롯만 on)
# ----------------------------------------------------------------------

def set_slot_visible(container_path, idx, stage=None):
    """container(경계)의 _slot_NN 자식 중 idx만 visible, 나머지 invisible."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    container = stage.GetPrimAtPath(container_path)
    if not container.IsValid():
        return
    for child in container.GetChildren():
        slot_idx = _slot_suffix_index(child.GetName())
        if slot_idx is None:
            continue
        vis = (UsdGeom.Tokens.inherited if slot_idx == idx
               else UsdGeom.Tokens.invisible)
        UsdGeom.Imageable(child).GetVisibilityAttr().Set(vis)


def set_slot_visible_all(merged_root, idx, boundaries, stage=None):
    merged_root = merged_root.rstrip("/")
    for rel in boundaries:
        set_slot_visible(f"{merged_root}/{rel.strip('/')}", idx, stage)
