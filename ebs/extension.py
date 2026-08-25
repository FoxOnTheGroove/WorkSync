import omni.ext

from .ebs_simulate_service import EbsSimulateService  # noqa: F401 — 외부 호출용 재노출
from .ebs_simulate_overlay import EbsSimulateOverlay
from .dummy_ui import EbsDummyUI


class EbsSimulateExtension(omni.ext.IExt):

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
        EbsSimulateOverlay.destroy()
        EbsSimulateService.finalize()
