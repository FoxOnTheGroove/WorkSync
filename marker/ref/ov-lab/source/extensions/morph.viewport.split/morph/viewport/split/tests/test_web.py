"""웹 메시지 핸들러 (논리 전용 SplitView + 가짜 MessageContext)."""

from unittest import mock

import omni.kit.test
import omni.usd
from pxr import UsdGeom

from .. import EVENT_FOCUS_CHANGED, EVENT_LAYOUT_CHANGED, EVENT_VIEW_CAMERA_CHANGED, SplitView
from .. import LAYOUTS
from .. import extension as ext_mod
from .. import web as web_mod
from ..adapters import HeadlessAdapter, validate_camera_prim
from ..web import (
    ERR_FAILED_PRECONDITION,
    ERR_INVALID_ARGUMENT,
    ERR_NOT_FOUND,
    ERR_UNAVAILABLE,
    MSG_FOCUS_VIEW,
    MSG_GET_STATE,
    MSG_LIST_CAMERAS,
    MSG_SET_LAYOUT,
    MSG_SET_VIEW_CAMERA,
    MSG_STATE,
    WebBridge,
)
from .fakes import FakeCtx, FakeWindowAdapter


class _RaisingMessaging:
    is_connected = True

    def __init__(self):
        self.sent = []

    def send(self, msg_type, payload, reply_to=None):
        self.sent.append(msg_type)
        raise ValueError("schema violation (test)")


class TestStateSendIsolation(omni.kit.test.AsyncTestCase):
    async def test_send_state_swallows_send_errors(self):
        msg = _RaisingMessaging()
        with mock.patch.object(ext_mod, "get_messaging", lambda: msg):
            ext_mod._send_state({"layout": "1x1", "focused_view_id": 0, "views": []})  # 예외가 새지 않음
        self.assertEqual(msg.sent, [MSG_STATE])

    async def test_client_connected_swallows_send_errors(self):
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
        self.addCleanup(view._shutdown)
        msg = _RaisingMessaging()
        with mock.patch.object(ext_mod, "get_messaging", lambda: msg), mock.patch.object(
            ext_mod, "get_split_view", lambda: view
        ):
            ext_mod.SplitViewExtension._on_client_connected(object(), None)  # 예외가 새지 않음
        self.assertEqual(msg.sent, [MSG_STATE])

    async def test_state_sink_error_does_not_break_update(self):
        events = []

        def sink(_payload):
            raise ValueError("send failed (test)")

        view = SplitView(HeadlessAdapter(), emit=lambda n, p: events.append(n), state_sink=sink)
        self.addCleanup(view._shutdown)
        view.set_layout("1x2")
        view._tick()  # 예외가 새지 않음
        self.assertEqual(events, [EVENT_LAYOUT_CHANGED])
        view.set_layout("1x1")
        view._tick()
        self.assertEqual(events, [EVENT_LAYOUT_CHANGED, EVENT_LAYOUT_CHANGED])


class TestWebHandlers(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
        self.bridge = WebBridge(lambda: self.view)

    async def tearDown(self):
        self.view._shutdown()

    async def test_set_layout_replies_ok_immediately(self):
        ctx = FakeCtx(MSG_SET_LAYOUT)
        self.bridge.on_set_layout({"layout": "2x2"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])
        self.assertEqual(self.view.get_layout(), "2x2")

    async def test_set_view_camera_and_focus(self):
        self.bridge.on_set_layout({"layout": "1x2"}, FakeCtx())
        ctx = FakeCtx(MSG_SET_VIEW_CAMERA)
        self.bridge.on_set_view_camera({"view_id": 1, "camera_path": "/Cam"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])
        ctx = FakeCtx(MSG_FOCUS_VIEW)
        self.bridge.on_focus_view({"view_id": 1}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])
        focused = self.view.get_focused_view()
        self.assertEqual((focused.view_id, focused.camera_path), (1, "/Cam"))

    async def test_get_state(self):
        self.bridge.on_set_layout({"layout": "1x2"}, FakeCtx())
        ctx = FakeCtx(MSG_GET_STATE)
        self.bridge.on_get_state({}, ctx)
        self.assertEqual(
            ctx.replies,
            [
                {
                    "ok": True,
                    "layout": "1x2",
                    "focused_view_id": 0,
                    "views": [
                        {"view_id": 0, "viewport_name": "", "camera_path": "/OmniverseKit_Persp", "rect": None},
                        {"view_id": 1, "viewport_name": "", "camera_path": "/OmniverseKit_Top", "rect": None},
                    ],
                    "side_ratio": 0.25,  # 1.5.0
                }
            ],
        )
        self.assertTrue(ctx.replied)

    async def test_expected_errors_reply_with_code(self):
        # 예상된 실패는 예외로 흘리지 않고 같은 ctx로 정확히 한 번 실패 회신한다.
        cases = [
            (self.bridge.on_set_layout, {"layout": "3x3"}, ERR_INVALID_ARGUMENT, {}),
            (self.bridge.on_focus_view, {"view_id": 7}, ERR_NOT_FOUND, {"view_id": 7}),
            (self.bridge.on_set_view_camera, {"view_id": 9, "camera_path": "/C"}, ERR_NOT_FOUND, {"view_id": 9}),
            (self.bridge.on_set_view_camera, {"view_id": 0, "camera_path": ""}, ERR_INVALID_ARGUMENT, {}),
        ]
        for handler, payload, code, extra in cases:
            with self.subTest(handler=handler.__name__, payload=payload):
                ctx = FakeCtx()
                handler(payload, ctx)  # 예외 없음
                self.assertEqual(len(ctx.replies), 1)
                reply = ctx.replies[0]
                self.assertIs(reply["ok"], False)
                self.assertEqual(reply["error"], code)
                self.assertIsInstance(reply["message"], str)
                self.assertTrue(reply["message"])
                for key, value in extra.items():
                    self.assertEqual(reply[key], value)
        # 실패한 요청은 상태를 바꾸지 않는다
        self.assertEqual(self.view.get_layout(), "1x1")
        self.assertEqual(self.view.get_focused_view().view_id, 0)

    async def test_not_running_replies_unavailable(self):
        bridge = WebBridge(lambda: None)
        for handler, payload in (
            (bridge.on_get_state, {}),
            (bridge.on_set_layout, {"layout": "1x2"}),
            (bridge.on_set_view_camera, {"view_id": 0, "camera_path": "/C"}),
            (bridge.on_focus_view, {"view_id": 0}),
        ):
            ctx = FakeCtx()
            handler(payload, ctx)
            self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])

    async def test_shut_down_view_replies_unavailable(self):
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
        view._shutdown()
        ctx = FakeCtx()
        WebBridge(lambda: view).on_set_layout({"layout": "1x2"}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])

    async def test_null_id_request_still_replies_once(self):
        # id: null이면 stream이 송신만 생략한다. 핸들러는 똑같이 한 번 회신한다.
        ctx = FakeCtx(MSG_SET_LAYOUT, msg_id=None)
        self.bridge.on_set_layout({"layout": "1x2"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])


class TestWebHandlersWindowMode(omni.kit.test.AsyncTestCase):
    """창 모드(가짜 어댑터)에서 카메라 검증 실패의 code 매핑."""

    def _bridge(self, validate):
        view = SplitView(FakeWindowAdapter(validate=validate), emit=lambda n, p: None)
        self.addCleanup(view._shutdown)
        return view, WebBridge(lambda: view)

    async def test_no_stage_is_failed_precondition(self):
        view, bridge = self._bridge(lambda p: validate_camera_prim(None, p))
        ctx = FakeCtx(MSG_SET_VIEW_CAMERA)
        bridge.on_set_view_camera({"view_id": 0, "camera_path": "/World/Cam"}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_FAILED_PRECONDITION])
        self.assertEqual(view.list_views()[0].camera_path, "/OmniverseKit_Persp")
        # view_id 검사가 먼저: 스테이지가 없어도 없는 view_id는 not_found
        ctx = FakeCtx(MSG_SET_VIEW_CAMERA)
        bridge.on_set_view_camera({"view_id": 5, "camera_path": "/World/Cam"}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_NOT_FOUND])

    async def test_non_camera_prim_is_invalid_argument(self):
        usd_ctx = omni.usd.get_context()
        await usd_ctx.new_stage_async()
        stage = usd_ctx.get_stage()
        UsdGeom.Camera.Define(stage, "/World/Cam")
        UsdGeom.Xform.Define(stage, "/World/NotCam")
        view, bridge = self._bridge(lambda p: validate_camera_prim(usd_ctx.get_stage(), p))
        for bad in ("/World/NotCam", "/World/Missing", "not a path"):
            ctx = FakeCtx(MSG_SET_VIEW_CAMERA)
            bridge.on_set_view_camera({"view_id": 0, "camera_path": bad}, ctx)
            self.assertEqual([r["error"] for r in ctx.replies], [ERR_INVALID_ARGUMENT], bad)
        ctx = FakeCtx(MSG_SET_VIEW_CAMERA)
        bridge.on_set_view_camera({"view_id": 0, "camera_path": "/World/Cam"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])
        self.assertEqual(view.list_views()[0].camera_path, "/World/Cam")

    async def test_unexpected_errors_propagate_to_stream(self):
        # 예상 밖 예외는 잡지 않는다 (stream이 handler_error로 회신). 핸들러는 회신하지 않는다.
        def boom(_path):
            raise KeyError("unexpected (test)")

        _view, bridge = self._bridge(boom)
        ctx = FakeCtx(MSG_SET_VIEW_CAMERA)
        with self.assertRaises(KeyError):
            bridge.on_set_view_camera({"view_id": 0, "camera_path": "/X"}, ctx)
        self.assertFalse(ctx.replied)


class TestFakeCtxContract(omni.kit.test.AsyncTestCase):
    """가짜 MessageContext가 stream 1.x 회신 규칙을 따르는지 (테스트 신뢰성)."""

    async def test_reply_rules(self):
        ctx = FakeCtx()
        with self.assertRaises(ValueError):
            ctx.reply_ok(ok=False)
        with self.assertRaises(ValueError):
            ctx.reply({"layout": "1x1"})
        with self.assertRaises(ValueError):
            ctx.reply_error("Bad-Code", "x")
        self.assertFalse(ctx.replied)
        ctx.reply_ok()
        with self.assertRaises(RuntimeError):
            ctx.reply_error("not_found", "again")
        self.assertEqual(ctx.replies, [{"ok": True}])


class TestWebTypeNames(omni.kit.test.AsyncTestCase):
    async def test_types_follow_owner_rule(self):
        # stream 1.x: `<owner ext-id>.<name>`, name에 점 없음
        for msg_type in (
            MSG_SET_LAYOUT, MSG_SET_VIEW_CAMERA, MSG_FOCUS_VIEW, MSG_GET_STATE, MSG_LIST_CAMERAS, MSG_STATE
        ):
            self.assertTrue(msg_type.startswith("morph.viewport.split."), msg_type)
            self.assertRegex(msg_type[len("morph.viewport.split.") :], r"^[a-z][a-z0-9_]*$")

    async def test_event_names_unchanged(self):
        # carb 이벤트 이름은 웹 type과 별개이며 바뀌지 않는다 (morph.camera.control 수신).
        self.assertEqual(EVENT_LAYOUT_CHANGED, "morph.viewport.layout_changed")
        self.assertEqual(EVENT_FOCUS_CHANGED, "morph.viewport.focus_changed")
        self.assertEqual(EVENT_VIEW_CAMERA_CHANGED, "morph.viewport.view_camera_changed")


class TestWebSchemas(omni.kit.test.AsyncTestCase):
    async def test_set_layout_enum_includes_1p3(self):
        # 1.3.0: 웹 enum = LAYOUTS (CONTRACT 표와 같은 순서)
        self.assertEqual(
            web_mod.SCHEMA_SET_LAYOUT["properties"]["layout"]["enum"], ["1x1", "1x2", "2x1", "2x2", "1+3"]
        )
        self.assertEqual(web_mod.SCHEMA_SET_LAYOUT["properties"]["layout"]["enum"], list(LAYOUTS))

    async def test_set_layout_1p3_handler(self):
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None)
        self.addCleanup(view._shutdown)
        bridge = WebBridge(lambda: view)
        ctx = FakeCtx(MSG_SET_LAYOUT)
        bridge.on_set_layout({"layout": "1+3"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])
        self.assertEqual(view.get_layout(), "1+3")
        self.assertEqual([v.view_id for v in view.list_views()], [0, 1, 2, 3])
        for bad in ("1+2", "3+1"):
            ctx = FakeCtx(MSG_SET_LAYOUT)
            bridge.on_set_layout({"layout": bad}, ctx)
            self.assertEqual(ctx.replies[0]["error"], ERR_INVALID_ARGUMENT)

    async def test_rect_schema_requires_all_fields(self):
        rect = web_mod._VIEW_SCHEMA["properties"]["rect"]
        self.assertEqual(rect["type"], ["object", "null"])
        self.assertEqual(rect["required"], ["x", "y", "w", "h"])
