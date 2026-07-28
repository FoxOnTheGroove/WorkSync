"""Viewport overlay for the measure tool.

One MeasureOverlay per enabled viewport. Draws only; every decision is made in
measure.py. Input arrives here first because the SceneView owns the gestures,
and is forwarded straight out through on_hover / on_click as NDC coordinates.
"""

from __future__ import annotations

import carb
import omni.ui as ui
from omni.ui import scene as sc

# Marker colour and screen-space size per snap class. Snap kinds are read as
# plain ints so this module keeps no dependency on measure.py.
_SURFACE, _MIDPOINT, _EDGE, _VERTEX = 0, 1, 2, 3

_MARKER_STYLE = {
    _SURFACE: ((0.7, 0.7, 0.7, 0.9), 6.0),
    _MIDPOINT: ((0.4, 0.9, 1.0, 1.0), 8.0),
    _EDGE: ((0.4, 1.0, 0.5, 1.0), 9.0),
    _VERTEX: ((1.0, 0.8, 0.2, 1.0), 11.0),
}

_LINE_COLOR = (1.0, 1.0, 1.0, 1.0)
_PREVIEW_COLOR = (1.0, 1.0, 1.0, 0.5)
_LINE_THICKNESS = 2.0


class MeasureOverlay:
    def __init__(
        self, viewport_id: str, viewport_api, window, on_hover=None, on_click=None
    ):
        self._viewport_id = viewport_id
        self._viewport_api = viewport_api
        self._window = window
        self._on_hover = on_hover
        self._on_click = on_click

        self._scene_view = None
        self._frame = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None

        self._build()

    # ----------------------------------------------------------------- build

    def _build(self):
        if self._window is None:
            carb.log_warn(f"[measure] no viewport window for '{self._viewport_id}'")
            return

        self._frame = self._window.get_frame(f"measure.overlay.{self._viewport_id}")
        with self._frame:
            self._scene_view = sc.SceneView()
            with self._scene_view.scene:
                # Screen covers the viewport and is what receives the gestures.
                sc.Screen(
                    gestures=[
                        sc.ClickGesture(self._forward_click),
                        sc.HoverGesture(on_changed_fn=self._forward_hover),
                    ]
                )
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

    # ------------------------------------------------------------------ draw

    def set_lines(self, lines, format_length):
        """Redraw all confirmed measurements for this viewport."""
        if self._lines_root is None:
            return
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
                _draw_label(a, b, format_length(line.length_m), _LINE_COLOR)

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
                _draw_label(start, end, text, _PREVIEW_COLOR)

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
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._frame = None
        self._window = None


# --------------------------------------------------------------------- utils


def _draw_label(a, b, text, color):
    """Length label at the midpoint of the segment."""
    mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*mid)):
        sc.Label(text, alignment=ui.Alignment.CENTER, color=color, size=18)


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
