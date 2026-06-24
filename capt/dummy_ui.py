import omni.ui as ui
from . import capture_service


class CaptureUI:

    def __init__(self):
        self._window = None
        self._status_label = None

    def build_ui(self):
        self._window = ui.Window("Capt", width=300, height=120)

        with self._window.frame:
            with ui.VStack(spacing=4):
                ui.Button("Capture", clicked_fn=self._on_capture, height=40)
                self._status_label = ui.Label("", style={"color": 0xFF888888})

    def _on_capture(self):
        ok = capture_service.capture_to_file("capture.png")
        if self._status_label:
            self._status_label.text = "[OK] Captured" if ok else "[TODO] Not implemented"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
