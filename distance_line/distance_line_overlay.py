from __future__ import annotations

import carb
import omni.ui as ui
from omni.ui import scene as sc

_SURFACE, _EDGE, _VERTEX = 0, 1, 2

_MARKER_STYLE = {
    _SURFACE: ((0.7, 0.7, 0.7, 0.9), 6.0),
    _EDGE: ((0.4, 1.0, 0.5, 1.0), 9.0),
    _VERTEX: ((1.0, 0.8, 0.2, 1.0), 11.0),
}

_LINE_COLOR = (1.0, 1.0, 1.0, 1.0)
_PREVIEW_COLOR = (1.0, 1.0, 1.0, 0.5)
_LINE_THICKNESS = 2.0
_END_DOT_SIZE = 8.0
_END_DOT_COLOR = (1.0, 1.0, 1.0, 1.0)

_SELECTED_LINE_COLOR = (0.0, 0.0, 0.0, 1.0)

_LABEL_SIZE = 18
_PREVIEW_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)

_PLATE_FONT_SIZE = 36
_PLATE_PAD_X = 10.0
_PLATE_PAD_Y = 5.0
_PLATE_CHAR_WIDTH = 0.62
_PLATE_LINE_HEIGHT = 1.3
_PLATE_RADIUS = 10
_PLATE_BORDER = 5

_HIT_SCALE = 0.5

_PLATE_COLOR = 0xFFFFFFFF
_PLATE_TEXT_COLOR = 0xFF000000
_PLATE_BORDER_COLOR = 0xFF000000
_PLATE_COLOR_SELECTED = 0xFF000000
_PLATE_TEXT_SELECTED = 0xFFFFFFFF
_PLATE_BORDER_SELECTED = 0xFFFFFFFF


class DistanceLineOverlay:
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
        self._frame = frame
        self._on_hover = on_hover
        self._on_click = on_click
        self._on_label_click = on_label_click

        self._clip = None
        self._scene_view = None
        self._hover_screen = None
        self._click_screen = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._shape = None
        self._selected = None
        self._items = {}
        self._free_slots = []
        self._preview = None
        self._marker = None

        self._build()

    def _build(self):
        if self._frame is None:
            carb.log_warn(f"[distance_line] no frame to draw '{self._viewport_id}' into")
            return

        with self._frame:
            self._clip = _clipping_frame()
            with self._clip:
                self._scene_view = sc.SceneView()
            with self._scene_view.scene:
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

        try:
            self._viewport_api.add_scene_view(self._scene_view)
        except Exception as exc:
            carb.log_warn(f"[distance_line] add_scene_view failed: {exc}")

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
        if self._on_label_click is None:
            return None
        return lambda _sender, i=line_id: self._on_label_click(i)

    def set_lines(self, lines, format_length, selected_id=None):
        """바뀐 직선만 다시 만든다.

        판은 sc.Widget 이라 하나가 GPU 텍스처 하나다. 전부 부수고 다시 만들면
        누적 할당이 직선 개수의 제곱으로 늘어 descriptor pool 이 고갈된다.
        """
        if self._lines_root is None:
            return
        shape = {
            line.id: (
                tuple(line.start.position),
                tuple(line.end.position),
                format_length(line.length_m),
            )
            for line in lines
        }
        known = self._shape or {}
        for line_id in [i for i in list(self._items) if shape.get(i) != known.get(i)]:
            self._release(line_id)
        self._shape = shape

        for line in lines:
            if line.id not in self._items:
                self._add(line, shape[line.id][2], line.id == selected_id)

        if selected_id != self._selected:
            previous, self._selected = self._selected, selected_id
            for line_id in (previous, selected_id):
                if line_id in self._items:
                    self._reselect(line_id, line_id == selected_id, lines)

    def _release(self, line_id):
        """이 직선의 씬 아이템을 버리고 슬롯은 다음 직선을 위해 남겨 둔다."""
        slot, _curve, _plate = self._items.pop(line_id)
        slot.clear()
        self._free_slots.append(slot)

    def _slot(self):
        if self._free_slots:
            return self._free_slots.pop()
        with self._lines_root:
            return sc.Transform()

    def _add(self, line, text, selected):
        slot = self._slot()
        a, b = line.start.position, line.end.position
        with slot:
            curve = sc.Line(
                (a[0], a[1], a[2]),
                (b[0], b[1], b[2]),
                color=_SELECTED_LINE_COLOR if selected else _LINE_COLOR,
                thickness=_LINE_THICKNESS,
            )
            sc.Points(
                [(a[0], a[1], a[2]), (b[0], b[1], b[2])],
                colors=[_END_DOT_COLOR, _END_DOT_COLOR],
                sizes=[_END_DOT_SIZE, _END_DOT_SIZE],
            )
            plate = _draw_plate_label(
                a,
                b,
                text,
                on_click=self._label_clicked(line.id),
                selected=selected,
            )
        self._items[line.id] = (slot, curve, plate)

    def _reselect(self, line_id, chosen, lines):
        """색만 갈아끼운다. 판이 다시 칠해지지 않으면 그 하나만 새로 만든다."""
        _slot, curve, plate = self._items[line_id]
        try:
            curve.color = _SELECTED_LINE_COLOR if chosen else _LINE_COLOR
            if plate.set_selected(chosen):
                return
        except Exception as exc:
            carb.log_warn(f"[distance_line] in-place recolour failed: {exc}")
        line = next((ln for ln in lines if ln.id == line_id), None)
        if line is None:
            return
        self._release(line_id)
        self._add(line, self._shape[line_id][2], chosen)

    def set_preview(self, start, end, text):
        """고무줄 선. hover 마다 만들지 않고 좌표만 갈아끼운다."""
        if self._preview_root is None:
            return
        if start is None or end is None:
            self._preview_root.visible = False
            return
        a = (start[0], start[1], start[2])
        b = (end[0], end[1], end[2])
        if self._preview is not None:
            line, holder, label = self._preview
            try:
                line.start, line.end = a, b
                holder.transform = sc.Matrix44.get_translation_matrix(*_midpoint(a, b))
                label.text = text or ""
                self._preview_root.visible = True
                return
            except Exception as exc:
                carb.log_warn(f"[distance_line] preview update failed: {exc}")
                self._preview = None

        self._preview_root.clear()
        with self._preview_root:
            line = sc.Line(a, b, color=_PREVIEW_COLOR, thickness=_LINE_THICKNESS)
            holder = sc.Transform(
                transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))
            )
            with holder:
                with sc.Transform(
                    look_at=sc.Transform.LookAt.CAMERA, scale_to=sc.Space.SCREEN
                ):
                    label = _label(text or "", _PREVIEW_TEXT_COLOR)
        self._preview = (line, holder, label)
        self._preview_root.visible = True

    def set_snap_marker(self, snap):
        """스냅 자리 표시. 마찬가지로 점 하나를 재사용한다."""
        if self._marker_root is None:
            return
        if snap is None:
            self._marker_root.visible = False
            return
        color, size = _MARKER_STYLE.get(int(snap.kind), _MARKER_STYLE[_SURFACE])
        p = snap.position
        spot = [(p[0], p[1], p[2])]
        if self._marker is not None:
            try:
                self._marker.positions = spot
                self._marker.colors = [color]
                self._marker.sizes = [size]
                self._marker_root.visible = True
                return
            except Exception as exc:
                carb.log_warn(f"[distance_line] marker update failed: {exc}")
                self._marker = None

        self._marker_root.clear()
        with self._marker_root:
            self._marker = sc.Points(spot, colors=[color], sizes=[size])
        self._marker_root.visible = True

    def screen_size(self):
        for source in (self._frame, self._scene_view):
            width = getattr(source, "computed_width", 0) or 0
            height = getattr(source, "computed_height", 0) or 0
            if width > 1 and height > 1:
                return float(width), float(height)
        try:
            res = self._viewport_api.resolution
            return float(res[0]), float(res[1])
        except Exception:
            return 1920.0, 1080.0

    def set_scene_visible(self, visible: bool):
        if self._scene_view is not None:
            self._scene_view.visible = visible

    def set_click_active(self, active: bool):
        if self._click_screen is not None:
            self._click_screen.visible = active
        if not active:
            self.set_snap_marker(None)

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
            self._clip = None
        self._scene_view = None
        self._hover_screen = None
        self._click_screen = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._shape = None
        self._selected = None
        self._items = {}
        self._free_slots = []
        self._preview = None
        self._marker = None
        self._frame = None


def _clipping_frame():
    """뷰포트 밖으로 나간 부분을 잘라낸다. 안 자르면 옆 뷰포트 위에 그려진다."""
    try:
        return ui.Frame(horizontal_clipping=True, vertical_clipping=True)
    except TypeError:
        frame = ui.Frame()
        frame.horizontal_clipping = True
        frame.vertical_clipping = True
        return frame


def _midpoint(a, b):
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)


def plate_hit_size(text: str):
    width, height = plate_size(text)
    return width * _HIT_SCALE, height * _HIT_SCALE


def plate_size(text: str):
    edges = (_PLATE_PAD_X + _PLATE_BORDER) * 2, (_PLATE_PAD_Y + _PLATE_BORDER) * 2
    width = max(len(text), 1) * _PLATE_FONT_SIZE * _PLATE_CHAR_WIDTH + edges[0]
    height = _PLATE_FONT_SIZE * _PLATE_LINE_HEIGHT + edges[1]
    return width, height


def _draw_plate_label(a, b, text, on_click=None, selected=False):
    width, height = plate_size(text)
    plate = _Plate(text, selected, on_click)
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))):
        with sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA, scale_to=sc.Space.SCREEN
        ):
            widget = sc.Widget(
                width,
                height,
                update_policy=sc.Widget.UpdatePolicy.ON_MOUSE_HOVERED,
            )
            widget.frame.set_build_fn(plate.build)
            plate.attach(widget)
    return plate


class _Plate:

    def __init__(self, text: str, selected: bool, on_click):
        self._text = text
        self._selected = selected
        self._on_click = on_click
        self._widget = None
        self._outer = None
        self._inner = None
        self._label = None

    def attach(self, widget):
        self._widget = widget

    def build(self):
        click = self._on_click
        with ui.ZStack():
            self._outer = ui.Rectangle(
                style=self._outer_style(),
                mouse_pressed_fn=(lambda *_: click(None)) if click else None,
            )
            self._inner = ui.Rectangle(style=self._inner_style())
            with ui.VStack():
                ui.Spacer()
                self._label = ui.Label(
                    self._text,
                    height=0,
                    alignment=ui.Alignment.CENTER,
                    style=self._label_style(),
                )
                ui.Spacer()

    def set_selected(self, selected: bool) -> bool:
        if selected == self._selected:
            return True
        self._selected = selected
        if self._outer is None:
            return True
        self._outer.set_style(self._outer_style())
        self._inner.set_style(self._inner_style())
        self._label.set_style(self._label_style())
        return self._repaint()

    def _repaint(self) -> bool:
        for name in ("invalidate", "invalidate_raster", "update"):
            call = getattr(self._widget, name, None)
            if call is None:
                continue
            try:
                call()
                return True
            except Exception:
                continue
        return False

    def _outer_style(self):
        edge = _PLATE_BORDER_SELECTED if self._selected else _PLATE_BORDER_COLOR
        return {"background_color": edge, "border_radius": _PLATE_RADIUS}

    def _inner_style(self):
        fill = _PLATE_COLOR_SELECTED if self._selected else _PLATE_COLOR
        return {
            "background_color": fill,
            "border_radius": max(_PLATE_RADIUS - _PLATE_BORDER, 0),
            "margin": _PLATE_BORDER,
        }

    def _label_style(self):
        return {
            "color": _PLATE_TEXT_SELECTED if self._selected else _PLATE_TEXT_COLOR,
            "font_size": _PLATE_FONT_SIZE,
        }



def _label(text: str, color):
    return sc.Label(text, alignment=ui.Alignment.CENTER, color=color, size=_LABEL_SIZE)


def _ndc_from(sender):
    payload = getattr(sender, "gesture_payload", None)
    if payload is None:
        return None
    for name in ("mouse", "mouse_ndc", "ndc_position"):
        value = getattr(payload, name, None)
        if value is not None and len(value) >= 2:
            return (float(value[0]), float(value[1]))
    return None
