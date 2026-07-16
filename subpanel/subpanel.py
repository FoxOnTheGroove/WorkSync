# subpanel.py — 참고용 패널 (180x80). mixer 오버레이의 슬라이더 컴포넌트를 이식.
#
#   행1 (y0,h16):  [최소화10x10] Path line(폰트10, h15, 윗점)
#   행2 (y16,h32): 8 | Iteration(폰트12) | x72 좌'1' | x93 슬라이더58 | 우'20'(-8)
#   행3 (y48,h32): 8 | Thickness       | x72 좌'0.5'| x93 슬라이더58 | 우'1.0'
#
# 아이콘: 이 파일과 같은 subpanel/ 아래 data/icons/ 에 둔다 (파일은 별도 준비).
#   arrow.png / arrow_r.png  (최소화 화살표, 펼침/접힘)
#   ov_n.png  / ov_h.png     (슬라이더 핸들 기본/hover)

import os

import omni.ui as ui

_ICON_DIR     = os.path.join(os.path.dirname(__file__), "data", "icons")
_ICON_ARROW   = os.path.join(_ICON_DIR, "arrow.png")
_ICON_ARROW_R = os.path.join(_ICON_DIR, "arrow_r.png")
_ICON_OV_N    = os.path.join(_ICON_DIR, "ov_n.png")
_ICON_OV_H    = os.path.join(_ICON_DIR, "ov_h.png")

# ── 패널 치수 (전체 180x80) ─────────────────────────────────────────────
PANEL_W = 180
PANEL_H = 80
PANEL_W_MIN = 16
PANEL_H_MIN = 16

_ROW1_H  = 16        # 타이틀
_ROW_H   = 32        # 행2 / 행3 각각 (합 64)

# 행2/3 공통 x 정렬 (좌→우 폭 누적 = 180)
_MARGIN_L = 8
_LABEL_W  = 72 - 8         # Iteration/Thickness 라벨 폭 (x8→72)
_LIDX_W   = 93 - 72        # 좌 인덱스 폭 (x72→93)
_SLIDER_W = 58            # 슬라이더 (x93→151)
_RIDX_W   = 172 - 151     # 우 인덱스 폭 (x151→172)
_MARGIN_R = 8

_TITLE_BG = 0xFF000000      # 타이틀바: 진한 검정
_BODY_BG  = 0xFF3C3C3C      # 본문: 진한 회색
_WHITE    = 0xFFFFFFFF

_SLIDER_TRACK_BG = 0xFF6A6A6A     # #6a6a6a
_SLIDER_FILL_BG  = 0xFFF18D95     # #958df1 (0xAABBGGRR)

_LABEL_FONT = 12
_INDEX_FONT = 10
_TITLE_FONT = 10

_WINDOW_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR
    | ui.WINDOW_FLAGS_NO_BACKGROUND
    | ui.WINDOW_FLAGS_NO_RESIZE
    | ui.WINDOW_FLAGS_NO_SCROLLBAR
    | ui.WINDOW_FLAGS_NO_COLLAPSE
    | ui.WINDOW_FLAGS_NO_MOVE
)

_STYLE = {
    "Rectangle":            {"border_width": 0, "border_color": 0x00000000},
    "Rectangle::title_bg":  {"background_color": _TITLE_BG},
    "Rectangle::body_bg":   {"background_color": _BODY_BG},
    "Button":               {"background_color": 0x00000000, "color": _WHITE,
                             "margin": 0, "padding": 0, "border_width": 0},
    "Label":                {"color": _WHITE},
}


def _vcenter(width, factory):
    """고정 크기 위젯을 행 높이 안에서 세로 중앙에 배치하고 반환."""
    with ui.VStack(width=width):
        ui.Spacer()
        w = factory()
        ui.Spacer()
    return w


# ──────────────────────────────────────────────────────────────────────
# 슬라이더 컴포넌트 (mixer UVMixer_overlay 에서 이식)
#   트랙 w x 4 (#6a6a6a) + 채움(#958df1) + 10x10 이미지 핸들(ov_n/ov_h).
#   hover는 핸들 위 여부만(press와 무관). .model(SimpleFloatModel) 노출.
# ──────────────────────────────────────────────────────────────────────

class ImageSlider:

    def __init__(self, width=58, track_h=4, knob=10):
        self._w, self._th, self._ks = width, track_h, knob
        self._dragging = False
        self._hovering = False
        self.model = ui.SimpleFloatModel(0.0)
        self.model.add_value_changed_fn(lambda m: self._update_visual())
        self._build()
        self._update_visual()

    def _build(self):
        with ui.ZStack(width=self._w, height=self._ks):
            # 드래그 안전망 (맨 아래, 영구): move/release만
            self._catcher = ui.Rectangle(style={"background_color": 0x00000000})
            self._catcher.set_mouse_moved_fn(self._on_move)
            self._catcher.set_mouse_released_fn(self._on_release)

            # 트랙 + 채움 (세로 중앙)
            with ui.VStack():
                ui.Spacer()
                with ui.ZStack(height=self._th):
                    ui.Rectangle(style={"background_color": _SLIDER_TRACK_BG,
                                        "border_radius": self._th / 2})
                    with ui.Placer():
                        self._fill = ui.Rectangle(
                            width=0, height=self._th,
                            style={"background_color": _SLIDER_FILL_BG,
                                   "border_radius": self._th / 2})
                ui.Spacer()

            # 핸들 (Placer.offset_x). n/h 미리 로드 후 visible 토글.
            self._knob_placer = ui.Placer(width=self._w, height=self._ks)
            with self._knob_placer:
                with ui.ZStack(width=self._ks, height=self._ks):
                    self._knob_n = ui.Image(_ICON_OV_N, width=self._ks,
                                            height=self._ks,
                                            fill_policy=ui.FillPolicy.STRETCH)
                    self._knob_h = ui.Image(_ICON_OV_H, width=self._ks,
                                            height=self._ks,
                                            fill_policy=ui.FillPolicy.STRETCH,
                                            visible=False)
                    self._hit = ui.Rectangle(style={"background_color": 0x00000000})
                    self._hit.set_mouse_hovered_fn(self._on_hover)
                    self._hit.set_mouse_pressed_fn(self._on_press)
                    self._hit.set_mouse_moved_fn(self._on_move)
                    self._hit.set_mouse_released_fn(self._on_release)

    def _update_visual(self):
        t = self.model.get_value_as_float()
        self._fill.visible = t > 0.0
        self._fill.width = ui.Pixel(t * self._w)
        self._knob_placer.offset_x = ui.Pixel(t * (self._w - self._ks))

    def _set_knob_img(self):
        self._knob_h.visible = self._hovering
        self._knob_n.visible = not self._hovering

    def _t_from_screen_x(self, x):
        local = x - self._catcher.screen_position_x - self._ks / 2
        span = self._w - self._ks
        return max(0.0, min(1.0, local / span)) if span > 0 else 0.0

    def _on_hover(self, hovered):
        self._hovering = hovered
        self._set_knob_img()

    def _on_press(self, x, y, button, modifier):
        if button == 0:
            self._dragging = True

    def _on_move(self, x, y, modifier, pressed):
        if self._dragging:
            self.model.set_value(self._t_from_screen_x(x))

    def _on_release(self, x, y, button, modifier):
        if button == 0:
            self._dragging = False


# ──────────────────────────────────────────────────────────────────────

class SubPanel:

    def __init__(self, title: str = "Path line"):
        self._title = title
        self._window = None
        self._body = None
        self._title_content = None
        self._minimized = False
        self._btn_min = None
        self.iter_slider = None
        self.thick_slider = None

    def build_ui(self):
        self._window = ui.Window(f"__subpanel_{self._title}__",
                                 width=PANEL_W, height=PANEL_H,
                                 flags=_WINDOW_FLAGS)
        self._window.padding_x = 0
        self._window.padding_y = 0

        with self._window.frame:
            with ui.VStack(spacing=0, style=_STYLE):

                # ── 행1: 타이틀 ──────────────────────────────────
                with ui.ZStack(height=_ROW1_H):
                    ui.Rectangle(name="title_bg")
                    with ui.HStack():
                        ui.Spacer(width=3)
                        self._btn_min = _vcenter(10, lambda: ui.Button(
                            "", width=10, height=10, name="min_btn",
                            alignment=ui.Alignment.CENTER,
                            clicked_fn=self._on_toggle_minimize,
                            style={"image_url": _ICON_ARROW,
                                   "fill_policy": ui.FillPolicy.STRETCH}))
                        ui.Spacer(width=2)
                        self._title_content = ui.HStack()
                        with self._title_content:
                            with ui.VStack():                 # 윗점 정렬
                                ui.Label(self._title, height=15,
                                         style={"font_size": _TITLE_FONT})
                                ui.Spacer()

                # ── 본문 (행2 + 행3) ─────────────────────────────
                self._body = ui.ZStack()
                with self._body:
                    ui.Rectangle(name="body_bg")
                    with ui.VStack(spacing=0):
                        self.iter_slider = self._slider_row("Iteration", "1", "20")
                        self.thick_slider = self._slider_row("Thickness", "0.5", "1.0")

    def _slider_row(self, label, left_idx, right_idx):
        """행2/행3 공통 레이아웃. 반환: 그 행의 ImageSlider."""
        with ui.HStack(height=_ROW_H):
            ui.Spacer(width=_MARGIN_L)
            ui.Label(label, width=_LABEL_W, height=_ROW_H,
                     alignment=ui.Alignment.LEFT_CENTER,
                     style={"font_size": _LABEL_FONT})
            ui.Label(left_idx, width=_LIDX_W, height=_ROW_H,
                     alignment=ui.Alignment.LEFT_CENTER,
                     style={"font_size": _INDEX_FONT})
            slider = _vcenter(_SLIDER_W, lambda: ImageSlider(width=_SLIDER_W))
            ui.Label(right_idx, width=_RIDX_W, height=_ROW_H,
                     alignment=ui.Alignment.RIGHT_CENTER,
                     style={"font_size": _INDEX_FONT})
            ui.Spacer(width=_MARGIN_R)
        return slider

    def _on_toggle_minimize(self):
        self._minimized = not self._minimized
        self._title_content.visible = not self._minimized
        self._body.visible = not self._minimized
        self._btn_min.style = {
            "image_url": _ICON_ARROW_R if self._minimized else _ICON_ARROW,
            "fill_policy": ui.FillPolicy.STRETCH,
        }
        if self._minimized:
            self._window.width = PANEL_W_MIN
            self._window.height = PANEL_H_MIN
        else:
            self._window.width = PANEL_W
            self._window.height = PANEL_H

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
        self._body = None
        self._title_content = None
        self._btn_min = None
        self.iter_slider = None
        self.thick_slider = None
