import omni.ext
from .axiscontrol_ui import AxisControlUI


class MyExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[axis_controller] startup")
        self._ui = AxisControlUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[axis_controller] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
