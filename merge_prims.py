# merge_prims.py — reference로 로드된 N개 소스를 1번 프림으로 병합
#
#   aplane/
#       이하a            ← dest(prim1) 원본 = slot 1 (그대로 둠)
#       이하a_slot_02    ← prim2의 이하a (invisible로 삽입)
#       이하a_slot_03    ← prim3의 이하a (invisible로 삽입)
#
# - 경계(boundary) 아래의 자식들만 대상. 경계 밖은 건드리지 않는다.
# - dest 원본 자식은 복사/개명/비활성화 없이 그대로 slot 1 역할.
# - 기록은 루트 레이어에만 (Usd.EditContext).
# - 병합 후 prim_paths[1:] 는 통째로 삭제.
#
# 사용 예:
#   BOUNDARIES = ["scene/vec/aplane", "scene/vec/bplane"]
#   count, merged = merge_into_first([p1, p2], BOUNDARIES)
#   set_slot_visible_all(merged, 2, BOUNDARIES)

import omni.usd
from pxr import Usd, UsdGeom, Sdf

SLOT_SUFFIX = "_slot_"       # 자식 이름 뒤 소스 식별자: name_slot_02


def _slot_suffix_index(name: str):
    base, sep, tail = name.rpartition(SLOT_SUFFIX)
    if sep and base and tail.isdigit():
        return int(tail)
    return None


def merge_into_first(prim_paths, boundaries, stage=None, delete_rest=True,
                     timeline=False):
    """반환: (소스 개수, dest 경로). 실패 시 (0, None).
    timeline=True면 병합 후 visibility 타임샘플을 author해서
    타임라인 t=i 에서 슬롯 i만 보이게 한다."""
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

            # 2번째 소스부터: 경계 자식 → dest 경계 아래 name_slot_NN 으로 복제
            for i, root in enumerate(prim_paths[1:], start=2):
                boundary = stage.GetPrimAtPath(f"{root.rstrip('/')}/{rel}")
                if not boundary.IsValid():
                    continue
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
                    UsdGeom.Imageable(
                        stage.GetPrimAtPath(dst)).GetVisibilityAttr().Set(
                        UsdGeom.Tokens.invisible)          # 삽입 시 off, 원본(slot 1)만 보임

        if delete_rest:
            for p in dict.fromkeys(x.rstrip("/") for x in prim_paths[1:]):
                if p != dest and stage.GetPrimAtPath(p).IsValid():
                    stage.RemovePrim(p)

    if timeline:
        author_slot_timeline(dest, boundaries, len(prim_paths), stage)

    return len(prim_paths), dest


# ----------------------------------------------------------------------
# 가시성 (idx 슬롯만 on / suffix 없는 원본 = slot 1)
# ----------------------------------------------------------------------

def set_slot_visible(container_path, idx, stage=None):
    """container(경계) 자식 중 idx 슬롯만 visible.
    suffix 없는 자식은 slot 1로 취급한다."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    container = stage.GetPrimAtPath(container_path)
    if not container.IsValid():
        return
    for child in container.GetChildren():
        imageable = UsdGeom.Imageable(child)
        if not imageable:
            continue
        slot_idx = _slot_suffix_index(child.GetName())
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
# 타임라인 전환 (t=i 에서 슬롯 i만 보임)
# ----------------------------------------------------------------------

def _slot_timecode(idx):
    """슬롯 idx(1..N)의 타임코드 = 정수 프레임 (idx-1).
    타임라인이 정수 프레임으로만 스냅하므로 소수 타임코드는 도달 불가."""
    return float(idx - 1)


def author_slot_timeline(merged_root, boundaries, count, stage=None):
    """슬롯 전환을 타임라인으로: 프레임 (i-1) 에서 슬롯 i만 visible.
    visibility는 어트리뷰트라 타임샘플 가능. suffix 없는 원본 = slot 1."""
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    if stage is None:
        return
    merged_root = merged_root.rstrip("/")

    with Usd.EditContext(stage, Usd.EditTarget(stage.GetRootLayer())):
        for rel in boundaries:
            container = stage.GetPrimAtPath(f"{merged_root}/{rel.strip('/')}")
            if not container.IsValid():
                continue
            for child in container.GetChildren():
                imageable = UsdGeom.Imageable(child)
                if not imageable:
                    continue
                slot_idx = _slot_suffix_index(child.GetName()) or 1   # 원본 = slot 1
                attr = imageable.GetVisibilityAttr()
                for i in range(1, count + 1):
                    t = Usd.TimeCode(_slot_timecode(i))
                    attr.Set(UsdGeom.Tokens.inherited if i == slot_idx
                             else UsdGeom.Tokens.invisible, t)

        # 스테이지 타임 범위를 0 ~ (N-1) 프레임으로 덮음
        stage.SetStartTimeCode(0.0)
        if stage.GetEndTimeCode() < count - 1:
            stage.SetEndTimeCode(float(count - 1))


def set_slot_time(idx, stage=None):
    """타임라인 현재 시간을 슬롯 idx의 프레임(idx-1)으로 이동.
    author_slot_timeline이 적용된 stage에서 set_slot_visible_all 대신 사용.
    IntSlider(min=1, max=N) 콜백용:
        slider.model.add_value_changed_fn(
            lambda m: set_slot_time(m.get_value_as_int()))"""
    import omni.timeline
    if stage is None:
        stage = omni.usd.get_context().get_stage()
    tps = stage.GetTimeCodesPerSecond() if stage else 24.0
    omni.timeline.get_timeline_interface().set_current_time(
        _slot_timecode(idx) / tps)


def set_slot_fraction(frac, count, stage=None):
    """0~1 값(frac)을 슬롯으로 매핑해 타임라인 이동. FloatSlider(0~1)용.
    frac=0 → slot 1, frac=1 → slot N. IntSlider면 set_slot_time을 써라."""
    idx = 1 + int(round(max(0.0, min(1.0, frac)) * (count - 1)))
    set_slot_time(idx, stage)
