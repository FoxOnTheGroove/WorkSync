from .capture import Capture


class CaptureService:

    @classmethod
    def set_prefix(cls, prefix):
        Capture.set_prefix(prefix)

    @classmethod
    def get_window(cls):
        return Capture.get_window()

    @classmethod
    def capture_to_folder(cls, folder_path=None):
        return Capture.capture_to_folder(folder_path)
