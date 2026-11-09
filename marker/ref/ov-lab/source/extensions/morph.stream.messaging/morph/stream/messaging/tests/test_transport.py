"""EventChannelTransport 단위 테스트 (실제 carb.eventdispatcher 사용, 싱글턴과 무관한 채널 이름 사용 안 함).

startup 경로(_hook_livestream)를 livestream 없음/있음(가짜 모듈) 두 분기 모두 실행한다.
"""
import importlib.util
import json
import sys
import types

import omni.kit.app
import omni.kit.test
from carb.eventdispatcher import get_eventdispatcher

from ..transport import IN_CHANNEL, OUT_CHANNEL, EventChannelTransport

CONNECT_EVT = "morph.testtransport.connected"
DISCONNECT_EVT = "morph.testtransport.disconnected"
_LSM = "omni.kit.livestream.messaging"


def _livestream_importable() -> bool:
    if _LSM in sys.modules:
        return True
    try:
        return importlib.util.find_spec(_LSM) is not None
    except (ImportError, ValueError):
        return False


class TestEventChannelTransport(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.received = []
        self.connections = []
        self.t = EventChannelTransport(connect_events=[CONNECT_EVT], disconnect_events=[DISCONNECT_EVT])
        self.t.start(self.received.append, lambda cid, up: self.connections.append((cid, up)))

    async def tearDown(self):
        self.t.stop()
        # 싱글턴도 IN 채널을 관찰한다: 여기서 넣은 메시지(무효 처리됨)를 다음 테스트 전에 비운다
        for _ in range(2):
            await omni.kit.app.get_app().next_update_async()

    async def test_start_without_or_with_real_livestream(self):
        # 테스트 환경에서는 보통 livestream이 없다: 예외 없이 시작되고 비활성이어야 한다
        if not _livestream_importable():
            self.assertFalse(self.t.livestream_available)

    async def test_in_channel_string_and_dict(self):
        ed = get_eventdispatcher()
        # 싱글턴도 IN 채널을 관찰하므로, 어떤 익스텐션도 등록하지 않은 타입은 쓰지 않고 내용만 비교한다
        text = json.dumps({"type": "morph.testtransport.x", "v": 1, "id": None, "payload": {}})
        ed.dispatch_event(IN_CHANNEL, payload={"message": text})
        self.assertEqual(self.received[-1], text)
        ed.dispatch_event(IN_CHANNEL, payload={"type": "morph.testtransport.y", "v": 1})
        self.assertEqual(self.received[-1].get("type"), "morph.testtransport.y")

    async def test_in_channel_message_as_object(self):
        get_eventdispatcher().dispatch_event(
            IN_CHANNEL, payload={"message": {"type": "morph.testtransport.obj", "v": 1, "payload": {"a": 1}}}
        )
        got = self.received[-1]
        self.assertIsInstance(got, dict)
        self.assertEqual(got.get("type"), "morph.testtransport.obj")
        self.assertEqual(dict(got.get("payload")), {"a": 1})

    async def test_send_dispatches_out_channel(self):
        out = []
        sub = get_eventdispatcher().observe_event(
            observer_name="morph.stream.messaging.tests:transport_out",
            event_name=OUT_CHANNEL,
            on_event=lambda e: out.append(e.get("message")),
        )
        self.t.send('{"type":"morph.testtransport.z"}')
        sub = None  # noqa: F841
        self.assertEqual(out, ['{"type":"morph.testtransport.z"}'])

    async def test_connection_events_mapping(self):
        ed = get_eventdispatcher()
        ed.dispatch_event(CONNECT_EVT, payload={"client_id": "c1"})
        ed.dispatch_event(CONNECT_EVT, payload={"peerId": 7})
        ed.dispatch_event(DISCONNECT_EVT, payload={})
        self.assertEqual(self.connections, [("c1", True), ("7", True), ("default", False)])

    async def test_stop_detaches(self):
        self.t.stop()
        get_eventdispatcher().dispatch_event(IN_CHANNEL, payload={"message": "{}"})
        get_eventdispatcher().dispatch_event(CONNECT_EVT, payload={"client_id": "c1"})
        self.assertEqual(self.received, [])
        self.assertEqual(self.connections, [])

    async def test_hookup_with_fake_livestream_module(self):
        if _livestream_importable():
            return  # 실제 livestream이 있는 환경에서는 가짜 모듈로 덮지 않는다
        registered, unregistered = [], []
        fake = types.ModuleType(_LSM)
        fake.register_event_type_to_send = registered.append
        fake.unregister_event_type_to_send = unregistered.append
        parent = types.ModuleType("omni.kit.livestream")
        parent.__path__ = []
        parent.messaging = fake
        added = {"omni.kit.livestream": parent, _LSM: fake}
        added = {k: v for k, v in added.items() if k not in sys.modules}
        sys.modules.update(added)
        try:
            t = EventChannelTransport()
            t.start(lambda raw: None, lambda cid, up: None)
            self.assertTrue(t.livestream_available)
            self.assertEqual(registered, [OUT_CHANNEL])
            t.stop()
            self.assertEqual(unregistered, [OUT_CHANNEL])
            self.assertFalse(t.livestream_available)
        finally:
            for k in added:
                sys.modules.pop(k, None)
