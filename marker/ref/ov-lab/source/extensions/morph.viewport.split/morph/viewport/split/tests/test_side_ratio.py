"""1.5.0: 실행 중 sideRatio 변경 (`set_side_ratio` / `get_side_ratio`, 웹 `set_side_ratio`, state `side_ratio`)."""

import math
import weakref
from unittest import mock

import omni.kit.test

from .. import EVENT_FOCUS_CHANGED, EVENT_LAYOUT_CHANGED, EVENT_VIEW_CAMERA_CHANGED, SplitView
from .. import adapters as adapters_mod
from .. import web as web_mod
from ..adapters import HeadlessAdapter
from ..layout import check_side_ratio
from ..web import ERR_INVALID_ARGUMENT, ERR_UNAVAILABLE, MSG_GET_STATE, MSG_SET_SIDE_RATIO, WebBridge
from .fakes import FakeCtx, FakeWindowAdapter
from .test_kit_adapter import KitAdapterHarness, _FakeViewportWindowClass

SETTING = "/exts/morph.viewport.split/sideRatio"
BAD_VALUES = (True, False, None, "0.3", [0.3], {"r": 0.3}, math.nan, math.inf, -math.inf)


class _Harness:
    def __init__(self, test, adapter=None, **kwargs):
        self.events = []
        self.states = []
        self.adapter = adapter or HeadlessAdapter()
        self.view = SplitView(
            self.adapter,
            emit=lambda name, payload: self.events.append((name, payload)),
            state_sink=self.states.append,
            **kwargs,
        )
        test.addCleanup(self.view._shutdown)

    def tick(self, n=1):
        for _ in range(n):
            self.view._tick()

    def names(self):
        return [name for name, _ in self.events]

    def take(self):
        out, self.events = self.events, []
        return out

    def run_transition(self, max_ticks=30):
        for _ in range(max_ticks):
            self.tick()
            if not self.view._core.transitioning:
                return
        raise AssertionError("transition did not finish")


class TestCheckSideRatio(omni.kit.test.AsyncTestCase):
    async def test_clamps(self):
        self.assertEqual(check_side_ratio(0.3), 0.3)
        self.assertEqual(check_side_ratio(0.1), 0.1)
        self.assertEqual(check_side_ratio(0.5), 0.5)
        self.assertEqual(check_side_ratio(0.05), 0.1)
        self.assertEqual(check_side_ratio(-3.0), 0.1)
        self.assertEqual(check_side_ratio(0.9), 0.5)
        self.assertEqual(check_side_ratio(1), 0.5)  # int도 숫자
        self.assertEqual(check_side_ratio(0), 0.1)
        self.assertEqual(check_side_ratio(10**400), 0.5)  # float 범위 밖 정수도 OverflowError 없이 자름
        self.assertIsInstance(check_side_ratio(1), float)

    async def test_rejects_non_numbers(self):
        for bad in BAD_VALUES:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check_side_ratio(bad)


class TestSideRatioLogic(omni.kit.test.AsyncTestCase):
    """논리 전용 모드 (창 없음)."""

    async def test_headless_store_and_return(self):
        h = _Harness(self)
        self.assertTrue(h.view.is_headless)
        self.assertEqual(h.view.get_side_ratio(), 0.25)
        self.assertEqual(h.view.set_side_ratio(0.3), 0.3)
        self.assertEqual(h.view.get_side_ratio(), 0.3)
        self.assertEqual(h.view.set_side_ratio(0.01), 0.1)
        self.assertEqual(h.view.get_side_ratio(), 0.1)
        self.assertEqual(h.view.set_side_ratio(2), 0.5)
        self.assertEqual(h.view.get_side_ratio(), 0.5)

    async def test_invalid_values_raise_value_error_and_keep_value(self):
        h = _Harness(self)
        h.view.set_side_ratio(0.4)
        for bad in BAD_VALUES:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                h.view.set_side_ratio(bad)
        self.assertEqual(h.view.get_side_ratio(), 0.4)

    async def test_shut_down_raises_runtime_error(self):
        h = _Harness(self)
        h.view._shutdown()
        with self.assertRaises(RuntimeError):
            h.view.set_side_ratio(0.3)
        with self.assertRaises(ValueError):  # 값 검사가 먼저 (set_layout과 같은 순서)
            h.view.set_side_ratio("x")

    async def test_headless_change_pushes_state_without_events(self):
        h = _Harness(self)
        h.view.set_layout("1+3")
        h.tick()
        h.take()
        h.states.clear()
        h.view.set_side_ratio(0.4)
        h.tick()
        self.assertEqual(h.events, [])  # carb 이벤트 없음
        self.assertEqual(len(h.states), 1)
        self.assertEqual(h.states[0]["side_ratio"], 0.4)
        self.assertEqual(h.states[0]["layout"], "1+3")
        h.tick(3)
        self.assertEqual(len(h.states), 1)  # 값이 그대로면 다시 보내지 않음
        h.view.set_side_ratio(0.4)  # 같은 값
        h.tick()
        self.assertEqual(len(h.states), 1)

    async def test_state_payload_has_side_ratio(self):
        h = _Harness(self)
        self.assertEqual(h.view._state_payload()["side_ratio"], 0.25)
        h.view.set_side_ratio(0.35)
        self.assertEqual(h.view._state_payload()["side_ratio"], 0.35)
        h.tick()
        self.assertEqual(h.states, [])  # get_state 등으로 이미 담은 값이면 푸시하지 않음


class TestSideRatioSetting(omni.kit.test.AsyncTestCase):
    """설정 `sideRatio`와 양방향 동기 (dict로 흉내 낸 carb settings)."""

    def _make(self, initial=None, adapter=None):
        self.store = {} if initial is None else {SETTING: initial}
        self.h = _Harness(self, adapter, side_ratio_setting=(self.store.get, self.store.__setitem__))
        return self.h

    async def test_initial_value_from_setting(self):
        self.assertEqual(self._make(0.3).view.get_side_ratio(), 0.3)

    async def test_missing_setting_is_default(self):
        self.assertEqual(self._make().view.get_side_ratio(), 0.25)

    async def test_set_writes_setting(self):
        h = self._make(0.25)
        self.assertEqual(h.view.set_side_ratio(0.45), 0.45)
        self.assertEqual(self.store[SETTING], 0.45)
        self.assertEqual(h.view.set_side_ratio(0.7), 0.5)
        self.assertEqual(self.store[SETTING], 0.5)  # 잘린 값을 쓴다 (설정과 상태가 같음)
        with self.assertRaises(ValueError):
            h.view.set_side_ratio(math.nan)
        self.assertEqual(self.store[SETTING], 0.5)  # 실패하면 설정도 그대로

    async def test_direct_setting_change_is_adopted(self):
        h = self._make(0.25)
        self.store[SETTING] = 0.2
        self.assertEqual(h.view.get_side_ratio(), 0.2)  # 조회 즉시
        warnings = []
        with mock.patch.object(adapters_mod, "_side_ratio_warned", set()), mock.patch.object(
            adapters_mod.carb, "log_warn", warnings.append
        ):
            self.store[SETTING] = 0.8
            self.assertEqual(h.view.get_side_ratio(), 0.5)  # 범위 밖: 자르고 경고
            self.store[SETTING] = "wide"
            self.assertEqual(h.view.get_side_ratio(), 0.25)  # 숫자 아님: 기본값 + 경고
            self.store[SETTING] = math.nan
            for _ in range(3):
                self.assertEqual(h.view.get_side_ratio(), 0.25)
                h.tick()
        self.assertEqual(len(warnings), 3)  # 같은 값은 다시 해석·경고하지 않음

    async def test_direct_setting_change_pushes_state(self):
        h = self._make(0.25)
        h.tick()
        self.store[SETTING] = 0.3
        h.tick()
        self.assertEqual([s["side_ratio"] for s in h.states], [0.3])


class TestSideRatioWindowFlow(omni.kit.test.AsyncTestCase):
    """창 모드 (가짜 어댑터): 1+3 즉시 반영, 전환 중 반영, 같은 프레임 병합."""

    def _make(self, steps=3):
        self.h = _Harness(self, FakeWindowAdapter(steps=steps))
        return self.h

    def _ready(self, layout):
        h = self.h
        h.view.set_layout(layout)
        h.run_transition()
        h.take()
        h.states.clear()

    async def test_1p3_redocks_in_place(self):
        h = self._make()
        self._ready("1+3")
        self.assertEqual(h.adapter.begun, ["1+3"])
        self.assertEqual(h.adapter.side_ratios, [0.25])
        windows = dict(h.adapter.extras)
        h.view.set_view_camera(2, "/CamB")
        h.view.focus_view(3)
        h.tick()
        h.take()
        h.states.clear()

        self.assertEqual(h.view.set_side_ratio(0.4), 0.4)
        self.assertEqual(h.adapter.begun, ["1+3"])  # API 안에서는 창을 건드리지 않음
        h.tick()  # 재도킹 시작
        self.assertTrue(h.view._core.transitioning)
        self.assertEqual(h.adapter.begun, ["1+3", "1+3"])
        self.assertEqual(h.adapter.side_ratios[-1], 0.4)
        self.assertEqual(h.states, [])  # 재도킹 중에는 푸시 보류 (rect가 모두 null인 state를 보내지 않음)
        h.run_transition()
        # 같은 창 객체 그대로, 카메라·포커스 유지, carb 이벤트 없음
        self.assertEqual(h.adapter.extras, windows)
        self.assertTrue(all(h.adapter.extras[v] is windows[v] for v in windows))
        self.assertEqual([v.camera_path for v in h.view.list_views()][2], "/CamB")
        self.assertEqual(h.view.get_focused_view().view_id, 3)
        self.assertEqual(h.events, [])
        self.assertEqual(h.view.get_layout(), "1+3")
        self.assertEqual(len(h.states), 1)  # 재도킹 완료 시 1회
        self.assertEqual(h.states[0]["side_ratio"], 0.4)
        self.assertEqual(h.states[0]["focused_view_id"], 3)
        h.tick(5)
        self.assertEqual(h.adapter.begun, ["1+3", "1+3"])  # 값이 같으면 다시 하지 않음

    async def test_same_frame_calls_merge_to_last(self):
        h = self._make()
        self._ready("1+3")
        for value in (0.3, 0.35, 0.41, 0.2):  # 슬라이더 드래그
            h.view.set_side_ratio(value)
        h.run_transition()
        self.assertEqual(h.adapter.begun, ["1+3", "1+3"])
        self.assertEqual(h.adapter.side_ratios, [0.25, 0.2])
        self.assertEqual([s["side_ratio"] for s in h.states], [0.2])

    async def test_change_during_relayout_applies_latest_after(self):
        h = self._make(steps=3)
        self._ready("1+3")
        h.view.set_side_ratio(0.3)
        h.tick()  # 재도킹(0.3) 시작
        h.view.set_side_ratio(0.35)
        h.view.set_side_ratio(0.45)
        h.run_transition()  # 0.3 재도킹 완료
        self.assertEqual(h.adapter.side_ratios, [0.25, 0.3])
        h.run_transition()  # 다음 틱에 최신 값으로 한 번 더
        self.assertEqual(h.adapter.side_ratios, [0.25, 0.3, 0.45])
        self.assertEqual(h.events, [])
        self.assertEqual([s["side_ratio"] for s in h.states], [0.45, 0.45])  # 재도킹 완료마다 1회

    async def test_change_during_layout_transition_applies_after(self):
        h = self._make(steps=3)
        self._ready("2x2")
        h.view.set_layout("1+3")
        h.tick()  # 1+3 전환 진행 중 (0.25로 시작)
        h.view.set_side_ratio(0.4)
        h.run_transition()
        self.assertEqual([n for n, _ in h.take()], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(h.adapter.side_ratios[-1], 0.25)
        h.run_transition()  # 끝난 뒤 최신 값으로 재도킹
        self.assertEqual(h.adapter.begun[-2:], ["1+3", "1+3"])
        self.assertEqual(h.adapter.side_ratios[-1], 0.4)
        self.assertEqual(h.events, [])

    async def test_change_during_layout_transition_pushes_state_after(self):
        # 1.5.1: 전환 중(rect 모두 null)에는 side_ratio 변경 푸시를 보내지 않고 끝난 프레임에 보낸다
        for target in ("1+3", "1x2"):
            with self.subTest(target=target):
                h = self._make(steps=3)
                self._ready("2x2")
                h.view.set_layout(target)
                h.tick()  # 전환 진행 중
                h.view.set_side_ratio(0.4)
                while h.view._core.transitioning:
                    h.tick()
                    if h.view._core.transitioning:
                        self.assertEqual(h.states, [])  # 전환 중 푸시 없음
                self.assertEqual([n for n, _ in h.take()], [EVENT_LAYOUT_CHANGED])
                self.assertEqual([s["side_ratio"] for s in h.states], [0.4])  # 완료 프레임에 1회 (병합)
                h.states.clear()
                if target == "1+3":
                    h.run_transition()  # 최신 값으로 재도킹 → 완료 때 1회
                    self.assertEqual([s["side_ratio"] for s in h.states], [0.4])
                else:
                    h.tick(3)
                    self.assertEqual(h.states, [])  # 이미 보낸 값 → 다시 보내지 않음

    async def test_not_1p3_only_stores(self):
        h = self._make()
        self._ready("2x2")
        self.assertEqual(h.view.set_side_ratio(0.33), 0.33)
        h.tick(5)
        self.assertEqual(h.adapter.begun, ["2x2"])  # 재도킹 없음
        self.assertEqual([s["side_ratio"] for s in h.states], [0.33])  # 값 변경은 바로 푸시 (rect는 그대로)
        h.view.set_layout("1+3")
        self.assertEqual(h.adapter.side_ratios[-1], 0.33)  # 다음 1+3 전환에 쓰임
        h.run_transition()
        h.tick(3)
        self.assertEqual(h.adapter.begun, ["2x2", "1+3"])

    async def test_set_layout_during_relayout_emits_layout_changed(self):
        h = self._make(steps=3)
        self._ready("1+3")
        h.view.set_side_ratio(0.4)
        h.tick()  # 재도킹 시작
        h.view.set_layout("2x2")  # 재도킹을 실제 전환으로 교체
        h.run_transition()
        self.assertEqual([(n, p["layout"]) for n, p in h.take()], [(EVENT_LAYOUT_CHANGED, "2x2")])
        h.tick(3)
        self.assertEqual(h.adapter.begun, ["1+3", "1+3", "2x2"])

    async def test_focus_during_relayout_emits_focus_changed_only(self):
        h = self._make(steps=3)
        self._ready("1+3")
        h.view.set_side_ratio(0.4)
        h.tick()
        h.view.focus_view(2)
        h.run_transition()
        self.assertEqual([(n, p["view_id"]) for n, p in h.take()], [(EVENT_FOCUS_CHANGED, 2)])

    async def test_external_camera_change_during_relayout(self):
        h = self._make(steps=3)
        self._ready("1+3")
        h.view.set_side_ratio(0.4)
        h.tick()
        h.adapter.window(1).camera = "/Ext"
        h.run_transition()
        self.assertEqual([(n, p["camera_path"]) for n, p in h.take()], [(EVENT_VIEW_CAMERA_CHANGED, "/Ext")])
        self.assertEqual(h.view.list_views()[1].camera_path, "/Ext")


class TestSideRatioKitAdapter(KitAdapterHarness):
    """실제 KitWindowAdapter: 1+3에서 재도킹만 하고 창·뷰포트 API 객체를 유지한다."""

    def _docks(self, view_id):
        w = self.created[f"MORPH Viewport {view_id}"]
        return list(zip(w.dock_calls, w.dock_ratios))

    def _ready(self, layout):
        self.view.set_layout(layout)
        self._run_transition()
        self.assertFalse(self.view._core.transitioning)
        self.events.clear()

    async def test_1p3_redock_keeps_windows_and_viewport_api(self):
        self.states = []
        self.view._state_sink = self.states.append
        self._ready("1+3")
        windows = dict(self.created)
        apis = {name: w.viewport_api for name, w in windows.items()}
        cameras = [v.camera_path for v in self.view.list_views()]
        self.view.focus_view(2)
        self._tick()
        self.events.clear()
        self.states.clear()

        self.assertEqual(self.view.set_side_ratio(0.4), 0.4)
        self._run_transition()
        self.assertFalse(self.view._core.transitioning)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.created, windows)  # 새 창 없음
        for name, w in windows.items():
            self.assertFalse(w.destroyed, name)
            self.assertIs(w.viewport_api, apis[name], name)  # 같은 뷰포트 API 객체
        self.assertEqual(self._docks(1)[-1], (("Viewport", "RIGHT"), 0.4))
        (call2, ratio2) = self._docks(2)[-1]
        self.assertEqual(call2, ("MORPH Viewport 1", "BOTTOM"))
        self.assertAlmostEqual(ratio2, 2.0 / 3.0)
        self.assertEqual(self._docks(3)[-1], (("MORPH Viewport 2", "BOTTOM"), 0.5))
        self.assertEqual([len(self._docks(v)) for v in (1, 2, 3)], [2, 2, 2])
        self.assertEqual([v.camera_path for v in self.view.list_views()], cameras)
        self.assertEqual(self.view.get_focused_view().view_id, 2)
        self.assertEqual(_FakeViewportWindowClass.active_window.title, "MORPH Viewport 2")
        self.assertEqual(self.events, [])  # layout_changed 없음
        self.assertEqual([s["side_ratio"] for s in self.states], [0.4])

    async def test_set_layout_1p3_uses_current_value(self):
        self.view.set_side_ratio(0.3)
        self._ready("1+3")
        self.assertEqual(self._docks(1), [(("Viewport", "RIGHT"), 0.3)])
        self._tick(5)
        self.assertEqual(len(self._docks(1)), 1)  # 이미 그 값으로 도킹 → 재도킹 없음

    async def test_view0_proxy_not_leaked(self):
        # 재도킹에서도 view 0(프록시)은 핸들로 바꿔 dock_in에 넘긴다 (가짜 창이 프록시 인자를 거부)
        self._ready("1+3")
        self.view.set_side_ratio(0.2)
        self._run_transition()
        self.assertEqual(self.errors, [])
        self.assertIsInstance(_FakeViewportWindowClass.active_window, weakref.ProxyTypes)


class TestSideRatioWeb(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
        self.bridge = WebBridge(lambda: self.view)

    async def tearDown(self):
        self.view._shutdown()

    async def test_reply_with_applied_value(self):
        for ratio, applied in ((0.3, 0.3), (0.05, 0.1), (0.9, 0.5), (1, 0.5)):
            ctx = FakeCtx(MSG_SET_SIDE_RATIO)
            self.bridge.on_set_side_ratio({"ratio": ratio}, ctx)
            self.assertEqual(ctx.replies, [{"ok": True, "side_ratio": applied}])
            self.assertEqual(self.view.get_side_ratio(), applied)

    async def test_invalid_values_reply_invalid_argument(self):
        self.view.set_side_ratio(0.3)
        payloads = [{"ratio": bad} for bad in BAD_VALUES] + [{}]
        for payload in payloads:
            with self.subTest(payload=payload):
                ctx = FakeCtx(MSG_SET_SIDE_RATIO)
                self.bridge.on_set_side_ratio(payload, ctx)  # 예외 없음
                self.assertEqual(len(ctx.replies), 1)
                self.assertIs(ctx.replies[0]["ok"], False)
                self.assertEqual(ctx.replies[0]["error"], ERR_INVALID_ARGUMENT)
                self.assertTrue(ctx.replies[0]["message"])
        self.assertEqual(self.view.get_side_ratio(), 0.3)  # 실패는 상태를 바꾸지 않음

    async def test_not_running_replies_unavailable(self):
        ctx = FakeCtx(MSG_SET_SIDE_RATIO)
        WebBridge(lambda: None).on_set_side_ratio({"ratio": 0.3}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
        view._shutdown()
        ctx = FakeCtx(MSG_SET_SIDE_RATIO)
        WebBridge(lambda: view).on_set_side_ratio({"ratio": 0.3}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])

    async def test_shutdown_after_handler_entry_replies_unavailable(self):
        # 1.5.1: _view 검사 뒤 shutdown 경합으로 난 RuntimeError는 failed_precondition이 아니라 unavailable
        calls = (
            ("on_set_layout", "set_layout", {"layout": "2x2"}),
            ("on_set_view_camera", "set_view_camera", {"view_id": 0, "camera_path": "/OmniverseKit_Persp"}),
            ("on_focus_view", "focus_view", {"view_id": 0}),
            ("on_set_side_ratio", "set_side_ratio", {"ratio": 0.3}),
        )
        for handler_name, method, payload in calls:
            with self.subTest(method=method):
                view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
                self.addCleanup(view._shutdown)
                bridge = WebBridge(lambda view=view: view)
                original = getattr(SplitView, method)

                def racing(self_, *args, _original=original, **kwargs):
                    self_._shutdown()  # 핸들러의 _view 검사는 이미 통과한 뒤
                    return _original(self_, *args, **kwargs)

                ctx = FakeCtx()
                with mock.patch.object(SplitView, method, racing):
                    getattr(bridge, handler_name)(payload, ctx)
                self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])

    async def test_runtime_error_while_running_is_failed_precondition(self):
        ctx = FakeCtx()
        with mock.patch.object(SplitView, "set_layout", side_effect=RuntimeError("no open stage")):
            self.bridge.on_set_layout({"layout": "2x2"}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [web_mod.ERR_FAILED_PRECONDITION])

    async def test_get_state_has_side_ratio(self):
        self.bridge.on_set_side_ratio({"ratio": 0.42}, FakeCtx())
        ctx = FakeCtx(MSG_GET_STATE)
        self.bridge.on_get_state({}, ctx)
        self.assertEqual(ctx.replies[0]["side_ratio"], 0.42)

    async def test_schemas(self):
        self.assertEqual(MSG_SET_SIDE_RATIO, "morph.viewport.split.set_side_ratio")
        self.assertEqual(
            web_mod.SCHEMA_SET_SIDE_RATIO,
            {"type": "object", "required": ["ratio"], "properties": {"ratio": {"type": "number"}}},
        )
        self.assertIn("side_ratio", web_mod.SCHEMA_STATE["required"])
        self.assertEqual(web_mod.SCHEMA_STATE["properties"]["side_ratio"], {"type": "number"})

    async def test_registered_with_messaging(self):
        registered = []

        class _Sub:
            def unsubscribe(self):
                pass

        class _Messaging:
            def register_outbound(self, msg_type, schema=None, *, owner, version=1):
                registered.append(("out", msg_type))
                return _Sub()

            def register_inbound(self, msg_type, handler, schema=None, *, owner, version=1):
                registered.append(("in", msg_type))
                return _Sub()

        subs = WebBridge(lambda: self.view).register(_Messaging())
        self.assertIn(("in", MSG_SET_SIDE_RATIO), registered)
        self.assertEqual(len(subs), 7)  # outbound state + inbound 6
