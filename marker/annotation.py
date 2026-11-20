import asyncio
import copy
import json
import math
import os
import unicodedata

from .annotation_constant import *

__all__ = ["Annotation", "instance", "forget",
           "text_width", "one_line", "desc_lines", "clip_line", "panel_layout",
           "resize_rect", "move_rect", "leader_end", "surface_normal", "arrow_mesh", "plane_geometry", "plane_matrix", "ray_on_plane", "ray_in_plane", "prim_center",
           "marker_file_of", "clean_marker", "id_number"]


# -- 글자와 상자 (Kit 없이 도는 계산) ----------------------------------------------

def text_width(text: str, size: float) -> float:
    """글자 폭 어림. sc.Label(size) 의 실제 글자는 size x GLYPH_EM 크기라 그 em 으로 잰다.
    전각은 WIDE_EM, 나머지는 NARROW_EM (넉넉하게)"""
    em = 0.0
    for ch in text:
        em += WIDE_EM if unicodedata.east_asian_width(ch) in ("W", "F") else NARROW_EM
    return em * size * GLYPH_EM


def _with_ellipsis(text: str, max_px: float, size: float) -> str:
    """끝에 … 을 붙여 폭 안에 들게 줄인다"""
    out = text.rstrip()
    while out and text_width(out + ELLIPSIS, size) > max_px:
        out = out[:-1].rstrip()
    if out:
        return out + ELLIPSIS
    return ELLIPSIS if text_width(ELLIPSIS, size) <= max_px else ""


def one_line(text: str) -> str:
    """label 용 한 줄. 줄바꿈은 공백으로"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")


def desc_lines(text: str) -> list:
    """description 의 줄. 입력한 줄바꿈에서만 나누고, 빈 줄과 공백은 그대로"""
    if not text:
        return []
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def clip_line(text: str, max_px: float, size: float) -> str:
    """폭을 넘으면 오른쪽을 … 으로 자른다. 안 넘으면 그대로"""
    if text_width(text, size) <= max_px:
        return text
    return _with_ellipsis(text, max_px, size)


def panel_layout(title: str, desc: str, box=None, scale: float = TEXT_SCALE) -> dict:
    """패널 크기와 줄. 줄은 사용자가 넣은 줄바꿈 그대로 (자동 줄바꿈 없음).
    box 가 없으면 상자가 글에 맞춰 커지고 (폭 상한 없음), 있으면 폭을 넘는 줄은 … 으로 자르고
    높이를 넘으면 마지막 보이는 줄 끝을 … 로. 줄 수는 MAX_PANEL_LINES 까지"""
    fs = FONT_PX * scale
    ds = fs * DESC_RATIO
    th = fs * LINE_HEIGHT
    dh = ds * LINE_HEIGHT
    pad = PAD_PX * scale
    min_w = MIN_WIDE_PX * scale
    min_h = th + 2.0 * pad
    head = one_line(title)
    every = desc_lines(desc)
    if box is None:
        rows = MAX_PANEL_LINES
        lines = every[:rows]
        if len(every) > rows:
            lines[-1] = lines[-1].rstrip() + ELLIPSIS
        inner = max([text_width(head, fs)] + [text_width(line, ds) for line in lines])
        w = max(inner + 2.0 * pad, min_w)
        h = max(th + dh * len(lines) + 2.0 * pad, min_h)
    else:
        w = max(float(box[0]), min_w)
        h = max(float(box[1]), min_h)
        room = w - 2.0 * pad
        head = clip_line(head, room, fs)
        rows = max(0, min(int(math.floor((h - 2.0 * pad - th) / dh + 1e-6)), MAX_PANEL_LINES))
        lines = [clip_line(line, room, ds) for line in every[:rows]]
        if len(every) > rows > 0 and not lines[-1].endswith(ELLIPSIS):
            lines[-1] = _with_ellipsis(lines[-1], room, ds)  # 더 있다는 표시
    return {"w": w, "h": h, "title": head, "lines": lines, "fs": fs, "ds": ds,
            "th": th, "dh": dh, "pad": pad, "min": (min_w, min_h)}


def move_rect(offset, delta) -> list:
    """상자를 그만큼 옮긴 왼쪽 위"""
    return [float(offset[0]) + delta[0], float(offset[1]) + delta[1]]


def resize_rect(offset, size, edge, delta, minimum) -> tuple:
    """그 변을 delta 만큼 끈 (왼쪽 위, [w, h]). 맞은편 변은 그대로, 최소 크기 아래로는 안 줄인다.
    edge 는 (x, y): x -1 왼쪽 / 1 오른쪽 / 0 안 움직임, y 1 위 / -1 아래 / 0 안 움직임"""
    left, top = float(offset[0]), float(offset[1])
    right, bottom = left + float(size[0]), top - float(size[1])
    ex, ey = edge
    dx, dy = delta
    min_w, min_h = minimum
    if ex > 0:
        right = max(right + dx, left + min_w)
    elif ex < 0:
        left = min(left + dx, right - min_w)
    if ey > 0:
        top = max(top + dy, bottom + min_h)
    elif ey < 0:
        bottom = min(bottom + dy, top - min_h)
    return [left, top], [right - left, top - bottom]


def leader_end(offset, size):
    """앵커(0, 0)에서 상자 가운데를 향하는 선이 상자 테두리에 닿는 점. 앵커가 상자 안이면 None"""
    left, top = float(offset[0]), float(offset[1])
    right, bottom = left + float(size[0]), top - float(size[1])
    if left <= 0.0 <= right and bottom <= 0.0 <= top:
        return None
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    t = 0.0  # 앵커 -> 가운데 선이 상자에 들어가는 비율 (0..1)
    for d, lo, hi in ((cx, left, right), (cy, bottom, top)):
        if abs(d) > 1e-12:
            t = max(t, min(lo / d, hi / d))
    return [cx * t, cy * t]


def marker_file_of(usd_path: str) -> str:
    """형상 USD 와 같은 자리의 마커 파일. omniverse://srv/a/pump.usd -> omniverse://srv/a/pump_marker.json"""
    path = (usd_path or "").strip()
    if not path:
        return ""
    cut = max(path.rfind("/"), path.rfind("\\"))
    folder, name = path[:cut + 1], path[cut + 1:]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return folder + stem + MARKER_FILE_SUFFIX


def _remote(path: str) -> bool:
    """omniverse:// 같은 원격 경로인가"""
    head = path.split("://", 1)
    return len(head) == 2 and head[0].isalpha()


def _read_text(path: str):
    """로컬이든 원격이든 글을 통째로 읽는다. 파일이 없으면 None"""
    if _remote(path):
        import omni.client
        result, _version, content = omni.client.read_file(path)
        if result == omni.client.Result.ERROR_NOT_FOUND:
            return None
        if result != omni.client.Result.OK:
            raise OSError(f"{result} on {path}")
        return memoryview(content).tobytes().decode("utf-8-sig")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def _write_text(path: str, text: str) -> None:
    """글을 쓴다. 로컬은 옆자리에 썼다가 바꿔치기한다 (도중에 실패해도 기존 파일은 그대로)"""
    if _remote(path):
        import omni.client
        result = omni.client.write_file(path, text.encode("utf-8"))
        if result != omni.client.Result.OK:
            raise OSError(f"{result} on {path}")
        return
    spare = path + ".part"
    try:
        with open(spare, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(spare, path)
    except Exception:
        try:
            os.remove(spare)
        except OSError:
            pass
        raise


def _floats(v, n: int, positive: bool = False):
    """길이 n 의 유한한 숫자 목록이면 float 목록, 아니면 None"""
    if isinstance(v, (str, bytes, dict)) or not hasattr(v, "__len__") or len(v) != n:
        return None
    out = []
    for c in v:
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(float(c)):
            return None
        if positive and c <= 0:
            return None
        out.append(float(c))
    return out


def clean_marker(raw) -> dict:
    """파일에서 읽은 마커 하나를 검사해 고친 사본. 쓸 수 없으면 ValueError (이유)"""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    kind = raw.get("kind")
    if kind not in KINDS and kind != "plane":
        raise ValueError(f"unknown kind {kind!r}")
    mid = raw.get("id")
    m = {"id": mid if isinstance(mid, str) and mid else None, "kind": kind}
    label, desc = raw.get("label", ""), raw.get("description", "")
    m["label"] = one_line(label)[:MAX_LABEL] if isinstance(label, str) else ""
    m["description"] = desc[:MAX_DESCRIPTION] if isinstance(desc, str) else ""
    color = _floats(raw.get("color"), 3)
    m["color"] = color if color and all(0.0 <= c <= 1.0 for c in color) else list(DEFAULT_COLORS[kind])
    m["offset"] = _floats(raw.get("offset"), 2) or list(DEFAULT_OFFSET)
    m["box"] = _floats(raw.get("box"), 2, positive=True)
    font = raw.get("font_px")
    ok_font = isinstance(font, (int, float)) and not isinstance(font, bool) and MIN_FONT_PX <= font <= MAX_FONT_PX
    m["font_px"] = float(font) if ok_font else FONT_PX
    m["visible"] = raw.get("visible", True) if isinstance(raw.get("visible", True), bool) else True
    if kind == "plane":
        if raw.get("axis") not in PLANE_AXES:
            raise ValueError(f"plane axis {raw.get('axis')!r}")
        m["axis"] = raw["axis"]
        m["color"] = list(PLANE_COLORS[m["axis"]])  # 평면 색은 축 색 (파일 값은 안 씀)
        at = raw.get("at", 0.0)
        m["at"] = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at) else 0.0
        m["size"] = _floats(raw.get("size"), 2, positive=True) or list(PLANE_SIZE)
        m["shift"] = _floats(raw.get("shift"), 2) or [0.0, 0.0]
        m["origin"] = _floats(raw.get("origin"), 3) or [0.0, 0.0, 0.0]
        rot = _floats(raw.get("rot"), 9)
        if rot is not None:
            m["rot"] = rot
        m["position"] = _plane_of(m)["center"]
        return m
    pos = _floats(raw.get("position"), 3)
    if pos is None:
        raise ValueError("position missing or not 3 numbers")
    m["position"] = pos
    if kind in ARROW_KINDS:
        d = _floats(raw.get("direction"), 3)
        if d is None or _unit(d) is None:
            raise ValueError("direction missing or zero")
        m["direction"] = d
    return m


def id_number(marker_id, kind: str):
    """'<kind>_<n>' 꼴이면 n, 아니면 None"""
    if not isinstance(marker_id, str) or not marker_id.startswith(kind + "_"):
        return None
    tail = marker_id[len(kind) + 1:]
    return int(tail) if tail.isdigit() else None


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _mul(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    """단위 벡터. 0 이면 None"""
    if a is None:
        return None
    length = math.sqrt(_dot(a, a))
    if not math.isfinite(length) or length < 1e-12:
        return None
    return [a[0] / length, a[1] / length, a[2] / length]


def _perp_basis(d):
    """d 에 수직인 단위 벡터 둘"""
    axis = [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0]
    u = _unit(_cross(d, axis))
    return u, _cross(d, u)


def surface_normal(p0, p1, p2, toward):
    """세 점 삼각형의 단위 법선. toward 쪽으로 뒤집는다. 한 직선이면 None"""
    e1, e2 = _sub(p1, p0), _sub(p2, p0)
    n = _cross(e1, e2)
    length = math.sqrt(_dot(n, n))
    scale = max(math.sqrt(_dot(e1, e1)) * math.sqrt(_dot(e2, e2)), 1e-300)
    if length < 1e-12 or length / scale < 1e-6:
        return None
    n = [n[0] / length, n[1] / length, n[2] / length]
    if toward is not None and _dot(n, toward) < 0.0:
        n = [-n[0], -n[1], -n[2]]
    return n


def _rgba(color, alpha: float = 1.0):
    return [float(color[0]), float(color[1]), float(color[2]), float(alpha)]


def _place_body(mesh, pivot) -> None:
    """몸체 prim 을 피봇 자리에: xformOp:translate 하나만 (기즈모가 더한 회전 / 크기는 데이터에 담겼으니 걷는다)"""
    from pxr import Gf, UsdGeom
    xf = UsdGeom.Xformable(mesh.GetPrim())
    op = next((o for o in xf.GetOrderedXformOps() if o.GetOpType() == UsdGeom.XformOp.TypeTranslate), None)
    if op is None:
        op = xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
    op.Set(Gf.Vec3d(*[float(c) for c in pivot]))
    xf.SetXformOpOrder([op])


def _plane_state_name(state) -> str:
    if state is True:
        return "selected"
    if state is False or state not in PLANE_LOOKS:
        return "idle"
    return state


def _plane_look(marker, state: str):
    """평면 상태의 (색, 불투명도). 색은 축 색을 흰색 쪽으로 섞는다"""
    opacity, mix = PLANE_LOOKS[state]
    return [float(c) + (1.0 - float(c)) * mix for c in marker["color"][:3]], opacity


def _bright(color):
    """골랐을 때 색. 흰색 쪽으로"""
    return [float(c) + (1.0 - float(c)) * HIGHLIGHT_MIX for c in color[:3]]


def _arrow_ends(marker, length: float):
    """(꼬리, 머리) 월드. inlet 은 머리가 표면, outlet 은 꼬리가 표면"""
    p = marker["position"]
    d = _unit(marker["direction"]) or [0.0, 0.0, 1.0]
    if marker["kind"] == "inlet":
        return _sub(p, _mul(d, length)), list(p)
    return list(p), _add(p, _mul(d, length))


def plane_geometry(axis: str, at: float, size, shift=(0.0, 0.0), origin=(0.0, 0.0, 0.0), rot=None) -> dict:
    """축 평면 (월드). axis 는 xy / yz / zx, origin 은 기준점 (타겟 프림 바운드 중점),
    at 은 기준점에서 법선 축 방향 거리, size 는 [가로, 세로], shift 는 평면 안에서 옮긴 [가로 축, 세로 축] 거리.
    기준점에 가운데를 두고 at 만큼 법선 방향으로, shift 만큼 평면 안에서 옮긴다.
    돌려주는 것: center, corners (4점, 둘레 순), top (위쪽 변 가운데, 패널 자리), normal"""
    n, u, v = PLANE_AXES[axis]
    w, h = float(size[0]), float(size[1])
    center = [float(origin[i]) for i in range(3)]
    center[n] += float(at)
    center[u] += float(shift[0])
    center[v] += float(shift[1])

    U, V, N = _frame(axis, rot)  # 가로, 세로, 법선 (회전했으면 돌아간 축)

    def point(su, sv):
        return [center[i] + U[i] * su * w / 2.0 + V[i] * sv * h / 2.0 for i in range(3)]

    return {"center": center, "corners": [point(-1, -1), point(1, -1), point(1, 1), point(-1, 1)],
            "top": point(0, 1), "normal": list(N), "frame": (list(U), list(V), list(N))}


def _frame(axis: str, rot=None):
    """평면의 (가로, 세로, 법선) 단위 벡터. rot (9 값: 세 벡터) 가 있으면 그것, 아니면 축 그대로"""
    if rot is not None and len(rot) == 9:
        vs = [_unit([float(c) for c in rot[i:i + 3]]) for i in (0, 3, 6)]
        if all(x is not None for x in vs):
            return vs
    n, u, v = PLANE_AXES[axis]
    out = []
    for k in (u, v, n):
        e = [0.0, 0.0, 0.0]
        e[k] = 1.0
        out.append(e)
    return out


def _plane_of(marker) -> dict:
    return plane_geometry(marker["axis"], marker["at"], marker["size"], marker.get("shift", (0.0, 0.0)),
                          marker.get("origin", (0.0, 0.0, 0.0)), marker.get("rot"))


def ray_plane_point(marker, origin, direction):
    """광선이 그 평면 (회전 포함) 과 만나는 월드 점. 나란하면 None"""
    g = _plane_of(marker)
    nrm = g["normal"]
    o = [float(c) for c in origin]
    d = [float(c) for c in direction]
    den = _dot(d, nrm)
    if abs(den) < 1e-12:
        return None
    t = _dot(_sub(g["center"], o), nrm) / den
    return [o[i] + t * d[i] for i in range(3)]


def plane_level(marker) -> float:
    """그 평면의 법선 축 월드 좌표 (기준점 + at)"""
    n = PLANE_AXES[marker["axis"]][0]
    return float(marker.get("origin", (0.0, 0.0, 0.0))[n]) + float(marker["at"])


def prim_center(stage, prim_path: str):
    """그 프림의 월드 바운드 (축 정렬) 중점 [x, y, z]. 프림이 없거나 바운드가 비면 None"""
    got = prim_bounds(stage, prim_path)
    return got[0] if got else None


def prim_bounds(stage, prim_path: str):
    """그 프림의 월드 바운드 (축 정렬) (중점, 크기 [x, y, z]). 프림이 없거나 바운드가 비면 None"""
    try:
        from pxr import Usd, UsdGeom
        prim = stage.GetPrimAtPath(prim_path) if stage is not None and prim_path else None
        if prim is None or not prim.IsValid():
            return None
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if box.IsEmpty():
            return None
        c, size = box.GetMidpoint(), box.GetSize()
        out = [float(c[0]), float(c[1]), float(c[2])]
        dims = [float(size[0]), float(size[1]), float(size[2])]
        return (out, dims) if all(math.isfinite(x) for x in out + dims) else None
    except Exception:
        return None


def plane_matrix(marker) -> list:
    """sc.Rectangle (지역 XY, 가운데 원점) 을 그 평면에 눕히는 16 값 (Gf 와 같은 행 순서:
    0행 = 가로 축, 1행 = 세로 축, 2행 = 법선, 3행 = 가운데)"""
    n, u, v = PLANE_AXES[marker["axis"]]
    rows = []
    for k in (u, v, n):
        row = [0.0, 0.0, 0.0, 0.0]
        row[k] = 1.0
        rows += row
    c = _plane_of(marker)["center"]
    return rows + [c[0], c[1], c[2], 1.0]


def _ndc_ray(api, ndc):
    """뷰포트 NDC 의 월드 광선 (시작, 방향). 카메라 행렬로. 못 구하면 None"""
    try:
        from pxr import Gf
        to_world = Gf.Matrix4d(api.ndc_to_world)
        a = to_world.Transform(Gf.Vec3d(float(ndc[0]), float(ndc[1]), 0.0))
        b = to_world.Transform(Gf.Vec3d(float(ndc[0]), float(ndc[1]), 0.5))
        d = [b[i] - a[i] for i in range(3)]
        inv = Gf.Matrix4d(api.view).GetInverse()
        ahead = inv.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0))
        if _dot(d, [ahead[0], ahead[1], ahead[2]]) < 0:
            d = [-c for c in d]
        cam = inv.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
        if abs(api.projection[3][3]) < 1e-9:  # 원근: 카메라에서
            o = [cam[0], cam[1], cam[2]]
        else:
            o = [a[i] - d[i] * 1e6 for i in range(3)]
        d = _unit(d)
        return (o, d) if d is not None else None
    except Exception:
        return None


def ray_near_segment(origin, direction, a, b, radius: float):
    """광선이 선분 a-b 에 radius 안으로 지나가면 그 가장 가까운 자리까지의 광선 거리, 아니면 None"""
    d = _unit([float(c) for c in direction])
    if d is None:
        return None
    o = [float(c) for c in origin]
    u = _sub(b, a)
    uu = _dot(u, u)
    if uu <= 0:
        return None
    w = _sub(o, a)
    du, dw, uw = _dot(d, u), _dot(d, w), _dot(u, w)
    denom = uu - du * du
    s = 0.0 if denom < 1e-12 else max(0.0, min(1.0, (uw - du * dw) / denom))  # 선분 위 자리
    q = _add(a, _mul(u, s))
    t = _dot(_sub(q, o), d)  # 그 점에 가장 가까운 광선 자리
    if t <= 0:
        return None
    gap = _sub(_add(o, _mul(d, t)), q)
    return t if _dot(gap, gap) <= radius * radius else None


def ray_in_plane(marker, origin, direction):
    """광선이 그 평면 사각형 안에 닿으면 시작점에서의 거리, 아니면 None"""
    p = ray_plane_point(marker, origin, direction)
    if p is None:
        return None
    gap = [p[i] - float(origin[i]) for i in range(3)]
    if _dot(gap, [float(c) for c in direction]) <= 0:
        return None  # 카메라 뒤
    g = _plane_of(marker)
    U, V, _N = g["frame"]
    rel = _sub(p, g["center"])
    w, h = float(marker["size"][0]), float(marker["size"][1])
    if abs(_dot(rel, U)) > w / 2.0 or abs(_dot(rel, V)) > h / 2.0:
        return None
    return math.sqrt(_dot(gap, gap))


def ray_on_plane(axis: str, at: float, origin, direction):
    """광선이 그 축 평면(법선 축 = at)과 만나는 월드 점. 나란하면 None"""
    n = PLANE_AXES[axis][0]
    try:
        o = [float(origin[i]) for i in range(3)]
        d = [float(direction[i]) for i in range(3)]
    except Exception:
        return None
    if abs(d[n]) < 1e-12:
        return None
    t = (float(at) - o[n]) / d[n]
    return [o[i] + t * d[i] for i in range(3)]


def _panel_anchor(marker, length: float):
    """패널이 매달리는 월드 점. label 은 점, inlet 은 꼬리, outlet 은 머리, plane 은 위쪽 변 가운데"""
    if marker["kind"] == "plane":
        return _plane_of(marker)["top"]
    if marker["kind"] == "inlet":
        return _arrow_ends(marker, length)[0]
    if marker["kind"] == "outlet":
        return _arrow_ends(marker, length)[1]
    return list(marker["position"])


def _title_of(marker) -> str:
    """패널 첫 줄. 이름이 없으면 id"""
    return marker["label"] or marker["id"]


# -- 뷰포트 (Kit) ------------------------------------------------------------------

def _camera_of(api, point):
    """(점에서 카메라 쪽 벡터, 카메라까지 거리). 직교면 거리 None, 못 물으면 (None, None)"""
    try:
        from pxr import Gf
        inv = Gf.Matrix4d(api.view).GetInverse()
        if abs(api.projection[3][3]) < 1e-9:
            cam = inv.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
            toward = [cam[0] - point[0], cam[1] - point[1], cam[2] - point[2]]
            return toward, math.sqrt(_dot(toward, toward))
        back = inv.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0))
        return [back[0], back[1], back[2]], None
    except Exception:
        return None, None


def _resolution(api):
    """렌더 해상도 (w, h). 못 물으면 None"""
    try:
        w, h = int(api.resolution[0]), int(api.resolution[1])
    except Exception:
        return None
    return (w, h) if w > 0 and h > 0 else None


def _texture_pixel(api, ndc):
    """NDC 를 렌더 이미지 픽셀로. 이미지 밖이면 None"""
    try:
        pixel, inside = api.map_ndc_to_texture_pixel((float(ndc[0]), float(ndc[1])))
    except Exception as e:
        print(f"{TAG} cannot map the click: {type(e).__name__}: {e}")
        return None
    if not inside:
        return None
    return int(pixel[0]), int(pixel[1])


def _block_selection(api):
    """Kit 의 클릭 선택을 끈다. 받은 토큰을 놓으면 되살아난다"""
    if api is None:
        return None
    try:
        from omni.kit.viewport.utility import disable_selection
        return disable_selection(api)
    except Exception as e:
        print(f"{TAG} selection NOT off: {type(e).__name__}: {e}")
        return None


def _xy(ndc):
    """제스처 마우스 값을 (x, y) 로"""
    try:
        return float(ndc[0]), float(ndc[1])
    except Exception:
        return None


ZONE_NAMES = {None: "body", (-1, 0): "L", (1, 0): "R", (0, 1): "T", (0, -1): "B"}


def _later(fn) -> None:
    """다음 루프에 부른다. 지금 지우는 도형의 콜백 안에서 부르지 않으려고"""
    def run():
        try:
            fn()
        except Exception as e:
            print(f"{TAG} deferred call failed: {type(e).__name__}: {e}")

    asyncio.get_event_loop().call_soon(run)


class _OrbitYield:
    """orbit 익스텐션(카메라 조작)을 그 뷰포트에서 잠시 끈다. 마커가 마우스를 쓰는 동안 (패널 위, 끌기, 찍기 대기)
    그 뷰포트 orbit manipulator 의 enabled 를 False, 다 놓으면 True. 뷰포트 id 마다 따로.
    orbit 이 없거나 모양이 다르면 아무것도 안 한다 (마커는 그대로 돈다)"""

    _off = set()      # 우리가 끈 뷰포트 id
    _warned = False

    @classmethod
    def hold(cls, viewport_id) -> None:
        if not ORBIT_YIELD or viewport_id in cls._off:
            return
        if cls._set(viewport_id, False):
            cls._off.add(viewport_id)

    @classmethod
    def release(cls, viewport_id) -> None:
        if viewport_id in cls._off:
            cls._off.discard(viewport_id)
            cls._set(viewport_id, True)

    @classmethod
    def release_all(cls) -> None:
        """익스텐션이 꺼질 때. 끈 것은 반드시 켠다"""
        for vid in list(cls._off):
            cls.release(vid)

    @classmethod
    def holding(cls, viewport_id=None) -> bool:
        return bool(cls._off) if viewport_id is None else viewport_id in cls._off

    @classmethod
    def _manipulator(cls, viewport_id):
        import importlib
        ext = getattr(importlib.import_module(ORBIT_MODULE), ORBIT_CLASS)
        scenes = getattr(ext, ORBIT_SCENES)
        scene = scenes.get(viewport_id)
        if scene is None:
            raise LookupError(f"no orbit scene for viewport {viewport_id!r} (keys: {list(scenes)[:5]})")
        return getattr(scene, ORBIT_MANIPULATOR)

    @classmethod
    def _set(cls, viewport_id, enable: bool) -> bool:
        """그 뷰포트 orbit 을 켜고 끈다. 됐으면 True"""
        try:
            m = cls._manipulator(viewport_id)
            if bool(getattr(m, ORBIT_ENABLED)) != enable:
                setattr(m, ORBIT_ENABLED, enable)
            return True
        except Exception as e:
            if not cls._warned:
                cls._warned = True
                print(f"{TAG} could not set orbit enable={enable} on viewport {viewport_id!r}: {type(e).__name__}: {e}")
            return False


class _OrbitWatch:
    """orbit 이 카메라를 끄는 중인가. 메시지 버스의 orbit 드래그 시작 (on_began_first) / 끝 (on_ended) 을 구독한다.
    끄는 동안 마커 위로 마우스가 지나가도 마커는 아무것도 안 한다 (orbit 을 끄지 않는다).
    그동안 들어온 hover 는 기억했다가, orbit 이 끝났을 때 아직 그 위면 그때 들인다"""

    _dragging = False
    _subs = []
    _waiting = {}     # key -> 들일 함수 (hover 들어옴)

    @classmethod
    def start(cls) -> None:
        cls.stop()
        try:
            import carb.events
            import omni.kit.app
            bus = omni.kit.app.get_app().get_message_bus_event_stream()
            cls._subs = [
                bus.create_subscription_to_pop_by_type(carb.events.type_from_string(ORBIT_DRAG_BEGAN),
                                                       lambda _e: cls._began(), name=f"{NAME}.orbit_began"),
                bus.create_subscription_to_pop_by_type(carb.events.type_from_string(ORBIT_DRAG_ENDED),
                                                       lambda _e: cls._ended(), name=f"{NAME}.orbit_ended"),
            ]
        except Exception as e:
            cls._subs = []
            print(f"{TAG} cannot watch the orbit drag: {type(e).__name__}: {e}")

    @classmethod
    def stop(cls) -> None:
        for sub in cls._subs:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        cls._subs = []
        cls._dragging = False
        cls._waiting = {}

    @classmethod
    def busy(cls) -> bool:
        """orbit 이 끄는 중인가. 끝 신호를 못 받았는데 마우스 버튼이 다 떨어져 있으면 끝난 것으로 본다"""
        if cls._dragging and _Mouse.pressed() is False:
            cls._ended()
        return cls._dragging

    @classmethod
    def later(cls, key, fn) -> None:
        cls._waiting[key] = fn

    @classmethod
    def forget(cls, key) -> None:
        cls._waiting.pop(key, None)

    @classmethod
    def _began(cls) -> None:
        if _OrbitYield.holding():
            return  # 우리가 orbit 을 꺼 둔 동안의 시작 신호는 진짜 카메라 끌기가 아니다 (우리 끌기에 orbit 제스처도 반응)
        cls._dragging = True

    @classmethod
    def _ended(cls) -> None:
        cls._dragging = False
        waiting, cls._waiting = cls._waiting, {}
        for fn in waiting.values():
            try:
                fn()
            except Exception as e:
                print(f"{TAG} hover after orbit failed: {type(e).__name__}: {e}")


class _Cursor:
    """앱 창 커서 모양. 변 손잡이 위에서는 <-> / 위아래 화살표, 끝나면 되돌린다.
    여럿이 잡을 수 있게 key 별로 쥐고, 마지막에 잡은 모양을 쓴다. 바꿀 수 없으면 조용히 그대로 둔다"""

    _holds = {}
    _way = None  # 바꾸는 방법: ("window", 커서 창) / ("carb", (windowing, app window)) / False

    @classmethod
    def hold(cls, key: str, shape: str) -> None:
        cls._holds.pop(key, None)
        cls._holds[key] = shape
        cls._apply()

    @classmethod
    def release(cls, key: str) -> None:
        if cls._holds.pop(key, None) is not None:
            cls._apply()

    @classmethod
    def release_prefix(cls, prefix: str) -> None:
        keys = [k for k in cls._holds if k.startswith(prefix)]
        for k in keys:
            del cls._holds[k]
        if keys:
            cls._apply()

    @classmethod
    def _apply(cls) -> None:
        shape = list(cls._holds.values())[-1] if cls._holds else None
        try:
            way = cls._find()
            if not way:
                return
            import carb.windowing
            std = carb.windowing.CursorStandardShape
            kind = {"h": std.HORIZONTAL_RESIZE, "v": std.VERTICAL_RESIZE}.get(shape)
            if way[0] == "window":
                if kind is None:
                    way[1].clear_overridden_cursor_shape()
                else:
                    way[1].override_cursor_shape(kind)
            else:
                windowing, app_window = way[1]
                windowing.set_cursor(app_window, windowing.create_cursor_standard(kind or std.ARROW))
        except Exception as e:
            cls._way = False
            print(f"{TAG} cursor shape stays as is: {type(e).__name__}: {e}")

    @classmethod
    def _find(cls):
        if cls._way is not None:
            return cls._way
        try:
            import omni.kit.window.cursor as cursor_ext
            cls._way = ("window", cursor_ext.get_main_window())
            return cls._way
        except Exception:
            pass
        try:
            import carb.windowing
            import omni.appwindow
            cls._way = ("carb", (carb.windowing.acquire_windowing_interface(),
                                 omni.appwindow.get_default_app_window().get_window()))
        except Exception as e:
            cls._way = False
            print(f"{TAG} cursor shape stays as is: {type(e).__name__}: {e}")
        return cls._way


class _Mouse:
    """앱 창 기준 커서 자리 (ui 픽셀, y 아래로). 끄는 거리를 픽셀로 재는 데 쓴다"""

    _cursor = None

    @classmethod
    def at(cls):
        """지금 커서 자리. 못 물으면 None"""
        if cls._cursor is False:
            return None
        try:
            import omni.ui as ui
            if cls._cursor is None:
                import carb.input
                import omni.appwindow
                cls._cursor = (carb.input.acquire_input_interface(),
                               omni.appwindow.get_default_app_window().get_mouse())
            reader, mouse = cls._cursor
            at = reader.get_mouse_coords_normalized(mouse)
            return (at.x * ui.Workspace.get_main_window_width(),
                    at.y * ui.Workspace.get_main_window_height())
        except Exception as e:
            cls._cursor = False
            print(f"{TAG} cannot ask the cursor, dragging falls back to NDC: {type(e).__name__}: {e}")
            return None

    @classmethod
    def pressed(cls):
        """마우스 버튼 (왼, 오른, 가운데) 중 하나라도 눌려 있나. 못 물으면 None"""
        try:
            import carb.input
            import omni.appwindow
            reader = carb.input.acquire_input_interface()
            mouse = omni.appwindow.get_default_app_window().get_mouse()
            M = carb.input.MouseInput
            return any(reader.get_mouse_button_flags(mouse, b) & carb.input.BUTTON_FLAG_DOWN
                       for b in (M.LEFT_BUTTON, M.RIGHT_BUTTON, M.MIDDLE_BUTTON))
        except Exception:
            return None


def _hover_in(view, key, take) -> None:
    """마커 위에 마우스가 들어옴. orbit 이 끄는 중이면 들이지 않고 (orbit 을 안 끈다), 끝나면 들인다"""
    if _OrbitWatch.busy():
        _OrbitWatch.later((id(view), key), take)
        return
    take()


def _close(a, b, eps: float = 1e-7) -> bool:
    return len(a) == len(b) and all(abs(float(a[i]) - float(b[i])) <= eps for i in range(len(a)))


def _clear_kit_selection() -> None:
    """Kit 선택 (형상) 을 푼다. 마커 패널이나 평면을 잡을 때. orbit 은 그때 꺼져 있어 그 클릭을 못 본다"""
    try:
        import omni.usd
        omni.usd.get_context().get_selection().clear_selected_prim_paths()
    except Exception:
        pass


def _plane_named(args):
    """쿼리 답이 우리 평면이나 화살표면 그 id"""
    try:
        path = str(args[0] or "")
    except Exception:
        return None
    for scope in (PLANE_SCOPE, ARROW_SCOPE):
        head = scope + "/"
        if path.startswith(head):
            return path[len(head):].split("/")[0] or None
    return None


def _hit(args):
    """쿼리 콜백 값을 (prim 경로, [x, y, z]) 로. 빈 곳이거나 우리 화살표면 None"""
    if not args or not args[0] or str(args[0]).startswith(MARKER_ROOT):
        return None
    try:
        pos = [float(args[1][0]), float(args[1][1]), float(args[1][2])]
    except Exception:
        return None
    if not all(math.isfinite(c) for c in pos):
        return None
    return str(args[0]), pos


class _Query:
    """가운데와 이웃 두 픽셀을 물어 표면 점과 법선을 얻는다. 결과는 done(query, 결과)"""

    _seq = 0

    def __init__(self, api, pixel, done):
        """바로 묻는다. 묻지도 못하면 done 에 RuntimeError"""
        self._api = api
        self._done = done
        self._loop = asyncio.get_event_loop()
        self._hits = [None, None, None]
        self._got = [False, False, False]
        self._timer = None
        self.over = False
        try:
            self._ask(pixel)
        except Exception as e:
            self._finish(e if isinstance(e, RuntimeError) else RuntimeError(f"viewport query failed: {e}"))

    def _ask(self, pixel) -> None:
        """세 픽셀을 묻는다. 가운데가 거부되면 실패"""
        px, py = pixel
        res = _resolution(self._api)
        if res is not None:
            w, h = res
            px, py = min(max(px, 0), w - 1), min(max(py, 0), h - 1)
            nx = px + NEIGHBOR_PX if px + NEIGHBOR_PX < w else max(px - NEIGHBOR_PX, 0)
            ny = py + NEIGHBOR_PX if py + NEIGHBOR_PX < h else max(py - NEIGHBOR_PX, 0)
        else:
            nx, ny = px + NEIGHBOR_PX, py + NEIGHBOR_PX
        _Query._seq += 1
        base = f"{NAME}.pick.{_Query._seq}"
        for i, pix in enumerate(((px, py), (nx, py), (px, ny))):
            try:
                ok = self._api.request_query(pix, self._callback(i), query_name=f"{base}.{i}")
            except TypeError:
                ok = self._api.request_query(pix, self._callback(i)) if i == 0 else False
            except Exception:
                if i == 0:
                    raise
                ok = False
            if self.over:
                return
            if not ok:
                if i == 0:
                    raise RuntimeError("viewport rejected the query")
                self._got[i] = True
        self._timer = self._loop.call_later(PICK_TIMEOUT_S, self._timeout)

    def _callback(self, i: int):
        """렌더 쪽에서 불릴 수 있어 메인 루프로 넘긴다"""
        def on_query(*args):
            try:
                self._loop.call_soon_threadsafe(self._result, i, args)
            except RuntimeError:
                pass

        return on_query

    def _result(self, i: int, args) -> None:
        """답 하나. 가운데가 빈 곳이면 바로 끝"""
        if self.over or self._got[i]:
            return
        self._got[i] = True
        self._hits[i] = _hit(args)
        if i == 0 and self._hits[0] is None:
            self._finish(None)
            return
        if all(self._got):
            self._finish(self._compose())

    def _compose(self):
        """가운데 점. 세 점이 같은 prim 이면 법선도"""
        centre = self._hits[0]
        if centre is None:
            return None
        path, p0 = centre
        a, b = self._hits[1], self._hits[2]
        normal = None
        if a is not None and b is not None and a[0] == path and b[0] == path:
            normal = surface_normal(p0, a[1], b[1], _camera_of(self._api, p0)[0])
        return {"position": p0, "normal": normal}

    def _timeout(self) -> None:
        """이웃이 안 오면 위치만, 가운데도 안 오면 실패"""
        self._timer = None
        if self.over:
            return
        if self._got[0] and self._hits[0] is not None:
            self._finish(self._compose())
            return
        self._finish(TimeoutError("viewport query did not answer"))

    def cancel(self) -> None:
        """답을 버린다"""
        self.over = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _finish(self, result) -> None:
        """한 번만 알린다"""
        if self.over:
            return
        self.over = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        try:
            self._done(self, result)
        except Exception as e:
            print(f"{TAG} pick result handler failed: {type(e).__name__}: {e}")


_GESTURES = None


def _gesture_types():
    """(Click, Drag, Hover) 제스처 클래스. omni.ui.scene 을 그때 가서 부른다"""
    global _GESTURES
    if _GESTURES is not None:
        return _GESTURES
    from omni.ui import scene as sc

    def mouse_of(gesture):
        try:
            return gesture.sender.gesture_payload.mouse
        except Exception:
            return None

    def payload_of(gesture):
        try:
            return gesture.sender.gesture_payload
        except Exception:
            return None

    class Manager(sc.GestureManager):
        """우리 제스처끼리 누가 이기나. 기본 매니저는 먼저 시작한 것이 다른 것을 다 막는다.
        여기서는 priority 가 더 높은 쪽만 낮은 쪽을 막는다 (찍기 대기 > 패널 > 평면).
        hover 는 막지도, 막히지도 않는다 (겹친 평면 사각형 hover 가 다 들어와야 가까운 평면을 고른다)"""

        def can_be_prevented(self, gesture):
            return not isinstance(gesture, sc.HoverGesture)

        def should_prevent(self, gesture, preventer):
            if isinstance(gesture, sc.HoverGesture) or isinstance(preventer, sc.HoverGesture):
                return False
            if preventer.state not in (sc.GestureState.BEGAN, sc.GestureState.CHANGED):
                return False
            # 같은 priority 끼리는 막지 않는다. 겹친 패널에서 고르지 않은 패널의 끌기가 먼저 시작해
            # (owner 는 거절해도 제스처는 살아 있어) 위 패널 끌기를 막았다. 같은 패널의 클릭과 끌기도 서로 안 막는다
            return getattr(preventer, "priority", 0) > getattr(gesture, "priority", 0)

    try:
        manager = Manager()
    except Exception as e:
        manager = None
        print(f"{TAG} no gesture manager, Kit default decides: {type(e).__name__}: {e}")

    def base(gesture_cls, obj):
        if manager is None:
            gesture_cls.__init__(obj)
            return
        try:
            gesture_cls.__init__(obj, manager=manager)
        except TypeError:
            gesture_cls.__init__(obj)

    class Click(sc.ClickGesture):
        def __init__(self, fn, priority=GESTURE_PRIORITY, name="click"):
            base(sc.ClickGesture, self)
            self._fn, self._priority, self._name = fn, priority, name

        def on_ended(self, *_args):
            if self.state == sc.GestureState.CANCELED:
                return
            self._fn(mouse_of(self))

        @property
        def name(self):
            return f"{NAME}.{self._name}"

        @property
        def priority(self):
            return self._priority

    class Drag(sc.DragGesture):
        def __init__(self, began, changed, ended, priority=GESTURE_PRIORITY, name="drag", raw=False, at_down=False):
            base(sc.DragGesture, self)
            if at_down:  # 움직이기 전, 누르는 순간 시작 (누름 색). 없는 Kit 이면 움직인 뒤 시작
                try:
                    self.check_mouse_moved = False
                except Exception:
                    pass
            self._began, self._changed, self._ended = began, changed, ended
            self._priority, self._name = priority, name
            self._take = payload_of if raw else mouse_of  # raw: 광선까지 든 payload 를 넘긴다

        def on_began(self, *_args):
            self._began(self._take(self))

        def on_changed(self, *_args):
            self._changed(self._take(self))

        def on_ended(self, *_args):
            self._ended()

        @property
        def name(self):
            return f"{NAME}.{self._name}"

        @property
        def priority(self):
            return self._priority

    class Hover(sc.HoverGesture):
        def __init__(self, began, changed, ended, name="hover", raw=False):
            base(sc.HoverGesture, self)
            self._began, self._changed, self._ended, self._name = began, changed, ended, name
            self._take = payload_of if raw else mouse_of  # raw: 광선까지 든 payload 를 넘긴다

        def on_began(self, *_args):
            self._began(self._take(self))

        def on_changed(self, *_args):
            self._changed(self._take(self))

        def on_ended(self, *_args):
            self._ended()

        @property
        def name(self):
            return f"{NAME}.{self._name}"

    _GESTURES = (Click, Drag, Hover)
    return _GESTURES


def arrow_mesh(tail, tip, size: float) -> dict:
    """화살표 메시 (월드). 원통 몸통 + 원뿔 머리, 뚜껑까지 막고 면 꼭짓점마다 법선을 단다.
    돌려주는 것: points, counts, indices, normals (faceVarying)"""
    d = _unit(_sub(tip, tail)) or [0.0, 0.0, 1.0]
    length = math.sqrt(_dot(_sub(tip, tail), _sub(tip, tail)))
    head = min(ARROW_HEAD * size, length)
    r_head = ARROW_RADIUS * size
    r_shaft = min(ARROW_SHAFT * size, r_head)
    base = _sub(tip, _mul(d, head))
    u, v = _perp_basis(d)
    k = ARROW_RINGS
    dirs = []
    for i in range(k + 1):
        a = 2.0 * math.pi * i / k
        dirs.append(_add(_mul(u, math.cos(a)), _mul(v, math.sin(a))))
    slope = _unit([head, r_head, 0.0])  # 원뿔 옆면 법선 = 바깥 x cos + 앞 x sin
    points, counts, indices, normals = [], [], [], []

    def face(pts, nrm):
        start = len(points)
        points.extend(pts)
        normals.extend(nrm)
        counts.append(len(pts))
        indices.extend(range(start, start + len(pts)))

    back = _mul(d, -1.0)
    for i in range(k):  # 몸통 옆면 (바깥이 앞면)
        a, b = dirs[i], dirs[i + 1]
        face([_add(tail, _mul(a, r_shaft)), _add(tail, _mul(b, r_shaft)),
              _add(base, _mul(b, r_shaft)), _add(base, _mul(a, r_shaft))], [a, b, b, a])
    face([_add(tail, _mul(dirs[i], r_shaft)) for i in reversed(range(k))], [back] * k)  # 꼬리 뚜껑
    face([_add(base, _mul(dirs[i], r_head)) for i in reversed(range(k))], [back] * k)  # 원뿔 밑면
    for i in range(k):  # 원뿔 옆면
        a, b = dirs[i], dirs[i + 1]
        mid = _unit(_add(a, b)) or a
        na = _add(_mul(a, slope[0]), _mul(d, slope[1]))
        nb = _add(_mul(b, slope[0]), _mul(d, slope[1]))
        nt = _add(_mul(mid, slope[0]), _mul(d, slope[1]))
        face([_add(base, _mul(a, r_head)), _add(base, _mul(b, r_head)), list(tip)], [na, nb, nt])
    return {"points": points, "counts": counts, "indices": indices, "normals": normals}


class _Arrows:
    """inlet / outlet 화살표. 세션 레이어 USD 메시라 RTX 가 그리고 형상 뒤면 가려진다.
    스테이지에 하나씩이라 뷰포트마다가 아니라 여기서 한 번만 쓴다"""

    def __init__(self):
        self._warned = False
        self.authored = {}  # id -> 마지막으로 쓴 피봇, 방향 (화살표) / 축 (평면). 기즈모가 바꾼 것을 되읽는 기준

    def _fail(self, what: str, e: Exception) -> None:
        if not self._warned:
            self._warned = True
            print(f"{TAG} arrows: could not {what}: {type(e).__name__}: {e}")

    @staticmethod
    def _stage():
        import omni.usd
        return omni.usd.get_context().get_stage()

    def draw(self, marker, picked: bool, length: float) -> bool:
        """그 마커의 화살표를 (다시) 쓴다. 길이는 모든 화살표가 같은 length. 고른 것은 밝은 머티리얼"""
        try:
            from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
            stage = self._stage()
            if stage is None:
                return False
            tail, tip = _arrow_ends(marker, length)
            mesh_data = arrow_mesh(tail, tip, length)
            pivot = list(marker["position"])  # 표면에 닿는 끝 (inlet 머리, outlet 꼬리): 회전 중심
            base = [float(c) * ARROW_SHADE for c in marker["color"][:3]]  # 머티리얼은 마커 색보다 진하게
            color = _bright(base) if picked else base
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                self._root(stage, UsdGeom)
                mesh = UsdGeom.Mesh.Define(stage, f"{ARROW_SCOPE}/{marker['id']}")
                pts = [_sub(p, pivot) for p in mesh_data["points"]]  # 지역 (피봇 기준)
                _place_body(mesh, pivot)
                self.authored[marker["id"]] = {"pivot": pivot, "dir": _unit(marker["direction"]) or [0.0, 0.0, 1.0]}
                mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
                mesh.CreateFaceVertexCountsAttr(Vt.IntArray(mesh_data["counts"]))
                mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(mesh_data["indices"]))
                mesh.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*n) for n in mesh_data["normals"]]))
                mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
                mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
                mesh.CreateDoubleSidedAttr(False)
                mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
                low = [min(p[i] for p in pts) for i in range(3)]
                high = [max(p[i] for p in pts) for i in range(3)]
                mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*low), Gf.Vec3f(*high)]))
                mesh.GetPrim().CreateAttribute("primvars:doNotCastShadows",
                                               Sdf.ValueTypeNames.Bool).Set(True)
                look = self._material(stage, f"{marker['kind']}{'_picked' if picked else ''}", color,
                                      ARROW_PICKED_EMISSION if picked else ARROW_EMISSION)
                UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(look)  # 새 USD 는 Apply 가 있어야 바인딩을 본다
            return True
        except Exception as e:
            self._fail("draw", e)
            return False

    def draw_plane(self, marker, state="idle") -> bool:
        """그 plane 의 반투명 사각형을 (다시) 쓴다. 앞뒤 두 면, 그림자 없음.
        state 는 idle / selected / hover / press (True / False 는 selected / idle)"""
        state = _plane_state_name(state)
        try:
            from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
            stage = self._stage()
            if stage is None:
                return False
            plane = _plane_of(marker)
            color, opacity = _plane_look(marker, state)
            # 뒷면은 법선 반대쪽으로 조금 띄운다. 같은 자리에 겹치면 두 면의 대각선이 X 자로 보였다
            gap = PLANE_GAP_RATIO * max(marker["size"])
            back = [_sub(p, _mul(plane["normal"], gap)) for p in plane["corners"]]
            centre = plane["center"]  # 회전 중심
            pts = [_sub(p, centre) for p in plane["corners"] + back]  # 지역 (가운데 기준)
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                self._root(stage, UsdGeom)
                mesh = UsdGeom.Mesh.Define(stage, f"{PLANE_SCOPE}/{marker['id']}")
                _place_body(mesh, centre)
                self.authored[marker["id"]] = {"pivot": list(centre), "frame": plane["frame"]}
                mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
                # 앞면과 뒷면을 따로 둔다. doubleSided 만으로는 RTX 가 뒤에서 안 그렸다 (ebs 판도 같은 방식)
                mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4, 4]))
                mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([0, 1, 2, 3, 7, 6, 5, 4]))
                mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
                mesh.CreateDoubleSidedAttr(False)
                mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
                mesh.CreateDisplayOpacityAttr(Vt.FloatArray([opacity]))
                low = [min(p[i] for p in pts) for i in range(3)]
                high = [max(p[i] for p in pts) for i in range(3)]
                mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*low), Gf.Vec3f(*high)]))
                mesh.GetPrim().CreateAttribute("primvars:doNotCastShadows",
                                               Sdf.ValueTypeNames.Bool).Set(True)
                look = self._material(stage, f"plane_{marker['axis']}_{state}", color,
                                      PLANE_EMISSION, opacity, unlit=True)  # 그림자, 조명과 무관한 색
                UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(look)
            return True
        except Exception as e:
            self._fail("draw a plane", e)
            return False

    def plane_look(self, marker, state: str) -> bool:
        """평면 상태 색만 바꾼다 (메시는 그대로). hover / press 처럼 자주 바뀔 때"""
        state = _plane_state_name(state)
        try:
            from pxr import Gf, Usd, UsdGeom, UsdShade, Vt
            stage = self._stage()
            prim = stage.GetPrimAtPath(f"{PLANE_SCOPE}/{marker['id']}") if stage is not None else None
            if prim is None or not prim.IsValid():
                return self.draw_plane(marker, state)
            color, opacity = _plane_look(marker, state)
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                mesh = UsdGeom.Mesh(prim)
                mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
                mesh.CreateDisplayOpacityAttr(Vt.FloatArray([opacity]))
                path = f"{LOOKS_SCOPE}/plane_{marker['axis']}_{state}"
                look = UsdShade.Material(stage.GetPrimAtPath(path))
                if not look.GetPrim().IsValid():
                    look = self._material(stage, f"plane_{marker['axis']}_{state}", color,
                                          PLANE_EMISSION, opacity, unlit=True)
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(look)
            return True
        except Exception as e:
            self._fail("restyle a plane", e)
            return False

    def _root(self, stage, UsdGeom) -> None:
        """뿌리를 세우고 클릭, 선택에서 뺀다 (찍기가 화살표, 평면에 막히지 않게)"""
        if stage.GetPrimAtPath(ARROW_SCOPE).IsValid() and stage.GetPrimAtPath(PLANE_SCOPE).IsValid():
            return
        root = UsdGeom.Scope.Define(stage, MARKER_ROOT).GetPrim()
        UsdGeom.Scope.Define(stage, ARROW_SCOPE)
        UsdGeom.Scope.Define(stage, PLANE_SCOPE)
        UsdGeom.Scope.Define(stage, LOOKS_SCOPE)
        try:
            root.SetMetadata("hide_in_stage_window", True)
        except Exception:
            pass
        try:
            import omni.usd
            omni.usd.get_context().set_pickable(MARKER_ROOT, False)
        except Exception as e:
            print(f"{TAG} arrows stay pickable (query filter still skips them): {e}")

    @staticmethod
    def _material(stage, name: str, color, emission: float, opacity: float = 1.0, unlit: bool = False):
        """머티리얼. preview 와 OmniPBR 두 셰이더. 고른 것은 조금 빛나게, opacity < 1 이면 반투명.
        unlit 이면 diffuse 를 끄고 색을 발광으로만 낸다 (조명, 그림자에 상관없이 늘 같은 색)"""
        from pxr import Gf, Sdf, UsdShade
        path = f"{LOOKS_SCOPE}/{name}"
        material = UsdShade.Material.Define(stage, path)
        rgb = Gf.Vec3f(*[float(c) for c in color[:3]])
        dark = Gf.Vec3f(0.0, 0.0, 0.0)
        preview = UsdShade.Shader.Define(stage, path + "/shader")
        preview.CreateIdAttr("UsdPreviewSurface")
        preview.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(dark if unlit else rgb)
        preview.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            rgb if unlit else rgb * (ARROW_PREVIEW_GLOW if emission > 0 else 0.0))
        preview.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(ARROW_ROUGHNESS)
        preview.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        preview.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))
        material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), "surface")
        mdl = UsdShade.Shader.Define(stage, path + "/mdl")
        mdl.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
        mdl.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
        for key, kind, value in (
                ("diffuse_color_constant", Sdf.ValueTypeNames.Color3f, dark if unlit else rgb),
                ("diffuse_tint", Sdf.ValueTypeNames.Color3f, dark if unlit else Gf.Vec3f(1.0, 1.0, 1.0)),
                ("specular_level", Sdf.ValueTypeNames.Float, 0.0 if unlit else 0.5),
                ("reflection_roughness_constant", Sdf.ValueTypeNames.Float, ARROW_ROUGHNESS),
                ("metallic_constant", Sdf.ValueTypeNames.Float, 0.0),
                ("enable_emission", Sdf.ValueTypeNames.Bool, emission > 0),
                ("emissive_color", Sdf.ValueTypeNames.Color3f, rgb),
                ("emissive_intensity", Sdf.ValueTypeNames.Float, float(emission)),
                ("enable_opacity", Sdf.ValueTypeNames.Bool, opacity < 1.0),
                ("opacity_constant", Sdf.ValueTypeNames.Float, float(opacity))):
            mdl.CreateInput(key, kind).Set(value)
        material.CreateSurfaceOutput("mdl").ConnectToSource(mdl.ConnectableAPI(), "out")
        return material

    def show(self, marker_id: str, on: bool) -> None:
        """그 마커의 화살표나 평면을 보이거나 숨긴다 (prim visibility)"""
        try:
            from pxr import Usd, UsdGeom
            stage = self._stage()
            if stage is None:
                return
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for path in (f"{ARROW_SCOPE}/{marker_id}", f"{PLANE_SCOPE}/{marker_id}"):
                    prim = stage.GetPrimAtPath(path)
                    if prim.IsValid():
                        img = UsdGeom.Imageable(prim)
                        img.MakeVisible() if on else img.MakeInvisible()
        except Exception as e:
            self._fail("show / hide", e)

    def drop(self, marker_id: str) -> None:
        """그 마커의 화살표나 평면을 지운다"""
        self._remove(f"{ARROW_SCOPE}/{marker_id}")
        self._remove(f"{PLANE_SCOPE}/{marker_id}")
        self.authored.pop(marker_id, None)

    def clear(self) -> None:
        """화살표와 평면을 다 지운다"""
        self._remove(ARROW_SCOPE)
        self._remove(PLANE_SCOPE)

    def teardown(self) -> None:
        """뿌리째 치운다 (머티리얼까지)"""
        self._remove(MARKER_ROOT)

    def _remove(self, path: str) -> None:
        try:
            from pxr import Usd
            stage = self._stage()
            if stage is None or not stage.GetPrimAtPath(path).IsValid():
                return
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                stage.RemovePrim(path)
        except Exception as e:
            self._fail("remove", e)


def _move(sc, x: float, y: float, z: float = 0.0):
    return sc.Matrix44.get_translation_matrix(x, y, z)


class _MarkerView:
    """뷰포트 하나에 그린 마커 하나. 도형은 처음에 한 번만 짓고, 그 뒤로는 크기, 자리, 글, 색만 바꾼다.
    (끄는 도중에 도형을 지우면 제스처 매니저가 처리하던 도형을 잃는다)"""

    def __init__(self, view, marker, layout, picked: bool):
        """뼈대와 도형을 짓고 한 번 맞춘다"""
        sc, ui = view.sc, view.ui
        Click, Drag, Hover = _gesture_types()
        owner, mid = view.owner, marker["id"]
        self._view = view
        self.id = mid
        self.anchor = list(marker["position"])
        self.shown = True
        self.wanted = True
        self._desc = []
        white = ui.color(1.0, 1.0, 1.0, 1.0)
        with view.root:
            self.top = sc.Transform()
        with self.top:
            self._anchor_xf = sc.Transform()
            with self._anchor_xf:
                with sc.Transform(scale_to=sc.Space.SCREEN):
                    with sc.Transform(look_at=sc.Transform.LookAt.CAMERA):
                        self._dot = None
                        if marker["kind"] == "label":
                            self._dot = sc.Points([[0.0, 0.0, 0.0]], colors=[[1.0, 1.0, 1.0, 1.0]], sizes=[DOT_PX])
                        self._leader_xf = sc.Transform()
                        with self._leader_xf:
                            self._leader = sc.Line([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], color=white,
                                                   thickness=LEADER_PX)
                        self._panel = sc.Transform()
                        with self._panel:
                            self._bg_xf = sc.Transform()
                            with self._bg_xf:
                                self._bg = sc.Rectangle(1.0, 1.0, color=ui.color(*BOX_BG))
                                self._border = sc.Rectangle(1.0, 1.0, color=white, wireframe=True,
                                                            thickness=BORDER_PX)
                            self._title_xf = sc.Transform()
                            with self._title_xf:
                                self._title = sc.Label("", color=white, size=FONT_PX,
                                                       alignment=ui.Alignment.LEFT_CENTER)
                            for _ in range(MAX_PANEL_LINES):
                                xf = sc.Transform()
                                with xf:
                                    label = sc.Label("", color=ui.color(*DESC_COLOR), size=FONT_PX,
                                                     alignment=ui.Alignment.LEFT_CENTER)
                                self._desc.append((xf, label))
                            # 잡는 곳: 겹치지 않게 타일로 나눈 사각형 다섯 (몸통 + 변 띠 넷).
                            # 어느 사각형 위인가가 곧 판정이라, 커서와 동작이 늘 같다
                            self._zones = {}
                            for zone in ZONES:
                                self._zones[zone] = self._zone_rect(zone)
        self.restyle(marker, layout, picked)

    def _zone_rect(self, zone):
        """잡는 사각형 하나. zone None = 몸통 (클릭 고르기, 끌기 이동), 변 = 그 변 (커서, 끌기 크기).
        고르지 않은 패널에서는 변 띠도 몸통처럼 클릭으로 고르기만 한다"""
        sc, ui, view = self._view.sc, self._view.ui, self._view
        Click, Drag, Hover = _gesture_types()
        owner, mid = view.owner, self.id
        key = f"h:{mid}:{'body' if zone is None else f'{zone[0]}{zone[1]}'}"
        shape = None if zone is None else ("h" if zone[0] else "v")
        tag = f"{mid}/{ZONE_NAMES[zone]}"

        def take():
            view.hold(key)
            owner._plane_recheck(view)  # 패널이 평면 앞이다
            owner._hover_on(view, mid, zone)
            if shape and mid == owner.selected():
                _Cursor.hold(key, shape)

        def enter(_m):
            _hover_in(view, key, take)

        def leave():
            _OrbitWatch.forget((id(view), key))
            view.release(key)
            owner._plane_recheck(view)
            owner._hover_off(view, mid, zone)
            _Cursor.release(key)

        xf = sc.Transform()
        with xf:
            rect = sc.Rectangle(1.0, 1.0, color=ui.color(*HIT_COLOR), gesture=[
                Hover(enter, lambda _m: None, leave, name=tag),
                Click(lambda _m: owner._panel_clicked(mid, zone), name=tag),
                Drag(lambda m: owner._drag_began(view, mid, zone, m),
                     lambda m: owner._drag_changed(view, m, zone),
                     lambda: owner._drag_ended(view, zone), name=tag),
            ])
        return xf, rect

    def _place_zones(self, layout) -> None:
        """잡는 사각형 다섯을 상자에 맞춘다. 몸통은 상자 전체(이동), 변 띠는 테두리 바깥 EDGE_OUT_PX
        (크기 조절). 상자 안에는 바탕, 테두리, 글자가 겹쳐 있어 띠를 안으로 들이면 끌기가 안 먹는다.
        좌우 띠가 꼭짓점까지 덮고, 위아래 띠는 그 사이만. 서로 안 겹친다"""
        sc = self._view.sc
        w, h, i, o = layout["w"], layout["h"], EDGE_IN_PX, EDGE_OUT_PX
        inner_w, inner_h = max(w - 2.0 * i, 1.0), max(h - 2.0 * i, 1.0)
        places = {
            None: (w / 2.0, -h / 2.0, inner_w, inner_h),
            (-1, 0): ((i - o) / 2.0, -h / 2.0, i + o, h + 2.0 * o),
            (1, 0): (w + (o - i) / 2.0, -h / 2.0, i + o, h + 2.0 * o),
            (0, 1): (w / 2.0, (o - i) / 2.0, inner_w, i + o),
            (0, -1): (w / 2.0, -h - (o - i) / 2.0, inner_w, i + o),
        }
        for zone, (xf, rect) in self._zones.items():
            x, y, rw, rh = places[zone]
            rect.width, rect.height = rw, rh
            xf.transform = _move(sc, x, y)

    # -- 맞추기 (도형은 그대로, 속성만) ---------------------------------------------

    def restyle(self, marker, layout, picked: bool) -> None:
        """다 맞춘다. 고른 상태, 글, 색이 바뀌었을 때"""
        sc, ui = self._view.sc, self._view.ui
        self.anchor = list(marker["position"])
        self._anchor_xf.transform = _move(sc, *_panel_anchor(marker, self._view.owner.arrow_length()))
        color = self._color(marker, picked)
        ink = ui.color(*_rgba(color))
        if self._dot is not None:
            self._dot.colors = [_rgba(color)]
            self._dot.sizes = [DOT_PX * (PICKED_DOT_SCALE if picked else 1.0)]
        self._leader.color = ink
        self._leader.thickness = LEADER_PX * (PICKED_LINE_SCALE if picked else 1.0)
        self._border.color = ink
        self._border.thickness = BORDER_PICKED_PX if picked else BORDER_PX
        if marker["kind"] == "plane":
            self._title.color = ui.color(*PLANE_TITLE_COLOR)  # 평면 패널 글자는 고정 색 (고를 때도 그대로)
        else:
            self._title.color = ink if marker["label"] else ui.color(*DIM_COLOR)
        self.relayout(marker, layout, picked)

    def relayout(self, marker, layout, picked: bool) -> None:
        """상자 크기가 바뀌었을 때. 바탕, 글, 손잡이, 자리, 지시선"""
        sc = self._view.sc
        w, h, pad = layout["w"], layout["h"], layout["pad"]
        self._bg_xf.transform = _move(sc, w / 2.0, -h / 2.0, BOX_Z)
        for rect in (self._bg, self._border):
            rect.width, rect.height = w, h
        self._place_zones(layout)
        y = -pad
        self._title_xf.transform = _move(sc, pad, y - layout["th"] / 2.0)
        self._title.text = layout["title"]
        self._title.size = layout["fs"]
        y -= layout["th"]
        lines = layout["lines"]
        for i, (xf, label) in enumerate(self._desc):
            if i < len(lines):
                xf.transform = _move(sc, pad, y - layout["dh"] / 2.0)
                label.text = lines[i]
                label.size = layout["ds"]
                xf.visible = True
                y -= layout["dh"]
            elif xf.visible:
                xf.visible = False
        if not picked:
            _Cursor.release_prefix(f"h:{self.id}:")
        self.place(marker, layout, picked)

    def place(self, marker, layout, picked: bool) -> None:
        """상자만 옮겼을 때. 자리와 지시선"""
        sc = self._view.sc
        off = marker["offset"]
        self._panel.transform = _move(sc, off[0], off[1])
        end = leader_end(off, (layout["w"], layout["h"]))
        if end is None:
            self._leader_xf.visible = False
            return
        self._leader.end = [end[0], end[1], 0.0]
        self._leader_xf.visible = True

    def _color(self, marker, picked: bool):
        return _bright(marker["color"]) if picked else marker["color"]

    def show(self, on: bool) -> None:
        """카메라 뒤면 숨긴다. 보이기 값(wanted)이 꺼져 있으면 앞이어도 숨긴다"""
        if on != self.shown:
            self.shown = on
            self.top.visible = on and self.wanted

    def set_wanted(self, on: bool) -> None:
        """보이기 / 숨기기 (마커 값 x 전체 표시)"""
        self.wanted = bool(on)
        self.top.visible = self.shown and self.wanted

    def destroy(self) -> None:
        """그린 것과 걸어 둔 선택 잠금을 놓는다"""
        self._view.release_prefix(f"bg:{self.id}")
        self._view.release_prefix(f"h:{self.id}:")
        _Cursor.release_prefix(f"h:{self.id}:")
        self._zones = {}
        self._desc = []
        self.top.clear()
        self.top.visible = False


def _clip_frame(ui):
    """뷰포트 밖으로 나간 부분을 잘라내는 Frame. 안 자르면 분할 화면에서 옆 뷰포트 위에 그려진다"""
    try:
        return ui.Frame(horizontal_clipping=True, vertical_clipping=True)
    except TypeError:
        frame = ui.Frame()
        frame.horizontal_clipping = True
        frame.vertical_clipping = True
        return frame


class _View:
    """뷰포트 하나의 마커 층. 호스트가 주는 vph 의 ui_zstack 위에 SceneView 를 깔고 그 뷰포트 카메라에 잇는다
    (ui_zstack -> 자르는 Frame -> SceneView, viewport_api.add_scene_view). ZStack 이라 다른 기능 층 위에 겹쳐 쌓인다"""

    def __init__(self, owner, viewport_api, frame):
        """층을 짓고 주인에게 알린다"""
        import omni.ui as ui
        from omni.ui import scene as sc
        self.ui, self.sc = ui, sc
        self.owner = owner
        self.api = viewport_api
        self._orbit_id = getattr(viewport_api, "id", None)  # orbit scene dict 의 키 (뷰포트 id 그대로)
        self.frame = frame
        with frame:
            self._clip = _clip_frame(ui)
            with self._clip:
                self._scene_view = sc.SceneView()
        with self._scene_view.scene:
            self._plane_root = sc.Transform()
            self.root = sc.Transform()
            self._pick_root = sc.Transform()
        # 평면 조작: 화면 전체 하나 (sc.Screen 은 어떤 도형보다 뒤라 패널이 늘 먼저 받는다).
        # 어느 평면이 마우스 아래 보이는지는 owner 가 광선 + 깊이 쿼리로 정한다
        Click, Drag, Hover = _gesture_types()
        with self._plane_root:
            self._plane_screen = sc.Screen(gesture=[
                Hover(lambda p: owner._plane_over(self, p), lambda p: owner._plane_over(self, p),
                      lambda: owner._plane_out(self), name="plane_hover", raw=True),
                Drag(lambda p: owner._plane_grab(self, "screen", p), lambda p: owner._plane_move(self, p, "screen"),
                     lambda: owner._plane_release(self, "screen"), priority=PLANE_PRIORITY,
                     name="plane_move", raw=True, at_down=PLANE_PRESS_AT_DOWN),
                Click(lambda m: owner._plane_click(self, m), priority=PLANE_PRIORITY, name="plane_click"),
            ])
        try:
            viewport_api.add_scene_view(self._scene_view)
        except Exception as e:
            print(f"{TAG} add_scene_view failed (markers will not follow the camera): {type(e).__name__}: {e}")
        self._screen = None
        self.marks = {}
        self._holds = set()
        self._blocked = False
        self._token = None
        self._unblock = None
        self._orbit_back = None  # orbit 을 돌려줄 타이머
        self._visible = True
        self._view_sub = None
        api = self.api
        if api is not None and callable(getattr(api, "subscribe_to_view_change", None)):
            try:
                self._view_sub = api.subscribe_to_view_change(lambda *_: self.cull())
            except Exception as e:
                print(f"{TAG} view change subscription failed: {e}")
        owner._attach(self)

    def destroy(self) -> None:
        """뷰포트 연결을 풀 때. 층을 걷고 카메라 연결도 끊는다"""
        self.arm(False)
        self.drop_all()
        self._holds = set()
        self._sync_block(now=True)
        sub, self._view_sub = self._view_sub, None
        if sub is not None and callable(getattr(sub, "destroy", None)):
            try:
                sub.destroy()
            except Exception:
                pass
        self.owner._detach(self)
        if self._pick_root is not None:
            self._pick_root.clear()
        if self.root is not None:
            self.root.clear()
        scene_view, self._scene_view = self._scene_view, None
        if scene_view is not None:
            try:
                self.api.remove_scene_view(scene_view)
            except Exception:
                pass
            try:
                scene_view.destroy()
            except Exception:
                pass
        clip, self._clip = self._clip, None
        if clip is not None:
            try:
                clip.destroy()
            except Exception:
                pass
        if getattr(self, "_plane_root", None) is not None:
            self._plane_root.clear()
        self._plane_root = self._plane_screen = None
        self._pick_root = None
        self.root = None
        self.api = None
        self.frame = None

    # -- Kit 선택 잠금 ------------------------------------------------------------

    def hold(self, key: str) -> None:
        """패널 위에 있거나 끄는 동안 Kit 클릭 선택을 끈다"""
        self._holds.add(key)
        self._sync_block()

    def release(self, key: str) -> None:
        self._holds.discard(key)
        self._sync_block()

    def release_prefix(self, prefix: str) -> None:
        self._holds = {k for k in self._holds if not k.startswith(prefix)}
        self._sync_block()

    def _sync_block(self, now: bool = False) -> None:
        """쥔 것이 있으면 Kit 선택을 끄고, 다 놓으면 SELECT_RELEASE_S 뒤에 되살린다.
        바로 되살리지 않는 것은: 누르는 순간 hover 끝이 drag 시작보다 먼저 오면 잠금이 한순간 비는데,
        그때 껐다 켜면 Kit 이 선택 레이어를 다시 지으면서 막 시작한 끌기가 끊긴다"""
        if self._holds:
            if self._unblock is not None:
                self._unblock.cancel()
                self._unblock = None
            if self._orbit_back is not None:
                self._orbit_back.cancel()
                self._orbit_back = None
            _OrbitYield.hold(self._orbit_id)  # 그 뷰포트 orbit 을 끈다 (누르기 전에). 이미 껐으면 그대로
            if not self._blocked:
                self._blocked = True
                self._token = _block_selection(self.api)
            return
        # orbit 은 거의 바로 (ORBIT_RELEASE_S) 돌려준다. 빠져나오자마자 카메라를 돌릴 수 있게.
        # 0 이 아닌 것은 누르는 순간 hover 끝 -> drag 시작 사이 빈틈에 orbit 이 누름을 가져가지 않게
        if now:
            if self._orbit_back is not None:
                self._orbit_back.cancel()
                self._orbit_back = None
            _OrbitYield.release(self._orbit_id)
        elif self._orbit_back is None and _OrbitYield.holding(self._orbit_id):
            self._orbit_back = asyncio.get_event_loop().call_later(ORBIT_RELEASE_S, self._orbit_back_now)
        if not self._blocked:
            return
        if now:
            if self._unblock is not None:
                self._unblock.cancel()
                self._unblock = None
            self._unblock_now()
        elif self._unblock is None:
            self._unblock = asyncio.get_event_loop().call_later(SELECT_RELEASE_S, self._unblock_now)

    def _orbit_back_now(self) -> None:
        self._orbit_back = None
        if not self._holds:
            _OrbitYield.release(self._orbit_id)

    def _unblock_now(self) -> None:
        self._unblock = None
        if not self._holds and self._blocked:
            self._blocked = False
            self._token = None

    # -- 찍기 대기 ---------------------------------------------------------------

    @property
    def armed(self) -> bool:
        return self._screen is not None

    def arm(self, on: bool) -> None:
        """다음 왼쪽 클릭 한 번을 화면 전체에서 받는다"""
        if on == self.armed or self._pick_root is None:
            return
        if not on:
            self._screen = None
            self._pick_root.clear()
            self.release("arm")
            return
        Click, _drag, _hover = _gesture_types()
        with self._pick_root:
            self._screen = self.sc.Screen(gesture=[
                Click(lambda m: _later(lambda: self.owner._view_clicked(self, m)),
                      priority=ARM_PRIORITY, name="arm_click")])
        self.hold("arm")

    # -- 마커 --------------------------------------------------------------------

    def add(self, marker, layout, picked: bool) -> None:
        if self.root is None:
            return
        self.drop(marker["id"])
        mv = _MarkerView(self, marker, layout, picked)
        self.marks[marker["id"]] = mv
        self._cull_one(mv, *self._camera())

    def set_wanted(self, marker_id: str, on: bool) -> None:
        mv = self.marks.get(marker_id)
        if mv is not None:
            mv.set_wanted(on)

    def drop(self, marker_id: str) -> None:
        mv = self.marks.pop(marker_id, None)
        if mv is not None:
            mv.destroy()

    def drop_all(self) -> None:
        for mv in list(self.marks.values()):
            mv.destroy()
        self.marks = {}
        if self.root is not None:
            self.root.clear()

    def restyle(self, marker, layout, picked: bool) -> None:
        mv = self.marks.get(marker["id"])
        if mv is not None:
            mv.restyle(marker, layout, picked)

    def relayout(self, marker, layout, picked: bool) -> None:
        mv = self.marks.get(marker["id"])
        if mv is not None:
            mv.relayout(marker, layout, picked)

    def place(self, marker, layout, picked: bool) -> None:
        mv = self.marks.get(marker["id"])
        if mv is not None:
            mv.place(marker, layout, picked)

    # -- 카메라 뒤 숨기기 ----------------------------------------------------------

    def _camera(self):
        """(view 행렬, 원근인가). 못 물으면 (None, False)"""
        try:
            return self.api.view, abs(self.api.projection[3][3]) < 1e-9
        except Exception:
            return None, False

    def _cull_one(self, mv, view, perspective: bool) -> None:
        if view is None or not perspective:
            mv.show(True)
            return
        try:
            from pxr import Gf
            z = view.Transform(Gf.Vec3d(*mv.anchor))[2]
        except Exception:
            mv.show(True)
            return
        mv.show(z < 0.0)  # 카메라는 -Z 를 본다

    def cull(self) -> None:
        """카메라가 움직일 때마다 앵커가 뒤로 간 마커를 숨긴다"""
        if self.api is None or not self.marks:
            return
        view, perspective = self._camera()
        for mv in self.marks.values():
            self._cull_one(mv, view, perspective)



class Annotation:
    """마커의 속. 목록, 선택, 찍어서 만들기, 패널 끌기, 그리기를 쥔다"""

    def __init__(self):
        """목록과 상태 자리만 비워 둔다. 뷰포트에 거는 것은 start"""
        self._markers: list = []
        self._selected = None
        self._creating: str = ""
        self._relocating = None  # 위치 재설정을 기다리는 마커 id
        self._plane_drag = None
        self._over = {}       # view -> 마우스가 그 뷰포트 위인가
        self._plane_dbg = {}  # view -> 마지막 평면 판정 (진단용)
        self._press_ndc = {}  # view -> 누른 자리 NDC (클릭 판정)
        self._gizmo_mode = "off"      # Kit 기즈모: move / rotate / off
        self._gizmo_target = None     # 기즈모가 붙은 마커
        self._gizmo_pending = False
        self._body_notice = None
        self._body_notice_stage = None
        self._edit_was = None         # (스테이지, 원래 편집 대상)
        self._op_was = None           # 원래 Kit 변환 도구
        self._gizmo_warned = False
        self._panel_dbg = None  # 마지막 패널 끌기 시작 (진단용)
        self._ray = {}        # view -> (광선 시작, 방향, 마우스 NDC). 마지막 hover
        self._hot = {}        # view -> 마우스 아래 정말 보이는 평면 id (깊이까지 확인)
        self._depth = {}      # view -> 묻는 중인 깊이 쿼리
        self._status: str = ""
        self._views: list = []
        self._listeners: list = []
        self._queries: set = set()
        self._drag = None
        self._hover_edge = None  # 마우스가 올라간 변 띠 (view, marker id, edge): 커서가 크기 모양인 곳
        self._clicked = None  # 마지막으로 클릭한 패널
        self._arrow_scale = 1.0  # 화살표 크기 배수 (모든 화살표 공통)
        self._all_visible = True  # 전체 표시. 마커는 자기 visible 과 이것이 둘 다 켜져야 보인다
        self._target_usd = ""  # 마커가 붙는 형상 USD 경로. 마커 파일은 그 옆 <이름>_marker.json
        self._bindings = {}  # 뷰포트 id -> {"prim": 스테이지 프림 경로, "usd": 원본 USD 경로}. 없으면 지금처럼 다 보인다
        self._next_ids = {}  # 종류별 다음 번호. 늘기만 하고 지운 번호는 다시 안 쓴다 (id 는 웹 파라미터와 1:1)
        self._closed = False
        self._arrows = _Arrows()

    # -- 시작과 정리 --------------------------------------------------------------

    def start(self) -> bool:
        """시작. 그리는 자리는 bind_viewport(vph, ...) 로 받은 뷰포트에만 깐다"""
        self._closed = False
        _OrbitWatch.start()
        return True

    def teardown(self) -> None:
        """뷰포트에서 떼고 들고 있던 것을 놓는다"""
        self._closed = True
        self._creating = ""
        for q in list(self._queries):
            q.cancel()
        self._queries = set()
        self._drag = None
        for view in list(self._views):
            view.destroy()
        self._views = []
        _OrbitYield.release_all()
        _OrbitWatch.stop()
        self._gizmo_mode = "off"
        self._gizmo_attach(None)
        self._unwatch_bodies()
        self._arrows.teardown()
        self._listeners = []
        self._markers = []
        self._selected = None

    def _attach(self, view) -> None:
        """새 뷰포트. 있는 마커를 다 그린다"""
        self._views.append(view)
        for m in self._markers:
            view.add(m, self._layout(m), m["id"] == self._selected)
            view.set_wanted(m["id"], self._shown(m) and self._shown_in(view, m))
        if self._creating:
            view.arm(True)

    def _detach(self, view) -> None:
        self._forget_planes(view)
        if view in self._views:
            self._views.remove(view)
        if self._drag is not None and self._drag["view"] is view:
            self._drag = None

    def _each(self, fn) -> None:
        """뷰포트마다. 하나가 실패해도 나머지는 한다"""
        for view in list(self._views):
            try:
                fn(view)
            except Exception as e:
                print(f"{TAG} viewport update failed: {type(e).__name__}: {e}")

    # -- 알림 --------------------------------------------------------------------

    def add_listener(self, fn) -> None:
        """바뀔 때마다 fn(reason, marker_id). marker_id 는 바뀐 마커 (전체에 걸친 변경이면 None).
        reason: add, remove, select(새로 고른 id), click(패널 클릭), text, font, panel, plane, move, visible,
        arrow(전체), target, bind(뷰포트 연결), save, load(전체), create, status"""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, reason: str, marker_id=None) -> None:
        for fn in list(self._listeners):
            try:
                fn(reason, marker_id)
            except Exception as e:
                print(f"{TAG} listener failed: {type(e).__name__}: {e}")

    def _say(self, text: str, reason: str = "status", marker_id=None) -> None:
        self._status = text
        self._notify(reason, marker_id)

    # -- 질의 --------------------------------------------------------------------

    def markers(self) -> list:
        """모든 마커 (사본)"""
        return copy.deepcopy(self._markers)

    def get(self, marker_id):
        """마커 하나 (사본). 없으면 None"""
        m = self._find(marker_id)
        return copy.deepcopy(m) if m is not None else None

    def selected(self):
        """고른 마커 id. 없으면 None"""
        return self._selected

    def clicked(self):
        """마지막으로 클릭한 패널의 마커 id. 없으면 None"""
        return self._clicked

    def creating(self) -> str:
        """찍기를 기다리는 종류. 아니면 빈 칸"""
        return self._creating

    def status(self) -> str:
        """마지막 상태 글"""
        return self._status

    def arrow_length(self) -> float:
        """모든 화살표가 쓰는 길이 (스테이지 단위). ARROW_LENGTH_M x 화살표 배수를 장면의 metersPerUnit 으로 환산"""
        try:
            import omni.usd
            from pxr import UsdGeom
            stage = omni.usd.get_context().get_stage()
            mpu = float(UsdGeom.GetStageMetersPerUnit(stage)) if stage is not None else 1.0
        except Exception:
            mpu = 1.0
        metres = ARROW_LENGTH_M * self._arrow_scale
        return metres / mpu if mpu > 0 else metres

    def arrow_scale(self) -> float:
        """화살표 크기 배수 (모든 inlet, outlet 공통)"""
        return self._arrow_scale

    def set_arrow_scale(self, scale) -> bool:
        """화살표 크기 배수. 모든 inlet, outlet 이 함께 바뀌어 늘 같은 크기다"""
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(float(scale)):
            self._say("화살표 배수는 숫자여야 합니다")
            return False
        if not MIN_ARROW_SCALE <= float(scale) <= MAX_ARROW_SCALE:
            self._say(f"화살표 배수는 {MIN_ARROW_SCALE:g} ~ {MAX_ARROW_SCALE:g} 입니다")
            return False
        self._arrow_scale = float(scale)
        for m in self._markers:  # 화살표와, 화살표 끝에 붙은 패널 자리
            if m["kind"] in ARROW_KINDS:
                self._restyle(m["id"])
        self._notify("arrow")
        return True

    def _new_id(self, kind: str, taken=None) -> str:
        """새 id '<kind>_<n>'. 번호는 늘기만 한다: 지운 마커의 id 를 다른 마커가 물려받지 않게
        (웹에서 id 로 파라미터와 1:1 로 묶는다)"""
        taken = set(taken) if taken is not None else {m["id"] for m in self._markers}
        n = max(self._next_ids.get(kind, 1), 1)
        while f"{kind}_{n}" in taken:
            n += 1
        self._next_ids[kind] = n + 1
        return f"{kind}_{n}"

    def _bump_id(self, marker_id: str, kind: str) -> None:
        """이미 있는 id 의 번호보다 다음 번호가 작지 않게"""
        n = id_number(marker_id, kind)
        if n is not None:
            self._next_ids[kind] = max(self._next_ids.get(kind, 1), n + 1)

    def _find(self, marker_id):
        for m in self._markers:
            if m["id"] == marker_id:
                return m
        return None

    def _layout(self, marker) -> dict:
        font = marker.get("font_px") or FONT_PX
        return panel_layout(_title_of(marker), marker["description"], marker.get("box"),
                            TEXT_SCALE * font / FONT_PX)

    # -- 만들기 ------------------------------------------------------------------

    def create(self, kind: str) -> bool:
        """그 종류를 만들 준비. 다음 클릭한 모델 표면에 생긴다"""
        if kind not in KINDS:
            self._say(f"모르는 종류: {kind!r}")
            return False
        if not self._views:
            self._say("그릴 뷰포트가 없습니다")
            return False
        for q in list(self._queries):  # 묻던 것이 있으면 버리고 새로 기다린다
            q.cancel()
        self._queries = set()
        self._creating = kind
        self._each(lambda v: v.arm(True))
        self._say(f"{kind}: 뷰포트에서 모델을 왼쪽 클릭하세요", "create")
        return True

    def relocate(self, marker_id) -> bool:
        """그 label / inlet / outlet 을 다음 클릭한 모델 표면으로 옮길 준비. 빈 곳을 찍으면 아무것도 안 바꾸고 취소"""
        m = self._find(marker_id)
        if m is None:
            self._say("옮길 마커를 고르세요")
            return False
        if m["kind"] not in KINDS:
            self._say("평면은 끌어서 옮깁니다 (고른 뒤 평면을 끌기)")
            return False
        if not self._views:
            self._say("그릴 뷰포트가 없습니다")
            return False
        for q in list(self._queries):
            q.cancel()
        self._queries = set()
        self._creating = ""
        self._relocating = marker_id
        self._each(lambda v: v.arm(True))
        self._say(f"{marker_id}: 새 위치를 왼쪽 클릭하세요", "create")
        return True

    def relocating(self):
        """위치 재설정을 기다리는 마커 id. 아니면 None"""
        return self._relocating

    def cancel_create(self) -> None:
        """찍기 대기(만들기, 위치 재설정)를 거둔다. 묻는 중이던 답도 버린다"""
        if not self._creating and not self._relocating:
            return
        self._creating = ""
        self._relocating = None
        for q in list(self._queries):
            q.cancel()
        self._queries = set()
        self._each(lambda v: v.arm(False))
        self._say("찍기를 취소했습니다", "create")

    def create_plane(self, axis: str) -> bool:
        """축 평면 하나를 바로 만든다 (xy / yz / zx). 가운데는 타겟 프림 바운드 중점 (연결된 뷰포트의 프림,
        못 구하면 원점). 위치 0, 크기 PLANE_SIZE"""
        if axis not in PLANE_AXES:
            self._say(f"모르는 평면: {axis!r} (xy, yz, zx)")
            return False
        origin, dims, prim_path = self._plane_origin()
        size = list(PLANE_SIZE)
        if dims is not None:  # 그 평면의 두 축 바운드 길이 x PLANE_BOUND_SCALE (0 인 축은 기본 크기)
            _n, u, v = PLANE_AXES[axis]
            size = [dims[k] * PLANE_BOUND_SCALE if dims[k] > 0 else PLANE_SIZE[i] for i, k in enumerate((u, v))]
        mid = self._new_id("plane")
        marker = {
            "id": mid,
            "kind": "plane",
            "axis": axis,
            "at": 0.0,
            "size": size,
            "label": f"{axis}_plane",
            "description": "",
            "color": list(PLANE_COLORS[axis]),
            "offset": list(DEFAULT_OFFSET),
            "box": None,
            "font_px": FONT_PX,
            "shift": [0.0, 0.0],
            "origin": origin,
            "visible": True,
            "target": self._target_usd,
        }
        marker["position"] = _plane_of(marker)["center"]
        self._markers.append(marker)
        self._arrows.draw_plane(marker, False)
        layout = self._layout(marker)
        self._each(lambda v: v.add(marker, layout, False))
        self._apply_visible(marker)
        self._notify("add", marker["id"])
        self.select(marker["id"])
        where = f"{prim_path} 바운드 중점" if prim_path else "원점 (타겟 프림 바운드를 못 구함)"
        self._say(f"만들었습니다: {marker['id']} ({axis.upper()} 평면, 가운데 {where})", "status", marker["id"])
        return True

    def _plane_prim(self):
        """새 평면의 기준 프림: 지금 타겟 USD 로 연결된 뷰포트의 프림. 없고 연결이 하나뿐이면 그 프림"""
        binds = list(self._bindings.values())
        hit = next((b["prim"] for b in binds if b["usd"] == self._target_usd), None)
        if hit is None and len(binds) == 1:
            hit = binds[0]["prim"]
        return hit or ""

    def _plane_origin(self):
        """새 평면의 (가운데, 바운드 크기 [x, y, z], 프림 경로). 못 구하면 ([0, 0, 0], None, "")"""
        prim_path = self._plane_prim()
        try:
            import omni.usd
            stage = omni.usd.get_context().get_stage()
        except Exception:
            stage = None
        got = prim_bounds(stage, prim_path) if prim_path else None
        return (got[0], got[1], prim_path) if got is not None else ([0.0, 0.0, 0.0], None, "")

    def _plane(self, marker_id):
        m = self._find(marker_id)
        if m is None or m["kind"] != "plane":
            self._say("고른 것이 평면이 아닙니다")
            return None
        return m

    def set_plane_at(self, marker_id, at) -> bool:
        """평면 위치. 기준점 (만들 때의 타겟 프림 바운드 중점) 에서 법선 축 방향으로 at 만큼 (xy 면 z, yz 면 x, zx 면 y)"""
        m = self._plane(marker_id)
        if m is None:
            return False
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(float(at)):
            self._say("위치는 숫자여야 합니다")
            return False
        m["at"] = float(at)
        m["position"] = _plane_of(m)["center"]
        self._restyle(marker_id)
        self._notify("plane", marker_id)
        return True

    def set_plane_size(self, marker_id, w, h) -> bool:
        """평면 크기 [가로, 세로] (스테이지 단위, 0 보다 크게)"""
        m = self._plane(marker_id)
        if m is None:
            return False
        for v in (w, h):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or v <= 0:
                self._say("크기는 0 보다 큰 숫자여야 합니다")
                return False
        m["size"] = [float(w), float(h)]
        self._restyle(marker_id)
        self._notify("plane", marker_id)
        return True

    def _view_clicked(self, view, ndc) -> None:
        """찍기 대기 중 클릭. 이미지 밖이면 계속 기다린다"""
        kind, moving = self._creating, self._relocating
        if not (kind or moving) or self._closed or view.api is None:
            return
        xy = _xy(ndc)
        pixel = _texture_pixel(view.api, xy) if xy is not None else None
        if pixel is None:
            return
        self._each(lambda v: v.arm(False))
        self._say("위치 확인 중…", "create")
        api = view.api
        if moving:
            q = _Query(api, pixel, lambda query, result: self._relocated(query, api, moving, result))
        else:
            q = _Query(api, pixel, lambda query, result: self._picked(query, api, kind, result))
        if not q.over:
            self._queries.add(q)

    def _picked(self, query, api, kind: str, result) -> None:
        """찍은 답. 맞으면 만들고 고른다, 빈 곳이면 다시 기다린다"""
        self._queries.discard(query)
        if self._closed or self._creating != kind:
            return
        if isinstance(result, Exception):
            self._creating = ""
            self._say(f"위치 찍기 실패: {result}", "create")
            return
        if result is None:
            self._each(lambda v: v.arm(True))
            self._say("빈 곳(또는 화살표 위)입니다. 모델 위를 클릭하세요", "create")
            return
        self._creating = ""
        marker = self._new_marker(kind, result, api)
        self._markers.append(marker)
        layout = self._layout(marker)
        if kind in ARROW_KINDS:
            self._arrows.draw(marker, False, self.arrow_length())
        self._each(lambda v: v.add(marker, layout, False))
        self._apply_visible(marker)
        self._notify("add", marker["id"])
        self.select(marker["id"])
        self._say(f"만들었습니다: {marker['id']}", "create", marker["id"])

    def _relocated(self, query, api, marker_id: str, result) -> None:
        """위치 재설정 답. 맞으면 옮긴다 (inlet / outlet 은 방향도 새 표면으로), 아니면 그대로 두고 취소"""
        self._queries.discard(query)
        if self._closed or self._relocating != marker_id:
            return
        self._relocating = None
        m = self._find(marker_id)
        if m is None:
            return
        if isinstance(result, Exception) or result is None:
            self._say("그 자리에는 놓을 수 없어 위치 재설정을 취소했습니다", "create")
            return
        pos = [float(c) for c in result["position"]]
        m["position"] = pos
        if m["kind"] in ARROW_KINDS:
            m["direction"] = self._arrow_direction(m["kind"], result, api, pos)
        self._restyle(marker_id)
        self._each(lambda v: v.cull())
        self._notify("move", marker_id)
        self._say(f"옮겼습니다: {marker_id}", "create", marker_id)

    def _arrow_direction(self, kind: str, hit: dict, api, pos) -> list:
        """inlet 은 -법선, outlet 은 +법선. 법선을 못 얻으면 카메라 쪽"""
        toward, _distance = _camera_of(api, pos)
        n = hit.get("normal") or _unit(toward) or [0.0, 0.0, 1.0]
        sign = -1.0 if kind == "inlet" else 1.0
        return [sign * float(c) for c in n]

    # -- 평면 끌기: 마우스 광선이 평면과 만나는 점을 따라, 평면 안에서만 ------------------------

    def _plane_grab(self, view, by: str, payload) -> None:
        """평면 사각형 누름. 어느 사각형이 보냈든 마우스 아래 정말 보이는 평면 (hot) 을 쓴다 (겹친 자리).
        고른 평면이면 끌기, 아니면 고르기만 (다음 누름부터 끌린다)"""
        try:
            self._press_ndc[view] = _xy(payload.mouse)  # 클릭 판정용 누른 자리
        except Exception:
            self._press_ndc.pop(view, None)
        if self._panel_under(view):
            return  # 패널 위: 패널이 맡는다
        marker_id = self._hot.get(view)
        m = self._find(marker_id) if marker_id else None
        dbg = self._plane_dbg.setdefault(view, {"moves": 0})
        dbg["drag_began"] = dbg.get("drag_began", 0) + 1
        mine = _OrbitYield.holding(view._orbit_id)  # orbit 을 우리가 꺼 뒀으면 orbit 끌기일 수 없다
        if m is not None and m["kind"] in ARROW_KINDS and not self._creating:
            if self._selected != marker_id:  # 화살표: 누르면 고르기만 (끌기는 없다)
                self._kit_take(marker_id)
                self.select(marker_id)
            return
        if (m is None or m["kind"] != "plane" or self._creating or (_OrbitWatch.busy() and not mine)
                or self._plane_drag is not None):
            dbg["drag_skip"] = ("no plane" if m is None else "creating" if self._creating
                                else "dragging" if self._plane_drag is not None else "orbit busy")
            return  # 가려진 자리 (orbit 이 받는다), 패널 앞, 찍기 대기, 이미 끄는 중
        if self._selected != marker_id:
            self._kit_take(marker_id)
            self.select(marker_id)
            return
        if self._gizmo_target == marker_id:
            return  # Kit 기즈모가 붙어 있다: 평면 끌기는 기즈모가 맡는다
        start = self._plane_point(m, payload, view)  # None 이면 제스처 이동량(moved)으로 따라간다
        dbg.update(drag_skip=None, drag_start=start, drag_moves=0)
        self._plane_drag = {"view": view, "id": marker_id, "by": by, "start": start, "moved": [0.0, 0.0, 0.0],
                            "shift": list(m.get("shift", [0.0, 0.0])), "at0": m["at"]}
        view.hold("plane_drag")
        self._paint_plane(marker_id)

    def _plane_move(self, view, payload, by=None) -> None:
        d = self._plane_drag
        if d is None or d["view"] is not view or (by is not None and d["by"] != by):
            return
        m = self._find(d["id"])
        if m is None:
            self._plane_release(view)
            return
        n, u, v = PLANE_AXES[m["axis"]]
        dbg = self._plane_dbg.setdefault(view, {"moves": 0})
        dbg["drag_moves"] = dbg.get("drag_moves", 0) + 1
        now = self._plane_point(m, payload, view) if d["start"] is not None else None
        if now is not None:
            delta = _sub(now, d["start"])
        else:
            try:  # 광선을 못 받으면 제스처가 주는 월드 이동량을 쌓는다 (평면 안 성분만)
                step = [float(c) for c in payload.moved]
            except Exception:
                return
            d["moved"] = [d["moved"][i] + step[i] for i in range(3)]
            nrm = _plane_of(m)["normal"]
            delta = _sub(d["moved"], _mul(nrm, _dot(d["moved"], nrm)))
        # 월드 이동을 기준 축 성분으로 (돌린 평면이면 법선 축 성분도 생긴다)
        m["shift"] = [d["shift"][0] + delta[u], d["shift"][1] + delta[v]]
        m["at"] = d["at0"] + delta[n]
        m["position"] = _plane_of(m)["center"]
        self._restyle(d["id"])

    def _plane_release(self, view, by=None) -> None:
        d = self._plane_drag
        if d is None or (by is not None and d["by"] != by):
            return  # 다른 사각형 제스처의 끝 (취소) 은 이 끌기와 상관없다
        self._plane_drag = None
        if d["view"] in self._views:
            d["view"].release("plane_drag")
        self._each(lambda v: v.cull())
        self._paint_plane(d["id"])
        if d["view"] in self._views:
            self._probe(d["view"])  # 뗀 자리가 아직 그 평면 위인가
        self._notify("plane", d["id"])

    # -- 평면 hover / 누름: 판정 사각형 위 + 카메라 기준 깊이로 정말 보이는 평면만 ------------------
    # 보이는 평면 위면 orbit 을 끈다 (클릭이 orbit 으로 안 감). 다른 오브젝트에 가려진 자리는 orbit 에 둔다

    def _plane_state(self, marker_id) -> str:
        """press > hover > selected > idle"""
        if self._plane_drag is not None and self._plane_drag["id"] == marker_id:
            return "press"
        if marker_id in self._hot.values():
            return "hover"
        return "selected" if marker_id == self._selected else "idle"

    def _paint_plane(self, marker_id) -> None:
        m = self._find(marker_id)
        if m is not None and m["kind"] == "plane":
            self._arrows.plane_look(m, self._plane_state(marker_id))

    def _plane_over(self, view, payload) -> None:
        """마우스가 그 뷰포트 위 (들어옴 / 움직임)"""
        self._over[view] = True
        dbg = self._plane_dbg.setdefault(view, {"moves": 0})
        dbg["moves"] += 1
        ndc = None
        try:
            ndc = _xy(payload.mouse)
        except Exception:
            pass
        ray, source = None, "none"
        try:
            ray = ([float(c) for c in payload.ray_origin], [float(c) for c in payload.ray_direction], ndc)
            source = "payload"
        except Exception:
            made = _ndc_ray(view.api, ndc) if ndc is not None else None
            if made is not None:
                ray, source = (made[0], made[1], ndc), "camera"
        dbg.update(ray=source, ndc=ndc)
        self._ray[view] = ray
        self._probe(view)

    def _plane_out(self, view) -> None:
        self._over.pop(view, None)
        self._probe(view)

    def _plane_click(self, view, ndc=None) -> None:
        """패널 밖 클릭 (뗄 때). 마우스 아래 보이는 평면이면 고르고, 마커 아닌 오브젝트나 빈 곳이면 고른 것을 푼다.
        패널 위 클릭은 패널이 맡는다. 누른 자리에서 움직였으면 (카메라 끌기 등) 클릭이 아니다"""
        if self._creating or self._closed or self._panel_under(view):
            return
        pressed = self._press_ndc.pop(view, None)
        at = _xy(ndc)
        if pressed is not None and at is not None and max(abs(at[0] - pressed[0]), abs(at[1] - pressed[1])) > CLICK_SLOP_NDC:
            return
        hot = self._hot.get(view)
        if hot is not None:
            self._kit_take(hot)
            if self._selected != hot:
                self.select(hot)
        elif self._selected is not None:
            self.select(None)

    def _plane_recheck(self, view) -> None:
        if self._over.get(view):
            self._probe(view)

    def _plane_candidate(self, view, ray):
        """광선이 먼저 닿는 보이는 마커 몸체 (id, 거리): 평면 사각형, inlet / outlet 화살표. 없으면 None"""
        o, d, _ndc = ray
        best = None
        length = self.arrow_length()
        for m in self._markers:
            if not self._plane_seen_in(view, m["id"]):
                continue
            if m["kind"] == "plane":
                dist = ray_in_plane(m, o, d)
            else:
                tail, tip = _arrow_ends(m, length)
                dist = ray_near_segment(o, d, tail, tip, ARROW_RADIUS * length * ARROW_HIT_SCALE)
            if dist is not None and (best is None or dist < best[1]):
                best = (m["id"], dist)
        return best

    def _plane_seen_in(self, view, marker_id) -> bool:
        """USD 몸체가 있고 지금 보이는 마커 (평면, 화살표)"""
        m = self._find(marker_id)
        return (m is not None and (m["kind"] == "plane" or m["kind"] in ARROW_KINDS)
                and self._shown(m) and self._shown_in(view, m))

    def _panel_under(self, view) -> bool:
        """그 뷰포트에서 마우스가 패널 위인가 (패널이 평면보다 앞)"""
        return any(k.startswith("h:") for k in view._holds)

    def _probe(self, view) -> None:
        """마우스 아래 보이는 평면을 다시 정한다. 판정 사각형 후보 중 광선이 먼저 닿는 평면을 고르고,
        그 자리 모델 표면이 그보다 가까우면 (가려짐) 없음"""
        if view not in self._views:
            return
        over = self._over.get(view)
        if not over or self._creating or self._closed or self._panel_under(view):
            self._set_hot(view, None)
            return
        if self._plane_drag is not None:
            return  # 끄는 동안은 그대로
        if _OrbitWatch.busy():
            self._set_hot(view, None)
            _OrbitWatch.later((id(view), "plane_probe"), lambda: self._probe(view))
            return
        ray = self._ray.get(view)
        if ray is None or ray[2] is None:
            self._set_hot(view, None)  # 광선을 못 받으면 평면을 못 고른다 (orbit 에 둔다)
            return
        best = self._plane_candidate(view, ray)
        self._plane_dbg.setdefault(view, {"moves": 0}).update(candidate=best)
        if best is None:
            self._set_hot(view, None)
            return
        asking = self._depth.get(view)
        if asking is not None:
            asking["dirty"] = True  # 답이 오면 다시
            return
        self._ask_depth(view, best[0], best[1], ray)

    def _ask_depth(self, view, marker_id: str, dist: float, ray) -> None:
        """마우스 픽셀의 모델 표면을 묻는다 (평면, 화살표는 pick 에서 빠져 있어 그 뒤 모델이 온다)"""
        pixel = _texture_pixel(view.api, ray[2]) if view.api is not None else None
        if pixel is None:
            self._set_hot(view, None)
            return
        loop = asyncio.get_event_loop()
        asking = {"id": marker_id, "dist": dist, "origin": ray[0], "dirty": False, "timer": None}
        self._plane_dbg.setdefault(view, {"moves": 0}).update(pixel=pixel)
        self._depth[view] = asking

        def on_query(*args):
            try:
                loop.call_soon_threadsafe(self._depth_answer, view, asking, args)
            except RuntimeError:
                pass

        _Query._seq += 1
        try:
            try:
                ok = view.api.request_query(pixel, on_query, query_name=f"{NAME}.depth.{_Query._seq}")
            except TypeError:
                ok = view.api.request_query(pixel, on_query)
        except Exception:
            ok = False
        if self._depth.get(view) is not asking:
            return  # 그 자리에서 답이 왔다
        if ok is False:
            self._depth.pop(view, None)
            self._set_hot(view, marker_id)  # 못 물으면 보이는 것으로 (평면 조작이 아예 막히지 않게)
            return
        asking["timer"] = loop.call_later(PICK_TIMEOUT_S, lambda: self._depth_answer(view, asking, None))

    def _depth_answer(self, view, asking, args) -> None:
        if self._depth.get(view) is not asking:
            return
        self._depth.pop(view, None)
        if asking["timer"] is not None:
            asking["timer"].cancel()
        dbg = self._plane_dbg.setdefault(view, {"moves": 0})
        if view not in self._views or self._panel_under(view) or not self._over.get(view):
            self._set_hot(view, None)  # 묻는 사이 패널 위로 갔거나 뷰포트를 나갔다: 늦은 답은 버린다
            if asking["dirty"]:
                self._probe(view)
            return
        if args is None:
            dbg.update(query="timeout")
            self._set_hot(view, None)
        else:
            dbg.update(query=str(args[0]) if args else "", query_at=list(args[1]) if len(args) > 1 else None)
            front = _plane_named(args)
            if front is not None:
                # 렌더 쿼리가 평면을 집었다 (pickable 을 안 따르는 경우): 그 평면이 맨 앞이다
                self._set_hot(view, front if self._plane_seen_in(view, front) else None)
            else:
                hit = _hit(args)
                if hit is None:
                    seen = True  # 뒤에 모델 없음
                else:
                    gap = _sub(hit[1], asking["origin"])
                    dbg.update(model_dist=round(math.sqrt(_dot(gap, gap)), 4), plane_dist=round(asking["dist"], 4))
                    seen = math.sqrt(_dot(gap, gap)) >= asking["dist"] * (1.0 - PLANE_DEPTH_EPS)
                self._set_hot(view, asking["id"] if seen else None)
        if asking["dirty"]:
            self._probe(view)

    def _set_hot(self, view, marker_id) -> None:
        """마우스 아래 보이는 평면. 있으면 그 뷰포트 orbit 을 끄고 (클릭이 평면으로), hover 색"""
        was = self._hot.get(view)
        if was == marker_id:
            return
        if marker_id is None:
            self._hot.pop(view, None)
            if view in self._views:
                view.release("plane_hot")
        else:
            self._hot[view] = marker_id
            view.hold("plane_hot")
        for mid in {was, marker_id} - {None}:
            self._paint_plane(mid)
            self._paint_arrow(mid)

    def _paint_arrow(self, marker_id) -> None:
        """화살표 hover: 고른 것과 같은 밝은 머티리얼"""
        m = self._find(marker_id)
        if m is not None and m["kind"] in ARROW_KINDS and marker_id != self._gizmo_target:
            lit = marker_id == self._selected or marker_id in self._hot.values()
            self._arrows.draw(m, lit, self.arrow_length())

    def _forget_planes(self, view=None, marker_id=None) -> None:
        """뷰포트가 떨어지거나 평면이 지워질 때"""
        views = [view] if view is not None else list(set(self._over) | set(self._hot))
        for v in views:
            if marker_id is None or self._hot.get(v) == marker_id:
                self._set_hot(v, None)
            if marker_id is None:
                self._over.pop(v, None)
                self._ray.pop(v, None)
                self._plane_dbg.pop(v, None)
                self._press_ndc.pop(v, None)
                asking = self._depth.pop(v, None)
                if asking is not None and asking["timer"] is not None:
                    asking["timer"].cancel()

    @staticmethod
    def _plane_point(m, payload, view=None):
        """제스처 광선이 그 평면과 만나는 점. payload 에 광선이 없으면 마우스 NDC 와 카메라로 만든 광선"""
        try:
            return ray_plane_point(m, payload.ray_origin, payload.ray_direction)
        except Exception:
            pass
        try:
            made = _ndc_ray(view.api, _xy(payload.mouse))
        except Exception:
            made = None
        return ray_plane_point(m, made[0], made[1]) if made else None

    def _new_marker(self, kind: str, hit: dict, api) -> dict:
        """찍은 점으로 마커 하나. inlet 은 -법선, outlet 은 +법선 방향"""
        pos = [float(c) for c in hit["position"]]
        marker = {
            "id": self._new_id(kind),
            "kind": kind,
            "label": "",
            "description": "",
            "color": list(DEFAULT_COLORS[kind]),
            "position": pos,
        }
        if kind in ARROW_KINDS:
            marker["direction"] = self._arrow_direction(kind, hit, api, pos)
        marker["offset"] = list(DEFAULT_OFFSET)
        marker["box"] = None
        marker["font_px"] = FONT_PX
        marker["visible"] = True
        marker["target"] = self._target_of_api(api)
        return marker

    # -- 고르기와 고치기 ------------------------------------------------------------

    # -- Kit 기즈모: 고른 마커의 USD 몸체 (화살표, 평면) 에 Kit 기본 이동 / 회전 기즈모를 띄운다 ----------
    # 몸체를 Kit 선택에 넣고 변환 도구를 바꾼다. 몸체는 피봇 자리 translate + 지역 점이라 회전 중심이 맞다
    # (평면 = 가운데, 화살표 = 표면에 닿는 끝). 기즈모가 바꾼 것은 USD 알림으로 받아 마커 데이터에 담는다

    def set_gizmo(self, mode: str) -> bool:
        """move / rotate / off. 켜 두면 마커를 고를 때마다 그 마커에 뜬다 (label 은 몸체가 없어 안 뜬다)"""
        if mode not in GIZMO_MODES:
            self._say(f"모르는 기즈모: {mode!r} ({', '.join(GIZMO_MODES)})")
            return False
        self._gizmo_mode = mode
        self._gizmo_attach(self._selected if mode != "off" else None)
        m = self._find(self._selected) if self._selected else None
        if mode != "off" and (m is None or self._body_path(m["id"]) is None):
            self._say(f"기즈모: {mode} (화살표나 평면을 고르면 뜹니다)", "gizmo")
        else:
            self._say(f"기즈모: {mode}", "gizmo")
        return True

    def gizmo(self) -> str:
        return self._gizmo_mode

    def _body_path(self, marker_id):
        m = self._find(marker_id) if marker_id else None
        if m is None:
            return None
        if m["kind"] in ARROW_KINDS:
            return f"{ARROW_SCOPE}/{marker_id}"
        if m["kind"] == "plane":
            return f"{PLANE_SCOPE}/{marker_id}"
        return None

    def _kit_take(self, marker_id) -> None:
        """마커를 화면에서 잡았을 때 Kit 선택: 기즈모가 켜져 있으면 그 몸체, 아니면 비운다 (형상 선택 풀기)"""
        path = self._body_path(marker_id) if self._gizmo_mode != "off" else None
        if path is None:
            _clear_kit_selection()
            return
        try:
            import omni.usd
            omni.usd.get_context().get_selection().set_selected_prim_paths([path], True)
        except Exception:
            pass

    def _gizmo_attach(self, marker_id) -> None:
        """기즈모를 그 마커로 (None 이면 뗀다)"""
        path = self._body_path(marker_id) if self._gizmo_mode != "off" else None
        was = self._gizmo_target
        if was is not None and was != marker_id:
            self._gizmo_target = None
            try:
                import omni.usd
                sel = omni.usd.get_context().get_selection()
                if self._body_path(was) in (sel.get_selected_prim_paths() or []):
                    sel.clear_selected_prim_paths()
            except Exception:
                pass
        if path is None:
            self._gizmo_target = None
            self._edit_session(False)
            self._transform_op(None)
            return
        self._gizmo_target = marker_id
        self._watch_bodies()
        self._edit_session(True)
        self._transform_op(self._gizmo_mode)
        try:
            import omni.usd
            omni.usd.get_context().get_selection().set_selected_prim_paths([path], True)
        except Exception as e:
            print(f"{TAG} cannot put the marker into the Kit selection: {type(e).__name__}: {e}")

    def _transform_op(self, mode) -> None:
        """Kit 변환 도구 (move / rotate). None 이면 처음 값으로"""
        if mode is None and self._op_was is None:
            return  # 바꾼 적 없다
        try:
            import carb.settings
            settings = carb.settings.get_settings()
            if mode is None:
                if self._op_was is not None:
                    settings.set(KIT_TRANSFORM_OP, self._op_was)
                    self._op_was = None
                return
            if self._op_was is None:
                self._op_was = settings.get(KIT_TRANSFORM_OP) or "select"
            settings.set(KIT_TRANSFORM_OP, mode)
        except Exception as e:
            if not self._gizmo_warned:
                self._gizmo_warned = True
                print(f"{TAG} cannot set the Kit transform tool: {type(e).__name__}: {e}")

    def _edit_session(self, on: bool) -> None:
        """기즈모가 붙은 동안만 스테이지 편집 대상을 세션 레이어로. 몸체가 세션 레이어에 있어서
        루트 레이어에 쓰면 세션 값에 덮여 안 움직이고 파일만 더러워진다. 떼면 원래 대상으로"""
        try:
            stage = self._arrows._stage()
        except Exception:
            stage = None
        if stage is None:
            return
        if on:
            if self._edit_was is None:
                self._edit_was = (stage, stage.GetEditTarget())
                stage.SetEditTarget(stage.GetSessionLayer())
            return
        was, self._edit_was = self._edit_was, None
        if was is not None and was[0] is stage:
            try:
                stage.SetEditTarget(was[1])
            except Exception as e:
                print(f"{TAG} cannot restore the edit target: {type(e).__name__}: {e}")

    def _watch_bodies(self) -> None:
        """몸체 prim 의 USD 변경을 듣는다 (스테이지가 바뀌면 다시)"""
        try:
            from pxr import Tf, Usd
            stage = self._arrows._stage()
        except Exception:
            return
        if stage is None or (self._body_notice is not None and self._body_notice_stage is stage):
            return
        self._unwatch_bodies()
        self._body_notice_stage = stage
        self._body_notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._on_usd_changed, stage)

    def _unwatch_bodies(self) -> None:
        notice, self._body_notice, self._body_notice_stage = self._body_notice, None, None
        if notice is not None:
            try:
                notice.Revoke()
            except Exception:
                pass

    def _on_usd_changed(self, notice, _stage) -> None:
        """알림 안에서는 USD 를 안 쓴다. 기즈모가 붙은 몸체가 바뀌었으면 다음 루프에 되읽는다"""
        target = self._gizmo_target
        path = self._body_path(target) if target else None
        if path is None or self._gizmo_pending:
            return
        try:
            changed = list(notice.GetChangedInfoOnlyPaths()) + list(notice.GetResyncedPaths())
        except Exception:
            return
        if any(str(p.GetPrimPath()) == path for p in changed):
            self._gizmo_pending = True
            _later(self._body_from_usd)

    def _body_from_usd(self) -> None:
        """기즈모가 옮기거나 돌린 몸체의 월드 변환을 마커 데이터로. 패널, 지시선만 다시 맞춘다 (메시는 안 씀)"""
        self._gizmo_pending = False
        mid = self._gizmo_target
        m = self._find(mid) if mid else None
        base = self._arrows.authored.get(mid) if m else None
        if m is None or base is None:
            return
        try:
            from pxr import Gf, Usd, UsdGeom
            prim = self._arrows._stage().GetPrimAtPath(self._body_path(mid))
            M = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            pivot = M.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
            turn = lambda v: _unit([float(c) for c in M.TransformDir(Gf.Vec3d(*v))])
        except Exception:
            return
        pivot = [float(c) for c in pivot]
        if m["kind"] in ARROW_KINDS:
            direction = turn(base["dir"]) or m["direction"]
            if _close(pivot, m["position"]) and _close(direction, _unit(m["direction"])):
                return
            m["position"], m["direction"] = pivot, direction
        else:
            frame = [turn(v) for v in base["frame"]]
            if any(f is None for f in frame):
                return
            n, u, v = PLANE_AXES[m["axis"]]
            o = m.get("origin", [0.0, 0.0, 0.0])
            at, shift = pivot[n] - o[n], [pivot[u] - o[u], pivot[v] - o[v]]
            rot = frame[0] + frame[1] + frame[2]
            if (abs(at - m["at"]) < 1e-9 and _close(shift, m["shift"])
                    and _close(rot, sum(_plane_of(m)["frame"], []))):
                return
            m["at"], m["shift"] = at, shift
            m["rot"] = None if _close(rot, sum(_frame(m["axis"]), [])) else rot
            if m["rot"] is None:
                m.pop("rot")
            m["position"] = _plane_of(m)["center"]
        layout = self._layout(m)
        picked = mid == self._selected
        self._each(lambda v: v.restyle(m, layout, picked))
        self._notify("gizmo", mid)

    def select(self, marker_id) -> bool:
        """그 마커를 고른다 (None 이면 풀기). 고른 것은 밝게, 모서리 손잡이가 나온다"""
        if marker_id is not None and self._find(marker_id) is None:
            self._say(f"마커 {marker_id!r} 이(가) 없습니다")
            return False
        if marker_id == self._selected:
            return True
        was, self._selected = self._selected, marker_id
        for mid in (was, marker_id):
            if mid is not None:
                self._restyle(mid)
        if self._gizmo_mode != "off":
            self._gizmo_attach(marker_id)
        self._notify("select", marker_id)
        return True

    def _restyle(self, marker_id: str) -> None:
        m = self._find(marker_id)
        if m is None:
            return
        layout = self._layout(m)
        picked = marker_id == self._selected
        if m["kind"] in ARROW_KINDS:
            self._arrows.draw(m, picked or marker_id in self._hot.values(), self.arrow_length())
        elif m["kind"] == "plane":
            self._arrows.draw_plane(m, self._plane_state(marker_id))
        self._each(lambda v: v.restyle(m, layout, picked))
        self._arrows.show(m["id"], self._shown(m))

    def _shown(self, m) -> bool:
        return self._all_visible and m.get("visible", True)

    @staticmethod
    def _view_id(view) -> str:
        """그 뷰포트의 id (viewport_api.id). 못 구하면 빈 칸"""
        try:
            return str(view.api.id)
        except Exception:
            return ""

    def _shown_in(self, view, m) -> bool:
        """그 뷰포트에 그 마커를 보이나. 연결(bind) 안 된 뷰포트는 다 보이고, 연결된 뷰포트는
        그 뷰포트가 비추는 형상(원본 USD) 의 마커만"""
        bound = self._bindings.get(self._view_id(view))
        return bound is None or m.get("target", "") in ("", bound["usd"])

    def _apply_visible(self, m) -> None:
        """그 마커의 패널(뷰포트마다)과 USD 몸체(화살표, 평면)를 지금 표시 상태로"""
        on = self._shown(m)
        self._each(lambda v: v.set_wanted(m["id"], on and self._shown_in(v, m)))
        self._arrows.show(m["id"], on)
        if not on and m["kind"] == "plane":
            self._forget_planes(marker_id=m["id"])

    # -- 뷰포트 연결: 뷰포트는 형상 하나를 비춘다. 마커는 형상 것이고 뷰포트와는 상관없다 ------------

    def bind_viewport(self, vph, prim_path: str, usd_path: str) -> bool:
        """그 뷰포트(vph) 가 이 프림(원본 USD) 을 비춘다. 그 뷰포트 ui_zstack 위에 마커 층을 깔고
        (처음 한 번), 그 형상의 마커를 그린다. 그 뷰포트에서 찍은 마커는 그 형상 것.
        같은 뷰포트를 다시 부르면 층은 두고 연결만 바꾼다 (다른 형상으로 바뀔 때)"""
        try:
            api, frame = vph.viewport_api, vph.ui_zstack
            vid = str(getattr(api, "id", "") or "")
        except Exception as e:
            self._say(f"bind_viewport: vph 에서 viewport_api / ui_zstack 을 못 얻었습니다: {e}")
            return False
        if not vid or frame is None:
            self._say("bind_viewport: viewport_api.id 나 ui_zstack 이 없습니다")
            return False
        if not isinstance(prim_path, str) or not isinstance(usd_path, str) or not usd_path.strip():
            self._say("bind_viewport: 프림 경로, USD 경로가 필요합니다")
            return False
        self._bindings[vid] = {"prim": prim_path.strip(), "usd": usd_path.strip()}
        view = next((v for v in self._views if self._view_id(v) == vid), None)
        if view is not None and (view.api is not api or view.frame is not frame):
            view.destroy()  # 같은 id 의 다른 뷰포트 위젯: 새로 깐다
            view = None
        if view is None:
            _View(self, api, frame)  # 짓고 _attach 로 마커를 그린다
        for m in self._markers:
            self._apply_visible(m)
        self._notify("bind", None)
        return True

    def unbind_viewport(self, viewport_id) -> bool:
        """그 뷰포트 연결을 풀고 마커 층도 걷는다 (뷰포트가 없어질 때)"""
        vid = str(viewport_id)
        found = self._bindings.pop(vid, None) is not None
        for v in [v for v in self._views if self._view_id(v) == vid]:
            v.destroy()
            found = True
        if found:
            self._notify("bind", None)
        return found

    def diagnose(self) -> dict:
        """지금 상태 (그려지지 않을 때 원인 찾기). 마커 층이 붙은 뷰포트, 연결, 타겟, 마커와 어긋나는 점"""
        views = []
        for v in self._views:
            vid = self._view_id(v)
            views.append({
                "viewport_id": vid,
                "api": type(v.api).__name__ if v.api is not None else None,
                "bound": self._bindings.get(vid),
                "drawn": len(v.marks),
                "visible": sum(1 for mv in v.marks.values() if mv.top.visible),
                "armed": v.armed,
                "orbit_off": _OrbitYield.holding(v._orbit_id),
                "orbit_dragging": _OrbitWatch.busy(),
                "plane_hot": self._hot.get(v),
                "plane_probe": copy.deepcopy(self._plane_dbg.get(v)),
            })
        markers = [{"id": m["id"], "kind": m["kind"], "target": m.get("target", ""),
                    "visible": m.get("visible", True)} for m in self._markers]
        hints = []
        if not self._views:
            hints.append("마커 층이 붙은 뷰포트가 없음: bind_viewport(vph, prim, usd) 가 아직 안 불림")
        bound_ids = set(self._bindings)
        seen_ids = {d["viewport_id"] for d in views}
        for vid in sorted(bound_ids - seen_ids):
            hints.append(f"bind 한 뷰포트 {vid!r} 에 마커 층이 없음 (마커 층이 붙은 뷰포트 id: {sorted(seen_ids)})")
        targets = {m.get("target", "") for m in self._markers}
        for vid, b in self._bindings.items():
            if self._markers and b["usd"] not in targets:
                hints.append(f"뷰포트 {vid!r} 의 usd {b['usd']!r} 와 같은 target 을 가진 마커가 없음 "
                             f"(마커 target: {sorted(targets)})")
        if not self._all_visible:
            hints.append("전체 표시가 꺼져 있음")
        return {"instance": f"{__name__}@{id(self):x}", "target_usd": self._target_usd,
                "panel_drag": copy.deepcopy(self._panel_dbg), "selected": self._selected,
                "bindings": copy.deepcopy(self._bindings), "views": views, "markers": markers, "hints": hints}

    def bindings(self) -> dict:
        """뷰포트 id -> {"prim", "usd"} (사본)"""
        return copy.deepcopy(self._bindings)

    def _target_of_api(self, api) -> str:
        """그 뷰포트에서 찍은 마커의 형상: 연결돼 있으면 그 USD, 아니면 지금 타겟 (없으면 빈 칸)"""
        try:
            bound = self._bindings.get(str(api.id))
        except Exception:
            bound = None
        return bound["usd"] if bound else self._target_usd

    def set_visible(self, marker_id, on) -> bool:
        """마커 하나 보이기 / 숨기기. 전체 표시가 꺼져 있으면 켜도 안 보인다 (값은 기억)"""
        m = self._find(marker_id)
        if m is None:
            self._say("보이기 / 숨기기할 마커가 없습니다")
            return False
        if not isinstance(on, bool):
            self._say("보이기 값은 True / False 입니다")
            return False
        m["visible"] = on
        self._apply_visible(m)
        self._notify("visible", marker_id)
        return True

    def is_visible(self, marker_id) -> bool:
        """그 마커의 보이기 값 (전체 표시와 따로). 없으면 False"""
        m = self._find(marker_id)
        return bool(m is not None and m.get("visible", True))

    def set_all_visible(self, on) -> bool:
        """전체 보이기 / 숨기기. 마커마다의 보이기 값은 그대로 둔다"""
        if not isinstance(on, bool):
            self._say("보이기 값은 True / False 입니다")
            return False
        self._all_visible = on
        for m in self._markers:
            self._apply_visible(m)
        self._notify("visible", None)
        return True

    def all_visible(self) -> bool:
        """전체 표시"""
        return self._all_visible

    def set_text(self, marker_id, label: str, description: str) -> bool:
        """패널 글. 상자 크기를 정해 둔 마커는 그 안에 들어가는 만큼만 보인다"""
        m = self._find(marker_id)
        if m is None:
            self._say("고친 마커가 없습니다" if marker_id is None else f"마커 {marker_id!r} 이(가) 없습니다")
            return False
        if not isinstance(label, str) or not isinstance(description, str):
            self._say("label, description 은 글이어야 합니다")
            return False
        if len(label) > MAX_LABEL or len(description) > MAX_DESCRIPTION:
            self._say(f"글이 너무 깁니다 (label ≤ {MAX_LABEL}, description ≤ {MAX_DESCRIPTION})")
            return False
        m["label"], m["description"] = one_line(label), description  # label 은 한 줄
        self._restyle(marker_id)
        self._notify("text", marker_id)
        return True

    def set_font_size(self, marker_id, px) -> bool:
        """그 마커 패널의 글자 크기 (첫 줄 px, description 은 DESC_RATIO 배). 상자 크기를 정해 뒀으면
        그 안에 들어가는 만큼만 보인다"""
        m = self._find(marker_id)
        if m is None:
            self._say("글자 크기를 바꿀 마커가 없습니다")
            return False
        if isinstance(px, bool) or not isinstance(px, (int, float)) or not math.isfinite(float(px)):
            self._say("글자 크기는 숫자여야 합니다")
            return False
        if not MIN_FONT_PX <= float(px) <= MAX_FONT_PX:
            self._say(f"글자 크기는 {MIN_FONT_PX:g} ~ {MAX_FONT_PX:g} 입니다")
            return False
        m["font_px"] = float(px)
        self._restyle(marker_id)
        self._notify("font", marker_id)
        return True

    def reset_panel(self, marker_id) -> bool:
        """패널을 처음 자리, 글에 맞춘 크기로"""
        m = self._find(marker_id)
        if m is None:
            self._say("되돌릴 마커가 없습니다")
            return False
        if self._drag is not None and self._drag["id"] == marker_id:
            self._end_drag()
        m["offset"], m["box"] = list(DEFAULT_OFFSET), None
        layout = self._layout(m)
        picked = marker_id == self._selected
        self._each(lambda v: v.relayout(m, layout, picked))
        self._notify("panel", marker_id)
        return True

    def remove(self, marker_id) -> bool:
        """마커 하나를 지운다"""
        m = self._find(marker_id)
        if m is None:
            self._say("지울 마커가 없습니다")
            return False
        if self._drag is not None and self._drag["id"] == marker_id:
            self._end_drag()
        if self._plane_drag is not None and self._plane_drag["id"] == marker_id:
            self._plane_release(self._plane_drag["view"])
        self._forget_planes(marker_id=marker_id)
        self._markers.remove(m)
        self._arrows.drop(marker_id)
        self._each(lambda v: v.drop(marker_id))
        if self._gizmo_target == marker_id:
            self._gizmo_attach(None)
        if self._selected == marker_id:
            self._selected = None
            self._notify("select", None)
        self._notify("remove", marker_id)
        return True

    def clear(self) -> None:
        """모두 지운다"""
        if self._drag is not None:
            self._end_drag()
        if self._plane_drag is not None:
            self._plane_release(self._plane_drag["view"])
        self._forget_planes()
        self._gizmo_attach(None)
        self._markers = []
        self._arrows.clear()
        self._each(lambda v: v.drop_all())
        if self._selected is not None:
            self._selected = None
            self._notify("select", None)
        self._notify("remove", None)

    # -- 형상 USD 와 마커 파일 --------------------------------------------------------

    def set_target_usd(self, path: str) -> bool:
        """마커가 붙는 형상 USD 경로 (omniverse:// 나 로컬). 익스텐션은 열지 않고 경로만 든다.
        다른 형상으로 바뀌면 지금 마커는 그 형상 것이 아니므로 비운다"""
        path = (path or "").strip()
        if not isinstance(path, str) or not path:
            self._say("형상 USD 경로가 비었습니다")
            return False
        if self._target_usd and path != self._target_usd:
            if self._markers:
                self.clear()  # 다른 형상으로 바뀜. 처음 정할 때는 지금 마커를 그 형상 것으로 둔다
            self._next_ids = {}  # 번호는 형상마다 따로
        self._adopt(path)
        self._say(f"형상: {path}  (마커 파일: {marker_file_of(path)})", "target")
        return True

    def _mine(self, m) -> bool:
        """지금 타겟 형상의 마커인가 (타겟 없이 만든 것도 지금 형상 것으로 본다)"""
        return m.get("target", "") in (self._target_usd, "")

    def _drop_mine(self) -> None:
        """지금 타겟 형상의 마커만 지운다"""
        for m in [m for m in self._markers if self._mine(m)]:
            self.remove(m["id"])

    def _adopt(self, path: str) -> None:
        """타겟을 정하고, 타겟 없이 만든 마커를 그 형상 것으로"""
        self._target_usd = path
        for m in self._markers:
            if not m.get("target"):
                m["target"] = path

    def target_usd(self) -> str:
        """마커가 붙는 형상 USD 경로. 없으면 빈 칸"""
        return self._target_usd

    def marker_file(self) -> str:
        """저장, 불러오기에 쓰는 마커 파일 경로 (형상 USD 옆 <이름>_marker.json). 없으면 빈 칸"""
        return marker_file_of(self._target_usd)

    def save(self, target_usd: str = None) -> bool:
        """지금 형상의 마커를 형상 USD 옆 마커 파일에 쓴다 (다른 형상 마커는 안 쓴다).
        target_usd 를 주면 그 형상으로 정하면서 저장한다: 타겟 없이 찍어 둔 마커를 첫 저장 때 형상에 붙이는 순서.
        다른 형상이 이미 정해져 있으면 지금 마커를 그 형상 것으로 옮겨 저장한다 (다른 이름으로 저장)"""
        if target_usd is not None:
            target = (target_usd or "").strip() if isinstance(target_usd, str) else ""
            if not target:
                self._say("저장할 형상 USD 경로가 비었습니다")
                return False
            if self._target_usd and target != self._target_usd:
                for m in self._markers:
                    if m.get("target") == self._target_usd:
                        m["target"] = target
            self._adopt(target)
            self._notify("target", None)
        path = self.marker_file()
        if not path:
            self._say("형상 USD 경로를 먼저 정하세요 (또는 저장할 때 경로를 주세요)")
            return False
        keep = ("id", "kind", "label", "description", "color", "offset", "box", "font_px", "visible", "target",
                "position", "direction", "axis", "at", "size", "shift", "origin", "rot")
        data = {
            "version": MARKER_FILE_VERSION,
            "target_usd": self._target_usd,
            "arrow_scale": self._arrow_scale,
            "next_ids": dict(self._next_ids),
            "all_visible": self._all_visible,
            "markers": [{k: copy.deepcopy(m[k]) for k in keep if k in m and not (m["kind"] == "plane" and k == "position")}
                        for m in self._markers if self._mine(m)],
        }
        try:
            _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        except Exception as e:
            self._say(f"저장 실패: {type(e).__name__}: {e}")
            return False
        others = sorted({m.get("target", "") for m in self._markers if not self._mine(m)})
        note = f" (다른 형상 마커 {len(self._markers) - len(data['markers'])}개는 안 씀, 그 target: {others[:3]})" if others else ""
        self._say(f"저장했습니다: {len(data['markers'])}개 -> {path}{note}", "save")
        return True

    def load(self) -> bool:
        """형상 USD 옆 마커 파일을 읽어 지금 형상의 마커를 바꾼다 (다른 형상 마커는 그대로).
        파일이 없거나 깨졌으면 지금 것을 그대로 둔다.
        잘못된 마커 하나는 건너뛰고 몇 개를 건너뛰었는지 알린다"""
        path = self.marker_file()
        if not path:
            self._say("형상 USD 경로를 먼저 정하세요")
            return False
        try:
            text = _read_text(path)
        except Exception as e:
            self._say(f"불러오기 실패: {type(e).__name__}: {e}")
            return False
        if text is None:
            self._say(f"마커 파일이 없습니다: {path}")
            return False
        try:
            data = json.loads(text)
        except ValueError as e:
            self._say(f"마커 파일이 깨졌습니다: {e}")
            return False
        if not isinstance(data, dict) or not isinstance(data.get("markers"), list):
            self._say("마커 파일 형식이 아닙니다 (markers 목록 없음)")
            return False
        version = data.get("version", 1)
        if not isinstance(version, int) or version > MARKER_FILE_VERSION:
            self._say(f"더 새 판의 마커 파일입니다 (version {version})")
            return False
        loaded, skipped = [], []
        for i, raw in enumerate(data["markers"]):
            try:
                loaded.append(clean_marker(raw))
            except ValueError as e:
                skipped.append(f"#{i}: {e}")
        self.cancel_create()
        self._drop_mine()  # 이 형상의 마커만 바꾼다 (다른 형상 마커는 그대로)
        # 번호: 파일의 카운터와 파일에 있는 id 중 큰 쪽부터. id 가 없거나 겹치면 새 id 를 주고 알린다
        self._next_ids = {}
        saved = data.get("next_ids")
        if isinstance(saved, dict):
            for kind, n in saved.items():
                if isinstance(kind, str) and isinstance(n, int) and not isinstance(n, bool) and n >= 1:
                    self._next_ids[kind] = n
        taken, renamed = {m["id"] for m in self._markers}, []
        for m in loaded:
            if m["id"] is not None and m["id"] not in taken:
                taken.add(m["id"])
                self._bump_id(m["id"], m["kind"])
            else:
                m["_was"] = m["id"]
        for m in loaded:
            if "_was" in m:
                was = m.pop("_was")
                m["id"] = self._new_id(m["kind"], taken)
                taken.add(m["id"])
                renamed.append(f"{was or '(없음)'}->{m['id']}")
        if isinstance(data.get("all_visible", True), bool):
            self._all_visible = data.get("all_visible", True)
        scale = data.get("arrow_scale", 1.0)
        if isinstance(scale, (int, float)) and not isinstance(scale, bool) and MIN_ARROW_SCALE <= scale <= MAX_ARROW_SCALE:
            self._arrow_scale = float(scale)
        for m in loaded:
            m["target"] = self._target_usd  # 이 파일은 이 형상 것
            self._markers.append(m)
            if m["kind"] in ARROW_KINDS:
                self._arrows.draw(m, False, self.arrow_length())
            elif m["kind"] == "plane":
                self._arrows.draw_plane(m, False)
            layout = self._layout(m)
            self._each(lambda v, m=m, layout=layout: v.add(m, layout, False))
            self._apply_visible(m)
        self._notify("add", None)
        self._notify("arrow", None)
        self._notify("visible", None)
        note = f" ({len(skipped)}개 건너뜀: {'; '.join(skipped[:3])})" if skipped else ""
        if renamed:
            note += f" (id 가 없거나 겹쳐 새 id: {', '.join(renamed[:5])})"
        self._say(f"불러왔습니다: {len(loaded)}개 <- {path}{note}", "load")
        return True

    # -- 패널 끌기 (씬 손짓) ---------------------------------------------------------

    def _hover_on(self, view, marker_id: str, zone) -> None:
        """변 띠에 마우스가 들어오면 기억한다. 커서가 크기 모양인 동안의 끌기는 이 변의 크기 조절"""
        if zone is not None:
            self._hover_edge = (view, marker_id, zone)

    def _hover_off(self, view, marker_id: str, zone) -> None:
        if zone is not None and self._hover_edge == (view, marker_id, zone):
            self._hover_edge = None

    def _panel_clicked(self, marker_id: str, zone) -> None:
        """클릭. 고른 패널의 변 띠(크기 커서 자리)면 무시, 아니면 고른다 (다음 루프에)"""
        if zone is not None and marker_id == self._selected:
            return

        def clicked():
            if self._find(marker_id) is None:
                return
            self._kit_take(marker_id)  # 마커를 잡으면 형상 선택은 푼다 (기즈모가 켜져 있으면 그 몸체로)
            self.select(marker_id)
            self._clicked = marker_id
            self._notify("click", marker_id)

        _later(clicked)

    def _drag_began(self, view, marker_id: str, edge, ndc) -> None:
        """끌기 시작. 변 띠에서 시작하면 그 변 크기 조절, 몸통이면 이동. 먼저 클릭해서 고른 패널만"""
        m = self._find(marker_id)
        skip = ("no marker" if m is None else "creating" if self._creating
                else f"not selected (selected: {self._selected})" if marker_id != self._selected
                else "orbit busy" if _OrbitWatch.busy() else None)
        self._panel_dbg = {"id": marker_id, "zone": None if edge is None else list(edge), "skip": skip, "moves": 0}
        if skip:
            return
        zone = edge  # 끌기를 보낸 영역 (None = 몸통)
        h = self._hover_edge
        if edge is None and h is not None and h[0] is view and h[1] == marker_id:
            edge = h[2]  # 커서가 크기 모양이면 몸통 끌기라도 그 변의 크기 조절
        d = self._drag
        if d is not None and d["id"] == marker_id and d["view"] is view and not d["moved"]:
            # 같은 누름에 두 영역이 끌기를 보냈다: 하나로 합치고, 변이 있으면 크기 조절
            d["zones"].add(zone)
            if edge and not d["edge"]:
                d["edge"] = edge
                _Cursor.hold("drag", "h" if edge[0] else "v")
            return
        if d is not None:  # 끝 신호를 못 받은 끌기는 버린다
            self._end_drag()
        layout = self._layout(m)
        self._drag = {
            "view": view, "id": marker_id, "edge": edge, "zones": {zone},
            "mouse": _Mouse.at(), "ndc": _xy(ndc), "res": _resolution(view.api),
            "offset": list(m["offset"]), "box": [layout["w"], layout["h"]],
            "min": layout["min"], "moved": False,
        }
        view.hold("drag")
        if edge:
            _Cursor.hold("drag", "h" if edge[0] else "v")

    def _drag_changed(self, view, ndc, zone=None) -> None:
        """끄는 중. 끝 위치 기준으로 다시 계산해 뷰포트마다 그 패널만 고친다"""
        if self._panel_dbg is not None:
            self._panel_dbg["moves"] += 1
        d = self._drag
        if d is None or d["view"] is not view:
            return
        m = self._find(d["id"])
        if m is None:
            self._end_drag()
            return
        delta = self._delta(d, view, ndc)
        if delta is None:
            return
        picked = m["id"] == self._selected
        if d["edge"] is None:
            m["offset"] = move_rect(d["offset"], delta)
            layout = self._layout(m)
            self._each(lambda v: v.place(m, layout, picked))
        else:
            m["offset"], m["box"] = resize_rect(d["offset"], d["box"], d["edge"], delta, d["min"])
            layout = self._layout(m)
            self._each(lambda v: v.relayout(m, layout, picked))
        d["moved"] = True

    def _drag_ended(self, view, zone=None) -> None:
        """끌기 끝. 합친 끌기는 마지막 영역이 끝날 때 끝난다 (Kit 이 한쪽을 막아 먼저 끝내도 이어간다)"""
        d = self._drag
        if d is None or d["view"] is not view:
            return
        d["zones"].discard(zone)
        if d["zones"]:
            return
        self._end_drag()
        if d["moved"]:
            self._notify("panel", d["id"])

    def _end_drag(self) -> None:
        d, self._drag = self._drag, None
        _Cursor.release("drag")
        if d is not None and d["view"] in self._views:
            d["view"].release("drag")

    @staticmethod
    def _delta(d, view, ndc):
        """시작부터 끈 거리 (패널 단위, y 위로). 앱 커서 이동(ui px) x DRAG_SCALE.
        커서를 못 물으면 NDC x 해상도 / 2 x DRAG_SCALE 로 어림"""
        scale = DRAG_SCALE
        now = _Mouse.at()
        if now is not None and d["mouse"] is not None:
            return (now[0] - d["mouse"][0]) * scale, (d["mouse"][1] - now[1]) * scale
        xy, start, res = _xy(ndc), d["ndc"], d["res"] or _resolution(view.api)
        if xy is not None and start is not None and res is not None:
            return (xy[0] - start[0]) * res[0] / 2.0 * scale, (xy[1] - start[1]) * res[1] / 2.0 * scale
        return None


_ONE = None


def instance():
    """이 킷앱이 쓰는 Annotation 하나. 없으면 그 자리에서 만든다"""
    global _ONE
    if _ONE is None:
        _ONE = Annotation()
    return _ONE


def forget() -> None:
    """들고 있던 것을 치우고 놓는다"""
    global _ONE
    if _ONE is not None:
        _ONE.teardown()
    _ONE = None
