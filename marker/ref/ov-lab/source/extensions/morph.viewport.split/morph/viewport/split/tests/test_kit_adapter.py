"""실제 KitWindowAdapter를 Kit처럼 동작하는 가짜 창으로 검사한다.

Kit의 get_viewport_window_instances()는 view 0 창을 weakref.proxy로 돌려준다. 가짜 창은
pybind처럼 프록시 인자를 거부하고(dock_in), 가짜 ViewportWindow.active_window는 Kit처럼
weakref.proxy를 담는다. 프록시가 Kit API 인자로 새 나가면 이 테스트가 실패한다.

렌더 준비 게이트: 가짜 view 0 뷰포트 API는 `render_frame()`을 부를 때만 프레임을 전달한다.
하네스의 `_tick()`은 `self.rendering`이 True일 때 매 틱 프레임 하나를 전달한다.
"""

import types
import weakref
from unittest import mock

import omni.kit.test

from .. import EVENT_FOCUS_CHANGED, EVENT_LAYOUT_CHANGED, SplitView
from .. import adapters as adapters_mod
from .. import split_view as split_view_mod
from ..adapters import KitWindowAdapter
from .fakes import FakeClock, FakeKitWindow


class _FakeViewportWindowClass:
    active_window = None


class KitAdapterHarness(omni.kit.test.AsyncTestCase):
    """실제 KitWindowAdapter + 가짜 Kit 창 하네스 (테스트 메서드 없음). `adapter_kwargs`로 어댑터 인자를 준다."""

    def adapter_kwargs(self) -> dict:
        return {}

    async def setUp(self):
        self.window0 = FakeKitWindow("Viewport")  # 강한 참조는 테스트가 보관 (Kit에서는 창 관리자가 보관)
        self.registry = {"Viewport": self.window0}
        self.created = {}
        self.errors = []
        self.missing_titles = set()
        self.rendering = True
        self.clock = FakeClock()

        def create_viewport_window(name=None, camera_path=None, **_kwargs):
            window = FakeKitWindow(name, camera_path or "")
            self.created[name] = window
            self.registry[name] = window
            return window

        def get_window(title):
            return None if title in self.missing_titles else self.registry.get(title)

        _FakeViewportWindowClass.active_window = weakref.proxy(self.window0)
        fake_ui = types.SimpleNamespace(
            DockPosition=types.SimpleNamespace(RIGHT="RIGHT", BOTTOM="BOTTOM"),
            Workspace=types.SimpleNamespace(get_window=get_window),
        )
        fake_vpw = types.SimpleNamespace(ViewportWindow=_FakeViewportWindowClass)
        self._patches = [
            mock.patch.object(adapters_mod, "ui", fake_ui),
            mock.patch.object(adapters_mod, "vp_util", types.SimpleNamespace(create_viewport_window=create_viewport_window)),
            mock.patch.object(adapters_mod, "_viewport_window_module", lambda: fake_vpw),
            mock.patch.object(split_view_mod, "_log_error", self.errors.append),
        ]
        for p in self._patches:
            p.start()
        self.events = []
        # Kit과 같이 view 0은 프록시로 넘긴다
        self.adapter = KitWindowAdapter(weakref.proxy(self.window0), clock=self.clock, **self.adapter_kwargs())
        self.view = SplitView(self.adapter, emit=lambda n, p: self.events.append((n, p)))

    async def tearDown(self):
        self.view._shutdown()
        for p in reversed(self._patches):
            p.stop()
        _FakeViewportWindowClass.active_window = None

    def _tick(self, n=1):
        for _ in range(n):
            if self.rendering and self.window0.viewport_api is not None:
                self.window0.viewport_api.render_frame()
            self.view._tick()

    def _run_transition(self, max_ticks=30):
        for _ in range(max_ticks):
            self._tick()
            if not self.view._core.transitioning:
                return


class TestKitWindowAdapterWithProxies(KitAdapterHarness):
    async def test_as_proxy_does_not_rewrap(self):
        proxy = weakref.proxy(self.window0)
        self.assertIs(KitWindowAdapter._as_proxy(proxy), proxy)
        self.assertIsInstance(KitWindowAdapter._as_proxy(self.window0), weakref.ProxyTypes)
        self.assertIs(KitWindowAdapter._as_handle(proxy), self.window0)  # 제목으로 다시 얻은 핸들

    async def test_2x2_docks_with_proxy_view0(self):
        self.view.set_layout("2x2")
        self._run_transition()
        self.assertEqual(self.errors, [])
        self.assertEqual([n for n, _ in self.events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(self.events[0][1]["layout"], "2x2")
        self.assertEqual(
            [v["viewport_name"] for v in self.events[0][1]["views"]],
            ["Viewport", "MORPH Viewport 1", "MORPH Viewport 2", "MORPH Viewport 3"],
        )
        self.assertEqual(self.created["MORPH Viewport 1"].dock_calls, [("Viewport", "RIGHT")])
        self.assertEqual(self.created["MORPH Viewport 2"].dock_calls, [("Viewport", "BOTTOM")])
        self.assertEqual(self.created["MORPH Viewport 3"].dock_calls, [("MORPH Viewport 1", "BOTTOM")])
        # 완료 시 view 0(프록시)에 포커스를 줘도 예외가 없어야 한다
        self.assertEqual(_FakeViewportWindowClass.active_window.title, "Viewport")

    async def test_focus_view_roundtrip_through_proxy(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.events.clear()
        self.view.focus_view(1)
        self.assertEqual(_FakeViewportWindowClass.active_window.title, "MORPH Viewport 1")
        self.view.focus_view(0)  # view 0은 프록시: 다시 감싸면 TypeError였다
        self.assertEqual(_FakeViewportWindowClass.active_window.title, "Viewport")
        self._tick()
        self.assertEqual([n for n, _ in self.events], [EVENT_FOCUS_CHANGED, EVENT_FOCUS_CHANGED])
        self.assertEqual(self.errors, [])

    async def test_external_activation_detected(self):
        self.view.set_layout("2x2")
        self._run_transition()
        self.events.clear()
        _FakeViewportWindowClass.active_window = weakref.proxy(self.created["MORPH Viewport 2"])
        self._tick()
        self.assertEqual([(n, p["view_id"]) for n, p in self.events], [(EVENT_FOCUS_CHANGED, 2)])

    async def test_focus_view_same_view_refocuses_after_foreign_window(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.view.focus_view(1)
        self._tick()
        self.events.clear()
        foreign = FakeKitWindow("Viewport 2")  # 이 익스텐션이 만들지 않은 뷰포트 창
        _FakeViewportWindowClass.active_window = weakref.proxy(foreign)
        self._tick()  # 무시: 포커스 뷰는 그대로
        self.assertEqual(self.view.get_focused_view().view_id, 1)
        focus_calls = self.created["MORPH Viewport 1"].focus_calls
        self.view.focus_view(1)  # 같은 view_id라도 Kit 활성 뷰포트를 다시 맞춘다
        self.assertEqual(_FakeViewportWindowClass.active_window.title, "MORPH Viewport 1")
        self.assertEqual(self.created["MORPH Viewport 1"].focus_calls, focus_calls + 1)
        self._tick()
        self.assertEqual(self.events, [])  # 논리 포커스는 안 바뀜 → focus_changed 없음
        self.view.focus_view(1)  # 이미 맞으면 아무것도 안 함
        self.assertEqual(self.created["MORPH Viewport 1"].focus_calls, focus_calls + 1)

    async def test_relayout_redocks_without_proxy_args(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.view.set_layout("2x1")
        self._run_transition()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.created["MORPH Viewport 1"].dock_calls, [("Viewport", "RIGHT"), ("Viewport", "BOTTOM")])
        self.view.set_layout("1x1")
        self._run_transition()
        self.assertTrue(self.created["MORPH Viewport 1"].destroyed)

    async def test_docking_failure_falls_back_to_1x1_once(self):
        self.missing_titles.add("Viewport")  # 도킹 대상 핸들을 못 찾음 → 도킹 실패
        self.view.set_layout("2x2")
        self._run_transition()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.view.get_layout(), "1x1")
        self.assertEqual([n for n, _ in self.events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(self.events[0][1]["layout"], "1x1")
        self.assertEqual(len(self.events[0][1]["views"]), 1)
        self.assertTrue(all(w.destroyed for w in self.created.values()))
        for _ in range(5):  # 반복되지 않는다
            self._tick()
        self.assertEqual(len(self.errors), 1)

    # ------------------------------------------------------------ 렌더 준비 게이트
    async def test_no_windows_before_first_rendered_frame(self):
        self.rendering = False  # renderer.asyncInit: 기본 뷰포트가 아직 프레임을 못 냄
        self.view.set_layout("2x2")
        self._tick(20)
        # 구독 시 Kit이 동기로 한 번 부르는 콜백은 렌더된 프레임으로 세지 않는다
        self.assertEqual(self.created, {})
        self.assertEqual(self.events, [])
        self.assertTrue(self.view._core.transitioning)
        self.assertEqual(self.view.get_layout(), "2x2")  # 논리 상태는 이미 바뀜
        self.assertEqual([v.viewport_name for v in self.view.list_views()], ["Viewport", "", "", ""])
        self.assertFalse(self.adapter.render_ready)

        self.rendering = True  # 렌더러 준비됨
        self._run_transition()
        self.assertTrue(self.adapter.render_ready)
        self.assertEqual(self.errors, [])
        self.assertEqual(sorted(self.created), ["MORPH Viewport 1", "MORPH Viewport 2", "MORPH Viewport 3"])
        self.assertEqual([n for n, _ in self.events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(self.events[0][1]["layout"], "2x2")
        self.assertEqual(self.window0.viewport_api.frame_callbacks, [])  # 통과 후 구독 해제

    async def test_windows_created_one_per_frame(self):
        self.view.set_layout("2x2")
        counts = []
        for _ in range(30):
            self._tick()
            counts.append(len(self.created))
            if not self.view._core.transitioning:
                break
        self.assertEqual(counts[-1], 3)
        self.assertTrue(all(b - a <= 1 for a, b in zip([0, *counts], counts)), counts)

    async def test_render_ready_is_checked_once(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.assertTrue(self.adapter.render_ready)
        self.view.set_layout("1x1")
        self._run_transition()
        self.rendering = False  # 이후에는 다시 기다리지 않는다
        self.view.set_layout("1x2")
        self._run_transition()
        self.assertEqual(self.view.get_layout(), "1x2")
        self.assertFalse(self.view._core.transitioning)
        self.assertEqual(self.errors, [])

    async def test_render_timeout_falls_back_to_1x1(self):
        self.rendering = False
        self.view.set_layout("2x2")
        self._tick(3)
        self.clock.now = KitWindowAdapter.RENDER_READY_TIMEOUT_S + 1.0
        self._tick()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.view.get_layout(), "1x1")
        self.assertFalse(self.view._core.transitioning)
        self.assertEqual([n for n, _ in self.events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(self.events[0][1]["layout"], "1x1")
        self.assertEqual(self.created, {})
        self.assertEqual(self.window0.viewport_api.frame_callbacks, [])  # 대기 구독 해제
        self._tick(5)  # 재시도하지 않는다
        self.assertEqual(len(self.errors), 1)

        # 다음 set_layout은 처음부터 다시 기다린다 (시작 시각도 새로)
        self.events.clear()
        self.rendering = True
        self.view.set_layout("1x2")
        self._run_transition()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual([(n, p["layout"]) for n, p in self.events], [(EVENT_LAYOUT_CHANGED, "1x2")])

    async def test_shrink_to_1x1_while_waiting_needs_no_render(self):
        self.rendering = False
        self.view.set_layout("2x2")
        self._tick(3)
        self.view.set_layout("1x1")  # 만들 창이 없으므로 렌더를 기다리지 않는다
        self._run_transition()
        self.assertFalse(self.view._core.transitioning)
        self.assertEqual([(n, p["layout"]) for n, p in self.events], [(EVENT_LAYOUT_CHANGED, "1x1")])
        self.assertEqual(self.window0.viewport_api.frame_callbacks, [])
        self.assertEqual(self.errors, [])

    async def test_shutdown_cancels_render_wait(self):
        self.rendering = False
        self.view.set_layout("1x2")
        self._tick(3)
        self.assertEqual(len(self.window0.viewport_api.frame_callbacks), 1)
        self.view._shutdown()
        self.assertEqual(self.window0.viewport_api.frame_callbacks, [])
        self.assertEqual(self.created, {})


class TestKitWindowAdapterDockRatios(KitAdapterHarness):
    """1.3.0: 도킹 단계마다 비율, "1+3", 같은 뷰 개수 전환의 재도킹."""

    def adapter_kwargs(self) -> dict:
        self.side = 0.25
        self.side_reads = 0

        def side_ratio():
            self.side_reads += 1
            return self.side

        return {"side_ratio": side_ratio}

    def _docks(self, view_id):
        w = self.created[f"MORPH Viewport {view_id}"]
        return list(zip(w.dock_calls, w.dock_ratios))

    def _ready(self, layout):
        self.events.clear()
        self.view.set_layout(layout)
        self._run_transition()
        self.assertFalse(self.view._core.transitioning)
        self.assertEqual(self.errors, [])
        return [(n, p.get("layout", p.get("view_id"))) for n, p in self.events]

    async def test_grid_layouts_dock_at_half(self):
        self._ready("2x2")
        self.assertEqual(self._docks(1), [(("Viewport", "RIGHT"), 0.5)])
        self.assertEqual(self._docks(2), [(("Viewport", "BOTTOM"), 0.5)])
        self.assertEqual(self._docks(3), [(("MORPH Viewport 1", "BOTTOM"), 0.5)])

    async def test_1p3_docks_with_ratios(self):
        self.assertEqual(self._ready("1+3"), [(EVENT_LAYOUT_CHANGED, "1+3")])
        self.assertEqual(self._docks(1), [(("Viewport", "RIGHT"), 0.25)])
        (call2, ratio2), = self._docks(2)
        self.assertEqual(call2, ("MORPH Viewport 1", "BOTTOM"))
        self.assertAlmostEqual(ratio2, 2.0 / 3.0)
        self.assertEqual(self._docks(3), [(("MORPH Viewport 2", "BOTTOM"), 0.5)])
        self.assertEqual(
            [v["viewport_name"] for v in self.events[0][1]["views"]],
            ["Viewport", "MORPH Viewport 1", "MORPH Viewport 2", "MORPH Viewport 3"],
        )

    async def test_2x2_to_1p3_redocks_same_windows(self):
        self._ready("2x2")
        windows = dict(self.created)
        cameras = [v.camera_path for v in self.view.list_views()]
        self.view.focus_view(3)
        self._tick()
        self.assertEqual(self._ready("1+3"), [(EVENT_LAYOUT_CHANGED, "1+3")])  # 포커스·카메라 그대로 → 이벤트 하나
        self.assertEqual(self.created, windows)  # 창을 새로 만들거나 없애지 않음
        self.assertFalse(any(w.destroyed for w in windows.values()))
        # view 1의 비율부터 달라지므로 1~3 모두 다시 도킹
        self.assertEqual(self._docks(1)[-1], (("Viewport", "RIGHT"), 0.25))
        self.assertEqual(self._docks(2)[-1][0], ("MORPH Viewport 1", "BOTTOM"))
        self.assertEqual(self._docks(3)[-1], (("MORPH Viewport 2", "BOTTOM"), 0.5))
        self.assertEqual([v.camera_path for v in self.view.list_views()], cameras)  # 같은 view_id는 카메라 유지
        self.assertEqual(self.view.get_focused_view().view_id, 3)
        self.assertEqual(_FakeViewportWindowClass.active_window.title, "MORPH Viewport 3")

        self.assertEqual(self._ready("2x2"), [(EVENT_LAYOUT_CHANGED, "2x2")])
        self.assertEqual(self.created, windows)
        self.assertEqual(self._docks(1)[-1], (("Viewport", "RIGHT"), 0.5))
        self.assertEqual(self._docks(2)[-1], (("Viewport", "BOTTOM"), 0.5))
        self.assertEqual(self._docks(3)[-1], (("MORPH Viewport 1", "BOTTOM"), 0.5))
        self.assertEqual([len(self._docks(v)) for v in (1, 2, 3)], [3, 3, 3])

    async def test_1p3_shrink_and_expand(self):
        self._ready("1+3")
        w2, w3 = self.created["MORPH Viewport 2"], self.created["MORPH Viewport 3"]
        self.view.focus_view(2)
        self._tick()
        # 1+3 → 1x2: view 2·3 창 정리, view 1은 비율이 달라 다시 도킹, 포커스는 view 0으로
        self.assertEqual(self._ready("1x2"), [(EVENT_LAYOUT_CHANGED, "1x2"), (EVENT_FOCUS_CHANGED, 0)])
        self.assertTrue(w2.destroyed and w3.destroyed)
        self.assertEqual(self._docks(1)[-1], (("Viewport", "RIGHT"), 0.5))
        # 1x2 → 1+3: view 1 다시 도킹(0.25), view 2·3 새로 생성
        self.assertEqual(self._ready("1+3"), [(EVENT_LAYOUT_CHANGED, "1+3")])
        self.assertIsNot(self.created["MORPH Viewport 2"], w2)
        self.assertEqual(self._docks(1)[-1], (("Viewport", "RIGHT"), 0.25))
        self.assertEqual(self._docks(3), [(("MORPH Viewport 2", "BOTTOM"), 0.5)])

    async def test_grid_expand_keeps_existing_dock(self):
        # 1.2.0까지와 같은 동작: 1x2 → 2x2에서 view 1은 다시 도킹하지 않는다
        self._ready("1x2")
        self._ready("2x2")
        self.assertEqual(self._docks(1), [(("Viewport", "RIGHT"), 0.5)])

    async def test_split_view_side_ratio_overrides_adapter_source(self):
        # 1.5.0: SplitView가 begin_layout에 sideRatio를 항상 넘긴다. 어댑터 자체 출처(side_ratio 인자)는
        # side_ratio 없이 begin_layout을 부를 때만 쓰인다.
        self.side = 0.4
        self._ready("1+3")
        self.assertEqual(self._docks(1), [(("Viewport", "RIGHT"), 0.25)])  # SplitView 값 (기본 0.25)
        self.assertEqual(self.side_reads, 0)
        self.adapter.begin_layout("1+3", self.view._core.camera_of)  # 인자 없음 → 어댑터 출처
        self.assertEqual(self.side_reads, 1)
        self.assertEqual(self.adapter._plan[0], (1, 0, "RIGHT", 0.4))
        self.adapter.begin_layout("1+3", self.view._core.camera_of, side_ratio=0.3)
        self.assertEqual(self.adapter._plan[0], (1, 0, "RIGHT", 0.3))
        self.assertEqual(self.side_reads, 1)
