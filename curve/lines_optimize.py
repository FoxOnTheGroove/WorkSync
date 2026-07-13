"""streamline USD 최적화 핵심 로직 (색-인지 복셀 PointInstancer 방식).

소스: 곡선(BasisCurves/NurbsCurves) 다수, 각자 고유 머티리얼을 가질 수 있음.
개별 흐름 궤적은 필요 없고 "어느 영역에 무슨 색이 얼마나 있나"(색·밀도 분포)만
보면 되므로, 곡선을 **복셀 그리드로 다운샘플링**하여 단일 **UsdGeomPointInstancer**
로 표현한다. .usd/.usdz 소스 모두 Usd.Stage.Open() 이 네이티브로 처리한다.

핵심 규칙:
- **복셀당 인스턴스 1개.** 같은 복셀에 여러 색이 겹치면 그 색들을 **평균**내어
  하나만 둔다(중복 스피어 제거).
- 비슷한 색끼리 버킷으로 양자화(color_levels)하여 대표색 프로토타입 수를 소수로
  줄인다. 각 인스턴스는 자기 복셀 색에 가장 가까운 버킷 프로토타입을 가리킨다.
- 곡선을 개별 Sphere prim 으로 만들지 않고 인스턴싱 → draw call 소수 유지.
- group_paths 로 지정한 대상 xform 하위 곡선만 복셀 연산 대상이고, 대상 밖
  ("ungrouped") 곡선은 연산하지 않고 원본을 그대로 참조해서 가져온다.
- 결과 계층은 원본/타겟 절대경로를 그대로 미러링한다 (flat 이름으로 뭉개지 않음):
  grouped 는 MERGED_PATH + 타겟경로 자리에 instancer 가 자식으로,
  ungrouped 는 MERGED_PATH + 원본경로 자리에 원본을 참조(월드 변환은 구워서 위치 보존).

색 추출: 1) 곡선의 displayColor primvar  2) 바인딩/형제 머티리얼의 셰이더 색 입력.
지오메트리가 time-sample 로만 저장된 경우도 첫 샘플로 폴백해 읽는다.
모든 실질 구현은 이 파일에 있다. UI 는 dummy_ui.py 에서 이 함수들만 호출한다.
"""

import asyncio
import os
import time
from collections import Counter

import numpy as np

from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf, Tf
import omni.usd
import omni.kit.app


MERGED_PATH = "/World/OptimizedStreamlines"
RAW_MARKER_ATTR = "curveOptim:raw"   # ungrouped 미러링 prim 표식 (일괄 visibility 토글용)
DEFAULT_COLOR = np.array([0.8, 0.8, 0.8])

# 머티리얼 셰이더에서 색으로 읽어볼 입력 이름들 (UsdPreviewSurface / MDL 계열).
# emissive 를 먼저 본다 — streamline 은 조명 없이도 보이도록 발광(unlit)형
# 셰이더를 쓰는 경우가 흔한데, 그 경우 실제 눈에 보이는 색은 diffuse 가 아니라
# emissive 쪽에 있다 (diffuse 는 기본값/미사용인 채로 남아있기 쉬움).
_PREFERRED_COLOR_INPUTS = [
    "emissiveColor", "emissive_color",
    "diffuseColor", "diffuse_color_constant", "base_color", "diffuse_tint",
    "diffuse_reflection_color", "albedo",
]
_COLOR_TYPE_NAMES = (Sdf.ValueTypeNames.Color3f, Sdf.ValueTypeNames.Float3,
                    Sdf.ValueTypeNames.Vector3f)


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


def _resolve_input_color(inp, tc, depth=0):
    """UsdShade Input 하나에서 색을 뽑는다.

    상수로 authoring 돼 있으면 바로 읽고, 다른 셰이더 출력에 연결(connected)돼
    있으면 그 소스 셰이더까지 1단계 따라가 본다 (텍스처/셰이더 그래프에 물린
    색이라 상수 읽기가 실패하는 흔한 경우 대응).
    """
    v = _get(inp.GetAttr(), tc)
    if v is not None and hasattr(v, "__len__") and len(v) >= 3:
        return np.array([float(v[0]), float(v[1]), float(v[2])])
    if depth >= 1 or not inp.HasConnectedSource():
        return None
    src_api, _src_name, _src_type = inp.GetConnectedSource()
    src_prim = src_api.GetPrim()
    if not src_prim.IsA(UsdShade.Shader):
        return None
    return _shader_color(src_prim, tc, depth=depth + 1)


def _shader_color(shader_prim: Usd.Prim, tc, depth=0):
    """셰이더에서 대표 색을 읽는다. 없으면 None.

    1) 알려진 이름(_PREFERRED_COLOR_INPUTS, emissive 우선)으로 탐색
    2) 그래도 없으면 이름 무관하게 색 타입(Color3f/Float3/Vector3f) 입력을
       전부 시도 (커스텀/MDL 등 이름 목록에 없는 셰이더 대응)
    """
    shader = UsdShade.Shader(shader_prim)
    for name in _PREFERRED_COLOR_INPUTS:
        inp = shader.GetInput(name)
        if inp:
            c = _resolve_input_color(inp, tc, depth)
            if c is not None:
                return c
    for inp in shader.GetInputs():
        if inp.GetTypeName() in _COLOR_TYPE_NAMES:
            c = _resolve_input_color(inp, tc, depth)
            if c is not None:
                return c
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
        어느 대상에도 안 속함  → "ungrouped" (연산 대상 제외, 원본 그대로 미러링)
    targets 가 비면 전부 "all" (그룹 분류 없음, 전부 연산 대상).
    """
    if not targets:
        return "all"
    for t in targets:
        if path_str == t or path_str.startswith(t + "/"):
            return t
    return "ungrouped"


def _sanitize_usd_path(path_str: str) -> str:
    """경로 문자열을 세그먼트별로 유효한 prim 이름으로 정리해 재조합한다.

    원본 절대경로/사용자가 입력한 타겟 경로를 그대로 계층에 미러링할 때,
    각 세그먼트가 유효한 USD 식별자가 되도록 보정한다 (구조는 그대로 유지).
    """
    parts = [p for p in path_str.strip("/").split("/") if p]
    safe = []
    for p in parts:
        ident = Tf.MakeValidIdentifier(p) if hasattr(Tf, "MakeValidIdentifier") else p
        safe.append(ident or "_")
    return "/" + "/".join(safe) if safe else ""


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
    n_color_fallback = 0   # 색 추출 실패 -> DEFAULT_COLOR(회색) 대체 예정 개수
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
            else:
                n_color_fallback += 1

    color_line = "colors: none"
    if colors:
        arr = np.array(colors)
        exact = len(np.unique(np.round(arr, 4), axis=0))
        approx = len(np.unique(np.round(arr, 1), axis=0))
        color_line = (f"colors: {len(colors)} read | unique(round4)={exact} | "
                      f"grouped(round1)={approx}")
    if n_color_fallback:
        color_line += (f" | ⚠ {n_color_fallback} curve(s) will fall back to "
                       "default gray (color unreadable: no displayColor, no "
                       "constant color input on bound material)")

    n_proto = len(src.GetPrototypes())
    top = ", ".join(f"{t}:{c}" for t, c in types.most_common(15))
    return (f"curves={n_curve} | default-pts={n_def} | timesampled-pts={n_ts} | "
            f"instanceable={n_inst} | prototypes={n_proto}\n{color_line}\n"
            f"types: {top}")


# ---------------------------------------------------------------------------
# 소스에서 곡선 + 색 수집 (world 좌표)
# ---------------------------------------------------------------------------
def _extract_curve(prim: Usd.Prim, tc, xform_cache):
    """곡선 prim → (segments[list of np(N,3) world], color np(3), wmin, wmax,
    used_default_color bool). 비면 None.

    used_default_color 는 원본 색 추출에 실패해 DEFAULT_COLOR(회색)로 대체됐는지
    표시 — 진단/결과 메시지에서 "색이 왜 흐려 보이는지" 바로 알 수 있게 한다.
    """
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
    used_default = color is None
    if used_default:
        color = DEFAULT_COLOR.copy()

    cnts = np.array(counts, dtype=np.int64)
    segs = []
    offset = 0
    for c in cnts:
        seg = world[offset:offset + int(c)]
        offset += int(c)
        if len(seg) >= 1:
            segs.append(seg)
    return segs, color, world.min(axis=0), world.max(axis=0), used_default


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
    """동기 수집. group_paths 밖("ungrouped") 곡선은 연산하지 않고 (경로, world 행렬)만
    기록한다 (행렬 하나만 조회 — O(depth), 점 데이터는 안 읽음).

    return: (pl, cl, gl, raw_entries, n, skipped, mins, maxs, n_default_color)
      raw_entries = [(path_str, Gf.Matrix4d world_xform), ...]
      n_default_color = 원본 색 추출 실패로 DEFAULT_COLOR(회색) 로 대체된 곡선 수
    """
    targets = group_paths or []
    prims, tc = _curve_prims_and_timecode(stage)
    xc = UsdGeom.XformCache(tc)
    pl, cl, gl, skipped, raw_entries, n_default = [], [], [], 0, [], 0
    mins = np.full(3, np.inf)
    maxs = np.full(3, -np.inf)
    for prim in prims:
        path_str = prim.GetPath().pathString
        gkey = _group_for_path(path_str, targets)
        if gkey == "ungrouped":
            raw_entries.append((path_str, xc.GetLocalToWorldTransform(prim)))
            continue
        r = _extract_curve(prim, tc, xc)
        if r is None:
            skipped += 1
            continue
        segs, color, wmin, wmax, used_default = r
        if used_default:
            n_default += 1
        mins = np.minimum(mins, wmin)
        maxs = np.maximum(maxs, wmax)
        for s in segs:
            pl.append(s)
            cl.append(color)
            gl.append(gkey)
    return pl, cl, gl, raw_entries, len(prims), skipped, mins, maxs, n_default


async def _collect_curves_async(stage: Usd.Stage, group_paths: list = None,
                                report=None, yield_interval: float = 0.05):
    """비동기 수집. yield_interval 초 이상 일했을 때만 UI 에 양보한다."""
    targets = group_paths or []
    prims, tc = _curve_prims_and_timecode(stage)
    xc = UsdGeom.XformCache(tc)
    app = omni.kit.app.get_app()
    total = len(prims)

    pl, cl, gl, skipped, raw_entries, n_default = [], [], [], 0, [], 0
    mins = np.full(3, np.inf)
    maxs = np.full(3, -np.inf)
    last_yield = time.perf_counter()
    for i, prim in enumerate(prims):
        path_str = prim.GetPath().pathString
        gkey = _group_for_path(path_str, targets)
        if gkey == "ungrouped":
            raw_entries.append((path_str, xc.GetLocalToWorldTransform(prim)))
        else:
            r = _extract_curve(prim, tc, xc)
            if r is None:
                skipped += 1
            else:
                segs, color, wmin, wmax, used_default = r
                if used_default:
                    n_default += 1
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
    return pl, cl, gl, raw_entries, total, skipped, mins, maxs, n_default


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


def _group_root_path(gkey: str) -> str:
    """그룹 키 → 결과 계층 경로. 'all' 이면 MERGED_PATH 자체, 아니면 타겟 경로를
    MERGED_PATH 밑에 그대로 미러링 (flat 이름으로 뭉개지 않음)."""
    if gkey == "all":
        return MERGED_PATH
    return MERGED_PATH + _sanitize_usd_path(gkey)


def _define_proto_shape(stage, proto_path, shape, radius_factor):
    """프로토타입 지오메트리 1개 정의. shape: 'sphere' 또는 'cube'.

    Sphere 는 radius, Cube 는 size(변 길이) 로 반경 개념을 구현 — Cube 는
    radius_factor*2 를 변 길이로 써서 sphere 와 시각적 크기를 맞춘다.
    return: (geom_prim, geom_schema) — geom_schema 는 이후 방사형 크기 조정에 사용.
    """
    if shape == "cube":
        cube = UsdGeom.Cube.Define(stage, proto_path)
        cube.CreateSizeAttr(float(radius_factor) * 2.0)
        return cube
    sphere = UsdGeom.Sphere.Define(stage, proto_path)
    # radius 를 프로토타입에 두면 이후 슬라이더로 전 인스턴스 일괄 조정 가능
    sphere.CreateRadiusAttr(float(radius_factor))
    return sphere


def _author_group(stage, group_root, centers, counts, bucket_idx, reps,
                  voxel_size, radius_factor, density_to_scale, proto_shape="sphere"):
    """한 그룹을 group_root 아래 단일 PointInstancer 로 저작한다.

    프로토타입은 인스턴서 하위(instancer/Prototypes)에 중첩 → Hydra 가 원점에
    직접 그리지 않고 인스턴스로만 렌더한다. bucket_idx/reps 는 미리 계산.
    큰 배열은 Vt.*Array.FromNumpy 로 벡터화 저작.
    proto_shape: 'sphere'(기본) 또는 'cube' — 프로토타입 지오메트리 종류.
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
        geom = _define_proto_shape(stage, proto_path, proto_shape, radius_factor)
        col = Gf.Vec3f(float(rep[0]), float(rep[1]), float(rep[2]))
        geom.CreateDisplayColorAttr(Vt.Vec3fArray([col]))
        # RTX 에서 확실히 보이도록 대표색 UsdPreviewSurface 바인딩
        mat_path = f"{looks}/mat_{k}"
        mat = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path + "/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(col)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(geom.GetPrim()).Bind(mat)
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


def _author_raw(stage, source_path, raw_entries, visible):
    """group_paths 밖(ungrouped) 곡선을 연산 없이 원본 계층 그대로 미러링해 가져온다.

    각 원본 절대경로를 MERGED_PATH 밑에 그대로 재현하고(중간 조상은 DefinePrim 이
    자동 생성), 리프에 world 변환(1회 조회, O(depth))을 구워 원본 위치를 재현한
    뒤 원본 prim 을 참조한다. RAW_MARKER_ATTR 로 표식해 일괄 visibility 토글이
    가능하게 한다.
    """
    if not raw_entries:
        return 0
    for path_str, mat in raw_entries:
        dst = MERGED_PATH + _sanitize_usd_path(path_str)
        xf = UsdGeom.Xform.Define(stage, dst)
        xf.ClearXformOpOrder()
        xf.AddTransformOp().Set(mat)
        prim = xf.GetPrim()
        prim.GetReferences().AddReference(source_path, path_str)
        prim.CreateAttribute(RAW_MARKER_ATTR, Sdf.ValueTypeNames.Bool, custom=True).Set(True)
        xf.CreateVisibilityAttr().Set(
            UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)
    return len(raw_entries)


def set_sphere_radius(radius: float) -> str:
    """최적화 결과의 모든 프로토타입 반경을 일괄 변경한다 (라이브 슬라이더용).

    Sphere/Cube 어느 쪽으로 만들어졌든 대응: Sphere 는 radius, Cube 는 size
    (radius*2, sphere 와 시각적 크기를 맞춤) 로 설정. 인스턴스 scale 은 voxel
    크기만 담고 있으므로, 프로토타입 몇 개만 고치면 전체 인스턴스가 즉시
    리사이즈된다.
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
        elif prim.IsA(UsdGeom.Cube):
            UsdGeom.Cube(prim).GetSizeAttr().Set(float(radius) * 2.0)
            n += 1
    return f"radius={radius:.3g} applied to {n} prototype shape(s)"


def set_raw_visible(visible: bool) -> str:
    """group_paths 밖(ungrouped) 원본 지오메트리의 visibility 를 일괄 토글한다.

    미러링된 raw prim 들은 흩어져 있으므로(원본 계층 유지), RAW_MARKER_ATTR 로
    표식된 prim 을 전부 찾아서 토글한다.
    """
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage"
    root = stage.GetPrimAtPath(MERGED_PATH)
    if not root:
        return "no optimized result in scene"
    n = 0
    for prim in Usd.PrimRange(root):
        if prim.HasAttribute(RAW_MARKER_ATTR):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(
                UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)
            n += 1
    return f"raw/ungrouped geometry visible={visible} ({n} prim(s))"


def save_voxelized(source_path: str) -> str:
    """생성된 OptimizedStreamlines 프림(raw 포함)을 자기완결 usd 파일로 저장한다.

    저장 경로: source_path 와 같은 디렉터리에 "{원본이름}_voxelized.usd".
    OptimizedStreamlines 라는 wrapper 는 저장 파일에 남기지 않고, 그 바로 아래
    자식들(예: group_paths="/root/target" 이면 "root", raw 미러링이면 원본
    최상위 세그먼트 등)을 파일의 최상단 prim 으로 그대로 승격한다.
        (라이브 씬) OptimizedStreamlines / root / target / ...
        (저장 파일) root / target / ...              <- wrapper 없이 최상단

    임시 stage 에 OptimizedStreamlines 의 각 자식을 현재 stage 의 해당 경로에
    대한 참조로 최상단에 걸고 Stage.Flatten() 하면, 원본 곡선·머티리얼 참조를
    포함한 모든 합성 결과가 실제 값으로 구워진다 → source_path 에 더 이상
    의존하지 않는 자기완결 파일이 된다.
    """
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return "ERROR: no active stage"
    src_prim = stage.GetPrimAtPath(MERGED_PATH)
    if not src_prim or not src_prim.IsValid():
        return "ERROR: no OptimizedStreamlines to save (run Voxelize & Load first)"

    children = list(src_prim.GetChildren())
    if not children:
        return "ERROR: OptimizedStreamlines has no content to save"

    base = os.path.splitext(os.path.basename(source_path))[0]
    out_dir = os.path.dirname(source_path) or "."
    out_path = os.path.join(out_dir, f"{base}_voxelized.usd")

    tmp_stage = Usd.Stage.CreateInMemory()
    top_prims = []
    for child in children:
        dst_path = f"/{child.GetName()}"
        dst_prim = tmp_stage.DefinePrim(dst_path)
        dst_prim.GetReferences().AddReference(
            stage.GetRootLayer().identifier, child.GetPath())
        top_prims.append(dst_prim)
    tmp_stage.SetDefaultPrim(top_prims[0])   # 최상단 prim 이 하나면 그게 defaultPrim

    flat_layer = tmp_stage.Flatten()
    flat_layer.Export(out_path)
    return f"Saved (self-contained): {out_path}"


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


def _result_msg(n_curves, n_src_pts, totals, voxel_size, n_groups, n_raw,
                n_default_color=0):
    n_voxels, n_protos = totals
    print(f"[curve] voxelize: curves {n_curves}, pts {n_src_pts} -> "
          f"instances {n_voxels}, color-buckets {n_protos}, groups {n_groups}, "
          f"raw(ungrouped) {n_raw}, default-color {n_default_color}, "
          f"voxel_size={voxel_size:.4g}")
    color_hint = ""
    if n_default_color:
        color_hint = (f" | ⚠ {n_default_color}/{n_curves} curves used default "
                      "gray (color unreadable)")
    return (f"OK: curves {n_curves} / pts {n_src_pts} -> {n_voxels} instances "
            f"(1 per voxel, color-averaged) | color-buckets {n_protos} | "
            f"groups {n_groups} | raw {n_raw}{color_hint} | voxel={voxel_size:.4g} | "
            f"{MERGED_PATH}")


def optimize_and_load(source_path: str, voxel_size: float = 0.0,
                      resolution: int = 128, radius_factor: float = 0.5,
                      density_to_scale: bool = False, color_levels: int = 8,
                      group_paths: list = None, raw_visible: bool = True,
                      proto_shape: str = "sphere") -> str:
    """streamline USD/USDZ 를 색-인지 복셀 다운샘플링해 현재 씬에 로드한다.

    - 복셀당 인스턴스 1개, 겹치는 색은 평균 / 비슷한 색은 color_levels 로 버킷 양자화
    - group_paths(대상 xform 경로 리스트)가 있으면 각 경로 하위 곡선끼리 그룹으로
      묶어 별도 PointInstancer 저작(타겟 경로 그대로 계층에 미러링). 대상 밖
      ("ungrouped") 곡선은 연산하지 않고 원본 계층을 그대로 미러링해 참조한다.
    - voxel_size<=0 이면 root world bbox 최대변 / resolution 으로 자동 산출
    - proto_shape: 'sphere'(기본) 또는 'cube' — 프로토타입 지오메트리 종류
    """
    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    points_list, colors_list, groups_list, raw_entries, _n, skipped_empty, mins, maxs, \
        n_default_color = _collect_curves(src, group_paths)
    if not points_list and not raw_entries:
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

    n_raw = _author_raw(stage, source_path, raw_entries, raw_visible)

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
    for gkey, pl, cl in parts:
        centers, counts, mean_colors = _voxelize(pl, cl, origin, voxel_size)
        bucket_idx, reps = _quantize_colors(mean_colors, color_levels)
        group_root = _group_root_path(gkey)
        n_protos, n_vox = _author_group(
            stage, group_root, centers, counts, bucket_idx, reps,
            voxel_size, radius_factor, density_to_scale, proto_shape)
        print(f"[curve]   group '{gkey}': curves {len(pl)} -> "
              f"instances {n_vox}, color-buckets {n_protos}")
        tot_vox += n_vox
        tot_proto += n_protos

    return _result_msg(len(points_list), n_src_pts, (tot_vox, tot_proto),
                       voxel_size, len(parts), n_raw, n_default_color)


async def optimize_and_load_async(source_path: str, voxel_size: float = 0.0,
                                  resolution: int = 128,
                                  radius_factor: float = 0.5,
                                  density_to_scale: bool = False,
                                  color_levels: int = 8,
                                  group_paths: list = None,
                                  raw_visible: bool = True,
                                  proto_shape: str = "sphere",
                                  progress=None) -> str:
    """optimize_and_load 의 비동기 버전 — Kit 멈춤 방지.

    - USD 읽기: 주기적으로 UI 에 양보 / 무거운 numpy: 백그라운드 스레드 오프로드
    - 그룹마다 별도 PointInstancer 저작, 그룹 사이에 한 프레임 양보
    - group_paths 밖(ungrouped) 곡선은 연산하지 않고 원본 계층을 그대로 미러링
    - proto_shape: 'sphere'(기본) 또는 'cube' — 프로토타입 지오메트리 종류
    - progress 콜백에 단계별 진행률(%)을 보고: [1/3] read, [2/3] voxelize, [3/3] author
    """
    def report(msg):
        if progress:
            progress(msg)

    src = Usd.Stage.Open(source_path)
    if not src:
        return f"ERROR: cannot open: {source_path}"

    report("[1/3] reading curves... 0%")
    points_list, colors_list, groups_list, raw_entries, _n, skipped_empty, mins, maxs, \
        n_default_color = await _collect_curves_async(src, group_paths, report)
    if not points_list and not raw_entries:
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

    n_raw = _author_raw(stage, source_path, raw_entries, raw_visible)

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
        group_root = _group_root_path(gkey)
        n_protos, n_vox = _author_group(
            stage, group_root, centers, counts, bucket_idx, reps,
            voxel_size, radius_factor, density_to_scale, proto_shape)
        print(f"[curve]   group '{gkey}': curves {len(pl)} -> "
              f"instances {n_vox}, color-buckets {n_protos}")
        tot_vox += n_vox
        tot_proto += n_protos
        await app.next_update_async()
    report("[3/3] authoring... 100%")

    return _result_msg(len(points_list), n_src_pts, (tot_vox, tot_proto),
                       voxel_size, n_parts, n_raw, n_default_color)
