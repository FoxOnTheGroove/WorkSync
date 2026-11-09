"""뷰 창 꾸밈 (hideTabBar / viewPadding / dockSplitterSize).

규칙 단위 테스트와, 실제 KitWindowAdapter를 가짜 Kit 창으로 돌리는 흐름 테스트.
ImGui 분할선 push는 주입한 가짜 imgui(`FakeImGui`)로만 검증한다 (실제 전역 스타일을 건드리지 않음).
"""

from unittest import mock

import omni.kit.test

from .. import EVENT_LAYOUT_CHANGED, SplitView
from .. import adapters as adapters_mod
from .. import style as style_mod
from ..style import DockSplitter, ViewStyle, apply_window_attrs, read_view_style, window_attrs
from .fakes import FakeImGui, FakeKitWindow, FakeWindowAdapter
from .test_kit_adapter import KitAdapterHarness

ROOT = "/exts/morph.viewport.split"
SPLITTER_VAR = "DockSplitterSize"
STYLED = {"dock_tab_bar_visible": False, "dock_tab_bar_enabled": False, "padding_x": 0.0, "padding_y": 0.0}
DEFAULT = {"dock_tab_bar_visible": True, "dock_tab_bar_enabled": True, "padding_x": 4.0, "padding_y": 4.0}


class KitWindowAdapterFrames:
    STYLE = adapters_mod.KitWindowAdapter.STYLE_CHECK_FRAMES


def _attrs(window):
    return {name: getattr(window, name) for name in DEFAULT}


class _BrokenWindow:
    """꾸밈 속성 접근이 모두 실패하는 창 핸들 (도킹 대상 제목만 있음)."""

    def __init__(self, title):
        self.title = title

    def __getattr__(self, name):
        raise RuntimeError(f"no {name}")


# ---------------------------------------------------------------------- 규칙
class TestViewStyleRules(omni.kit.test.AsyncTestCase):
    async def test_read_defaults_touch_nothing(self):
        warnings = []
        style = read_view_style(lambda _p: None, ROOT, warnings.append)
        self.assertEqual(style, ViewStyle())
        self.assertFalse(style.active)
        self.assertEqual(window_attrs(style), {})
        self.assertEqual(warnings, [])

    async def test_read_values(self):
        values = {f"{ROOT}/hideTabBar": True, f"{ROOT}/viewPadding": 0, f"{ROOT}/dockSplitterSize": 1.0}
        style = read_view_style(values.get, ROOT, lambda _m: None)
        self.assertEqual(style, ViewStyle(hide_tab_bar=True, padding=0.0, splitter_size=1.0))
        self.assertEqual(window_attrs(style), STYLED)

    async def test_read_invalid_numbers_warn_and_are_ignored(self):
        for bad in (-1, "1", True, float("nan"), [1]):
            warnings = []
            values = {f"{ROOT}/viewPadding": bad, f"{ROOT}/dockSplitterSize": bad}
            style = read_view_style(values.get, ROOT, warnings.append)
            self.assertEqual((style.padding, style.splitter_size), (None, None), bad)
            self.assertEqual(len(warnings), 2, bad)

    async def test_padding_only_leaves_tab_bar(self):
        self.assertEqual(window_attrs(ViewStyle(padding=1.5)), {"padding_x": 1.5, "padding_y": 1.5})
        self.assertEqual(
            window_attrs(ViewStyle(hide_tab_bar=True)), {"dock_tab_bar_visible": False, "dock_tab_bar_enabled": False}
        )

    async def test_apply_writes_only_differences(self):
        window = FakeKitWindow("W")
        window.padding_y = 0.0
        before, failures = apply_window_attrs(window, STYLED)
        self.assertEqual(failures, [])
        self.assertEqual(before, {"dock_tab_bar_visible": True, "dock_tab_bar_enabled": True, "padding_x": 4.0})
        self.assertEqual(_attrs(window), STYLED)
        self.assertEqual(apply_window_attrs(window, STYLED), ({}, []))

    async def test_apply_collects_failures_and_continues(self):
        class PartlyBroken(FakeKitWindow):
            def __setattr__(self, name, value):
                if name == "dock_tab_bar_enabled" and hasattr(self, "padding_y"):
                    raise RuntimeError("read only")
                super().__setattr__(name, value)

        window = PartlyBroken("W")
        before, failures = apply_window_attrs(window, STYLED)
        self.assertEqual([name for name, _ in failures], ["dock_tab_bar_enabled"])
        self.assertEqual(set(before), {"dock_tab_bar_visible", "padding_x", "padding_y"})
        self.assertFalse(window.dock_tab_bar_visible)
        self.assertEqual((window.padding_x, window.padding_y), (0.0, 0.0))

    async def test_splitter_push_once_and_pop(self):
        imgui = FakeImGui()
        splitter = DockSplitter(1.0, lambda: (imgui, SPLITTER_VAR))
        splitter.push()
        splitter.push()
        self.assertEqual(imgui.stack, [(SPLITTER_VAR, 1.0)])
        self.assertEqual(imgui.push_calls, 1)
        splitter.pop()
        splitter.pop()
        self.assertEqual(imgui.stack, [])
        self.assertEqual(imgui.pop_calls, 1)

    async def test_splitter_unset_never_touches_imgui(self):
        calls = []
        splitter = DockSplitter(None, lambda: calls.append(1))
        splitter.push()
        splitter.pop()
        self.assertEqual(calls, [])

    async def test_splitter_unavailable_warns_once(self):
        warnings = []

        def factory():
            raise ImportError("No module named 'omni.kit.imgui'")

        with mock.patch.object(style_mod, "_log_warn", warnings.append):
            splitter = DockSplitter(1.0, factory)
            for _ in range(3):
                splitter.push()
            splitter.pop()
        self.assertEqual(len(warnings), 1)
        self.assertFalse(splitter.pushed)


# ---------------------------------------------------------------------- KitWindowAdapter 흐름
class _StyledHarness(KitAdapterHarness):
    style = ViewStyle(hide_tab_bar=True, padding=0.0, splitter_size=1.0)

    def adapter_kwargs(self):
        self.imgui = FakeImGui()
        self.imgui_factory_calls = 0

        def factory():
            self.imgui_factory_calls += 1
            return self.imgui, SPLITTER_VAR

        return {"style": self.style, "imgui_factory": factory}

    async def setUp(self):
        await super().setUp()
        self.warnings = []
        for p in (
            mock.patch.object(adapters_mod, "_style_warn", self.warnings.append),
            mock.patch.object(style_mod, "_log_warn", self.warnings.append),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _all_windows(self):
        return [self.window0, *(self.created[k] for k in sorted(self.created) if not self.created[k].destroyed)]


class TestViewStyleApplied(_StyledHarness):
    async def test_startup_1x1_styles_view0_and_pushes_splitter(self):
        self._tick()
        self.assertEqual(_attrs(self.window0), STYLED)
        self.assertEqual(self.imgui.stack, [(SPLITTER_VAR, 1.0)])

    async def test_all_views_styled_when_layout_changed(self):
        snapshots = []

        def emit(name, payload):
            if name == EVENT_LAYOUT_CHANGED:
                snapshots.append([_attrs(w) for w in self._all_windows()])
            self.events.append((name, payload))

        self.view._emit = emit
        self.view.set_layout("2x2")
        self._run_transition()
        self.assertEqual([n for n, _ in self.events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(snapshots, [[STYLED] * 4])  # layout_changed 시점에 이미 적용
        self.assertEqual(self.errors, [])
        self.assertEqual(self.warnings, [])

    async def test_windows_of_later_layout_are_styled(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.view.set_layout("2x2")
        self._run_transition()
        self.assertEqual(len(self._all_windows()), 4)
        for window in self._all_windows():
            self.assertEqual(_attrs(window), STYLED, window.title)

    async def test_back_to_1x1_keeps_view0_styled(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.view.set_layout("1x1")
        self._run_transition()
        self.assertEqual(_attrs(self.window0), STYLED)

    async def test_external_change_is_reapplied(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self.window0.dock_tab_bar_visible = True  # 예: 재도킹으로 탭바가 다시 나타남
        self.created["MORPH Viewport 1"].padding_x = 4.0
        self._tick(KitWindowAdapterFrames.STYLE + 1)
        self.assertEqual(_attrs(self.window0), STYLED)
        self.assertEqual(_attrs(self.created["MORPH Viewport 1"]), STYLED)

    async def test_splitter_pushed_once_across_layouts(self):
        for layout in ("1x2", "2x2", "1x1", "2x1"):
            self.view.set_layout(layout)
            self._run_transition()
        self._tick(KitWindowAdapterFrames.STYLE * 2)
        self.assertEqual(self.imgui.push_calls, 1)
        self.assertEqual(self.imgui_factory_calls, 1)

    async def test_shutdown_restores_view0_and_pops_splitter(self):
        self.view.set_layout("2x2")
        self._run_transition()
        self.view._shutdown()
        self.assertEqual(_attrs(self.window0), DEFAULT)
        self.assertEqual(self.imgui.stack, [])
        self.assertEqual(self.imgui.pop_calls, 1)

    async def test_style_failure_warns_once_and_layout_completes(self):
        self.registry["Viewport"] = _BrokenWindow("Viewport")  # omni.ui 핸들의 꾸밈 API가 모두 실패
        self.view.set_layout("2x2")
        self._run_transition()
        self.assertEqual(self.view.get_layout(), "2x2")
        self.assertEqual([(n, p["layout"]) for n, p in self.events], [(EVENT_LAYOUT_CHANGED, "2x2")])
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.warnings), 4)  # view 0의 속성 4개, 각 1회
        self._tick(KitWindowAdapterFrames.STYLE * 3)
        self.assertEqual(len(self.warnings), 4)
        for window in self._all_windows()[1:]:
            self.assertEqual(_attrs(window), STYLED)


class TestViewStyleSplitterUnavailable(_StyledHarness):
    style = ViewStyle(splitter_size=1.0)

    def adapter_kwargs(self):
        kwargs = super().adapter_kwargs()

        def factory():
            raise ImportError("No module named 'omni.kit.imgui'")

        kwargs["imgui_factory"] = factory
        return kwargs

    async def test_warns_once_and_layout_works(self):
        self.view.set_layout("1x2")
        self._run_transition()
        self._tick(KitWindowAdapterFrames.STYLE * 2)
        self.assertEqual(self.view.get_layout(), "1x2")
        self.assertEqual([n for n, _ in self.events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(len(self.warnings), 1)
        self.assertEqual(_attrs(self.window0), DEFAULT)  # 창 꾸밈 설정 없음
        self.view._shutdown()
        self.assertEqual(len(self.warnings), 1)


class TestViewStyleNotConfigured(_StyledHarness):
    style = ViewStyle()

    async def test_nothing_touched(self):
        self._tick()
        self.view.set_layout("2x2")
        self._run_transition()
        self._tick(KitWindowAdapterFrames.STYLE * 2)
        for window in self._all_windows():
            self.assertEqual(_attrs(window), DEFAULT, window.title)
        self.assertEqual(self.imgui_factory_calls, 0)
        self.view._shutdown()
        self.assertEqual(_attrs(self.window0), DEFAULT)
        self.assertEqual(self.warnings, [])


# ---------------------------------------------------------------------- SplitView 호출 시점
class TestRefreshStyleTiming(omni.kit.test.AsyncTestCase):
    async def test_refresh_only_outside_transition(self):
        adapter = FakeWindowAdapter(steps=3)
        view = SplitView(adapter, emit=lambda n, p: None)
        self.addCleanup(view._shutdown)
        view._tick()
        self.assertEqual(adapter.style_refreshes, 1)
        view.set_layout("1x2")
        for _ in range(adapter.steps):
            view._tick()  # 전환 중 (마지막 틱에서 완료)
        self.assertEqual(adapter.style_refreshes, 1)
        view._tick()
        self.assertEqual(adapter.style_refreshes, 2)

    async def test_headless_adapter_has_refresh(self):
        view = SplitView(adapters_mod.HeadlessAdapter(), emit=lambda n, p: None)
        self.addCleanup(view._shutdown)
        for _ in range(3):
            view._tick()  # 예외 없음
        self.assertTrue(view.is_headless)
