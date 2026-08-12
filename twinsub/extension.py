import omni.ext

from .dummy_ui import DummyUI


class TwinSubExtension(omni.ext.IExt):

    _ui = None

    def on_startup(self, ext_id):
        print("[twinsub] startup")
        self._ui = DummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[twinsub] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
