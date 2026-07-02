# merge_prims.py — reference로 로드된 N개 소스를 1번 프림으로 병합 (Bake 방식)
#
# 구조 가정:
#   /World/Space/LoadPrim/
#       prim1   ← AddReference(assetA)  → scene/vec/aplane/... 합성
#       prim2   ← AddReference(assetB)  → scene/vec/aplane/... 합성
#       ...
#   asset은 같아도 달라도 각각 독립(different)으로 처리한다.
#
# 동작:
#   - prim_paths[0](1번)을 목적지로, 각 소스의 경계 노드(트랜스폼+"이하" 통째)를
#     dest/<경계>/01, /02, ... 슬롯으로 복사한다.
#   - reference로 합성된 콘텐츠라 stage.Flatten()으로 한 번 구운 뒤 복사한다.
#   - dest의 원본 reference는 제거하고 스켈레톤(경계 상위 경로)을 재생성한다.
#   - 병합 후 prim_paths[1:] 은 삭제한다.
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
from pxr import UsdGeom, Sdf


# ----------------------------------------------------------------------
# 병합 (Bake)
# ----------------------------------------------------------------------

def merge_into_first(prim_paths, boundaries, stage=None, delete_rest=True):
    """
    prim_paths  : 소스 prim 경로 리스트 (list[str]). [0]이 병합 목적지.
    boundaries  : 각 prim 기준 경계 노드의 상대경로 (list[str])
    delete_rest : True면 병합 후 prim_paths[1:] 삭제
    반환        : (병합된 소스 개수, 목적지 prim path)  실패 시 (0, None)
    """
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    if stage is None:
        print("[merge] stage 없음")
        return 0, None
    if not prim_paths:
        print("[merge] prim_paths 비어있음")
        return 0, None

    flat = stage.Flatten()                       # 모든 reference를 concrete spec으로 bake
    edit = stage.GetEditTarget().GetLayer()
    dest = prim_paths[0].rstrip("/")

    # dest 원본 reference/payload 제거 (원본 합성 콘텐츠 정리) — flat은 별도 스냅샷이라 안전
    dp = stage.GetPrimAtPath(dest)
    if dp.IsValid():
        dp.GetReferences().ClearReferences()
        dp.GetPayloads().ClearPayloads()

    for rel in boundaries:
        rel = rel.strip("/")
        _ensure_skeleton(stage, f"{dest}/{rel}")     # 스켈레톤(경계 포함) Xform 생성

        for i, root in enumerate(prim_paths, start=1):
            src_boundary = f"{root.rstrip('/')}/{rel}"
            if flat.GetPrimAtPath(src_boundary) is None:
                print(f"[merge] flat에 경계 없음, 건너뜀: {src_boundary}")
                continue
            slot = f"{dest}/{rel}/{i:02d}"           # 01, 02, ... (경계 노드째 복사)
            _copy_from(flat, edit, src_boundary, slot)

    if delete_rest:
        for root in prim_paths[1:]:
            p = root.rstrip("/")
            if stage.GetPrimAtPath(p).IsValid():
                stage.RemovePrim(p)

    return len(prim_paths), dest


# ----------------------------------------------------------------------
# 가시성 (idx 슬롯만 on)
# ----------------------------------------------------------------------

def set_slot_visible(container_path, idx, stage=None):
    """container의 자식 번호 슬롯 중 idx만 visible, 나머지는 invisible."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    container = stage.GetPrimAtPath(container_path)
    if not container.IsValid():
        print(f"[merge] 컨테이너 없음: {container_path}")
        return
    for child in container.GetChildren():
        name = child.GetName()
        if not name.isdigit():
            continue
        vis = (UsdGeom.Tokens.inherited if int(name) == idx
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

def _ensure_skeleton(stage, path):
    """/a/b/c 경로의 각 조상 prim을 Xform으로 생성 (이미 있으면 유지)."""
    cur = ""
    for part in path.strip("/").split("/"):
        cur = f"{cur}/{part}"
        if not stage.GetPrimAtPath(cur).IsValid():
            UsdGeom.Xform.Define(stage, cur)


def _copy_from(src_layer, dst_layer, src_path, dst_path):
    """src_layer(flat)의 서브트리를 dst_layer(edit)로 복사."""
    Sdf.CreatePrimInLayer(dst_layer, dst_path)       # 조상 spec 확보
    Sdf.CopySpec(src_layer, src_path, dst_layer, dst_path)
