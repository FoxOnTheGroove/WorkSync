import omni.ext
from .parts_manager import PartsManager
from .parts_manager_ui import PartsManagerUI


class MyExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[parts_manager] startup")
        self._manager = PartsManager()
        self._ui = PartsManagerUI(self._manager)
        self._ui.build_ui()

    def on_shutdown(self):
        print("[parts_manager] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
        self._manager = None
