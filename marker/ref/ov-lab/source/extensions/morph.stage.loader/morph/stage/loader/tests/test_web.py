"""웹 핸들러 (내부 핸들러 함수를 가짜 ctx로 직접 호출) + stream 등록 확인."""

import asyncio
import os
import tempfile

import omni.kit.test
import omni.usd

from morph.stage.loader import MSG_STATE, get_loader

from .. import web
from .helpers import SAMPLE_ID, FakeCtx, SettingsGuard, wait_until


class TestWeb(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.loader = get_loader()
        await wait_until(lambda: not self.loader.is_loading)

    async def tearDown(self):
        await wait_until(lambda: not self.loader.is_loading)
        await omni.usd.get_context().new_stage_async()

    async def test_types_registered(self):
        from morph.stream.messaging import get_messaging

        msg = get_messaging()
        self.assertIsNotNone(msg)
        self.assertEqual(MSG_STATE, "morph.stage.loader.state")
        # 이미 등록돼 있으므로 같은 type 재등록은 ValueError (= 이 익스텐션이 등록했음)
        for t in (web.MSG_LIST_SCENES, web.MSG_OPEN_SCENE, web.MSG_RELOAD, web.MSG_NEW_SCENE, web.MSG_GET_STATE):
            with self.assertRaises(ValueError, msg=t):
                msg.register_inbound(t, lambda p, c: None, owner="morph.stage.loader")
        # state 스키마로 실제 페이로드가 송신 가능해야 한다 (미연결이면 송신 생략, 예외 없음)
        msg.send(MSG_STATE, self.loader._state_payload())

    async def test_list_scenes(self):
        ctx = FakeCtx()
        web.handle_list_scenes({}, ctx)
        r = ctx.payload
        self.assertTrue(r["ok"])
        ids = [s["id"] for s in r["scenes"]]
        self.assertIn(SAMPLE_ID, ids)
        for s in r["scenes"]:
            self.assertEqual(set(s), {"id", "name", "root", "size_bytes"})  # path 미노출
        self.assertIn("current_scene_id", r)

        ctx = FakeCtx()
        web.handle_list_scenes({"refresh": True}, ctx)
        self.assertTrue(ctx.payload["ok"])

    async def test_open_scene_invalid_and_not_found(self):
        for payload in ({}, {"id": ""}, {"id": 3}):
            ctx = FakeCtx()
            web.handle_open_scene(payload, ctx)
            self.assertEqual(ctx.payload["error"], "invalid_argument", payload)
        ctx = FakeCtx()
        web.handle_open_scene({"id": "../../etc/passwd"}, ctx)
        self.assertEqual(ctx.payload["error"], "not_found")
        ctx = FakeCtx()
        web.handle_open_scene({"id": "C:/Windows/win.ini"}, ctx)
        self.assertEqual(ctx.payload["error"], "not_found")
        self.assertFalse(self.loader.is_loading)

    async def test_open_scene_path_variants_not_found(self):
        # 목록 id와 글자 그대로 같은 것만 연다: 경로 정규화·대소문자·절대 경로 변형은 모두 not_found
        info = self.loader.find_scene(SAMPLE_ID)
        self.assertIsNotNone(info)
        for scene_id in (
            "samples/../samples/morph_demo.usda",
            "samples\\morph_demo.usda",
            "SAMPLES/morph_demo.usda",
            info.path,
            info.path.replace("\\", "/"),
            "/samples/morph_demo.usda",
        ):
            ctx = FakeCtx()
            web.handle_open_scene({"id": scene_id}, ctx)
            self.assertEqual(ctx.payload.get("error"), "not_found", scene_id)
        self.assertFalse(self.loader.is_loading)

    async def test_list_refresh_rate_limited(self):
        guard = SettingsGuard()
        tmp = tempfile.TemporaryDirectory()
        try:
            def write(name):
                with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as f:
                    f.write("#usda 1.0\n")

            def ids(refresh):
                ctx = FakeCtx()
                web.handle_list_scenes({"refresh": refresh}, ctx)
                self.assertTrue(ctx.payload["ok"])
                return [s["id"] for s in ctx.payload["scenes"] if s["root"] == "tmp"]

            write("a.usda")
            guard.set("roots", [f"tmp={tmp.name}"])
            web._last_web_refresh = 0.0
            self.assertEqual(ids(True), ["tmp/a.usda"])
            write("b.usda")
            self.assertEqual(ids(True), ["tmp/a.usda"])  # 1초 안의 refresh는 캐시
            web._last_web_refresh -= 2.0
            self.assertEqual(ids(True), ["tmp/a.usda", "tmp/b.usda"])
        finally:
            guard.restore()
            self.loader.list_scenes(refresh=True)
            tmp.cleanup()

    async def test_unavailable_after_shutdown(self):
        # shutdown된 로더(_alive False)면 모든 요청은 unavailable (failed_precondition 아님)
        self.loader._alive = False
        try:
            for handler, payload in (
                (web.handle_list_scenes, {}),
                (web.handle_get_state, {}),
                (web.handle_open_scene, {"id": SAMPLE_ID}),
                (web.handle_reload, {}),
                (web.handle_new_scene, {}),
            ):
                ctx = FakeCtx()
                handler(payload, ctx)
                self.assertEqual(ctx.payload.get("error"), "unavailable", handler.__name__)
        finally:
            self.loader._alive = True
        self.assertFalse(self.loader.is_loading)

    async def test_cancelled_finish_replies_unavailable(self):
        class _Stub:
            _alive = True

            async def _run(self, op):
                await asyncio.sleep(3600)

        ctx = FakeCtx()
        task = asyncio.ensure_future(web._finish(_Stub(), None, ctx, lambda: ctx.reply_ok()))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(ctx.payload.get("error"), "unavailable")

    async def test_open_scene_deferred_reply_and_busy(self):
        ctx = FakeCtx()
        web.handle_open_scene({"id": SAMPLE_ID}, ctx)
        # 지연 회신: 핸들러 반환 시점에는 아직 회신 없음, 이미 loading
        self.assertFalse(ctx.replied)
        self.assertTrue(self.loader.is_loading)
        state = FakeCtx()
        web.handle_get_state({}, state)
        self.assertEqual(state.payload["loading"], True)

        for handler, payload in (
            (web.handle_open_scene, {"id": SAMPLE_ID}),
            (web.handle_reload, {}),
            (web.handle_new_scene, {}),
        ):
            busy = FakeCtx()
            handler(payload, busy)
            self.assertEqual(busy.payload["error"], "failed_precondition", handler.__name__)

        r = await ctx.wait()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["scene"], {"id": SAMPLE_ID, "name": "morph_demo.usda", "root": "samples",
                                      "size_bytes": self.loader.find_scene(SAMPLE_ID).size_bytes})
        state = FakeCtx()
        web.handle_get_state({}, state)
        self.assertEqual(state.payload, {"ok": True, "scene_id": SAMPLE_ID, "name": "morph_demo.usda", "loading": False})

        # reload
        ctx = FakeCtx()
        web.handle_reload({}, ctx)
        r = await ctx.wait()
        self.assertEqual(r, {"ok": True, "scene_id": SAMPLE_ID})

        # new_scene, 그 뒤 reload는 failed_precondition
        ctx = FakeCtx()
        web.handle_new_scene({}, ctx)
        r = await ctx.wait()
        self.assertEqual(r, {"ok": True})
        state = FakeCtx()
        web.handle_get_state({}, state)
        self.assertEqual(state.payload, {"ok": True, "scene_id": None, "name": None, "loading": False})
        ctx = FakeCtx()
        web.handle_reload({}, ctx)
        self.assertEqual(ctx.payload["error"], "failed_precondition")
