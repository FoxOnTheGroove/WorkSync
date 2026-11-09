"""전송 계층 어댑터 (내부 구현).

Messaging 코어는 이 어댑터 인터페이스만 안다:
    start(on_receive, on_connection) / send(text) / stop()

EventChannelTransport는 carb.eventdispatcher의 두 채널 이벤트로 웹과 통신한다.
    웹 -> Kit : IN_CHANNEL  이벤트, payload {"message": "<봉투 JSON 문자열>"}
    Kit -> 웹 : OUT_CHANNEL 이벤트, payload {"message": "<봉투 JSON 문자열>"}
omni.kit.livestream.messaging이 로드돼 있으면 OUT_CHANNEL을 송신 대상으로 등록해
실제 WebRTC 데이터 채널로 이어 준다. 없으면(테스트 환경 등) 이벤트 채널만 동작한다.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional

import carb
import carb.events
import omni.kit.app
from carb.eventdispatcher import get_eventdispatcher

IN_CHANNEL = "morph.stream.in"
OUT_CHANNEL = "morph.stream.out"

ReceiveFn = Callable[[Any], None]
ConnectionFn = Callable[[str, bool], None]


class Transport:
    """전송 계층 인터페이스."""

    livestream_available: bool = False

    def start(self, on_receive: ReceiveFn, on_connection: ConnectionFn) -> None:
        raise NotImplementedError

    def send(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


def _client_id_of(event: Any) -> str:
    for key in ("client_id", "clientId", "peer_id", "peerId", "id"):
        try:
            value = event.get(key)
        except Exception:  # noqa: BLE001
            value = None
        if value is not None and value != "":
            return str(value)
    return "default"


class EventChannelTransport(Transport):
    """carb.eventdispatcher 채널 + (있으면) omni.kit.livestream.messaging."""

    def __init__(self, connect_events: Iterable[str] = (), disconnect_events: Iterable[str] = ()):
        self._connect_events = [e for e in (connect_events or ()) if e]
        self._disconnect_events = [e for e in (disconnect_events or ()) if e]
        self._subs: List[Any] = []
        self._on_receive: Optional[ReceiveFn] = None
        self._on_connection: Optional[ConnectionFn] = None
        self._lsm: Any = None
        self.livestream_available = False

    # -- lifecycle -------------------------------------------------------
    def start(self, on_receive: ReceiveFn, on_connection: ConnectionFn) -> None:
        self._on_receive = on_receive
        self._on_connection = on_connection
        self.livestream_available = self._hook_livestream()

        ed = get_eventdispatcher()
        self._subs.append(
            ed.observe_event(
                observer_name="morph.stream.messaging:in",
                event_name=IN_CHANNEL,
                on_event=self._on_in_event,
            )
        )
        for name in self._connect_events:
            self._subs.append(
                ed.observe_event(
                    observer_name=f"morph.stream.messaging:connect:{name}",
                    event_name=name,
                    on_event=lambda e: self._notify_connection(e, True),
                )
            )
        for name in self._disconnect_events:
            self._subs.append(
                ed.observe_event(
                    observer_name=f"morph.stream.messaging:disconnect:{name}",
                    event_name=name,
                    on_event=lambda e: self._notify_connection(e, False),
                )
            )

    def stop(self) -> None:
        self._subs.clear()
        self._on_receive = None
        self._on_connection = None
        if self._lsm is not None:
            unregister = getattr(self._lsm, "unregister_event_type_to_send", None)
            if callable(unregister):
                try:
                    unregister(OUT_CHANNEL)
                except Exception as e:  # noqa: BLE001
                    carb.log_warn(f"[morph.stream.messaging] livestream unregister failed: {e}")
            self._lsm = None
        self.livestream_available = False

    # -- io --------------------------------------------------------------
    def send(self, text: str) -> None:
        get_eventdispatcher().dispatch_event(OUT_CHANNEL, payload={"message": text})

    def _on_in_event(self, event: Any) -> None:
        cb = self._on_receive
        if cb is None:
            return
        message = event.get("message")
        if isinstance(message, (str, bytes, bytearray, dict)):
            # 문자열: 봉투 JSON / dict: 봉투를 message에 객체로 실은 경우
            cb(dict(message) if isinstance(message, dict) else message)
        else:
            # 봉투를 payload에 직접 실어 보낸 경우도 허용
            cb(dict(event.payload))

    def _notify_connection(self, event: Any, connected: bool) -> None:
        cb = self._on_connection
        if cb is not None:
            cb(_client_id_of(event), connected)

    # -- livestream ------------------------------------------------------
    def _hook_livestream(self) -> bool:
        try:
            import omni.kit.livestream.messaging as lsm  # type: ignore
        except ImportError:
            carb.log_info("[morph.stream.messaging] omni.kit.livestream.messaging not loaded; event channel only")
            return False
        try:
            lsm.register_event_type_to_send(OUT_CHANNEL)
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[morph.stream.messaging] livestream messaging hookup failed: {e}")
            return False
        self._lsm = lsm
        # Events 1.0 메시지 버스 경유 전달과의 호환 (usd_viewer.messaging 템플릿과 같은 방식).
        # Kit 110.1.3에는 별칭 해제 API가 없어 프로세스 수명 동안 남는다. 재활성화 시 이미 등록돼
        # 있으면 False/예외가 날 수 있지만 훅업 실패로 치지 않는다.
        for name in (OUT_CHANNEL, IN_CHANNEL):
            try:
                omni.kit.app.register_event_alias(carb.events.type_from_string(name), name)
            except Exception as e:  # noqa: BLE001
                carb.log_info(f"[morph.stream.messaging] event alias for '{name}' not registered: {e}")
        return True
