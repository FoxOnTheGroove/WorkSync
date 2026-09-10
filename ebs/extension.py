import omni.ext
import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService
from .dummy_ui import EbsDummyUI

WINDOW_TITLE = "EBS Simulate"
RAISE_FRAMES = 300


class EbsExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        """익스텐션 시작"""
        print("[ebs] startup")
        EbsSimulateService.initialize()
        self._ui = EbsDummyUI()
        self._ui.build_ui()
        self._raise = None
        self._frames = 0
        self._shown = False
        self._stage = None
        self._watch_layout()
        self._watch_stage()

    def _watch_stage(self):
        """스테이지가 준비되면 init 을 한 번 누른다"""
        try:
            import omni.kit.app
            self._stage = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._stage_step(),
                                            name="ebs auto init")
        except Exception as e:
            print(f"[ebs] no auto init, press INIT: {e}")

    def _stage_step(self):
        """다 들어온 프레임에 init 을 돌리고 그만 본다"""
        if not self._stage_ready():
            return
        self._stage = None
        self._ui.auto_init()

    @staticmethod
    def _stage_ready() -> bool:
        """스테이지가 열렸고 파일도 다 들어왔나"""
        import omni.usd
        context = omni.usd.get_context()
        if context.get_stage() is None:
            return False
        _, loaded, total = context.get_stage_loading_status()
        return loaded >= total

    def _watch_layout(self):
        """레이아웃이 창을 감추나 매 프레임 지켜본다"""
        try:
            import omni.kit.app
            self._raise = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._raise_step(),
                                            name="ebs window raise")
        except Exception as e:
            print(f"[ebs] the layout may hide the window: {e}")

    def _raise_step(self):
        """감춰졌으면 한 번 되살리고, 우측에 붙으면 그만 본다"""
        self._frames += 1
        window = ui.Workspace.get_window(WINDOW_TITLE)
        if window is not None and not window.visible and not self._shown:
            window.visible = True
            self._shown = True
            print("[ebs] the layout hid the window, showing it again")
        if self._ui.dock_right() or self._frames >= RAISE_FRAMES:
            self._raise = None

    def on_shutdown(self):
        """익스텐션 종료"""
        print("[ebs] shutdown")
        self._raise = None
        self._stage = None
        if self._ui:
            self._ui.destroy()
            self._ui = None
        EbsSimulateService.finalize()
