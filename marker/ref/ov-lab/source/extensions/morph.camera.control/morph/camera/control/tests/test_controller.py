"""CameraController 테스트: 병합, 보간, 프리셋, 포커스 전환, 상태 송신. 뷰포트 창 없음 (FakeViewport)."""
import json
import math

import omni.kit.test
from pxr import Gf, Sdf, UsdGeom

from morph.camera.control import camera_math as cm

from morph.camera.control import EVENT_ACTIVE_CHANGED, EVENT_CHANGED, CameraController, CameraState
from morph.camera.control import usd_camera
from morph.camera.control.controller import SETTING_ORBIT_SPEED, SETTING_STATE_RATE_HZ

from ._helpers import (
    BrokenViewport,
    DyingViewport,
    EventRecorder,
    FakeClock,
    FakeViewport,
    SendRecorder,
    assert_finite_list,
    assert_vec,
    define_camera,
    new_stage,
)

ORIGIN = (0.0, 0.0, 0.0)
H = 10.0 * math.sqrt(0.5)  # (0,0,10)→(10,0,0) 보간 중간점 (target 기준 구면 보간)
MID = (H, 0.0, H)


class TestController(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = await new_stage("Y")
        define_camera(self.stage, "/World/Cam", eye=(0.0, 0.0, 10.0), coi_distance=10.0)
        define_camera(self.stage, "/World/Cam2", eye=(0.0, 5.0, 20.0), coi_distance=20.0)
        self.vp = FakeViewport("/World/Cam")
        self.vp2 = FakeViewport("/World/Cam2")
        self.vp3 = FakeViewport("/World/Cam")  # 같은 카메라를 보는 다른 뷰
        self.viewports = {"Viewport": self.vp, "Viewport 2": self.vp2, "Viewport 3": self.vp3}
        self.settings = {}
        self.clock = FakeClock(0.0)
        self.events = EventRecorder()
        self.send = SendRecorder(connected=True)
        self.ctrl = CameraController(
            dispatch=self.events,
            send_state=self.send,
            viewport_resolver=self.viewports.get,
            settings_getter=self.settings.get,
            clock=self.clock,
        )
        self._extra_ctrls = []
        self.ctrl._set_target_viewport(self.vp, "Viewport")
        self.initial_events = list(self.events.events)
        self.ctrl._update()
        self.events.clear()
        self.send.sent.clear()

    async def tearDown(self):
        self.ctrl._shutdown()
        for ctrl in self._extra_ctrls:
            ctrl._shutdown()

    def _read(self, path="/World/Cam"):
        return usd_camera.read_camera_pose(self.stage, path)

    def _step(self, dt=0.0):
        self.clock.t += dt
        self.ctrl._update()

    # ---------------------------------------------------------------- 초기 상태

    async def test_initial_target_and_state(self):
        self.assertEqual(
            self.initial_events,
            [(EVENT_ACTIVE_CHANGED, {"camera_path": "/World/Cam", "viewport_name": "Viewport"})],
        )
        state = self.ctrl.get_state()
        self.assertIsInstance(state, CameraState)
        self.assertEqual(state.camera_path, "/World/Cam")
        assert_vec(self, state.eye, (0.0, 0.0, 10.0))
        assert_vec(self, state.target, ORIGIN)
        self.assertEqual(self.ctrl.viewport_name, "Viewport")

    async def test_no_commands_no_events(self):
        self._step(0.1)
        self._step(0.1)
        self.assertEqual(self.events.events, [])

    # ---------------------------------------------------------------- 병합

    async def test_orbit_merged_once_per_frame(self):
        self.ctrl.orbit(45.0, 0.0)
        self.ctrl.orbit(45.0, 0.0)
        self.assertEqual(self.events.events, [])  # 큐잉만
        self._step()
        changed = self.events.named(EVENT_CHANGED)
        self.assertEqual(len(changed), 1)
        assert_vec(self, changed[0]["eye"], (10.0, 0.0, 0.0))
        assert_vec(self, self._read()[0], (10.0, 0.0, 0.0))
        assert_vec(self, self.ctrl.get_state().eye, (10.0, 0.0, 0.0))

    async def test_orbit_speed_setting(self):
        self.settings[SETTING_ORBIT_SPEED] = 2.0
        self.ctrl.orbit(45.0, 0.0)
        self._step()
        assert_vec(self, self._read()[0], (10.0, 0.0, 0.0))

    async def test_pan_then_zoom_merged(self):
        self.ctrl.pan(1.0, 0.0)
        self.ctrl.zoom(0.5)
        self.ctrl.pan(1.0, 0.0)
        self.ctrl.zoom(0.5)
        self._step()
        eye, target, _ = self._read()
        assert_vec(self, target, (2.0, 0.0, 0.0))
        assert_vec(self, eye, (2.0, 0.0, 2.5))
        self.assertEqual(len(self.events.named(EVENT_CHANGED)), 1)

    async def test_external_change_is_reported(self):
        usd_camera.write_camera_pose(self.stage, "/World/Cam", (0.0, 3.0, 10.0), ORIGIN, (0.0, 1.0, 0.0))
        self._step()
        changed = self.events.named(EVENT_CHANGED)
        self.assertEqual(len(changed), 1)
        assert_vec(self, changed[0]["eye"], (0.0, 3.0, 10.0))

    # ---------------------------------------------------------------- 절대 명령 / 보간

    async def test_look_at_immediate(self):
        self.ctrl.look_at((5.0, 5.0, 5.0), (1.0, 1.0, 1.0))
        self._step()
        eye, target, _ = self._read()
        assert_vec(self, eye, (5.0, 5.0, 5.0))
        assert_vec(self, target, (1.0, 1.0, 1.0))

    async def test_look_at_interpolated(self):
        self.ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
        self._step()
        assert_vec(self, self._read()[0], (0.0, 0.0, 10.0))
        self._step(0.5)
        assert_vec(self, self._read()[0], MID)
        self._step(0.5)
        assert_vec(self, self._read()[0], (10.0, 0.0, 0.0))
        n = len(self.events.named(EVENT_CHANGED))
        self._step(0.5)
        self.assertEqual(len(self.events.named(EVENT_CHANGED)), n)  # 보간 종료 후 변화 없음
        self.assertEqual(n, 2)  # 프레임당 최대 1회 (첫 프레임은 시작 포즈라 변화 없음)

    async def test_relative_input_cancels_interpolation(self):
        self.ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
        self._step()
        self._step(0.5)
        self.ctrl.pan(0.0, 0.0)
        self._step(0.0)
        self._step(0.5)
        assert_vec(self, self._read()[0], MID)

    async def test_absolute_drops_earlier_relative_but_not_later(self):
        self.ctrl.pan(5.0, 0.0)
        self.ctrl.look_at((0.0, 0.0, 20.0), ORIGIN)
        self._step()
        assert_vec(self, self._read()[0], (0.0, 0.0, 20.0))
        self.ctrl.look_at((0.0, 0.0, 20.0), ORIGIN)
        self.ctrl.pan(1.0, 0.0)
        self._step()
        assert_vec(self, self._read()[0], (1.0, 0.0, 20.0))

    async def test_invalid_inputs(self):
        nan, inf = float("nan"), float("inf")
        with self.assertRaises(ValueError):
            self.ctrl.orbit(nan, 0.0)
        with self.assertRaises(ValueError):
            self.ctrl.pan(0.0, inf)
        with self.assertRaises(ValueError):
            self.ctrl.zoom(0.0)
        with self.assertRaises(ValueError):
            self.ctrl.zoom(-1.0)
        with self.assertRaises(ValueError):
            self.ctrl.orbit("a", 0.0)
        with self.assertRaises(ValueError):
            self.ctrl.look_at((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
        with self.assertRaises(ValueError):
            self.ctrl.look_at((1.0, 1.0), ORIGIN)
        with self.assertRaises(ValueError):
            self.ctrl.look_at((1.0, 1.0, 1.0), ORIGIN, duration_s=nan)
        self._step()
        self.assertEqual(self.events.events, [])  # 거부된 명령은 큐에 들어가지 않음

    # ---------------------------------------------------------------- 프리셋

    async def test_preset_save_and_apply(self):
        self.ctrl.save_preset("home")
        self.ctrl.orbit(90.0, 0.0)
        self._step()
        self.ctrl.apply_preset("home", duration_s=0.0)
        self._step()
        assert_vec(self, self._read()[0], (0.0, 0.0, 10.0))

    async def test_apply_unknown_preset_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.ctrl.apply_preset("nope")

    async def test_save_preset_validation(self):
        with self.assertRaises(ValueError):
            self.ctrl.save_preset("")

    async def test_list_presets_sorted_by_code_point(self):
        self.assertEqual(self.ctrl.list_presets(), [])
        for name in ("b", "A", "a", "home"):
            self.ctrl.save_preset(name)
        self.ctrl.save_preset("a")  # 덮어써도 하나
        self.assertEqual(self.ctrl.list_presets(), ["A", "a", "b", "home"])
        names = self.ctrl.list_presets()
        names.append("x")  # 복사본
        self.assertEqual(self.ctrl.list_presets(), ["A", "a", "b", "home"])

    async def test_get_preset_returns_copy_or_none(self):
        self.assertIsNone(self.ctrl.get_preset("home"))
        self.assertIsNone(self.ctrl.get_preset(None))
        self.ctrl.save_preset("home")
        preset = self.ctrl.get_preset("home")
        self.assertEqual(set(preset), {"name", "eye", "target", "fov"})
        self.assertEqual(preset["name"], "home")
        assert_vec(self, preset["eye"], (0.0, 0.0, 10.0))
        assert_vec(self, preset["target"], ORIGIN)
        self.assertIsInstance(preset["eye"], list)
        self.assertIsInstance(preset["fov"], float)
        self.assertAlmostEqual(preset["fov"], self.ctrl.get_state().fov)
        preset["eye"][0] = 99.0  # 돌려준 dict를 바꿔도 저장본은 그대로
        assert_vec(self, self.ctrl.get_preset("home")["eye"], (0.0, 0.0, 10.0))

    async def test_delete_preset(self):
        self.ctrl.save_preset("home")
        self.ctrl.save_preset("top")
        self.ctrl.delete_preset("home")
        self.assertEqual(self.ctrl.list_presets(), ["top"])
        self.assertIsNone(self.ctrl.get_preset("home"))
        with self.assertRaises(KeyError):
            self.ctrl.apply_preset("home")
        with self.assertRaises(KeyError):
            self.ctrl.delete_preset("home")
        with self.assertRaises(KeyError):
            self.ctrl.delete_preset(None)

    async def test_delete_preset_after_apply_keeps_queued_command(self):
        self.ctrl.save_preset("home")
        self.ctrl.orbit(90.0, 0.0)
        self._step()
        self.ctrl.apply_preset("home")
        self.ctrl.delete_preset("home")  # 이미 큐에 들어간 apply_preset은 그대로 반영
        self._step()
        assert_vec(self, self._read()[0], (0.0, 0.0, 10.0))

    async def test_preset_management_works_without_target(self):
        self.ctrl.save_preset("home")
        self.ctrl._set_target_viewport(None)
        self.assertEqual(self.ctrl.list_presets(), ["home"])
        self.assertIsNotNone(self.ctrl.get_preset("home"))
        self.ctrl.delete_preset("home")
        self.assertEqual(self.ctrl.list_presets(), [])

    async def test_presets_are_global_and_apply_to_focus_view(self):
        self.ctrl.save_preset("home")  # Cam 기준
        self.ctrl._on_focus_changed({"view_id": 1, "viewport_name": "Viewport 2", "camera_path": "/World/Cam2"})
        self.ctrl.apply_preset("home")
        self._step()
        eye2, target2, _ = self._read("/World/Cam2")
        assert_vec(self, eye2, (0.0, 0.0, 10.0))
        assert_vec(self, target2, ORIGIN)
        assert_vec(self, self._read("/World/Cam")[0], (0.0, 0.0, 10.0))  # 이전 대상은 그대로

    # ---------------------------------------------------------------- 포커스 전환

    async def test_focus_changed_switches_target_and_cancels_pending(self):
        self.ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
        self._step()
        self._step(0.5)
        self.ctrl.orbit(90.0, 0.0)  # 큐에 남은 상대 명령
        self.events.clear()

        self.ctrl._on_focus_changed({"view_id": 1, "viewport_name": "Viewport 2", "camera_path": "/World/Cam2"})
        self.assertEqual(
            self.events.named(EVENT_ACTIVE_CHANGED), [{"camera_path": "/World/Cam2", "viewport_name": "Viewport 2"}]
        )
        self.assertEqual(self.ctrl.get_state().camera_path, "/World/Cam2")
        self.assertEqual(self.ctrl.viewport_name, "Viewport 2")

        self._step(0.5)
        assert_vec(self, self._read("/World/Cam")[0], MID)  # 보간 중단
        assert_vec(self, self._read("/World/Cam2")[0], (0.0, 5.0, 20.0))  # 큐 취소
        changed = self.events.named(EVENT_CHANGED)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["camera_path"], "/World/Cam2")

    async def test_focus_changed_ignored_cases(self):
        for payload in (
            {"view_id": 1, "viewport_name": "", "camera_path": "/World/Cam2"},
            {"view_id": 1, "viewport_name": "Unknown", "camera_path": "/World/Cam2"},
            {"view_id": 0, "viewport_name": "Viewport", "camera_path": "/World/Cam"},
            {},
        ):
            self.ctrl._on_focus_changed(payload)
        self.assertEqual(self.events.events, [])
        self.assertEqual(self.ctrl.viewport_name, "Viewport")
        self.assertEqual(self.ctrl.get_state().camera_path, "/World/Cam")

    async def test_focus_to_view_with_same_camera_no_active_changed(self):
        self.ctrl._on_focus_changed({"view_id": 2, "viewport_name": "Viewport 3", "camera_path": "/World/Cam"})
        self.assertEqual(self.ctrl.viewport_name, "Viewport 3")
        self.assertEqual(self.events.named(EVENT_ACTIVE_CHANGED), [])

    # ---------------------------------------------------------------- set_active_camera

    async def test_set_active_camera(self):
        self.ctrl.set_active_camera("/World/Cam2")
        self.assertEqual(self.vp.camera_path, Sdf.Path("/World/Cam2"))
        self.assertEqual(
            self.events.named(EVENT_ACTIVE_CHANGED), [{"camera_path": "/World/Cam2", "viewport_name": "Viewport"}]
        )
        self.assertEqual(self.ctrl.get_state().camera_path, "/World/Cam2")
        self._step()
        self.assertEqual(len(self.events.named(EVENT_ACTIVE_CHANGED)), 1)
        self.assertEqual(self.events.named(EVENT_CHANGED)[-1]["camera_path"], "/World/Cam2")

    async def test_set_active_camera_errors(self):
        UsdGeom.Xform.Define(self.stage, "/World/NotCam")
        for bad in ("/World/NotCam", "/Nope", ""):
            with self.assertRaises(ValueError):
                self.ctrl.set_active_camera(bad)
        self.ctrl._set_target_viewport(None)
        with self.assertRaises(RuntimeError):
            self.ctrl.set_active_camera("/World/Cam2")

    async def test_external_camera_path_change_emits_active_changed(self):
        self.vp.camera_path = Sdf.Path("/World/Cam2")
        self._step()
        self.assertEqual(
            self.events.named(EVENT_ACTIVE_CHANGED), [{"camera_path": "/World/Cam2", "viewport_name": "Viewport"}]
        )

    def _ctrl_with_active(self, active):
        """active_viewport_getter가 active["vp"], active["name"]을 돌려주는 컨트롤러."""
        ctrl = CameraController(
            dispatch=self.events,
            viewport_resolver=self.viewports.get,
            active_viewport_getter=lambda: (active.get("vp"), active.get("name", "")),
            clock=self.clock,
        )
        self._extra_ctrls.append(ctrl)
        return ctrl

    async def test_set_active_camera_follows_current_kit_active_viewport(self):
        # split의 focus_view는 Kit 활성 뷰포트를 즉시 바꾸고 focus_changed는 다음 프레임에 온다.
        define_camera(self.stage, "/World/Cam3", eye=(0.0, 0.0, 30.0), coi_distance=30.0)
        active = {"vp": self.vp, "name": "Viewport"}
        ctrl = self._ctrl_with_active(active)
        ctrl._set_target_viewport(self.vp, "Viewport")
        active.update(vp=self.vp2, name="Viewport 2")  # focus_changed 도착 전
        self.events.clear()
        ctrl.set_active_camera("/World/Cam3")
        # 대상 전환(Cam2를 보는 뷰)과 카메라 변경이 한 호출에 일어나도 active_changed는 최종 경로로 1회
        self.assertEqual(
            self.events.named(EVENT_ACTIVE_CHANGED), [{"camera_path": "/World/Cam3", "viewport_name": "Viewport 2"}]
        )
        self.assertEqual(self.vp2.camera_path, Sdf.Path("/World/Cam3"))
        self.assertEqual(self.vp.camera_path, Sdf.Path("/World/Cam"))  # 이전 뷰는 그대로
        self.assertEqual(ctrl.viewport_name, "Viewport 2")
        self.assertEqual(ctrl.get_state().camera_path, "/World/Cam3")
        # 뒤늦게 온 focus_changed는 이미 같은 대상이라 무시된다
        self.events.clear()
        ctrl._on_focus_changed({"view_id": 1, "viewport_name": "Viewport 2", "camera_path": "/World/Cam3"})
        self.assertEqual(self.events.named(EVENT_ACTIVE_CHANGED), [])

    async def test_set_active_camera_failure_after_target_switch_keeps_new_target(self):
        UsdGeom.Xform.Define(self.stage, "/World/NotCam")
        active = {"vp": self.vp, "name": "Viewport"}
        ctrl = self._ctrl_with_active(active)
        ctrl._set_target_viewport(self.vp, "Viewport")
        active.update(vp=self.vp2, name="Viewport 2")
        self.events.clear()
        with self.assertRaises(ValueError):
            ctrl.set_active_camera("/World/NotCam")
        self.assertEqual(ctrl.viewport_name, "Viewport 2")
        self.assertEqual(self.vp2.camera_path, Sdf.Path("/World/Cam2"))  # 카메라 연결은 그대로
        self.assertEqual(
            self.events.named(EVENT_ACTIVE_CHANGED), [{"camera_path": "/World/Cam2", "viewport_name": "Viewport 2"}]
        )

    async def test_set_active_camera_same_path_no_event_keeps_pending(self):
        self.ctrl.orbit(90.0, 0.0)
        self.ctrl.set_active_camera("/World/Cam")
        self.assertEqual(self.events.named(EVENT_ACTIVE_CHANGED), [])
        self._step()
        assert_vec(self, self._read()[0], (10.0, 0.0, 0.0))  # 대기 명령은 취소되지 않음

    async def test_public_surface_matches_contract(self):
        public = {n for n in dir(CameraController) if not n.startswith("_")}
        self.assertEqual(
            public,
            {
                "orbit",
                "pan",
                "zoom",
                "look_at",
                "save_preset",
                "apply_preset",
                "delete_preset",
                "list_presets",
                "get_preset",
                "set_active_camera",
                "get_state",
                "viewport_name",
            },
        )

    async def test_set_active_camera_probes_when_no_target(self):
        ctrl = self._ctrl_with_active({"vp": self.vp, "name": "Viewport"})
        self.assertIsNone(ctrl.get_state())
        ctrl.set_active_camera("/World/Cam2")
        self.assertEqual(self.vp.camera_path, Sdf.Path("/World/Cam2"))
        self.assertEqual(ctrl.get_state().camera_path, "/World/Cam2")

    async def test_look_at_with_duration_then_relative_in_same_frame(self):
        self.ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
        self.ctrl.pan(0.0, 2.0)  # 같은 프레임: 보간 목표에 적용
        self._step()
        self._step(1.0)
        eye, target, _ = self._read()
        assert_vec(self, eye, (10.0, 2.0, 0.0))
        assert_vec(self, target, (0.0, 2.0, 0.0))

    async def test_stage_opened_cancels_interpolation_and_queue(self):
        self.ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
        self._step()
        self._step(0.5)
        self.ctrl.orbit(90.0, 0.0)
        self.ctrl._on_stage_opened()
        self._step(0.5)
        assert_vec(self, self._read()[0], MID)

    async def test_scaled_parent_orbit_keeps_distance(self):
        for s in (0.01, 2.0):
            rig = UsdGeom.Xform.Define(self.stage, f"/World/Rig_{int(s * 100)}")
            UsdGeom.Xformable(rig.GetPrim()).AddScaleOp().Set(Gf.Vec3f(s, s, s))
            path = define_camera(self.stage, f"/World/Rig_{int(s * 100)}/Cam", eye=(0.0, 0.0, 10.0), coi_distance=10.0)
            self.ctrl._set_target_viewport(FakeViewport(path), f"Rig {s}")
            state = self.ctrl.get_state()
            d0 = cm.distance(state.eye, state.target)
            self.assertAlmostEqual(d0, 10.0 * s, places=6)
            for _ in range(10):
                self.ctrl.orbit(10.0, 3.0)
                self._step(0.016)
            eye, target, _ = self._read(path)
            self.assertAlmostEqual(cm.distance(eye, target), d0, places=4)

    # ---------------------------------------------------------------- 대상 없음

    async def test_no_target(self):
        self.ctrl._set_target_viewport(None)
        self.assertIsNone(self.ctrl.get_state())
        self.assertEqual(self.events.named(EVENT_ACTIVE_CHANGED), [{"camera_path": "", "viewport_name": ""}])
        self.events.clear()
        self.ctrl.orbit(10.0, 0.0)
        self.ctrl.look_at((1.0, 2.0, 3.0), ORIGIN)
        self._step()
        self.assertEqual(self.events.events, [])
        with self.assertRaises(RuntimeError):
            self.ctrl.save_preset("x")
        self.ctrl._on_client_connected()
        self.assertEqual(self.send.sent, [])

    async def test_broken_viewport_dropped(self):
        self.ctrl._set_target_viewport(BrokenViewport(), "Gone")
        self.assertIsNone(self.ctrl.get_state())
        self._step()
        self.assertIsNone(self.ctrl.get_state())

    async def test_probe_finds_viewport_later(self):
        found = {"vp": None}
        ctrl = CameraController(
            dispatch=self.events, active_viewport_getter=lambda: (found["vp"], "Viewport"), clock=self.clock
        )
        ctrl._update()
        self.assertIsNone(ctrl.get_state())
        found["vp"] = self.vp
        self._step_other(ctrl, 0.1)
        self.assertIsNone(ctrl.get_state())  # 재탐색 주기 전
        self._step_other(ctrl, 0.5)
        self.assertEqual(ctrl.get_state().camera_path, "/World/Cam")
        ctrl._shutdown()

    def _step_other(self, ctrl, dt):
        self.clock.t += dt
        ctrl._update()

    # ---------------------------------------------------------------- 웹 상태 송신

    async def test_state_send_rate_limited(self):
        self.settings[SETTING_STATE_RATE_HZ] = 10.0
        self.clock.t = 100.0
        self.ctrl.orbit(10.0, 0.0)
        self._step()
        self.assertEqual(len(self.send.sent), 1)
        self.ctrl.orbit(10.0, 0.0)
        self._step(0.05)
        self.assertEqual(len(self.send.sent), 1)  # 제한에 걸림
        self._step(0.06)
        self.assertEqual(len(self.send.sent), 2)  # 밀린 최신 상태가 나감
        payload, force = self.send.sent[-1]
        self.assertFalse(force)
        assert_vec(self, payload["eye"], self.ctrl.get_state().eye)
        self._step(0.2)
        self.assertEqual(len(self.send.sent), 2)  # 변화 없으면 송신 없음

    async def test_state_not_sent_when_disconnected(self):
        self.send.connected = False
        self.ctrl.orbit(10.0, 0.0)
        self._step(1.0)
        self.assertEqual(self.send.sent, [])

    async def test_client_connected_sends_once(self):
        self.ctrl._on_client_connected({"client_id": "web-1"})
        self.assertEqual(len(self.send.sent), 1)
        payload, force = self.send.sent[0]
        self.assertTrue(force)
        self.assertEqual(payload["camera_path"], "/World/Cam")

    async def test_payload_is_json_serializable(self):
        payload = self.ctrl.get_state().to_payload()
        json.dumps(payload, allow_nan=False)
        self.assertEqual(set(payload), {"camera_path", "eye", "target", "fov"})
        assert_finite_list(self, payload["eye"])
        assert_finite_list(self, payload["target"])
        self.assertIsInstance(payload["fov"], float)


class TestDestroyedViewport(omni.kit.test.AsyncTestCaseFailOnLogError):
    """레이아웃 축소로 대상 창이 destroy된 뒤 focus_changed가 오기 전 몇 프레임: 예외·에러 로그 없음."""

    async def setUp(self):
        self.stage = await new_stage("Y")
        define_camera(self.stage, "/World/Cam", eye=(0.0, 0.0, 10.0), coi_distance=10.0)
        define_camera(self.stage, "/World/Cam2", eye=(0.0, 5.0, 20.0), coi_distance=20.0)
        self.view0 = FakeViewport("/World/Cam2")
        self.clock = FakeClock(0.0)
        self.events = EventRecorder()

    async def _run(self, mode):
        dying = DyingViewport("/World/Cam", mode)
        ctrl = CameraController(
            dispatch=self.events,
            viewport_resolver={"Viewport": self.view0}.get,
            clock=self.clock,
        )
        try:
            ctrl._set_target_viewport(dying, "Viewport 2")
            ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
            ctrl._update()
            dying.kill()
            for _ in range(3):  # focus_changed 도착 전 프레임들
                ctrl.orbit(5.0, 0.0)
                ctrl.zoom(0.9)
                self.clock.t += 0.1
                ctrl._update()
                ctrl.get_state()
            with self.assertRaises((RuntimeError, ValueError)):
                ctrl.set_active_camera("/World/Cam2")  # 죽은 대상: 명시적 예외 (에러 로그 아님)
            ctrl._on_focus_changed({"view_id": 0, "viewport_name": "Viewport", "camera_path": "/World/Cam2"})
            self.assertEqual(ctrl.get_state().camera_path, "/World/Cam2")
            ctrl.orbit(90.0, 0.0)
            self.clock.t += 0.1
            ctrl._update()
            eye, _, _ = usd_camera.read_camera_pose(self.stage, "/World/Cam2")
            self.assertAlmostEqual(cm.distance(eye, (0.0, 0.0, 0.0)), cm.length((0.0, 5.0, 20.0)), places=4)
            assert_vec(self, eye, (20.0, 5.0, 0.0), places=3)  # Cam2가 90도 오빗됨
        finally:
            ctrl._shutdown()
        return dying

    async def _run_with_active_fallback(self, mode, active_vp, active_name):
        """대상 창 무효화 시 Kit 활성 뷰포트로 곧바로 전환되는지. 반환: active_changed 페이로드 목록."""
        dying = DyingViewport("/World/Cam", mode)
        active = {"vp": active_vp, "name": active_name}
        ctrl = CameraController(
            dispatch=self.events,
            viewport_resolver={"Viewport": self.view0}.get,
            active_viewport_getter=lambda: (active["vp"] if active["vp"] != "dying" else dying, active["name"]),
            clock=self.clock,
        )
        try:
            ctrl._set_target_viewport(dying, "Viewport 4")
            ctrl.look_at((10.0, 0.0, 0.0), ORIGIN, duration_s=1.0)
            ctrl._update()
            self.events.clear()
            dying.kill()
            ctrl.orbit(45.0, 0.0)
            self.clock.t += 0.1
            ctrl._update()
            ctrl._update()
            return ctrl, self.events.named(EVENT_ACTIVE_CHANGED)
        finally:
            ctrl._shutdown()

    async def test_destroyed_viewport_falls_back_to_active_without_empty_event(self):
        for mode in ("raise", "empty"):
            self.events.clear()
            ctrl, active_events = await self._run_with_active_fallback(mode, self.view0, "Viewport")
            self.assertEqual(active_events, [{"camera_path": "/World/Cam2", "viewport_name": "Viewport"}], mode)
            self.assertNotIn({"camera_path": "", "viewport_name": ""}, active_events)
            # 전환과 함께 보간·대기 명령 취소: view 0 카메라는 그대로
            assert_vec(self, usd_camera.read_camera_pose(self.stage, "/World/Cam2")[0], (0.0, 5.0, 20.0))

    async def test_destroyed_viewport_fallback_resumes_control(self):
        dying = DyingViewport("/World/Cam", "raise")
        ctrl = CameraController(
            dispatch=self.events, active_viewport_getter=lambda: (self.view0, "Viewport"), clock=self.clock
        )
        try:
            ctrl._set_target_viewport(dying, "Viewport 4")
            dying.kill()
            ctrl._update()
            self.assertEqual(ctrl.viewport_name, "Viewport")
            self.assertEqual(ctrl.get_state().camera_path, "/World/Cam2")
            ctrl.orbit(90.0, 0.0)
            ctrl._update()
            assert_vec(self, usd_camera.read_camera_pose(self.stage, "/World/Cam2")[0], (20.0, 5.0, 0.0), places=3)
        finally:
            ctrl._shutdown()

    async def test_destroyed_viewport_without_valid_active_reports_no_target(self):
        # 활성 뷰포트도 같은 (죽은) 뷰포트이거나 없으면 그때만 ""를 낸다
        for active_vp in ("dying", None):
            self.events.clear()
            _, active_events = await self._run_with_active_fallback("raise", active_vp, "Viewport 4")
            self.assertEqual(active_events, [{"camera_path": "", "viewport_name": ""}], str(active_vp))

    async def test_destroyed_viewport_raising(self):
        await self._run("raise")

    async def test_destroyed_viewport_empty_path(self):
        await self._run("empty")

    async def test_destroyed_viewport_stale_path(self):
        await self._run("stale")
