"""streamline USD 최적화 핵심 로직.

수많은 개별 line renderer(BasisCurves) prim으로 이루어진 streamline USD는
prim 개수 = draw call 개수가 되어 뷰포트가 심하게 버벅인다.

여기서는 소스 USD의 모든 곡선을 읽어 world 공간 좌표로 변환한 뒤,
**바인딩된 머티리얼(색)별로 그룹핑**하여 그룹마다 단일 BasisCurves prim
하나로 병합한다. 즉 draw call 수 = 색(머티리얼) 종류 수로 줄어든다.
원본 머티리얼은 그대로 참조 바인딩하므로 모든 색이 정확히 보존된다.
(선택적으로 Ramer-Douglas-Peucker 데시메이션으로 정점 수도 줄인다.)

머티리얼 탐색 순서:
  1) UsdShade MaterialBindingAPI 로 바인딩된 머티리얼
  2) 없으면 형제(sibling) 위치의 UsdShade.Material prim
  3) 그래도 없으면 displayColor / 무색 그룹

모든 실질 구현은 이 파일에 있다. UI 는 dummy_ui.py 에서 이 함수들만 호출한다.
"""

import numpy as np

from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf
import omni.usd


MERGED_PATH = "/World/OptimizedStreamlines"
LOOKS_PATH = MERGED_PATH + "/Looks"
NO_MAT_KEY = "__no_material__"


# ---------------------------------------------------------------------------
# 머티리얼 탐색
# ---------------------------------------------------------------------------
def _find_material_path(prim: Usd.Prim):
    """prim 의 머티리얼 경로(Sdf.Path)를 찾는다. 없으면 None.

    1) 바인딩된 머티리얼  2) 형제 위치의 Material prim
    """
    mat, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if mat and mat.GetPrim().IsValid():
        return mat.GetPath()

    parent = prim.GetParent()
    if parent and parent.IsValid():
        for sib in parent.GetChildren():
            if sib != prim and sib.IsA(UsdShade.Material):
                return sib.GetPath()
    return None


# ---------------------------------------------------------------------------
# 소스에서 곡선 수집 (머티리얼별 그룹핑)
# ---------------------------------------------------------------------------
def _collect_curves(stage: Usd.Stage):
    """stage 내 모든 BasisCurves 를 순회하며 머티리얼별 그룹으로 모은다.

    return: groups = {
        matkey(str): {
            "src": Sdf.Path | None,   # 참조할 원본 머티리얼 경로
            "points": [np.ndarray(N,3), ...],
            "type": "linear" | "cubic",
            "colors": [Gf.Vec3f | None, ...],   # 머티리얼 없을 때 fallback 용
        }
    }
    """
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    groups: dict = {}
    src_prim_count = 0  # 소스의 BasisCurves prim(=draw call) 개수

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.BasisCurves):
            continue
        src_prim_count += 1

        curves = UsdGeom.BasisCurves(prim)
        pts = curves.GetPointsAttr().Get(Usd.TimeCode.Default())
        counts = curves.GetCurveVertexCountsAttr().Get(Usd.TimeCode.Default())
        if not pts or not counts:
            continue

        t = curves.GetTypeAttr().Get(Usd.TimeCode.Default())
        ctype = "cubic" if t == "cubic" else "linear"

        mat_path = _find_material_path(prim)
        matkey = str(mat_path) if mat_path is not None else NO_MAT_KEY
        grp = groups.setdefault(matkey, {
            "src": mat_path, "points": [], "type": "linear", "colors": [],
        })
        if ctype == "cubic":
            grp["type"] = "cubic"

        # local -> world 변환
        m = np.array(xform_cache.GetLocalToWorldTransform(prim)).reshape(4, 4)
        np_pts = np.array([[p[0], p[1], p[2]] for p in pts], dtype=np.float64)
        homo = np.hstack([np_pts, np.ones((np_pts.shape[0], 1))])
        world = (homo @ m)[:, :3]

        color = _representative_color(curves, len(counts))

        offset = 0
        for ci, c in enumerate(counts):
            seg = world[offset:offset + c]
            offset += c
            if len(seg) < 2:
                continue
            grp["points"].append(seg.astype(np.float32))
            grp["colors"].append(color[ci] if color is not None else None)

    return groups, src_prim_count


def _representative_color(curves: UsdGeom.BasisCurves, n_curves: int):
    """곡선별 대표 displayColor 리스트를 반환. 없으면 None."""
    dc_pv = UsdGeom.PrimvarsAPI(curves).GetPrimvar("displayColor")
    if not dc_pv or not dc_pv.GetAttr().IsValid():
        return None
    vals = dc_pv.Get(Usd.TimeCode.Default())
    if not vals:
        return None
    interp = dc_pv.GetInterpolation()
    if interp == "constant" or len(vals) == 1:
        return [vals[0]] * n_curves
    if interp == "uniform" and len(vals) == n_curves:
        return list(vals)
    return [vals[0]] * n_curves


# ---------------------------------------------------------------------------
# 데시메이션 (Ramer-Douglas-Peucker)
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
# 병합 & 씬에 저장
# ---------------------------------------------------------------------------
def _author_groups(stage: Usd.Stage, groups: dict, source_path: str,
                   epsilon: float, width: float):
    if stage.GetPrimAtPath(MERGED_PATH):
        stage.RemovePrim(MERGED_PATH)

    UsdGeom.Xform.Define(stage, MERGED_PATH)
    UsdGeom.Scope.Define(stage, LOOKS_PATH)

    total_curves = 0
    total_pts = 0

    for gi, (matkey, grp) in enumerate(sorted(groups.items())):
        pts_list = grp["points"]
        if not pts_list:
            continue
        if epsilon > 0.0:
            pts_list = [_rdp(p, epsilon) for p in pts_list]

        group_prim_path = f"{MERGED_PATH}/group_{gi}"
        curves = UsdGeom.BasisCurves.Define(stage, group_prim_path)
        curves.CreateTypeAttr(grp["type"])
        if grp["type"] == "cubic":
            curves.CreateBasisAttr("bspline")
            curves.CreateWrapAttr("nonperiodic")

        counts: list[int] = []
        all_points: list = []
        for p in pts_list:
            counts.append(len(p))
            all_points.extend(
                Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])) for v in p)

        curves.CreateCurveVertexCountsAttr(Vt.IntArray(counts))
        curves.CreatePointsAttr(Vt.Vec3fArray(all_points))
        curves.CreateWidthsAttr(Vt.FloatArray([float(width)]))
        curves.SetWidthsInterpolation("constant")

        total_curves += len(counts)
        total_pts += len(all_points)

        # 머티리얼: 원본을 참조로 가져와 그대로 바인딩 → 색 정확 보존
        if grp["src"] is not None:
            dst_mat_path = f"{LOOKS_PATH}/mat_{gi}"
            mat_prim = stage.DefinePrim(dst_mat_path)
            mat_prim.GetReferences().AddReference(source_path, str(grp["src"]))
            mat = UsdShade.Material(mat_prim)
            binding = UsdShade.MaterialBindingAPI.Apply(curves.GetPrim())
            binding.Bind(mat)
        else:
            # 머티리얼이 없으면 displayColor 로 색 보존 (uniform)
            cols = [c if c is not None else Gf.Vec3f(0.8, 0.8, 0.8)
                    for c in grp["colors"]]
            dc = UsdGeom.PrimvarsAPI(curves).CreatePrimvar(
                "displayColor", Sdf.ValueTypeNames.Color3fArray)
            dc.SetInterpolation("uniform")
            dc.Set(Vt.Vec3fArray([Gf.Vec3f(*c) for c in cols]))

    return len(groups), total_curves, total_pts


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def optimize_and_load(source_path: str, epsilon: float = 0.0,
                      width: float = 0.1) -> str:
    """source_path 의 streamline USD 를 최적화해 현재 씬에 로드한다.

    - 모든 BasisCurves 를 world 공간으로 변환
    - 바인딩된 머티리얼(색)별로 그룹핑 → 그룹마다 단일 BasisCurves 로 병합
    - 원본 머티리얼을 참조 바인딩하여 모든 색 보존
    - epsilon>0 이면 RDP 데시메이션 적용
    return: 사람이 읽을 상태 문자열
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: 열 수 없음: {source_path}"

    groups, src_prim_count = _collect_curves(src)
    if not groups or all(not g["points"] for g in groups.values()):
        return f"ERROR: BasisCurves 를 찾지 못함: {source_path}"

    n_src_curves = sum(len(g["points"]) for g in groups.values())
    n_src_pts = sum(len(p) for g in groups.values() for p in g["points"])

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: 활성 stage 가 없음 (씬을 먼저 열어주세요)"
    if not stage.GetPrimAtPath("/World"):
        UsdGeom.Xform.Define(stage, "/World")

    n_groups, n_curves, n_pts = _author_groups(
        stage, groups, source_path, epsilon, width)

    # draw call 감소가 실제 최적화의 핵심 지표
    print(f"[curve] draw call(prim): {src_prim_count} -> {n_groups} | "
          f"curves {n_src_curves} | pts {n_src_pts} -> {n_pts} (epsilon={epsilon})")

    if src_prim_count <= n_groups:
        note = (" | ⚠ 이미 색당 prim 1개 구조라 draw call 감소 없음 "
                "— 데시메이션(ε) / usdc 변환 위주로 최적화하세요")
    else:
        note = f" | draw call {src_prim_count}→{n_groups} 감소"

    return (f"OK: prim {src_prim_count} → {n_groups} (색 {n_groups}종 보존) | "
            f"곡선 {n_src_curves} | 정점 {n_src_pts} → {n_pts}{note}")
