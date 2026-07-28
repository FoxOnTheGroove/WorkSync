import omni.ext

from .dummy_ui import MeasureDummyUI
from .measure import MeasureCore


class MyExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[measure] startup")
        MeasureCore.startup()
        self._ui = MeasureDummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[measure] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
        MeasureCore.shutdown()
