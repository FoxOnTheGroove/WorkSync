import omni.ext
import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService
from .dummy_ui import EbsDummyUI

WINDOW_TITLE = "EBS Simulate"
RAISE_FRAMES = 120


class EbsExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        """익스텐션 시작"""
        print("[ebs] startup")
        EbsSimulateService.initialize()
        self._ui = EbsDummyUI()
        self._ui.build_ui()
        self._raise = None
        self._frames = 0
        self._stage = None
        self._inited = False
        self._watch_stage()
        self._watch_layout()

    def _watch_stage(self):
        """켤 때 한 번. 스테이지가 아직이면 처음 열릴 때까지만 기다린다"""
        try:
            import omni.usd
            if self._auto_init():
                return
            self._stage = omni.usd.get_context().get_stage_event_stream() \
                .create_subscription_to_pop(self._stage_step,
                                            name="ebs auto init")
        except Exception as e:
            print(f"[ebs] no auto init, press INIT: {e}")

    def _stage_step(self, event):
        """스테이지를 다 읽었으면 그때 한 번 돌고 그만 본다"""
        import omni.usd
        if (event.type == int(omni.usd.StageEventType.ASSETS_LOADED)
                and self._auto_init()):
            self._stage = None

    def _auto_init(self) -> bool:
        """스테이지가 있으면 init 을 돌린다. 돌았으면 True. 두 번은 안 돈다"""
        import omni.usd
        if self._inited:
            return True
        if omni.usd.get_context().get_stage() is None and not self._ui.usd_path():
            return False
        self._inited = True
        print(f"[ebs] auto init: {self._ui.auto_init().get('reason', '')}")
        return True

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
        """감춰졌으면 한 번 되살리고 그만 본다"""
        self._frames += 1
        window = ui.Workspace.get_window(WINDOW_TITLE)
        if window is not None and not window.visible:
            window.visible = True
            print("[ebs] the layout hid the window, showing it again")
            self._raise = None
        elif self._frames >= RAISE_FRAMES:
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
