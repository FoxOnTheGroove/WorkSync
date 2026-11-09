import omni.ui as ui

from .annotation_service import AnnotationService

__all__ = ["AnnotationDummyUI"]

WINDOW_TITLE = "Annotation"


class AnnotationDummyUI:
    """시험용 창"""

    def __init__(self, annotation):
        """위젯 참조 자리만. 구성은 build_ui"""
        self._ann = annotation
        self._window = None
        self._status_label = None

    def build_ui(self):
        """창 하나에 버튼 줄과 상태 줄을 쌓는다"""
        self._window = ui.Window(WINDOW_TITLE, width=420, height=240,
                                 dockPreference=ui.DockPreference.RIGHT_BOTTOM)
        with self._window.frame:
            with ui.VStack(spacing=5, style={"margin": 3}):
                with ui.HStack(height=0, spacing=4):
                    ui.Button("Ready?", clicked_fn=self._on_ready)
                self._status_label = ui.Label("", height=0)

    def _on_ready(self):
        """준비 상태를 상태 줄에 띄운다"""
        self._status(f"ready={AnnotationService.ready()}")

    def _status(self, text: str) -> None:
        """상태 줄"""
        if self._status_label is not None:
            self._status_label.text = text

    def show(self, on: bool) -> None:
        """창을 띄우거나 가린다"""
        if self._window is not None:
            self._window.visible = bool(on)

    def destroy(self):
        """창을 닫는다"""
        if self._window:
            self._window.destroy()
            self._window = None
