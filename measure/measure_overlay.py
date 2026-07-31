"""뷰포트 오버레이. 그리기만 하고 판단은 전부 measure.py 가 한다.

등록된 뷰포트마다 하나씩 있으며, SceneView 가 제스처를 소유하므로 입력이
여기로 먼저 들어와 NDC 좌표로 넘어간다.
"""

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
        self._frame = frame
        self._on_hover = on_hover
        self._on_click = on_click
        self._on_label_click = on_label_click

        self._scene_view = None
        self._hover_screen = None
        self._click_screen = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._drawn = None

        self._build()

    def _build(self):
        if self._frame is None:
            carb.log_warn(f"[measure] no frame to draw '{self._viewport_id}' into")
            return

        with self._frame:
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
            carb.log_warn(f"[measure] add_scene_view failed: {exc}")

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
        """확정된 측정을 다시 그린다. 바뀐 게 없으면 건너뛴다."""
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
                colour = _SELECTED_LINE_COLOR if chosen else _LINE_COLOR
                a, b = line.start.position, line.end.position
                sc.Line(
                    (a[0], a[1], a[2]),
                    (b[0], b[1], b[2]),
                    color=colour,
                    thickness=_LINE_THICKNESS,
                )
                sc.Points(
                    [(a[0], a[1], a[2]), (b[0], b[1], b[2])],
                    colors=[_END_DOT_COLOR, _END_DOT_COLOR],
                    sizes=[_END_DOT_SIZE, _END_DOT_SIZE],
                )
                _draw_plate_label(
                    a,
                    b,
                    format_length(line.length_m),
                    on_click=self._label_clicked(line.id),
                    selected=chosen,
                )

    def set_preview(self, start, end, text):
        """첫 점과 커서 사이의 고무줄 선."""
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
        """다음 클릭이 놓일 자리를 스냅 등급 색으로 표시."""
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
        """오버레이가 그리는 영역의 픽셀 크기."""
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
        """픽 진행 중에만 뷰포트 클릭을 가로챈다."""
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
            self._scene_view = None
        self._hover_screen = None
        self._click_screen = None
        self._lines_root = None
        self._marker_root = None
        self._preview_root = None
        self._drawn = None
        self._frame = None


def _midpoint(a, b):
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)


def plate_hit_size(text: str):
    """클릭 판정용 판 크기.

    scale_to=Space.SCREEN 으로 그린 판은 screen_size 가 보고하는 픽셀
    공간과 크기가 다르다(실측 약 2배). _HIT_SCALE 이 그 보정값이다.
    """
    width, height = plate_size(text)
    return width * _HIT_SCALE, height * _HIT_SCALE


def plate_size(text: str):
    """그려지는 판 크기(화면 단위)."""
    edges = (_PLATE_PAD_X + _PLATE_BORDER) * 2, (_PLATE_PAD_Y + _PLATE_BORDER) * 2
    width = max(len(text), 1) * _PLATE_FONT_SIZE * _PLATE_CHAR_WIDTH + edges[0]
    height = _PLATE_FONT_SIZE * _PLATE_LINE_HEIGHT + edges[1]
    return width, height


def _draw_plate_label(a, b, text, on_click=None, selected=False):
    """확정된 측정의 값 표기. 카메라를 향하는 둥근 판 위에 올린다."""
    width, height = plate_size(text)
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))):
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
    """둥근 사각형 두 장을 겹쳐 그 사이 간격을 테두리로 쓴다.

    border_width 스트로크는 모서리에서 두껍게 래스터화된다.
    """
    fill = _PLATE_COLOR_SELECTED if selected else _PLATE_COLOR
    edge = _PLATE_BORDER_SELECTED if selected else _PLATE_BORDER_COLOR
    with ui.ZStack():
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
        with ui.VStack():
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
    """끌고 있는 중의 값 표기. 판 없이 흰 글자만."""
    with sc.Transform(transform=sc.Matrix44.get_translation_matrix(*_midpoint(a, b))):
        with sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA, scale_to=sc.Space.SCREEN
        ):
            _label(text, _PREVIEW_TEXT_COLOR)


def _label(text: str, color):
    sc.Label(text, alignment=ui.Alignment.CENTER, color=color, size=_LABEL_SIZE)


def _ndc_from(sender):
    """제스처 payload 에서 NDC 좌표를 꺼낸다. 버전마다 필드명이 다르다."""
    payload = getattr(sender, "gesture_payload", None)
    if payload is None:
        return None
    for name in ("mouse", "mouse_ndc", "ndc_position"):
        value = getattr(payload, name, None)
        if value is not None and len(value) >= 2:
            return (float(value[0]), float(value[1]))
    return None
