import omni.ui as ui


class DummyUI:

    WINDOW_TITLE = "TwinSub"

    def __init__(self):
        self._window = None

    def build_ui(self):
        self._window = ui.Window(self.WINDOW_TITLE, width=420, height=220)

        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                ui.Label(self.WINDOW_TITLE)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
