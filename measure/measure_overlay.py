"""Viewport overlay for the measure tool.

One MeasureOverlay per registered viewport. Draws only; every decision is made in
measure.py. Input arrives here first because the SceneView owns the gestures,
and is forwarded straight out through on_hover / on_click as NDC coordinates.
"""

from __future__ import annotations

import carb
import omni.ui as ui
from omni.ui import scene as sc

# Marker colour and screen-space size per snap class. Snap kinds are read as
# plain ints so this module keeps no dependency on measure.py.
_SURFACE, _EDGE, _VERTEX = 0, 1, 2

_MARKER_STYLE = {
    _SURFACE: ((0.7, 0.7, 0.7, 0.9), 6.0),
    _EDGE: ((0.4, 1.0, 0.5, 1.0), 9.0),
    _VERTEX: ((1.0, 0.8, 0.2, 1.0), 11.0),
}

_LINE_COLOR = (1.0, 1.0, 1.0, 1.0)
_PREVIEW_COLOR = (1.0, 1.0, 1.0, 0.5)
_LINE_THICKNESS = 2.0

# Selected measurement: its line goes black too, matching its plate.
_SELECTED_LINE_COLOR = (0.0, 0.0, 0.0, 1.0)

# The live preview is a scene label: cheap, and rebuilt on every mouse move.
_LABEL_SIZE = 18
_PREVIEW_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)

# The placed readout is omni.ui inside an sc.Widget, which rounds its corners
# properly and sets type better. It renders through a different path, so it
# needs a larger number than _LABEL_SIZE to look the same size on screen.
_PLATE_FONT_SIZE = 36
_PLATE_PAD_X = 10.0  # screen units left and right of the text
_PLATE_PAD_Y = 5.0  # above and below
_PLATE_CHAR_WIDTH = 0.62  # of the font size, to size the widget box
# Glyphs need more room than their point size, or the padding above and below
# ends up uneven and the text reads as sitting high in the plate.
_PLATE_LINE_HEIGHT = 1.3
_PLATE_RADIUS = 10
_PLATE_BORDER = 10  # ring thickness, added outside the padding

# omni.ui colours (0xAABBGGRR).
_PLATE_COLOR = 0xFFFFFFFF
_PLATE_TEXT_COLOR = 0xFF000000
_PLATE_BORDER_COLOR = 0xFF000000
_PLATE_COLOR_SELECTED = 0xFF000000
_PLATE_TEXT_SELECTED = 0xFFFFFFFF
_PLATE_BORDER_SELECTED = 0xFFFFFFFF


class MeasureOverlay:
    def __init__(
        self,
        viewport_id: str,
        viewport_api,
        frame,
        on_hover=None,
        on_click=None,
        on_label_click=None,
    ):
        self._viewport_id = viewport_id
        self._viewport_api = viewport_api
        self._frame = frame  # supplied by the caller, never looked up here
        self._on_hover = on_hover
        self._on_click = on_click
        self._on_label_click = on_label_click

        self._scene_view = None
        self._hover_screen = None
        self._click_screen = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._drawn = None  # signature of what set_lines last built

        self._build()

    # ----------------------------------------------------------------- build

    def _build(self):
        if self._frame is None:
            carb.log_warn(f"[measure] no frame to draw '{self._viewport_id}' into")
            return

        with self._frame:
            self._scene_view = sc.SceneView()
            with self._scene_view.scene:
                # Hover and click are separate Screens on purpose. Hover can
                # stay live harmlessly, but a click Screen swallows the button
                # press, so it only appears while a pick is armed and never
                # when the host owns input.
                self._hover_screen = sc.Screen(
                    gestures=[sc.HoverGesture(on_changed_fn=self._forward_hover)]
                )
                self._click_screen = sc.Screen(
                    gestures=[sc.ClickGesture(self._forward_click)]
                )
                self._click_screen.visible = False
                self._lines_root = sc.Transform()
                self._preview_root = sc.Transform()
                self._marker_root = sc.Transform()

        # Keeps the scene camera in sync with the viewport camera.
        try:
            self._viewport_api.add_scene_view(self._scene_view)
        except Exception as exc:
            carb.log_warn(f"[measure] add_scene_view failed: {exc}")

    # ----------------------------------------------------------------- input

    def _forward_click(self, sender):
        if self._on_click:
            ndc = _ndc_from(sender)
            if ndc is not None:
                self._on_click(ndc)

    def _forward_hover(self, sender):
        if self._on_hover:
            ndc = _ndc_from(sender)
            if ndc is not None:
                self._on_hover(ndc)

    def _label_clicked(self, line_id: int):
        """Gesture callback for one plate, carrying which line it belongs to."""
        if self._on_label_click is None:
            return None
        return lambda _sender, i=line_id: self._on_label_click(i)

    # ------------------------------------------------------------------ draw

    def set_lines(self, lines, format_length, selected_id=None):
        """Redraw all confirmed measurements for this viewport.

        Skipped when nothing about the set has changed, selection included: a
        refresh fires on tab switches and visibility changes too, and there is
        no point rebuilding the scene for those.
        """
        if self._lines_root is None:
            return
        drawn = [
            (line.id, tuple(line.start.position), tuple(line.end.position))
            for line in lines
        ]
        if (drawn, selected_id) == self._drawn:
            return
        self._drawn = (drawn, selected_id)

        self._lines_root.clear()
        with self._lines_root:
            for line in lines:
                chosen = line.id == selected_id
                a, b = line.start.position, line.end.position
                sc.Line(
                    (a[0], a[1], a[2]),
                    (b[0], b[1], b[2]),
                    color=_SELECTED_LINE_COLOR if chosen else _LINE_COLOR,
                    thickness=_LINE_THICKNESS,
                )
                _draw_plate_label(
                    a,
                    b,
                    format_length(line.length_m),
                    on_click=self._label_clicked(line.id),
                    selected=chosen,
                )

    def set_preview(self, start, end, text):
        """Rubber-band line between the first click and the cursor."""
        if self._preview_root is None:
            return
        self._preview_root.clear()
        if start is None or end is None:
            return
        with self._preview_root:
            sc.Line(
                (start[0], start[1], start[2]),
                (end[0], end[1], end[2]),
                color=_PREVIEW_COLOR,
                thickness=_LINE_THICKNESS,
            )
            if text:
                _draw_plain_label(start, end, text)

    def set_snap_marker(self, snap):
        """Show where the next click would land, coloured by snap class."""
        if self._marker_root is None:
            return
        self._marker_root.clear()
        if snap is None:
            return
        color, size = _MARKER_STYLE.get(int(snap.kind), _MARKER_STYLE[_SURFACE])
        p = snap.position
        with self._marker_root:
            sc.Points([(p[0], p[1], p[2])], colors=[color], sizes=[size])

    def screen_size(self):
        """(width, height) of the drawing area, in the units plates are sized in.

        The frame's computed size is in logical pixels, while
        scale_to=Space.SCREEN works in physical ones, so the DPI scale has to
        come back in. Getting this wrong scales every plate's hit area by
        exactly that factor. The render resolution is only a fallback: it is set
        independently of the widget size and is a different number again.
        """
        scale = _dpi_scale()
        for source in (self._frame, self._scene_view):
            width = getattr(source, "computed_width", 0) or 0
            height = getattr(source, "computed_height", 0) or 0
            if width > 1 and height > 1:
                return float(width) * scale, float(height) * scale
        try:
            res = self._viewport_api.resolution
            return float(res[0]), float(res[1])
        except Exception:
            return 1920.0, 1080.0

    def set_scene_visible(self, visible: bool):
        if self._scene_view is not None:
            self._scene_view.visible = visible

    def set_click_active(self, active: bool):
        """Only swallow viewport clicks while a pick is in progress."""
        if self._click_screen is not None:
            self._click_screen.visible = active
        if not active:
            self.set_snap_marker(None)

    # --------------------------------------------------------------- teardown

    def destroy(self):
        if self._scene_view is not None:
            try:
                self._viewport_api.remove_scene_view(self._scene_view)
            except Exception:
                pass
            try:
                self._scene_view.destroy()
            except Exception:
                pass
            self._scene_view = None
        self._hover_screen = None
        self._click_screen = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._drawn = None
        self._frame = None


# --------------------------------------------------------------------- utils


def _midpoint(a, b):
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)


def _dpi_scale() -> float:
    try:
        scale = float(ui.Workspace.get_dpi_scale())
    except Exception:
        return 1.0
    return scale if scale > 0.0 else 1.0


def plate_size(text: str):
    """(width, height) of a readout plate in screen units.

    Shared with measure.py, which hit-tests clicks against these rectangles:
    when another extension owns the mouse, the plate's own gesture never fires
    and the only way to press a plate is to work out where it is.
    """
    # The ring grows the plate outwards, so the text keeps the same padding
    # however thick the outline gets.
    edges = (_PLATE_PAD_X + _PLATE_BORDER) * 2, (_PLATE_PAD_Y + _PLATE_BORDER) * 2
    width = max(len(text), 1) * _PLATE_FONT_SIZE * _PLATE_CHAR_WIDTH + edges[0]
    height = _PLATE_FONT_SIZE * _PLATE_LINE_HEIGHT + edges[1]
    return width, height


def _draw_plate_label(a, b, text, on_click=None, selected=False):
    """Finished measurement: a rounded plate carrying the length.

    omni.ui inside an sc.Widget, so the corners round from a style and the text
    lays itself out. Only placed lines get one: the widget owns a render
    target, and one built per mouse move exhausts the descriptor pool, which is
    why the live preview stays a plain scene label.
    """
    width, height = plate_size(text)
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))):
        # look_at turns the plate to face the camera, scale_to holds its size on
        # screen. scale_to alone leaves it lying in world space, so it goes
        # edge-on and vanishes from most angles.
        with sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA, scale_to=sc.Space.SCREEN
        ):
            widget = sc.Widget(
                width,
                height,
                update_policy=sc.Widget.UpdatePolicy.ON_MOUSE_HOVERED,
            )
            widget.frame.set_build_fn(
                lambda t=text, s=selected, f=on_click: _build_plate(t, s, f)
            )


def _build_plate(text: str, selected: bool, on_click):
    fill = _PLATE_COLOR_SELECTED if selected else _PLATE_COLOR
    edge = _PLATE_BORDER_SELECTED if selected else _PLATE_BORDER_COLOR
    with ui.ZStack():
        # Two filled rounded rectangles, the smaller inset over the larger, so
        # the outline is the gap between them. A border_width stroke on a
        # rounded rectangle rasterises heavier where it turns the corners.
        ui.Rectangle(
            style={"background_color": edge, "border_radius": _PLATE_RADIUS},
            mouse_pressed_fn=(lambda *_: on_click(None)) if on_click else None,
        )
        ui.Rectangle(
            style={
                "background_color": fill,
                "border_radius": max(_PLATE_RADIUS - _PLATE_BORDER, 0),
                "margin": _PLATE_BORDER,
            }
        )
        # Inset by the ring as well, or a thick outline runs under the text.
        # Spacers rather than a bare alignment: a Label sizes itself to its
        # text, so centring it needs something pushing from both sides.
        with ui.VStack(style={"margin": _PLATE_BORDER}):
            ui.Spacer()
            ui.Label(
                text,
                height=0,
                alignment=ui.Alignment.CENTER,
                style={
                    "color": _PLATE_TEXT_SELECTED if selected else _PLATE_TEXT_COLOR,
                    "font_size": _PLATE_FONT_SIZE,
                },
            )
            ui.Spacer()


def _draw_plain_label(a, b, text):
    """While still dragging: plain white text, no plate."""
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))):
        with sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA, scale_to=sc.Space.SCREEN
        ):
            _label(text, _PREVIEW_TEXT_COLOR)


def _label(text: str, color):
    sc.Label(text, alignment=ui.Alignment.CENTER, color=color, size=_LABEL_SIZE)


def _ndc_from(sender):
    """Pull normalised device coords out of a gesture payload.

    Payload field names have shifted between omni.ui.scene versions, so try the
    known spellings rather than pinning one.
    """
    payload = getattr(sender, "gesture_payload", None)
    if payload is None:
        return None
    for name in ("mouse", "mouse_ndc", "ndc_position"):
        value = getattr(payload, name, None)
        if value is not None and len(value) >= 2:
            return (float(value[0]), float(value[1]))
    return None
