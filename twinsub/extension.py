import omni.ext

from .dummy_ui import DummyUI
from .twinview_service import cleanup


class TwinSubExtension(omni.ext.IExt):

    _ui = None

    def on_startup(self, ext_id):
        print("[twinsub] startup")
        self._ui = DummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[twinsub] shutdown")

        # 받아둔 .twin 임시폴더를 남기지 않는다.
        cleanup()

        if self._ui:
            self._ui.destroy()
            self._ui = None
