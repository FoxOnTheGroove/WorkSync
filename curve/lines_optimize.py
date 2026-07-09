"""streamline USD 최적화 핵심 로직.

수많은 개별 line renderer(BasisCurves) prim으로 이루어진 streamline USD는
prim 개수 = draw call 개수가 되어 뷰포트가 심하게 버벅인다.
여기서는 소스 USD의 모든 곡선을 읽어 world 공간 좌표로 변환한 뒤,
curveVertexCounts 를 이용해 **단일 BasisCurves prim** 하나로 병합한다.
(선택적으로 Ramer-Douglas-Peucker 데시메이션으로 정점 수도 줄인다.)

모든 실질 구현은 이 파일에 있다. UI 는 dummy_ui.py 에서 이 함수들만 호출한다.
"""

import numpy as np

from pxr import Usd, UsdGeom, Gf, Vt, Sdf
import omni.usd


MERGED_PATH = "/World/OptimizedStreamlines"


# ---------------------------------------------------------------------------
# 1. 소스에서 곡선 수집
# ---------------------------------------------------------------------------
def _collect_curves(stage: Usd.Stage):
    """stage 내 모든 BasisCurves 를 순회하며 world 공간 point 배열들과
    각 곡선의 정점 개수, 대표 색을 모은다.

    return: (list[np.ndarray(N,3)], list[Gf.Vec3f | None], str type)
    """
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    per_curve_points: list[np.ndarray] = []
    per_curve_color: list = []
    curve_type = "linear"

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.BasisCurves):
            continue

        curves = UsdGeom.BasisCurves(prim)
        pts = curves.GetPointsAttr().Get(Usd.TimeCode.Default())
        counts = curves.GetCurveVertexCountsAttr().Get(Usd.TimeCode.Default())
        if not pts or not counts:
            continue

        # cubic 곡선이 하나라도 있으면 결과도 cubic 유지
        t = curves.GetTypeAttr().Get(Usd.TimeCode.Default())
        if t == "cubic":
            curve_type = "cubic"

        # local -> world 변환
        m = xform_cache.GetLocalToWorldTransform(prim)
        np_pts = np.array([[p[0], p[1], p[2]] for p in pts], dtype=np.float64)
        ones = np.ones((np_pts.shape[0], 1))
        homo = np.hstack([np_pts, ones])
        mat = np.array(m).reshape(4, 4)  # Gf.Matrix4d, row-major, point * M
        world = (homo @ mat)[:, :3]

        # 색: uniform(곡선당 1개) 또는 constant 1개면 곡선별 대표색으로 사용
        color = _representative_color(curves, len(counts))

        # 하나의 prim 이 여러 곡선을 품을 수 있으므로 counts 로 잘라서 개별화
        offset = 0
        for ci, c in enumerate(counts):
            seg = world[offset:offset + c]
            offset += c
            if len(seg) < 2:
                continue
            per_curve_points.append(seg.astype(np.float32))
            per_curve_color.append(color[ci] if color is not None else None)

    return per_curve_points, per_curve_color, curve_type


def _representative_color(curves: UsdGeom.BasisCurves, n_curves: int):
    """곡선별 대표 displayColor 리스트를 반환. 없으면 None."""
    dc_pv = UsdGeom.PrimvarsAPI(curves).GetPrimvar("displayColor")
    if not dc_pv or not dc_pv.GetAttr().IsValid():
        return None
    vals = dc_pv.Get(Usd.TimeCode.Default())
    if not vals:
        return None
    interp = dc_pv.GetInterpolation()
    if interp in ("constant",) or len(vals) == 1:
        return [vals[0]] * n_curves
    if interp == "uniform" and len(vals) == n_curves:
        return list(vals)
    # vertex/varying 등은 대표색으로 첫 값 사용
    return [vals[0]] * n_curves


# ---------------------------------------------------------------------------
# 2. 데시메이션 (Ramer-Douglas-Peucker)
# ---------------------------------------------------------------------------
def _rdp(points: np.ndarray, epsilon: float) -> np.ndarray:
    """폴리라인 정점 수를 epsilon 허용오차로 줄인다. epsilon<=0 이면 원본."""
    if epsilon <= 0.0 or len(points) < 3:
        return points

    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        a, b = points[start], points[end]
        ab = b - a
        ab_len = np.linalg.norm(ab)
        seg = points[start + 1:end]
        if ab_len < 1e-12:
            dists = np.linalg.norm(seg - a, axis=1)
        else:
            cross = np.cross(seg - a, ab)
            dists = np.linalg.norm(cross, axis=1) / ab_len
        idx = int(np.argmax(dists))
        if dists[idx] > epsilon:
            split = start + 1 + idx
            keep[split] = True
            stack.append((start, split))
            stack.append((split, end))

    return points[keep]


# ---------------------------------------------------------------------------
# 3. 단일 BasisCurves 로 병합하여 현재 stage 에 저장
# ---------------------------------------------------------------------------
def _author_merged(stage: Usd.Stage, per_curve_points, per_curve_color,
                   curve_type: str, width: float):
    # 기존 결과가 있으면 교체
    if stage.GetPrimAtPath(MERGED_PATH):
        stage.RemovePrim(MERGED_PATH)

    all_points: list = []
    counts: list[int] = []
    colors: list = []
    has_color = any(c is not None for c in per_curve_color)

    for pts, col in zip(per_curve_points, per_curve_color):
        counts.append(len(pts))
        all_points.extend(Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])) for p in pts)
        if has_color:
            colors.append(col if col is not None else Gf.Vec3f(0.8, 0.8, 0.8))

    curves = UsdGeom.BasisCurves.Define(stage, MERGED_PATH)
    curves.CreateTypeAttr(curve_type)
    if curve_type == "cubic":
        curves.CreateBasisAttr("bspline")
        curves.CreateWrapAttr("nonperiodic")
    curves.CreateCurveVertexCountsAttr(Vt.IntArray(counts))
    curves.CreatePointsAttr(Vt.Vec3fArray(all_points))

    widths = curves.CreateWidthsAttr(Vt.FloatArray([float(width)]))
    curves.SetWidthsInterpolation("constant")

    if has_color:
        dc = UsdGeom.PrimvarsAPI(curves).CreatePrimvar(
            "displayColor", Sdf.ValueTypeNames.Color3fArray)
        dc.SetInterpolation("uniform")
        dc.Set(Vt.Vec3fArray([Gf.Vec3f(*c) for c in colors]))

    return len(counts), len(all_points)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def optimize_and_load(source_path: str, epsilon: float = 0.0,
                      width: float = 0.1) -> str:
    """source_path 의 streamline USD 를 최적화해 현재 씬에 로드한다.

    - 모든 BasisCurves 를 world 공간으로 변환 후 단일 prim 으로 병합
    - epsilon>0 이면 RDP 데시메이션 적용
    return: 사람이 읽을 상태 문자열
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: 열 수 없음: {source_path}"

    per_curve_points, per_curve_color, curve_type = _collect_curves(src)
    if not per_curve_points:
        return f"ERROR: BasisCurves 를 찾지 못함: {source_path}"

    n_src_curves = len(per_curve_points)
    n_src_pts = sum(len(p) for p in per_curve_points)

    if epsilon > 0.0:
        per_curve_points = [_rdp(p, epsilon) for p in per_curve_points]

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: 활성 stage 가 없음 (씬을 먼저 열어주세요)"

    # /World 보장
    if not stage.GetPrimAtPath("/World"):
        UsdGeom.Xform.Define(stage, "/World")

    n_curves, n_pts = _author_merged(
        stage, per_curve_points, per_curve_color, curve_type, width)

    print(f"[curve] merged {n_src_curves} curves / {n_src_pts} pts "
          f"-> 1 prim / {n_pts} pts (epsilon={epsilon})")
    return (f"OK: {n_src_curves}개 곡선 → 단일 BasisCurves 1개 | "
            f"정점 {n_src_pts} → {n_pts} | {MERGED_PATH}")
