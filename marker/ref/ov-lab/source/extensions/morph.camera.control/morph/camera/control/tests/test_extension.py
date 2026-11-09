"""통합 테스트: 실제 on_startup을 지난 싱글턴, 실제 carb 이벤트·업데이트 루프·stream 등록.

테스트 kit에는 Viewport 창이 없다. 대상 뷰포트는 가짜로 넣고 끝나면 원래대로 돌린다.
"""
import carb.eventdispatcher
import omni.kit.app
import omni.kit.test
from morph.stream.messaging import get_messaging

from morph.camera.control import (
    EVENT_ACTIVE_CHANGED,
    EVENT_CHANGED,
    CameraController,
    get_camera_controller,
)
from morph.camera.control import usd_camera
from morph.camera.control.web import (
    TYPE_APPLY_PRESET,
    TYPE_DELETE_PRESET,
    TYPE_LIST_PRESETS,
    TYPE_ORBIT,
    TYPE_PAN,
    TYPE_SAVE_PRESET,
    TYPE_STATE,
    TYPE_ZOOM,
)

from ._helpers import FakeViewport, assert_vec, define_camera, new_stage

FOCUS_EVENT = "morph.viewport.focus_changed"
FAKE_NAME = "morph-camera-test-viewport"


async def _frames(n=3):
    for _ in range(n):
        await omni.kit.app.get_app().next_update_async()


class TestExtensionStartup(omni.kit.test.AsyncTestCase):
    async def test_singleton_after_startup(self):
        ctrl = get_camera_controller()
        self.assertIsInstance(ctrl, CameraController)
        ctrl.get_state()  # 뷰포트가 없어도 예외 없음 (None 또는 상태)

    async def test_web_type_names(self):
        # stream 1.x 규칙: <owner ext-id>.<name>
        self.assertEqual(
            (
                TYPE_ORBIT,
                TYPE_PAN,
                TYPE_ZOOM,
                TYPE_APPLY_PRESET,
                TYPE_SAVE_PRESET,
                TYPE_LIST_PRESETS,
                TYPE_DELETE_PRESET,
                TYPE_STATE,
            ),
            (
                "morph.camera.control.orbit",
                "morph.camera.control.pan",
                "morph.camera.control.zoom",
                "morph.camera.control.apply_preset",
                "morph.camera.control.save_preset",
                "morph.camera.control.list_presets",
                "morph.camera.control.delete_preset",
                "morph.camera.control.state",
            ),
        )

    async def test_inbound_types_registered(self):
        msg = get_messaging()
        self.assertIsNotNone(msg)
        for type_ in (
            TYPE_ORBIT,
            TYPE_PAN,
            TYPE_ZOOM,
            TYPE_APPLY_PRESET,
            TYPE_SAVE_PRESET,
            TYPE_LIST_PRESETS,
            TYPE_DELETE_PRESET,
        ):
            try:
                sub = msg.register_inbound(type_, lambda p, c: None, owner="morph.camera.control")
            except ValueError:
                continue  # 이미 등록됨 = 정상
            sub.unsubscribe()
            self.fail(f"{type_} was not registered by on_startup")

    async def test_stage_open_event_reaches_controller(self):
        ctrl = get_camera_controller()
        calls = []
        ctrl._on_stage_opened = lambda event=None: calls.append(event)  # 인스턴스 속성으로 가로채기
        try:
            await new_stage("Y")
            await _frames(3)
        finally:
            del ctrl._on_stage_opened
        self.assertTrue(calls, "on_startup did not subscribe to stage OPENED/CLOSED events")

    async def test_state_outbound_declared(self):
        msg = get_messaging()
        # 미선언 타입이면 ValueError
        msg.send(TYPE_STATE, {"camera_path": "/X", "eye": [0.0, 0.0, 1.0], "target": [0.0, 0.0, 0.0], "fov": 60.0})


class TestExtensionEvents(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = await new_stage("Y")
        define_camera(self.stage, "/World/Cam", eye=(0.0, 0.0, 10.0), coi_distance=10.0)
        self.ctrl = get_camera_controller()
        self.saved_target = self.ctrl._target
        self.saved_resolver = self.ctrl._viewport_resolver
        self.fake_vp = FakeViewport("/World/Cam")
        self.ctrl._viewport_resolver = lambda name: self.fake_vp if name == FAKE_NAME else None
        self.received = []
        dispatcher = carb.eventdispatcher.get_eventdispatcher()
        self.subs = [
            dispatcher.observe_event(
                observer_name="morph.camera.control.test:active",
                event_name=EVENT_ACTIVE_CHANGED,
                on_event=lambda e: self.received.append((EVENT_ACTIVE_CHANGED, e["camera_path"], None)),
            ),
            dispatcher.observe_event(
                observer_name="morph.camera.control.test:changed",
                event_name=EVENT_CHANGED,
                on_event=lambda e: self.received.append(
                    (EVENT_CHANGED, e["camera_path"], [float(c) for c in e["eye"]])
                ),
            ),
        ]

    async def tearDown(self):
        self.subs = []
        self.ctrl._viewport_resolver = self.saved_resolver
        self.ctrl._cancel_pending()
        self.ctrl._target = self.saved_target
        self.ctrl._refresh_state()

    def _dispatch_focus(self, name, camera_path="/World/Cam"):
        carb.eventdispatcher.get_eventdispatcher().dispatch_event(
            FOCUS_EVENT, payload={"view_id": 1, "viewport_name": name, "camera_path": camera_path}
        )

    async def test_focus_event_with_empty_name_is_ignored(self):
        before = self.ctrl._target
        self._dispatch_focus("")
        await _frames(1)
        self.assertIs(self.ctrl._target, before)

    async def test_focus_event_switches_target_and_update_loop_moves_camera(self):
        self._dispatch_focus(FAKE_NAME)
        await _frames(1)
        self.assertEqual(self.ctrl.viewport_name, FAKE_NAME)
        self.assertEqual(self.ctrl.get_state().camera_path, "/World/Cam")
        self.assertIn((EVENT_ACTIVE_CHANGED, "/World/Cam", None), self.received)

        self.ctrl.orbit(90.0, 0.0)
        await _frames(3)
        assert_vec(self, usd_camera.read_camera_pose(self.stage, "/World/Cam")[0], (10.0, 0.0, 0.0))
        changed = [r for r in self.received if r[0] == EVENT_CHANGED]
        self.assertTrue(changed)
        assert_vec(self, changed[-1][2], (10.0, 0.0, 0.0))
