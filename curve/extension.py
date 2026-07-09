import omni.ext

from .dummy_ui import LinesOptimizeUI


class LinesOptimizeExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[curve] startup")
        self._ui = LinesOptimizeUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[curve] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
