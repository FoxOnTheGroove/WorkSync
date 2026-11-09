"""실제 on_startup을 지난 싱글턴 통합 테스트 (실제 업데이트 루프, carb 이벤트, stream 등록)."""

import carb.eventdispatcher
import carb.settings
import omni.kit.app
import omni.kit.test
import omni.usd
from morph.stream.messaging import get_messaging
from omni.kit.viewport.utility import get_viewport_from_window_name
from pxr import UsdGeom

from .. import (
    EVENT_FOCUS_CHANGED,
    EVENT_LAYOUT_CHANGED,
    EVENT_VIEW_CAMERA_CHANGED,
    LAYOUTS,
    SplitView,
    ViewInfo,
    get_split_view,
)
from ..web import (
    EXT_ID,
    MSG_GET_STATE,
    MSG_LIST_CAMERAS,
    MSG_SET_LAYOUT,
    MSG_SET_SIDE_RATIO,
    MSG_STATE,
    WebBridge,
)
from .fakes import FakeCtx


class TestExtensionIntegration(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.app = omni.kit.app.get_app()
        self.events = []
        dispatcher = carb.eventdispatcher.get_eventdispatcher()
        self._subs = [
            dispatcher.observe_event(
                event_name=name,
                on_event=lambda e: self.events.append((e.event_name, dict(e.payload))),
                observer_name="morph.viewport.split test",
            )
            for name in (EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED)
        ]
        view = get_split_view()
        if view is not None and view.get_layout() != "1x1":
            view.set_layout("1x1")
            await self._wait(lambda: not view._core.transitioning)
        self.events.clear()

    async def tearDown(self):
        view = get_split_view()
        if view is not None and view.get_layout() != "1x1":
            view.set_layout("1x1")
            await self._wait(lambda: not view._core.transitioning)
        self._subs = []

    async def _wait(self, pred, frames=120):
        for _ in range(frames):
            if pred():
                return True
            await self.app.next_update_async()
        return pred()

    def _names(self):
        return [n for n, _ in self.events]

    async def test_public_symbols(self):
        self.assertEqual(LAYOUTS, ("1x1", "1x2", "2x1", "2x2", "1+3"))
        self.assertEqual(EVENT_LAYOUT_CHANGED, "morph.viewport.layout_changed")
        self.assertEqual(EVENT_FOCUS_CHANGED, "morph.viewport.focus_changed")
        self.assertEqual(EVENT_VIEW_CAMERA_CHANGED, "morph.viewport.view_camera_changed")
        self.assertIsInstance(get_split_view(), SplitView)

    async def test_is_headless_matches_environment(self):
        view = get_split_view()
        has_default_window = get_viewport_from_window_name() is not None
        self.assertEqual(view.is_headless, not has_default_window)
        if view.is_headless:
            self.assertTrue(all(v.viewport_name == "" for v in view.list_views()))

    async def test_layout_and_focus_through_update_loop(self):
        view = get_split_view()
        view.set_layout("1x2")
        self.assertEqual(view.get_layout(), "1x2")
        self.assertEqual(len(view.list_views()), 2)
        self.assertTrue(await self._wait(lambda: EVENT_LAYOUT_CHANGED in self._names()))
        payload = self.events[0][1]
        self.assertEqual(payload["layout"], "1x2")
        views = list(payload["views"])
        self.assertEqual(len(views), 2)
        self.assertEqual(views[1]["view_id"], 1)
        self.assertEqual(views[1]["camera_path"], view.list_views()[1].camera_path)

        self.events.clear()
        view.focus_view(1)
        self.assertTrue(await self._wait(lambda: EVENT_FOCUS_CHANGED in self._names()))
        self.assertEqual(self.events[0][1]["view_id"], 1)
        self.assertEqual(view.get_focused_view().view_id, 1)

        # 축소: layout_changed 다음 focus_changed(view 0)
        self.events.clear()
        view.set_layout("1x1")
        self.assertTrue(await self._wait(lambda: EVENT_FOCUS_CHANGED in self._names()))
        self.assertEqual(self._names(), [EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED])
        self.assertEqual(self.events[1][1]["view_id"], 0)
        self.assertEqual(view.get_focused_view(), ViewInfo(0, view.list_views()[0].viewport_name, view.list_views()[0].camera_path, True))

    async def test_web_types_registered_with_stream(self):
        msg = get_messaging()
        self.assertIsNotNone(msg)
        # 이미 이 익스텐션이 등록했으므로 같은 type 재등록은 ValueError
        sub = None
        try:
            with self.assertRaises(ValueError):
                sub = msg.register_inbound(MSG_SET_LAYOUT, lambda p, c: None, owner=EXT_ID)
        finally:
            if sub is not None:
                sub.unsubscribe()
        # outbound state 스키마로 검증된다
        msg.send(MSG_STATE, get_split_view()._state_payload())
        with self.assertRaises(ValueError):
            msg.send(MSG_STATE, {"layout": "1x1"})

    async def test_state_schema_accepts_rect_object_and_null(self):
        # 1.2.0: View.rect는 {x,y,w,h} 또는 null. stream outbound 스키마 검증을 실제로 통과해야 한다.
        msg = get_messaging()
        view = {"view_id": 0, "viewport_name": "Viewport", "camera_path": "/OmniverseKit_Persp"}
        base = {"layout": "1x1", "focused_view_id": 0, "side_ratio": 0.25}  # side_ratio 1.5.0 필수
        for rect in (None, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}):
            msg.send(MSG_STATE, {**base, "views": [{**view, "rect": rect}]})
        for bad in ("0,0,1,1", 1.0):
            with self.assertRaises(ValueError):
                msg.send(MSG_STATE, {**base, "views": [{**view, "rect": bad}]})
        for partial in ({"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 0.0, "w": 0.5}):  # 1.3.0: x,y,w,h 필수
            with self.assertRaises(ValueError):
                msg.send(MSG_STATE, {**base, "views": [{**view, "rect": partial}]})
        with self.assertRaises(ValueError):  # rect 필드 누락
            msg.send(MSG_STATE, {**base, "views": [view]})
        payload = get_split_view()._state_payload()
        self.assertTrue(all("rect" in v for v in payload["views"]))
        if get_split_view().is_headless:
            self.assertTrue(all(v["rect"] is None for v in payload["views"]))

    async def test_1p3_through_update_loop_and_web(self):
        # 1.3.0: 웹 set_layout 핸들러로 "1+3", 이어서 같은 뷰 개수인 2x2로 전환이 완료된다.
        view = get_split_view()
        ctx = FakeCtx(MSG_SET_LAYOUT)
        WebBridge(get_split_view).on_set_layout({"layout": "1+3"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])
        self.assertEqual(view.get_layout(), "1+3")
        self.assertEqual(len(view.list_views()), 4)
        self.assertTrue(await self._wait(lambda: EVENT_LAYOUT_CHANGED in self._names(), frames=600))
        self.assertEqual(self.events[-1][1]["layout"], "1+3")
        self.events.clear()
        view.set_layout("2x2")
        self.assertTrue(await self._wait(lambda: EVENT_LAYOUT_CHANGED in self._names(), frames=600))
        self.assertEqual([p["layout"] for n, p in self.events if n == EVENT_LAYOUT_CHANGED], ["2x2"])

    async def test_get_state_handler_on_singleton(self):
        ctx = FakeCtx(MSG_GET_STATE)
        WebBridge(get_split_view).on_get_state({}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True, **get_split_view()._state_payload()}])
        self.assertEqual(ctx.replies[0]["layout"], "1x1")

    async def test_list_cameras_on_singleton(self):
        # 1.4.0: 기본 UsdContext 스테이지의 카메라. 웹 type은 stream에 등록돼 있다 (재등록 ValueError).
        usd_ctx = omni.usd.get_context()
        await usd_ctx.new_stage_async()
        UsdGeom.Camera.Define(usd_ctx.get_stage(), "/World/ListCam")
        UsdGeom.Xform.Define(usd_ctx.get_stage(), "/World/NotCam")
        view = get_split_view()
        cams = view.list_cameras()
        paths = [c.path for c in cams]
        self.assertIn("/World/ListCam", paths)
        self.assertNotIn("/World/NotCam", paths)
        # 사용자 카메라가 기본 카메라보다 앞 (테스트 kit에 기본 카메라가 있든 없든 성립)
        builtins = [c.builtin for c in cams]
        self.assertEqual(builtins, sorted(builtins))
        ctx = FakeCtx(MSG_LIST_CAMERAS)
        WebBridge(get_split_view).on_list_cameras({}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True, "cameras": [c.to_dict() for c in cams]}])
        msg = get_messaging()
        sub = None
        try:
            with self.assertRaises(ValueError):
                sub = msg.register_inbound(MSG_LIST_CAMERAS, lambda p, c: None, owner=EXT_ID)
        finally:
            if sub is not None:
                sub.unsubscribe()

    async def test_side_ratio_on_singleton(self):
        # 1.5.0: set_side_ratio는 carb 설정에도 쓰고, 설정을 직접 바꾸면 get_side_ratio가 따른다. 웹 type 등록됨.
        settings = carb.settings.get_settings()
        path = "/exts/morph.viewport.split/sideRatio"
        original = settings.get(path)
        view = get_split_view()
        try:
            self.assertEqual(view.set_side_ratio(0.9), 0.5)
            self.assertAlmostEqual(settings.get(path), 0.5)
            settings.set(path, 0.3)
            self.assertAlmostEqual(view.get_side_ratio(), 0.3)
            ctx = FakeCtx(MSG_SET_SIDE_RATIO)
            WebBridge(get_split_view).on_set_side_ratio({"ratio": 0.2}, ctx)
            self.assertEqual(ctx.replies, [{"ok": True, "side_ratio": 0.2}])
            payload = view._state_payload()
            self.assertEqual(payload["side_ratio"], 0.2)
            get_messaging().send(MSG_STATE, payload)  # outbound 스키마(side_ratio 필수) 통과
            await self.app.next_update_async()
            self.assertEqual(view.get_layout(), "1x1")  # 1+3이 아니면 저장만
            msg = get_messaging()
            sub = None
            try:
                with self.assertRaises(ValueError):
                    sub = msg.register_inbound(MSG_SET_SIDE_RATIO, lambda p, c: None, owner=EXT_ID)
            finally:
                if sub is not None:
                    sub.unsubscribe()
        finally:
            view.set_side_ratio(original if isinstance(original, (int, float)) and not isinstance(original, bool) else 0.25)
