import asyncio
import copy
import math
import unicodedata

from .annotation_constant import *

__all__ = ["Annotation", "instance", "forget",
           "text_width", "fit_line", "wrap_lines", "panel_layout",
           "resize_rect", "move_rect", "leader_end", "next_id", "surface_normal", "head_triangle"]


# -- 글자와 상자 (Kit 없이 도는 계산) ----------------------------------------------

def text_width(text: str, size: float) -> float:
    """글자 폭 어림. 전각은 1em, 나머지는 0.66em 로 넉넉하게"""
    em = 0.0
    for ch in text:
        em += WIDE_EM if unicodedata.east_asian_width(ch) in ("W", "F") else NARROW_EM
    return em * size


def _with_ellipsis(text: str, max_px: float, size: float) -> str:
    """끝에 … 을 붙여 폭 안에 들게 줄인다"""
    out = text.rstrip()
    while out and text_width(out + ELLIPSIS, size) > max_px:
        out = out[:-1].rstrip()
    if out:
        return out + ELLIPSIS
    return ELLIPSIS if text_width(ELLIPSIS, size) <= max_px else ""


def fit_line(text: str, max_px: float, size: float) -> str:
    """한 줄로. 넘치면 … 으로 자른다. 연속 공백은 하나로"""
    text = " ".join((text or "").split())
    if text_width(text, size) <= max_px:
        return text
    return _with_ellipsis(text, max_px, size)


def _split_word(word: str, max_px: float, size: float) -> list:
    """폭보다 긴 낱말을 폭에 맞게 자른다"""
    out = []
    while len(word) > 1 and text_width(word, size) > max_px:
        k = len(word) - 1
        while k > 1 and text_width(word[:k], size) > max_px:
            k -= 1
        out.append(word[:k])
        word = word[k:]
    if word:
        out.append(word)
    return out


def wrap_lines(text: str, max_px: float, size: float, max_lines: int) -> list:
    """폭에 맞춰 낱말 단위로 줄을 나눈다. 줄 수를 넘으면 마지막 줄을 … 로"""
    if max_lines <= 0 or not (text or "").strip():
        return []
    tokens = []
    for word in text.split():
        tokens.extend(_split_word(word, max_px, size))
    lines, cur = [], ""
    for tok in tokens:
        cand = f"{cur} {tok}" if cur else tok
        if not cur or text_width(cand, size) <= max_px:
            cur = cand
            continue
        lines.append(cur)
        cur = tok
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _with_ellipsis(lines[-1], max_px, size)
    return lines


def panel_layout(title: str, desc: str, box=None, scale: float = TEXT_SCALE) -> dict:
    """패널 크기와 줄. box 가 없으면 글자에 맞추고, 있으면 그 안에 들어가는 만큼만"""
    fs = FONT_PX * scale
    ds = fs * DESC_RATIO
    th = fs * LINE_HEIGHT
    dh = ds * LINE_HEIGHT
    pad = PAD_PX * scale
    min_w = MIN_WIDE_PX * scale
    min_h = th + 2.0 * pad
    if box is None:
        room = AUTO_WIDE_PX * scale
        head = fit_line(title, room, fs)
        lines = wrap_lines(desc, room, ds, AUTO_DESC_LINES)
        inner = max([text_width(head, fs)] + [text_width(line, ds) for line in lines])
        w = max(inner + 2.0 * pad, min_w)
        h = max(th + dh * len(lines) + 2.0 * pad, min_h)
    else:
        w = max(float(box[0]), min_w)
        h = max(float(box[1]), min_h)
        room = w - 2.0 * pad
        head = fit_line(title, room, fs)
        rows = min(int(math.floor((h - 2.0 * pad - th) / dh + 1e-6)), MAX_PANEL_LINES)
        lines = wrap_lines(desc, room, ds, rows)
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


def next_id(kind: str, taken) -> str:
    """비어 있는 가장 작은 <kind>_<n>"""
    taken = set(taken)
    n = 1
    while f"{kind}_{n}" in taken:
        n += 1
    return f"{kind}_{n}"


# -- 벡터 -------------------------------------------------------------------------

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


def _bright(color):
    """골랐을 때 색. 흰색 쪽으로"""
    return [float(c) + (1.0 - float(c)) * HIGHLIGHT_MIX for c in color[:3]]


def _arrow_ends(marker):
    """(꼬리, 머리). inlet 은 머리가 표면, outlet 은 꼬리가 표면"""
    p = marker["position"]
    d = _unit(marker["direction"]) or [0.0, 0.0, 1.0]
    size = marker["size"]
    if marker["kind"] == "inlet":
        return _sub(p, _mul(d, size)), list(p)
    return list(p), _add(p, _mul(d, size))


def _panel_anchor(marker):
    """패널이 매달리는 월드 점. label 은 점, inlet 은 꼬리, outlet 은 머리"""
    if marker["kind"] == "inlet":
        return _arrow_ends(marker)[0]
    if marker["kind"] == "outlet":
        return _arrow_ends(marker)[1]
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


def _later(fn) -> None:
    """다음 루프에 부른다. 지금 지우는 도형의 콜백 안에서 부르지 않으려고"""
    def run():
        try:
            fn()
        except Exception as e:
            print(f"{TAG} deferred call failed: {type(e).__name__}: {e}")

    asyncio.get_event_loop().call_soon(run)


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


def _hit(args):
    """쿼리 콜백 값을 (prim 경로, [x, y, z]) 로. 빈 곳이면 None"""
    if not args or not args[0]:
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

    class Click(sc.ClickGesture):
        def __init__(self, fn, priority=GESTURE_PRIORITY, name="click"):
            super().__init__()
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
        def __init__(self, began, changed, ended, priority=GESTURE_PRIORITY, name="drag"):
            super().__init__()
            self._began, self._changed, self._ended = began, changed, ended
            self._priority, self._name = priority, name

        def on_began(self, *_args):
            self._began(mouse_of(self))

        def on_changed(self, *_args):
            self._changed(mouse_of(self))

        def on_ended(self, *_args):
            self._ended()

        @property
        def name(self):
            return f"{NAME}.{self._name}"

        @property
        def priority(self):
            return self._priority

    class Hover(sc.HoverGesture):
        def __init__(self, began, ended, name="hover"):
            super().__init__()
            self._began, self._ended, self._name = began, ended, name

        def on_began(self, *_args):
            self._began()

        def on_ended(self, *_args):
            self._ended()

        @property
        def name(self):
            return f"{NAME}.{self._name}"

    _GESTURES = (Click, Drag, Hover)
    return _GESTURES


def head_triangle(tail, tip, size: float, toward=None) -> tuple:
    """(몸통 끝, 삼각형 세 점). 화살 방향을 따라 눕히고, 넓은 면이 카메라 쪽(toward)을 보게 세운다.
    카메라를 모르거나 화살이 카메라를 똑바로 가리키면 아무 수직 방향으로"""
    d = _unit(_sub(tip, tail)) or [0.0, 0.0, 1.0]
    length = math.sqrt(_dot(_sub(tip, tail), _sub(tip, tail)))
    head = min(ARROW_HEAD * size, length)
    half = ARROW_RADIUS * size
    base = _sub(tip, _mul(d, head))
    side = _unit(_cross(d, toward)) if toward is not None else None
    if side is None:
        side = _perp_basis(d)[0]
    return base, [list(tip), _add(base, _mul(side, half)), _sub(base, _mul(side, half))]


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
        self._edges = {}
        self._desc = []
        white = ui.color(1.0, 1.0, 1.0, 1.0)
        bg_key = f"bg:{mid}"
        with view.root:
            self.top = sc.Transform()
        with self.top:
            self._shaft = self._head = None
            self._arrow = None
            if marker["kind"] in ARROW_KINDS:
                self._arrow = (*_arrow_ends(marker), marker["size"])
                base, tri = head_triangle(*self._arrow)
                self._shaft = sc.Line(self._arrow[0], base, color=white, thickness=ARROW_LINE_PX)
                self._head = sc.PolygonMesh(tri, [[1.0, 1.0, 1.0, 1.0]] * 3, [3, 3],
                                            [0, 1, 2, 0, 2, 1])  # 앞뒤 두 면이라 어느 쪽에서도 보인다
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
                                self._bg = sc.Rectangle(1.0, 1.0, color=ui.color(*BOX_BG), gesture=[
                                    Hover(lambda: view.hold(bg_key), lambda: view.release(bg_key),
                                          name="panel_hover"),
                                    Click(lambda _m: owner._panel_clicked(mid), name="panel_click"),
                                    Drag(lambda m: owner._drag_began(view, mid, None, m),
                                         lambda m: owner._drag_changed(view, m),
                                         lambda: owner._drag_ended(view), name="panel_move"),
                                ])
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
                            self._handles = sc.Transform()
                            with self._handles:
                                for edge in EDGES:
                                    self._edges[edge] = self._edge(edge)
        self.restyle(marker, layout, picked)

    def _edge(self, edge) -> tuple:
        """변 손잡이 하나. 상자 바깥 띠(잡는 곳) + 가운데 막대(보이는 곳)"""
        sc, ui, view = self._view.sc, self._view.ui, self._view
        _click, Drag, Hover = _gesture_types()
        owner, mid = view.owner, self.id
        key = f"h:{mid}:{edge[0]}{edge[1]}"
        xf = sc.Transform()
        with xf:
            hit = sc.Rectangle(1.0, 1.0, color=ui.color(*EDGE_HIT_COLOR), gesture=[
                Hover(lambda: view.hold(key), lambda: view.release(key), name="edge_hover"),
                Drag(lambda m: owner._drag_began(view, mid, edge, m),
                     lambda m: owner._drag_changed(view, m),
                     lambda: owner._drag_ended(view), priority=HANDLE_PRIORITY, name="edge"),
            ])
            bar = sc.Rectangle(1.0, 1.0, color=ui.color(*HANDLE_COLOR))
        return xf, hit, bar

    # -- 맞추기 (도형은 그대로, 속성만) ---------------------------------------------

    def restyle(self, marker, layout, picked: bool) -> None:
        """다 맞춘다. 고른 상태, 글, 색이 바뀌었을 때"""
        sc, ui = self._view.sc, self._view.ui
        self.anchor = list(marker["position"])
        self._anchor_xf.transform = _move(sc, *_panel_anchor(marker))
        color = self._color(marker, picked)
        ink = ui.color(*_rgba(color))
        if self._dot is not None:
            self._dot.colors = [_rgba(color)]
            self._dot.sizes = [DOT_PX * (PICKED_DOT_SCALE if picked else 1.0)]
        self._leader.color = ink
        self._leader.thickness = LEADER_PX * (PICKED_LINE_SCALE if picked else 1.0)
        self._border.color = ink
        self._border.thickness = BORDER_PICKED_PX if picked else BORDER_PX
        self._title.color = ink if marker["label"] else ui.color(*DIM_COLOR)
        if self._shaft is not None:
            self._shaft.color = ink
            self._shaft.thickness = ARROW_LINE_PX * (PICKED_LINE_SCALE if picked else 1.0)
            self._head.colors = [_rgba(color)] * 3
        self.aim()
        self.relayout(marker, layout, picked)

    def aim(self) -> None:
        """화살촉 삼각형을 지금 카메라 쪽으로 세운다 (도형은 그대로, 점 자리만)"""
        if self._head is None:
            return
        tail, tip, size = self._arrow
        toward = _camera_of(self._view.api, tip)[0] if self._view.api is not None else None
        _base, tri = head_triangle(tail, tip, size, toward)
        self._head.positions = tri

    def relayout(self, marker, layout, picked: bool) -> None:
        """상자 크기가 바뀌었을 때. 바탕, 글, 손잡이, 자리, 지시선"""
        sc = self._view.sc
        w, h, pad = layout["w"], layout["h"], layout["pad"]
        self._bg_xf.transform = _move(sc, w / 2.0, -h / 2.0, BOX_Z)
        for rect in (self._bg, self._border):
            rect.width, rect.height = w, h
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
        self._place_handles(layout, picked)
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

    def _place_handles(self, layout, picked: bool) -> None:
        """손잡이는 고른 것에만, 상자 네 변 바깥에"""
        sc = self._view.sc
        self._handles.visible = bool(picked)
        if not picked:
            self._view.release_prefix(f"h:{self.id}:")
        w, h, t = layout["w"], layout["h"], EDGE_HIT_PX
        for (ex, ey), (xf, hit, bar) in self._edges.items():
            if ex:
                length, x, y = h, (w + t / 2.0 if ex > 0 else -t / 2.0), -h / 2.0
            else:
                length, x, y = w, w / 2.0, (t / 2.0 if ey > 0 else -h - t / 2.0)
            grip = min(EDGE_GRIP_LEN_PX, length * 0.6)
            if ex:
                hit.width, hit.height = t, length
                bar.width, bar.height = EDGE_GRIP_PX, grip
            else:
                hit.width, hit.height = length, t
                bar.width, bar.height = grip, EDGE_GRIP_PX
            xf.transform = _move(sc, x, y, HANDLE_Z)

    def _color(self, marker, picked: bool):
        return _bright(marker["color"]) if picked else marker["color"]

    def show(self, on: bool) -> None:
        """카메라 뒤면 숨긴다"""
        if on != self.shown:
            self.shown = on
            self.top.visible = on

    def destroy(self) -> None:
        """그린 것과 걸어 둔 선택 잠금을 놓는다"""
        self._view.release_prefix(f"bg:{self.id}")
        self._view.release_prefix(f"h:{self.id}:")
        self._edges = {}
        self._desc = []
        self.top.clear()
        self.top.visible = False


class _View:
    """뷰포트 하나. 뷰포트 레지스트리가 뷰포트마다 만든다"""

    def __init__(self, owner, desc):
        """레지스트리가 열어 둔 자리에 뿌리를 짓고 주인에게 알린다"""
        import omni.ui as ui
        from omni.ui import scene as sc
        self.ui, self.sc = ui, sc
        self.owner = owner
        self.api = (desc or {}).get("viewport_api")
        self.root = sc.Transform()
        self._pick_root = sc.Transform()
        self._screen = None
        self.marks = {}
        self._holds = set()
        self._blocked = False
        self._token = None
        self._visible = True
        self._view_sub = None
        api = self.api
        if api is not None and callable(getattr(api, "subscribe_to_view_change", None)):
            try:
                self._view_sub = api.subscribe_to_view_change(lambda *_: self.cull())
            except Exception as e:
                print(f"{TAG} view change subscription failed: {e}")
        owner._attach(self)

    # -- 레지스트리 항목 ---------------------------------------------------------

    @property
    def name(self) -> str:
        return SCENE_NAME

    @property
    def categories(self):
        return [SCENE_CATEGORY]

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        if self.root is not None:
            self.root.visible = self._visible

    def destroy(self) -> None:
        """뷰포트가 닫힐 때"""
        self.arm(False)
        self.drop_all()
        self._holds = set()
        self._sync_block()
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
        self._pick_root = None
        self.root = None
        self.api = None

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

    def _sync_block(self) -> None:
        want = bool(self._holds)
        if want and not self._blocked:
            self._blocked = True
            self._token = _block_selection(self.api)
        elif not want and self._blocked:
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
            if mv.shown:
                mv.aim()


class Annotation:
    """마커의 속. 목록, 선택, 찍어서 만들기, 패널 끌기, 그리기를 쥔다"""

    def __init__(self):
        """목록과 상태 자리만 비워 둔다. 뷰포트에 거는 것은 start"""
        self._markers: list = []
        self._selected = None
        self._creating: str = ""
        self._status: str = ""
        self._views: list = []
        self._listeners: list = []
        self._registration = None
        self._retry = None
        self._retries = 0
        self._queries: set = set()
        self._drag = None
        self._closed = False

    # -- 시작과 정리 --------------------------------------------------------------

    def start(self) -> bool:
        """모든 뷰포트에 그릴 자리를 건다. 레지스트리가 아직 없으면 몇 프레임 더 해 본다"""
        self._closed = False
        if self._register():
            return True
        try:
            import omni.kit.app
            self._retry = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._retry_step(),
                                            name="annotation registry wait")
        except Exception as e:
            print(f"{TAG} viewport registry unavailable, nothing will be drawn: {e}")
        return False

    def _register(self) -> bool:
        if self._registration is not None:
            return True
        try:
            from omni.kit.viewport.registry import RegisterScene
        except ImportError:
            return False
        try:
            self._registration = RegisterScene(self._factory, SCENE_ID)
        except Exception as e:
            print(f"{TAG} could not register the scene: {type(e).__name__}: {e}")
            return False
        return True

    def _retry_step(self) -> None:
        self._retries += 1
        if self._register():
            self._retry = None
        elif self._retries >= REGISTRY_TRIES:
            self._retry = None
            print(f"{TAG} viewport registry never came, nothing will be drawn")

    def _factory(self, desc):
        """레지스트리가 뷰포트마다 부른다"""
        return _View(self, desc)

    def teardown(self) -> None:
        """뷰포트에서 떼고 들고 있던 것을 놓는다"""
        self._closed = True
        self._retry = None
        self._creating = ""
        for q in list(self._queries):
            q.cancel()
        self._queries = set()
        self._drag = None
        reg, self._registration = self._registration, None
        if reg is not None:
            try:
                reg.destroy()
            except Exception as e:
                print(f"{TAG} unregister failed: {e}")
        for view in list(self._views):
            view.destroy()
        self._views = []
        self._listeners = []
        self._markers = []
        self._selected = None

    def _attach(self, view) -> None:
        """새 뷰포트. 있는 마커를 다 그린다"""
        self._views.append(view)
        for m in self._markers:
            view.add(m, self._layout(m), m["id"] == self._selected)
        if self._creating:
            view.arm(True)

    def _detach(self, view) -> None:
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
        """바뀔 때마다 fn(reason). reason: add, remove, select, text, panel, create, status"""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, reason: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(reason)
            except Exception as e:
                print(f"{TAG} listener failed: {type(e).__name__}: {e}")

    def _say(self, text: str, reason: str = "status") -> None:
        self._status = text
        self._notify(reason)

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

    def creating(self) -> str:
        """찍기를 기다리는 종류. 아니면 빈 칸"""
        return self._creating

    def status(self) -> str:
        """마지막 상태 글"""
        return self._status

    def _find(self, marker_id):
        for m in self._markers:
            if m["id"] == marker_id:
                return m
        return None

    def _layout(self, marker) -> dict:
        return panel_layout(_title_of(marker), marker["description"], marker.get("box"))

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

    def cancel_create(self) -> None:
        """찍기 대기를 거둔다. 묻는 중이던 답도 버린다"""
        if not self._creating:
            return
        self._creating = ""
        for q in list(self._queries):
            q.cancel()
        self._queries = set()
        self._each(lambda v: v.arm(False))
        self._say("찍기를 취소했습니다", "create")

    def _view_clicked(self, view, ndc) -> None:
        """찍기 대기 중 클릭. 이미지 밖이면 계속 기다린다"""
        kind = self._creating
        if not kind or self._closed or view.api is None:
            return
        xy = _xy(ndc)
        pixel = _texture_pixel(view.api, xy) if xy is not None else None
        if pixel is None:
            return
        self._each(lambda v: v.arm(False))
        self._say("위치 확인 중…", "create")
        api = view.api
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
            self._say("빈 곳입니다. 모델 위를 클릭하세요", "create")
            return
        self._creating = ""
        marker = self._new_marker(kind, result, api)
        self._markers.append(marker)
        layout = self._layout(marker)
        self._each(lambda v: v.add(marker, layout, False))
        self._notify("add")
        self.select(marker["id"])
        self._say(f"만들었습니다: {marker['id']}", "create")

    def _new_marker(self, kind: str, hit: dict, api) -> dict:
        """찍은 점으로 마커 하나. inlet 은 -법선, outlet 은 +법선 방향"""
        pos = [float(c) for c in hit["position"]]
        marker = {
            "id": next_id(kind, (m["id"] for m in self._markers)),
            "kind": kind,
            "label": "",
            "description": "",
            "color": list(DEFAULT_COLORS[kind]),
            "position": pos,
        }
        if kind in ARROW_KINDS:
            toward, distance = _camera_of(api, pos)
            n = hit.get("normal") or _unit(toward) or [0.0, 0.0, 1.0]
            sign = -1.0 if kind == "inlet" else 1.0
            marker["direction"] = [sign * float(c) for c in n]
            marker["size"] = distance * ARROW_VIEW_SHARE if distance else ARROW_FALLBACK
        marker["offset"] = list(DEFAULT_OFFSET)
        marker["box"] = None
        return marker

    # -- 고르기와 고치기 ------------------------------------------------------------

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
        self._notify("select")
        return True

    def _restyle(self, marker_id: str) -> None:
        m = self._find(marker_id)
        if m is None:
            return
        layout = self._layout(m)
        picked = marker_id == self._selected
        self._each(lambda v: v.restyle(m, layout, picked))

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
        m["label"], m["description"] = label, description
        self._restyle(marker_id)
        self._notify("text")
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
        self._notify("panel")
        return True

    def remove(self, marker_id) -> bool:
        """마커 하나를 지운다"""
        m = self._find(marker_id)
        if m is None:
            self._say("지울 마커가 없습니다")
            return False
        if self._drag is not None and self._drag["id"] == marker_id:
            self._end_drag()
        self._markers.remove(m)
        self._each(lambda v: v.drop(marker_id))
        if self._selected == marker_id:
            self._selected = None
            self._notify("select")
        self._notify("remove")
        return True

    def clear(self) -> None:
        """모두 지운다"""
        if self._drag is not None:
            self._end_drag()
        self._markers = []
        self._each(lambda v: v.drop_all())
        if self._selected is not None:
            self._selected = None
            self._notify("select")
        self._notify("remove")

    # -- 패널 끌기 (씬 손짓) ---------------------------------------------------------

    def _panel_clicked(self, marker_id: str) -> None:
        """패널 클릭. 지금 지우게 될 바탕의 콜백 안이라 다음 루프에 고른다"""
        _later(lambda: self.select(marker_id) if self._find(marker_id) else None)

    def _drag_began(self, view, marker_id: str, edge, ndc) -> None:
        """끌기 시작. edge 가 None 이면 옮기기, 아니면 그 변으로 크기"""
        m = self._find(marker_id)
        if m is None or self._creating:
            return
        if self._drag is not None:  # 끝 신호를 못 받은 끌기(도형이 지워진 경우)는 버린다
            self._end_drag()
        layout = self._layout(m)
        self._drag = {
            "view": view, "id": marker_id, "edge": edge,
            "mouse": _Mouse.at(), "ndc": _xy(ndc), "res": _resolution(view.api),
            "offset": list(m["offset"]), "box": [layout["w"], layout["h"]],
            "min": layout["min"], "moved": False,
        }
        view.hold("drag")

    def _drag_changed(self, view, ndc) -> None:
        """끄는 중. 끝 위치 기준으로 다시 계산해 뷰포트마다 그 패널만 고친다"""
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

    def _drag_ended(self, view) -> None:
        """끌기 끝. 옮긴 패널은 고른다"""
        d = self._drag
        if d is None or d["view"] is not view:
            return
        self._end_drag()
        if d["moved"]:
            self._notify("panel")
        if d["edge"] is None:
            marker_id = d["id"]
            _later(lambda: self.select(marker_id) if self._find(marker_id) else None)

    def _end_drag(self) -> None:
        d, self._drag = self._drag, None
        if d is not None and d["view"] in self._views:
            d["view"].release("drag")

    @staticmethod
    def _delta(d, view, ndc):
        """시작부터 끈 거리 (장면 px, y 위로). 패널의 화면 px 는 뷰포트 렌더 해상도 기준이라
        제스처 NDC 변화 x 해상도 / 2 로 잰다. NDC 가 없을 때만 앱 커서(ui px)로 어림"""
        xy, start, res = _xy(ndc), d["ndc"], d["res"] or _resolution(view.api)
        if xy is not None and start is not None and res is not None:
            return (xy[0] - start[0]) * res[0] / 2.0, (xy[1] - start[1]) * res[1] / 2.0
        now = _Mouse.at()
        if now is not None and d["mouse"] is not None:
            return now[0] - d["mouse"][0], d["mouse"][1] - now[1]
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
