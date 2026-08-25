import omni.ext

from .ebs_simulate_service import EbsSimulateService
from .dummy_ui import EbsDummyUI


class EbsExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[ebs] startup")
        EbsSimulateService.initialize()
        self._ui = EbsDummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[ebs] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
        EbsSimulateService.finalize()
