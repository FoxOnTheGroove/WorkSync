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
#     dest/<경계>/slot_01, /slot_02, ... 슬롯으로 복사한다.
#     (USD prim 이름은 숫자로 시작 불가 → "01"이 아니라 "slot_01")
#   - reference로 합성된 콘텐츠라 stage.Flatten()으로 한 번 구운 뒤 복사한다.
#   - dest의 원본 reference는 제거하고 스켈레톤(경계 상위 경로)을 재생성한다.
#   - 병합 후 prim_paths[1:] 은 삭제한다 (dest와 같은 경로는 제외).
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

SLOT_PREFIX = "slot_"        # USD prim 이름은 숫자로 시작할 수 없어 접두어를 붙인다


def _slot_name(i: int) -> str:
    return f"{SLOT_PREFIX}{i:02d}"        # 1 → "slot_01"


def _slot_index(name: str):
    """슬롯 이름에서 인덱스 추출. 슬롯이 아니면 None."""
    if name.startswith(SLOT_PREFIX):
        tail = name[len(SLOT_PREFIX):]
        if tail.isdigit():
            return int(tail)
    return None


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
            slot = f"{dest}/{rel}/{_slot_name(i)}"   # slot_01, slot_02, ... (경계 노드째 복사)
            # flat(오프라인 스냅샷) 쪽에 visibility를 미리 박아 넣고 복사
            # → 슬롯이 처음부터 1번만 on, 나머지는 off 상태로 삽입된다
            _author_visibility(flat, src_boundary, visible=(i == 1))
            _copy_from(flat, edit, src_boundary, slot)

    if delete_rest:
        seen = {dest}                                # dest는 지우면 안 됨 (중복 경로 대비)
        for root in prim_paths[1:]:
            p = root.rstrip("/")
            if p in seen:
                continue
            seen.add(p)
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
        slot_idx = _slot_index(child.GetName())
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


def _author_visibility(layer, prim_path, visible):
    """레이어의 prim spec에 visibility default를 직접 author."""
    spec = layer.GetPrimAtPath(prim_path)
    if spec is None:
        return
    attr = layer.GetAttributeAtPath(f"{prim_path}.visibility")
    if attr is None:
        attr = Sdf.AttributeSpec(spec, "visibility", Sdf.ValueTypeNames.Token)
    attr.default = "inherited" if visible else "invisible"
