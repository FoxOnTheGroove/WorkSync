"""morph.perf.monitor 테스트.

익스텐션이 실제로 켜진 상태(on_startup 경로 통과)의 싱글턴을 대상으로 한다.
의도적으로 carb error 로그를 남기지 않는다 (경고만 사용).
"""

import ctypes
import json
import math
import random
import sys
import threading

import carb.eventdispatcher
import carb.settings
import omni.kit.app
import omni.kit.test

from morph.perf.monitor import (
    EVENT_REPORT,
    MetricStat,
    async_scope,
    counter,
    gauge,
    get_monitor,
    scope,
)
from morph.perf.monitor import monitor as _impl
from morph.perf.monitor import sysmetrics as _sys
from morph.stream.messaging import get_messaging

P = "morph.perf.monitor.test_"


class _FakeMessaging:
    def __init__(self, connected=True):
        self.is_connected = connected
        self.sent = []

    def send(self, type, payload, reply_to=None):  # noqa: A002
        json.dumps(payload, allow_nan=False)  # 실제 stream과 같이 NaN이면 ValueError
        self.sent.append((type, payload))


class _FakeCtx:
    """stream 1.0 MessageContext 흉내: reply_ok/reply_error만, 컨텍스트당 1회."""

    def __init__(self):
        self.type = _impl.MSG_SET_ENABLED
        self.v = 1
        self.id = "req-1"
        self.replies = []

    @property
    def replied(self):
        return bool(self.replies)

    def _once(self, payload):
        if self.replies:
            raise RuntimeError("already replied")
        json.dumps(payload, allow_nan=False)
        self.replies.append(payload)

    def reply_ok(self, **fields):
        if "ok" in fields:
            raise ValueError("ok")
        self._once({"ok": True, **fields})

    def reply_error(self, code, message, **fields):
        if not isinstance(message, str) or "ok" in fields or "error" in fields:
            raise ValueError("bad reply_error")
        self._once({"ok": False, "error": code, "message": message, **fields})

    def reply(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("reply payload must have ok: bool")
        self._once(payload)


class _FakeCollector:
    """시스템 지표 수집기 흉내. values가 예외 인스턴스면 read()에서 던진다."""

    def __init__(self, key, values, unavailable=None):
        self.key = key
        self.values = values
        self.unavailable = unavailable
        self.calls = 0

    def read(self):
        self.calls += 1
        if isinstance(self.values, BaseException):
            raise self.values
        return self.values


class _FakeHydraStatsModule:
    """omni.hydra.engine.stats 흉내 (get_device_info만)."""

    def __init__(self, devices):
        self._devices = devices

    def get_device_info(self):
        return self._devices


async def _frames(n):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


class TestPerfMonitor(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.settings = carb.settings.get_settings()
        self.saved = {
            p: self.settings.get(p)
            for p in (
                _impl.SETTING_ENABLED,
                _impl.SETTING_PERIOD,
                _impl.SETTING_WEB_REPORT,
                _impl.SETTING_SYSTEM_METRICS,
            )
        }
        self.m = get_monitor()
        self.assertIsNotNone(self.m, "익스텐션 startup 이후 get_monitor()는 None이 아니어야 함")
        self.saved_getter = self.m._get_messaging
        self.saved_max_names = self.m._max_names
        self.saved_max_samples = self.m._max_samples
        self.saved_collectors = self.m._sys_collectors
        self.saved_sys_interval = self.m._sys_min_interval
        # 자동 주기 리포트가 테스트 도중 끼어들지 않게 크게 둔다.
        self.settings.set(_impl.SETTING_PERIOD, 1000.0)
        self.settings.set(_impl.SETTING_WEB_REPORT, False)
        # 기존 테스트는 지표 이름 집합을 정확히 본다 → 시스템 지표는 해당 테스트에서만 켠다.
        self.settings.set(_impl.SETTING_SYSTEM_METRICS, False)
        self.m.set_enabled(True)
        self.m.reset()

    async def tearDown(self):
        m = get_monitor()
        if m is not None:
            m._get_messaging = self.saved_getter
            m._max_names = self.saved_max_names
            m._max_samples = self.saved_max_samples
            m._sys_collectors = self.saved_collectors
            m._sys_min_interval = self.saved_sys_interval
            m.reset()
        for p, v in self.saved.items():
            if v is not None:
                self.settings.set(p, v)
        if m is not None:
            m.set_enabled(bool(self.saved[_impl.SETTING_ENABLED]) if self.saved[_impl.SETTING_ENABLED] is not None else True)

    # ------------------------------------------------ 통합 (실제 startup 경로)

    async def test_startup_integration(self):
        m = self.m
        self.assertTrue(m.enabled)
        self.assertEqual(self.saved[_impl.SETTING_ENABLED], True)  # extension.toml 기본값
        self.assertEqual(self.saved[_impl.SETTING_SYSTEM_METRICS], True)
        self.assertEqual(len(m._web_subs), 2)  # outbound report + inbound set_enabled
        await _frames(6)
        m.flush()
        self.assertGreaterEqual(m.frame_stat().count, 3)
        self.assertGreater(m.frame_stat().avg, 0.0)
        report = m.last_report()
        self.assertIsNotNone(report)
        # 실제 stream으로 송신해도 스키마·JSON 검증을 통과해야 한다.
        get_messaging().send(_impl.MSG_REPORT, report)

    async def test_public_names(self):
        import morph.perf.monitor as pub

        self.assertEqual(pub.EVENT_REPORT, "morph.perf.report")  # carb 이벤트 이름
        self.assertEqual(pub.MSG_REPORT, "morph.perf.monitor.report")  # 웹 type (stream 1.0 규칙)
        self.assertEqual(pub.MSG_SET_ENABLED, "morph.perf.monitor.set_enabled")

    async def test_web_reregister_after_unsubscribe(self):
        m = self.m
        m._unregister_web()
        self.assertEqual(m._web_subs, [])
        m._register_web()  # 해제가 제대로 됐으면 ValueError 없이 다시 등록된다
        self.assertEqual(len(m._web_subs), 2)

    # ------------------------------------------------ 집계

    async def test_scope_aggregate_p95(self):
        name = P + "agg_ms"
        values = list(range(1, 101))
        random.Random(7).shuffle(values)
        for v in values:
            self.m._record(name, _impl.KIND_SCOPE, float(v))
        st = self.m.flush()[name]
        self.assertEqual(st.kind, "scope")
        self.assertEqual(st.count, 100)
        self.assertAlmostEqual(st.avg, 50.5)
        self.assertAlmostEqual(st.p95, 95.0)
        self.assertAlmostEqual(st.max, 100.0)
        self.assertAlmostEqual(st.value, 5050.0)

    async def test_p95_uses_whole_period_when_over_max_samples(self):
        # 저장소 샘플링: maxSamples를 넘으면 주기 전체에서 균등 표본. 앞쪽 샘플만 쓰면 p95 = 95.
        self.m._max_samples = 100
        name = P + "reservoir_ms"
        for v in range(1, 2001):  # 오름차순으로 기록 → 앞쪽만 쓰면 p95가 작게 나온다
            self.m._record(name, _impl.KIND_SCOPE, float(v))
        st = self.m.flush()[name]
        self.assertEqual(st.count, 2000)  # count/avg/max/value는 전부 반영
        self.assertAlmostEqual(st.avg, 1000.5)
        self.assertAlmostEqual(st.max, 2000.0)
        self.assertAlmostEqual(st.value, 2001000.0)
        # 균등 표본 100개의 p95 기대값 ~1900. 1000 미만일 확률은 무시할 만큼 작다.
        self.assertGreater(st.p95, 1000.0)
        self.assertLessEqual(st.p95, 2000.0)
        # 다음 주기는 새 표본
        self.m._record(name, _impl.KIND_SCOPE, 5.0)
        self.assertEqual(self.m.flush()[name].p95, 5.0)

    async def test_p95_small_counts(self):
        self.assertEqual(_impl._p95([]), 0.0)
        self.assertEqual(_impl._p95([3.0]), 3.0)
        self.assertEqual(_impl._p95([1.0, 2.0]), 2.0)
        self.assertEqual(_impl._p95([float(i) for i in range(1, 21)]), 19.0)

    async def test_real_scope_records(self):
        name = P + "real_ms"
        with scope(name):
            sum(range(1000))
        with scope(name):
            pass
        st = self.m.flush()[name]
        self.assertEqual(st.count, 2)
        self.assertGreaterEqual(st.max, st.avg)
        self.assertGreaterEqual(st.avg, 0.0)

    async def test_scope_propagates_exception(self):
        name = P + "exc_ms"
        with self.assertRaises(KeyError):
            with scope(name):
                raise KeyError("x")
        self.assertEqual(self.m.flush()[name].count, 1)

    async def test_async_scope_across_await(self):
        name = P + "async_ms"
        async with async_scope(name):
            await _frames(2)
        with async_scope(name):
            await _frames(1)
        st = self.m.flush()[name]
        self.assertEqual(st.count, 2)
        self.assertGreater(st.max, 0.0)

    async def test_counter_resets_each_period(self):
        name = P + "cmds"
        counter(name)
        counter(name, 2)
        counter(name, 3)
        st = self.m.flush()[name]
        self.assertEqual(st.kind, "counter")
        self.assertEqual(st.count, 3)
        self.assertEqual(st.value, 6)
        st2 = self.m.flush()[name]  # 이름은 남고 값은 0
        self.assertEqual(st2.count, 0)
        self.assertEqual(st2.value, 0)

    async def test_gauge_keeps_last_value(self):
        name = P + "queue_len"
        gauge(name, 5)
        gauge(name, 7)
        st = self.m.flush()[name]
        self.assertEqual(st.kind, "gauge")
        self.assertEqual(st.count, 2)
        self.assertEqual(st.value, 7)
        self.assertAlmostEqual(st.avg, 6.0)
        self.assertEqual(st.max, 7)
        for _ in range(2):
            st = self.m.flush()[name]
            self.assertEqual(st.count, 0)
            self.assertEqual(st.value, 7)
            self.assertEqual(st.avg, 7)
            self.assertEqual(st.p95, 7)
            self.assertEqual(st.max, 7)
        self.assertEqual(self.m.last_report()["metrics"][name]["value"], 7)

    async def test_kind_mismatch_ignored(self):
        name = P + "mixed"
        counter(name)
        gauge(name, 3.0)  # 경고 1회 후 무시
        st = self.m.flush()[name]
        self.assertEqual(st.kind, "counter")
        self.assertEqual(st.value, 1)

    async def test_threads(self):
        name = P + "threads"
        tname = P + "thread_ms"

        def work():
            for _ in range(1000):
                counter(name)
            with scope(tname):
                pass

        ts = [threading.Thread(target=work) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        snap = self.m.flush()
        self.assertEqual(snap[name].value, 4000)
        self.assertEqual(snap[tname].count, 4)

    async def test_name_cap(self):
        self.m._max_names = 2
        for i in range(3):
            counter(P + f"cap_{i}")
        snap = self.m.flush()
        self.assertEqual(len(snap), 2)
        self.assertNotIn(P + "cap_2", snap)

    async def test_bool_values_ignored(self):
        counter(P + "bool_cnt", True)  # bool은 받지 않음 → 이름도 등록 안 함
        gauge(P + "bool_g", False)
        counter(P + "int_cnt", 2)
        counter(P + "int_cnt", 0.5)  # float inc는 허용
        snap = self.m.flush()
        self.assertNotIn(P + "bool_cnt", snap)
        self.assertNotIn(P + "bool_g", snap)
        self.assertEqual(snap[P + "int_cnt"].value, 2.5)

    async def test_ignored_values_do_not_use_name_slots(self):
        self.m._max_names = 1
        gauge(P + "slot_nan", float("nan"))  # 무시 → 이름 슬롯도 쓰지 않음
        counter(P + "slot_bad", float("inf"))
        gauge(P + "slot_ok", 2.0)
        snap = self.m.flush()
        self.assertEqual(list(snap), [P + "slot_ok"])
        self.assertEqual(snap[P + "slot_ok"].value, 2.0)

    # ------------------------------------------------ 비활성

    async def test_disabled_is_noop(self):
        self.m.set_enabled(False)
        self.assertFalse(self.settings.get(_impl.SETTING_ENABLED))
        a = scope(P + "off_a")
        b = scope(P + "off_b")
        self.assertIs(a, b)
        self.assertIs(async_scope(P + "off_c"), a)
        with scope(P + "off_a"):
            pass
        counter(P + "off_cnt")
        gauge(P + "off_g", 1.0)
        self.m.set_enabled(True)
        snap = self.m.flush()
        for n in ("off_a", "off_cnt", "off_g"):
            self.assertNotIn(P + n, snap)

    async def test_flush_while_disabled_emits_nothing(self):
        fake = _FakeMessaging(connected=True)
        self.m._get_messaging = lambda: fake
        self.settings.set(_impl.SETTING_WEB_REPORT, True)
        counter(P + "before_off")
        self.m.flush()
        self.assertEqual(len(fake.sent), 1)
        before_snap = self.m.snapshot()
        before_report = self.m.last_report()

        got = []
        sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
            observer_name="morph.perf.monitor.test:disabled_flush",
            event_name=EVENT_REPORT,
            on_event=lambda e: got.append(1),
        )
        try:
            self.m.set_enabled(False)
            snap = self.m.flush()
        finally:
            sub = None  # noqa: F841
        self.assertEqual(got, [])  # 이벤트 없음
        self.assertEqual(len(fake.sent), 1)  # 웹 송신 없음
        self.assertEqual(snap, before_snap)  # 최근 snapshot 그대로
        self.assertEqual(self.m.last_report(), before_report)

    async def test_setting_toggles_enabled(self):
        self.settings.set(_impl.SETTING_ENABLED, False)
        self.assertFalse(self.m.enabled)
        self.settings.set(_impl.SETTING_ENABLED, True)
        self.assertTrue(self.m.enabled)

    # ------------------------------------------------ 이벤트 / 주기

    async def test_report_event_on_flush(self):
        got = []

        def on_event(e):
            # 중첩 형태 하나만 계약: 이벤트에서 바로 dict로 읽을 수 있어야 한다.
            got.append((e["period_s"], e["frame_ms"], e["metrics"], "json" in e))

        sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
            observer_name="morph.perf.monitor.test:report",
            event_name=EVENT_REPORT,
            on_event=on_event,
        )
        try:
            counter(P + "evt")
            self.m.flush()
        finally:
            sub = None  # noqa: F841
        self.assertEqual(len(got), 1)
        period_s, frame_ms, metrics, has_json = got[0]
        self.assertGreaterEqual(period_s, 0.0)
        self.assertFalse(has_json)
        self.assertIsInstance(frame_ms, dict)
        self.assertEqual(set(frame_ms), {"kind", "count", "avg", "p95", "max", "value"})
        self.assertEqual(frame_ms["kind"], "frame")
        self.assertIsInstance(metrics, dict)
        self.assertEqual(metrics[P + "evt"]["kind"], "counter")
        self.assertEqual(metrics[P + "evt"]["value"], 1)
        self.assertEqual(self.m.last_report()["metrics"][P + "evt"]["value"], 1)

    async def test_report_event_payload_isolated_from_last_report(self):
        def mutate(e):
            e["metrics"].clear()  # 관찰자가 페이로드를 고쳐도
            e["frame_ms"]["count"] = -1

        sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
            observer_name="morph.perf.monitor.test:mutate",
            event_name=EVENT_REPORT,
            on_event=mutate,
        )
        try:
            counter(P + "iso")
            self.m.flush()
        finally:
            sub = None  # noqa: F841
        report = self.m.last_report()  # 내부 상태는 그대로
        self.assertEqual(report["metrics"][P + "iso"]["value"], 1)
        self.assertGreaterEqual(report["frame_ms"]["count"], 0)

    async def test_periodic_report(self):
        got = []
        sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
            observer_name="morph.perf.monitor.test:periodic",
            event_name=EVENT_REPORT,
            on_event=lambda e: got.append(e["period_s"]),
        )
        try:
            counter(P + "periodic")
            self.settings.set(_impl.SETTING_PERIOD, 0.05)
            for _ in range(300):
                await _frames(1)
                if got:
                    break
        finally:
            self.settings.set(_impl.SETTING_PERIOD, 1000.0)
            sub = None  # noqa: F841
        self.assertTrue(got, "periodSec 경과 후 morph.perf.report가 발행돼야 함")
        self.assertGreaterEqual(got[0], 0.05)
        self.assertEqual(self.m.snapshot()[P + "periodic"].value, 1)

    # ------------------------------------------------ NaN 방지

    async def test_no_nan_in_report(self):
        empty = P + "empty_ms"
        self.m._record(empty, _impl.KIND_SCOPE, 1.0)
        self.m.flush()
        gauge(P + "nan_g", float("nan"))  # 경고 후 무시
        gauge(P + "inf_g", float("inf"))
        self.m.flush()  # empty_ms는 샘플 0개
        st = self.m.snapshot()[empty]
        self.assertEqual(st.count, 0)
        for v in (st.avg, st.p95, st.max, st.value):
            self.assertEqual(v, 0.0)
        self.assertNotIn(P + "nan_g", self.m.snapshot())
        self.assertNotIn(P + "inf_g", self.m.snapshot())
        report = self.m.last_report()
        json.dumps(report, allow_nan=False)
        self.assertEqual(report["frame_ms"]["count"], 0)
        d = MetricStat(count=0, avg=float("nan"), p95=float("inf"), max=-float("inf")).to_dict()
        self.assertTrue(all(math.isfinite(d[k]) for k in ("avg", "p95", "max", "value")))

    # ------------------------------------------------ 웹

    async def test_web_report_on_off(self):
        fake = _FakeMessaging(connected=True)
        self.m._get_messaging = lambda: fake
        counter(P + "web")

        self.m.flush()  # webReport false
        self.assertEqual(fake.sent, [])

        self.settings.set(_impl.SETTING_WEB_REPORT, True)
        self.m.flush()
        self.assertEqual(len(fake.sent), 1)
        typ, payload = fake.sent[0]
        self.assertEqual(typ, "morph.perf.monitor.report")
        self.assertIn("period_s", payload)
        self.assertIn("frame_ms", payload)
        self.assertIn(P + "web", payload["metrics"])

        fake.is_connected = False  # 클라이언트 없음 → 송신 안 함
        self.m.flush()
        self.assertEqual(len(fake.sent), 1)

        self.m._get_messaging = lambda: None  # stream 없음 → 조용히 건너뜀
        self.m.flush()
        self.assertEqual(len(fake.sent), 1)

    async def test_set_enabled_message(self):
        ctx = _FakeCtx()
        self.m._on_set_enabled({"enabled": False}, ctx)
        self.assertFalse(self.m.enabled)
        self.assertFalse(self.settings.get(_impl.SETTING_ENABLED))
        self.assertIs(scope(P + "x"), _impl._NOOP)
        self.assertEqual(ctx.replies, [{"ok": True, "enabled": False}])
        ctx2 = _FakeCtx()  # 실제 stream처럼 요청마다 새 컨텍스트, 정확히 한 번 회신
        self.m._on_set_enabled({"enabled": True}, ctx2)
        self.assertTrue(self.m.enabled)
        self.assertEqual(ctx2.replies, [{"ok": True, "enabled": True}])

    async def test_set_enabled_message_invalid(self):
        # stream validate=false로 스키마 검증이 꺼져도 핸들러가 직접 거른다.
        for bad in ({"enabled": 1}, {"enabled": "false"}, {"enabled": None}, {}):
            ctx = _FakeCtx()
            self.m._on_set_enabled(bad, ctx)
            self.assertTrue(self.m.enabled, f"{bad} 는 상태를 바꾸면 안 됨")
            self.assertEqual(len(ctx.replies), 1)
            r = ctx.replies[0]
            self.assertIs(r["ok"], False)
            self.assertEqual(r["error"], "invalid_argument")
            self.assertIsInstance(r["message"], str)
            self.assertIs(r["enabled"], True)  # 현재 상태

    # ------------------------------------------------ 시스템 지표 (1.1)

    def _use_collectors(self, *cols):
        self.m._sys_collectors = list(cols)
        self.m._sys_min_interval = 0.0
        self.settings.set(_impl.SETTING_SYSTEM_METRICS, True)

    async def test_system_metric_names_and_units(self):
        import morph.perf.monitor as pub

        expected = {
            "SYS_MEM_WORKING_SET_MB": "morph.perf.monitor.sys.mem_working_set_mb",
            "SYS_MEM_PRIVATE_MB": "morph.perf.monitor.sys.mem_private_mb",
            "SYS_COLLECT_MS": "morph.perf.monitor.sys.collect_ms",
            "GPU_MEM_USED_MB": "morph.perf.monitor.gpu.mem_used_mb",
            "GPU_MEM_BUDGET_MB": "morph.perf.monitor.gpu.mem_budget_mb",
        }
        for const, name in expected.items():
            self.assertEqual(getattr(pub, const), name)  # CONTRACT 표와 같아야 함
            self.assertTrue(_impl._NAME_RE.match(name), name)  # <ext-id>.<metric> 규칙
            self.assertTrue(name.endswith(("_mb", "_ms")), name)  # 단위 접미어
        self.assertEqual(set(_sys.SYSTEM_METRIC_NAMES), set(expected.values()))
        self.assertFalse(hasattr(pub, "GPU_FRAME_MS"))  # 1.1 최종에서 제거됨
        self.assertIsNone(self.settings.get(_impl.SETTINGS_ROOT + "/gpuFrameTime"))

    async def test_system_metrics_on_off(self):
        fake = _FakeCollector("fake.mem", {_sys.SYS_MEM_WORKING_SET_MB: 123.5})
        self._use_collectors(fake)
        snap = self.m.flush()
        st = snap[_sys.SYS_MEM_WORKING_SET_MB]
        self.assertEqual(st.kind, "gauge")
        self.assertEqual(st.count, 1)
        self.assertEqual(st.value, 123.5)
        collect = snap[_sys.SYS_COLLECT_MS]
        self.assertEqual(collect.kind, "gauge")
        self.assertGreaterEqual(collect.value, 0.0)
        self.assertEqual(self.m.last_report()["metrics"][_sys.SYS_MEM_WORKING_SET_MB]["value"], 123.5)

        self.settings.set(_impl.SETTING_SYSTEM_METRICS, False)
        self.m.reset()
        snap = self.m.flush()
        self.assertEqual(fake.calls, 1)  # 끄면 읽지 않는다
        self.assertNotIn(_sys.SYS_MEM_WORKING_SET_MB, snap)
        self.assertNotIn(_sys.SYS_COLLECT_MS, snap)

    async def test_system_metrics_not_collected_when_disabled(self):
        fake = _FakeCollector("fake.mem", {_sys.SYS_MEM_WORKING_SET_MB: 1.0})
        self._use_collectors(fake)
        self.m.set_enabled(False)
        self.m.flush()
        await _frames(3)
        self.assertEqual(fake.calls, 0)
        self.m.set_enabled(True)
        self.assertIn(_sys.SYS_MEM_WORKING_SET_MB, self.m.flush())
        self.assertEqual(fake.calls, 1)

    async def test_system_metric_failure_skips_only_that_collector(self):
        bad = _FakeCollector("fake.bad", RuntimeError("boom"))
        good = _FakeCollector("fake.good", {_sys.GPU_MEM_USED_MB: 512.0})
        self._use_collectors(bad, good)
        for _ in range(2):
            snap = self.m.flush()  # 예외가 새지 않아야 함
            self.assertEqual(snap[_sys.GPU_MEM_USED_MB].value, 512.0)
        self.assertEqual(bad.calls, 2)  # 다음 주기에 다시 시도 (경고는 1회)
        self.assertIn(("sys", "fake.bad"), self.m._warned)
        self.assertNotIn(("sys", "fake.good"), self.m._warned)

    async def test_system_metric_bad_values_skipped(self):
        vals = {
            P + "sys_nan_mb": float("nan"),
            P + "sys_str_mb": "12",
            P + "sys_bool_mb": True,
            P + "sys_ok_mb": 7,
        }
        none_col = _FakeCollector("fake.none", None, unavailable="없음")
        self._use_collectors(_FakeCollector("fake.vals", vals), none_col)
        snap = self.m.flush()
        self.assertEqual(snap[P + "sys_ok_mb"].value, 7.0)
        for n in ("sys_nan_mb", "sys_str_mb", "sys_bool_mb"):
            self.assertNotIn(P + n, snap)
        self.assertIn("fake.none", self.m._sys_logged)  # 사용 불가는 info 1회 (경고 아님)
        self.assertNotIn(("sys", "fake.none"), self.m._warned)

    async def test_system_metrics_min_interval(self):
        fake = _FakeCollector("fake.mem", {_sys.SYS_MEM_PRIVATE_MB: 10.0})
        self._use_collectors(fake)
        self.m._sys_min_interval = 1000.0
        self.m.flush()
        st = self.m.flush()[_sys.SYS_MEM_PRIVATE_MB]
        self.assertEqual(fake.calls, 1)  # 간격 안에서는 다시 읽지 않음
        self.assertEqual(st.count, 0)  # gauge 마지막 값 유지
        self.assertEqual(st.value, 10.0)

    async def test_real_process_memory_collector(self):
        col = _sys.ProcessMemoryCollector()
        self.assertIsNotNone(col.backend_name)
        self.assertIsNone(col.unavailable)
        vals = col.read()
        ws = vals[_sys.SYS_MEM_WORKING_SET_MB]
        self.assertGreater(ws, 10.0)  # Kit 프로세스는 최소 수십 MB
        self.assertLess(ws, 1024.0 * 1024.0)  # 1 TiB 미만 (단위가 MB인지 확인)
        if _sys.SYS_MEM_PRIVATE_MB in vals:
            self.assertGreater(vals[_sys.SYS_MEM_PRIVATE_MB], 0.0)

    async def test_real_collectors_report_is_json_safe(self):
        self.m._sys_collectors = _sys.default_collectors()
        self.m._sys_min_interval = 0.0
        self.settings.set(_impl.SETTING_SYSTEM_METRICS, True)
        snap = self.m.flush()
        self.assertIn(_sys.SYS_MEM_WORKING_SET_MB, snap)
        self.assertNotIn("morph.perf.monitor.gpu.frame_ms", snap)  # 1.1 최종: GPU 프레임 시간 없음
        report = self.m.last_report()
        json.dumps(report, allow_nan=False)
        get_messaging().send(_impl.MSG_REPORT, report)  # 실제 stream 스키마 통과

    async def test_gpu_memory_collector(self):
        mib = 1024 * 1024
        mod = _FakeHydraStatsModule(
            devices=[
                {"description": "GPU A", "usage": 512 * mib, "budget": 8192 * mib},
                {"description": "GPU B", "usage": 256 * mib, "budget": 4096 * mib},
            ],
        )
        vals = _sys.GpuMemoryCollector(importer=lambda: mod).read()
        self.assertEqual(vals, {_sys.GPU_MEM_USED_MB: 768.0, _sys.GPU_MEM_BUDGET_MB: 12288.0})

        missing = _sys.GpuMemoryCollector(importer=lambda: None)
        self.assertIsNone(missing.read())  # 앱에 없으면 조용히
        self.assertIsNotNone(missing.unavailable)

        self.assertIsNone(_sys.GpuMemoryCollector(importer=lambda: _FakeHydraStatsModule([])).read())

        bad = _sys.GpuMemoryCollector(importer=lambda: _FakeHydraStatsModule([{"description": "no usage"}]))
        with self.assertRaises(KeyError):  # 형식이 다르면 예외 → Monitor가 경고 1회 후 건너뜀
            bad.read()

    async def test_windows_backend_struct_and_psutil_agreement(self):
        if sys.platform != "win32":
            return  # Windows 전용 백엔드
        be = _sys._WindowsBackend()
        expected = 80 if ctypes.sizeof(ctypes.c_void_p) == 8 else 44  # PROCESS_MEMORY_COUNTERS_EX
        self.assertEqual(be.struct_size, expected)
        vals = be.read()
        self.assertGreater(vals[_sys.SYS_MEM_WORKING_SET_MB], 10.0)
        self.assertGreater(vals[_sys.SYS_MEM_PRIVATE_MB], 10.0)
        try:
            import psutil
        except ImportError:
            return  # omni.kit.pip_archive가 없으면 비교 생략
        ps = _sys._PsutilBackend(psutil).read()
        mine = be.read()
        for name in (_sys.SYS_MEM_WORKING_SET_MB, _sys.SYS_MEM_PRIVATE_MB):
            # 두 호출 사이 할당 변화를 허용: 5% 또는 64MB 중 큰 쪽
            tol = max(64.0, 0.05 * ps[name])
            self.assertLess(abs(ps[name] - mine[name]), tol, name)

    async def test_system_metrics_runtime_off_keeps_last_value(self):
        # CONTRACT: 런타임에 끄면 수집은 멈추지만 이미 기록된 이름은 마지막 값·count 0으로 남는다.
        fake = _FakeCollector("fake.mem", {_sys.SYS_MEM_WORKING_SET_MB: 321.0})
        self._use_collectors(fake)
        self.assertEqual(self.m.flush()[_sys.SYS_MEM_WORKING_SET_MB].count, 1)
        self.settings.set(_impl.SETTING_SYSTEM_METRICS, False)
        for _ in range(2):
            snap = self.m.flush()
            st = snap[_sys.SYS_MEM_WORKING_SET_MB]
            self.assertEqual(st.count, 0)
            self.assertEqual(st.value, 321.0)
            self.assertEqual(snap[_sys.SYS_COLLECT_MS].count, 0)
        self.assertEqual(fake.calls, 1)

    async def test_system_metric_processing_failure_is_contained(self):
        class _BadValues:
            def __bool__(self):
                return True

            def items(self):
                raise ValueError("bad items")

        broken = _FakeCollector("fake.broken", _BadValues())
        good = _FakeCollector("fake.good", {_sys.SYS_MEM_PRIVATE_MB: 9.0})
        self._use_collectors(broken, good)
        snap = self.m.flush()  # read 이후 처리(값 순회)에서 나는 예외도 새지 않아야 함
        self.assertEqual(snap[_sys.SYS_MEM_PRIVATE_MB].value, 9.0)
        self.assertIn(_sys.SYS_COLLECT_MS, snap)
        self.assertIn(("sys", "fake.broken"), self.m._warned)
