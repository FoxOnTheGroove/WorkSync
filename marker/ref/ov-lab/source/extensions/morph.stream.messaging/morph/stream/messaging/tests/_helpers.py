"""테스트 공용 도우미 (전송 계층 없이 코어를 검증)."""
import json

from carb.eventdispatcher import get_eventdispatcher

from ..messaging import Messaging
from ..transport import Transport

OWNER = "morph.testmsg.owner"
OTHER_OWNER = "morph.testmsg.other"


class FakeTransport(Transport):
    def __init__(self):
        self.sent = []
        self.started = False
        self.stopped = False
        self._on_receive = None
        self._on_connection = None

    def start(self, on_receive, on_connection):
        self.started = True
        self._on_receive = on_receive
        self._on_connection = on_connection

    def send(self, text):
        self.sent.append(text)

    def stop(self):
        self.stopped = True

    # 웹 클라이언트 흉내
    def inject(self, raw):
        self._on_receive(raw)

    def inject_envelope(self, type_, payload=None, id_=None, v=1):
        self._on_receive(json.dumps({"type": type_, "v": v, "id": id_, "payload": payload or {}}))

    def connection(self, client_id, connected):
        self._on_connection(client_id, connected)

    def sent_envelopes(self):
        return [json.loads(t) for t in self.sent]


class CaptureLogger:
    def __init__(self):
        self.errors = []
        self.warns = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warns.append(msg)


def make_messaging(**kwargs):
    transport = FakeTransport()
    logger = CaptureLogger()
    m = Messaging(transport, auto_pump=False, logger=logger, **kwargs)
    return m, transport, logger


class EventRecorder:
    """carb.eventdispatcher 이벤트 페이로드를 모은다."""

    def __init__(self, *names):
        self.events = {n: [] for n in names}
        ed = get_eventdispatcher()
        self._subs = [
            ed.observe_event(
                observer_name=f"morph.stream.messaging.tests:{n}",
                event_name=n,
                on_event=lambda e, n=n: self.events[n].append(dict(e.payload)),
            )
            for n in names
        ]

    def close(self):
        self._subs = []
