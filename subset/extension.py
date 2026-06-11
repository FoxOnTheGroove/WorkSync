import omni.ext
from .dummy_ui import DummyUI


class MyExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[subset] startup")
        self._ui = DummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[subset] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
