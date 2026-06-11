import omni.ui as ui
from .subset import Subset


class DummyUI:

    def __init__(self):
        self._window = None
        self._status_label = None

    def build_ui(self):
        self._window = ui.Window("Subset", width=300, height=200)

        with self._window.frame:
            with ui.VStack(spacing=4):
                with ui.HStack(spacing=4):
                    ui.Button("Initialize", clicked_fn=self._on_initialize, width=100)
                    self._status_label = ui.Label("", style={"color": 0xFF888888})

                ui.Label("(WIP)", style={"color": 0xFF888888})

    def _on_initialize(self):
        ok = Subset.initialize()
        if self._status_label:
            self._status_label.text = "[OK] Initialized" if ok else "[FAIL]"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
