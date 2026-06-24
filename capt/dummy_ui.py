import omni.ui as ui
from .capture_service import CaptureService


class CaptureUI:

    def __init__(self):
        self._window = None
        self._folder_model = None

    def build_ui(self):
        self._window = ui.Window("Capt", width=360, height=90)

        with self._window.frame:
            with ui.VStack(spacing=4):
                self._folder_model = ui.StringField().model
                self._folder_model.set_value("")
                ui.Button("Capture", clicked_fn=self._on_capture, height=32)

    def _on_capture(self):
        folder_path = self._folder_model.get_value_as_string()
        CaptureService.capture_to_folder(folder_path)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
