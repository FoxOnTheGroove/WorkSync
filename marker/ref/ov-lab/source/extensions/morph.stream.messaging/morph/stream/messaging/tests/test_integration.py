"""싱글턴 + 이벤트 채널 전송 계층 통합 테스트 (livestream 없이 동작).

다른 익스텐션처럼 공개 API(get_messaging)만으로 등록하고,
웹 클라이언트는 morph.stream.in / morph.stream.out 채널 이벤트로 흉내 낸다.
"""
import json
import threading

import omni.kit.app
import omni.kit.test
from carb.eventdispatcher import get_eventdispatcher

from morph.stream.messaging import EVENT_INVALID_MESSAGE, get_messaging

from ..transport import IN_CHANNEL, OUT_CHANNEL
from ._helpers import EventRecorder

OWNER = "morph.testint.owner"
PING = f"{OWNER}.ping"
STATE = f"{OWNER}.state"
BOOM = f"{OWNER}.boom"
MSG_ERROR = "morph.stream.messaging.error"


class TestIntegration(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.app = omni.kit.app.get_app()
        self.subs = []
        self.out = []
        self.out_sub = get_eventdispatcher().observe_event(
            observer_name="morph.stream.messaging.tests:out",
            event_name=OUT_CHANNEL,
            on_event=lambda e: self.out.append(json.loads(e.get("message"))),
        )

    async def tearDown(self):
        for s in self.subs:
            s.unsubscribe()
        self.out_sub = None

    async def _frames(self, n=3):
        for _ in range(n):
            await self.app.next_update_async()

    def _client_send(self, envelope):
        get_eventdispatcher().dispatch_event(IN_CHANNEL, payload={"message": json.dumps(envelope)})

    async def test_singleton_available(self):
        m = get_messaging()
        self.assertIsNotNone(m)
        self.assertIs(m, get_messaging())

    async def test_roundtrip_on_main_loop(self):
        m = get_messaging()
        loop_thread = threading.get_ident()
        seen = []

        def on_ping(payload, ctx):
            seen.append((payload, threading.get_ident()))
            ctx.reply_ok()
            m.send(STATE, {"value": payload["value"]})

        self.subs.append(m.register_outbound(STATE, owner=OWNER))
        self.subs.append(
            m.register_inbound(
                PING,
                on_ping,
                schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "number"}}},
                owner=OWNER,
            )
        )
        self._client_send({"type": PING, "v": 1, "id": "c-1", "payload": {"value": 3}})
        await self._frames()

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], {"value": 3})
        self.assertEqual(seen[0][1], loop_thread, "핸들러는 메인 업데이트 루프 스레드에서 호출돼야 한다")
        self.assertIn({"type": PING, "v": 1, "id": "c-1", "payload": {"ok": True}}, self.out)
        self.assertIn({"type": STATE, "v": 1, "id": None, "payload": {"value": 3}}, self.out)

    async def test_invalid_message_event(self):
        m = get_messaging()
        rec = EventRecorder(EVENT_INVALID_MESSAGE)
        try:
            self.subs.append(
                m.register_inbound(PING, lambda p, c: None, schema={"type": "object", "required": ["value"]}, owner=OWNER)
            )
            self._client_send({"type": PING, "v": 1, "id": "c-2", "payload": {}})
            await self._frames()
            self.assertEqual(len(rec.events[EVENT_INVALID_MESSAGE]), 1)
            self.assertEqual(rec.events[EVENT_INVALID_MESSAGE][0]["type"], PING)
            self.assertEqual(rec.events[EVENT_INVALID_MESSAGE][0]["error"], "invalid_payload")
            errors = [e for e in self.out if e["type"] == MSG_ERROR]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["id"], "c-2")
            self.assertEqual(errors[0]["payload"]["ok"], False)
            self.assertEqual(errors[0]["payload"]["error"], "invalid_payload")
        finally:
            rec.close()

    async def test_handler_exception_replies_same_type(self):
        m = get_messaging()

        def boom(payload, ctx):
            raise KeyError("missing")

        self.subs.append(m.register_inbound(BOOM, boom, owner=OWNER))
        self._client_send({"type": BOOM, "v": 1, "id": "c-3", "payload": {}})
        await self._frames()
        replies = [e for e in self.out if e["id"] == "c-3"]
        self.assertEqual(
            replies,
            [{"type": BOOM, "v": 1, "id": "c-3", "payload": {"ok": False, "error": "handler_error", "message": "KeyError: 'missing'"}}],
        )

    async def test_hello_handshake(self):
        self._client_send({"type": "morph.stream.messaging.hello", "v": 1, "id": "h-1", "payload": {"client_id": "int-web"}})
        await self._frames()
        try:
            self.assertTrue(get_messaging().is_connected)
            self.assertIn(
                {
                    "type": "morph.stream.messaging.hello",
                    "v": 1,
                    "id": "h-1",
                    "payload": {"ok": True, "client_id": "int-web", "protocol": 2},
                },
                self.out,
            )
        finally:
            self._client_send({"type": "morph.stream.messaging.bye", "v": 1, "id": None, "payload": {"client_id": "int-web"}})
            await self._frames()

    async def test_worker_thread_send_is_delivered(self):
        m = get_messaging()
        self.subs.append(m.register_outbound(STATE, owner=OWNER))
        th = threading.Thread(target=lambda: m.send(STATE, {"value": 42}))
        th.start()
        th.join()
        await self._frames()
        self.assertIn({"type": STATE, "v": 1, "id": None, "payload": {"value": 42}}, self.out)
