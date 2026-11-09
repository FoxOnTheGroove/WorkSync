"""Face under a picked point: the planar region (or analytic surface) the hit lies on.

``FaceComputer.compute(stage, prim_path, hit, ...)`` (or ``await compute_async(...)``) returns a
``Face`` dict (world coordinates, stage units)::

    {"kind": str, "center": [x, y, z], "normal": [x, y, z], "area": float,
     "size": [w, h], "outline": [[x, y, z], ...]}

- ``kind``: ``"planar"`` (flat region), ``"cylinder_side"``, ``"sphere"``, ``"polygon"`` (one
  polygon of a curved mesh, or of a mesh over the size cap) or ``"point"`` (unsupported prim /
  fallback: centre = hit).
- UsdGeom.Mesh: candidates = faces of the hit polygon's plane component within 1 degree of its normal
  and on its plane (distance <= bbox diagonal x 1e-4); the region is flood-filled from the hit
  polygon through candidates. Degenerate faces (thinner than that distance: zero area, collinear,
  slivers) are judged by plane distance only. Faces with non-finite points are ignored. Vertices
  are welded by position for adjacency, so split-vertex meshes are grouped too. Centre =
  area-weighted centroid. Outline = the longest boundary loop (edges not shared inside the region).
- Size cap: meshes over ``MAX_FACES`` faces or ``MAX_TRIS`` fan triangles get no mesh data; the hit
  polygon is found by streaming the faces in chunks (bounded memory) and returned as ``polygon``.
- Implicit gprims (Cube, Cylinder, Cone, Sphere) are solved analytically in local space and mapped
  by the full local-to-world matrix (non-uniform scale and shear included, normals by the inverse
  transpose).
- ``normal`` faces the camera (``toward`` = point -> camera vector), else the surface outward.
- ``size`` = extent of the outline along the plane basis that a ``plane`` annotation with the same
  normal (and no ``up``) is drawn with (``plane_basis(normal, stage up)``). Cylinder side
  ``[right-section major diameter, generator length]``, sphere ``[2r, 2r]``.
- ``outline`` is a closed ring (last point connects to the first, not repeated), <= 256 points.
- Mesh data is cached per (prim path, time code): at most ``CACHE_SIZE`` meshes and ``CACHE_BYTES``
  (oldest evicted first, the newest always kept), dropped on Usd.Notice ObjectsChanged for the prim
  or an ancestor (and ``clear()`` on stage open / close). ``compute_async`` builds meshes with at
  least ``ASYNC_FACES`` faces on a worker thread.
- The prim path is only used inside the computation (and as the in-memory cache key); it is never
  part of the result.
"""

__all__ = [
    "ASYNC_FACES",
    "CACHE_BYTES",
    "FACE_KINDS",
    "MAX_FACES",
    "MAX_OUTLINE",
    "MAX_TRIS",
    "M_FACE",
    "M_FACE_BUILD",
    "FaceComputer",
    "normalize_face",
    "point_face",
    "plane_basis",
]

import asyncio
import collections
import contextlib
import math
import numbers
from time import perf_counter as _perf_counter
from typing import List, Optional, Tuple

import carb

_EXT_ID = "morph.annotation.overlay"
M_FACE = f"{_EXT_ID}.face_ms"
M_FACE_BUILD = f"{_EXT_ID}.face_build_ms"  # mesh data build / over-cap scan (worker thread or main)
FACE_KINDS = ("planar", "cylinder_side", "sphere", "polygon", "point")
MAX_FACES = 2_000_000  # mesh data (adjacency, planar regions) only up to this many faces ...
MAX_TRIS = 3_000_000  # ... and this many fan triangles; above either: low-memory scan, "polygon"
MAX_OUTLINE = 256
CACHE_SIZE = 8  # meshes kept (LRU) ...
CACHE_BYTES = 512 * 1024 * 1024  # ... within this many bytes of mesh data (the newest always stays)
ASYNC_FACES = 200_000  # compute_async: first build of a mesh this large runs on a worker thread
SLOW_BUILD_S = 1.0  # one warning when a build (or scan) takes longer
CHUNK = 1 << 18  # triangles / positions per chunk of the build (bounded temporaries), multiple of BLOCK
SCAN_FACES = 1 << 17  # faces per chunk of the over-cap scan
CIRCLE_POINTS = 64
COPLANAR_COS = math.cos(math.radians(1.0))
PLANE_TOL = 1e-4  # x bbox diagonal
HIT_EPS = 2e-3  # x max(bbox diagonal, camera distance): first search radius for the hit polygon
SPHERE_RING_DEG = 15.0
BLOCK = 32  # triangles per block of the (Morton-sorted) block list
_WELD_LEVELS = (1 << 21) - 1

try:
    from morph.perf.monitor import scope as _perf_scope
except Exception:  # noqa: BLE001
    _perf_scope = None


def _scope(name: str):
    if _perf_scope is None:
        return contextlib.nullcontext()
    return _perf_scope(name)


# ---------------------------------------------------------------------- small vector helpers (lists)
def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross3(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm3(a):
    length = math.sqrt(_dot3(a, a))
    if length < 1e-300:
        return [0.0, 0.0, 0.0]
    return [a[0] / length, a[1] / length, a[2] / length]


def plane_basis(normal, up_hint) -> Tuple[list, list, list]:
    """(n, horizontal, vertical) unit vectors for a plane. Falls back to another axis when
    ``up_hint`` is parallel to ``normal``. (Also what ``plane`` annotations are drawn with.)"""
    n = _norm3(normal)
    u = _norm3(up_hint) if up_hint is not None else [0.0, 1.0, 0.0]
    if abs(_dot3(u, n)) > 0.999:
        comps = [abs(n[0]), abs(n[1]), abs(n[2])]
        k = comps.index(min(comps))
        u = [0.0, 0.0, 0.0]
        u[k] = 1.0
    d = _dot3(u, n)
    vertical = _norm3([u[0] - n[0] * d, u[1] - n[1] * d, u[2] - n[2] * d])
    horizontal = _norm3(_cross3(vertical, n))
    return n, horizontal, vertical


# ---------------------------------------------------------------------- Face dict
def _vec(name: str, v, n: int) -> List[float]:
    if isinstance(v, (str, bytes, dict)) or not hasattr(v, "__len__") or len(v) != n:
        raise ValueError(f"face '{name}' must be a list of {n} numbers")
    out = []
    for c in v:
        if isinstance(c, bool) or not isinstance(c, numbers.Real):
            raise ValueError(f"face '{name}' must contain numbers")
        f = float(c)
        if not math.isfinite(f):
            raise ValueError(f"face '{name}' must be finite")
        out.append(f)
    return out


def normalize_face(face) -> dict:
    """Validated copy of a Face dict (known keys only, floats). ``ValueError`` when malformed."""
    if not isinstance(face, dict):
        raise ValueError("face must be an object")
    kind = face.get("kind")
    if kind not in FACE_KINDS:
        raise ValueError(f"face 'kind' must be one of {', '.join(FACE_KINDS)}")
    for key in ("center", "normal", "area", "size", "outline"):
        if key not in face:
            raise ValueError(f"face '{key}' is required")
    center = _vec("center", face["center"], 3)
    normal = _vec("normal", face["normal"], 3)
    if _dot3(normal, normal) < 1e-24:
        raise ValueError("face 'normal' must not be a zero vector")
    area = face["area"]
    if isinstance(area, bool) or not isinstance(area, numbers.Real) or not math.isfinite(float(area)) or area < 0:
        raise ValueError("face 'area' must be a finite number >= 0")
    size = _vec("size", face["size"], 2)
    if size[0] < 0 or size[1] < 0:
        raise ValueError("face 'size' must be >= 0")
    outline = face["outline"]
    if not isinstance(outline, (list, tuple)):
        raise ValueError("face 'outline' must be a list of points")
    if len(outline) > MAX_OUTLINE:
        raise ValueError(f"face 'outline' has more than {MAX_OUTLINE} points")
    pts = [_vec(f"outline[{i}]", p, 3) for i, p in enumerate(outline)]
    return {"kind": kind, "center": center, "normal": normal, "area": float(area), "size": size, "outline": pts}


def _fallback_normal(pick_normal, toward, up) -> List[float]:
    for v in (pick_normal, toward, up, (0.0, 0.0, 1.0)):
        if v is None:
            continue
        n = _norm3([float(c) for c in v])
        if _dot3(n, n) > 0.5:
            return n
    return [0.0, 0.0, 1.0]


def point_face(hit, pick_normal=None, toward=None, up=None) -> dict:
    """Fallback face: centre = hit, area 0, no outline. Normal = pick normal, else toward camera."""
    return {
        "kind": "point",
        "center": [float(c) for c in hit],
        "normal": _fallback_normal(pick_normal, toward, up),
        "area": 0.0,
        "size": [0.0, 0.0],
        "outline": [],
    }


# ---------------------------------------------------------------------- numpy geometry helpers
def _np():
    import numpy as np

    return np


def _matrix_np(gf_matrix):
    np = _np()
    return np.array([[float(gf_matrix[i][j]) for j in range(4)] for i in range(4)], dtype=np.float64)


def _xf(M, p):
    """Row-vector affine transform (Gf convention): p @ M3 + t."""
    return p @ M[:3, :3] + M[3, :3]


def _dirv(M, v):
    return v @ M[:3, :3]


def _unit(v):
    np = _np()
    length = float(np.linalg.norm(v))
    if not math.isfinite(length) or length < 1e-300:
        return None
    return v / length


def _ranges(starts, lengths):
    """Concatenated ``arange(s, s + n)`` for every (s, n), vectorized."""
    np = _np()
    lengths = np.asarray(lengths, dtype=np.int64)
    total = int(lengths.sum()) if lengths.size else 0
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    starts = np.asarray(starts, dtype=np.int64)
    offs = np.repeat(starts - (np.cumsum(lengths) - lengths), lengths)
    return offs + np.arange(total, dtype=np.int64)


def _seg_dist(p, a, b):
    np = _np()
    ab = b - a
    denom = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-300)
    t = np.clip(np.einsum("ij,ij->i", p - a, ab) / denom, 0.0, 1.0)
    q = a + ab * t[:, None]
    return np.linalg.norm(p - q, axis=1)


def _point_tri_dist(p, a, b, c):
    """Distance from point ``p`` (3,) to triangles (a, b, c) (k, 3) each."""
    np = _np()
    p = np.broadcast_to(p, a.shape)
    n = np.cross(b - a, c - a)
    nn = np.einsum("ij,ij->i", n, n)
    good = nn > 1e-300
    safe = np.where(good, nn, 1.0)
    ap = p - a
    t = np.einsum("ij,ij->i", ap, n) / safe
    q = p - n * t[:, None]
    s0 = np.einsum("ij,ij->i", np.cross(b - a, q - a), n) >= 0
    s1 = np.einsum("ij,ij->i", np.cross(c - b, q - b), n) >= 0
    s2 = np.einsum("ij,ij->i", np.cross(a - c, q - c), n) >= 0
    inside = good & s0 & s1 & s2
    d_plane = np.abs(t) * np.sqrt(safe)
    d_edge = np.minimum(np.minimum(_seg_dist(p, a, b), _seg_dist(p, b, c)), _seg_dist(p, c, a))
    return np.where(inside, d_plane, d_edge)


def _part1by2(x):
    x = x & 0x3FF
    x = (x | (x << 16)) & 0x030000FF
    x = (x | (x << 8)) & 0x0300F00F
    x = (x | (x << 4)) & 0x030C30C3
    x = (x | (x << 2)) & 0x09249249
    return x


def _polygon_props(P):
    """(area, unit normal | None, centroid) of a polygon (k, 3) by fan triangles (concave ok)."""
    np = _np()
    if len(P) < 3:
        return 0.0, None, P.mean(axis=0) if len(P) else None
    a = P[0]
    b = P[1:-1]
    c = P[2:]
    cr = np.cross(b - a, c - a)
    vec = cr.sum(axis=0)
    length = float(np.linalg.norm(vec))
    if length < 1e-300:
        return 0.0, None, P.mean(axis=0)
    n = vec / length
    w = cr @ n
    tc = (a + b + c) / 3.0
    sw = float(w.sum())
    centroid = (tc * w[:, None]).sum(axis=0) / sw if abs(sw) > 1e-300 else P.mean(axis=0)
    return length / 2.0, n, centroid


def _simplify_ring(P):
    """Drop repeated and collinear points of a closed ring (k, 3)."""
    np = _np()
    if len(P) < 3:
        return P
    nxt = np.roll(P, -1, axis=0)
    keep = np.linalg.norm(nxt - P, axis=1) > 1e-12 * (1.0 + float(np.abs(P).max()))
    P = P[keep]
    if len(P) < 3:
        return P
    prev = np.roll(P, 1, axis=0)
    nxt = np.roll(P, -1, axis=0)
    e1 = P - prev
    e2 = nxt - P
    cr = np.linalg.norm(np.cross(e1, e2), axis=1)
    scale = np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1)
    corner = cr > 1e-6 * np.maximum(scale, 1e-300)
    if corner.sum() >= 3:
        P = P[corner]
    return P


def _decimate(P, limit: int = MAX_OUTLINE):
    np = _np()
    if len(P) <= limit:
        return P
    idx = np.linspace(0, len(P), limit, endpoint=False).astype(np.int64)
    return P[idx]


def _extent(P, center, normal, up):
    """(w, h) of points along the plane basis (horizontal, vertical)."""
    np = _np()
    if P is None or len(P) == 0:
        return [0.0, 0.0]
    _n, hor, ver = plane_basis([float(c) for c in normal], up)
    d = P - np.asarray(center)
    xs = d @ np.asarray(hor)
    ys = d @ np.asarray(ver)
    return [float(xs.max() - xs.min()), float(ys.max() - ys.min())]


def _orient(n, toward):
    """Flip ``n`` (np 3,) toward the camera if known."""
    if toward is not None and float(n @ toward) < 0.0:
        return -n
    return n


def _as_list(v) -> List[float]:
    return [float(v[0]), float(v[1]), float(v[2])]


def _make_face(kind, center, normal, area, size, outline) -> dict:
    return {
        "kind": kind,
        "center": _as_list(center),
        "normal": _as_list(normal),
        "area": float(area),
        "size": [float(size[0]), float(size[1])],
        "outline": [_as_list(p) for p in outline],
    }


def _ramanujan(a: float, b: float) -> float:
    if a + b <= 0:
        return 0.0
    h = ((a - b) / (a + b)) ** 2
    return math.pi * (a + b) * (1.0 + 3.0 * h / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def _ellipsoid_area(a: float, b: float, c: float) -> float:
    p = 1.6075  # Knud Thomsen (exact for a sphere, <= 1.1% error)
    return 4.0 * math.pi * (((a * b) ** p + (a * c) ** p + (b * c) ** p) / 3.0) ** (1.0 / p)


# ---------------------------------------------------------------------- mesh data (cached)
def _chunks(n: int, size: int = CHUNK):
    for s in range(0, n, size):
        yield s, min(n, s + size)


def _components(F: int, e1, e2):
    """Connected-component label (smallest index) per node over undirected edges (e1, e2).

    Hook-and-compress: after the compression every label is a root, and every round with a
    crossing edge hooks at least one root under a smaller one, so the number of roots drops every
    round and the loop ends (at most F rounds; in practice a few, pointer jumping does the rest).
    """
    np = _np()
    lab = np.arange(F, dtype=np.int64)
    e1 = np.asarray(e1, dtype=np.int64)
    e2 = np.asarray(e2, dtype=np.int64)
    while e1.size:
        l1, l2 = lab[e1], lab[e2]
        diff = l1 != l2
        if not diff.any():
            break
        e1, e2 = e1[diff], e2[diff]  # joined edges stay joined (labels only merge)
        l1, l2 = l1[diff], l2[diff]
        np.minimum.at(lab, np.maximum(l1, l2), np.minimum(l1, l2))  # hook the larger root
        while True:  # compress to roots
            nl = lab[lab]
            if np.array_equal(nl, lab):
                break
            lab = nl
    return lab


def _trace_loops_python(a, b, pos):
    """Longest closed chain of undirected edges (a[i], b[i]) (vertex ids), as positions (k, 3)."""
    np = _np()
    ea, eb = a.tolist(), b.tolist()
    adj = collections.defaultdict(list)
    for i, (x, y) in enumerate(zip(ea, eb)):
        adj[x].append(i)
        adj[y].append(i)
    used = [False] * len(ea)
    best, best_len = None, -1.0
    for e in range(len(ea)):
        if used[e]:
            continue
        used[e] = True
        start, cur = ea[e], eb[e]
        loop = [start]
        while cur != start:
            loop.append(cur)
            nxt_e = None
            for j in adj[cur]:
                if not used[j]:
                    nxt_e = j
                    break
            if nxt_e is None:
                break  # open chain (non-manifold): keep what we have
            used[nxt_e] = True
            cur = eb[nxt_e] if ea[nxt_e] == cur else ea[nxt_e]
        P = pos[np.asarray(loop, dtype=np.int64)]
        perim = float(np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1).sum())
        if perim > best_len:
            best, best_len = P, perim
    return best


def _longest_loop(a, b, pos):
    """Longest loop of directed boundary edges a[i] -> b[i] (welded vertex ids into ``pos``).

    Manifold boundaries (every vertex: one edge out, one in) are labelled vectorized (pointer
    jumping); anything else falls back to an undirected Python walk.
    """
    np = _np()
    if a.size == 0:
        return None
    verts, inv = np.unique(np.concatenate([a, b]), return_inverse=True)
    inv = inv.reshape(-1)
    V = len(verts)
    ac, bc = inv[: a.size], inv[a.size:]
    if not (np.bincount(ac, minlength=V) == 1).all() or not (np.bincount(bc, minlength=V) == 1).all():
        return _trace_loops_python(a, b, pos)
    succ = np.empty(V, dtype=np.int64)
    succ[ac] = bc
    lab = np.arange(V, dtype=np.int64)
    s = succ.copy()
    steps = 1
    while steps < V:
        lab = np.minimum(lab, lab[s])
        s = s[s]
        steps *= 2
    P = pos[verts]
    seg = np.linalg.norm(P[succ] - P, axis=1)
    perim = np.bincount(lab, weights=seg, minlength=V)
    start = int(np.argmax(perim))
    n = int(np.count_nonzero(lab == start))
    ring = [start]
    succ_l = succ.tolist()
    cur = succ_l[start]
    while cur != start and len(ring) < n:
        ring.append(cur)
        cur = succ_l[cur]
    return P[np.asarray(ring, dtype=np.int64)]


def _mesh_arrays(pts, counts, fvi):
    """numpy views (points (N, 3), counts (F,), indices (L,)) or None when the topology is invalid."""
    np = _np()
    pts = np.asarray(pts)
    if pts.size == 0 or pts.size % 3:
        return None
    pts = pts.reshape(-1, 3)
    counts = np.asarray(counts).reshape(-1)
    fvi = np.asarray(fvi).reshape(-1)
    N, F, L = len(pts), len(counts), len(fvi)
    if F == 0 or L == 0:
        return None
    if int(counts.min()) < 0 or int(counts.sum(dtype=np.int64)) != L:
        return None
    if int(fvi.min()) < 0 or int(fvi.max()) >= N:
        return None
    return pts, counts, fvi


def _fan(counts, base, fvi, f0: int, f1: int, mask=None):
    """Fan triangles of faces [f0, f1): (triangle face ids, vertex index arrays ia, ib, ic)."""
    np = _np()
    c = counts[f0:f1].astype(np.int64)
    nt = np.maximum(c - 2, 0)
    if mask is not None:
        nt = np.where(mask[f0:f1], nt, 0)
    T = int(nt.sum())
    if T == 0:
        return None
    tf = np.repeat(np.arange(f0, f1, dtype=np.int64), nt)
    k = np.arange(T, dtype=np.int64) - np.repeat(np.cumsum(nt) - nt, nt)
    b = base[tf]
    return tf, fvi[b], fvi[b + k + 1], fvi[b + k + 2]


def _scan_polygon(pts, counts, fvi, M, p, toward, cam_dist: float):
    """Low-memory hit polygon search for meshes over the size cap: world points (k, 3) of the
    polygon nearest to ``p`` (same rule as ``_MeshData.nearest_face``), or None.

    Streams the faces in chunks: no mesh-sized float arrays, nothing kept afterwards."""
    np = _np()
    arrays = _mesh_arrays(pts, counts, fvi)
    if arrays is None:
        return None
    pts, counts, fvi = arrays
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for s, e in _chunks(len(pts)):
        w = _xf(M, pts[s:e].astype(np.float64))
        w = w[np.isfinite(w).all(axis=1)]
        if len(w):
            lo = np.minimum(lo, w.min(axis=0))
            hi = np.maximum(hi, w.max(axis=0))
    if not np.isfinite(lo).all():
        return None
    eps0 = HIT_EPS * max(float(np.linalg.norm(hi - lo)), cam_dist, 1e-12)
    eps1 = eps0 * 10.0
    base = np.cumsum(counts, dtype=np.int64) - counts
    F = len(counts)
    dists, faces, facing = [], [], []
    best = math.inf
    for f0, f1 in _chunks(F, SCAN_FACES):
        tri = _fan(counts, base, fvi, f0, f1)
        if tri is None:
            continue
        tf, ia, ib, ic = tri
        A = _xf(M, pts[ia].astype(np.float64))
        B = _xf(M, pts[ib].astype(np.float64))
        C = _xf(M, pts[ic].astype(np.float64))
        tmin = np.minimum(np.minimum(A, B), C)
        tmax = np.maximum(np.maximum(A, B), C)
        sel = np.flatnonzero(np.all((tmin - eps1 <= p) & (p <= tmax + eps1), axis=1))
        if sel.size == 0:
            continue
        a, b, c = A[sel], B[sel], C[sel]
        dist = _point_tri_dist(p, a, b, c)
        keep = dist <= eps1  # NaN (non-finite points) never passes
        if not keep.any():
            continue
        fac = (np.cross(b - a, c - a) @ toward > 0.0) if toward is not None else np.zeros(len(sel), dtype=bool)
        dists.append(dist[keep])
        faces.append(tf[sel][keep])
        facing.append(fac[keep])
        best = min(best, float(dist[keep].min()))
        if len(dists) > 8:  # keep only what can still win (within 2.5 eps0 of the best)
            d, f, fc = np.concatenate(dists), np.concatenate(faces), np.concatenate(facing)
            m = d <= best + 2.5 * eps0
            dists, faces, facing = [d[m]], [f[m]], [fc[m]]
    if not dists:
        return None
    d, f, fc = np.concatenate(dists), np.concatenate(faces), np.concatenate(facing)
    for eps in (eps0, eps1):
        ok = d <= eps
        if not ok.any():
            continue
        dmin = float(d[ok].min())
        pick = ok & (d <= dmin + eps * 0.25)
        if toward is not None and (pick & fc).any():
            pick = pick & fc
        cand = np.flatnonzero(pick)
        hit_face = int(f[cand[int(np.argmin(d[cand]))]])
        s, n = int(base[hit_face]), int(counts[hit_face])
        return _xf(M, pts[fvi[s:s + n]].astype(np.float64))
    return None


class _MeshData:
    """World-space mesh arrays for one mesh within the size cap: Morton-sorted triangle blocks (hit
    polygon search), face props, edge twins, plane components and their boundaries.

    Pure numpy (no USD): ``build`` runs on a worker thread for large meshes."""

    def __init__(self):
        self.regions = {}  # component label -> summary, when the whole component is the region
        self.region_list = []  # summaries of flood-filled regions (``region_of`` indexes them)

    def nbytes(self) -> int:
        np = _np()
        return int(sum(v.nbytes for v in self.__dict__.values() if isinstance(v, np.ndarray)))

    @classmethod
    def build(cls, pts, counts, fvi, M) -> Optional["_MeshData"]:
        np = _np()
        arrays = _mesh_arrays(pts, counts, fvi)
        if arrays is None:
            return None
        pts, counts, fvi = arrays
        counts = counts.astype(np.int64)
        fvi = fvi.astype(np.int32, copy=False)
        F, L = len(counts), len(fvi)
        d = cls()
        world = _xf(M, pts.astype(np.float64))
        vgood = np.isfinite(world).all(axis=1)
        if not vgood.any():
            return None
        lo, hi = world[vgood].min(axis=0), world[vgood].max(axis=0)
        if not vgood.all():
            world[~vgood] = lo  # placeholder: faces using these points are excluded (fok False)
        d.world = world
        d.counts, d.fvi = counts, fvi
        d.base = np.cumsum(counts) - counts
        d.F = F
        d.lo, d.span = lo, np.maximum(hi - lo, 1e-300)
        d.diag = float(np.linalg.norm(hi - lo))
        d.tol = PLANE_TOL * max(d.diag, 1e-300)
        face_of_pos = np.repeat(np.arange(F, dtype=np.int32), counts)
        fok = counts >= 3
        if not vgood.all():
            bad_pos = ~vgood[fvi]
            fok &= np.bincount(face_of_pos[bad_pos], minlength=F) == 0
        d.fok = fok

        # fan triangles of the usable faces
        tri = _fan(counts, d.base, fvi, 0, F, mask=fok)
        if tri is None:
            return None
        tf, ia, ib, ic = tri
        T = len(tf)
        d.tri = np.stack([ia, ib, ic], axis=1).astype(np.int32)
        d.tri_face = tf.astype(np.int32)
        d.T = T
        del tri, tf, ia, ib, ic

        # pass 1 (chunked): Morton codes of triangle box centres, face normal sums
        code = np.empty(T, dtype=np.int64)
        vec = np.zeros((F, 3))
        for s, e in _chunks(T):
            A, B, C = (world[d.tri[s:e, i]] for i in range(3))
            mid = (np.minimum(np.minimum(A, B), C) + np.maximum(np.maximum(A, B), C)) * 0.5
            q = np.clip((mid - lo) / d.span * 1023.0, 0, 1023).astype(np.int64)
            code[s:e] = _part1by2(q[:, 0]) | (_part1by2(q[:, 1]) << 1) | (_part1by2(q[:, 2]) << 2)
            cr = np.cross(B - A, C - A)
            tfc = d.tri_face[s:e]
            f0, f1 = int(tfc[0]), int(tfc[-1]) + 1
            loc = tfc - f0
            for i in range(3):
                vec[f0:f1, i] += np.bincount(loc, weights=cr[:, i], minlength=f1 - f0)
        d.order = np.argsort(code, kind="stable").astype(np.int32)
        del code
        nblk = (T + BLOCK - 1) // BLOCK
        d.bmin = np.empty((nblk, 3))
        d.bmax = np.empty((nblk, 3))
        for s, e in _chunks(T):  # CHUNK is a multiple of BLOCK
            o = d.order[s:e]
            A, B, C = (world[d.tri[o, i]] for i in range(3))
            starts = np.arange(0, e - s, BLOCK, dtype=np.int64)
            b0 = s // BLOCK
            d.bmin[b0:b0 + len(starts)] = np.minimum.reduceat(np.minimum(np.minimum(A, B), C), starts, axis=0)
            d.bmax[b0:b0 + len(starts)] = np.maximum.reduceat(np.maximum(np.maximum(A, B), C), starts, axis=0)

        # face props: area, unit normal, area-weighted centroid (pass 2, chunked)
        length = np.linalg.norm(vec, axis=1)
        area = length / 2.0
        fn = vec / np.maximum(length, 1e-300)[:, None]
        del vec, length
        sw = np.zeros(F)
        cen = np.zeros((F, 3))
        for s, e in _chunks(T):
            A, B, C = (world[d.tri[s:e, i]] for i in range(3))
            tfc = d.tri_face[s:e]
            f0, f1 = int(tfc[0]), int(tfc[-1]) + 1
            loc = tfc - f0
            w = np.einsum("ij,ij->i", np.cross(B - A, C - A), fn[tfc])
            tc = (A + B + C) / 3.0
            sw[f0:f1] += np.bincount(loc, weights=w, minlength=f1 - f0)
            for i in range(3):
                cen[f0:f1, i] += np.bincount(loc, weights=w * tc[:, i], minlength=f1 - f0)
        ok = np.abs(sw) > 1e-300
        cen[ok] /= sw[ok][:, None]
        nf = np.flatnonzero(~ok)
        if nf.size:  # zero-area / unusable faces: vertex mean
            cnt = counts[nf]
            pos = _ranges(d.base[nf], cnt)
            own = np.repeat(np.arange(len(nf), dtype=np.int64), cnt)
            sums = np.stack([np.bincount(own, weights=world[fvi[pos], i], minlength=len(nf)) for i in range(3)], axis=1)
            cen[nf] = sums / np.maximum(cnt, 1)[:, None]
        del sw, ok, nf

        # next position of every face-vertex position (closing each polygon)
        nxt = np.arange(1, L + 1, dtype=np.int64)
        has = counts > 0
        nxt[(d.base + counts - 1)[has]] = d.base[has]
        fnext = fvi[nxt]
        del nxt

        # degenerate faces (zero area, collinear, slivers): thinner than the plane tolerance, so the
        # normal is not reliable. They are grouped by plane distance only (never by angle).
        emax = np.zeros(F)
        for s, e in _chunks(L):
            seg = np.linalg.norm(world[fnext[s:e]] - world[fvi[s:e]], axis=1)
            fp = face_of_pos[s:e]
            heads = np.flatnonzero(np.concatenate([[True], fp[1:] != fp[:-1]]))
            fh = fp[heads]
            emax[fh] = np.maximum(emax[fh], np.maximum.reduceat(seg, heads))
        d.deg = fok & (2.0 * area <= d.tol * emax)
        del emax

        # welded vertex ids (by position) -> undirected edge per face-vertex position
        qv = np.clip(np.rint((world - lo) / d.span * _WELD_LEVELS), 0, _WELD_LEVELS).astype(np.int64)
        vkey = (qv[:, 0] << 42) | (qv[:, 1] << 21) | qv[:, 2]
        del qv
        if not vgood.all():
            badv = np.flatnonzero(~vgood)
            vkey[badv] = -1 - badv  # never welded
        _u, first, wid = np.unique(vkey, return_index=True, return_inverse=True)
        del _u, vkey
        wid = wid.reshape(-1).astype(np.int32)
        Nw = int(len(first))
        d.wpos = world[first]
        d.ea, d.eb = wid[fvi], wid[fnext]
        del wid, fnext, first
        d.evalid = (d.ea != d.eb) & fok[face_of_pos]

        # edge twins: positions sharing an undirected edge (built once per mesh)
        vpos = np.flatnonzero(d.evalid)
        ea, eb = d.ea[vpos].astype(np.int64), d.eb[vpos].astype(np.int64)
        key = np.minimum(ea, eb) * Nw + np.maximum(ea, eb)
        del ea, eb
        o = np.argsort(key, kind="stable")
        sp = vpos[o]
        sk = key[o]
        del key, o, vpos
        same = sk[1:] == sk[:-1]
        p1, p2 = sp[:-1][same], sp[1:][same]
        del sp, sk, same
        f1, f2 = face_of_pos[p1], face_of_pos[p2]
        keep = f1 != f2
        p1, p2, f1, f2 = p1[keep], p2[keep], f1[keep], f2[keep]
        d.twin = np.full(L, -1, dtype=np.int32)
        d.twin[p1] = f2
        d.twin[p2] = f1
        del p1, p2

        # plane components: adjacent faces within 1 degree and on each other's plane; a pair with
        # one degenerate face joins when that face lies on the other's plane (both degenerate: no)
        r1, r2 = ~d.deg[f1], ~d.deg[f2]
        n1, n2 = fn[f1], fn[f2]
        comp = (
            r1
            & r2
            & (np.abs(np.einsum("ij,ij->i", n1, n2)) >= COPLANAR_COS)
            & (np.abs(np.einsum("ij,ij->i", cen[f2] - cen[f1], n1)) <= d.tol)
            & (np.abs(np.einsum("ij,ij->i", cen[f1] - cen[f2], n2)) <= d.tol)
        )
        del n1, n2
        one = np.flatnonzero(r1 ^ r2)
        if one.size:
            fr = np.where(r1[one], f1[one], f2[one])
            fd = np.where(r1[one], f2[one], f1[one])
            comp[one] = d.plane_dist(fd, cen[fr], fn[fr]) <= d.tol
        label = _components(F, f1[comp], f2[comp]).astype(np.int32)
        del comp, f1, f2, r1, r2, one
        d.label = label
        # face props in component order: a component is one contiguous slice
        order = np.argsort(label, kind="stable")
        d.comp_order = order.astype(np.int32)
        d.rank = np.empty(F, dtype=np.int32)
        d.rank[order] = np.arange(F, dtype=np.int32)
        sl = label[order]
        d.sorted_label = sl
        d.fn_s, d.cen_s, d.area_s = fn[order], cen[order], area[order]
        del fn, cen, area, order
        first_s = np.concatenate([[True], sl[1:] != sl[:-1]])
        d.cstart = np.flatnonzero(first_s)
        cidx = np.cumsum(first_s) - 1
        # orientation reference per component: its largest face (not a degenerate one)
        by_area = np.lexsort((-d.area_s, sl))
        ref = d.fn_s[by_area[d.cstart]][cidx]
        del by_area
        sgn = np.where(np.einsum("ij,ij->i", d.fn_s, ref) >= 0.0, 1.0, -1.0)
        del ref, cidx, first_s
        d.c_area = np.add.reduceat(d.area_s, d.cstart)
        d.c_cen = np.add.reduceat(d.cen_s * d.area_s[:, None], d.cstart, axis=0)
        d.c_nrm = np.add.reduceat(d.fn_s * (d.area_s * sgn)[:, None], d.cstart, axis=0)
        # boundary edges of every component (twin missing or in another component), sorted by label
        lab_pos = label[face_of_pos]
        bpos = np.flatnonzero(d.evalid & ((d.twin < 0) | (label[np.maximum(d.twin, 0)] != lab_pos)))
        flip = sgn[d.rank[face_of_pos[bpos]]] < 0
        ba, bb = d.ea[bpos], d.eb[bpos]
        ba, bb = np.where(flip, bb, ba), np.where(flip, ba, bb)
        bl = lab_pos[bpos]
        o = np.argsort(bl, kind="stable")
        d.b_lab, d.b_a, d.b_b = bl[o], ba[o], bb[o]
        d.region_of = np.full(F, -1, dtype=np.int32)
        return d

    # helpers --------------------------------------------------------------------
    def plane_dist(self, faces, c, n):
        """Max distance of the vertices of each face in ``faces`` from the plane (c[i], n[i]) (or one
        plane for all when ``c``/``n`` are (3,))."""
        np = _np()
        faces = np.asarray(faces, dtype=np.int64)
        k = len(faces)
        if k == 0:
            return np.zeros(0)
        cnt = self.counts[faces]
        pos = _ranges(self.base[faces], cnt)
        P = self.world[self.fvi[pos]]
        if np.ndim(c) == 1:
            dist = np.abs((P - c) @ n)
        else:
            own = np.repeat(np.arange(k, dtype=np.int64), cnt)
            dist = np.abs(np.einsum("ij,ij->i", P - c[own], n[own]))
        return np.maximum.reduceat(dist, np.cumsum(cnt) - cnt)  # usable faces have >= 3 vertices

    def reliable(self, f: int) -> bool:
        """Usable face with a stable normal (can seed a planar region)."""
        return bool(self.fok[f]) and not bool(self.deg[f])

    # hit polygon --------------------------------------------------------------
    def nearest_face(self, p, toward, cam_dist: float) -> Optional[int]:
        np = _np()
        eps0 = HIT_EPS * max(self.diag, cam_dist, 1e-12)
        for eps in (eps0, eps0 * 10.0):
            blocks = np.nonzero(np.all((self.bmin - eps <= p) & (p <= self.bmax + eps), axis=1))[0]
            if blocks.size == 0:
                continue
            pos = _ranges(blocks * BLOCK, np.minimum(BLOCK, self.T - blocks * BLOCK))
            tris = self.order[pos]
            a = self.world[self.tri[tris, 0]]
            b = self.world[self.tri[tris, 1]]
            c = self.world[self.tri[tris, 2]]
            dist = _point_tri_dist(p, a, b, c)
            ok = dist <= eps
            if not ok.any():
                continue
            dmin = float(dist[ok].min())
            pick = ok & (dist <= dmin + eps * 0.25)
            rel = ~self.deg[self.tri_face[tris]]
            if (pick & rel).any():  # prefer faces with a stable normal
                pick = pick & rel
            if toward is not None:
                facing = np.cross(b - a, c - a) @ toward > 0.0
                if (pick & facing).any():
                    pick = pick & facing
            cand = np.nonzero(pick)[0]
            best = cand[int(np.argmin(dist[cand]))]
            return int(self.tri_face[tris[best]])
        return None

    def face_points(self, f: int):
        s, n = int(self.base[f]), int(self.counts[f])
        return self.world[self.fvi[s:s + n]]

    # planar region -----------------------------------------------------------
    def summary(self, seed: int):
        """(face count, center, unit normal | None, area, ring | None) of the planar region of ``seed``.

        Candidates = the faces of the seed's plane component that are within 1 degree of the seed
        normal and on the seed plane (degenerate faces: all vertices on the seed plane). Region =
        the candidates edge-connected to the seed through candidates (flood fill), so islands at
        the same height that only meet through other faces stay apart. When every face of the
        component is a candidate (flat surfaces), the per-component sums and boundary built with
        the mesh are used and cached per component; other regions are cached per face.
        """
        np = _np()
        lab = int(self.label[seed])
        cached = self.regions.get(lab)
        if cached is not None:
            return cached
        ro = int(self.region_of[seed])
        if ro >= 0:
            return self.region_list[ro]
        r = int(self.rank[seed])
        lo = int(np.searchsorted(self.sorted_label, lab, side="left"))
        hi = int(np.searchsorted(self.sorted_label, lab, side="right"))
        if hi - lo <= 1:
            return (1, self.cen_s[r], self.fn_s[r], float(self.area_s[r]), None)
        n0, c0 = self.fn_s[r], self.cen_s[r]
        faces = self.comp_order[lo:hi]  # ascending face ids (stable sort)
        fn, cen = self.fn_s[lo:hi], self.cen_s[lo:hi]
        deg = self.deg[faces]
        ok = ~deg & (np.abs(fn @ n0) >= COPLANAR_COS) & (np.abs((cen - c0) @ n0) <= self.tol)
        di = np.flatnonzero(deg)
        if di.size:
            ok[di] = self.plane_dist(faces[di], c0, n0) <= self.tol
        if ok.all():
            ci = int(np.searchsorted(self.cstart, lo))
            total = float(self.c_area[ci])
            center = self.c_cen[ci] / total
            n = _unit(self.c_nrm[ci])
            blo = int(np.searchsorted(self.b_lab, lab, side="left"))
            bhi = int(np.searchsorted(self.b_lab, lab, side="right"))
            ring = _longest_loop(self.b_a[blo:bhi], self.b_b[blo:bhi], self.wpos) if bhi > blo else None
            out = (hi - lo, center, n, total, ring)
            self.regions[lab] = out
            return out
        region = self._connected(faces[ok].astype(np.int64), seed)
        rk = self.rank[region]
        area, fnr, cenr = self.area_s[rk], self.fn_s[rk], self.cen_s[rk]
        total = float(area.sum())
        if not total > 0.0:
            return (1, c0, n0, float(self.area_s[r]), None)
        center = (cenr * area[:, None]).sum(axis=0) / total
        sign = np.where(fnr @ n0 >= 0.0, 1.0, -1.0)
        n = _unit((fnr * (area * sign)[:, None]).sum(axis=0))
        count = int(len(region))
        ring = self.boundary_ring(region, sign) if n is not None and count > 1 else None
        out = (count, center, n, total, ring)
        self.region_of[region] = len(self.region_list)
        self.region_list.append(out)
        return out

    def _connected(self, sub, seed: int):
        """Faces of ``sub`` (ascending face ids, contains ``seed``) edge-connected to ``seed`` within ``sub``."""
        np = _np()
        k = len(sub)
        cnt = self.counts[sub]
        pos = _ranges(self.base[sub], cnt)
        own = np.repeat(np.arange(k, dtype=np.int64), cnt)
        tw = self.twin[pos].astype(np.int64)
        m = tw >= 0
        own, tw = own[m], tw[m]
        j = np.minimum(np.searchsorted(sub, tw), k - 1)
        inside = sub[j] == tw
        lab = _components(k, own[inside], j[inside])
        s = int(np.searchsorted(sub, seed))
        return sub[lab == lab[s]]

    def boundary_ring(self, faces, sign):
        """Longest boundary loop (welded positions, (k, 3)) of a face region, or None.

        Boundary = edges of region faces whose twin face is missing or outside the region."""
        np = _np()
        inreg = np.zeros(self.F, dtype=bool)
        inreg[faces] = True
        cnt = self.counts[faces]
        pos = _ranges(self.base[faces], cnt)
        fsign = np.repeat(sign, cnt)
        tw = self.twin[pos]
        bnd = self.evalid[pos] & ((tw < 0) | ~inreg[np.maximum(tw, 0)])
        pos, fsign = pos[bnd], fsign[bnd]
        if pos.size == 0:
            return None
        a, b = self.ea[pos], self.eb[pos]
        flip = fsign < 0  # faces wound the other way: walk their edges backwards
        a, b = np.where(flip, b, a), np.where(flip, a, b)
        return _longest_loop(a, b, self.wpos)


def _polygon_face(P, tw, up) -> Optional[dict]:
    """``polygon`` face of one polygon (world points (k, 3)), or None (degenerate)."""
    if P is None or len(P) < 3:
        return None
    area, n, centroid = _polygon_props(P)
    if n is None:
        return None
    n = _orient(n, tw)
    ring = _simplify_ring(P)
    return _make_face("polygon", centroid, n, area, _extent(ring, centroid, n, up), _decimate(ring))


def _face_from_data(data: _MeshData, p, tw, cam_dist: float, up) -> Optional[dict]:
    np = _np()
    f = data.nearest_face(p, tw, cam_dist)
    if f is None:
        return None  # the hit is not on this geometry (e.g. point instancer prototype)
    if data.reliable(f):
        count, center, n, total, ring = data.summary(f)
        if count > 1 and n is not None:
            n = _orient(n, tw)
            ring = _simplify_ring(ring) if ring is not None else np.zeros((0, 3))
            size = _extent(ring, center, n, up)
            return _make_face("planar", center, n, total, size, _decimate(ring))
    return _polygon_face(data.face_points(f), tw, up)


def _toward_unit(toward):
    """(unit point -> camera vector | None, camera distance) as numpy."""
    np = _np()
    if toward is None:
        return None, 0.0
    tv = np.asarray([float(c) for c in toward], dtype=np.float64)
    cam_dist = float(np.linalg.norm(tv))
    if not math.isfinite(cam_dist) or cam_dist <= 1e-300:
        return None, 0.0
    return tv / cam_dist, cam_dist


# ---------------------------------------------------------------------- computer
class _Job:
    """Worker-thread mesh build (``"build"``) or over-cap scan (``"scan"``), planned on the main thread.
    ``args`` are numpy arrays / values only: ``run`` touches no USD object."""

    def __init__(self, kind: str, key, args, watch, epoch: int, faces: int):
        self.kind = kind
        self.key = key
        self.args = args
        self.watch = watch
        self.epoch = epoch
        self.faces = faces
        self.stale = False  # the prim (or an ancestor) changed while building
        self.seconds = 0.0
        self.future = None

    def run(self):
        t0 = _perf_counter()
        try:
            with _scope(M_FACE_BUILD):
                if self.kind == "build":
                    return _MeshData.build(*self.args)
                return _scan_polygon(*self.args)
        finally:
            self.seconds = _perf_counter() - t0
            self.args = None  # drop the input arrays


class FaceComputer:
    """Computes faces; caches mesh data per (prim path, time code). Main thread only (``compute_async``
    builds large meshes on a worker thread and finishes on the main thread)."""

    def __init__(
        self,
        max_faces: int = MAX_FACES,
        cache_size: int = CACHE_SIZE,
        *,
        max_tris: int = MAX_TRIS,
        cache_bytes: int = CACHE_BYTES,
        async_faces: int = ASYNC_FACES,
    ):
        self.max_faces = int(max_faces)
        self.max_tris = int(max_tris)
        self.cache_size = int(cache_size)
        self.cache_bytes = int(cache_bytes)
        self.async_faces = int(async_faces)
        self._cache: "collections.OrderedDict" = collections.OrderedDict()  # key -> (data, watch, nbytes)
        self._pending = {}  # key -> _Job (worker builds in flight)
        self._epoch = 0  # bumped on clear / stage switch: older jobs are not cached
        self._stage = None
        self._listener = None
        self.build_count = 0  # mesh data builds (tests)
        self.scan_count = 0  # over-cap scans (tests)
        self._warned = False
        self._slow_warned = set()

    # cache ---------------------------------------------------------------------
    @property
    def cached_keys(self) -> list:
        return list(self._cache.keys())

    @property
    def cached_bytes(self) -> int:
        return int(sum(entry[2] for entry in self._cache.values()))

    def clear(self) -> None:
        self._cache.clear()
        self._drop_pending()
        self._revoke()
        self._stage = None

    def shutdown(self) -> None:
        self.clear()

    def _drop_pending(self) -> None:
        self._epoch += 1
        for job in self._pending.values():
            job.stale = True
        self._pending.clear()

    def _revoke(self) -> None:
        lst, self._listener = self._listener, None
        if lst is not None:
            try:
                lst.Revoke()
            except Exception:  # noqa: BLE001
                pass

    def _watch_stage(self, stage) -> None:
        same = False
        try:
            same = self._stage is not None and self._stage == stage
        except Exception:  # noqa: BLE001
            same = False
        if same and self._listener is not None:
            return
        self._cache.clear()
        self._drop_pending()
        self._revoke()
        self._stage = stage
        try:
            from pxr import Tf, Usd

            self._listener = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._on_objects_changed, stage)
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{_EXT_ID}] face cache: cannot watch stage changes ({e}); cache disabled")
            self._listener = None

    def _on_objects_changed(self, notice, _sender) -> None:
        if not self._cache and not self._pending:
            return
        try:
            changed = [p.GetPrimPath() for p in list(notice.GetResyncedPaths()) + list(notice.GetChangedInfoOnlyPaths())]
        except Exception:  # noqa: BLE001
            self._cache.clear()
            for job in self._pending.values():
                job.stale = True
            return
        if not changed:
            return

        def hit(watch) -> bool:
            return any(w.HasPrefix(c) for w in watch for c in changed)

        for key in [k for k, (_d, watch, _b) in self._cache.items() if hit(watch)]:
            self._cache.pop(key, None)
        for job in self._pending.values():
            if hit(job.watch):
                job.stale = True

    def _store(self, key, data: _MeshData, watch) -> None:
        """Cache ``data``; evict the oldest entries beyond ``cache_size`` entries or ``cache_bytes``
        (the newest entry always stays)."""
        if self._listener is None:
            return  # cannot see edits: do not cache
        self._cache[key] = (data, watch, data.nbytes())
        self._cache.move_to_end(key)
        total = self.cached_bytes
        while len(self._cache) > 1 and (len(self._cache) > self.cache_size or total > self.cache_bytes):
            _key, (_data, _watch, nbytes) = self._cache.popitem(last=False)
            total -= nbytes

    @staticmethod
    def _mesh_key(prim, time):
        from pxr import UsdGeom

        mesh = UsdGeom.Mesh(prim)
        varying = False
        try:
            varying = mesh.GetPointsAttr().ValueMightBeTimeVarying()
            p = prim
            while p and not p.IsPseudoRoot() and not varying:
                if p.IsA(UsdGeom.Xformable) and UsdGeom.Xformable(p).TransformMightBeTimeVarying():
                    varying = True
                p = p.GetParent()
        except Exception:  # noqa: BLE001
            varying = True
        tkey = ("t", time.GetValue()) if varying and not time.IsDefault() else ("static",)
        return (str(prim.GetPath()), tkey)

    @staticmethod
    def _watch_paths(prim) -> list:
        watch = [prim.GetPath()]
        try:
            if prim.IsInstanceProxy():
                watch.append(prim.GetPrimInPrototype().GetPath())
        except Exception:  # noqa: BLE001
            pass
        return watch

    def _read(self, prim, time):
        """Main thread: (points, counts, indices, matrix, face count, over cap) as numpy, or None."""
        from pxr import UsdGeom

        np = _np()
        mesh = UsdGeom.Mesh(prim)
        pts = mesh.GetPointsAttr().Get(time)
        counts = mesh.GetFaceVertexCountsAttr().Get(time)
        fvi = mesh.GetFaceVertexIndicesAttr().Get(time)
        if pts is None or counts is None or fvi is None or len(pts) == 0 or len(counts) == 0 or len(fvi) == 0:
            return None
        counts = np.asarray(counts).reshape(-1)
        F = int(len(counts))
        T = int(np.maximum(counts.astype(np.int64) - 2, 0).sum())
        M = _matrix_np(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time))
        over = F > self.max_faces or T > self.max_tris
        return np.asarray(pts), counts, np.asarray(fvi), M, F, over

    def _note_time(self, kind: str, seconds: float, faces: int) -> None:
        if seconds < SLOW_BUILD_S or kind in self._slow_warned:
            return
        self._slow_warned.add(kind)
        if kind == "build":
            carb.log_warn(
                f"[{_EXT_ID}] face data for a mesh of {faces} faces took {seconds:.1f} s "
                "(first click on that mesh only; later clicks use the cache)"
            )
        else:
            carb.log_warn(
                f"[{_EXT_ID}] a mesh of {faces} faces is over the face size cap: every click scans it "
                f"({seconds:.1f} s) and returns only the hit polygon"
            )

    def _timed(self, kind: str, fn, faces: int):
        t0 = _perf_counter()
        with _scope(M_FACE_BUILD):
            out = fn()
        if kind == "build":
            self.build_count += 1
        else:
            self.scan_count += 1
        self._note_time(kind, _perf_counter() - t0, faces)
        return out

    # compute -------------------------------------------------------------------
    def compute(self, stage, prim_path, hit, pick_normal=None, toward=None, time=None, _data=None) -> dict:
        """Face at ``hit`` on the prim at ``prim_path``. Never raises: falls back to ``"point"``.

        Synchronous: a first click on a large mesh builds its data here. ``compute_async`` builds
        large meshes on a worker thread instead."""
        up = _stage_up(stage)
        return self._guarded(lambda: self._compute(stage, prim_path, hit, toward, time, up, _data),
                             hit, pick_normal, toward, up)

    def _guarded(self, fn, hit, pick_normal, toward, up) -> dict:
        face = None
        with _scope(M_FACE):
            try:
                face = fn()
                if face is not None:
                    face = normalize_face(face)
            except Exception as e:  # noqa: BLE001
                if not self._warned:
                    self._warned = True
                    carb.log_warn(f"[{_EXT_ID}] face computation failed, using the hit point: {e}")
                face = None
        if face is None:
            face = point_face(hit, pick_normal, toward, up)
        return face

    async def compute_async(self, stage, prim_path, hit, pick_normal=None, toward=None, time=None) -> dict:
        """``compute``, but the first build of a mesh with at least ``async_faces`` faces (and the scan
        of an over-cap mesh) runs on a worker thread (numpy releases the GIL). The result is checked
        on the main thread (stage / prim unchanged meanwhile, else built again once) and the face is
        computed there. Never raises except ``CancelledError``."""
        up = _stage_up(stage)
        last = None
        for _attempt in range(2):
            try:
                tw, cam_dist = _toward_unit(toward)
                p = _np().asarray([float(c) for c in hit], dtype=_np().float64)
                job = self._plan(stage, prim_path, time, p, tw, cam_dist)
            except Exception:  # noqa: BLE001
                job = None
            if job is None:  # small, cached, not a mesh, no numpy, ...: synchronous
                return self.compute(stage, prim_path, hit, pick_normal, toward, time)
            try:
                out = await asyncio.shield(job.future)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if not self._warned:
                    self._warned = True
                    carb.log_warn(f"[{_EXT_ID}] face computation failed, using the hit point: {e}")
                return point_face(hit, pick_normal, toward, up)
            if job.kind == "scan":
                self.scan_count += 1
                self._note_time("scan", job.seconds, job.faces)
                return self._guarded(lambda: _polygon_face(out, tw, up), hit, pick_normal, toward, up)
            if out is None:
                return point_face(hit, pick_normal, toward, up)
            entry = self._cache.get(job.key)
            if (entry is not None and entry[0] is out) or (
                self._listener is None and not job.stale and job.epoch == self._epoch
            ):
                return self.compute(stage, prim_path, hit, pick_normal, toward, time, _data=out)
            last = out  # changed while building: build again from the current geometry
        return self.compute(stage, prim_path, hit, pick_normal, toward, time, _data=last)

    def _plan(self, stage, prim_path, time, p, tw, cam_dist) -> Optional[_Job]:
        """Main thread: the worker job for this click, or None (compute synchronously)."""
        from pxr import Usd, UsdGeom

        prim = _resolve_prim(stage, prim_path)
        if prim is None or not prim.IsA(UsdGeom.Mesh):
            return None
        if time is None:
            time = Usd.TimeCode.Default()
        key = self._mesh_key(prim, time)
        self._watch_stage(stage)
        if key in self._cache:
            return None
        job = self._pending.get(key)
        if job is not None and not job.stale and job.epoch == self._epoch:
            return job  # same mesh already building: wait for it
        counts = UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get(time)
        if counts is None or len(counts) < self.async_faces:
            return None
        inp = self._read(prim, time)
        if inp is None:
            return None
        pts, counts, fvi, M, F, over = inp
        if over:
            job = _Job("scan", None, (pts, counts, fvi, M, p, tw, cam_dist), [], self._epoch, F)
        else:
            job = _Job("build", key, (pts, counts, fvi, M), self._watch_paths(prim), self._epoch, F)
            self._pending[key] = job
        job.future = asyncio.get_event_loop().run_in_executor(None, job.run)
        if job.kind == "build":
            job.future.add_done_callback(lambda fut, job=job: self._built(job, fut))
        return job

    def _built(self, job: _Job, fut) -> None:
        """Main thread (future callback): cache a finished worker build if nothing changed meanwhile."""
        if self._pending.get(job.key) is job:
            del self._pending[job.key]
        if fut.cancelled() or fut.exception() is not None:
            return
        self.build_count += 1
        self._note_time("build", job.seconds, job.faces)
        data = fut.result()
        if data is not None and not job.stale and job.epoch == self._epoch:
            self._store(job.key, data, job.watch)

    def _compute(self, stage, prim_path, hit, toward, time, up, data=None) -> Optional[dict]:
        prim = _resolve_prim(stage, prim_path)
        if prim is None:
            return None
        from pxr import Usd, UsdGeom

        if time is None:
            time = Usd.TimeCode.Default()
        np = _np()
        p = np.asarray([float(c) for c in hit], dtype=np.float64)
        tw, cam_dist = _toward_unit(toward)
        if prim.IsA(UsdGeom.Mesh):
            return self._mesh_face(stage, prim, p, tw, cam_dist, time, up, data)
        if not prim.IsA(UsdGeom.Gprim):
            return None
        M = _matrix_np(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time))
        M3 = M[:3, :3]
        if abs(float(np.linalg.det(M3))) < 1e-300:
            return None
        Minv = np.linalg.inv(M)
        Nmat = np.linalg.inv(M3).T
        q = _xf(Minv, p)
        if prim.IsA(UsdGeom.Cube):
            return _cube_face(UsdGeom.Cube(prim), M, Nmat, p, q, tw, time, up)
        if prim.IsA(UsdGeom.Cylinder):
            return _cylinder_face(UsdGeom.Cylinder(prim), M, Nmat, p, q, tw, time, up, cone=False)
        if prim.IsA(UsdGeom.Cone):
            return _cylinder_face(UsdGeom.Cone(prim), M, Nmat, p, q, tw, time, up, cone=True)
        if prim.IsA(UsdGeom.Sphere):
            return _sphere_face(UsdGeom.Sphere(prim), M, Nmat, p, q, tw, time, up)
        return None  # Capsule, curves, points, ...: "point"

    def _mesh_face(self, stage, prim, p, tw, cam_dist, time, up, data=None) -> Optional[dict]:
        if data is None:
            key = self._mesh_key(prim, time)
            self._watch_stage(stage)
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                data = entry[0]
            else:
                inp = self._read(prim, time)
                if inp is None:
                    return None
                pts, counts, fvi, M, F, over = inp
                if over:  # no mesh data at all: stream the faces, return the hit polygon
                    P = self._timed("scan", lambda: _scan_polygon(pts, counts, fvi, M, p, tw, cam_dist), F)
                    return _polygon_face(P, tw, up)
                data = self._timed("build", lambda: _MeshData.build(pts, counts, fvi, M), F)
                if data is None:
                    return None
                self._store(key, data, self._watch_paths(prim))
        return _face_from_data(data, p, tw, cam_dist, up)


def _resolve_prim(stage, prim_path):
    if stage is None or not prim_path or not isinstance(prim_path, str):
        return None
    from pxr import Sdf

    if not Sdf.Path.IsValidPathString(prim_path):
        return None
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None
    return prim


def _stage_up(stage) -> list:
    try:
        from pxr import UsdGeom

        if stage is not None and UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
            return [0.0, 0.0, 1.0]
    except Exception:  # noqa: BLE001
        pass
    return [0.0, 1.0, 0.0]


def _attr(schema_attr, time, default):
    try:
        v = schema_attr.Get(time)
    except Exception:  # noqa: BLE001
        v = None
    return default if v is None else v


_AXES = {"X": 0, "Y": 1, "Z": 2}


def _axes(ax: int):
    np = _np()
    e = np.eye(3)
    return e[ax], e[(ax + 1) % 3], e[(ax + 2) % 3]


def _world_diag(M, half_extents) -> float:
    np = _np()
    return float(np.linalg.norm(_dirv(M, 2.0 * np.asarray(half_extents, dtype=np.float64))))


def _too_far(M, p, closest_local, half_extents) -> bool:
    np = _np()
    d = float(np.linalg.norm(_xf(M, closest_local) - p))
    return d > 0.05 * max(_world_diag(M, half_extents), 1e-300)


def _planar_from_local(M, Nmat, center_l, n_l, u, v, outline_l, local_area, tw, up):
    np = _np()
    center = _xf(M, center_l)
    n = _unit(n_l @ Nmat)
    if n is None:
        return None
    n = _orient(n, tw)
    area = local_area * float(np.linalg.norm(np.cross(_dirv(M, u), _dirv(M, v))))
    ring = _xf(M, outline_l)
    return _make_face("planar", center, n, area, _extent(ring, center, n, up), _decimate(ring))


def _circle(center_l, u, v, r, k):
    np = _np()
    t = np.linspace(0.0, 2.0 * math.pi, k, endpoint=False)
    return center_l + r * (np.cos(t)[:, None] * u + np.sin(t)[:, None] * v)


def _cube_face(cube, M, Nmat, p, q, tw, time, up):
    np = _np()
    s = float(_attr(cube.GetSizeAttr(), time, 2.0))
    if not s > 0:
        return None
    h = s / 2.0
    best = None
    for a in range(3):
        for sign in (1.0, -1.0):
            c = np.clip(q, -h, h)
            c[a] = sign * h
            d = float(np.linalg.norm(_xf(M, c) - p))
            if best is None or d < best[0]:
                best = (d, a, sign, c)
    _d, a, sign, c = best
    if _too_far(M, p, c, [h, h, h]):
        return None
    e_a, u, v = _axes(a)
    center_l = sign * h * e_a
    corners = np.array([center_l + h * (su * u + sv * v) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    return _planar_from_local(M, Nmat, center_l, sign * e_a, u, v, corners, s * s, tw, up)


def _cylinder_face(geom, M, Nmat, p, q, tw, time, up, cone: bool):
    np = _np()
    r = float(_attr(geom.GetRadiusAttr(), time, 1.0))
    hgt = float(_attr(geom.GetHeightAttr(), time, 2.0))
    ax = _AXES.get(str(_attr(geom.GetAxisAttr(), time, "Z")), 2)
    if not (r > 0 and hgt > 0):
        return None
    e_ax, u, v = _axes(ax)
    hh = hgt / 2.0
    radial = q - q[ax] * e_ax
    rho = float(np.linalg.norm(radial))
    rdir = radial / rho if rho > 1e-300 else u
    cands = []  # (distance, kind, closest local, sign)
    for sign in ((-1.0,) if cone else (1.0, -1.0)):
        c = rdir * min(rho, r) + sign * hh * e_ax
        cands.append((float(np.linalg.norm(_xf(M, c) - p)), "cap", c, sign))
    if cone:
        # side segment in (rho, z): from (r, -hh) to (0, +hh)
        a2, b2 = np.array([r, -hh]), np.array([0.0, hh])
        ab = b2 - a2
        t = float(np.clip(np.dot(np.array([rho, q[ax]]) - a2, ab) / np.dot(ab, ab), 0.0, 1.0))
        rc, zc = a2 + t * ab
        c = rdir * rc + zc * e_ax
        cands.append((float(np.linalg.norm(_xf(M, c) - p)), "cone_side", c, 0.0))
    else:
        c = rdir * r + float(np.clip(q[ax], -hh, hh)) * e_ax
        cands.append((float(np.linalg.norm(_xf(M, c) - p)), "side", c, 0.0))
    _d, kind, c, sign = min(cands, key=lambda x: x[0])
    half = [r, r, r]
    half[ax] = hh
    if _too_far(M, p, c, half):
        return None
    if kind == "cone_side":
        return None  # "point"
    if kind == "cap":
        center_l = sign * hh * e_ax
        ring = _circle(center_l, u, v, r, CIRCLE_POINTS)
        face = _planar_from_local(M, Nmat, center_l, sign * e_ax, u, v, ring, math.pi * r * r, tw, up)
        if face is not None:  # extent from a denser circle (the 64-point ring under-reads by ~0.1%)
            dense = _xf(M, _circle(center_l, u, v, r, 720))
            face["size"] = _extent(dense, np.asarray(face["center"]), face["normal"], up)
        return face
    # cylinder side (any affine map, shear included): the world surface is a cylinder along D with
    # an elliptic right section (cut perpendicular to D). area = right-section perimeter x generator
    # length. center = foot of the perpendicular from the hit on the axis (clipped to the height);
    # outline = the right section through that point (contains the hit when not clipped).
    A0 = _xf(M, np.zeros(3))
    D = _dirv(M, e_ax)
    dd = float(D @ D)
    if not dd > 1e-300:
        return None
    dn = D / math.sqrt(dd)
    t = float(np.clip((p - A0) @ D / dd, -hh, hh))
    center = A0 + t * D
    n = _unit(rdir @ Nmat)
    if n is None:
        return None
    n = _orient(n, tw)
    Mu, Mv = _dirv(M, u) * r, _dirv(M, v) * r
    Mu, Mv = Mu - (Mu @ dn) * dn, Mv - (Mv @ dn) * dn  # right-section conjugate semi-diameters
    sv = np.linalg.svd(np.stack([Mu, Mv]), compute_uv=False)
    if not float(sv[1]) > 1e-300:
        return None
    length = math.sqrt(dd) * hgt
    area = _ramanujan(float(sv[0]), float(sv[1])) * length
    th = np.linspace(0.0, 2.0 * math.pi, CIRCLE_POINTS, endpoint=False)
    ring = center + np.cos(th)[:, None] * Mu + np.sin(th)[:, None] * Mv
    return _make_face("cylinder_side", center, n, area, [2.0 * float(sv[0]), length], ring)


def _sphere_face(sphere, M, Nmat, p, q, tw, time, up):
    np = _np()
    r = float(_attr(sphere.GetRadiusAttr(), time, 1.0))
    if not r > 0:
        return None
    length = float(np.linalg.norm(q))
    d = q / length if length > 1e-300 else np.array([0.0, 0.0, 1.0])
    if _too_far(M, p, d * r, [r, r, r]):
        return None
    center = _xf(M, np.zeros(3))
    n = _unit(d @ Nmat)
    if n is None:
        return None
    n = _orient(n, tw)
    axis = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(d, axis)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(d, e1)
    al = math.radians(SPHERE_RING_DEG)
    ring_l = r * (math.cos(al) * d + math.sin(al) * (_circle(np.zeros(3), e1, e2, 1.0, CIRCLE_POINTS)))
    sv = np.linalg.svd(M[:3, :3] * r, compute_uv=False)
    area = _ellipsoid_area(float(sv[0]), float(sv[1]), float(sv[2]))
    return _make_face("sphere", center, n, area, [2.0 * float(sv[0])] * 2, _xf(M, ring_l))
