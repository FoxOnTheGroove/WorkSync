import omni.ext
from .setbgcolor_ui import GradientBGWindow


class GradientBGExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[gradient_bg] startup")
        self._window = GradientBGWindow()

    def on_shutdown(self):
        print("[gradient_bg] shutdown")
        self._window = None
