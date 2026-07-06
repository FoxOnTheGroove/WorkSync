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

# ── 뷰포트 우하단 앵커링 (UVMixer_overlay 이식, 필요 시 주석 해제) ──────────
# import omni.kit.app
# import omni.kit.async_engine
# import morph.hytwin_viewportwidget_extension as _hytwin_vp_wg
#
# _MARGIN = 8
#
# def _find_vph(target_path: str):
#     for vph in _hytwin_vp_wg.ViewportWidgetHost.get_instances():
#         if vph.prim_header_path.rstrip("/") == target_path.rstrip("/"):
#             return vph
#     return None
#
# def _calc_panel_pos(vph, width=PANEL_W, height=PANEL_H,
#                     extra_margin_y=0):
#     frame = vph.frame
#     x = int(frame.screen_position_x + frame.computed_width  - width - _MARGIN)
#     y = int(frame.screen_position_y + frame.computed_height - height - _MARGIN - extra_margin_y)
#     return x, y
#
# 사용법:
#   __init__에서
#     self._vph = _find_vph(target_path)
#     self._reposition_task = None
#   build_ui에서 윈도우 생성 시
#     x, y = _calc_panel_pos(self._vph)
#     ui.Window(..., position_x=x, position_y=y, ...)
#   생성 직후
#     self._vph.frame.set_computed_content_size_changed_fn(self._on_viewport_resized)
#
# def _on_viewport_resized(self):
#     if self._window and self._vph:
#         if self._minimized:
#             x, y = _calc_panel_pos(self._vph, PANEL_W_MIN, PANEL_H_MIN,
#                                    extra_margin_y=_MARGIN)
#         else:
#             x, y = _calc_panel_pos(self._vph)
#         self._window.position_x = x
#         self._window.position_y = y
#
# def reposition_deferred(self, frames: int = 2):
#     """show 직후 frame 레이아웃이 stale할 수 있어 몇 프레임 뒤 재배치."""
#     if self._reposition_task is not None and not self._reposition_task.done():
#         self._reposition_task.cancel()
#     self._reposition_task = omni.kit.async_engine.run_coroutine(
#         self._reposition_async(frames))
#
# async def _reposition_async(self, frames: int):
#     app = omni.kit.app.get_app()
#     for _ in range(frames):
#         await app.next_update_async()
#     self._on_viewport_resized()
#
# destroy에서:
#     if self._reposition_task and not self._reposition_task.done():
#         self._reposition_task.cancel()
#     if self._vph:
#         self._vph.frame.set_computed_content_size_changed_fn(None)
#         self._vph = None
# 최소화 토글(_on_toggle_minimize) 끝에서:
#     self._on_viewport_resized()
# ────────────────────────────────────────────────────────────────────────


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
