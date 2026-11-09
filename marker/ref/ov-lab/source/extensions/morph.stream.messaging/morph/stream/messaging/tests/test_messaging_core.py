"""Messaging 코어 단위 테스트 (FakeTransport, 수동 pump)."""
import json
import threading

import omni.kit.test

from ..extension import _name_list
from ..messaging import (
    EVENT_CLIENT_CONNECTED,
    EVENT_CLIENT_DISCONNECTED,
    EVENT_INVALID_MESSAGE,
)
from ._helpers import OTHER_OWNER, OWNER, EventRecorder, make_messaging

PING = f"{OWNER}.ping"
STATE = f"{OWNER}.state"
OK_TYPE = f"{OWNER}.ok"
MSG_ERROR = "morph.stream.messaging.error"
MSG_HELLO = "morph.stream.messaging.hello"
MSG_BYE = "morph.stream.messaging.bye"
VALUE_SCHEMA = {"type": "object", "required": ["value"], "properties": {"value": {"type": "number"}}}


class TestRegistration(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.m, self.t, self.log = make_messaging()

    async def tearDown(self):
        self.m.shutdown()

    async def test_transport_started_and_stopped(self):
        self.assertTrue(self.t.started)
        self.m.shutdown()
        self.assertTrue(self.t.stopped)

    async def test_duplicate_inbound_raises(self):
        self.m.register_inbound(PING, lambda p, c: None, owner=OWNER)
        with self.assertRaises(ValueError):
            self.m.register_inbound(PING, lambda p, c: None, owner=OWNER)

    async def test_type_owned_by_other_extension_raises(self):
        self.m.register_outbound(STATE, owner=OWNER)
        with self.assertRaises(ValueError):
            self.m.register_inbound(STATE, lambda p, c: None, owner=OTHER_OWNER)
        with self.assertRaises(ValueError):
            self.m.register_outbound(STATE, owner=OTHER_OWNER)

    async def test_builtin_types_are_reserved(self):
        with self.assertRaises(ValueError):  # 다른 owner
            self.m.register_inbound(MSG_HELLO, lambda p, c: None, owner="morph.stream.other")
        with self.assertRaises(ValueError):  # 같은 owner라도 중복 등록
            self.m.register_inbound(MSG_HELLO, lambda p, c: None, owner="morph.stream.messaging")
        with self.assertRaises(ValueError):
            self.m.register_outbound(MSG_ERROR, owner="morph.stream.other")

    async def test_name_rules(self):
        h = lambda p, c: None  # noqa: E731
        with self.assertRaises(ValueError):  # 다른 익스텐션 접두어
            self.m.register_inbound("morph.camera.control.orbit", h, owner=OWNER)
        with self.assertRaises(ValueError):  # 같은 domain이라도 owner 전체 접두어가 아니면 거부
            self.m.register_inbound(f"{OTHER_OWNER}.ping", h, owner=OWNER)
        with self.assertRaises(ValueError):  # 0.x domain 접두어 형식은 더 이상 허용 안 함
            self.m.register_inbound("morph.testmsg.ping", h, owner=OWNER)
        with self.assertRaises(ValueError):  # <name>에 점 금지
            self.m.register_inbound(f"{OWNER}.sub.ping", h, owner=OWNER)
        with self.assertRaises(ValueError):  # <name> 대문자 금지
            self.m.register_outbound(f"{OWNER}.Ping", owner=OWNER)
        with self.assertRaises(ValueError):  # <name> 숫자 시작 금지
            self.m.register_outbound(f"{OWNER}.1ping", owner=OWNER)
        with self.assertRaises(ValueError):  # <name> 빈 값
            self.m.register_outbound(f"{OWNER}.", owner=OWNER)
        with self.assertRaises(ValueError):  # owner 형식
            self.m.register_inbound(PING, h, owner="testmsg")
        with self.assertRaises(ValueError):  # type 형식
            self.m.register_outbound("Morph.testmsg.owner.x", owner=OWNER)
        with self.assertRaises(TypeError):  # owner는 keyword-only
            self.m.register_inbound(PING, h, None, OWNER)  # type: ignore[misc]
        # 허용: 소문자·숫자·_
        self.m.register_inbound(f"{OWNER}.set_layout_2", h, owner=OWNER)

    async def test_invalid_schema_or_handler_rejected(self):
        with self.assertRaises(ValueError):
            self.m.register_inbound(PING, lambda p, c: None, schema={"type": "float"}, owner=OWNER)
        with self.assertRaises(ValueError):
            self.m.register_inbound(PING, "not callable", owner=OWNER)  # type: ignore[arg-type]

    async def test_outbound_redeclare_same_owner_is_idempotent(self):
        self.m.register_outbound(STATE, owner=OWNER)
        self.m.register_outbound(STATE, VALUE_SCHEMA, owner=OWNER, version=2)
        self.m.send(STATE, {"value": 1})
        self.m._pump()
        self.assertEqual(self.t.sent_envelopes()[-1]["v"], 2)

    async def test_unsubscribe_removes_handler_and_frees_type(self):
        calls = []
        sub = self.m.register_inbound(PING, lambda p, c: calls.append(p), owner=OWNER)
        sub.unsubscribe()
        sub.unsubscribe()  # 두 번 불러도 안전
        self.t.inject_envelope(PING, {"x": 1})
        self.m._pump()
        self.assertEqual(calls, [])
        self.assertEqual(self.t.sent_envelopes()[-1]["payload"]["error"], "unknown_type")
        # 해제 후에는 같은 owner가 다시 등록할 수 있다
        self.m.register_inbound(PING, lambda p, c: None, owner=OWNER)

    async def test_stale_subscription_after_redeclare_is_noop(self):
        sub_old = self.m.register_outbound(STATE, owner=OWNER)
        sub_new = self.m.register_outbound(STATE, owner=OWNER)  # 재선언 → sub_old 무효
        sub_old.unsubscribe()
        self.m.send(STATE, {"value": 1})  # 여전히 선언돼 있어야 함
        sub_new.unsubscribe()
        with self.assertRaises(ValueError):
            self.m.send(STATE, {"value": 1})

    async def test_outbound_unsubscribe(self):
        sub = self.m.register_outbound(STATE, owner=OWNER)
        sub.unsubscribe()
        with self.assertRaises(ValueError):
            self.m.send(STATE, {"value": 1})


class TestRouting(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.m, self.t, self.log = make_messaging()
        self.rec = EventRecorder(EVENT_INVALID_MESSAGE, EVENT_CLIENT_CONNECTED, EVENT_CLIENT_DISCONNECTED)

    async def tearDown(self):
        self.m.shutdown()
        self.rec.close()

    async def test_routes_payload_and_context(self):
        got = []
        self.m.register_inbound(PING, lambda p, c: got.append((p, c.type, c.v, c.id)), VALUE_SCHEMA, owner=OWNER)
        self.t.inject_envelope(PING, {"value": 3}, id_="abc")
        self.assertEqual(got, [], "핸들러는 pump(메인 업데이트) 전에 호출되면 안 된다")
        self.m._pump()
        self.assertEqual(got, [({"value": 3}, PING, 1, "abc")])

    async def test_accepts_dict_envelope_and_defaults(self):
        got = []
        self.m.register_inbound(PING, lambda p, c: got.append((p, c.v, c.id)), owner=OWNER)
        self.t.inject({"type": PING})
        self.m._pump()
        self.assertEqual(got, [({}, 1, None)])

    async def test_schema_failure_is_not_routed(self):
        got = []
        self.m.register_inbound(PING, lambda p, c: got.append(p), VALUE_SCHEMA, owner=OWNER)
        self.t.inject_envelope(PING, {"value": "three"}, id_="r1")
        self.t.inject_envelope(PING, {}, id_="r2")
        self.m._pump()
        self.assertEqual(got, [])
        invalid = self.rec.events[EVENT_INVALID_MESSAGE]
        self.assertEqual(len(invalid), 2)
        self.assertEqual(invalid[0]["type"], PING)
        self.assertEqual(invalid[0]["error"], "invalid_payload")
        self.assertIn("value", invalid[0]["reason"])
        errors = self.t.sent_envelopes()
        self.assertEqual([e["type"] for e in errors], [MSG_ERROR] * 2)
        self.assertEqual([e["id"] for e in errors], ["r1", "r2"])
        p = errors[0]["payload"]
        self.assertEqual(set(p.keys()), {"ok", "error", "message", "type"})
        self.assertIs(p["ok"], False)
        self.assertEqual(p["error"], "invalid_payload")
        self.assertEqual(p["type"], PING)
        self.assertEqual(p["message"], invalid[0]["reason"])

    async def test_validate_disabled_skips_schema(self):
        self.m.shutdown()
        self.m, self.t, self.log = make_messaging(validate=False)
        got = []
        self.m.register_inbound(PING, lambda p, c: got.append(p), VALUE_SCHEMA, owner=OWNER)
        self.t.inject_envelope(PING, {"value": "three"})
        self.m._pump()
        self.assertEqual(got, [{"value": "three"}])

    async def test_bad_envelopes(self):
        self.m.register_inbound(PING, lambda p, c: None, owner=OWNER)
        self.t.inject("{not json")
        self.t.inject("[1, 2]")
        self.t.inject(json.dumps({"type": PING, "payload": [1]}))
        self.t.inject(json.dumps({"type": PING, "v": 0}))
        self.t.inject(json.dumps({"type": PING, "id": 5}))
        self.t.inject(json.dumps({"v": 1, "payload": {}}))
        self.m._pump()
        invalid = self.rec.events[EVENT_INVALID_MESSAGE]
        self.assertEqual(len(invalid), 6)
        self.assertEqual({e["error"] for e in invalid}, {"bad_envelope"})
        sent = self.t.sent_envelopes()
        self.assertEqual(len(sent), 6)
        self.assertEqual({e["type"] for e in sent}, {MSG_ERROR})
        self.assertEqual({e["payload"]["error"] for e in sent}, {"bad_envelope"})

    async def test_error_without_id_is_still_sent(self):
        """핸들러 전 실패는 id가 null이어도 messaging.error로 알린다 (id: null)."""
        self.t.inject_envelope(f"{OWNER}.nobody", {})
        self.m._pump()
        sent = self.t.sent_envelopes()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], MSG_ERROR)
        self.assertIsNone(sent[0]["id"])

    async def test_unknown_type(self):
        self.t.inject_envelope(f"{OWNER}.nobody", {}, id_="q")
        self.m._pump()
        self.assertEqual(self.rec.events[EVENT_INVALID_MESSAGE][0]["type"], f"{OWNER}.nobody")
        self.assertEqual(self.rec.events[EVENT_INVALID_MESSAGE][0]["error"], "unknown_type")
        err = self.t.sent_envelopes()[0]
        self.assertEqual(err["id"], "q")
        self.assertEqual(err["payload"]["error"], "unknown_type")

    async def test_newer_version_rejected(self):
        got = []
        self.m.register_inbound(PING, lambda p, c: got.append(c.v), owner=OWNER, version=2)
        self.t.inject_envelope(PING, {}, v=1)
        self.t.inject_envelope(PING, {}, v=2)
        self.t.inject_envelope(PING, {}, v=3, id_="v3")
        self.m._pump()
        self.assertEqual(got, [1, 2])
        self.assertEqual(len(self.rec.events[EVENT_INVALID_MESSAGE]), 1)
        err = self.t.sent_envelopes()[0]
        self.assertEqual((err["id"], err["payload"]["error"]), ("v3", "unsupported_version"))

    async def test_handler_exception_is_isolated(self):
        ok = []

        def boom(p, c):
            raise RuntimeError("boom")

        self.m.register_inbound(PING, boom, owner=OWNER)
        self.m.register_inbound(OK_TYPE, lambda p, c: ok.append(p), owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="b")
        self.t.inject_envelope(OK_TYPE, {"n": 1})
        self.m._pump()
        self.assertEqual(ok, [{"n": 1}])
        self.assertEqual(len(self.log.errors), 1)
        self.assertIn("boom", self.log.errors[0])
        self.assertEqual(
            self.t.sent_envelopes(),
            [
                {
                    "type": PING,
                    "v": 1,
                    "id": "b",
                    "payload": {"ok": False, "error": "handler_error", "message": "RuntimeError: boom"},
                }
            ],
        )
        # 핸들러 예외는 검증 실패 이벤트가 아니다
        self.assertEqual(self.rec.events[EVENT_INVALID_MESSAGE], [])

    async def test_handler_exception_without_id_is_not_replied(self):
        def boom(p, c):
            raise ValueError("bad")

        self.m.register_inbound(PING, boom, owner=OWNER)
        self.t.inject_envelope(PING, {})
        self.m._pump()
        self.assertEqual(self.t.sent, [])
        self.assertEqual(len(self.log.errors), 1)

    async def test_handler_exception_after_reply_is_not_replied_again(self):
        def reply_then_boom(p, c):
            c.reply_ok()
            raise RuntimeError("late")

        self.m.register_inbound(PING, reply_then_boom, owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="x")
        self.m._pump()
        self.assertEqual(self.t.sent_envelopes(), [{"type": PING, "v": 1, "id": "x", "payload": {"ok": True}}])
        self.assertEqual(len(self.log.errors), 1)

    async def test_handler_error_message_is_truncated(self):
        def boom(p, c):
            raise RuntimeError("x" * 2000)

        self.m.register_inbound(PING, boom, owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="t")
        self.m._pump()
        msg = self.t.sent_envelopes()[0]["payload"]["message"]
        self.assertTrue(msg.startswith("RuntimeError: xxx"))
        self.assertLessEqual(len(msg), 500)

    async def test_reply_uses_same_type_and_id(self):
        self.m.register_inbound(PING, lambda p, c: c.reply({"ok": True, "pong": p["n"]}), owner=OWNER)
        self.t.inject_envelope(PING, {"n": 7}, id_="corr-1")
        self.m._pump()
        self.assertEqual(
            self.t.sent_envelopes(), [{"type": PING, "v": 1, "id": "corr-1", "payload": {"ok": True, "pong": 7}}]
        )

    async def test_reply_ok_and_reply_error(self):
        def handler(p, c):
            if p.get("fail"):
                c.reply_error("not_found", "no such thing", name="a")
            else:
                c.reply_ok(value=1)

        self.m.register_inbound(PING, handler, owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="s")
        self.t.inject_envelope(PING, {"fail": True}, id_="f")
        self.m._pump()
        self.assertEqual(
            [e["payload"] for e in self.t.sent_envelopes()],
            [
                {"ok": True, "value": 1},
                {"ok": False, "error": "not_found", "message": "no such thing", "name": "a"},
            ],
        )
        self.assertEqual([e["id"] for e in self.t.sent_envelopes()], ["s", "f"])

    async def test_reply_payload_rules(self):
        """회신 형식 위반은 ValueError → 핸들러 밖으로 나가면 handler_error 회신."""
        bad_payloads = [
            {"pong": 1},  # ok 없음
            {"ok": "yes"},  # ok가 bool 아님
            {"ok": False, "message": "m"},  # error 없음
            {"ok": False, "error": "Not Found", "message": "m"},  # code 형식
            {"ok": False, "error": "x"},  # message 없음
            [1],  # dict 아님
        ]
        results = []

        def handler(p, c):
            try:
                c.reply(bad_payloads[p["i"]])
            except ValueError:
                results.append("ValueError")
            self.assertFalse(c.replied, "실패한 회신은 replied로 치지 않는다")
            c.reply_ok()

        self.m.register_inbound(PING, handler, owner=OWNER)
        for i in range(len(bad_payloads)):
            self.t.inject_envelope(PING, {"i": i}, id_=str(i))
        self.m._pump()
        self.assertEqual(results, ["ValueError"] * len(bad_payloads))
        self.assertEqual([e["payload"] for e in self.t.sent_envelopes()], [{"ok": True}] * len(bad_payloads))

    async def test_reply_helper_reserved_fields(self):
        errors = []

        def handler(p, c):
            for call in (lambda: c.reply_ok(ok=False), lambda: c.reply_error("x", "m", ok=True),
                         lambda: c.reply_error("x", "m", error="y")):
                try:
                    call()
                except ValueError:
                    errors.append("ValueError")
            c.reply_ok()

        self.m.register_inbound(PING, handler, owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="r")
        self.m._pump()
        self.assertEqual(errors, ["ValueError"] * 3)
        self.assertEqual(len(self.t.sent), 1)

    async def test_reply_only_once(self):
        seen = []

        def handler(p, c):
            self.assertFalse(c.replied)
            c.reply_ok()
            self.assertTrue(c.replied)
            try:
                c.reply_error("again", "second")
            except RuntimeError:
                seen.append("RuntimeError")

        self.m.register_inbound(PING, handler, owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="o")
        self.m._pump()
        self.assertEqual(seen, ["RuntimeError"])
        self.assertEqual(len(self.t.sent), 1)

    async def test_reply_without_id_is_not_sent_but_validated(self):
        seen = []

        def handler(p, c):
            with self.assertRaises(ValueError):
                c.reply({"no_ok": 1})  # id가 null이어도 형식 오류는 드러난다
            c.reply_ok()
            seen.append(c.replied)

        self.m.register_inbound(PING, handler, owner=OWNER)
        self.t.inject_envelope(PING, {})
        self.m._pump()
        self.assertEqual(seen, [True])
        self.assertEqual(self.t.sent, [])

    async def test_reply_v_is_inbound_registration_version(self):
        ctxs = []
        self.m.register_inbound(PING, lambda p, c: ctxs.append(c), owner=OWNER, version=3)
        self.t.inject_envelope(PING, {}, id_="d", v=1)
        self.m._pump()
        ctxs[0].reply_ok()  # 핸들러 반환 뒤 지연 회신
        self.m._pump()
        env = self.t.sent_envelopes()[0]
        self.assertEqual((env["type"], env["id"], env["v"]), (PING, "d", 3))

    async def test_deferred_reply_from_worker_thread(self):
        ctxs = []
        self.m.register_inbound(PING, lambda p, c: ctxs.append(c), owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="w")
        self.m._pump()
        th = threading.Thread(target=lambda: ctxs[0].reply_ok(done=True))
        th.start()
        th.join()
        self.m._pump()
        self.assertEqual(self.t.sent_envelopes(), [{"type": PING, "v": 1, "id": "w", "payload": {"ok": True, "done": True}}])

    async def test_reply_after_shutdown_is_ignored(self):
        ctxs = []
        self.m.register_inbound(PING, lambda p, c: ctxs.append(c), owner=OWNER)
        self.t.inject_envelope(PING, {}, id_="z")
        self.m._pump()
        self.m.shutdown()
        ctxs[0].reply_ok()  # 예외 없음
        ctxs[0].reply({"not": "even valid"})  # shutdown 이후에는 형식 검사도 하지 않는다
        self.assertEqual(self.t.sent, [])

    async def test_hello_and_bye_publish_connection_events(self):
        self.assertFalse(self.m.is_connected)
        self.t.inject_envelope(MSG_HELLO, {"client_id": "web-1"}, id_="h")
        self.m._pump()
        self.assertTrue(self.m.is_connected)
        self.assertEqual(self.rec.events[EVENT_CLIENT_CONNECTED], [{"client_id": "web-1"}])
        welcome = self.t.sent_envelopes()[0]
        self.assertEqual(welcome["type"], MSG_HELLO)
        self.assertEqual(welcome["id"], "h")
        self.assertEqual(welcome["payload"], {"ok": True, "client_id": "web-1", "protocol": 2})
        # 중복 hello는 이벤트를 다시 내지 않는다. id null이면 회신 없음
        self.t.inject_envelope(MSG_HELLO, {"client_id": "web-1"})
        self.t.inject_envelope(MSG_BYE, {}, id_="b")
        self.m._pump()
        self.assertEqual(len(self.rec.events[EVENT_CLIENT_CONNECTED]), 1)
        self.assertEqual(self.rec.events[EVENT_CLIENT_DISCONNECTED], [{"client_id": "web-1"}])
        self.assertFalse(self.m.is_connected)
        self.assertEqual(
            self.t.sent_envelopes()[1:], [{"type": MSG_BYE, "v": 1, "id": "b", "payload": {"ok": True}}]
        )

    async def test_hello_without_client_id_generates_one(self):
        self.t.inject_envelope(MSG_HELLO, {}, id_="h")
        self.m._pump()
        cid = self.rec.events[EVENT_CLIENT_CONNECTED][0]["client_id"]
        self.assertTrue(cid)
        self.assertEqual(self.t.sent_envelopes()[0]["payload"]["client_id"], cid)

    async def test_old_builtin_names_are_unknown(self):
        self.t.inject_envelope("morph.stream.hello", {}, id_="old")
        self.m._pump()
        self.assertFalse(self.m.is_connected)
        err = self.t.sent_envelopes()[0]
        self.assertEqual((err["type"], err["payload"]["error"]), (MSG_ERROR, "unknown_type"))

    async def test_transport_connection_callbacks(self):
        self.t.connection("peer-9", True)
        self.assertEqual(self.rec.events[EVENT_CLIENT_CONNECTED], [], "pump 전에는 발행하지 않는다")
        self.m._pump()
        self.assertEqual(self.rec.events[EVENT_CLIENT_CONNECTED], [{"client_id": "peer-9"}])
        self.t.connection("peer-9", False)
        self.m._pump()
        self.assertEqual(self.rec.events[EVENT_CLIENT_DISCONNECTED], [{"client_id": "peer-9"}])

    async def test_shutdown_disconnects_clients_and_ignores_late_calls(self):
        sub = self.m.register_outbound(STATE, owner=OWNER)
        self.t.connection("peer-1", True)
        self.m._pump()
        self.m.shutdown()
        self.assertEqual(self.rec.events[EVENT_CLIENT_DISCONNECTED], [{"client_id": "peer-1"}])
        self.m.send(STATE, {"value": 1})  # no-op, 예외 없음
        sub.unsubscribe()  # no-op, 예외 없음
        with self.assertRaises(RuntimeError):
            self.m.register_outbound(STATE, owner=OWNER)


class TestSend(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.m, self.t, self.log = make_messaging()

    async def tearDown(self):
        self.m.shutdown()

    async def test_envelope_format(self):
        self.m.register_outbound(STATE, VALUE_SCHEMA, owner=OWNER)
        self.m.send(STATE, {"value": 1.5})
        self.m.send(STATE, {"value": 2}, reply_to="x-1")
        self.assertEqual(self.t.sent, [], "송신은 pump(메인 업데이트)에서 전달된다")
        self.m._pump()
        self.assertEqual(
            self.t.sent_envelopes(),
            [
                {"type": STATE, "v": 1, "id": None, "payload": {"value": 1.5}},
                {"type": STATE, "v": 1, "id": "x-1", "payload": {"value": 2}},
            ],
        )
        self.assertEqual(set(json.loads(self.t.sent[0]).keys()), {"type", "v", "id", "payload"})

    async def test_send_errors(self):
        with self.assertRaises(ValueError):  # 미선언
            self.m.send(STATE, {"value": 1})
        self.m.register_outbound(STATE, VALUE_SCHEMA, owner=OWNER)
        with self.assertRaises(ValueError):  # 스키마 위반
            self.m.send(STATE, {"value": "x"})
        with self.assertRaises(ValueError):  # dict 아님
            self.m.send(STATE, [1])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):  # 직렬화 불가
            self.m.send(STATE, {"value": 1, "obj": object()})
        with self.assertRaises(ValueError):  # NaN
            self.m.send(STATE, {"value": float("nan")})
        with self.assertRaises(ValueError):
            self.m.send(STATE, {"value": 1}, reply_to=3)  # type: ignore[arg-type]
        deep = []
        for _ in range(100000):
            deep = [deep]
        with self.assertRaises(ValueError):  # 너무 깊은 중첩 (RecursionError가 아니라 ValueError)
            self.m.send(STATE, {"value": 1, "deep": deep})
        self.m._pump()
        self.assertEqual(self.t.sent, [])

    async def test_payload_is_snapshotted_at_send(self):
        self.m.register_outbound(STATE, owner=OWNER)
        payload = {"value": 1}
        self.m.send(STATE, payload)
        payload["value"] = 2
        self.m._pump()
        self.assertEqual(self.t.sent_envelopes()[0]["payload"], {"value": 1})

    async def test_send_from_worker_threads(self):
        self.m.register_outbound(STATE, owner=OWNER)

        def worker(base):
            for i in range(50):
                self.m.send(STATE, {"value": base + i})

        threads = [threading.Thread(target=worker, args=(k * 100,)) for k in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.m._pump()
        values = sorted(e["payload"]["value"] for e in self.t.sent_envelopes())
        self.assertEqual(values, sorted(k * 100 + i for k in range(4) for i in range(50)))

    async def test_max_queue_drops_oldest(self):
        self.m.shutdown()
        self.m, self.t, self.log = make_messaging(max_queue=3)
        self.m.register_outbound(STATE, owner=OWNER)
        for i in range(5):
            self.m.send(STATE, {"value": i})
        self.m._pump()
        self.assertEqual([e["payload"]["value"] for e in self.t.sent_envelopes()], [2, 3, 4])
        self.assertTrue(any("dropped 2" in w for w in self.log.warns))


class TestRobustness(omni.kit.test.AsyncTestCase):
    """수신 항목 하나의 실패가 나머지 수신·연결 항목과 같은 프레임의 송신을 막지 않는다."""

    async def setUp(self):
        self.m, self.t, self.log = make_messaging()
        self.rec = EventRecorder(EVENT_INVALID_MESSAGE, EVENT_CLIENT_CONNECTED)
        self.got = []
        self.m.register_outbound(STATE, owner=OWNER)
        self.m.register_inbound(PING, self._on_ping, owner=OWNER)

    async def tearDown(self):
        self.m.shutdown()
        self.rec.close()

    def _on_ping(self, payload, ctx):
        self.got.append(payload)
        ctx.reply_ok(pong=True)

    def _types_sent(self):
        return [e["type"] for e in self.t.sent_envelopes()]

    async def test_deeply_nested_json_then_normal_message(self):
        depth = 100000
        deep = '{"type":"%s","payload":{"a":%s%s}}' % (PING, "[" * depth, "]" * depth)
        self.m.send(STATE, {"value": 1})  # 같은 프레임에 나갈 송신
        self.t.inject(deep)
        self.t.connection("c-1", True)
        self.t.inject_envelope(PING, {"n": 1}, id_="after")
        self.m._pump()
        self.assertEqual(self.got, [{"n": 1}])
        self.assertEqual(self.rec.events[EVENT_CLIENT_CONNECTED], [{"client_id": "c-1"}])
        self.assertEqual(len(self.rec.events[EVENT_INVALID_MESSAGE]), 1)
        self.assertIn("deeply nested", self.rec.events[EVENT_INVALID_MESSAGE][0]["reason"])
        self.assertEqual(self.rec.events[EVENT_INVALID_MESSAGE][0]["error"], "bad_envelope")
        self.assertEqual(sorted(self._types_sent()), sorted([STATE, MSG_ERROR, PING]))
        self.assertEqual(self.log.errors, [])

    async def test_schema_recursion_error_is_invalid(self):
        from .. import schema as schema_mod

        deep_type = f"{OWNER}.deep"
        self.m.register_inbound(deep_type, lambda p, c: self.got.append(p), {"type": "object"}, owner=OWNER)
        original = schema_mod.validate

        def recursing(value, schema, *a, **k):
            if value == {"x": 1}:  # 대상 수신 payload에만 반응 (오류 회신 검증은 정상 동작)
                raise RecursionError("maximum recursion depth exceeded")
            return original(value, schema, *a, **k)

        schema_mod.validate = recursing
        try:
            self.t.inject_envelope(deep_type, {"x": 1}, id_="d")
            self.t.inject_envelope(PING, {"n": 2})
            self.m._pump()
        finally:
            schema_mod.validate = original
        self.assertEqual(self.got, [{"n": 2}])
        self.assertEqual(self.rec.events[EVENT_INVALID_MESSAGE][0]["reason"], "payload too deeply nested")
        self.assertEqual(self.rec.events[EVENT_INVALID_MESSAGE][0]["error"], "invalid_payload")
        err = [e for e in self.t.sent_envelopes() if e["type"] == MSG_ERROR]
        self.assertEqual(err[0]["id"], "d")

    async def test_unexpected_item_failure_is_isolated_and_flush_runs(self):
        original = self.m._route
        calls = {"n": 0}

        def flaky(raw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("unexpected")
            original(raw)

        self.m._route = flaky
        self.m.send(STATE, {"value": 1})
        self.t.inject_envelope(PING, {"n": 1})
        self.t.inject_envelope(PING, {"n": 2}, id_="p2")
        self.m._pump()
        self.assertEqual(self.got, [{"n": 2}])
        self.assertEqual(len(self.log.errors), 1)
        invalid = self.rec.events[EVENT_INVALID_MESSAGE][0]
        self.assertEqual((invalid["type"], invalid["error"]), ("", "internal_error"))
        self.assertEqual(invalid["reason"], "internal error: RuntimeError")
        internal = [e for e in self.t.sent_envelopes() if e["type"] == MSG_ERROR]
        self.assertEqual(internal[0]["payload"]["error"], "internal_error")
        self.assertIsNone(internal[0]["id"])
        self.assertIn(STATE, self._types_sent())
        self.assertIn(PING, self._types_sent())

    async def test_overflow_keeps_connection_items(self):
        self.m.shutdown()
        self.m, self.t, self.log = make_messaging(max_queue=2)
        self.m.register_inbound(PING, self._on_ping, owner=OWNER)
        self.t.connection("c-9", True)
        for i in range(4):
            self.t.inject_envelope(PING, {"n": i})
        self.m._pump()
        self.assertEqual(self.rec.events[EVENT_CLIENT_CONNECTED], [{"client_id": "c-9"}])
        self.assertEqual(self.got, [{"n": 3}])
        self.assertTrue(any("dropped 3" in w for w in self.log.warns))

    async def test_non_str_client_id_with_validation_off(self):
        self.m.shutdown()
        self.m, self.t, self.log = make_messaging(validate=False)
        self.t.inject_envelope(MSG_HELLO, {"client_id": 5}, id_="h")
        self.m._pump()
        self.assertEqual(self.rec.events[EVENT_CLIENT_CONNECTED], [{"client_id": "5"}])
        self.assertEqual(self.t.sent_envelopes()[0]["payload"]["client_id"], "5")


class TestSettingsParsing(omni.kit.test.AsyncTestCase):
    async def test_event_name_list(self):
        self.assertEqual(_name_list(None), [])
        self.assertEqual(_name_list("omni.kit.livestream.connected"), ["omni.kit.livestream.connected"])
        self.assertEqual(_name_list(["a", "b"]), ["a", "b"])
        self.assertEqual(_name_list(("a",)), ["a"])
        self.assertEqual(_name_list(["a", "", None]), ["a"])
        self.assertEqual(_name_list(""), [])
