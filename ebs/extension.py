import omni.ext
import omni.ui as ui

from .ebs_simulate import instance, forget
from .ebs_simulate_overlay import attach as attach_overlay
from .ebs_simulate_service import EbsSimulateService

WINDOW_TITLE = "EBS Simulate"
RAISE_FRAMES = 300
USE_DUMMY_UI = False

PRESET = {"usd": "", "xml": "", "ebs2": "", "ebs3": "", "root": "", "rail": "",
          "skin": ""}
VIEW_PATHS = (("/World/Group_01/Foups", False),)
NEAR_SPAN = 2.5
MIN_SIDE, MIN_CEILING = 0.4, 0.05


class EbsExtension(omni.ext.IExt):
    """킷이 잡는 진입점"""

    def on_startup(self, ext_id):
        """익스텐션 시작. 오버레이에 속을 물리고, 켜 뒀으면 창까지 세운다"""
        self._sim = instance()
        attach_overlay(self._sim)
        self._ui = self._make_ui()
        self._raise = None
        self._frames = 0
        self._shown = False
        self._stage = None
        self._inited = False
        if self._ui is not None:
            self._ui.build_ui()
            self._watch_layout()
        self._watch_stage()

    def _make_ui(self):
        """시험용 창 하나. USE_DUMMY_UI 가 꺼져 있으면 들이지도 않는다"""
        if not USE_DUMMY_UI:
            return None
        from .dummy_ui import EbsDummyUI
        return EbsDummyUI(self._sim)

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
        """다 들어온 프레임에 init 을 돌린다. 구독은 다음 프레임에 놓는다"""
        if self._inited:
            self._stage = None
            return
        if not self._stage_ready():
            return
        self._inited = True
        if self._ui is not None:
            self._ui.auto_init()
        else:
            self._auto_init()

    def _auto_init(self) -> dict:
        """창 없이 도는 init. 사전값을 넘기고 보임 경로까지 맞춘다"""
        try:
            self._sim.set_usd_path(PRESET["usd"])
            self._sim.set_xml_path(PRESET["xml"])
            self._sim.set_ebs_paths(PRESET["ebs2"], PRESET["ebs3"])
            self._sim.set_search_root(PRESET["root"])
            self._sim.set_rail_root(PRESET["rail"])
            self._sim.set_near_span(NEAR_SPAN)
            self._sim.set_min_gaps(MIN_SIDE, MIN_CEILING)
            self._sim.set_skin(PRESET["skin"])
            result = EbsSimulateService.auto_init()
            for path, on in VIEW_PATHS:
                if path:
                    self._sim.set_visible(path, on)
            self._sim.set_nudge(0.0)
        except Exception:
            import traceback
            print(f"[ebs] auto init failed:\n{traceback.format_exc()}")
            return {}
        print(f"[ebs] auto init: {(result or {}).get('reason')}")
        return result

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
        if window is not None and not window.visible and not self._shown:
            window.visible = True
            self._shown = True
        if self._ui.dock_right() or self._frames >= RAISE_FRAMES:
            self._raise = None

    def on_shutdown(self):
        """익스텐션 종료"""
        self._raise = None
        self._stage = None
        if self._ui is not None:
            self._ui.destroy()
            self._ui = None
        self._sim = None
        forget()
