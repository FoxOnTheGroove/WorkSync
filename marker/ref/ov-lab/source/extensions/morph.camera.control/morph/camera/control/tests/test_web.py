"""웹 메시지 핸들러 테스트 (stream 1.x 회신 규칙을 강제하는 가짜 MessageContext / Messaging)."""
import omni.kit.test

from morph.camera.control import CameraController
from morph.camera.control import usd_camera
from morph.camera.control.web import (
    MAX_PRESET_NAME_LEN,
    MAX_PRESETS,
    TYPE_APPLY_PRESET,
    TYPE_DELETE_PRESET,
    TYPE_LIST_PRESETS,
    TYPE_SAVE_PRESET,
    TYPE_ORBIT,
    TYPE_PAN,
    TYPE_STATE,
    TYPE_ZOOM,
    WebHandlers,
    register_web,
)

from ._helpers import (
    FakeClock,
    FakeCtx,
    FakeViewport,
    assert_finite_list,
    assert_vec,
    define_camera,
    new_stage,
)


class _FakeSub:
    def __init__(self, log, type_):
        self._log, self._type = log, type_

    def unsubscribe(self):
        self._log.append(("unsubscribe", self._type))


class _FakeMessaging:
    def __init__(self, fail_on=None):
        self.log = []
        self.fail_on = fail_on

    def register_outbound(self, type_, schema=None, *, owner, version=1):
        self.log.append(("outbound", type_, owner))
        return _FakeSub(self.log, type_)

    def register_inbound(self, type_, handler, schema=None, *, owner, version=1):
        if type_ == self.fail_on:
            raise ValueError("duplicate")
        self.log.append(("inbound", type_, owner))
        return _FakeSub(self.log, type_)


class TestWebHandlers(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = await new_stage("Y")
        define_camera(self.stage, "/World/Cam")
        self.ctrl = CameraController(clock=FakeClock())
        self.ctrl._set_target_viewport(FakeViewport("/World/Cam"), "Viewport")
        self.handlers = WebHandlers(self.ctrl)

    async def tearDown(self):
        self.ctrl._shutdown()

    def _eye(self):
        return usd_camera.read_camera_pose(self.stage, "/World/Cam")[0]

    def _assert_error(self, ctx, code, **fields):
        self.assertEqual(len(ctx.replies), 1)
        reply = ctx.replies[0]
        self.assertIs(reply["ok"], False)
        self.assertEqual(reply["error"], code)
        self.assertIsInstance(reply["message"], str)
        for key, value in fields.items():
            self.assertEqual(reply[key], value)

    async def test_type_names_follow_owner_rule(self):
        for t in (
            TYPE_ORBIT,
            TYPE_PAN,
            TYPE_ZOOM,
            TYPE_APPLY_PRESET,
            TYPE_SAVE_PRESET,
            TYPE_LIST_PRESETS,
            TYPE_DELETE_PRESET,
            TYPE_STATE,
        ):
            owner, _, name = t.rpartition(".")
            self.assertEqual(owner, "morph.camera.control")
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")

    async def test_orbit_pan_zoom_queued_and_reply_ok(self):
        ctxs = [FakeCtx(TYPE_ORBIT), FakeCtx(TYPE_PAN), FakeCtx(TYPE_ZOOM)]
        self.handlers.on_orbit({"d_yaw": 90, "d_pitch": 0}, ctxs[0])
        self.handlers.on_pan({"dx": 0.0, "dy": 1.0}, ctxs[1])
        self.handlers.on_zoom({"factor": 0.5}, ctxs[2])
        for ctx in ctxs:
            self.assertEqual(ctx.replies, [{"ok": True}])
        assert_vec(self, self._eye(), (0.0, 0.0, 10.0))  # 핸들러에서는 쓰지 않음
        self.ctrl._update()
        assert_vec(self, self._eye(), (5.0, 1.0, 0.0))

    async def test_null_id_requests_still_reply_once(self):
        # id: null → stream이 송신을 생략하지만 핸들러는 규칙대로 한 번 회신한다 (드래그용 notify)
        ctx = FakeCtx(TYPE_ORBIT, id_=None)
        self.handlers.on_orbit({"d_yaw": 1, "d_pitch": 0}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True}])

    async def test_invalid_values_reply_invalid_argument(self):
        cases = [
            (self.handlers.on_zoom, {"factor": 0}),
            (self.handlers.on_zoom, {"factor": -2.0}),
            (self.handlers.on_orbit, {"d_yaw": float("nan"), "d_pitch": 0}),
            (self.handlers.on_pan, {"dx": float("inf"), "dy": 0}),
        ]
        for handler, payload in cases:
            ctx = FakeCtx()
            handler(payload, ctx)
            self._assert_error(ctx, "invalid_argument")
        self.ctrl._update()
        assert_vec(self, self._eye(), (0.0, 0.0, 10.0))  # 거부된 명령은 큐에 없음

    async def test_apply_preset_unknown_replies_not_found(self):
        ctx = FakeCtx(TYPE_APPLY_PRESET)
        self.handlers.on_apply_preset({"name": "nope"}, ctx)
        self.assertEqual(ctx.replies, [{"ok": False, "error": "not_found", "message": "unknown preset", "name": "nope"}])

    async def test_apply_preset_invalid_duration(self):
        self.ctrl._update()
        self.ctrl.save_preset("home")
        ctx = FakeCtx(TYPE_APPLY_PRESET)
        self.handlers.on_apply_preset({"name": "home", "duration_s": float("nan")}, ctx)
        self._assert_error(ctx, "invalid_argument", name="home")

    async def test_apply_preset_success(self):
        self.ctrl._update()
        self.ctrl.save_preset("home")
        self.ctrl.orbit(90.0, 0.0)
        self.ctrl._update()
        ctx = FakeCtx(TYPE_APPLY_PRESET)
        self.handlers.on_apply_preset({"name": "home", "duration_s": 0}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True, "name": "home"}])
        self.ctrl._update()
        assert_vec(self, self._eye(), (0.0, 0.0, 10.0))

    async def test_no_target_replies_failed_precondition_and_drops_command(self):
        self.ctrl._update()
        self.ctrl.save_preset("home")
        self.ctrl._set_target_viewport(None)
        cases = [
            (self.handlers.on_orbit, {"d_yaw": 10, "d_pitch": 0}, {}),
            (self.handlers.on_pan, {"dx": 1, "dy": 0}, {}),
            (self.handlers.on_zoom, {"factor": 0.5}, {}),
            (self.handlers.on_apply_preset, {"name": "home"}, {"name": "home"}),
        ]
        for handler, payload, fields in cases:
            ctx = FakeCtx()
            handler(payload, ctx)
            self._assert_error(ctx, "failed_precondition", **fields)
        # 거부된 명령은 큐에 들어가지 않는다 (대상이 다시 생겨도 반영되지 않음)
        abs_cmd, rel, n = self.ctrl._take_queue()
        self.assertEqual((abs_cmd, rel, n), (None, None, 0))

    async def test_no_camera_replies_failed_precondition(self):
        self.ctrl._set_target_viewport(FakeViewport("/World/Missing"), "Viewport")
        self.assertIsNone(self.ctrl.get_state())
        ctx = FakeCtx(TYPE_ORBIT)
        self.handlers.on_orbit({"d_yaw": 10, "d_pitch": 0}, ctx)
        self._assert_error(ctx, "failed_precondition")

    async def test_error_order_invalid_then_not_found_then_precondition(self):
        self.ctrl._set_target_viewport(None)
        ctx = FakeCtx()
        self.handlers.on_zoom({"factor": 0}, ctx)
        self._assert_error(ctx, "invalid_argument")  # 대상 없음보다 입력 오류 먼저
        ctx = FakeCtx()
        self.handlers.on_apply_preset({"name": "nope"}, ctx)
        self._assert_error(ctx, "not_found", name="nope")

    async def test_register_web_uses_owner_and_returns_subscriptions(self):
        msg = _FakeMessaging()
        subs = register_web(msg, self.ctrl)
        self.assertEqual(len(subs), 8)
        registered = {(kind, t) for kind, t, _ in msg.log}
        self.assertEqual(
            registered,
            {
                ("outbound", TYPE_STATE),
                ("inbound", TYPE_ORBIT),
                ("inbound", TYPE_PAN),
                ("inbound", TYPE_ZOOM),
                ("inbound", TYPE_APPLY_PRESET),
                ("inbound", TYPE_SAVE_PRESET),
                ("inbound", TYPE_LIST_PRESETS),
                ("inbound", TYPE_DELETE_PRESET),
            },
        )
        self.assertTrue(all(owner == "morph.camera.control" for _, _, owner in msg.log))

    async def test_register_web_rolls_back_on_failure(self):
        msg = _FakeMessaging(fail_on=TYPE_ZOOM)
        with self.assertRaises(ValueError):
            register_web(msg, self.ctrl)
        unsubscribed = {t for kind, t in [(e[0], e[1]) for e in msg.log] if kind == "unsubscribe"}
        self.assertEqual(unsubscribed, {TYPE_STATE, TYPE_ORBIT, TYPE_PAN})


class TestWebPresetHandlers(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = await new_stage("Y")
        define_camera(self.stage, "/World/Cam")
        self.ctrl = CameraController(clock=FakeClock())
        self.ctrl._set_target_viewport(FakeViewport("/World/Cam"), "Viewport")
        self.ctrl._update()
        self.handlers = WebHandlers(self.ctrl)

    async def tearDown(self):
        self.ctrl._shutdown()

    def _call(self, handler, payload, type_=""):
        ctx = FakeCtx(type_)
        handler(payload, ctx)
        self.assertEqual(len(ctx.replies), 1)
        return ctx.replies[0]

    def _assert_error(self, reply, code, **fields):
        self.assertIs(reply["ok"], False)
        self.assertEqual(reply["error"], code)
        self.assertIsInstance(reply["message"], str)
        for key, value in fields.items():
            self.assertEqual(reply[key], value)

    async def test_save_list_delete_roundtrip(self):
        self.assertEqual(self._call(self.handlers.on_list_presets, {}), {"ok": True, "presets": []})
        self.assertEqual(self._call(self.handlers.on_save_preset, {"name": "top"}), {"ok": True, "name": "top"})
        self.ctrl.orbit(90.0, 0.0)
        self.ctrl._update()
        self.assertEqual(self._call(self.handlers.on_save_preset, {"name": "home"}), {"ok": True, "name": "home"})

        reply = self._call(self.handlers.on_list_presets, {})
        self.assertIs(reply["ok"], True)
        self.assertEqual([p["name"] for p in reply["presets"]], ["home", "top"])  # 이름순
        home, top = reply["presets"]
        self.assertEqual(set(home), {"name", "eye", "target", "fov"})
        assert_vec(self, home["eye"], (10.0, 0.0, 0.0))
        assert_vec(self, top["eye"], (0.0, 0.0, 10.0))
        assert_vec(self, top["target"], (0.0, 0.0, 0.0))
        for p in reply["presets"]:
            assert_finite_list(self, p["eye"])
            assert_finite_list(self, p["target"])
            self.assertIsInstance(p["fov"], float)
        self.assertEqual(self.ctrl.list_presets(), ["home", "top"])  # Python API와 같은 저장소

        self.assertEqual(self._call(self.handlers.on_delete_preset, {"name": "home"}), {"ok": True, "name": "home"})
        reply = self._call(self.handlers.on_list_presets, {})
        self.assertEqual([p["name"] for p in reply["presets"]], ["top"])

    async def test_save_overwrites_same_name(self):
        self._call(self.handlers.on_save_preset, {"name": "home"})
        self.ctrl.orbit(90.0, 0.0)
        self.ctrl._update()
        self._call(self.handlers.on_save_preset, {"name": "home"})
        presets = self._call(self.handlers.on_list_presets, {})["presets"]
        self.assertEqual(len(presets), 1)
        assert_vec(self, presets[0]["eye"], (10.0, 0.0, 0.0))

    async def test_save_invalid_name(self):
        reply = self._call(self.handlers.on_save_preset, {"name": ""})
        self._assert_error(reply, "invalid_argument", name="")
        reply = self._call(self.handlers.on_save_preset, {"name": 3})  # 스키마 검증이 꺼진 경우 방어
        self._assert_error(reply, "invalid_argument", name="")
        long_name = "x" * (MAX_PRESET_NAME_LEN + 1)
        reply = self._call(self.handlers.on_save_preset, {"name": long_name})
        self._assert_error(reply, "invalid_argument", name="x" * MAX_PRESET_NAME_LEN, limit=MAX_PRESET_NAME_LEN)
        self.assertEqual(self.ctrl.list_presets(), [])
        ok = self._call(self.handlers.on_save_preset, {"name": "x" * MAX_PRESET_NAME_LEN})
        self.assertIs(ok["ok"], True)
        # 길이는 코드 포인트 기준 (BMP 밖 문자도 1자)
        emoji_name = "\U0001F4F7" * MAX_PRESET_NAME_LEN
        self.assertIs(self._call(self.handlers.on_save_preset, {"name": emoji_name})["ok"], True)

    async def test_save_preset_count_limit(self):
        self.assertEqual(MAX_PRESETS, 64)
        self.assertEqual(MAX_PRESET_NAME_LEN, 64)
        for i in range(MAX_PRESETS):
            self.assertIs(self._call(self.handlers.on_save_preset, {"name": f"p{i:02d}"})["ok"], True)
        reply = self._call(self.handlers.on_save_preset, {"name": "one_more"})
        self._assert_error(reply, "resource_exhausted", name="one_more", limit=MAX_PRESETS)
        self.assertEqual(len(self.ctrl.list_presets()), MAX_PRESETS)
        # 상한에서도 기존 이름 덮어쓰기는 된다
        self.assertEqual(self._call(self.handlers.on_save_preset, {"name": "p00"}), {"ok": True, "name": "p00"})
        # 하나 지우면 다시 저장 가능
        self._call(self.handlers.on_delete_preset, {"name": "p01"})
        self.assertIs(self._call(self.handlers.on_save_preset, {"name": "one_more"})["ok"], True)

    async def test_count_limit_includes_python_presets(self):
        for i in range(MAX_PRESETS + 2):  # Python API는 상한 없음
            self.ctrl.save_preset(f"py{i:02d}")
        reply = self._call(self.handlers.on_save_preset, {"name": "web"})
        self._assert_error(reply, "resource_exhausted", name="web")
        presets = self._call(self.handlers.on_list_presets, {})["presets"]
        self.assertEqual(len(presets), MAX_PRESETS + 2)  # 목록은 전부

    async def test_save_error_order(self):
        self.ctrl._set_target_viewport(None)
        reply = self._call(self.handlers.on_save_preset, {"name": "home"})
        self._assert_error(reply, "failed_precondition", name="home")
        self.assertEqual(self.ctrl.list_presets(), [])
        # 입력 오류가 대상 없음보다 먼저
        self._assert_error(self._call(self.handlers.on_save_preset, {"name": ""}), "invalid_argument")

    async def test_resource_exhausted_before_failed_precondition(self):
        for i in range(MAX_PRESETS):
            self.ctrl.save_preset(f"p{i:02d}")
        self.ctrl._set_target_viewport(None)
        reply = self._call(self.handlers.on_save_preset, {"name": "new"})
        self._assert_error(reply, "resource_exhausted", name="new")
        reply = self._call(self.handlers.on_save_preset, {"name": "p00"})  # 덮어쓰기는 대상 검사로
        self._assert_error(reply, "failed_precondition", name="p00")

    async def test_list_and_delete_work_without_camera(self):
        self._call(self.handlers.on_save_preset, {"name": "home"})
        self.ctrl._set_target_viewport(None)
        reply = self._call(self.handlers.on_list_presets, {})
        self.assertEqual([p["name"] for p in reply["presets"]], ["home"])
        self.assertEqual(self._call(self.handlers.on_delete_preset, {"name": "home"}), {"ok": True, "name": "home"})

    async def test_delete_unknown_replies_not_found(self):
        reply = self._call(self.handlers.on_delete_preset, {"name": "nope"}, TYPE_DELETE_PRESET)
        self.assertEqual(reply, {"ok": False, "error": "not_found", "message": "unknown preset", "name": "nope"})
        reply = self._call(self.handlers.on_delete_preset, {"name": None})
        self._assert_error(reply, "not_found", name="")

    async def test_apply_after_web_delete_replies_not_found(self):
        self._call(self.handlers.on_save_preset, {"name": "home"})
        self._call(self.handlers.on_delete_preset, {"name": "home"})
        reply = self._call(self.handlers.on_apply_preset, {"name": "home"})
        self._assert_error(reply, "not_found", name="home")
