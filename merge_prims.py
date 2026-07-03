# merge_prims.py — reference로 로드된 N개 소스를 1번 프림으로 병합 (Kit 네이티브 복제 방식)
#
# 구조 가정:
#   /World/Space/LoadPrim/
#       prim1   ← AddReference(assetA)  → scene/vec/aplane/... 합성
#       prim2   ← AddReference(assetB)  → scene/vec/aplane/... 합성
#       ...
#   asset은 같아도 달라도 각각 독립(different)으로 처리한다.
#
# 동작 (순서가 핵심):
#   1) 소스가 전부 살아있는 동안, 각 경계 노드를 Kit 네이티브 복제(CopyPrim)로
#      숨겨진 임시 스코프 아래에 복제한다.
#      → reference 합성 결과가 그대로 구워지고, material:binding 등
#        서브트리 내부 경로는 Kit이 자동 리매핑한다. (fabric-safe)
#   2) dest(prim_paths[0])의 reference/payload 제거, 나머지 소스 삭제.
#   3) 스켈레톤(경계 상위 경로) 재생성 후, 임시 복제본을 슬롯
#      (slot_01, slot_02, ...)으로 이동. 이동 직전에 visibility를 author하여
#      1번만 on, 나머지는 off 상태로 삽입된다.
#   4) 임시 스코프 삭제.
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
from pxr import UsdGeom

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
# 병합
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

    dest = prim_paths[0].rstrip("/")

    # 숨겨진 임시 스코프 (복제본 대기 장소 — invisible이라 화면에 안 비침)
    tmp_root = _unique_path(stage, f"{dest}_mergetmp")
    tmp_xf = UsdGeom.Xform.Define(stage, tmp_root)
    UsdGeom.Imageable(tmp_xf.GetPrim()).GetVisibilityAttr().Set(
        UsdGeom.Tokens.invisible)

    # 1) 복제 — 소스가 살아있는 동안 Kit 네이티브 복제 (내부 바인딩 자동 리매핑)
    copied = []                                  # (rel, i, tmp_path)
    for bi, rel in enumerate(boundaries):
        rel = rel.strip("/")
        for i, root in enumerate(prim_paths, start=1):
            src = f"{root.rstrip('/')}/{rel}"
            if not stage.GetPrimAtPath(src).IsValid():
                print(f"[merge] 경계 없음, 건너뜀: {src}")
                continue
            tmp = f"{tmp_root}/b{bi:02d}_s{i:02d}"
            if _kit_copy(stage, src, tmp):
                copied.append((rel, i, tmp))
            else:
                print(f"[merge] 복제 실패, 건너뜀: {src}")

    # 2) dest reference/payload 제거 + 나머지 소스 삭제
    dp = stage.GetPrimAtPath(dest)
    if dp.IsValid():
        dp.GetReferences().ClearReferences()
        dp.GetPayloads().ClearPayloads()

    if delete_rest:
        seen = {dest}                            # dest는 지우면 안 됨 (중복 경로 대비)
        for root in prim_paths[1:]:
            p = root.rstrip("/")
            if p in seen:
                continue
            seen.add(p)
            if stage.GetPrimAtPath(p).IsValid():
                stage.RemovePrim(p)

    # 3) 스켈레톤 재생성 + 임시 복제본 → 슬롯 이동 (visibility 먼저 author)
    for rel, i, tmp in copied:
        _ensure_skeleton(stage, f"{dest}/{rel}")
        prim = stage.GetPrimAtPath(tmp)
        if not prim.IsValid():
            continue
        vis = UsdGeom.Tokens.inherited if i == 1 else UsdGeom.Tokens.invisible
        UsdGeom.Imageable(prim).GetVisibilityAttr().Set(vis)   # 1번만 on 상태로 삽입
        slot = f"{dest}/{rel}/{_slot_name(i)}"
        _kit_move(tmp, slot)

    # 4) 임시 스코프 정리
    if stage.GetPrimAtPath(tmp_root).IsValid():
        stage.RemovePrim(tmp_root)

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

def _unique_path(stage, base):
    """base, base_1, base_2 ... 중 비어있는 경로 반환."""
    path, n = base, 0
    while stage.GetPrimAtPath(path).IsValid():
        n += 1
        path = f"{base}_{n}"
    return path


def _ensure_skeleton(stage, path):
    """/a/b/c 경로의 각 조상 prim을 Xform으로 생성 (이미 있으면 유지)."""
    cur = ""
    for part in path.strip("/").split("/"):
        cur = f"{cur}/{part}"
        if not stage.GetPrimAtPath(cur).IsValid():
            UsdGeom.Xform.Define(stage, cur)


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
