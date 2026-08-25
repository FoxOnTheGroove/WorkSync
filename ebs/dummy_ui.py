import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsDummyUI"]


class EbsDummyUI:
    """공개 API를 사용하는 더미 익스텐션 UI."""

    def __init__(self):
        self._window = None

    def build_ui(self):
        self._window = ui.Window("EBS Simulate", width=300, height=400)
        with self._window.frame:
            with ui.VStack(spacing=4):
                pass

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
