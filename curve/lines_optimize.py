"""streamline USD 최적화 핵심 로직 (색-인지 복셀 PointInstancer 방식).

소스: 곡선(BasisCurves/NurbsCurves) 다수, 각자 고유 머티리얼을 가질 수 있음.
개별 흐름 궤적은 필요 없고 "어느 영역에 무슨 색이 얼마나 있나"(색·밀도 분포)만
보면 되므로, 곡선을 **복셀 그리드로 다운샘플링**하여 단일 **UsdGeomPointInstancer**
로 표현한다.

핵심 규칙:
- **복셀당 인스턴스 1개.** 같은 복셀에 여러 색이 겹치면 그 색들을 **평균**내어
  하나만 둔다(중복 스피어 제거).
- 400개나 되는 색은 **비슷한 색끼리 버킷으로 양자화**(color_levels)하여 대표색
  프로토타입 수를 소수로 줄인다. 각 인스턴스는 자기 복셀 색에 가장 가까운
  버킷 프로토타입을 가리킨다.
- 곡선을 개별 Sphere prim 으로 만들지 않고 인스턴싱 → draw call 소수 유지.

색 추출: 1) 곡선의 displayColor primvar  2) 바인딩/형제 머티리얼의 셰이더 색 입력.
지오메트리가 time-sample 로만 저장된 경우도 첫 샘플로 폴백해 읽는다.
모든 실질 구현은 이 파일에 있다. UI 는 dummy_ui.py 에서 이 함수들만 호출한다.
"""

import asyncio
from collections import Counter

import numpy as np

from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf
import omni.usd
import omni.kit.app


MERGED_PATH = "/World/OptimizedStreamlines"
PROTO_SCOPE = MERGED_PATH + "/Prototypes"
LOOKS_PATH = MERGED_PATH + "/Looks"
DEFAULT_COLOR = np.array([0.8, 0.8, 0.8])

# 머티리얼 셰이더에서 색으로 읽어볼 입력 이름들 (UsdPreviewSurface / MDL 계열)
_COLOR_INPUTS = [
    "diffuseColor", "diffuse_color_constant", "base_color", "diffuse_tint",
    "diffuse_reflection_color", "albedo", "emissiveColor", "emissive_color",
]


# ---------------------------------------------------------------------------
# 순회 / 값 읽기 헬퍼
# ---------------------------------------------------------------------------
def _traverse_all(stage: Usd.Stage):
    """인스턴스(instanceable) 내부까지 들어가 모든 prim 을 순회한다."""
    return stage.Traverse(Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate))


def _is_curve(prim: Usd.Prim) -> bool:
    return prim.IsA(UsdGeom.BasisCurves) or prim.IsA(UsdGeom.NurbsCurves)


def _get(attr, tc):
    """attr 값을 tc 에서 읽되 없으면 첫 time-sample 로 폴백."""
    if attr is None or not attr.IsValid():
        return None
    v = attr.Get(tc)
    if v is None:
        ts = attr.GetTimeSamples()
        if ts:
            v = attr.Get(ts[0])
    return v


def _pick_timecode(stage: Usd.Stage):
    """곡선 지오메트리가 time-sample 이면 첫 샘플, 아니면 Default 타임코드."""
    for prim in _traverse_all(stage):
        if not _is_curve(prim):
            continue
        ts = UsdGeom.Curves(prim).GetPointsAttr().GetTimeSamples()
        return Usd.TimeCode(ts[0]) if ts else Usd.TimeCode.Default()
    return Usd.TimeCode.Default()


# ---------------------------------------------------------------------------
# 색 추출
# ---------------------------------------------------------------------------
def _find_material_prim(prim: Usd.Prim):
    """prim 의 머티리얼 prim 을 찾는다 (바인딩 → 형제). 없으면 None."""
    mat, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if mat and mat.GetPrim().IsValid():
        return mat.GetPrim()
    parent = prim.GetParent()
    if parent and parent.IsValid():
        for sib in parent.GetChildren():
            if sib != prim and sib.IsA(UsdShade.Material):
                return sib
    return None


def _shader_color(shader_prim: Usd.Prim, tc):
    """셰이더의 알려진 색 입력에서 상수 색을 읽는다. 없으면 None."""
    shader = UsdShade.Shader(shader_prim)
    for name in _COLOR_INPUTS:
        inp = shader.GetInput(name)
        if not inp:
            continue
        v = _get(inp.GetAttr(), tc)
        if v is not None and hasattr(v, "__len__") and len(v) >= 3:
            return np.array([float(v[0]), float(v[1]), float(v[2])])
    return None


def _material_color(mat_prim: Usd.Prim, tc):
    """머티리얼 서브트리의 셰이더에서 대표 색을 읽는다. 없으면 None."""
    if mat_prim is None or not mat_prim.IsValid():
        return None
    for p in Usd.PrimRange(mat_prim):
        if p.IsA(UsdShade.Shader):
            c = _shader_color(p, tc)
            if c is not None:
                return c
    return None


def _display_color(prim: Usd.Prim, tc):
    """곡선의 displayColor primvar 첫 값. 없으면 None."""
    dc = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
    if not dc or not dc.GetAttr().IsValid():
        return None
    vals = _get(dc.GetAttr(), tc)
    if not vals or not len(vals):
        return None
    v = vals[0]
    return np.array([float(v[0]), float(v[1]), float(v[2])])


def _curve_color(prim: Usd.Prim, tc):
    """곡선 색: displayColor → 머티리얼 셰이더 색 순으로 시도. 없으면 None."""
    c = _display_color(prim, tc)
    if c is not None:
        return c
    return _material_color(_find_material_prim(prim), tc)


# ---------------------------------------------------------------------------
# 진단
# ---------------------------------------------------------------------------
def inspect_source(source_path: str) -> str:
    """타입 분포 / 인스턴싱 / time-sample / 색 중복 여부를 진단한다."""
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    tc = _pick_timecode(src)
    types: Counter = Counter()
    n_curve = n_inst = n_ts = n_def = 0
    colors = []
    for prim in _traverse_all(src):
        types[prim.GetTypeName() or "(untyped)"] += 1
        if prim.IsInstanceable():
            n_inst += 1
        if _is_curve(prim):
            n_curve += 1
            pts_attr = UsdGeom.Curves(prim).GetPointsAttr()
            if pts_attr.Get(Usd.TimeCode.Default()) is not None:
                n_def += 1
            elif pts_attr.GetTimeSamples():
                n_ts += 1
            c = _curve_color(prim, tc)
            if c is not None:
                colors.append(c)

    # 색 중복/고유 분석
    color_line = "colors: none"
    if colors:
        arr = np.array(colors)
        exact = len(np.unique(np.round(arr, 4), axis=0))
        approx = len(np.unique(np.round(arr, 1), axis=0))  # ~10% 톨러런스
        color_line = (f"colors: {len(colors)} read | unique(round4)={exact} | "
                      f"grouped(round1)={approx}")

    n_proto = len(src.GetPrototypes())
    top = ", ".join(f"{t}:{c}" for t, c in types.most_common(15))
    return (f"curves={n_curve} | default-pts={n_def} | timesampled-pts={n_ts} | "
            f"instanceable={n_inst} | prototypes={n_proto}\n{color_line}\n"
            f"types: {top}")


# ---------------------------------------------------------------------------
# 소스에서 곡선 + 색 수집 (world 좌표)
# ---------------------------------------------------------------------------
def _extract_curve(prim: Usd.Prim, tc, xform_cache):
    """곡선 prim → (segments[list of np(N,3) world], color np(3)). 비면 None.

    Vt→numpy 변환을 벡터화(np.array(Vt배열))해 Python 루프 없이 빠르게 처리.
    """
    curves = UsdGeom.Curves(prim)
    pts = _get(curves.GetPointsAttr(), tc)
    counts = _get(curves.GetCurveVertexCountsAttr(), tc)
    if pts is None or not len(pts) or counts is None or not len(counts):
        return None

    m = np.array(xform_cache.GetLocalToWorldTransform(prim)).reshape(4, 4)
    np_pts = np.array(pts, dtype=np.float64)          # (N,3) 빠른 변환
    homo = np.hstack([np_pts, np.ones((np_pts.shape[0], 1))])
    world = (homo @ m)[:, :3]

    color = _curve_color(prim, tc)
    if color is None:
        color = DEFAULT_COLOR.copy()

    cnts = np.array(counts, dtype=np.int64)
    segs = []
    offset = 0
    for c in cnts:
        seg = world[offset:offset + int(c)]
        offset += int(c)
        if len(seg) >= 1:
            segs.append(seg)
    return segs, color


def _collect_curves(stage: Usd.Stage):
    """동기 수집. return: (points_list, colors_list, src_prim_count, skipped)."""
    tc = _pick_timecode(stage)
    xc = UsdGeom.XformCache(tc)
    pl, cl, n, skipped = [], [], 0, 0
    for prim in _traverse_all(stage):
        if not _is_curve(prim):
            continue
        n += 1
        r = _extract_curve(prim, tc, xc)
        if r is None:
            skipped += 1
            continue
        segs, color = r
        for s in segs:
            pl.append(s)
            cl.append(color)
    return pl, cl, n, skipped


async def _collect_curves_async(stage: Usd.Stage, report=None, chunk: int = 20):
    """비동기 수집: chunk 개마다 UI 에 양보(next_update_async)해 Kit 멈춤 방지."""
    tc = _pick_timecode(stage)
    xc = UsdGeom.XformCache(tc)
    app = omni.kit.app.get_app()
    pl, cl, n, skipped = [], [], 0, 0
    for prim in _traverse_all(stage):
        if not _is_curve(prim):
            continue
        n += 1
        r = _extract_curve(prim, tc, xc)
        if r is None:
            skipped += 1
        else:
            segs, color = r
            for s in segs:
                pl.append(s)
                cl.append(color)
        if n % chunk == 0:
            if report:
                report(f"reading curves... {n}")
            await app.next_update_async()
    return pl, cl, n, skipped


# ---------------------------------------------------------------------------
# 복셀화 — 색 인지 (순수 numpy, 오프라인 테스트 가능)
# ---------------------------------------------------------------------------
def _voxelize(points_list, colors_list, origin: np.ndarray, voxel_size: float):
    """곡선 점들을 복셀 그리드로 다운샘플링하되, 복셀당 하나로 합치고 색은 평균낸다.

    긴 세그먼트가 복셀을 건너뛰지 않도록 voxel_size 간격으로 리샘플한다.
    return: (centers np(M,3), counts np(M,), mean_colors np(M,3))
    """
    if voxel_size <= 0.0:
        raise ValueError("voxel_size must be > 0")

    sample_chunks = []
    color_chunks = []
    for curve, col in zip(points_list, colors_list):
        curve = np.asarray(curve, dtype=np.float64)
        col = np.asarray(col, dtype=np.float64)
        if len(curve) == 1:
            sample_chunks.append(curve)
            color_chunks.append(col[None, :])
            continue
        seg_vec = np.diff(curve, axis=0)
        seg_len = np.linalg.norm(seg_vec, axis=1)
        for i, L in enumerate(seg_len):
            n = max(int(np.ceil(L / voxel_size)), 1)
            ts = np.linspace(0.0, 1.0, n + 1)[:, None]
            pts = curve[i] + ts * seg_vec[i]
            sample_chunks.append(pts)
            color_chunks.append(np.repeat(col[None, :], len(pts), axis=0))

    if not sample_chunks:
        return (np.empty((0, 3)), np.empty((0,), dtype=np.int64), np.empty((0, 3)))

    samples = np.concatenate(sample_chunks, axis=0)
    sample_colors = np.concatenate(color_chunks, axis=0)

    idx = np.floor((samples - origin) / voxel_size).astype(np.int64)
    uniq, inverse, counts = np.unique(
        idx, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()

    sums = np.zeros((len(uniq), 3))
    np.add.at(sums, inverse, sample_colors)
    mean_colors = sums / counts[:, None]

    centers = origin + (uniq + 0.5) * voxel_size
    return centers, counts, mean_colors


# ---------------------------------------------------------------------------
# 색 양자화 — 비슷한 색을 버킷으로 (순수 numpy, 오프라인 테스트 가능)
# ---------------------------------------------------------------------------
def _quantize_colors(colors: np.ndarray, levels: int):
    """색들을 채널당 levels 단계 그리드로 양자화해 버킷으로 묶는다.

    return: (bucket_idx np(M,), reps np(K,3))
      bucket_idx[i] = 색 i 가 속한 버킷 인덱스
      reps[k]       = 버킷 k 의 대표색 (그 버킷에 속한 원본색들의 평균)
    """
    if len(colors) == 0:
        return np.empty((0,), dtype=np.int64), np.empty((0, 3))
    levels = max(int(levels), 2)
    q = np.round(colors * (levels - 1)) / (levels - 1)
    _uniq, inverse = np.unique(q, axis=0, return_inverse=True)
    inverse = inverse.ravel()
    k = inverse.max() + 1
    sums = np.zeros((k, 3))
    np.add.at(sums, inverse, colors)
    cnt = np.bincount(inverse, minlength=k)
    reps = sums / cnt[:, None]
    return inverse, reps


# ---------------------------------------------------------------------------
# root world bbox → 자동 voxel_size
# ---------------------------------------------------------------------------
def _world_bounds(stage: Usd.Stage, tc=None):
    if tc is None:
        tc = _pick_timecode(stage)
    bbox_cache = UsdGeom.BBoxCache(
        tc, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
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
# PointInstancer 저작 (복셀당 1 인스턴스, 색 버킷 프로토타입)
# ---------------------------------------------------------------------------
def _vec3f_array(np_arr):
    """(N,3) numpy → Vt.Vec3fArray (FromNumpy 우선, 없으면 폴백)."""
    a = np.ascontiguousarray(np_arr, dtype=np.float32)
    from_np = getattr(Vt.Vec3fArray, "FromNumpy", None)
    if from_np is not None:
        return from_np(a)
    return Vt.Vec3fArray([Gf.Vec3f(float(x), float(y), float(z)) for x, y, z in a])


def _int_array(np_arr):
    a = np.ascontiguousarray(np_arr, dtype=np.int32)
    from_np = getattr(Vt.IntArray, "FromNumpy", None)
    if from_np is not None:
        return from_np(a)
    return Vt.IntArray([int(v) for v in a])


def _author(stage, centers, counts, bucket_idx, reps, voxel_size,
            radius_factor, density_to_scale):
    """색 버킷 프로토타입 + 단일 PointInstancer 저작. bucket_idx/reps 는 미리 계산.

    큰 배열은 Vt.*Array.FromNumpy 로 벡터화 저작해 블로킹을 최소화한다.
    """
    if stage.GetPrimAtPath(MERGED_PATH):
        stage.RemovePrim(MERGED_PATH)
    UsdGeom.Xform.Define(stage, MERGED_PATH)
    UsdGeom.Scope.Define(stage, PROTO_SCOPE)
    UsdGeom.Scope.Define(stage, LOOKS_PATH)

    instancer = UsdGeom.PointInstancer.Define(stage, MERGED_PATH + "/instancer")
    proto_paths = []
    for k, rep in enumerate(reps):
        proto_path = f"{PROTO_SCOPE}/proto_{k}"
        sphere = UsdGeom.Sphere.Define(stage, proto_path)
        sphere.CreateRadiusAttr(1.0)
        col = Gf.Vec3f(float(rep[0]), float(rep[1]), float(rep[2]))
        sphere.CreateDisplayColorAttr(Vt.Vec3fArray([col]))
        # RTX 에서 확실히 보이도록 대표색 UsdPreviewSurface 바인딩
        mat_path = f"{LOOKS_PATH}/mat_{k}"
        mat = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path + "/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(col)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(mat)
        proto_paths.append(Sdf.Path(proto_path))

    # 밀도 → scale (옵션): count 로그 정규화 0.5~1.5 배
    base = float(voxel_size) * float(radius_factor)
    if density_to_scale and len(counts) > 0:
        cmax = float(counts.max())
        norm = np.log1p(counts.astype(np.float64)) / np.log1p(max(cmax, 1.0))
        factors = 0.5 + norm
    else:
        factors = np.ones(len(centers))
    scales_np = (base * factors)[:, None] * np.ones((1, 3))

    instancer.CreatePrototypesRel().SetTargets(proto_paths)
    instancer.CreateProtoIndicesAttr(_int_array(bucket_idx))
    instancer.CreatePositionsAttr(_vec3f_array(centers))
    instancer.CreateScalesAttr(_vec3f_array(scales_np))

    return len(proto_paths), len(centers)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def optimize_and_load(source_path: str, voxel_size: float = 0.0,
                      resolution: int = 128, radius_factor: float = 0.5,
                      density_to_scale: bool = False,
                      color_levels: int = 4) -> str:
    """streamline USD 를 색-인지 복셀 다운샘플링해 현재 씬에 로드한다.

    - 복셀당 인스턴스 1개, 겹치는 색은 평균
    - 비슷한 색은 color_levels 로 버킷 양자화 → 대표색 프로토타입 소수
    - voxel_size<=0 이면 root world bbox 최대변 / resolution 으로 자동 산출
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    points_list, colors_list, src_prim_count, skipped_empty = _collect_curves(src)
    if not points_list:
        hint = ""
        if skipped_empty:
            hint = (f"\n({skipped_empty} curve(s) skipped: points/counts "
                    "unreadable - time-sampled or empty)")
        return (f"ERROR: no valid curves found: {source_path}{hint}\n"
                f"{inspect_source(source_path)}")

    n_src_pts = sum(len(p) for p in points_list)

    mins, maxs = _world_bounds(src)
    if mins is None:
        return "ERROR: failed to compute world bbox"
    if voxel_size <= 0.0:
        voxel_size = float(np.max(maxs - mins)) / max(int(resolution), 1)
    if voxel_size <= 0.0:
        return "ERROR: failed to derive voxel_size (empty bbox)"

    origin = mins.astype(np.float64)
    centers, counts, mean_colors = _voxelize(
        points_list, colors_list, origin, voxel_size)
    bucket_idx, reps = _quantize_colors(mean_colors, color_levels)

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage (open a scene first)"
    if not stage.GetPrimAtPath("/World"):
        UsdGeom.Xform.Define(stage, "/World")

    n_protos, n_voxels = _author(
        stage, centers, counts, bucket_idx, reps, voxel_size,
        radius_factor, density_to_scale)

    return _result_msg(len(points_list), n_src_pts, n_voxels, n_protos, voxel_size)


def _result_msg(n_curves, n_src_pts, n_voxels, n_protos, voxel_size):
    print(f"[curve] voxelize: curves {n_curves}, pts {n_src_pts} -> "
          f"instances {n_voxels}, color-buckets {n_protos}, "
          f"voxel_size={voxel_size:.4g}")
    return (f"OK: curves {n_curves} / pts {n_src_pts} -> {n_voxels} instances "
            f"(1 per voxel, color-averaged) | color-buckets {n_protos} | "
            f"voxel={voxel_size:.4g} | {MERGED_PATH}")


async def optimize_and_load_async(source_path: str, voxel_size: float = 0.0,
                                  resolution: int = 128,
                                  radius_factor: float = 0.5,
                                  density_to_scale: bool = False,
                                  color_levels: int = 4,
                                  progress=None) -> str:
    """optimize_and_load 의 비동기 버전 — Kit 멈춤 방지.

    - USD 읽기: 주기적으로 UI 에 양보 (_collect_curves_async)
    - 무거운 순수-numpy(_voxelize/_quantize_colors): 백그라운드 스레드로 오프로드
    - USD 저작: 벡터화(FromNumpy)로 짧게, 직전 한 프레임 양보
    """
    def report(msg):
        if progress:
            progress(msg)

    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    report("reading curves...")
    points_list, colors_list, src_prim_count, skipped_empty = \
        await _collect_curves_async(src, report)
    if not points_list:
        hint = ""
        if skipped_empty:
            hint = (f"\n({skipped_empty} curve(s) skipped: points/counts "
                    "unreadable - time-sampled or empty)")
        return (f"ERROR: no valid curves found: {source_path}{hint}\n"
                f"{inspect_source(source_path)}")

    n_src_pts = sum(len(p) for p in points_list)

    mins, maxs = _world_bounds(src)
    if mins is None:
        return "ERROR: failed to compute world bbox"
    if voxel_size <= 0.0:
        voxel_size = float(np.max(maxs - mins)) / max(int(resolution), 1)
    if voxel_size <= 0.0:
        return "ERROR: failed to derive voxel_size (empty bbox)"
    origin = mins.astype(np.float64)

    # 무거운 순수-numpy 연산은 백그라운드 스레드에서
    report("voxelizing (background)...")
    loop = asyncio.get_event_loop()
    centers, counts, mean_colors = await loop.run_in_executor(
        None, _voxelize, points_list, colors_list, origin, voxel_size)
    bucket_idx, reps = await loop.run_in_executor(
        None, _quantize_colors, mean_colors, color_levels)

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage (open a scene first)"
    if not stage.GetPrimAtPath("/World"):
        UsdGeom.Xform.Define(stage, "/World")

    report("loading into scene...")
    await omni.kit.app.get_app().next_update_async()
    n_protos, n_voxels = _author(
        stage, centers, counts, bucket_idx, reps, voxel_size,
        radius_factor, density_to_scale)

    return _result_msg(len(points_list), n_src_pts, n_voxels, n_protos, voxel_size)
