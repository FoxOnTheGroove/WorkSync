import omni.ext

from .dummy_ui import TwinViewerUI
from .twin_viewer_service import unload_twin


class TwinViewerExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[twin] startup")
        self._ui = TwinViewerUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[twin] shutdown")
        unload_twin()
        if self._ui:
            self._ui.destroy()
            self._ui = None
