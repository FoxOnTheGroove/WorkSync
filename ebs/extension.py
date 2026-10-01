import asyncio

import omni.ext
import omni.ui as ui

from .ebs_simulate import instance, forget
from .ebs_simulate_camera import PickLock, viewport_window
from .ebs_simulate_overlay import attach as attach_overlay, EbsSimulateGrip
from .ebs_simulate_service import EbsSimulateService
from .dummy_ui import EbsDummyUI

WINDOW_TITLE = "EBS Simulate"
RAISE_FRAMES = 300
SHOW_DUMMY_UI = False
BLOCK_SELECT = True
KEY_STEP = 0.005
KEY_WAIT = 0.75


class EbsExtension(omni.ext.IExt):
    """킷이 잡는 진입점"""

    def on_startup(self, ext_id):
        """익스텐션 시작. 창과 오버레이에 속을 물린다"""
        self._sim = instance()
        attach_overlay(self._sim)
        self._ui = EbsDummyUI(self._sim)
        self._ui.build_ui()
        self._ui.show(SHOW_DUMMY_UI)
        self._raise = None
        self._frames = 0
        self._shown = False
        self._stage = None
        self._picking = False
        self._keys = None
        self._stepped = False
        self._waiting = None
        self._watch_layout()
        self._watch_stage()
        self._watch_keys()

    def _watch_keys(self):
        """킷 창의 키보드를 받는다"""
        try:
            import carb.input
            import omni.appwindow
            self._keyboard = omni.appwindow.get_default_app_window().get_keyboard()
            self._input = carb.input.acquire_input_interface()
            self._keys = self._input.subscribe_to_keyboard_events(
                self._keyboard, self._on_key)
        except Exception as e:
            print(f"[ebs] no keyboard: {type(e).__name__}: {e}")

    def _on_key(self, event, *args, **kwargs) -> bool:
        """좌우 화살표로 EBS 를 KEY_STEP 씩 밀고 떼면 다시 잰다. 임시로 R 은 카메라, T 는 EBS 를 되돌린다"""
        import carb.input
        keys = carb.input.KeyboardInput
        kind = carb.input.KeyboardEventType
        if event.type == kind.KEY_PRESS and event.input == keys.R:
            EbsSimulateService.cam_refresh()
            return True
        if event.type == kind.KEY_PRESS and event.input == keys.T:
            self._ebs_refresh()
            return True
        way = {keys.LEFT: -1.0, keys.RIGHT: 1.0}.get(event.input)
        if way is None:
            return True
        if event.type in (kind.KEY_PRESS, kind.KEY_REPEAT):
            self._step(way)
        elif event.type == kind.KEY_RELEASE:
            self._settle()
        return True

    def _ebs_refresh(self):
        """임시. T 키로 EBS 를 0mm 로 되돌린다"""
        self._hold_off()
        self._stepped = False
        asyncio.ensure_future(EbsSimulateService.ebs_refresh())

    def _step(self, way: float):
        """SIM 중이고 손잡이를 안 잡았을 때만 한 칸 민다. 기다리던 재측정은 미룬다"""
        if self._sim is None or EbsSimulateGrip.held():
            return
        self._hold_off()
        told = self._sim.nudge_by(way * KEY_STEP)
        if told and told.get("ok"):
            self._stepped = True

    def _settle(self):
        """민 것이 있으면 KEY_WAIT 동안 더 안 눌릴 때 그 자리에서 다시 잰다"""
        if not self._stepped or self._sim is None:
            return
        self._hold_off()
        self._waiting = asyncio.ensure_future(self._settle_later())

    async def _settle_later(self):
        """KEY_WAIT 를 기다렸다가 내부 충돌을 다시 잰다"""
        await asyncio.sleep(KEY_WAIT)
        self._waiting = None
        self._stepped = False
        if self._sim is not None:
            await self._sim.run_nudge_settle()

    def _hold_off(self):
        """기다리던 재측정을 거둔다"""
        if self._waiting is not None:
            self._waiting.cancel()
            self._waiting = None

    def _drop_keys(self):
        """키보드 구독을 놓는다"""
        if self._keys is None:
            return
        try:
            self._input.unsubscribe_to_keyboard_events(self._keyboard, self._keys)
        except Exception as e:
            print(f"[ebs] could not drop the keyboard: {e}")
        self._keys = None

    def _watch_stage(self):
        """스테이지가 준비되면 init 을 누른다"""
        try:
            import omni.kit.app
            self._stage = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._stage_step(),
                                            name="ebs auto init")
        except Exception as e:
            print(f"[ebs] no auto init, press INIT: {e}")

    def _stage_step(self):
        """다 들어온 프레임에 init 을 돌린다"""
        if not self._stage_ready():
            return
        self._stage = None
        self._lock_select()
        self._ui.auto_init()

    def _lock_select(self):
        """뷰포트에서 눌러 고르는 것을 익스텐션이 떠 있는 내내 끈다"""
        if not BLOCK_SELECT or self._picking:
            return
        window = viewport_window()
        if window is None:
            print("[ebs] no viewport, selection stays on")
            return
        try:
            self._picking = PickLock.hold(window)
        except Exception as e:
            print(f"[ebs] selection NOT off: {type(e).__name__}: {e}")

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
        """레이아웃이 창을 감추나 프레임마다 본다"""
        try:
            import omni.kit.app
            self._raise = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._raise_step(),
                                            name="ebs window raise")
        except Exception as e:
            print(f"[ebs] the layout may hide the window: {e}")

    def _raise_step(self):
        """감춰진 창을 되살린다"""
        self._frames += 1
        window = ui.Workspace.get_window(WINDOW_TITLE)
        if (SHOW_DUMMY_UI and window is not None and not window.visible
                and not self._shown):
            window.visible = True
            self._shown = True
        if self._ui.dock_right() or self._frames >= RAISE_FRAMES:
            self._raise = None

    def on_shutdown(self):
        """익스텐션 종료"""
        self._raise = None
        self._stage = None
        self._drop_keys()
        self._hold_off()
        if self._picking:
            PickLock.drop()
            self._picking = False
        if self._ui:
            self._ui.destroy()
            self._ui = None
        self._sim = None
        forget()
