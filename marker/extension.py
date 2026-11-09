import omni.ext

from .annotation import instance, forget
from .annotation_service import AnnotationService
from .dummy_ui import AnnotationDummyUI

SHOW_DUMMY_UI = True


class AnnotationExtension(omni.ext.IExt):
    """킷이 잡는 진입점"""

    def on_startup(self, ext_id):
        """익스텐션 시작. 창에 속을 물린다"""
        self._ann = instance()
        self._ui = AnnotationDummyUI(self._ann)
        self._ui.build_ui()
        self._ui.show(SHOW_DUMMY_UI)

    def on_shutdown(self):
        """익스텐션 종료"""
        if self._ui:
            self._ui.destroy()
            self._ui = None
        self._ann = None
        forget()
