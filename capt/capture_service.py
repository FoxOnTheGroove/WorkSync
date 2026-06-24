from .capture import Capture

"""Public API for the capt extension.

External callers should import from here, not from capture.py.
capture.py holds the implementation; this module is the stable surface.
"""


def capture_to_file(file_path):
    """Capture the active viewport and export it to file_path.

    Returns True on success, False otherwise.
    """
    return Capture.capture_to_file(file_path)
