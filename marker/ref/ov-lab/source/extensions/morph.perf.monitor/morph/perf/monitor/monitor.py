"""morph.perf.monitor 핵심: 측정 API(scope/counter/gauge)와 Monitor 집계기.

공개 심볼은 CONTRACT.md에 적힌 것만이다. 나머지(밑줄 접두어)는 내부 구현.

설계 메모
- 비활성/미기동 시 `scope`는 모듈 상수 `_NOOP`을 돌려준다 (할당 없음).
- 기록 경로는 락 하나 + 숫자 갱신만 한다. 문자열 포맷·dict 생성은 주기 집계에서만.
- 집계·이벤트 발행·웹 송신은 메인 업데이트 루프에서 주기마다 한다.
"""

from __future__ import annotations

import json
import math
import random
import re
import threading
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

import carb
import carb.eventdispatcher
import carb.settings
import omni.kit.app

from morph.stream.messaging import get_messaging

from . import sysmetrics as _sys

try:  # 프로파일러 바인딩이 없는 환경에서도 측정 자체는 동작해야 한다.
    import carb.profiler as _carb_profiler
except ImportError:  # pragma: no cover
    _carb_profiler = None

__all__ = [
    "EXT_ID",
    "EVENT_REPORT",
    "MSG_REPORT",
    "MSG_SET_ENABLED",
    "MetricStat",
    "Monitor",
    "scope",
    "async_scope",
    "counter",
    "gauge",
    "get_monitor",
]

EXT_ID = "morph.perf.monitor"
# carb 이벤트 이름 (이벤트 규칙 morph.<domain>.<event>). 웹 메시지 type과는 다른 이름이다.
EVENT_REPORT = "morph.perf.report"
# 웹 메시지 type (stream 1.0 규칙 <owner ext-id>.<name>)
MSG_REPORT = f"{EXT_ID}.report"
MSG_SET_ENABLED = f"{EXT_ID}.set_enabled"

SETTINGS_ROOT = "/exts/morph.perf.monitor"
SETTING_ENABLED = SETTINGS_ROOT + "/enabled"
SETTING_PERIOD = SETTINGS_ROOT + "/periodSec"
SETTING_WEB_REPORT = SETTINGS_ROOT + "/webReport"
SETTING_MAX_METRICS = SETTINGS_ROOT + "/maxMetrics"
SETTING_MAX_SAMPLES = SETTINGS_ROOT + "/maxSamples"
SETTING_SYSTEM_METRICS = SETTINGS_ROOT + "/systemMetrics"

_DEFAULT_PERIOD = 1.0
_MIN_PERIOD = 0.05
_DEFAULT_MAX_METRICS = 512
_DEFAULT_MAX_SAMPLES = 4096
_PROFILER_MASK = 1
# 시스템 지표 최소 수집 간격 (초). periodSec가 이보다 짧아도 이 간격보다 자주 읽지 않는다.
_SYS_MIN_INTERVAL = 0.5

KIND_SCOPE = "scope"
KIND_COUNTER = "counter"
KIND_GAUGE = "gauge"
KIND_FRAME = "frame"

_NAME_RE = re.compile(r"^morph\.[a-z0-9_]+\.[a-z0-9_]+\.[A-Za-z0-9_.\-]+$")

REPORT_SCHEMA = {
    "type": "object",
    "required": ["period_s", "frame_ms", "metrics"],
    "properties": {
        "period_s": {"type": "number"},
        "frame_ms": {"type": "object"},
        "metrics": {"type": "object"},
    },
}
SET_ENABLED_SCHEMA = {
    "type": "object",
    "required": ["enabled"],
    "properties": {"enabled": {"type": "boolean"}},
}


def _finite(x: float) -> float:
    x = float(x)
    return x if math.isfinite(x) else 0.0


@dataclass(frozen=True)
class MetricStat:
    """한 주기의 지표 집계. 모든 float는 유한값(NaN/inf 없음)이 보장된다."""

    count: int = 0
    avg: float = 0.0
    p95: float = 0.0
    max: float = 0.0
    kind: str = KIND_SCOPE
    value: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "count": int(self.count),
            "avg": _finite(self.avg),
            "p95": _finite(self.p95),
            "max": _finite(self.max),
            "value": _finite(self.value),
        }


def _p95(sorted_samples: list) -> float:
    """nearest-rank p95: ceil(0.95 * n)번째 값."""
    n = len(sorted_samples)
    if n == 0:
        return 0.0
    idx = max(0, math.ceil(0.95 * n) - 1)
    return sorted_samples[idx]


# p95 샘플 저장소 샘플링용. random()은 C 수준 호출 하나라 핫패스 비용이 작다.
_rng_random = random.Random().random


class _Acc:
    """지표 하나의 주기 누적값. 락 안에서만 수정한다 (frame은 메인 스레드 전용).

    p95용 샘플은 주기당 최대 `max_samples`개. 넘으면 저장소 샘플링(Algorithm R)으로
    주기 전체에서 균등하게 표본을 유지한다 (주기 앞쪽에 치우치지 않음).
    """

    __slots__ = ("kind", "count", "total", "max", "last", "has_last", "samples", "keep_samples")

    def __init__(self, kind: str):
        self.kind = kind
        self.count = 0
        self.total = 0.0
        self.max = -math.inf
        self.last = 0.0
        self.has_last = False
        self.samples: list = []
        self.keep_samples = kind in (KIND_SCOPE, KIND_GAUGE, KIND_FRAME)

    def add(self, value: float, max_samples: int) -> None:
        self.count += 1
        self.total += value
        if value > self.max:
            self.max = value
        self.last = value
        self.has_last = True
        if self.keep_samples:
            samples = self.samples
            if len(samples) < max_samples:
                samples.append(value)
            else:
                # count번째 샘플을 max_samples/count 확률로 무작위 위치에 넣는다.
                j = int(_rng_random() * self.count)
                if j < max_samples:
                    samples[j] = value

    def stat_and_reset(self) -> MetricStat:
        kind = self.kind
        n = self.count
        if kind == KIND_COUNTER:
            v = self.total
            st = MetricStat(count=n, avg=v, p95=v, max=v, kind=kind, value=v)
        elif n == 0:
            if kind == KIND_GAUGE and self.has_last:
                v = self.last
                st = MetricStat(count=0, avg=v, p95=v, max=v, kind=kind, value=v)
            else:
                st = MetricStat(kind=kind)
        else:
            s = sorted(self.samples)
            value = self.last if kind == KIND_GAUGE else self.total
            st = MetricStat(
                count=n,
                avg=self.total / n,
                p95=_p95(s),
                max=self.max,
                kind=kind,
                value=value,
            )
        self.count = 0
        self.total = 0.0
        self.max = -math.inf
        self.samples.clear()
        return st


# ---------------------------------------------------------------- scope 객체


class _NoopScope:
    __slots__ = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


_NOOP = _NoopScope()


class _TLS(threading.local):
    def __init__(self):
        self.stack: list = []


_tls = _TLS()


class _Scope:
    """동기 구간: 시간 + carb.profiler 구간."""

    __slots__ = ("_mon", "_name", "_t0", "_stack")

    def __init__(self, mon: "Monitor", name: str):
        self._mon = mon
        self._name = name
        self._t0 = 0.0
        self._stack = None

    def __enter__(self):
        mon = self._mon
        if mon._prof_on:
            stack = _tls.stack
            self._stack = stack
            stack.append(self)
            mon._prof_begin(_PROFILER_MASK, self._name)
        self._t0 = perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (perf_counter() - self._t0) * 1000.0
        mon = self._mon
        stack = self._stack
        if stack is not None:
            self._stack = None
            if stack is _tls.stack:
                if stack and stack[-1] is self:
                    stack.pop()
                else:
                    # await를 넘어 다른 scope와 교차됨. 구간 수는 맞추되 경계는 부정확.
                    try:
                        stack.remove(self)
                    except ValueError:
                        pass
                    mon._warn_once("interleave", f"scope('{self._name}')가 다른 scope와 교차됨 (await를 넘은 사용?). "
                                   "carb.profiler 구간 경계가 부정확할 수 있음. await를 넘는 구간은 async_scope를 쓸 것")
                mon._prof_end(_PROFILER_MASK)
            else:
                # 다른 스레드에서 끝남: 그 스레드에서 end를 부를 수 없다.
                mon._warn_once("thread", f"scope('{self._name}')가 시작과 다른 스레드에서 끝남. profiler 구간 미종료")
        mon._record(self._name, KIND_SCOPE, ms)
        return False


class _AsyncScope:
    """await를 넘는 구간: 시간만 기록, profiler 구간 없음. `with`/`async with` 모두 지원."""

    __slots__ = ("_mon", "_name", "_t0")

    def __init__(self, mon: "Monitor", name: str):
        self._mon = mon
        self._name = name
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._mon._record(self._name, KIND_SCOPE, (perf_counter() - self._t0) * 1000.0)
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb):
        return self.__exit__(exc_type, exc, tb)


# ---------------------------------------------------------------- 공개 함수

_monitor: "Monitor | None" = None


def get_monitor() -> "Monitor | None":
    """싱글턴. 익스텐션 startup 이후 유효, shutdown 이후 None."""
    return _monitor


def scope(name: str):
    """동기 구간 시간(ms) 측정 + carb.profiler 구간. 비활성 시 no-op."""
    m = _monitor
    if m is None or not m._enabled:
        return _NOOP
    return _Scope(m, name)


def async_scope(name: str):
    """await를 넘는 구간 시간(ms) 측정. profiler 구간은 남기지 않는다. 비활성 시 no-op."""
    m = _monitor
    if m is None or not m._enabled:
        return _NOOP
    return _AsyncScope(m, name)


def counter(name: str, inc: int = 1) -> None:
    """주기 누적 카운터. 비활성 시 no-op."""
    m = _monitor
    if m is None or not m._enabled:
        return
    m._record(name, KIND_COUNTER, inc)


def gauge(name: str, value: float) -> None:
    """현재 값 기록. 마지막 값은 다음 주기들에도 유지된다. 비활성 시 no-op."""
    m = _monitor
    if m is None or not m._enabled:
        return
    m._record(name, KIND_GAUGE, value)


# ---------------------------------------------------------------- Monitor


class Monitor:
    """지표 수집기 싱글턴. `get_monitor()`로 얻는다. 직접 생성하지 않는다."""

    def __init__(self):
        self._settings = carb.settings.get_settings()
        self._lock = threading.Lock()
        self._accs: dict = {}
        self._frame = _Acc(KIND_FRAME)
        self._warned: set = set()
        self._snapshot: dict = {}
        self._frame_stat = MetricStat(kind=KIND_FRAME)
        self._last_report: dict | None = None

        self._enabled = bool(self._get(SETTING_ENABLED, True))
        self._max_names = int(self._get(SETTING_MAX_METRICS, _DEFAULT_MAX_METRICS) or _DEFAULT_MAX_METRICS)
        self._max_samples = max(1, int(self._get(SETTING_MAX_SAMPLES, _DEFAULT_MAX_SAMPLES) or _DEFAULT_MAX_SAMPLES))

        self._prof_begin = getattr(_carb_profiler, "begin", None)
        self._prof_end = getattr(_carb_profiler, "end", None)
        self._prof_active_fn = getattr(_carb_profiler, "is_profiler_active", None)
        self._prof_on = False
        self._refresh_profiler()

        now = perf_counter()
        self._period_start = now
        self._last_frame_t: float | None = None

        # 테스트에서 가짜 Messaging으로 바꿀 수 있게 getter를 보관 (인스턴스는 캐시하지 않음).
        self._get_messaging: Callable = get_messaging
        self._web_subs: list = []
        self._update_sub = None
        self._settings_sub = None

        # 시스템 지표 수집기 (테스트에서 가짜로 바꿔 끼운다). 수집은 _report 직전에만 한다.
        self._sys_collectors: list = _sys.default_collectors()
        self._sys_min_interval = _SYS_MIN_INTERVAL
        self._sys_last_t: float | None = None
        self._sys_logged: set = set()

    # ---------------- 수명

    def _start(self) -> None:
        self._subscribe_update()
        try:
            self._settings_sub = self._settings.subscribe_to_node_change_events(
                SETTING_ENABLED, self._on_enabled_setting_changed
            )
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] enabled 설정 구독 실패: {e}")
        self._register_web()

    def _stop(self) -> None:
        self._unregister_web()
        if self._settings_sub is not None:
            try:
                self._settings.unsubscribe_to_change_events(self._settings_sub)
            except Exception:  # noqa: BLE001
                pass
            self._settings_sub = None
        self._update_sub = None
        self._enabled = False

    def _subscribe_update(self) -> None:
        ev = getattr(omni.kit.app, "GLOBAL_EVENT_UPDATE", None)
        if ev is not None:
            self._update_sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
                observer_name=f"{EXT_ID}:update",
                event_name=ev,
                on_event=self._on_update,
            )
        else:  # pragma: no cover - 구 Kit 호환
            self._update_sub = (
                omni.kit.app.get_app()
                .get_update_event_stream()
                .create_subscription_to_pop(self._on_update, name=f"{EXT_ID}:update")
            )

    def _register_web(self) -> None:
        msg = self._get_messaging()
        if msg is None:
            carb.log_warn(f"[{EXT_ID}] morph.stream.messaging 없음 — 웹 리포트/수신 비활성")
            return
        try:
            self._web_subs.append(msg.register_outbound(MSG_REPORT, REPORT_SCHEMA, owner=EXT_ID))
            self._web_subs.append(
                msg.register_inbound(MSG_SET_ENABLED, self._on_set_enabled, SET_ENABLED_SCHEMA, owner=EXT_ID)
            )
        except (ValueError, RuntimeError) as e:
            carb.log_warn(f"[{EXT_ID}] 웹 메시지 등록 실패: {e}")

    def _unregister_web(self) -> None:
        for sub in self._web_subs:
            try:
                sub.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        self._web_subs = []

    # ---------------- 설정

    def _get(self, path: str, default):
        v = self._settings.get(path)
        return default if v is None else v

    def _period(self) -> float:
        try:
            p = float(self._get(SETTING_PERIOD, _DEFAULT_PERIOD))
        except (TypeError, ValueError):
            p = _DEFAULT_PERIOD
        if not math.isfinite(p):
            p = _DEFAULT_PERIOD
        return max(_MIN_PERIOD, p)

    def _on_enabled_setting_changed(self, *_args) -> None:
        self._apply_enabled(bool(self._get(SETTING_ENABLED, True)))

    def _apply_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        with self._lock:
            # 상태 전환 시 진행 중이던 주기는 버린다 (gauge 마지막 값은 유지).
            for acc in self._accs.values():
                acc.stat_and_reset()
            self._frame.stat_and_reset()
            self._enabled = enabled
        self._period_start = perf_counter()
        self._last_frame_t = None
        self._sys_last_t = None

    def _refresh_profiler(self) -> None:
        if self._prof_begin is None or self._prof_end is None:
            self._prof_on = False
            return
        fn = self._prof_active_fn
        if fn is None:
            self._prof_on = True
            return
        try:
            self._prof_on = bool(fn())
        except Exception:  # noqa: BLE001
            self._prof_on = True

    # ---------------- 공개 메서드

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """측정 on/off. `/exts/morph.perf.monitor/enabled` 설정도 함께 바꾼다. 메인 스레드에서 호출."""
        enabled = bool(enabled)
        self._apply_enabled(enabled)
        if bool(self._get(SETTING_ENABLED, True)) != enabled:
            self._settings.set(SETTING_ENABLED, enabled)

    def snapshot(self) -> dict:
        """최근 완료된 주기의 집계 `{name: MetricStat}` (프레임 타임 제외)."""
        return dict(self._snapshot)

    def frame_stat(self) -> MetricStat:
        """최근 완료된 주기의 프레임 타임 집계 (ms)."""
        return self._frame_stat

    def last_report(self) -> dict | None:
        """최근 `morph.perf.report` 페이로드 (JSON 직렬화 가능한 dict 사본). 아직 없으면 None."""
        r = self._last_report
        return None if r is None else json.loads(json.dumps(r))

    def flush(self) -> dict:
        """주기를 기다리지 않고 지금 집계·발행한다. 테스트·디버그용. 메인 스레드에서 호출.

        비활성 중에는 집계·이벤트·웹 송신 모두 하지 않고 최근 snapshot 사본만 돌려준다.
        """
        if self._enabled:
            self._report(perf_counter())
        return self.snapshot()

    def reset(self) -> None:
        """모든 지표 이름·누적값·gauge 값·최근 집계를 지운다. 테스트용."""
        with self._lock:
            self._accs = {}
            self._frame = _Acc(KIND_FRAME)
        self._snapshot = {}
        self._frame_stat = MetricStat(kind=KIND_FRAME)
        self._last_report = None
        self._period_start = perf_counter()
        self._last_frame_t = None
        self._sys_last_t = None

    # ---------------- 기록 (핫패스)

    def _record(self, name: str, kind: str, value) -> None:
        if not self._enabled:
            return
        # 값 검증을 이름 등록보다 먼저 한다: 무시된 기록은 이름도 남기지 않는다 (상한 카운트 포함).
        if kind != KIND_SCOPE:
            # bool은 int의 하위 클래스지만 지표 값으로는 실수일 가능성이 커서 받지 않는다.
            if value.__class__ is bool or not isinstance(value, (int, float)):
                self._warn_once(("type", name), f"지표 '{name}' 값이 숫자가 아님 — 무시")
                return
            if not math.isfinite(value):
                self._warn_once(("nan", name), f"지표 '{name}'에 NaN/inf 기록 시도 — 무시")
                return
        with self._lock:
            acc = self._accs.get(name)
            if acc is None:
                acc = self._new_acc_locked(name, kind, value)
                if acc is None:
                    return
            elif acc.kind != kind:
                self._warn_once(("kind", name), f"지표 '{name}'는 {acc.kind}인데 {kind}로 기록 시도 — 무시")
                return
            acc.add(value, self._max_samples)

    def _new_acc_locked(self, name, kind, value):
        if not isinstance(name, str):
            self._warn_once(("badname", repr(type(name))), f"지표 이름은 str이어야 함: {name!r} — 무시")
            return None
        if len(self._accs) >= self._max_names:
            self._warn_once(
                "cap",
                f"지표 이름 수가 상한 {self._max_names}에 도달 — 새 이름 '{name}' 등은 무시됨. "
                "지표 이름을 동적으로 만들고 있지 않은지 확인할 것",
            )
            return None
        if not _NAME_RE.match(name):
            self._warn_once(("pattern", name), f"지표 이름 '{name}'이 '<ext-id>.<metric>' 규칙과 다름")
        acc = _Acc(kind)
        self._accs[name] = acc
        return acc

    def _warn_once(self, key, text: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        carb.log_warn(f"[{EXT_ID}] {text}")

    # ---------------- 업데이트 루프

    def _on_update(self, *_args) -> None:
        if not self._enabled:
            return
        now = perf_counter()
        last = self._last_frame_t
        self._last_frame_t = now
        if last is not None:
            self._frame.add((now - last) * 1000.0, self._max_samples)
        if now - self._period_start >= self._period():
            self._report(now)

    def _report(self, now: float) -> None:
        self._collect_system(now)
        period_s = max(0.0, now - self._period_start)
        self._period_start = now
        with self._lock:
            snap = {name: acc.stat_and_reset() for name, acc in self._accs.items()}
            frame = self._frame.stat_and_reset()
        self._snapshot = snap
        self._frame_stat = frame
        self._refresh_profiler()

        period_s = _finite(period_s)

        def build() -> dict:
            return {
                "period_s": period_s,
                "frame_ms": frame.to_dict(),
                "metrics": {name: st.to_dict() for name, st in snap.items()},
            }

        payload = build()
        self._last_report = payload
        # 웹 송신은 호출 시점에 직렬화되므로 먼저 보낸다.
        self._send_web(payload)
        # carb.eventdispatcher는 Python dict/list 값을 carb.variant Python 타입으로 보관해 Python 객체로
        # 관찰자에게 넘긴다 (복사 여부는 보장되지 않음). 관찰자가 고쳐도 내부 상태(last_report)가 바뀌지 않도록 이벤트에는 별도 사본을 싣는다.
        self._dispatch(build())

    # ---------------- 시스템 지표

    def _collect_system(self, now: float) -> None:
        """리포트 직전(메인 스레드)에 시스템 지표를 gauge로 기록한다. 활성 상태에서만 불린다."""
        if not self._get(SETTING_SYSTEM_METRICS, True):
            return
        last = self._sys_last_t
        if last is not None and now - last < self._sys_min_interval:
            return  # gauge는 마지막 값을 유지하므로 건너뛰어도 리포트에는 남는다
        self._sys_last_t = now
        t0 = perf_counter()
        for col in self._sys_collectors:
            key = getattr(col, "key", None) or type(col).__name__
            # 수집기 하나의 처리 전체(read + 값 검증 + 기록)를 감싼다: 어떤 실패도 다른 수집기·리포트를 막지 않는다.
            try:
                self._collect_one(col, key)
            except Exception as e:  # noqa: BLE001
                self._warn_once(("sys", key), f"시스템 지표 '{key}' 읽기 실패 — 건너뜀 (이후 경고 생략): {e!r}")
        self._record(_sys.SYS_COLLECT_MS, KIND_GAUGE, (perf_counter() - t0) * 1000.0)

    def _collect_one(self, col, key: str) -> None:
        values = col.read()
        reason = getattr(col, "unavailable", None)
        if reason and key not in self._sys_logged:
            self._sys_logged.add(key)
            carb.log_info(f"[{EXT_ID}] 시스템 지표 '{key}' 사용 불가: {reason}")
        if not values:
            return
        for name, value in values.items():
            if value.__class__ is bool or not isinstance(value, (int, float)) or not math.isfinite(value):
                self._warn_once(("sysval", name), f"시스템 지표 '{name}' 값이 유한한 숫자가 아님: {value!r} — 건너뜀")
                continue
            self._record(name, KIND_GAUGE, float(value))

    def _dispatch(self, payload: dict) -> None:
        try:
            carb.eventdispatcher.get_eventdispatcher().dispatch_event(EVENT_REPORT, payload=payload)
        except Exception as e:  # noqa: BLE001 - 관찰자 예외/바인딩 오류가 업데이트 루프를 깨지 않게
            self._warn_once("dispatch", f"{EVENT_REPORT} 발행 실패: {e}")

    def _send_web(self, payload: dict) -> None:
        if not self._get(SETTING_WEB_REPORT, False):
            return
        msg = self._get_messaging()
        if msg is None or not msg.is_connected:
            return
        try:
            msg.send(MSG_REPORT, payload)
        except ValueError as e:
            self._warn_once("send", f"웹 리포트 송신 실패: {e}")

    # ---------------- 웹 수신

    def _on_set_enabled(self, payload: dict, ctx) -> None:
        # stream 스키마 검증이 꺼져 있을 수 있으므로(validate=false) 직접 확인한다.
        enabled = payload.get("enabled") if isinstance(payload, dict) else None
        if enabled.__class__ is not bool:
            ctx.reply_error(
                "invalid_argument",
                f"'enabled' must be a boolean, got {type(enabled).__name__}",
                enabled=self._enabled,
            )
            return
        self.set_enabled(enabled)
        ctx.reply_ok(enabled=self._enabled)


def _install() -> Monitor:
    global _monitor
    if _monitor is not None:
        _uninstall()
    m = Monitor()
    _monitor = m
    m._start()
    return m


def _uninstall() -> None:
    global _monitor
    m = _monitor
    _monitor = None
    if m is not None:
        m._stop()
