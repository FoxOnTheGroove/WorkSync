"""streamline USD 최적화 핵심 로직 (복셀 PointInstancer 방식).

소스는 색(머티리얼)당 BasisCurves prim 1개 구조 — draw call 은 이미 최소이고
병목은 하나의 prim 안에 든 수십만 곡선 / 수백만 정점이다. 개별 흐름 궤적은
필요 없고 "어느 영역에 무슨 색이 얼마나 있나"(색·밀도 분포)만 보면 되므로,
라인을 **복셀 그리드로 다운샘플링**하여 단일 **UsdGeomPointInstancer +
색당 구 프로토타입**으로 표현한다.

- 결과 인스턴스 수 = 점유 복셀 수 → 소스 복잡도와 무관하게 상한 고정
- 색별로 독립 복셀화 + 색당 프로토타입 → 모든 색 보존
- 복셀을 개별 Sphere prim 으로 만들지 않고 인스턴싱 → draw call 소수 유지

머티리얼 탐색 순서: 1) 바인딩된 머티리얼  2) 형제 위치의 Material prim
모든 실질 구현은 이 파일에 있다. UI 는 dummy_ui.py 에서 이 함수들만 호출한다.
"""

from collections import Counter

import numpy as np

from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf
import omni.usd


MERGED_PATH = "/World/OptimizedStreamlines"
PROTO_SCOPE = MERGED_PATH + "/Prototypes"
LOOKS_PATH = MERGED_PATH + "/Looks"
NO_MAT_KEY = "__no_material__"


# ---------------------------------------------------------------------------
# 곡선 prim 순회 (인스턴스 프록시 포함 + BasisCurves/NurbsCurves 모두)
# ---------------------------------------------------------------------------
def _traverse_all(stage: Usd.Stage):
    """인스턴스(instanceable) 내부까지 들어가서 모든 prim 을 순회한다.

    스트림라인이 reference/instanceable 로 Xform 말단에 붙어 있으면
    일반 stage.Traverse() 는 인스턴스 내부로 들어가지 않아 곡선을 놓친다.
    """
    return stage.Traverse(Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate))


def _is_curve(prim: Usd.Prim) -> bool:
    """BasisCurves / NurbsCurves 등 곡선 계열이면 True."""
    return prim.IsA(UsdGeom.BasisCurves) or prim.IsA(UsdGeom.NurbsCurves)


def inspect_source(source_path: str) -> str:
    """소스 USD 의 prim 타입 분포/인스턴싱 여부를 진단해 문자열로 반환한다.

    'BasisCurves 를 찾지 못함' 이 뜰 때 실제로 무엇이 들어있는지 확인용.
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: 열 수 없음: {source_path}"

    types: Counter = Counter()
    n_curve = 0
    n_instanceable = 0
    for prim in _traverse_all(src):
        tn = prim.GetTypeName() or "(untyped)"
        types[tn] += 1
        if prim.IsInstanceable():
            n_instanceable += 1
        if _is_curve(prim):
            n_curve += 1

    n_proto = len(src.GetPrototypes())
    top = ", ".join(f"{t}:{c}" for t, c in types.most_common(15))
    return (f"곡선(Basis/Nurbs)={n_curve} | instanceable={n_instanceable} | "
            f"prototypes={n_proto}\n타입분포: {top}")


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
# 소스에서 곡선 수집 (머티리얼/색별 그룹핑, world 좌표)
# ---------------------------------------------------------------------------
def _collect_curves(stage: Usd.Stage):
    """stage 내 모든 BasisCurves 를 순회하며 색(머티리얼)별 그룹으로 모은다.

    return: (groups, src_prim_count)
      groups = {
        matkey(str): {
            "src": Sdf.Path | None,   # 참조할 원본 머티리얼 경로
            "points": [np.ndarray(N,3), ...],  # world 좌표 곡선들
            "color": Gf.Vec3f | None,          # 머티리얼 없을 때 fallback 색
        }
      }
    """
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    groups: dict = {}
    src_prim_count = 0

    for prim in _traverse_all(stage):
        if not _is_curve(prim):
            continue
        src_prim_count += 1

        curves = UsdGeom.Curves(prim)  # BasisCurves/NurbsCurves 공통 베이스
        pts = curves.GetPointsAttr().Get(Usd.TimeCode.Default())
        counts = curves.GetCurveVertexCountsAttr().Get(Usd.TimeCode.Default())
        if not pts or not counts:
            continue

        mat_path = _find_material_path(prim)
        matkey = str(mat_path) if mat_path is not None else NO_MAT_KEY
        grp = groups.setdefault(matkey, {
            "src": mat_path, "points": [], "color": None,
        })

        # local -> world 변환
        m = np.array(xform_cache.GetLocalToWorldTransform(prim)).reshape(4, 4)
        np_pts = np.array([[p[0], p[1], p[2]] for p in pts], dtype=np.float64)
        homo = np.hstack([np_pts, np.ones((np_pts.shape[0], 1))])
        world = (homo @ m)[:, :3]

        if grp["color"] is None:
            grp["color"] = _representative_color(curves)

        offset = 0
        for c in counts:
            seg = world[offset:offset + c]
            offset += c
            if len(seg) < 1:
                continue
            grp["points"].append(seg.astype(np.float64))

    return groups, src_prim_count


def _representative_color(curves: UsdGeom.BasisCurves):
    """곡선의 대표 displayColor 1개를 반환. 없으면 None."""
    dc_pv = UsdGeom.PrimvarsAPI(curves).GetPrimvar("displayColor")
    if not dc_pv or not dc_pv.GetAttr().IsValid():
        return None
    vals = dc_pv.Get(Usd.TimeCode.Default())
    if not vals:
        return None
    return vals[0]


# ---------------------------------------------------------------------------
# 복셀화 (순수 numpy — pxr/omni 불필요, 오프라인 테스트 가능)
# ---------------------------------------------------------------------------
def _voxelize(points_list, origin: np.ndarray, voxel_size: float):
    """색 그룹의 곡선 점들을 복셀 그리드로 다운샘플링한다.

    긴 세그먼트가 복셀을 건너뛰지 않도록 voxel_size 간격으로 리샘플한 뒤
    복셀 인덱스로 양자화하여 점유 복셀과 밀도(count)를 구한다.

    return: (centers np(M,3), counts np(M,)) — 점유 복셀 중심과 관통 밀도.
    """
    if voxel_size <= 0.0:
        raise ValueError("voxel_size must be > 0")

    all_samples = []
    for curve in points_list:
        curve = np.asarray(curve, dtype=np.float64)
        if len(curve) == 1:
            all_samples.append(curve)
            continue
        # 인접 점 간 거리 기반으로 세그먼트를 voxel_size 간격으로 리샘플
        seg_vec = np.diff(curve, axis=0)
        seg_len = np.linalg.norm(seg_vec, axis=1)
        for i, L in enumerate(seg_len):
            n = max(int(np.ceil(L / voxel_size)), 1)
            ts = np.linspace(0.0, 1.0, n + 1)[:, None]
            pts = curve[i] + ts * seg_vec[i]
            all_samples.append(pts)

    if not all_samples:
        return np.empty((0, 3)), np.empty((0,), dtype=np.int64)

    samples = np.concatenate(all_samples, axis=0)
    idx = np.floor((samples - origin) / voxel_size).astype(np.int64)
    uniq, counts = np.unique(idx, axis=0, return_counts=True)
    centers = origin + (uniq + 0.5) * voxel_size
    return centers, counts


# ---------------------------------------------------------------------------
# root world bbox → 자동 voxel_size
# ---------------------------------------------------------------------------
def _world_bounds(stage: Usd.Stage):
    """stage 내 모든 BasisCurves 를 감싸는 world AABB (min, max) 를 반환."""
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    mins = np.array([np.inf, np.inf, np.inf])
    maxs = np.array([-np.inf, -np.inf, -np.inf])
    found = False
    for prim in _traverse_all(stage):
        if not _is_curve(prim):
            continue
        rng = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if rng.IsEmpty():
            continue
        found = True
        lo, hi = rng.GetMin(), rng.GetMax()
        mins = np.minimum(mins, [lo[0], lo[1], lo[2]])
        maxs = np.maximum(maxs, [hi[0], hi[1], hi[2]])
    if not found:
        return None, None
    return mins, maxs


# ---------------------------------------------------------------------------
# PointInstancer 저작
# ---------------------------------------------------------------------------
def _author_point_instancer(stage, per_color, source_path, voxel_size,
                            radius_factor, density_to_scale):
    """per_color = [ {src, color, centers np(M,3), counts np(M,)} ] 로부터
    단일 PointInstancer + 색당 구 프로토타입을 저작한다."""
    if stage.GetPrimAtPath(MERGED_PATH):
        stage.RemovePrim(MERGED_PATH)

    UsdGeom.Xform.Define(stage, MERGED_PATH)
    UsdGeom.Scope.Define(stage, PROTO_SCOPE)
    UsdGeom.Scope.Define(stage, LOOKS_PATH)

    instancer = UsdGeom.PointInstancer.Define(stage, MERGED_PATH + "/instancer")
    proto_paths = []

    positions: list = []
    proto_indices: list = []
    scales: list = []
    base_scale = float(voxel_size) * float(radius_factor)
    total_voxels = 0

    for ci, grp in enumerate(per_color):
        centers = grp["centers"]
        counts = grp["counts"]
        if len(centers) == 0:
            continue

        # 색당 구 프로토타입 (radius=1, scale 로 반경 제어)
        proto_path = f"{PROTO_SCOPE}/proto_{ci}"
        sphere = UsdGeom.Sphere.Define(stage, proto_path)
        sphere.CreateRadiusAttr(1.0)
        proto_paths.append(Sdf.Path(proto_path))

        # 색: 원본 머티리얼 참조 바인딩(정확 보존) 또는 displayColor
        if grp["src"] is not None:
            dst_mat_path = f"{LOOKS_PATH}/mat_{ci}"
            mat_prim = stage.DefinePrim(dst_mat_path)
            mat_prim.GetReferences().AddReference(source_path, str(grp["src"]))
            binding = UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim())
            binding.Bind(UsdShade.Material(mat_prim))
        else:
            col = grp["color"] if grp["color"] is not None else Gf.Vec3f(0.8, 0.8, 0.8)
            sphere.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*col)]))

        # 밀도 → scale 매핑 (옵션): count 를 로그 정규화해 0.5~1.5 배
        if density_to_scale and len(counts) > 0:
            cmax = float(counts.max())
            norm = np.log1p(counts.astype(np.float64)) / np.log1p(max(cmax, 1.0))
            factors = 0.5 + norm  # 0.5 ~ 1.5
        else:
            factors = np.ones(len(centers))

        for center, f in zip(centers, factors):
            positions.append(Gf.Vec3f(float(center[0]), float(center[1]), float(center[2])))
            proto_indices.append(ci)
            s = base_scale * float(f)
            scales.append(Gf.Vec3f(s, s, s))

        total_voxels += len(centers)

    instancer.CreatePrototypesRel().SetTargets(proto_paths)
    instancer.CreateProtoIndicesAttr(Vt.IntArray(proto_indices))
    instancer.CreatePositionsAttr(Vt.Vec3fArray(positions))
    instancer.CreateScalesAttr(Vt.Vec3fArray(scales))

    return len(proto_paths), total_voxels


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def optimize_and_load(source_path: str, voxel_size: float = 0.0,
                      resolution: int = 128, radius_factor: float = 0.5,
                      density_to_scale: bool = False) -> str:
    """source_path 의 streamline USD 를 복셀 다운샘플링해 현재 씬에 로드한다.

    - 색(머티리얼)별로 곡선을 world 좌표로 모아 복셀 그리드로 다운샘플링
    - 단일 PointInstancer + 색당 구 프로토타입으로 표현 (모든 색 보존)
    - voxel_size<=0 이면 root world bbox 최대변 / resolution 으로 자동 산출
    return: 사람이 읽을 상태 문자열
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: 열 수 없음: {source_path}"

    groups, src_prim_count = _collect_curves(src)
    if not groups or all(not g["points"] for g in groups.values()):
        # 무엇이 들어있는지 타입 분포를 함께 보고
        return (f"ERROR: 곡선(BasisCurves/NurbsCurves)을 찾지 못함: {source_path}\n"
                f"{inspect_source(source_path)}")

    n_src_pts = sum(len(p) for g in groups.values() for p in g["points"])

    mins, maxs = _world_bounds(src)
    if mins is None:
        return "ERROR: world bbox 계산 실패"
    if voxel_size <= 0.0:
        extent = float(np.max(maxs - mins))
        voxel_size = extent / max(int(resolution), 1)
    if voxel_size <= 0.0:
        return "ERROR: voxel_size 산출 실패 (bbox 크기 0)"

    origin = mins.astype(np.float64)
    per_color = []
    for matkey in sorted(groups):
        grp = groups[matkey]
        if not grp["points"]:
            continue
        centers, counts = _voxelize(grp["points"], origin, voxel_size)
        per_color.append({
            "src": grp["src"], "color": grp["color"],
            "centers": centers, "counts": counts,
        })

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: 활성 stage 가 없음 (씬을 먼저 열어주세요)"
    if not stage.GetPrimAtPath("/World"):
        UsdGeom.Xform.Define(stage, "/World")

    n_protos, n_voxels = _author_point_instancer(
        stage, per_color, source_path, voxel_size, radius_factor, density_to_scale)

    print(f"[curve] voxelize: prim {src_prim_count}, pts {n_src_pts} -> "
          f"instances {n_voxels}, protos(colors) {n_protos}, "
          f"voxel_size={voxel_size:.4g}")
    return (f"OK: 정점 {n_src_pts} → 인스턴스 {n_voxels}개 | "
            f"색 {n_protos}종 보존 | voxel={voxel_size:.4g} | {MERGED_PATH}")
