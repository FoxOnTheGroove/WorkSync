import omni.ext

from .dummy_ui import DummyUI
from .twinview_service import TwinViewService


class TwinSubExtension(omni.ext.IExt):

    _ui = None

    def on_startup(self, ext_id):
        print("[twinviewer] startup")
        self._ui = DummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[twinviewer] shutdown")

        TwinViewService.cleanup()

        if self._ui:
            self._ui.destroy()
            self._ui = None
