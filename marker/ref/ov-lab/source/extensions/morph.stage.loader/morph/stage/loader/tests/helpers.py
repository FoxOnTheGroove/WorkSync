"""테스트 공용: 이벤트 수집기, 가짜 MessageContext."""

import asyncio

import carb.settings
import omni.kit.app
from carb.eventdispatcher import get_eventdispatcher

from ..loader import S_ROOT

SAMPLE_ID = "samples/morph_demo.usda"
SETTING_KEYS = ("roots", "includeSamples", "fileExtensions", "maxDepth", "maxScenes")


class EventCollector:
    def __init__(self, *names):
        self.events = []
        self._subs = [
            get_eventdispatcher().observe_event(
                observer_name=f"morph.stage.loader.tests:{n}", event_name=n, on_event=self._on
            )
            for n in names
        ]

    def _on(self, e):
        self.events.append((e.event_name, dict(e.payload)))

    def named(self, name):
        return [p for n, p in self.events if n == name]

    def close(self):
        self._subs = []


class FakeCtx:
    """morph.stream.messaging MessageContext 대역 (reply_ok / reply_error 한 번)."""

    def __init__(self, type_="test", id_="req-1"):
        self.type = type_
        self.id = id_
        self.v = 1
        self.replied = False
        self.payload = None
        self._done = asyncio.get_event_loop().create_future()

    def reply_ok(self, **fields):
        self._set({"ok": True, **fields})

    def reply_error(self, code, message, **fields):
        self._set({"ok": False, "error": code, "message": message, **fields})

    def _set(self, payload):
        if self.replied:
            raise RuntimeError("already replied")
        self.replied = True
        self.payload = payload
        if not self._done.done():
            self._done.set_result(payload)

    async def wait(self, timeout=30.0):
        return await asyncio.wait_for(asyncio.shield(self._done), timeout)


class SettingsGuard:
    """테스트 동안 바꾼 로더 설정을 원래대로 돌린다."""

    def __init__(self):
        self._s = carb.settings.get_settings()
        self._saved = {k: self._s.get(f"{S_ROOT}/{k}") for k in SETTING_KEYS}

    def set(self, key, value):
        path = f"{S_ROOT}/{key}"
        if isinstance(value, (list, tuple)):
            self._s.set_string_array(path, [str(v) for v in value])
        else:
            self._s.set(path, value)

    def restore(self):
        for k, v in self._saved.items():
            if v is None:
                if k in ("roots", "fileExtensions"):
                    self.set(k, [])
                else:
                    self._s.destroy_item(f"{S_ROOT}/{k}")
            else:
                self.set(k, list(v) if isinstance(v, (list, tuple)) else v)


async def wait_until(pred, frames=600):
    app = omni.kit.app.get_app()
    for _ in range(frames):
        if pred():
            return True
        await app.next_update_async()
    return pred()


async def wait_frames(n=10):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()
