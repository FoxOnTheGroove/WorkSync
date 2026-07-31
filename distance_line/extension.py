import omni.ext

from .dummy_ui import DistanceLineDummyUI
from .distance_line import DistanceLineCore


class MyExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[distance_line] startup")
        DistanceLineCore.startup()
        self._ui = DistanceLineDummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[distance_line] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
        DistanceLineCore.shutdown()
