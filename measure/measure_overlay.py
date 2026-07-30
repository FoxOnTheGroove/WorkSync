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

# Length readout, centred on the line: plain white while still dragging, black
# on a white billboard plate once placed. One sc.Label size for both, so the
# text is the same on screen either way.
_LABEL_SIZE = 16
_LABEL_TEXT_COLOR = (0.0, 0.0, 0.0, 1.0)
_LABEL_PLATE_COLOR = (1.0, 1.0, 1.0, 1.0)
_PREVIEW_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)
_LABEL_PAD = 10.0  # screen units around the text
_LABEL_CHAR_WIDTH = 0.62  # of the font size, enough to size the plate


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

    def set_lines(self, lines, format_length):
        """Redraw all confirmed measurements for this viewport.

        Skipped when the set is unchanged. Every label here owns a render
        target, and a refresh happens on tab switches and visibility changes
        too, so rebuilding regardless burns through the descriptor pool.
        """
        if self._lines_root is None:
            return
        drawn = [
            (line.id, tuple(line.start.position), tuple(line.end.position))
            for line in lines
        ]
        if drawn == self._drawn:
            return
        self._drawn = drawn

        self._lines_root.clear()
        with self._lines_root:
            for line in lines:
                a, b = line.start.position, line.end.position
                sc.Line(
                    (a[0], a[1], a[2]),
                    (b[0], b[1], b[2]),
                    color=_LINE_COLOR,
                    thickness=_LINE_THICKNESS,
                )
                _draw_plate_label(
                    a,
                    b,
                    format_length(line.length_m),
                    on_click=self._label_clicked(line.id),
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


def _draw_plate_label(a, b, text, on_click=None):
    """Finished measurement: black text on a white billboard plate.

    Both the plate and the text are scene shapes. Putting the text in an
    sc.Widget instead rendered it through omni.ui, which sized it differently
    from the preview's sc.Label and allocated a render target per label.

    The plate carries a click gesture, so a measurement can be switched off by
    pressing its own readout in the viewport rather than through the API.
    """
    plate_w = max(len(text), 1) * _LABEL_SIZE * _LABEL_CHAR_WIDTH + _LABEL_PAD * 2
    plate_h = _LABEL_SIZE + _LABEL_PAD * 1.5
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))):
        # look_at turns the plate to face the camera, scale_to holds its size on
        # screen. scale_to alone leaves it lying in world space, so it goes
        # edge-on and vanishes from most angles.
        with sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA, scale_to=sc.Space.SCREEN
        ):
            sc.Rectangle(
                plate_w,
                plate_h,
                color=_LABEL_PLATE_COLOR,
                wireframe=False,
                gestures=[sc.ClickGesture(on_click)] if on_click else None,
            )
            _label(text, _LABEL_TEXT_COLOR)


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
