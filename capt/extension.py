import omni.ext
from .dummy_ui import ScreenCaptureUI


class ScreenCaptureExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[capt] startup")
        self._ui = ScreenCaptureUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[capt] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
