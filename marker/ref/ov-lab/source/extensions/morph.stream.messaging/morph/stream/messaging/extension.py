"""morph.stream.messaging 익스텐션 진입점."""
from __future__ import annotations

import carb
import carb.settings
import omni.ext

from .messaging import Messaging, _set_instance, get_messaging
from .transport import EventChannelTransport

_SETTINGS_ROOT = "/exts/morph.stream.messaging"


def _setting(settings, key: str, default):
    value = settings.get(f"{_SETTINGS_ROOT}/{key}")
    return default if value is None else value


def _name_list(value) -> list:
    """설정 값을 이벤트 이름 목록으로. 문자열 하나면 [s] (글자 단위로 쪼개지 않음), 빈 값은 제외."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)
        except TypeError:
            items = [value]
    return [str(v) for v in items if v is not None and str(v) != ""]


class MessagingExtension(omni.ext.IExt):
    """Messaging 싱글턴의 수명을 관리한다."""

    def on_startup(self, ext_id):
        settings = carb.settings.get_settings()
        transport = EventChannelTransport(
            connect_events=_name_list(_setting(settings, "connectEvents", None)),
            disconnect_events=_name_list(_setting(settings, "disconnectEvents", None)),
        )
        self._messaging = Messaging(
            transport,
            validate=bool(_setting(settings, "validate", True)),
            max_queue=int(_setting(settings, "maxQueue", 1024)),
        )
        _set_instance(self._messaging)
        carb.log_info(
            f"[morph.stream.messaging] started (livestream={'on' if transport.livestream_available else 'off'})"
        )

    def on_shutdown(self):
        if get_messaging() is self._messaging:
            _set_instance(None)
        if self._messaging is not None:
            self._messaging.shutdown()
            self._messaging = None
