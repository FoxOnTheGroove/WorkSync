# subpanel.py — 260x105 오버레이 스타일 패널 (UVMixer_overlay 디자인 기반)
#
#   행1: key 라벨 ────────────────── ▼(최소화)
#   행2: index  [IntSlider]
#   행3: thick  [FloatSlider]
#
# 최소화 시 20x20 (버튼만 남음)

import omni.ui as ui

PANEL_W     = 260
PANEL_H     = 105
PANEL_W_MIN = 20          # 최소화 시 너비 (버튼만)
PANEL_H_MIN = 20          # 최소화 시 높이 (버튼만)

_WINDOW_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR
    | ui.WINDOW_FLAGS_NO_RESIZE
    | ui.WINDOW_FLAGS_NO_SCROLLBAR
    | ui.WINDOW_FLAGS_NO_COLLAPSE
    | ui.WINDOW_FLAGS_NO_MOVE
)


class SubPanel:

    def __init__(self, key: str = ""):
        self._key = key
        self._window = None
        self._body = None
        self._row1_content = None
        self._minimized = False
        self._index_slider = None
        self._thick_slider = None
        self._btn_min = None

    def build_ui(self, index_min: int = 1, index_max: int = 10):
        self._window = ui.Window(
            f"__subpanel_{self._key}__",
            width=PANEL_W, height=PANEL_H,
            flags=_WINDOW_FLAGS,
        )
        self._window.frame.style = {"background_color": 0xCC151515}

        with self._window.frame:
            with ui.VStack(spacing=1, style={"margin": 2}):

                # 행1: key 라벨(최소화 시 숨김) + 최소화 버튼(우상단 고정)
                with ui.HStack(height=16, spacing=3):
                    self._row1_content = ui.HStack(spacing=3)
                    with self._row1_content:
                        ui.Label(self._key, height=14,
                                 style={"color": 0xFF888888, "font_size": 10})
                    self._btn_min = ui.Button("▼", width=16, height=14,
                                              clicked_fn=self._on_toggle_minimize,
                                              style={"font_size": 10})

                # 본문(행2·행3): 최소화 시 숨김
                self._body = ui.VStack(spacing=1)
                with self._body:
                    # 행2: index 슬라이더
                    with ui.HStack(height=16, spacing=3):
                        ui.Label("index", width=42, height=14,
                                 style={"font_size": 10})
                        self._index_slider = ui.IntSlider(
                            min=index_min, max=index_max)

                    # 행3: thick 슬라이더
                    with ui.HStack(height=16, spacing=3):
                        ui.Label("thick", width=42, height=14,
                                 style={"font_size": 10})
                        self._thick_slider = ui.FloatSlider(
                            min=0.0, max=1.0, step=0.005)

    # 외부에서 콜백 연결용
    @property
    def index_model(self):
        return self._index_slider.model if self._index_slider else None

    @property
    def thick_model(self):
        return self._thick_slider.model if self._thick_slider else None

    def _on_toggle_minimize(self):
        self._minimized = not self._minimized
        self._row1_content.visible = not self._minimized
        self._body.visible = not self._minimized
        self._btn_min.text = "▲" if self._minimized else "▼"
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
        self._row1_content = None
        self._index_slider = None
        self._thick_slider = None
        self._btn_min = None
