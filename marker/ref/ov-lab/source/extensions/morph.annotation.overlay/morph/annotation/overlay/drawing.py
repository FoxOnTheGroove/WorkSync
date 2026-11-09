"""omni.ui.scene drawing on every viewport (omni.kit.viewport.registry RegisterScene).

- One ``_AnnotationScene`` per viewport window (the registry calls our factory for every
  ViewportSceneLayer, including windows created later, e.g. by the split view).
- Shapes are rebuilt only when the list / visibility / textScale / showDescription changes, or a
  viewport crosses the small-view threshold (``OverlayDrawing.flush`` once per update), timed with
  perf ``scope("morph.annotation.overlay.rebuild_ms")``.
- Tag boxes are sized from an estimated text width (wide CJK glyphs count ~1 em, others ~0.66 em),
  descriptions wrap at ~40 characters, max 2 lines, then an ellipsis.
- omni.ui.scene is drawn as a UI layer over the rendered image, so the model never hides it.
- Annotations whose anchor is behind a perspective camera are hidden (re-checked on view change).
- The selected annotation is highlighted (thicker lines, brighter colour, thicker tag border).
- The selected face (``AnnotationOverlay.selected_face``) is drawn as a bright cyan outline, a
  screen-size cross at its centre and an info box (centre, normal, area). Not an annotation.
- ``OverlayDrawing.arm_click`` puts a full-screen ``sc.Screen`` + ``ClickGesture`` in every
  viewport scene for one left click (the editor's "pick in view"); Kit's click selection is
  disabled on those viewports meanwhile (``omni.kit.viewport.utility.disable_selection``).
"""

__all__ = [
    "OverlayDrawing",
    "FACTORY_ID",
    "M_REBUILD",
    "wrap_text",
    "text_width",
    "view_text_factor",
    "highlight_color",
    "ndc_to_image",
    "build_face",
    "face_label_lines",
    "plane_basis",
    "FACE_COLOR",
]

import asyncio
import contextlib
import math
import unicodedata
import weakref
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

import carb

from .face import plane_basis
from .overlay import EXT_ID, AnnotationOverlay, show_description_setting, text_scale_setting

FACTORY_ID = f"{EXT_ID}.scene"
M_REBUILD = f"{EXT_ID}.rebuild_ms"
REGISTRY_EXT = "omni.kit.viewport.registry"

BASE_FONT_PX = 14.0
DESC_FONT_RATIO = 0.8
TITLE_MAX_CHARS = 48
DESC_LINE_CHARS = 40
DESC_MAX_LINES = 2
ELLIPSIS = "\u2026"
LINE_HEIGHT = 1.3  # line pitch / font size
WIDE_EM = 1.0  # East Asian wide / fullwidth glyph width (em)
NARROW_EM = 0.66  # other glyphs (em); generous so the box always covers the text
SMALL_VIEW_PX = 400  # viewport height below this ...
SMALL_VIEW_FACTOR = 0.8  # ... scales text by this
LEADER_PX = 28.0
DOT_PX = 8.0
LINE_PX = 2.0
ARROW_LINE_PX = 3.0
PLANE_ALPHA = 0.22
BOX_BG = (0.08, 0.08, 0.08, 0.72)
CONE_SEGMENTS = 12
HIGHLIGHT_MIX = 0.45  # selected colour = colour mixed toward white by this much
PICK_GESTURE_PRIORITY = 1000  # above Kit's selection gestures (negative priority)
FACE_COLOR = (0.1, 0.95, 1.0)  # selected face: bright cyan
FACE_LINE_PX = 4.0
FACE_CROSS_PX = 9.0


class _Style(NamedTuple):
    line: float = LINE_PX
    arrow: float = ARROW_LINE_PX
    border: float = 1.0
    dot: float = 1.0  # dot size factor
    alpha: float = PLANE_ALPHA


NORMAL_STYLE = _Style()
SELECTED_STYLE = _Style(line=LINE_PX * 2.0, arrow=ARROW_LINE_PX * 2.0, border=3.0, dot=1.5, alpha=0.35)


def highlight_color(color) -> list:
    """Colour of a selected annotation (brighter, toward white)."""
    return [float(c) + (1.0 - float(c)) * HIGHLIGHT_MIX for c in color[:3]]


def ndc_to_image(viewport_api, ndc) -> Optional[Tuple[float, float]]:
    """Viewport NDC mouse -> normalized image (x, y), origin top-left. None outside the image."""
    try:
        (u, v), inside = viewport_api.map_ndc_to_texture(ndc)
    except Exception:  # noqa: BLE001
        return None
    if inside is None:
        return None
    x, y = float(u), 1.0 - float(v)
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return x, y

try:
    from morph.perf.monitor import scope as _perf_scope
except Exception:  # noqa: BLE001
    _perf_scope = None


def _scope(name: str):
    if _perf_scope is None:
        return contextlib.nullcontext()
    return _perf_scope(name)


# ---------------------------------------------------------------------- vector math
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


def _norm(a):
    length = math.sqrt(_dot(a, a))
    if length < 1e-12:
        return [0.0, 0.0, 0.0]
    return [a[0] / length, a[1] / length, a[2] / length]


def _perp_basis(d) -> Tuple[list, list]:
    """Two unit vectors perpendicular to unit ``d`` and to each other."""
    axis = [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0]
    u = _norm(_cross(d, axis))
    v = _cross(d, u)
    return u, v


def _stage_up() -> list:
    try:
        import omni.usd
        from pxr import UsdGeom

        stage = omni.usd.get_context().get_stage()
        if stage is not None and UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
            return [0.0, 0.0, 1.0]
    except Exception:  # noqa: BLE001
        pass
    return [0.0, 1.0, 0.0]


def _ellipsize(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + ELLIPSIS


def wrap_text(text: str, width: int = DESC_LINE_CHARS, max_lines: int = DESC_MAX_LINES) -> List[str]:
    """Greedy word wrap at ``width`` characters (long words are cut), at most ``max_lines`` lines.

    Whitespace runs collapse to one space. If text remains, the last line ends with an ellipsis
    (still at most ``width`` characters).
    """
    tokens: List[str] = []
    for word in text.split():
        while len(word) > width:
            tokens.append(word[:width])
            word = word[width:]
        if word:
            tokens.append(word)
    lines: List[str] = []
    cur = ""
    truncated = False
    for tok in tokens:
        cand = f"{cur} {tok}" if cur else tok
        if len(cand) <= width:
            cur = cand
            continue
        lines.append(cur)
        cur = tok
        if len(lines) == max_lines:
            truncated = True
            break
    if not truncated and cur:
        lines.append(cur)
    if truncated:
        last = lines[-1]
        lines[-1] = (last if len(last) < width else last[: width - 1]) + ELLIPSIS
    return lines


def text_width(text: str, size: float) -> float:
    """Estimated pixel width of ``text`` at font ``size`` (over-estimates rather than under)."""
    em = 0.0
    for ch in text:
        em += WIDE_EM if unicodedata.east_asian_width(ch) in ("W", "F") else NARROW_EM
    return em * size


def view_text_factor(viewport_api) -> float:
    """``SMALL_VIEW_FACTOR`` when the viewport is shorter than ``SMALL_VIEW_PX`` pixels, else 1.0."""
    if viewport_api is None:
        return 1.0
    try:
        height = float(viewport_api.resolution[1])
    except Exception:  # noqa: BLE001
        return 1.0
    if not math.isfinite(height) or height <= 0:
        return 1.0
    return SMALL_VIEW_FACTOR if height < SMALL_VIEW_PX else 1.0


# ---------------------------------------------------------------------- shape builders (inside a sc container)
def _rgba(color, alpha=1.0):
    return [float(color[0]), float(color[1]), float(color[2]), float(alpha)]


def _build_tag(sc, ui, anchor, title: str, desc: str, color, text_scale: float, dot: bool,
               style: _Style = NORMAL_STYLE) -> None:
    """Screen-size tag at a world anchor: optional dot, leader line, box with label/description.

    ``desc`` is drawn wrapped (``wrap_text``); pass "" to hide it. The box is sized from
    ``text_width`` so it covers every line.
    """
    cl = ui.color
    c = cl(*_rgba(color))
    title = _ellipsize(" ".join(title.split()), TITLE_MAX_CHARS) if title else ""
    desc_lines = wrap_text(desc) if desc else []
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*anchor)):
        with sc.Transform(scale_to=sc.Space.SCREEN):
            with sc.Transform(look_at=sc.Transform.LookAt.CAMERA):
                if dot:
                    sc.Points([[0, 0, 0]], colors=[_rgba(color)], sizes=[DOT_PX * text_scale * style.dot])
                if not title and not desc_lines:
                    return
                fs = BASE_FONT_PX * text_scale
                ds = fs * DESC_FONT_RATIO
                th = fs * LINE_HEIGHT if title else 0.0  # title line pitch
                dh = ds * LINE_HEIGHT  # description line pitch
                lead = LEADER_PX * text_scale
                elbow = [lead, lead, 0]
                knee = [lead + 8 * text_scale, lead, 0]
                sc.Line([0, 0, 0], elbow, color=c, thickness=style.line)
                sc.Line(elbow, knee, color=c, thickness=style.line)
                pad = 4 * text_scale
                width = max(tag_text_widths(title, desc_lines, fs, ds)) + 2 * pad
                height = th + dh * len(desc_lines) + 2 * pad
                x0 = knee[0] + 2 * text_scale
                first_h = th if title else dh
                top = lead + first_h / 2 + pad  # the first line is centred on the leader knee
                cy = top - height / 2
                with sc.Transform(transform=sc.Matrix44.get_translation_matrix(x0 + width / 2, cy, -1)):
                    sc.Rectangle(width, height, color=cl(*BOX_BG))
                    sc.Rectangle(width, height, color=c, wireframe=True, thickness=style.border)
                y = top - pad
                if title:
                    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(x0 + pad, y - th / 2, 0)):
                        sc.Label(title, color=c, size=fs, alignment=ui.Alignment.LEFT_CENTER)
                    y -= th
                desc_color = cl(0.85, 0.85, 0.85, 1.0)
                for line in desc_lines:
                    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(x0 + pad, y - dh / 2, 0)):
                        sc.Label(line, color=desc_color, size=ds, alignment=ui.Alignment.LEFT_CENTER)
                    y -= dh


def tag_text_widths(title: str, desc_lines: List[str], fs: float, ds: float) -> List[float]:
    """Estimated widths of every tag line (title at ``fs``, description lines at ``ds``)."""
    widths = [text_width(title, fs)] if title else []
    widths += [text_width(line, ds) for line in desc_lines]
    return widths or [0.0]


def _double_sided(counts: List[int], indices: List[int]) -> Tuple[List[int], List[int]]:
    """Append reversed copies of every face (culling-independent)."""
    out_c = list(counts)
    out_i = list(indices)
    pos = 0
    for n in counts:
        out_c.append(n)
        out_i.extend(reversed(indices[pos:pos + n]))
        pos += n
    return out_c, out_i


def _build_arrow(sc, ui, tail, tip, color, size: float, style: _Style = NORMAL_STYLE) -> None:
    """World-size arrow: shaft line + cone head (PolygonMesh) ending at ``tip``."""
    d = _norm(_sub(tip, tail))
    head_len = 0.25 * size
    radius = 0.08 * size
    base = _sub(tip, _mul(d, head_len))
    sc.Line(tail, base, color=ui.color(*_rgba(color)), thickness=style.arrow)
    u, v = _perp_basis(d)
    positions = [list(tip), list(base)]
    for i in range(CONE_SEGMENTS):
        a = 2.0 * math.pi * i / CONE_SEGMENTS
        positions.append(_add(base, _add(_mul(u, radius * math.cos(a)), _mul(v, radius * math.sin(a)))))
    counts, indices = [], []
    for i in range(CONE_SEGMENTS):
        a = 2 + i
        b = 2 + (i + 1) % CONE_SEGMENTS
        counts += [3, 3]
        indices += [0, a, b, 1, b, a]
    counts, indices = _double_sided(counts, indices)
    rgba = _rgba(color)
    sc.PolygonMesh(positions, [rgba] * len(positions), counts, indices)


def _build_plane(sc, ui, ann, up_hint, color=None, style: _Style = NORMAL_STYLE) -> list:
    """Translucent rectangle + border + short normal arrow. Returns the label anchor."""
    o = ann["origin"]
    w, h = ann["size"]
    n, hor, ver = plane_basis(ann["normal"], ann.get("up", up_hint))
    hx, vy = _mul(hor, w / 2), _mul(ver, h / 2)
    corners = [
        _sub(_sub(o, hx), vy),
        _sub(_add(o, hx), vy),
        _add(_add(o, hx), vy),
        _add(_sub(o, hx), vy),
    ]
    color = ann["color"] if color is None else color
    counts, indices = _double_sided([4], [0, 1, 2, 3])
    sc.PolygonMesh(corners, [_rgba(color, style.alpha)] * 4, counts, indices)
    c = ui.color(*_rgba(color))
    for i in range(4):
        sc.Line(corners[i], corners[(i + 1) % 4], color=c, thickness=style.line)
    arrow_len = 0.35 * min(w, h)
    _build_arrow(sc, ui, o, _add(o, _mul(n, arrow_len)), color, arrow_len, style)
    return _add(o, vy)


def build_annotation(sc, ui, ann: dict, text_scale: float, up_hint, show_description: bool = True,
                     selected: bool = False) -> Optional[list]:
    """Build one annotation in the current container. Returns its anchor (culling point)."""
    kind = ann["kind"]
    color = highlight_color(ann["color"]) if selected else ann["color"]
    style = SELECTED_STYLE if selected else NORMAL_STYLE
    title = ann.get("label", "")
    desc = ann.get("description", "") if show_description else ""
    if kind == "label":
        p = ann["position"]
        _build_tag(sc, ui, p, title, desc, color, text_scale, dot=True, style=style)
        return p
    if kind in ("inlet", "outlet"):
        p = ann["position"]
        d = _norm(ann["direction"])
        size = ann["size"]
        if kind == "inlet":
            tail, tip = _sub(p, _mul(d, size)), p
            tag_at = tail
        else:
            tail, tip = p, _add(p, _mul(d, size))
            tag_at = tip
        _build_arrow(sc, ui, tail, tip, color, size, style)
        _build_tag(sc, ui, tag_at, title, desc, color, text_scale, dot=False, style=style)
        return p
    if kind == "plane":
        anchor = _build_plane(sc, ui, ann, up_hint, color, style)
        _build_tag(sc, ui, anchor, title, desc, color, text_scale, dot=False, style=style)
        return ann["origin"]
    return None


def _fmt_area(a: float) -> str:
    a = float(a)
    return f"{a:.3f}" if a == 0.0 or abs(a) >= 0.001 else f"{a:.3e}"


def face_label_lines(face: dict) -> List[str]:
    """Info box lines of a selected face: centre, normal, area (3 decimals)."""
    c, n = face["center"], face["normal"]
    return [
        f"({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})",
        f"n ({n[0]:.3f}, {n[1]:.3f}, {n[2]:.3f})",
        f"A = {_fmt_area(face['area'])}",
    ]


def build_face(sc, ui, face: dict, text_scale: float) -> list:
    """Selected face: cyan outline (world), screen-size cross + info box at the centre. Returns the centre."""
    c = ui.color(*_rgba(FACE_COLOR))
    pts = face.get("outline") or []
    if len(pts) >= 2:
        for i in range(len(pts)):
            sc.Line(pts[i], pts[(i + 1) % len(pts)], color=c, thickness=FACE_LINE_PX)
    center = face["center"]
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*center)):
        with sc.Transform(scale_to=sc.Space.SCREEN):
            with sc.Transform(look_at=sc.Transform.LookAt.CAMERA):
                k = FACE_CROSS_PX * text_scale
                sc.Line([-k, 0, 0], [k, 0, 0], color=c, thickness=2.5)
                sc.Line([0, -k, 0], [0, k, 0], color=c, thickness=2.5)
                lines = face_label_lines(face)
                fs = BASE_FONT_PX * text_scale * 0.9
                lh = fs * LINE_HEIGHT
                pad = 4 * text_scale
                width = max(text_width(line, fs) for line in lines) + 2 * pad
                height = lh * len(lines) + 2 * pad
                x0 = k + 6 * text_scale
                top = -4 * text_scale  # box to the lower right of the cross
                with sc.Transform(transform=sc.Matrix44.get_translation_matrix(x0 + width / 2, top - height / 2, -1)):
                    sc.Rectangle(width, height, color=ui.color(*BOX_BG))
                    sc.Rectangle(width, height, color=c, wireframe=True, thickness=1.5)
                y = top - pad
                for line in lines:
                    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(x0 + pad, y - lh / 2, 0)):
                        sc.Label(line, color=c, size=fs, alignment=ui.Alignment.LEFT_CENTER)
                    y -= lh
    return center


# ---------------------------------------------------------------------- per-viewport scene item
class _AnnotationScene:
    """Scene item created by the viewport registry for one viewport (inside its sc.Transform)."""

    def __init__(self, desc: dict, drawing: "OverlayDrawing"):
        from omni.ui import scene as sc

        self._drawing = weakref.ref(drawing)
        self._viewport_api = (desc or {}).get("viewport_api")
        self._items: List[Tuple[object, Sequence[float], bool]] = []
        self._root = sc.Transform()
        self._pick_root = sc.Transform()  # holds the click-capture screen while armed
        self._pick_screen = None
        self._selection_block = None
        self._visible = True
        self._view_sub = None
        self.selected_built: Optional[str] = None
        self.face_built = False
        self._view_factor = view_text_factor(self._viewport_api)
        self.built_count = 0
        self.text_scale_used = 1.0
        drawing._attach(self)
        api = self._viewport_api
        if api is not None and callable(getattr(api, "subscribe_to_view_change", None)):
            try:
                self._view_sub = api.subscribe_to_view_change(self._on_view_changed)
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] view change subscription failed: {e}")

    # registry item protocol -------------------------------------------------
    @property
    def name(self) -> str:
        return "Annotations"

    @property
    def categories(self):
        return ["annotation"]

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        if self._root is not None:
            self._root.visible = self._visible

    def destroy(self) -> None:
        self.set_armed(False)
        pick_root, self._pick_root = self._pick_root, None
        if pick_root is not None:
            pick_root.clear()
        sub, self._view_sub = self._view_sub, None
        if sub is not None and callable(getattr(sub, "destroy", None)):
            try:
                sub.destroy()
            except Exception:  # noqa: BLE001
                pass
        drawing = self._drawing()
        if drawing is not None:
            drawing._detach(self)
        root, self._root = self._root, None
        if root is not None:
            root.clear()
        self._items = []
        self._viewport_api = None

    # drawing ----------------------------------------------------------------
    def rebuild(self, anns: List[dict], global_visible: bool, text_scale: float, up_hint,
                show_description: bool = True, selected_id: Optional[str] = None,
                face: Optional[dict] = None) -> None:
        if self._root is None:
            return
        import omni.ui as ui
        from omni.ui import scene as sc

        self._root.clear()
        self._items = []
        self.built_count = 0
        self.selected_built = None
        self.face_built = False
        self._view_factor = view_text_factor(self._viewport_api)
        text_scale = text_scale * self._view_factor
        self.text_scale_used = text_scale
        if global_visible:
            with self._root:
                for ann in anns:
                    if not ann.get("visible", True):
                        continue
                    selected = selected_id is not None and ann.get("id") == selected_id
                    if selected:
                        self.selected_built = selected_id
                    xf = sc.Transform()
                    with xf:
                        try:
                            anchor = build_annotation(sc, ui, ann, text_scale, up_hint, show_description, selected)
                        except Exception as e:  # noqa: BLE001
                            carb.log_error(f"[{EXT_ID}] drawing {ann.get('id')!r} failed: {e}")
                            anchor = None
                    if anchor is not None:
                        self._items.append((xf, anchor, True))
                    self.built_count += 1
                if face is not None:
                    xf = sc.Transform()
                    with xf:
                        try:
                            anchor = build_face(sc, ui, face, text_scale)
                            self.face_built = True
                        except Exception as e:  # noqa: BLE001
                            carb.log_error(f"[{EXT_ID}] drawing the selected face failed: {e}")
                            anchor = None
                    if anchor is not None:
                        self._items.append((xf, anchor, True))
        self._apply_culling()

    def _on_view_changed(self, *_args) -> None:
        self._apply_culling()
        # resolution changes also arrive here: rebuild when the small-view text factor flips
        if view_text_factor(self._viewport_api) != self._view_factor:
            drawing = self._drawing()
            if drawing is not None:
                drawing.mark_dirty()

    def _apply_culling(self) -> None:
        """Hide annotations whose anchor is behind a perspective camera."""
        api = self._viewport_api
        if api is None or not self._items:
            return
        try:
            from pxr import Gf

            view = api.view
            proj = api.projection
            perspective = abs(proj[3][3]) < 1e-9
        except Exception:  # noqa: BLE001
            return
        items = []
        for xf, anchor, shown in self._items:
            vis = True
            if perspective:
                z = view.Transform(Gf.Vec3d(anchor[0], anchor[1], anchor[2]))[2]
                vis = z < 0.0  # camera looks down -Z
            if vis != shown:
                xf.visible = vis
            items.append((xf, anchor, vis))
        self._items = items

    @property
    def shown_count(self) -> int:
        return sum(1 for _xf, _a, shown in self._items if shown)

    # click capture (editor "pick in view") ----------------------------------
    @property
    def viewport_api(self):
        return self._viewport_api

    @property
    def armed(self) -> bool:
        return self._pick_screen is not None

    def set_armed(self, armed: bool) -> None:
        if armed == self.armed:
            return
        if not armed:
            self._pick_screen = None
            if self._pick_root is not None:
                self._pick_root.clear()
            self._selection_block = None  # restores Kit's selection layers
            return
        if self._pick_root is None:
            return
        from omni.ui import scene as sc

        with self._pick_root:
            self._pick_screen = sc.Screen(gesture=[_make_pick_gesture(sc, weakref.ref(self))])
        self._selection_block = _block_selection(self._viewport_api)

    def _on_pick_click(self, ndc) -> bool:
        """Mouse (NDC) clicked while armed. Returns True when it landed on the image."""
        api = self._viewport_api
        drawing = self._drawing()
        if api is None or drawing is None:
            return False
        xy = ndc_to_image(api, ndc)
        if xy is None:
            return False
        drawing._clicked(api, xy[0], xy[1])
        return True


def _block_selection(viewport_api):
    """Hide Kit's selection / object-click layers on this viewport (token restores on release)."""
    if viewport_api is None:
        return None
    try:
        from omni.kit.viewport.utility import disable_selection

        return disable_selection(viewport_api)
    except Exception:  # noqa: BLE001  (no viewport window extension, fake API in tests)
        return None


def _make_pick_gesture(sc, scene_ref):
    class _PickClickGesture(sc.ClickGesture):
        def on_ended(self, *_args):
            if self.state == sc.GestureState.CANCELED:
                return
            scene = scene_ref()
            if scene is not None:
                scene._on_pick_click(self.sender.gesture_payload.mouse)

        @property
        def name(self):
            return f"{EXT_ID}.PickClickGesture"

        @property
        def priority(self):
            return PICK_GESTURE_PRIORITY

    return _PickClickGesture()


# ---------------------------------------------------------------------- manager
class OverlayDrawing:
    def __init__(self, overlay: AnnotationOverlay):
        self._overlay = overlay
        self._scenes: List[_AnnotationScene] = []
        self._dirty = True
        self._registration = None
        self._enable_hook = None
        self._click_cb: Optional[Callable] = None
        overlay._add_listener(self._on_changed)

    @property
    def scenes(self) -> List[_AnnotationScene]:
        return list(self._scenes)

    @property
    def registered(self) -> bool:
        return self._registration is not None

    def start(self) -> bool:
        """Register with omni.kit.viewport.registry now if it is loaded, and always follow its
        enable / disable afterwards (register again when it comes back). Returns True if registered now."""
        registered = self._register()
        try:
            import omni.kit.app

            mgr = omni.kit.app.get_app().get_extension_manager()
            self._enable_hook = mgr.subscribe_to_extension_enable(
                lambda *_: self._register(),
                lambda *_: self._unregister(),
                ext_name=REGISTRY_EXT,
                hook_name=f"{EXT_ID}.registry.{id(self):x}",  # unique per instance (tests make extra ones)
            )
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] cannot follow {REGISTRY_EXT} enable/disable: {e}")
        if not registered:
            carb.log_warn(f"[{EXT_ID}] {REGISTRY_EXT} not available: drawing disabled until it is enabled")
        return registered

    def _register(self) -> bool:
        if self._registration is not None:
            return True
        try:
            from omni.kit.viewport.registry import RegisterScene
        except ImportError:
            return False
        self._registration = RegisterScene(self._factory, FACTORY_ID)
        return True

    def _unregister(self) -> None:
        reg, self._registration = self._registration, None
        if reg is not None:
            try:
                reg.destroy()
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] unregister scene failed: {e}")

    def stop(self) -> None:
        self._click_cb = None
        self._overlay._remove_listener(self._on_changed)
        self._enable_hook = None
        self._unregister()
        for s in list(self._scenes):
            s.destroy()
        self._scenes = []

    def _factory(self, desc: dict):
        return _AnnotationScene(desc, self)

    def create_scene(self, desc: Optional[dict] = None) -> _AnnotationScene:
        """Build a scene item in the current sc container (what the registry does). Used by tests."""
        return _AnnotationScene(desc or {}, self)

    def _params(self):
        return (
            self._overlay._snapshot(),
            self._overlay.visible,
            text_scale_setting(),
            _stage_up(),
            show_description_setting(),
            self._overlay.selected_id,
            self._overlay._face,  # selected face (read only, no copy)
        )

    def _attach(self, scene: _AnnotationScene) -> None:
        self._scenes.append(scene)
        with _scope(M_REBUILD):
            scene.rebuild(*self._params())
        if self._click_cb is not None:
            scene.set_armed(True)

    def _detach(self, scene: _AnnotationScene) -> None:
        try:
            self._scenes.remove(scene)
        except ValueError:
            pass

    def _on_changed(self, _reason: str) -> None:
        self._dirty = True

    def mark_dirty(self) -> None:
        self._dirty = True

    # one-shot click capture on every viewport ---------------------------------
    @property
    def click_armed(self) -> bool:
        return self._click_cb is not None

    def arm_click(self, callback: Callable) -> None:
        """Capture the next left click in any viewport: ``callback(viewport_api, x, y)`` with
        normalized image coordinates (origin top-left). Re-arming replaces the callback."""
        self._click_cb = callback
        for s in list(self._scenes):
            try:
                s.set_armed(True)
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] arming click capture failed: {e}")

    def disarm_click(self) -> None:
        self._click_cb = None
        self._disarm_scenes()

    def _disarm_scenes(self) -> None:
        for s in list(self._scenes):
            try:
                s.set_armed(False)
            except Exception:  # noqa: BLE001
                pass

    def _clicked(self, viewport_api, x: float, y: float) -> None:
        """One click only. The capture screens are removed and the callback runs on the next loop
        iteration (not inside the gesture callback of the screen being removed)."""
        cb, self._click_cb = self._click_cb, None
        if cb is None:
            return

        def finish():
            if self._click_cb is None:  # not re-armed meanwhile
                self._disarm_scenes()
            try:
                cb(viewport_api, x, y)
            except Exception as e:  # noqa: BLE001
                carb.log_error(f"[{EXT_ID}] view click handler failed: {e}")

        asyncio.get_event_loop().call_soon(finish)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def flush(self) -> bool:
        """Rebuild every viewport scene if something changed. Returns True when rebuilt."""
        if not self._dirty:
            return False
        self._dirty = False
        if not self._scenes:
            return False
        params = self._params()
        with _scope(M_REBUILD):
            for s in list(self._scenes):
                try:
                    s.rebuild(*params)
                except Exception as e:  # noqa: BLE001
                    carb.log_error(f"[{EXT_ID}] rebuild failed: {e}")
        return True
