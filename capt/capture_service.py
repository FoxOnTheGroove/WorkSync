from .capture import Capture


class CaptureService:

    @classmethod
    def get_window(cls):
        return Capture.get_window()

    @classmethod
    def capture_to_file(cls, file_path):
        return Capture.capture_to_file(file_path)
