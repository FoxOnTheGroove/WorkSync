"""논리 상태 테스트 (논리 전용 모드 / 가짜 창 어댑터, 업데이트는 수동 _tick)."""

import omni.kit.test

from .. import EVENT_FOCUS_CHANGED, EVENT_LAYOUT_CHANGED, EVENT_VIEW_CAMERA_CHANGED, SplitView, ViewInfo
from ..adapters import HeadlessAdapter
from .fakes import FakeWindowAdapter

PERSP, TOP, FRONT, RIGHT = "/OmniverseKit_Persp", "/OmniverseKit_Top", "/OmniverseKit_Front", "/OmniverseKit_Right"


class _Harness:
    def __init__(self, test, adapter=None, **kwargs):
        self.events = []
        self.states = []
        self.adapter = adapter or HeadlessAdapter()
        self.view = SplitView(
            self.adapter,
            emit=lambda name, payload: self.events.append((name, payload)),
            state_sink=self.states.append,
            **kwargs,
        )
        test.addCleanup(self.view._shutdown)

    def tick(self, n=1):
        for _ in range(n):
            self.view._tick()

    def names(self):
        return [name for name, _ in self.events]

    def take(self):
        out, self.events = self.events, []
        return out


class TestSplitViewLogic(omni.kit.test.AsyncTestCase):
    async def test_is_headless(self):
        self.assertTrue(_Harness(self).view.is_headless)
        self.assertFalse(_Harness(self, FakeWindowAdapter()).view.is_headless)

    async def test_initial_state(self):
        h = _Harness(self)
        self.assertEqual(h.view.get_layout(), "1x1")
        self.assertEqual(h.view.list_views(), [ViewInfo(0, "", PERSP, True)])
        self.assertEqual(h.view.get_focused_view().view_id, 0)

    async def test_set_layout_is_logical_immediately_event_next_frame(self):
        h = _Harness(self)
        h.view.set_layout("2x2")
        self.assertEqual(h.view.get_layout(), "2x2")
        views = h.view.list_views()
        self.assertEqual([v.view_id for v in views], [0, 1, 2, 3])
        self.assertEqual([v.camera_path for v in views], [PERSP, TOP, FRONT, RIGHT])
        self.assertEqual([v.viewport_name for v in views], ["", "", "", ""])
        self.assertEqual(h.events, [])  # 다음 업데이트 프레임에 발행
        h.tick()
        self.assertEqual(h.names(), [EVENT_LAYOUT_CHANGED])
        payload = h.events[0][1]
        self.assertEqual(payload["layout"], "2x2")
        self.assertEqual(payload["focused_view_id"], 0)
        self.assertEqual(
            payload["views"][2], {"view_id": 2, "viewport_name": "", "camera_path": FRONT}
        )
        self.assertEqual(len(h.states), 1)
        self.assertEqual(h.states[0]["layout"], "2x2")

    async def test_same_layout_is_noop(self):
        h = _Harness(self)
        h.view.set_layout("1x1")
        h.tick()
        self.assertEqual(h.events, [])
        self.assertEqual(h.states, [])

    async def test_invalid_layout(self):
        h = _Harness(self)
        for bad in ("3x3", "", None, 4):
            with self.assertRaises(ValueError):
                h.view.set_layout(bad)
        self.assertEqual(h.view.get_layout(), "1x1")

    async def test_existing_views_keep_camera_new_views_get_default(self):
        h = _Harness(self)
        h.view.set_layout("2x2")
        h.tick()
        h.view.set_view_camera(1, "/Cam1")
        h.view.set_view_camera(3, "/Cam3")
        h.tick()
        h.take()
        h.view.set_layout("1x2")
        self.assertEqual([v.camera_path for v in h.view.list_views()], [PERSP, "/Cam1"])
        h.view.set_layout("2x2")
        self.assertEqual([v.camera_path for v in h.view.list_views()], [PERSP, "/Cam1", FRONT, RIGHT])
        h.tick()
        # 새 뷰의 기본 카메라는 view_camera_changed 없이 layout_changed에만 담긴다
        self.assertEqual(h.names(), [EVENT_LAYOUT_CHANGED])

    async def test_default_cameras_setting(self):
        h = _Harness(self, default_cameras=["/A", "/B"])
        self.assertEqual(h.view.list_views()[0].camera_path, "/A")
        h.view.set_layout("2x2")
        self.assertEqual([v.camera_path for v in h.view.list_views()], ["/A", "/B", FRONT, RIGHT])

    async def test_set_view_camera_events_and_errors(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.tick()
        h.take()
        h.view.set_view_camera(1, "/AnyPathInLogicalMode")  # 논리 전용: prim 검사 없음
        h.view.set_view_camera(1, "/AnyPathInLogicalMode")  # 같은 경로: 아무것도 안 함
        h.tick()
        self.assertEqual(
            h.take(),
            [(EVENT_VIEW_CAMERA_CHANGED, {"view_id": 1, "viewport_name": "", "camera_path": "/AnyPathInLogicalMode"})],
        )
        with self.assertRaises(IndexError):
            h.view.set_view_camera(2, "/X")
        with self.assertRaises(IndexError):
            h.view.set_view_camera(-1, "/X")
        with self.assertRaises(ValueError):
            h.view.set_view_camera(0, "")
        with self.assertRaises(ValueError):
            h.view.set_view_camera(0, None)
        h.tick()
        self.assertEqual(h.events, [])

    async def test_focus_view(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.tick()
        h.take()
        h.view.focus_view(1)
        self.assertEqual(h.view.get_focused_view(), ViewInfo(1, "", TOP, True))
        h.view.focus_view(1)  # 이미 포커스
        h.tick()
        self.assertEqual(h.take(), [(EVENT_FOCUS_CHANGED, {"view_id": 1, "viewport_name": "", "camera_path": TOP})])
        self.assertEqual([v.focused for v in h.view.list_views()], [False, True])
        for bad in (2, -1, True, "1"):
            with self.assertRaises(IndexError):
                h.view.focus_view(bad)

    async def test_shrink_moves_focus_to_view0_after_layout_changed(self):
        h = _Harness(self)
        h.view.set_layout("2x2")
        h.tick()
        h.view.focus_view(3)
        h.tick()
        h.take()
        h.view.set_layout("1x2")
        self.assertEqual(h.view.get_focused_view().view_id, 0)  # 논리 상태는 즉시
        h.tick()
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED])
        self.assertEqual(events[0][1]["focused_view_id"], 0)
        self.assertEqual(events[1][1]["view_id"], 0)

    async def test_grow_keeps_focus(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.tick()
        h.view.focus_view(1)
        h.tick()
        h.take()
        h.view.set_layout("2x2")
        h.tick()
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(events[0][1]["focused_view_id"], 1)

    async def test_merge_in_same_frame(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.view.set_layout("2x2")
        h.tick(3)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(events[0][1]["layout"], "2x2")

    async def test_merge_during_multi_frame_transition(self):
        adapter = FakeWindowAdapter(steps=3)
        h = _Harness(self, adapter)
        h.view.set_layout("1x2")
        h.tick()
        h.view.set_layout("2x1")
        h.tick(10)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])
        self.assertEqual(events[0][1]["layout"], "2x1")
        self.assertEqual(adapter.begun, ["1x2", "2x1"])

    async def test_focus_coalesced_during_transition(self):
        adapter = FakeWindowAdapter(steps=3)
        h = _Harness(self, adapter)
        h.view.set_layout("1x2")
        h.view.focus_view(1)  # 창이 아직 없음 → 생길 때 포커스
        self.assertEqual(adapter.focus_calls, [])
        h.tick(5)
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED])
        self.assertEqual(events[1][1], {"view_id": 1, "viewport_name": "MORPH Viewport 1", "camera_path": TOP})
        self.assertEqual(adapter.active, 1)

    async def test_stage_opened_resets_changed_views(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.tick()
        h.view.set_view_camera(0, "/X")
        h.tick()
        h.take()
        h.view._on_stage_opened()
        h.tick()
        self.assertEqual(h.take(), [(EVENT_VIEW_CAMERA_CHANGED, {"view_id": 0, "viewport_name": "", "camera_path": PERSP})])

    async def test_deleted_camera_prim_resets_only_that_view(self):
        existing = {"/CamA", "/CamB"}
        h = _Harness(self, prim_exists=lambda p: p in existing)
        h.view.set_layout("1x2")
        h.tick()
        h.view.set_view_camera(0, "/CamA")
        h.view.set_view_camera(1, "/CamB")
        h.tick()
        h.take()
        existing.discard("/CamB")
        h.tick()
        self.assertEqual(h.take(), [(EVENT_VIEW_CAMERA_CHANGED, {"view_id": 1, "viewport_name": "", "camera_path": TOP})])
        self.assertEqual(h.view.list_views()[0].camera_path, "/CamA")
        h.tick(3)  # 기본 카메라 prim도 없지만 반복 복귀하지 않는다
        self.assertEqual(h.events, [])

    async def test_state_push_merged_per_frame(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.tick()
        h.states.clear()
        h.view.focus_view(1)
        h.view.set_view_camera(0, "/Y")
        h.tick()
        self.assertEqual(len(h.states), 1)
        self.assertEqual(
            h.states[0],
            {
                "layout": "1x2",
                "focused_view_id": 1,
                "views": [
                    {"view_id": 0, "viewport_name": "", "camera_path": "/Y", "rect": None},
                    {"view_id": 1, "viewport_name": "", "camera_path": TOP, "rect": None},
                ],
                "side_ratio": 0.25,  # 1.5.0
            },
        )
        h.tick()
        self.assertEqual(len(h.states), 1)  # 이벤트 없는 프레임은 송신 없음

    async def test_shutdown_is_silent(self):
        h = _Harness(self)
        h.view.set_layout("1x2")
        h.view._shutdown()
        h.tick()
        self.assertEqual(h.events, [])
        with self.assertRaises(RuntimeError):
            h.view.set_layout("2x2")


class TestSameCountTransition(omni.kit.test.AsyncTestCase):
    """1.3.0: 뷰 개수가 같은 2x2 ↔ 1+3 전환 (논리 상태)."""

    async def test_2x2_to_1p3_keeps_views(self):
        h = _Harness(self)
        h.view.set_layout("2x2")
        h.tick()
        h.view.set_view_camera(2, "/CamA")
        h.view.focus_view(3)
        h.tick()
        h.take()
        h.view.set_layout("1+3")
        self.assertEqual(h.view.get_layout(), "1+3")
        self.assertEqual([v.camera_path for v in h.view.list_views()], [PERSP, TOP, "/CamA", RIGHT])
        self.assertEqual(h.view.get_focused_view().view_id, 3)
        h.tick()
        events = h.take()
        self.assertEqual([n for n, _ in events], [EVENT_LAYOUT_CHANGED])  # 포커스·카메라 변경 이벤트 없음
        self.assertEqual(events[0][1]["layout"], "1+3")
        self.assertEqual(events[0][1]["focused_view_id"], 3)
        self.assertEqual([v["camera_path"] for v in events[0][1]["views"]], [PERSP, TOP, "/CamA", RIGHT])
        h.view.set_layout("1+3")  # 같은 레이아웃: 아무것도 안 함
        h.tick()
        self.assertEqual(h.events, [])
        h.view.set_layout("2x2")
        h.tick()
        self.assertEqual([(n, p["layout"]) for n, p in h.take()], [(EVENT_LAYOUT_CHANGED, "2x2")])

    async def test_1p3_shrink_and_expand(self):
        h = _Harness(self)
        h.view.set_layout("1+3")
        h.tick()
        h.view.focus_view(2)
        h.tick()
        h.take()
        h.view.set_layout("1x2")
        h.tick()
        self.assertEqual(h.names(), [EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED])
        self.assertEqual(h.events[1][1]["view_id"], 0)
        h.take()
        h.view.set_layout("1+3")
        h.tick()
        self.assertEqual(h.names(), [EVENT_LAYOUT_CHANGED])
        self.assertEqual([v.camera_path for v in h.view.list_views()], [PERSP, TOP, FRONT, RIGHT])
