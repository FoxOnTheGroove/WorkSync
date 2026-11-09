"""뷰 화면 영역(rect): 정규화 계산, 변경 감지 푸시, null 처리 (1.2.0)."""

import math
import types
from unittest import mock

import omni.kit.test

from .. import SplitView
from .. import adapters as adapters_mod
from ..adapters import HeadlessAdapter, main_window_size
from ..rect import RECT_EPSILON, normalize_rect, rects_differ
from ..split_view import RECT_CHECK_FRAMES, RECT_SETTLE_FRAMES
from ..web import MSG_GET_STATE, WebBridge
from .fakes import FakeCtx, FakeWindowAdapter
from .test_kit_adapter import KitAdapterHarness

MAIN_W, MAIN_H = 1000.0, 500.0


def _r(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


# ---------------------------------------------------------------------- 순수 계산
class TestNormalizeRect(omni.kit.test.AsyncTestCase):
    async def test_normalized_top_left_origin(self):
        self.assertEqual(normalize_rect(0, 0, 500, 250, MAIN_W, MAIN_H), _r(0.0, 0.0, 0.5, 0.5))
        self.assertEqual(normalize_rect(500, 250, 500, 250, MAIN_W, MAIN_H), _r(0.5, 0.5, 0.5, 0.5))
        self.assertEqual(normalize_rect(0, 0, 1000, 500, MAIN_W, MAIN_H), _r(0.0, 0.0, 1.0, 1.0))

    async def test_rounded(self):
        rect = normalize_rect(1, 1, 333, 1, 3000, 3)
        self.assertEqual(rect["w"], round(333 / 3000, 5))
        self.assertEqual(rect["x"], round(1 / 3000, 5))

    async def test_clipped_to_main_window(self):
        # 메인 창 밖으로 나간 부분은 잘라낸다 (떠 있는 창 등)
        self.assertEqual(normalize_rect(-100, -50, 300, 150, MAIN_W, MAIN_H), _r(0.0, 0.0, 0.2, 0.2))
        self.assertEqual(normalize_rect(900, 400, 300, 300, MAIN_W, MAIN_H), _r(0.9, 0.8, 0.1, 0.2))
        for v in normalize_rect(-10, -10, 5000, 5000, MAIN_W, MAIN_H).values():
            self.assertTrue(0.0 <= v <= 1.0)

    async def test_unknown_is_none(self):
        cases = [
            (0, 0, 0, 100, MAIN_W, MAIN_H),  # 크기 0
            (0, 0, 100, -1, MAIN_W, MAIN_H),
            (0, 0, 100, 100, 0, MAIN_H),  # 메인 창 크기 모름
            (0, 0, 100, 100, MAIN_W, -5),
            (2000, 0, 100, 100, MAIN_W, MAIN_H),  # 완전히 밖
            (0, 0, math.nan, 100, MAIN_W, MAIN_H),
            (0, 0, math.inf, 100, MAIN_W, MAIN_H),
            (None, 0, 100, 100, MAIN_W, MAIN_H),
            ("a", 0, 100, 100, MAIN_W, MAIN_H),
            (True, 0, 100, 100, MAIN_W, MAIN_H),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertIsNone(normalize_rect(*args))


class TestRectsDiffer(omni.kit.test.AsyncTestCase):
    async def test_small_changes_ignored(self):
        a = [_r(0.0, 0.0, 0.5, 1.0), None]
        self.assertFalse(rects_differ(a, [_r(0.0, 0.0, 0.5, 1.0), None]))
        self.assertFalse(rects_differ(a, [_r(0.0, 0.0, 0.5 + RECT_EPSILON / 2, 1.0), None]))
        self.assertTrue(rects_differ(a, [_r(0.0, 0.0, 0.5 + RECT_EPSILON, 1.0), None]))
        self.assertTrue(rects_differ(a, [_r(0.0, 0.01, 0.5, 1.0), None]))

    async def test_null_and_count_changes(self):
        self.assertTrue(rects_differ([None], [_r(0, 0, 1, 1)]))
        self.assertTrue(rects_differ([_r(0, 0, 1, 1)], [None]))
        self.assertFalse(rects_differ([None, None], [None, None]))
        self.assertTrue(rects_differ([None], [None, None]))
        self.assertTrue(rects_differ(None, [None]))


# ---------------------------------------------------------------------- 논리 전용 모드
class TestHeadlessRect(omni.kit.test.AsyncTestCase):
    async def test_rect_null_in_state_and_get_state(self):
        states = []
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None, state_sink=states.append)
        self.addCleanup(view._shutdown)
        view.set_layout("2x2")
        view._tick()
        self.assertEqual([v["rect"] for v in states[0]["views"]], [None, None, None, None])
        ctx = FakeCtx(MSG_GET_STATE)
        WebBridge(lambda: view).on_get_state({}, ctx)
        self.assertEqual([v["rect"] for v in ctx.replies[0]["views"]], [None] * 4)
        # rect가 계속 null이면 다시 푸시하지 않는다
        for _ in range(RECT_CHECK_FRAMES * 3):
            view._tick()
        self.assertEqual(len(states), 1)

    async def test_events_payload_has_no_rect(self):
        # carb 이벤트 페이로드는 1.1.0과 같다 (rect는 웹 state에만)
        events = []
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: events.append(p))
        self.addCleanup(view._shutdown)
        view.set_layout("1x2")
        view._tick()
        self.assertNotIn("rect", events[0]["views"][0])


# ---------------------------------------------------------------------- 변경 감지 푸시
class TestRectChangePush(omni.kit.test.AsyncTestCase):
    def _make(self):
        self.adapter = FakeWindowAdapter(steps=3)
        self.adapter.window0.rect = _r(0.0, 0.0, 1.0, 1.0)
        self.states = []
        self.view = SplitView(self.adapter, emit=lambda n, p: None, state_sink=self.states.append)
        self.addCleanup(self.view._shutdown)

    def _tick(self, n=1):
        for _ in range(n):
            self.view._tick()

    async def test_startup_does_not_push(self):
        self._make()
        self._tick(RECT_CHECK_FRAMES * 2 + 2)
        self.assertEqual(self.states, [])

    async def test_resize_pushes_once(self):
        self._make()
        self.view.set_layout("1x2")
        self._tick(5)  # 전환 완료 → 푸시 1회
        self.assertEqual(len(self.states), 1)
        self.assertEqual(self.states[0]["layout"], "1x2")
        # 새 창의 rect는 아직 모름 (가짜 어댑터) → 완료 푸시에는 null
        self.assertIsNone(self.states[0]["views"][1]["rect"])

        # 도킹 크기가 자리 잡음 → RECT_SETTLE_FRAMES 안에 한 번 더
        self.adapter.window0.rect = _r(0.0, 0.0, 0.5, 1.0)
        self.adapter.extras[1].rect = _r(0.5, 0.0, 0.5, 1.0)
        self._tick(RECT_SETTLE_FRAMES + 1)
        self.assertEqual(len(self.states), 2)
        self.assertEqual(
            [v["rect"] for v in self.states[1]["views"]], [_r(0.0, 0.0, 0.5, 1.0), _r(0.5, 0.0, 0.5, 1.0)]
        )

        # 작은 흔들림은 무시
        self.adapter.window0.rect = _r(0.0, 0.0, 0.5 + RECT_EPSILON / 2, 1.0)
        self._tick(RECT_CHECK_FRAMES * 2 + 2)
        self.assertEqual(len(self.states), 2)

        # 사용자가 분할선을 끌어 크기 변경 → 약 RECT_CHECK_FRAMES 안에 1회
        self.adapter.window0.rect = _r(0.0, 0.0, 0.3, 1.0)
        self.adapter.extras[1].rect = _r(0.3, 0.0, 0.7, 1.0)
        self._tick(RECT_CHECK_FRAMES + 1)
        self.assertEqual(len(self.states), 3)
        self.assertEqual(self.states[2]["views"][0]["rect"], _r(0.0, 0.0, 0.3, 1.0))
        self.assertEqual(self.states[2]["views"][1]["rect"], _r(0.3, 0.0, 0.7, 1.0))
        self._tick(RECT_CHECK_FRAMES * 2 + 2)
        self.assertEqual(len(self.states), 3)  # 바뀌지 않으면 다시 보내지 않음

    async def test_rect_becomes_null_pushes(self):
        self._make()
        self.adapter.window0.rect = None  # 창 크기를 모르게 됨
        self._tick(RECT_CHECK_FRAMES + 1)
        self.assertEqual(len(self.states), 1)
        self.assertIsNone(self.states[0]["views"][0]["rect"])

    async def test_no_rect_push_during_transition(self):
        self._make()
        self.adapter.steps = 1000  # 전환이 끝나지 않음
        self.view.set_layout("1x2")
        self.adapter.window0.rect = _r(0.0, 0.0, 0.5, 1.0)
        self._tick(RECT_CHECK_FRAMES * 3)
        self.assertEqual(self.states, [])

    async def test_get_state_updates_baseline(self):
        self._make()
        self.adapter.window0.rect = _r(0.0, 0.0, 0.8, 1.0)
        ctx = FakeCtx(MSG_GET_STATE)
        WebBridge(lambda: self.view).on_get_state({}, ctx)
        self.assertEqual(ctx.replies[0]["views"][0]["rect"], _r(0.0, 0.0, 0.8, 1.0))
        self._tick(RECT_CHECK_FRAMES + 1)
        self.assertEqual(self.states, [])  # 웹이 이미 받은 값

    async def test_adapter_rect_errors_become_null(self):
        self._make()

        def boom(_n):
            raise RuntimeError("rect read failed (test)")

        self.adapter.read_rects = boom
        with mock.patch("carb.log_warn") as warn:
            self._tick(RECT_CHECK_FRAMES * 2 + 2)
            payload = self.view._state_payload()
        self.assertEqual([v["rect"] for v in payload["views"]], [None])
        self.assertEqual(len(self.states), 1)  # 값 → null 로 한 번
        self.assertEqual(warn.call_count, 1)  # 경고는 1회만


# ---------------------------------------------------------------------- 실제 KitWindowAdapter
class TestKitAdapterRects(KitAdapterHarness):
    def adapter_kwargs(self):
        self.main = (MAIN_W, MAIN_H)
        return {"main_size": lambda: self.main}

    async def test_reads_window_geometry(self):
        self.view.set_layout("1x2")
        self._run_transition()
        w0, w1 = self.window0, self.created["MORPH Viewport 1"]
        w0.position_x, w0.position_y, w0.width, w0.height = 0.0, 20.0, 500.0, 480.0
        w1.position_x, w1.position_y, w1.width, w1.height = 500.0, 20.0, 500.0, 480.0
        self.assertEqual(
            self.adapter.read_rects(2), [_r(0.0, 0.04, 0.5, 0.96), _r(0.5, 0.04, 0.5, 0.96)]
        )
        payload = self.view._state_payload()
        self.assertEqual(payload["views"][1]["rect"], _r(0.5, 0.04, 0.5, 0.96))

    async def test_null_cases(self):
        self.view.set_layout("1x2")
        self._run_transition()
        w1 = self.created["MORPH Viewport 1"]
        w1.position_x, w1.width, w1.height = 500.0, 500.0, 500.0
        # 숨겨진 창
        w1.visible = False
        self.assertIsNone(self.adapter.read_rects(2)[1])
        w1.visible = True
        self.assertIsNotNone(self.adapter.read_rects(2)[1])
        # 메인 창 크기를 모름 → 모두 null
        self.main = None
        self.assertEqual(self.adapter.read_rects(2), [None, None])
        self.main = (MAIN_W, MAIN_H)
        # 창이 아직 없는 뷰 (요청 개수가 창보다 많음)
        self.assertIsNone(self.adapter.read_rects(3)[2])
        # 전환 중에는 위치가 확정되지 않았으므로 모두 null
        self.view.set_layout("2x2")
        self.assertEqual(self.adapter.read_rects(4), [None] * 4)
        self._run_transition()
        self.assertIsNotNone(self.adapter.read_rects(4)[0])

    async def test_dead_window_is_null(self):
        class _Dead:
            title = "Gone"

            @property
            def visible(self):
                raise ReferenceError("weakly-referenced object no longer exists")

        self.registry["Viewport"] = _Dead()
        self.assertEqual(self.adapter.read_rects(1), [None])


class TestMainWindowSize(omni.kit.test.AsyncTestCase):
    async def test_never_raises(self):
        # 실제 Kit: None 또는 양수 (폭, 높이)
        size = main_window_size()
        if size is not None:
            self.assertGreater(size[0], 0)
            self.assertGreater(size[1], 0)
        # omni.ui Workspace가 없는(가짜) 환경에서도 예외 없음
        with mock.patch.object(adapters_mod, "ui", types.SimpleNamespace(Workspace=types.SimpleNamespace())):
            size = main_window_size()
        self.assertTrue(size is None or (size[0] > 0 and size[1] > 0))

    async def test_dpi_scale_applied(self):
        app_window = types.SimpleNamespace(get_width=lambda: 2000, get_height=lambda: 1000)
        fake_ui = types.SimpleNamespace(Workspace=types.SimpleNamespace(get_dpi_scale=lambda: 2.0))
        fake_appwindow = types.SimpleNamespace(get_default_app_window=lambda: app_window)
        with mock.patch.object(adapters_mod, "ui", fake_ui), mock.patch.dict(
            "sys.modules", {"omni.appwindow": fake_appwindow}
        ):
            self.assertEqual(main_window_size(), (1000.0, 500.0))
