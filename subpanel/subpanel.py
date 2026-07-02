# subpanel.py — 참고용 최소 패널 UI
# ui.Window → frame → ZStack → FloatSlider 하나만 있는 간단한 패널

import omni.ui as ui


class SubPanel:
    def __init__(self, title: str = "SubPanel"):
        self._window = None
        self._slider = None
        self._title = title

    def build_ui(self):
        self._window = ui.Window(self._title, width=240, height=80)
        with self._window.frame:
            with ui.ZStack():
                self._slider = ui.FloatSlider(min=0.0, max=1.0)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
        self._slider = None
