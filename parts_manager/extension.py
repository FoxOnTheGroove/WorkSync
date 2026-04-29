import omni.ext
from .parts_manager import PartsManager  # noqa: F401 — re-exported for external callers
from .parts_manager_ui import PartsManagerUI


class MyExtension(omni.ext.IExt):

    active_viewport_id = None  # 외부에서 갱신 후 PartsManager.set_active_viewport() 호출

    def on_startup(self, ext_id):
        print("[parts_manager] startup")
        self._ui = PartsManagerUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[parts_manager] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
