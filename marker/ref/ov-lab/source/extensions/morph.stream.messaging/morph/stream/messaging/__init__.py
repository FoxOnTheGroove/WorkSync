"""morph.stream.messaging 공개 API. CONTRACT.md에 적힌 심볼만 공개 계약이다."""

from .extension import MessagingExtension
from .messaging import (
    EVENT_CLIENT_CONNECTED,
    EVENT_CLIENT_DISCONNECTED,
    EVENT_INVALID_MESSAGE,
    MessageContext,
    Messaging,
    Subscription,
    get_messaging,
)

__all__ = [
    "get_messaging",
    "Messaging",
    "MessageContext",
    "Subscription",
    "EVENT_CLIENT_CONNECTED",
    "EVENT_CLIENT_DISCONNECTED",
    "EVENT_INVALID_MESSAGE",
]
# MessagingExtension은 Kit이 IExt를 찾도록 최상위에 import만 해 둔다 (공개 API 아님).
