import omni.ext
from .dummy_ui import UsdInterpolationUI


class UsdInterpolationExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[usd_interpolation] startup")
        self._ui = UsdInterpolationUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[usd_interpolation] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
