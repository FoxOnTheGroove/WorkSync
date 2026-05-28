import asyncio
import time
from typing import Callable

import omni.kit.app

PLAY_DURATION = 1.0


class UVMixerPlayer:
    """재생 루프와 tick 알림을 담당하는 플레이어.
    omni 의존성 없음 — seek_timeline 등 외부 동작은 콜백으로 주입한다.

    tick 콜백 시그니처:    (t: float, correction: bool)
    stopped 콜백 시그니처: ()
    """

    def __init__(self) -> None:
        self._t: float = 0.0
        self._speed: float = 1.0
        self._forward: bool = True
        self._loop: bool = False
        self._play_task: asyncio.Task | None = None
        self._tick_cbs: list[Callable[[float, bool], None]] = []
        self._stopped_cbs: list[Callable[[], None]] = []

    # ── 재생 제어 ──────────────────────────────────────────────────────

    def play(self) -> None:
        self.stop()
        self._play_task = asyncio.ensure_future(self._animate())

    def stop(self) -> None:
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
        self._play_task = None

    def is_playing(self) -> bool:
        return self._play_task is not None and not self._play_task.done()

    # ── t / 설정 ───────────────────────────────────────────────────────

    def set_t(self, t: float, *, correction: bool = True) -> None:
        self._t = max(0.0, min(1.0, t))
        self._notify_tick(self._t, correction)

    def set_speed(self, v: float) -> None:
        self._speed = max(0.1, float(v))

    def set_forward(self, v: bool) -> None:
        self._forward = bool(v)

    def set_loop(self, v: bool) -> None:
        self._loop = bool(v)

    # ── 상태 조회 ──────────────────────────────────────────────────────

    @property
    def t(self) -> float:
        return self._t

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def forward(self) -> bool:
        return self._forward

    @property
    def loop(self) -> bool:
        return self._loop

    # ── 상태 복사 (sync 진입 시) ───────────────────────────────────────

    def copy_from(self, other: 'UVMixerPlayer') -> None:
        """다른 플레이어의 t/speed/forward/loop를 복사한다. play_task는 복사하지 않는다."""
        self._t = other._t
        self._speed = other._speed
        self._forward = other._forward
        self._loop = other._loop

    def reset(self) -> None:
        """재생 정지 후 모든 상태를 초기값으로 리셋한다."""
        self.stop()
        self._t = 0.0
        self._speed = 1.0
        self._forward = True
        self._loop = False

    # ── 구독 ───────────────────────────────────────────────────────────

    def subscribe_tick(self, cb: Callable[[float, bool], None]) -> None:
        if cb not in self._tick_cbs:
            self._tick_cbs.append(cb)

    def unsubscribe_tick(self, cb: Callable[[float, bool], None]) -> None:
        self._tick_cbs = [c for c in self._tick_cbs if c != cb]

    def subscribe_stopped(self, cb: Callable[[], None]) -> None:
        if cb not in self._stopped_cbs:
            self._stopped_cbs.append(cb)

    def unsubscribe_stopped(self, cb: Callable[[], None]) -> None:
        self._stopped_cbs = [c for c in self._stopped_cbs if c != cb]

    # ── 내부 ───────────────────────────────────────────────────────────

    def _notify_tick(self, t: float, correction: bool) -> None:
        for cb in list(self._tick_cbs):
            cb(t, correction)

    def _notify_stopped(self) -> None:
        for cb in list(self._stopped_cbs):
            cb()

    async def _animate(self) -> None:
        """델타-타임 스텝·루프·경계 correction 재생 루프."""
        cur_t = self._t
        try:
            last = time.monotonic()
            while True:
                await omni.kit.app.get_app().next_update_async()
                now = time.monotonic()
                step = (now - last) * self._speed / PLAY_DURATION
                last = now
                cur_t = max(0.0, min(1.0, cur_t + (step if self._forward else -step)))
                self._t = cur_t
                self._notify_tick(cur_t, False)
                reached_end = (self._forward and cur_t >= 1.0) or \
                              (not self._forward and cur_t <= 0.0)
                if reached_end:
                    self._notify_tick(cur_t, True)
                    if not self._loop:
                        break
                    cur_t = 0.0 if self._forward else 1.0
                    self._notify_tick(cur_t, False)
        except asyncio.CancelledError:
            self._notify_tick(cur_t, True)
        finally:
            self._play_task = None
            self._notify_stopped()
