import omni.ext

from .ebs_simulate_service import EbsSimulateService
from .dummy_ui import EbsDummyUI


class EbsExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        """익스텐션 시작. 서비스를 세우고 창을 띄운다"""
        print("[ebs] startup")
        EbsSimulateService.initialize()
        self._ui = EbsDummyUI()
        self._ui.build_ui()

    def on_shutdown(self):
        """익스텐션 종료. 창을 닫고 서비스가 그린 것을 전부 치운다"""
        print("[ebs] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
        EbsSimulateService.finalize()
