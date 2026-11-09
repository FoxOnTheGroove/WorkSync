import omni.ext

from .annotation import instance, forget
from .dummy_ui import AnnotationDummyUI

SHOW_DUMMY_UI = True


class AnnotationExtension(omni.ext.IExt):
    """킷이 잡는 진입점"""

    def on_startup(self, ext_id):
        """익스텐션 시작. 뷰포트에 마커 그릴 자리를 걸고 시험 창을 띄운다"""
        self._ann = instance()
        self._ann.start()
        self._ui = AnnotationDummyUI()
        self._ui.build_ui()
        self._ui.show(SHOW_DUMMY_UI)

    def on_shutdown(self):
        """익스텐션 종료"""
        if self._ui:
            self._ui.destroy()
            self._ui = None
        self._ann = None
        forget()
