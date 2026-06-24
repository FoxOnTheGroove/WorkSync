from .capture import Capture


class CaptureService:
    """Public API for the capt extension.

    External callers should use CaptureService, not capture.py directly.
    capture.py holds the implementation; this class is the stable surface.
    """

    @staticmethod
    def get_window():
        """Return the target ui.Window to capture."""
        return Capture.get_window()

    @staticmethod
    def capture_to_file(file_path):
        """Capture the active viewport and export it to file_path.

        Returns True on success, False otherwise.
        """
        return Capture.capture_to_file(file_path)
