import omni.ui as ui
from .capture_service import CaptureService


class CaptureUI:

    def __init__(self):
        self._window = None
        self._path_model = None

    def build_ui(self):
        self._window = ui.Window("Capt", width=360, height=90)

        with self._window.frame:
            with ui.VStack(spacing=4):
                self._path_model = ui.StringField().model
                self._path_model.set_value("capture.png")
                ui.Button("Capture", clicked_fn=self._on_capture, height=32)

    def _on_capture(self):
        file_path = self._path_model.get_value_as_string()
        CaptureService.capture_to_file(file_path)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
