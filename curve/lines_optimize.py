"""streamline USD 최적화 핵심 로직 (색-인지 복셀 PointInstancer 방식).

소스: 곡선(BasisCurves/NurbsCurves) 다수, 각자 고유 머티리얼을 가질 수 있음.
개별 흐름 궤적은 필요 없고 "어느 영역에 무슨 색이 얼마나 있나"(색·밀도 분포)만
보면 되므로, 곡선을 **복셀 그리드로 다운샘플링**하여 단일 **UsdGeomPointInstancer**
로 표현한다.

핵심 규칙:
- **복셀당 인스턴스 1개.** 같은 복셀에 여러 색이 겹치면 그 색들을 **평균**내어
  하나만 둔다(중복 스피어 제거).
- 비슷한 색끼리 버킷으로 양자화(color_levels)하여 대표색 프로토타입 수를 소수로
  줄인다. 각 인스턴스는 자기 복셀 색에 가장 가까운 버킷 프로토타입을 가리킨다.
- 곡선을 개별 Sphere prim 으로 만들지 않고 인스턴싱 → draw call 소수 유지.
- group_paths 로 지정한 대상 xform 하위 곡선만 복셀 연산 대상이고, 대상 밖
  ("ungrouped") 곡선은 연산하지 않고 원본을 그대로 참조해서 가져온다.

색 추출: 1) 곡선의 displayColor primvar  2) 바인딩/형제 머티리얼의 셰이더 색 입력.
지오메트리가 time-sample 로만 저장된 경우도 첫 샘플로 폴백해 읽는다.
모든 실질 구현은 이 파일에 있다. UI 는 dummy_ui.py 에서 이 함수들만 호출한다.
"""

import asyncio
import time
from collections import Counter

import numpy as np

from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf, Tf
import omni.usd
import omni.kit.app


MERGED_PATH = "/World/OptimizedStreamlines"
RAW_PATH = MERGED_PATH + "/RawUngrouped"
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


def _parse_group_paths(text: str) -> list:
    """UI 입력("/root/target, /root/target2")을 대상 경로 리스트로 파싱한다."""
    if not text:
        return []
    out = []
    for tok in text.replace("\n", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if not tok.startswith("/"):
            tok = "/" + tok
        out.append(tok.rstrip("/"))
    return out


def _group_for_path(path_str: str, targets: list) -> str:
    """곡선 경로가 어느 대상 xform 하위인지 판별해 그룹 키를 반환.

    예) targets=["/root/target","/root/target2"] 일 때
        /root/target/b/line1  → "/root/target"
        /root/target2/line5   → "/root/target2"
        어느 대상에도 안 속함  → "ungrouped" (연산 대상 제외, 원본 그대로 참조)
    targets 가 비면 전부 "all" (그룹 분류 없음, 전부 연산 대상).
    """
    if not targets:
        return "all"
    for t in targets:
        if path_str == t or path_str.startswith(t + "/"):
            return t
    return "ungrouped"


def _safe_name(key: str, idx: int) -> str:
    """그룹 키 → 유효한 prim 이름 (충돌 방지로 idx 접두)."""
    raw = key.strip("/").replace("/", "_") or "root"
    ident = Tf.MakeValidIdentifier(raw) if hasattr(Tf, "MakeValidIdentifier") else raw
    return f"g{idx}_{ident}"


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

    color_line = "colors: none"
    if colors:
        arr = np.array(colors)
        exact = len(np.unique(np.round(arr, 4), axis=0))
        approx = len(np.unique(np.round(arr, 1), axis=0))
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
    """곡선 prim → (segments[list of np(N,3) world], color np(3), wmin, wmax). 비면 None."""
    curves = UsdGeom.Curves(prim)
    pts = _get(curves.GetPointsAttr(), tc)
    counts = _get(curves.GetCurveVertexCountsAttr(), tc)
    if pts is None or not len(pts) or counts is None or not len(counts):
        return None

    m = np.array(xform_cache.GetLocalToWorldTransform(prim)).reshape(4, 4)
    np_pts = np.array(pts, dtype=np.float64)          # (N,3) 빠른 변환
    homo = np.hstack([np_pts, np.ones((np_pts.shape[0], 1))])
    world = (homo @ m)[:, :3].astype(np.float32)      # 이후 처리는 float32 로 충분

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
    return segs, color, world.min(axis=0), world.max(axis=0)


def _curve_prims_and_timecode(stage: Usd.Stage):
    """곡선 prim 목록과 대표 타임코드를 단일 순회로 구한다."""
    prims = [p for p in _traverse_all(stage) if _is_curve(p)]
    tc = Usd.TimeCode.Default()
    if prims:
        ts = UsdGeom.Curves(prims[0]).GetPointsAttr().GetTimeSamples()
        if ts:
            tc = Usd.TimeCode(ts[0])
    return prims, tc


def _collect_curves(stage: Usd.Stage, group_paths: list = None):
    """동기 수집. group_paths 밖("ungrouped") 곡선은 연산하지 않고 경로만 기록.

    return: (pl, cl, gl, raw_paths, n, skipped, mins, maxs)
    """
    targets = group_paths or []
    prims, tc = _curve_prims_and_timecode(stage)
    xc = UsdGeom.XformCache(tc)
    pl, cl, gl, skipped, raw_paths = [], [], [], 0, []
    mins = np.full(3, np.inf)
    maxs = np.full(3, -np.inf)
    for prim in prims:
        path_str = prim.GetPath().pathString
        gkey = _group_for_path(path_str, targets)
        if gkey == "ungrouped":
            raw_paths.append(path_str)   # 연산 스킵, 원본 그대로 나중에 참조
            continue
        r = _extract_curve(prim, tc, xc)
        if r is None:
            skipped += 1
            continue
        segs, color, wmin, wmax = r
        mins = np.minimum(mins, wmin)
        maxs = np.maximum(maxs, wmax)
        for s in segs:
            pl.append(s)
            cl.append(color)
            gl.append(gkey)
    return pl, cl, gl, raw_paths, len(prims), skipped, mins, maxs


async def _collect_curves_async(stage: Usd.Stage, group_paths: list = None,
                                report=None, yield_interval: float = 0.05):
    """비동기 수집. yield_interval 초 이상 일했을 때만 UI 에 양보한다."""
    targets = group_paths or []
    prims, tc = _curve_prims_and_timecode(stage)
    xc = UsdGeom.XformCache(tc)
    app = omni.kit.app.get_app()
    total = len(prims)

    pl, cl, gl, skipped, raw_paths = [], [], [], 0, []
    mins = np.full(3, np.inf)
    maxs = np.full(3, -np.inf)
    last_yield = time.perf_counter()
    for i, prim in enumerate(prims):
        path_str = prim.GetPath().pathString
        gkey = _group_for_path(path_str, targets)
        if gkey == "ungrouped":
            raw_paths.append(path_str)
        else:
            r = _extract_curve(prim, tc, xc)
            if r is None:
                skipped += 1
            else:
                segs, color, wmin, wmax = r
                mins = np.minimum(mins, wmin)
                maxs = np.maximum(maxs, wmax)
                for s in segs:
                    pl.append(s)
                    cl.append(color)
                    gl.append(gkey)
        if time.perf_counter() - last_yield >= yield_interval:
            if report and total:
                report(f"[1/3] reading curves... "
                       f"{100 * (i + 1) // total}% ({i + 1}/{total})")
            await app.next_update_async()
            last_yield = time.perf_counter()
    return pl, cl, gl, raw_paths, total, skipped, mins, maxs


# ---------------------------------------------------------------------------
# 복셀화 — 색 인지 (순수 numpy, 오프라인 테스트 가능)
# ---------------------------------------------------------------------------
def _resample(points_list, colors_list, voxel_size: float):
    """곡선들을 voxel_size 간격으로 리샘플. 완전 벡터화 (Python 루프 없음).

    return: (samples np(S,3) float32, sample_curve np(S,) int64, palette np(nc,3))
    """
    lengths = np.array([len(p) for p in points_list], dtype=np.int64)
    all_pts = np.concatenate(
        [np.asarray(p, dtype=np.float32) for p in points_list], axis=0)
    n_curves = len(points_list)
    curve_ids = np.repeat(np.arange(n_curves, dtype=np.int64), lengths)
    palette = np.asarray(colors_list, dtype=np.float64)  # (n_curves, 3)

    # 곡선 내부의 인접 점 쌍만 세그먼트로 (곡선 경계를 잇는 쌍은 제외)
    valid = curve_ids[:-1] == curve_ids[1:]
    seg_start = all_pts[:-1][valid]
    seg_vec = all_pts[1:][valid] - seg_start
    seg_curve = curve_ids[:-1][valid]
    seg_len = np.linalg.norm(seg_vec, axis=1)
    n_sub = np.maximum(np.ceil(seg_len / voxel_size).astype(np.int64), 1)

    # 샘플 t = k/n (k=0..n-1): 시작점 포함, 끝점 제외(다음 세그먼트가 커버)
    total = int(n_sub.sum())
    seg_of = np.repeat(np.arange(len(n_sub), dtype=np.int64), n_sub)
    k = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(n_sub) - n_sub, n_sub)
    t = (k / n_sub[seg_of]).astype(np.float32)
    samples = seg_start[seg_of] + t[:, None] * seg_vec[seg_of]
    sample_curve = seg_curve[seg_of]

    # 각 곡선의 마지막 점(위에서 제외된 유일한 점) 추가
    last_idx = np.cumsum(lengths) - 1
    samples = np.concatenate([samples, all_pts[last_idx]], axis=0)
    sample_curve = np.concatenate(
        [sample_curve, np.arange(n_curves, dtype=np.int64)])
    return samples, sample_curve, palette


def _reduce_voxels(key, sample_curve, palette):
    """복셀 키별로 밀도(count)와 채널별 평균색을 구한다."""
    uniq, inverse, counts = np.unique(
        key, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    mean_colors = np.empty((len(uniq), 3))
    for c in range(3):
        mean_colors[:, c] = np.bincount(
            inverse, weights=palette[sample_curve, c], minlength=len(uniq))
    mean_colors /= counts[:, None]
    return uniq, counts, mean_colors


def _voxelize(points_list, colors_list, origin: np.ndarray, voxel_size: float):
    """단일 데이터셋 복셀화 (지역 격자). return: (centers, counts, mean_colors)."""
    if voxel_size <= 0.0:
        raise ValueError("voxel_size must be > 0")
    if not points_list:
        return (np.empty((0, 3)), np.empty((0,), dtype=np.int64), np.empty((0, 3)))

    samples, sample_curve, palette = _resample(points_list, colors_list, voxel_size)
    origin32 = origin.astype(np.float32)
    inv_vs = np.float32(1.0 / voxel_size)
    idx = np.floor((samples - origin32) * inv_vs).astype(np.int64)
    imin = idx.min(axis=0)
    rel = idx - imin
    dims = rel.max(axis=0) + 1
    key = (rel[:, 0] * dims[1] + rel[:, 1]) * dims[2] + rel[:, 2]
    uniq, counts, mean_colors = _reduce_voxels(key, sample_curve, palette)
    plane = dims[1] * dims[2]
    rem = uniq % plane
    uniq3d = np.stack([uniq // plane, rem // dims[2], rem % dims[2]], axis=1) + imin
    centers = origin + (uniq3d + 0.5) * voxel_size
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


def _author_group(stage, group_root, centers, counts, bucket_idx, reps,
                  voxel_size, radius_factor, density_to_scale):
    """한 그룹을 group_root 아래 단일 PointInstancer 로 저작한다.

    프로토타입은 인스턴서 하위(instancer/Prototypes)에 중첩 → Hydra 가 원점에
    직접 그리지 않고 인스턴스로만 렌더한다. bucket_idx/reps 는 미리 계산.
    큰 배열은 Vt.*Array.FromNumpy 로 벡터화 저작.
    """
    UsdGeom.Xform.Define(stage, group_root)
    instancer_path = group_root + "/instancer"
    instancer = UsdGeom.PointInstancer.Define(stage, instancer_path)
    proto_scope = instancer_path + "/Prototypes"   # 인스턴서 하위로 중첩
    looks = group_root + "/Looks"
    UsdGeom.Scope.Define(stage, proto_scope)
    UsdGeom.Scope.Define(stage, looks)

    proto_paths = []
    for k, rep in enumerate(reps):
        proto_path = f"{proto_scope}/proto_{k}"
        sphere = UsdGeom.Sphere.Define(stage, proto_path)
        # radius 를 프로토타입에 두면 이후 슬라이더로 전 인스턴스 일괄 조정 가능
        sphere.CreateRadiusAttr(float(radius_factor))
        col = Gf.Vec3f(float(rep[0]), float(rep[1]), float(rep[2]))
        sphere.CreateDisplayColorAttr(Vt.Vec3fArray([col]))
        # RTX 에서 확실히 보이도록 대표색 UsdPreviewSurface 바인딩
        mat_path = f"{looks}/mat_{k}"
        mat = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path + "/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(col)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(mat)
        proto_paths.append(Sdf.Path(proto_path))

    # scale 에는 voxel 크기(+밀도 옵션)만 넣고, 반경은 프로토타입 radius 가 담당
    # → 슬라이더가 proto radius 만 바꿔도 전 인스턴스가 즉시 리사이즈된다
    base = float(voxel_size)
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


def _author_raw(stage, source_path, raw_paths, visible):
    """group_paths 밖(ungrouped) 곡선을 연산 없이 원본 그대로 참조해서 가져온다."""
    if not raw_paths:
        return 0
    UsdGeom.Xform.Define(stage, RAW_PATH)
    for i, path_str in enumerate(raw_paths):
        dst = f"{RAW_PATH}/raw_{i}"
        prim = stage.DefinePrim(dst)
        prim.GetReferences().AddReference(source_path, path_str)
    imageable = UsdGeom.Imageable(stage.GetPrimAtPath(RAW_PATH))
    imageable.CreateVisibilityAttr().Set(
        UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)
    return len(raw_paths)


def set_sphere_radius(radius: float) -> str:
    """최적화 결과의 모든 프로토타입 구 radius 를 일괄 변경한다 (라이브 슬라이더용).

    인스턴스 scale 은 voxel 크기만 담고 있으므로, 프로토타입 radius 몇 개만
    고치면 전체 인스턴스가 즉시 리사이즈된다.
    """
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage"
    root = stage.GetPrimAtPath(MERGED_PATH)
    if not root:
        return "ERROR: no optimized result in scene (run Voxelize first)"
    n = 0
    for prim in Usd.PrimRange(root):
        if prim.IsA(UsdGeom.Sphere):
            UsdGeom.Sphere(prim).GetRadiusAttr().Set(float(radius))
            n += 1
    return f"radius={radius:.3g} applied to {n} prototype sphere(s)"


def set_raw_visible(visible: bool) -> str:
    """group_paths 밖(ungrouped) 원본 지오메트리의 visibility 를 즉시 토글한다."""
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage"
    prim = stage.GetPrimAtPath(RAW_PATH)
    if not prim:
        return "no raw/ungrouped geometry in scene"
    UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(
        UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)
    return f"raw/ungrouped geometry visible={visible}"


def _partition(points_list, colors_list, groups_list):
    """그룹 키별로 (points, colors) 인덱스를 묶는다. return: ordered dict-like list."""
    order = []
    buckets = {}
    for i, g in enumerate(groups_list):
        if g not in buckets:
            buckets[g] = ([], [])
            order.append(g)
        buckets[g][0].append(points_list[i])
        buckets[g][1].append(colors_list[i])
    return [(g, buckets[g][0], buckets[g][1]) for g in order]


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def _setup_root(stage):
    """/World 와 MERGED_PATH(기존 결과 제거 후 재생성)를 준비한다."""
    if not stage.GetPrimAtPath("/World"):
        UsdGeom.Xform.Define(stage, "/World")
    if stage.GetPrimAtPath(MERGED_PATH):
        stage.RemovePrim(MERGED_PATH)
    UsdGeom.Xform.Define(stage, MERGED_PATH)


def _result_msg(n_curves, n_src_pts, totals, voxel_size, n_groups, n_raw):
    n_voxels, n_protos = totals
    print(f"[curve] voxelize: curves {n_curves}, pts {n_src_pts} -> "
          f"instances {n_voxels}, color-buckets {n_protos}, groups {n_groups}, "
          f"raw(ungrouped) {n_raw}, voxel_size={voxel_size:.4g}")
    return (f"OK: curves {n_curves} / pts {n_src_pts} -> {n_voxels} instances "
            f"(1 per voxel, color-averaged) | color-buckets {n_protos} | "
            f"groups {n_groups} | raw {n_raw} | voxel={voxel_size:.4g} | "
            f"{MERGED_PATH}")


def optimize_and_load(source_path: str, voxel_size: float = 0.0,
                      resolution: int = 128, radius_factor: float = 0.5,
                      density_to_scale: bool = False, color_levels: int = 8,
                      group_paths: list = None, raw_visible: bool = True) -> str:
    """streamline USD 를 색-인지 복셀 다운샘플링해 현재 씬에 로드한다.

    - 복셀당 인스턴스 1개, 겹치는 색은 평균 / 비슷한 색은 color_levels 로 버킷 양자화
    - group_paths(대상 xform 경로 리스트)가 있으면 각 경로 하위 곡선끼리 그룹으로
      묶어 별도 PointInstancer 저작. 대상 밖("ungrouped") 곡선은 연산하지 않고
      원본을 그대로 참조해서 가져온다 (raw_visible 로 초기 표시 여부 지정).
    - voxel_size<=0 이면 root world bbox 최대변 / resolution 으로 자동 산출
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    points_list, colors_list, groups_list, raw_paths, _n, skipped_empty, mins, maxs = \
        _collect_curves(src, group_paths)
    if not points_list and not raw_paths:
        hint = ""
        if skipped_empty:
            hint = (f"\n({skipped_empty} curve(s) skipped: points/counts "
                    "unreadable - time-sampled or empty)")
        return (f"ERROR: no valid curves found: {source_path}{hint}\n"
                f"{inspect_source(source_path)}")

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage (open a scene first)"
    _setup_root(stage)

    n_raw = _author_raw(stage, source_path, raw_paths, raw_visible)

    if not points_list:
        return (f"OK: no curves matched group_paths | raw(ungrouped) {n_raw} | "
                f"{MERGED_PATH}")

    n_src_pts = sum(len(p) for p in points_list)
    if voxel_size <= 0.0:
        voxel_size = float(np.max(maxs - mins)) / max(int(resolution), 1)
    if voxel_size <= 0.0:
        return "ERROR: failed to derive voxel_size (empty bbox)"
    origin = mins.astype(np.float64)

    parts = _partition(points_list, colors_list, groups_list)
    tot_vox = tot_proto = 0
    for gi, (gkey, pl, cl) in enumerate(parts):
        centers, counts, mean_colors = _voxelize(pl, cl, origin, voxel_size)
        bucket_idx, reps = _quantize_colors(mean_colors, color_levels)
        group_root = f"{MERGED_PATH}/{_safe_name(gkey, gi)}"
        n_protos, n_vox = _author_group(
            stage, group_root, centers, counts, bucket_idx, reps,
            voxel_size, radius_factor, density_to_scale)
        print(f"[curve]   group '{gkey}': curves {len(pl)} -> "
              f"instances {n_vox}, color-buckets {n_protos}")
        tot_vox += n_vox
        tot_proto += n_protos

    return _result_msg(len(points_list), n_src_pts, (tot_vox, tot_proto),
                       voxel_size, len(parts), n_raw)


async def optimize_and_load_async(source_path: str, voxel_size: float = 0.0,
                                  resolution: int = 128,
                                  radius_factor: float = 0.5,
                                  density_to_scale: bool = False,
                                  color_levels: int = 8,
                                  group_paths: list = None,
                                  raw_visible: bool = True,
                                  progress=None) -> str:
    """optimize_and_load 의 비동기 버전 — Kit 멈춤 방지.

    - USD 읽기: 주기적으로 UI 에 양보 / 무거운 numpy: 백그라운드 스레드 오프로드
    - 그룹마다 별도 PointInstancer 저작, 그룹 사이에 한 프레임 양보
    - group_paths 밖(ungrouped) 곡선은 연산하지 않고 원본을 그대로 참조
    - progress 콜백에 단계별 진행률(%)을 보고: [1/3] read, [2/3] voxelize, [3/3] author
    """
    def report(msg):
        if progress:
            progress(msg)

    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    report("[1/3] reading curves... 0%")
    points_list, colors_list, groups_list, raw_paths, _n, skipped_empty, mins, maxs = \
        await _collect_curves_async(src, group_paths, report)
    if not points_list and not raw_paths:
        hint = ""
        if skipped_empty:
            hint = (f"\n({skipped_empty} curve(s) skipped: points/counts "
                    "unreadable - time-sampled or empty)")
        return (f"ERROR: no valid curves found: {source_path}{hint}\n"
                f"{inspect_source(source_path)}")

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage (open a scene first)"
    _setup_root(stage)

    n_raw = _author_raw(stage, source_path, raw_paths, raw_visible)

    if not points_list:
        return (f"OK: no curves matched group_paths | raw(ungrouped) {n_raw} | "
                f"{MERGED_PATH}")

    n_src_pts = sum(len(p) for p in points_list)
    if voxel_size <= 0.0:
        voxel_size = float(np.max(maxs - mins)) / max(int(resolution), 1)
    if voxel_size <= 0.0:
        return "ERROR: failed to derive voxel_size (empty bbox)"
    origin = mins.astype(np.float64)

    loop = asyncio.get_event_loop()
    app = omni.kit.app.get_app()
    parts = _partition(points_list, colors_list, groups_list)
    n_parts = len(parts)
    tot_vox = tot_proto = 0
    for gi, (gkey, pl, cl) in enumerate(parts):
        pct = 100 * gi // n_parts
        report(f"[2/3] voxelizing... {pct}% (group {gi + 1}/{n_parts} '{gkey}')")
        centers, counts, mean_colors = await loop.run_in_executor(
            None, _voxelize, pl, cl, origin, voxel_size)
        bucket_idx, reps = await loop.run_in_executor(
            None, _quantize_colors, mean_colors, color_levels)
        await app.next_update_async()
        pct = 100 * (2 * gi + 1) // (2 * n_parts)
        report(f"[3/3] authoring... {pct}% (group {gi + 1}/{n_parts} '{gkey}')")
        group_root = f"{MERGED_PATH}/{_safe_name(gkey, gi)}"
        n_protos, n_vox = _author_group(
            stage, group_root, centers, counts, bucket_idx, reps,
            voxel_size, radius_factor, density_to_scale)
        print(f"[curve]   group '{gkey}': curves {len(pl)} -> "
              f"instances {n_vox}, color-buckets {n_protos}")
        tot_vox += n_vox
        tot_proto += n_protos
        await app.next_update_async()
    report("[3/3] authoring... 100%")

    return _result_msg(len(points_list), n_src_pts, (tot_vox, tot_proto),
                       voxel_size, n_parts, n_raw)
