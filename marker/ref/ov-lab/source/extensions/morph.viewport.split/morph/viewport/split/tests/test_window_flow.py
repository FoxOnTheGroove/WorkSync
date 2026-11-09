"""창 모드 핵심 흐름 (가짜 창 어댑터)."""

import omni.kit.test
import omni.usd
from pxr import UsdGeom

from .. import EVENT_FOCUS_CHANGED, EVENT_LAYOUT_CHANGED, EVENT_VIEW_CAMERA_CHANGED, SplitView
from ..adapters import validate_camera_prim
from .fakes import FakeWindowAdapter

PERSP, TOP = "/OmniverseKit_Persp", "/OmniverseKit_Top"


class _Harness:
    def __init__(self, test, adapter, **kwargs):
        self.events = []
        self.adapter = adapter
        self.view = SplitView(adapter, emit=lambda n, p: self.events.append((n, p)), **kwargs)
        test.addCleanup(self.view._shutdown)

    def tick(self, n=1):
        for _ in range(n):
            self.view._tick()

    def take(self):
        out, self.events = self.events, []
        return out

    def ready(self, layout):
        self.view.set_layout(layout)
        self.tick(self.adapter.steps + 1)
        return self.take()


class TestWindowFlow(omni.kit.test.AsyncTestCase):
    async def test_startup_reads_view0_from_viewport(self):
        h = _Harness(self, FakeWindowAdapter(camera0="/StartCam"))
        info = h.view.list_views()[0]
        self.assertEqual((info.viewport_name, info.camera_path), ("Viewport", "/StartCam"))

    async def test_layout_changed_after_windows_ready(self):
        adapter = FakeWindowAdapter(steps=3)
        h = _Harness(self, adapter)
        h.view.set_layout("1x2")
        h.tick(2)
        self.assertEqual(h.events, [])
        self.assertEqual([v.viewport_name for v in h.view.list_views()], ["Viewport", ""])  # 준비 전
        h.tick()
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(
            events[0][1]["views"],
            [
                {"view_id": 0, "viewport_name": "Viewport", "camera_path": PERSP},
                {"view_id": 1, "viewport_name": "MORPH Viewport 1", "camera_path": TOP},
            ],
        )
        self.assertEqual(adapter.extras[1].camera, TOP)

    async def test_pending_camera_applied_when_window_created(self):
        adapter = FakeWindowAdapter(steps=3)
        h = _Harness(self, adapter)
        h.view.set_layout("1x2")
        h.view.set_view_camera(1, "/PendingCam")  # 창이 아직 없음
        self.assertNotIn(1, adapter.extras)
        h.tick(4)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])  # 새 뷰라 camera 이벤트 없음
        self.assertEqual(events[0][1]["views"][1]["camera_path"], "/PendingCam")
        self.assertEqual(adapter.extras[1].camera, "/PendingCam")

    async def test_focus_view_sets_kit_active(self):
        adapter = FakeWindowAdapter()
        h = _Harness(self, adapter)
        h.ready("1x2")
        h.view.focus_view(1)
        self.assertEqual(adapter.active, 1)
        h.tick()
        self.assertEqual(h.take(), [(EVENT_FOCUS_CHANGED, {"view_id": 1, "viewport_name": "MORPH Viewport 1", "camera_path": TOP})])

    async def test_external_active_viewport_change(self):
        adapter = FakeWindowAdapter()
        h = _Harness(self, adapter)
        h.ready("1x2")
        adapter.active = 1  # 사용자가 창 클릭
        h.tick()
        self.assertEqual([n for n, _ in h.take()], [EVENT_FOCUS_CHANGED])
        self.assertEqual(h.view.get_focused_view().view_id, 1)

    async def test_focus_view_same_view_refocuses_when_kit_active_differs(self):
        adapter = FakeWindowAdapter()
        h = _Harness(self, adapter)
        h.ready("1x2")
        adapter.active = None  # 이 익스텐션이 만들지 않은 뷰포트 창이 활성 (무시 대상)
        h.tick()
        self.assertEqual(h.take(), [])
        calls = len(adapter.focus_calls)
        h.view.focus_view(0)  # 이미 포커스 뷰지만 Kit 활성 뷰포트가 다름 → 다시 포커스
        self.assertEqual(adapter.active, 0)
        self.assertEqual(adapter.focus_calls[calls:], [0])
        h.tick()
        self.assertEqual(h.take(), [])  # 논리 포커스는 그대로 → focus_changed 없음
        h.view.focus_view(0)  # 이미 맞으면 아무것도 안 함
        self.assertEqual(len(adapter.focus_calls), calls + 1)

    async def test_focus_on_click_disabled_restores_kit_active(self):
        adapter = FakeWindowAdapter()
        h = _Harness(self, adapter, focus_on_click=False)
        h.ready("1x2")
        adapter.active = 1
        h.tick()
        self.assertEqual(h.take(), [])
        self.assertEqual(h.view.get_focused_view().view_id, 0)
        self.assertEqual(adapter.active, 0)  # 포커스 뷰 = Kit 활성 뷰포트 유지

    async def test_external_camera_change(self):
        adapter = FakeWindowAdapter()
        h = _Harness(self, adapter)
        h.ready("1x2")
        adapter.window0.camera = "/ExternallySet"  # set_active_camera / Kit UI
        h.tick()
        self.assertEqual(
            h.take(),
            [(EVENT_VIEW_CAMERA_CHANGED, {"view_id": 0, "viewport_name": "Viewport", "camera_path": "/ExternallySet"})],
        )
        self.assertEqual(h.view.list_views()[0].camera_path, "/ExternallySet")

    async def test_external_camera_change_during_transition_is_kept(self):
        adapter = FakeWindowAdapter(steps=3)
        h = _Harness(self, adapter)
        h.ready("1x2")
        h.view.set_layout("2x2")
        adapter.window0.camera = "/SetActiveCamera"  # 같은 프레임에 camera.control set_active_camera
        adapter.extras[1].camera = "/KitUiCam"  # 전환 중 유지되는 기존 뷰
        h.tick(adapter.steps + 1)
        events = h.take()
        self.assertEqual(
            [n for n, _ in events], [EVENT_VIEW_CAMERA_CHANGED, EVENT_VIEW_CAMERA_CHANGED, EVENT_LAYOUT_CHANGED]
        )
        self.assertEqual([p["view_id"] for _, p in events[:2]], [0, 1])
        views = events[2][1]["views"]
        self.assertEqual([v["camera_path"] for v in views[:2]], ["/SetActiveCamera", "/KitUiCam"])
        self.assertEqual(adapter.window0.camera, "/SetActiveCamera")  # 덮어쓰지 않음
        self.assertEqual(adapter.extras[1].camera, "/KitUiCam")

    async def test_click_during_transition_is_reverted(self):
        adapter = FakeWindowAdapter(steps=3)
        h = _Harness(self, adapter)
        h.ready("1x2")
        h.view.set_layout("2x2")
        h.tick()
        adapter.active = 1  # 전환 중 클릭 (정책: 반영하지 않음)
        h.tick(adapter.steps)
        self.assertEqual([n for n, _ in h.take()], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(h.view.get_focused_view().view_id, 0)
        self.assertEqual(adapter.active, 0)

    async def test_user_closes_extra_window_returns_to_1x1(self):
        adapter = FakeWindowAdapter()
        h = _Harness(self, adapter)
        h.ready("2x2")
        h.view.focus_view(2)
        h.tick()
        h.take()
        adapter.extras[3].visible = False  # 사용자가 닫음
        h.tick()
        self.assertEqual(h.view.get_layout(), "1x1")
        h.tick(adapter.steps + 1)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED])
        self.assertEqual(events[0][1]["layout"], "1x1")
        self.assertEqual(len(events[0][1]["views"]), 1)
        self.assertEqual(adapter.extras, {})
        self.assertEqual(adapter.active, 0)

    async def test_viewport_rejecting_camera_is_value_error(self):
        adapter = FakeWindowAdapter(valid_cameras={PERSP, TOP})
        h = _Harness(self, adapter)
        with self.assertRaises(ValueError):
            h.view.set_view_camera(0, "/NotACameraForViewport")
        self.assertEqual(h.view.list_views()[0].camera_path, PERSP)

    async def test_camera_validation_against_stage(self):
        ctx = omni.usd.get_context()
        await ctx.new_stage_async()
        stage = ctx.get_stage()
        UsdGeom.Camera.Define(stage, "/World/Cam")
        UsdGeom.Xform.Define(stage, "/World/NotCam")
        adapter = FakeWindowAdapter(validate=lambda p: validate_camera_prim(ctx.get_stage(), p))
        h = _Harness(self, adapter)
        h.view.set_view_camera(0, "/World/Cam")
        self.assertEqual(adapter.window0.camera, "/World/Cam")
        for bad in ("/World/NotCam", "/World/Missing", "not a path", "World/Cam"):
            with self.assertRaises(ValueError):
                h.view.set_view_camera(0, bad)
        self.assertEqual(h.view.list_views()[0].camera_path, "/World/Cam")

    async def test_no_stage_is_runtime_error(self):
        adapter = FakeWindowAdapter(validate=lambda p: validate_camera_prim(None, p))
        h = _Harness(self, adapter)
        with self.assertRaises(RuntimeError):
            h.view.set_view_camera(0, "/World/Cam")
        with self.assertRaises(IndexError):  # view_id 검사가 먼저
            h.view.set_view_camera(5, "/World/Cam")

    async def test_shutdown_restores_view0_camera_without_events(self):
        adapter = FakeWindowAdapter(camera0="/StartCam")
        h = _Harness(self, adapter)
        h.ready("1x2")
        h.view.set_view_camera(0, "/Other")
        h.view._shutdown()
        h.tick()
        self.assertEqual(h.events, [])
        self.assertTrue(adapter.is_shut_down)
        self.assertEqual(adapter.shutdown_camera, "/StartCam")
        self.assertEqual(adapter.window0.camera, "/StartCam")
        self.assertEqual(adapter.extras, {})

    async def test_late_window_attach(self):
        from ..adapters import HeadlessAdapter

        adapter = FakeWindowAdapter(camera0=PERSP)
        probe_results = [None, adapter]
        h = _Harness(self, HeadlessAdapter(), adapter_probe=lambda: probe_results.pop(0) if probe_results else None)
        h.view.set_layout("1x2")
        h.tick()  # 논리 전용 전환 완료
        h.take()
        self.assertTrue(h.view.is_headless)
        h.tick(32)  # 30프레임 간격 탐색, 두 번째 탐색에서 창 발견
        self.assertFalse(h.view.is_headless)
        h.tick(adapter.steps + 1)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual([v["viewport_name"] for v in events[0][1]["views"]], ["Viewport", "MORPH Viewport 1"])

    async def test_late_attach_adopts_kit_view0_camera(self):
        from ..adapters import HeadlessAdapter

        adapter = FakeWindowAdapter(camera0="/KitCam")
        probe_results = [adapter]
        h = _Harness(self, HeadlessAdapter(), adapter_probe=lambda: probe_results.pop(0) if probe_results else None)
        h.tick(1 + adapter.steps + 1)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(events[0][1]["views"][0]["camera_path"], "/KitCam")
        self.assertEqual(adapter.window0.camera, "/KitCam")  # Kit 쪽 카메라를 덮어쓰지 않음

    async def test_late_attach_applies_camera0_set_while_headless(self):
        from ..adapters import HeadlessAdapter

        adapter = FakeWindowAdapter(camera0="/KitCam")
        probe_results = [adapter]
        h = _Harness(self, HeadlessAdapter(), adapter_probe=lambda: probe_results.pop(0) if probe_results else None)
        h.view.set_view_camera(0, "/Mine")  # 논리 전용 모드 중 명시적 연결
        h.tick(1 + adapter.steps + 1)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_VIEW_CAMERA_CHANGED, EVENT_LAYOUT_CHANGED])
        self.assertEqual(events[1][1]["views"][0]["camera_path"], "/Mine")
        self.assertEqual(adapter.window0.camera, "/Mine")
