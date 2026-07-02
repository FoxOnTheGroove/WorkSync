# subpanel.py — 참고용 최소 패널 UI
# ui.Window → frame → ZStack → VStack(IntSlider 위 / FloatSlider 아래)

import omni.ui as ui


class SubPanel:
    def __init__(self, title: str = "SubPanel"):
        self._window = None
        self._int_slider = None
        self._slider = None
        self._title = title

    def build_ui(self):
        self._window = ui.Window(self._title, width=240, height=100)
        with self._window.frame:
            with ui.ZStack():
                with ui.VStack(spacing=6):
                    self._int_slider = ui.IntSlider(min=0, max=10)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
        self._int_slider = None
        self._slider = None
