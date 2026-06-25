import omni.ext
from .tab_ui import TabManagerWindow


class TesterUIExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[tester_ui] startup")
        self._window = TabManagerWindow()

    def on_shutdown(self):
        print("[tester_ui] shutdown")
        if self._window is not None:
            self._window.destroy()
        self._window = None
