"""Messaging 코어: 타입 등록, 봉투 검증, 메인 스레드 라우팅, 송신 큐."""
from __future__ import annotations

import threading
import traceback
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

import carb
import omni.kit.app
from carb.eventdispatcher import get_eventdispatcher

from . import envelope as _env
from . import schema as _schema
from .transport import Transport

EXT_ID = "morph.stream.messaging"

# 발행 이벤트 (carb.eventdispatcher)
EVENT_CLIENT_CONNECTED = "morph.stream.client_connected"
EVENT_CLIENT_DISCONNECTED = "morph.stream.client_disconnected"
EVENT_INVALID_MESSAGE = "morph.stream.invalid_message"

# 자체 소유 웹 메시지 타입 (<owner>.<name>)
MSG_ERROR = f"{EXT_ID}.error"
MSG_HELLO = f"{EXT_ID}.hello"
MSG_BYE = f"{EXT_ID}.bye"

# 실패 code (CONTRACT "실패 code" 표)
ERR_BAD_ENVELOPE = "bad_envelope"
ERR_UNKNOWN_TYPE = "unknown_type"
ERR_UNSUPPORTED_VERSION = "unsupported_version"
ERR_INVALID_PAYLOAD = "invalid_payload"
ERR_INTERNAL = "internal_error"
ERR_HANDLER = "handler_error"

# handler_error 회신 message 최대 길이
_MAX_ERROR_MESSAGE = 500

Handler = Callable[[dict, "MessageContext"], None]


class _CarbLogger:
    @staticmethod
    def error(msg: str) -> None:
        carb.log_error(msg)

    @staticmethod
    def warn(msg: str) -> None:
        carb.log_warn(msg)


class Subscription:
    """등록 해제 핸들. GC로 자동 해제되지 않는다. unsubscribe()는 여러 번 불러도 안전하다."""

    __slots__ = ("_release",)

    def __init__(self, release: Callable[[], None]):
        self._release: Optional[Callable[[], None]] = release

    def unsubscribe(self) -> None:
        release, self._release = self._release, None
        if release is not None:
            release()


class MessageContext:
    """수신 메시지 컨텍스트. 핸들러 호출 동안과 이후(아무 스레드) 모두 회신 가능. 회신은 한 번만."""

    __slots__ = ("_messaging", "type", "v", "id", "_reply_v", "_replied")

    def __init__(self, messaging: "Messaging", type_: str, v: int, id_: Optional[str], reply_v: int = 1):
        self._messaging = messaging
        self.type: str = type_
        self.v: int = v
        self.id: Optional[str] = id_
        self._reply_v = reply_v
        self._replied = False

    @property
    def replied(self) -> bool:
        """이미 회신했으면 True (id가 null이라 실제 송신이 생략된 경우 포함)."""
        return self._replied

    def reply(self, payload: dict) -> None:
        """같은 type, 같은 id로 웹에 회신한다. payload는 { "ok": bool, ... } 형식이어야 한다."""
        self._messaging._reply(self, payload)

    def reply_ok(self, **fields: Any) -> None:
        """성공 회신: { "ok": true, **fields }."""
        if "ok" in fields:
            raise ValueError("reply_ok fields must not contain 'ok'")
        self.reply({"ok": True, **fields})

    def reply_error(self, code: str, message: str, **fields: Any) -> None:
        """실패 회신: { "ok": false, "error": code, "message": message, **fields }."""
        if "ok" in fields or "error" in fields:
            raise ValueError("reply_error fields must not contain 'ok' or 'error'")
        self.reply({"ok": False, "error": code, "message": message, **fields})


@dataclass
class _Inbound:
    handler: Handler
    schema: Optional[dict]
    version: int
    token: object


@dataclass
class _Outbound:
    schema: Optional[dict]
    version: int
    token: object


@dataclass
class _TypeEntry:
    owner: str
    inbound: Optional[_Inbound] = None
    outbound: Optional[_Outbound] = None


class Messaging:
    """웹 클라이언트 메시지 통로. 인스턴스는 get_messaging()으로 얻는다."""

    def __init__(
        self,
        transport: Transport,
        *,
        validate: bool = True,
        max_queue: int = 1024,
        auto_pump: bool = True,
        logger: Any = None,
    ):
        self._transport = transport
        self._validate = bool(validate)
        self._max_queue = max(1, int(max_queue))
        self._log = logger or _CarbLogger()
        self._lock = threading.RLock()
        self._types: Dict[str, _TypeEntry] = {}
        self._inbox: deque = deque()
        self._outbox: deque = deque()
        self._dropped_in = 0
        self._dropped_out = 0
        self._clients: set = set()
        self._closed = False
        self._own_subs = []

        self._register_builtin_types()
        transport.start(self._on_transport_receive, self._on_transport_connection)

        self._update_sub = None
        if auto_pump:
            self._update_sub = get_eventdispatcher().observe_event(
                observer_name="morph.stream.messaging:pump",
                event_name=omni.kit.app.GLOBAL_EVENT_UPDATE,
                on_event=lambda _e: self._pump(),
            )

    # ------------------------------------------------------------------ public
    def register_inbound(
        self,
        type: str,  # noqa: A002 - CONTRACT 시그니처 유지
        handler: Handler,
        schema: Optional[dict] = None,
        *,
        owner: str,
        version: int = 1,
    ) -> Subscription:
        """웹->Kit 메시지 핸들러 등록. 같은 type 중복 등록 또는 다른 소유자면 ValueError."""
        if not callable(handler):
            raise ValueError("handler must be callable")
        self._check_names(type, owner)
        self._check_version(version)
        if schema is not None:
            _schema.check_schema(schema)
        token = object()
        with self._lock:
            self._check_open()
            entry = self._entry_for(type, owner)
            if entry.inbound is not None:
                raise ValueError(f"inbound type '{type}' is already registered by '{entry.owner}'")
            entry.inbound = _Inbound(handler, schema, version, token)
        return Subscription(lambda: self._release(type, "inbound", token))

    def register_outbound(
        self,
        type: str,  # noqa: A002
        schema: Optional[dict] = None,
        *,
        owner: str,
        version: int = 1,
    ) -> Subscription:
        """Kit->웹 메시지 타입 선언. 같은 owner가 다시 선언하면 스키마/버전을 교체한다.

        재선언하면 이전에 받은 Subscription은 무효가 된다 (unsubscribe()는 아무 일도 하지 않음).
        """
        self._check_names(type, owner)
        self._check_version(version)
        if schema is not None:
            _schema.check_schema(schema)
        token = object()
        with self._lock:
            self._check_open()
            entry = self._entry_for(type, owner)
            entry.outbound = _Outbound(schema, version, token)
        return Subscription(lambda: self._release(type, "outbound", token))

    def send(self, type: str, payload: dict, reply_to: Optional[str] = None) -> None:  # noqa: A002
        """웹으로 송신. 아무 스레드에서 호출 가능. 미선언 타입/스키마 위반/직렬화 불가면 ValueError."""
        if reply_to is not None and not isinstance(reply_to, str):
            raise ValueError("reply_to must be a str or None")
        with self._lock:
            if self._closed:
                return  # shutdown 이후 송신은 조용히 버린다
            entry = self._types.get(type)
            outbound = entry.outbound if entry else None
        if outbound is None:
            raise ValueError(f"outbound type '{type}' is not declared (call register_outbound first)")
        self._enqueue_out(type, payload, reply_to, outbound.version, outbound.schema)

    @property
    def is_connected(self) -> bool:
        """웹 클라이언트가 하나 이상 연결돼 있으면 True."""
        with self._lock:
            return bool(self._clients)

    # ---------------------------------------------------------------- internal
    def shutdown(self) -> None:
        if self._closed:
            return
        try:
            self._pump()  # 남은 송신 최대한 전달
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._closed = True
            clients = list(self._clients)
            self._clients.clear()
            self._types.clear()
            self._inbox.clear()
            self._outbox.clear()
        self._update_sub = None
        self._own_subs = []
        for cid in clients:
            self._dispatch(EVENT_CLIENT_DISCONNECTED, {"client_id": cid})
        try:
            self._transport.stop()
        except Exception as e:  # noqa: BLE001
            self._log.warn(f"[{EXT_ID}] transport stop failed: {e}")

    def _register_builtin_types(self) -> None:
        self._own_subs = [
            self.register_outbound(
                MSG_ERROR,
                {
                    "type": "object",
                    "required": ["ok", "error", "message", "type"],
                    "properties": {
                        "ok": {"type": "boolean", "enum": [False]},
                        "error": {
                            "type": "string",
                            "enum": [
                                ERR_BAD_ENVELOPE,
                                ERR_UNKNOWN_TYPE,
                                ERR_UNSUPPORTED_VERSION,
                                ERR_INVALID_PAYLOAD,
                                ERR_INTERNAL,
                            ],
                        },
                        "message": {"type": "string"},
                        "type": {"type": "string"},
                    },
                },
                owner=EXT_ID,
            ),
            self.register_inbound(
                MSG_HELLO,
                self._on_hello,
                {"type": "object", "properties": {"client_id": {"type": "string"}}},
                owner=EXT_ID,
            ),
            self.register_inbound(
                MSG_BYE,
                self._on_bye,
                {"type": "object", "properties": {"client_id": {"type": "string"}}},
                owner=EXT_ID,
            ),
        ]

    @staticmethod
    def _check_names(type_: Any, owner: Any) -> None:
        if not isinstance(owner, str) or not _env.OWNER_RE.match(owner):
            raise ValueError(f"owner must be an extension id like 'morph.<domain>.<name>', got {owner!r}")
        if not isinstance(type_, str) or not _env.TYPE_RE.match(type_):
            raise ValueError(
                f"type must be '<owner>.<name>' with <name> matching [a-z][a-z0-9_]* "
                f"(e.g. '{owner}.do_thing'), got {type_!r}"
            )
        if _env.owner_of(type_) != owner:
            raise ValueError(f"type '{type_}' must start with owner prefix '{owner}.'")

    @staticmethod
    def _check_version(version: Any) -> None:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive int")

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"{EXT_ID} is shut down")

    def _entry_for(self, type_: str, owner: str) -> _TypeEntry:
        entry = self._types.get(type_)
        if entry is None:
            entry = _TypeEntry(owner)
            self._types[type_] = entry
        elif entry.owner != owner:
            raise ValueError(f"type '{type_}' is owned by '{entry.owner}', not '{owner}'")
        return entry

    def _release(self, type_: str, kind: str, token: object) -> None:
        with self._lock:
            entry = self._types.get(type_)
            if entry is None:
                return
            current = getattr(entry, kind)
            if current is None or current.token is not token:
                return
            setattr(entry, kind, None)
            if entry.inbound is None and entry.outbound is None:
                del self._types[type_]

    # -- outbound ----------------------------------------------------------
    def _encode_out(
        self, type_: str, payload: Any, id_: Optional[str], version: int, schema: Optional[dict]
    ) -> str:
        if not isinstance(payload, dict):
            raise ValueError(f"payload of '{type_}' must be a dict")
        if self._validate and schema:
            try:
                reason = _schema.validate(payload, schema)
            except RecursionError:
                reason = "payload too deeply nested"
            if reason:
                raise ValueError(f"outbound '{type_}' failed schema: {reason}")
        try:
            return _env.encode(type_, payload, id_, version)
        except RecursionError:
            raise ValueError(f"payload of '{type_}' is too deeply nested") from None

    def _append_out(self, text: str) -> None:
        """잠금을 잡은 상태에서 호출한다."""
        self._outbox.append(text)
        while len(self._outbox) > self._max_queue:
            self._outbox.popleft()
            self._dropped_out += 1

    def _enqueue_out(
        self, type_: str, payload: Any, id_: Optional[str], version: int, schema: Optional[dict]
    ) -> None:
        text = self._encode_out(type_, payload, id_, version, schema)
        with self._lock:
            if self._closed:
                return
            self._append_out(text)

    @staticmethod
    def _check_reply_payload(type_: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ValueError(f"reply payload of '{type_}' must be a dict")
        ok = payload.get("ok")
        if not isinstance(ok, bool):
            raise ValueError(f"reply payload of '{type_}' must have bool 'ok'")
        if not ok:
            code = payload.get("error")
            if not isinstance(code, str) or not _env.CODE_RE.match(code):
                raise ValueError(
                    f"failed reply of '{type_}' must have 'error' code matching [a-z][a-z0-9_]*, got {code!r}"
                )
            if not isinstance(payload.get("message"), str):
                raise ValueError(f"failed reply of '{type_}' must have str 'message'")

    def _reply(self, ctx: MessageContext, payload: Any) -> None:
        """회신: 같은 type·id, v = 수신 시점의 inbound 등록 버전. 출력 스키마 검증은 하지 않는다."""
        if self._closed:
            return  # shutdown 이후 회신은 조용히 버린다
        self._check_reply_payload(ctx.type, payload)
        # id가 null이어도 형식·직렬화 오류는 똑같이 드러나도록 먼저 직렬화한다
        text = self._encode_out(ctx.type, payload, ctx.id, ctx._reply_v, None)
        with self._lock:
            if self._closed:
                return
            if ctx._replied:
                raise RuntimeError(f"'{ctx.type}' (id {ctx.id!r}) has already been replied to")
            ctx._replied = True
            if ctx.id is not None:
                self._append_out(text)

    # -- inbound -----------------------------------------------------------
    def _on_transport_receive(self, raw: Any) -> None:
        """전송 계층 콜백. 아무 스레드에서 올 수 있으므로 큐에만 넣는다."""
        self._push_inbox(("msg", raw))

    def _on_transport_connection(self, client_id: str, connected: bool) -> None:
        self._push_inbox(("conn", (client_id, connected)))

    def _push_inbox(self, item: tuple) -> None:
        with self._lock:
            if self._closed:
                return
            self._inbox.append(item)
            # 넘치면 가장 오래된 "msg"만 버린다. 연결 항목("conn")은 상태 일관성을 위해 보존.
            while len(self._inbox) > self._max_queue:
                idx = next((i for i, it in enumerate(self._inbox) if it[0] == "msg"), None)
                if idx is None:
                    break
                del self._inbox[idx]
                self._dropped_in += 1

    def _pump(self) -> None:
        """메인 업데이트 루프에서 호출: 수신 라우팅 후 송신 큐 비우기.

        수신 항목 하나의 실패가 나머지 항목이나 송신 플러시를 막지 않는다.
        """
        try:
            self._drain_inbox()
        finally:
            self._flush_outbox()

    def _drain_inbox(self) -> None:
        with self._lock:
            inbox, self._inbox = self._inbox, deque()
            dropped_in, self._dropped_in = self._dropped_in, 0
        if dropped_in:
            self._log.warn(f"[{EXT_ID}] inbound queue overflow: dropped {dropped_in} message(s)")
        for kind, data in inbox:
            try:
                if kind == "msg":
                    self._route(data)
                else:
                    self._set_connected(*data)
            except Exception as e:  # noqa: BLE001 - 항목 단위 격리 (RecursionError 포함)
                self._log.error(f"[{EXT_ID}] failed to process inbound {kind} item: {e!r}")
                if kind == "msg":
                    try:
                        self._invalid("", ERR_INTERNAL, f"internal error: {type(e).__name__}", None)
                    except Exception:  # noqa: BLE001
                        pass

    def _flush_outbox(self) -> None:
        with self._lock:
            outbox, self._outbox = self._outbox, deque()
            dropped_out, self._dropped_out = self._dropped_out, 0
        if dropped_out:
            self._log.warn(f"[{EXT_ID}] outbound queue overflow: dropped {dropped_out} message(s)")
        for text in outbox:
            try:
                self._transport.send(text)
            except Exception as e:  # noqa: BLE001
                self._log.error(f"[{EXT_ID}] transport send failed: {e}")

    def _route(self, raw: Any) -> None:
        try:
            type_, v, id_, payload = _env.decode(raw)
        except _env.EnvelopeError as e:
            self._invalid(e.type, ERR_BAD_ENVELOPE, e.reason, e.id)
            return

        with self._lock:
            entry = self._types.get(type_)
            inbound = entry.inbound if entry else None
        if inbound is None:
            self._invalid(type_, ERR_UNKNOWN_TYPE, "unknown inbound type", id_)
            return
        if v > inbound.version:
            self._invalid(type_, ERR_UNSUPPORTED_VERSION, f"unsupported version {v} (max {inbound.version})", id_)
            return
        if self._validate and inbound.schema:
            try:
                reason = _schema.validate(payload, inbound.schema)
            except RecursionError:
                reason = "payload too deeply nested"
            if reason:
                self._invalid(type_, ERR_INVALID_PAYLOAD, reason, id_)
                return

        ctx = MessageContext(self, type_, v, id_, inbound.version)
        try:
            inbound.handler(payload, ctx)
        except Exception as e:  # noqa: BLE001 - 핸들러 예외 격리
            self._log.error(
                f"[{EXT_ID}] handler for '{type_}' (owner {entry.owner}) raised: {e!r}\n{traceback.format_exc()}"
            )
            self._reply_handler_error(ctx, e)

    def _reply_handler_error(self, ctx: MessageContext, exc: BaseException) -> None:
        """잡히지 않은 핸들러 예외 → 같은 type·id로 handler_error 회신 (id null이거나 이미 회신했으면 생략)."""
        if ctx.id is None or ctx.replied:
            return
        try:
            try:
                detail = str(exc)
            except Exception:  # noqa: BLE001
                detail = "<unprintable>"
            message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
            if len(message) > _MAX_ERROR_MESSAGE:
                message = message[: _MAX_ERROR_MESSAGE - 3] + "..."
            ctx.reply_error(ERR_HANDLER, message)
        except Exception as e:  # noqa: BLE001
            self._log.warn(f"[{EXT_ID}] could not send handler_error reply for '{ctx.type}': {e}")

    def _invalid(self, type_: str, code: str, reason: str, id_: Optional[str]) -> None:
        self._log.warn(f"[{EXT_ID}] invalid message '{type_}' ({code}): {reason}")
        self._dispatch(EVENT_INVALID_MESSAGE, {"type": type_ or "", "error": code, "reason": reason})
        self._send_error(type_, code, reason, id_)

    def _send_error(self, type_: str, code: str, message: str, id_: Optional[str]) -> None:
        try:
            self.send(
                MSG_ERROR,
                {"ok": False, "error": code, "message": message, "type": type_ or ""},
                reply_to=id_,
            )
        except Exception as e:  # noqa: BLE001
            self._log.warn(f"[{EXT_ID}] could not send error reply: {e}")

    def _dispatch(self, name: str, payload: dict) -> None:
        try:
            get_eventdispatcher().dispatch_event(name, payload=payload)
        except Exception as e:  # noqa: BLE001
            self._log.error(f"[{EXT_ID}] dispatching '{name}' failed: {e}")

    # -- connection --------------------------------------------------------
    def _set_connected(self, client_id: str, connected: bool) -> None:
        with self._lock:
            if connected:
                changed = client_id not in self._clients
                self._clients.add(client_id)
            else:
                changed = client_id in self._clients
                self._clients.discard(client_id)
        if changed:
            self._dispatch(
                EVENT_CLIENT_CONNECTED if connected else EVENT_CLIENT_DISCONNECTED, {"client_id": client_id}
            )

    @staticmethod
    def _client_id_from(payload: dict) -> Optional[str]:
        # validate=false면 스키마가 적용되지 않으므로 str이 아닐 수 있다
        value = payload.get("client_id")
        if value is None or value == "":
            return None
        return value if isinstance(value, str) else str(value)

    def _on_hello(self, payload: dict, ctx: MessageContext) -> None:
        client_id = self._client_id_from(payload) or uuid.uuid4().hex
        self._set_connected(client_id, True)
        ctx.reply_ok(client_id=client_id, protocol=_env.HANDSHAKE_PROTOCOL)

    def _on_bye(self, payload: dict, ctx: MessageContext) -> None:
        client_id = self._client_id_from(payload)
        if client_id:
            targets: Iterable[str] = [client_id]
        else:
            with self._lock:
                targets = list(self._clients)
        for cid in targets:
            self._set_connected(cid, False)
        ctx.reply_ok()


# ---------------------------------------------------------------- singleton
_instance: Optional[Messaging] = None


def get_messaging() -> Optional[Messaging]:
    """싱글턴. 이 익스텐션 startup 이후 유효, shutdown 이후 None."""
    return _instance


def _set_instance(instance: Optional[Messaging]) -> None:
    global _instance
    _instance = instance
