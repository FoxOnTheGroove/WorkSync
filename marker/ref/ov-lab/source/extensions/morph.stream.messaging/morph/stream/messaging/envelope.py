"""메시지 봉투 직렬화/역직렬화 (내부 구현).

봉투: { "type": str, "v": int, "id": str|null, "payload": dict }
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple

# 봉투 "v" 기본값 (수신 시 생략하면 1)
DEFAULT_V = 1
# hello 회신의 "protocol" 값. 1.0.0에서 type 이름 규칙·회신 형식이 바뀌어 2.
HANDSHAKE_PROTOCOL = 2

_SEG = r"[a-z][a-z0-9_]*"
# 익스텐션 ID 규칙 (morph.<domain>.<name>)
OWNER_RE = re.compile(rf"^morph\.{_SEG}\.{_SEG}$")
# 웹 메시지 type 규칙: <owner ext-id>.<name> (name에는 점 없음)
TYPE_RE = re.compile(rf"^morph\.{_SEG}\.{_SEG}\.{_SEG}$")
# 회신 error code 규칙 (소문자 snake_case)
CODE_RE = re.compile(rf"^{_SEG}$")


class EnvelopeError(ValueError):
    """수신 봉투가 규격에 맞지 않을 때."""

    def __init__(self, reason: str, type_: str = "", id_: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.type = type_
        self.id = id_


def owner_of(type_: str) -> str:
    """'morph.viewport.split.set_layout' -> 'morph.viewport.split'"""
    return type_.rsplit(".", 1)[0]


def decode(raw: Any) -> Tuple[str, int, Optional[str], dict]:
    """JSON 문자열 또는 dict를 (type, v, id, payload)로 해석한다.

    누락 시 기본값: v=1, id=None, payload={}. 형식이 틀리면 EnvelopeError.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except RecursionError:
            raise EnvelopeError("JSON too deeply nested") from None
        except ValueError as e:
            raise EnvelopeError(f"invalid JSON: {getattr(e, 'msg', e)}") from None
    else:
        data = raw
    if not isinstance(data, dict):
        raise EnvelopeError("envelope must be a JSON object")

    type_ = data.get("type")
    id_ = data.get("id")
    if id_ is not None and not isinstance(id_, str):
        raise EnvelopeError("'id' must be a string or null", type_ if isinstance(type_, str) else "")
    if not isinstance(type_, str) or not type_:
        raise EnvelopeError("'type' must be a non-empty string", "", id_)

    v = data.get("v", DEFAULT_V)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise EnvelopeError("'v' must be a positive integer", type_, id_)

    payload = data.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise EnvelopeError("'payload' must be an object", type_, id_)
    return type_, v, id_, payload


def encode(type_: str, payload: dict, id_: Optional[str] = None, v: int = DEFAULT_V) -> str:
    """봉투를 JSON 문자열로 직렬화한다. 직렬화 불가 값이면 ValueError."""
    try:
        return json.dumps(
            {"type": type_, "v": v, "id": id_, "payload": payload},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as e:
        raise ValueError(f"payload of '{type_}' is not JSON-serializable: {e}") from None
