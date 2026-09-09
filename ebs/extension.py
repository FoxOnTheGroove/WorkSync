import omni.ext
import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService
from .dummy_ui import EbsDummyUI

WINDOW_TITLE = "EBS Simulate"
RAISE_FRAMES = 120


class EbsExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        """익스텐션 시작. 서비스를 세우고 창을 띄운다"""
        print("[ebs] startup")
        EbsSimulateService.initialize()
        self._ui = EbsDummyUI()
        self._ui.build_ui()
        self._raise = None
        self._frames = 0
        self._watch_layout()

    def _watch_layout(self):
        """레이아웃은 익스텐션보다 늦게 뜨면서 제 목록에 없는 창을 감춘다"""
        try:
            import omni.kit.app
            self._raise = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._raise_step(),
                                            name="ebs window raise")
        except Exception as e:
            print(f"[ebs] the layout may hide the window: {e}")

    def _raise_step(self):
        """감춰지면 한 번만 되살리고 그만 본다. 그 뒤로 닫는 것은 사용자 몫"""
        self._frames += 1
        window = ui.Workspace.get_window(WINDOW_TITLE)
        if window is not None and not window.visible:
            window.visible = True
            print("[ebs] the layout hid the window, showing it again")
            self._raise = None
        elif self._frames >= RAISE_FRAMES:
            self._raise = None

    def on_shutdown(self):
        """익스텐션 종료. 창을 닫고 서비스가 그린 것을 전부 치운다"""
        print("[ebs] shutdown")
        self._raise = None
        if self._ui:
            self._ui.destroy()
            self._ui = None
        EbsSimulateService.finalize()
