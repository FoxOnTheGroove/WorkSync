import math

from pxr import Usd, UsdGeom, Sdf, Gf

__all__ = ["EbsSimulateCamera", "CAMERA_PATH", "CAMERA_BACK",
           "CAMERA_NEAR", "CAMERA_FAR"]

CAMERA_PATH = "/EbsCamera"     # 세션 레이어에 우리가 만드는 카메라
CAMERA_BACK = 30.0             # interest 에서 정면으로 물러나는 거리, 스테이지 단위
CAMERA_NEAR = 0.01             # 렌즈에 붙여 둔다. 앞을 자르지 않는다
CAMERA_FAR  = 1.0e6

FOCAL      = 50.0
APERTURE_H = 20.955
APERTURE_V = 15.2908

ORBIT_FRAME = "ebs_orbit_input"   # 뷰포트를 덮어 입력을 가로채는 프레임
YAW_PER_PIXEL   = 0.35   # 1 픽셀 끌 때 도는 각도, 도
PITCH_PER_PIXEL = 0.35
AXIS_LOCK  = 5      # 이만큼 끌기 전에는 축을 안 정한다. 요와 피치는 안 섞인다

ZOOM_PER_NOTCH = 0.88   # 휠 한 칸에 반지름이 이만큼이 된다. 위로 굴리면 가까이
ZOOM_NEAREST   = 0.15   # 기본 거리(CAMERA_BACK) 대비 가장 가까이
ZOOM_FURTHEST  = 8.0    # 그리고 가장 멀리

# Kit 의 카메라 조작 바인딩. 비우면 Kit 이 카메라를 안 움직인다
CAMERA_BINDINGS = "/exts/omni.kit.viewport.window/bindings/camera"
DEFAULT_BINDINGS = {
    "PanGesture": "Any MiddleButton", "TumbleGesture": "Alt LeftButton",
    "ZoomGesture": "Alt RightButton", "LookGesture": "RightButton",
    "ZoomScrollGesture": "Any", "FlightSpeedGesture": "RightButton",
    "FlightMode": "RightButton",
}
PITCH_LIMIT = 85.0  # 수평에서 위아래로 여기까지. 극을 넘으면 롤이 생긴다

LEFT_BUTTON, RIGHT_BUTTON, MIDDLE_BUTTON = 0, 1, 2


def viewport_window(name: str = None):
    try:
        import omni.kit.viewport.utility as vp_util
    except Exception as e:
        print(f"[ebs] viewport utility unavailable: {e}")
        return None

    tried = []

    def ask(helper_name, call):
        helper = getattr(vp_util, helper_name, None)
        if helper is None:
            tried.append(f"{helper_name}: not in this build")
            return None
        try:
            got = call(helper)
        except Exception as e:
            tried.append(f"{helper_name}: {e}")
            return None
        if got is None:
            tried.append(f"{helper_name}: gave nothing back")
        return got

    window = None
    if name:
        window = ask("get_viewport_window_by_name", lambda f: f(name))
    if window is None:
        pair = ask("get_active_viewport_and_window", lambda f: f())
        window = pair[1] if pair else None
    if window is None:
        window = ask("get_active_viewport_window", lambda f: f())
    if window is None:
        try:
            import omni.ui as ui
            window = ui.Workspace.get_window(name or "Viewport")
        except Exception as e:
            tried.append(f"Workspace.get_window: {e}")
    if window is None:
        print("[ebs] no viewport window -- " + "; ".join(tried))
    return window


class EbsSimulateCamera:

    def __init__(self):
        self._previous = None
        self._orbit = False
        self._interest = None
        self._frame_ui = None
        self._catch = None
        self._at = None
        self._selection = None
        self._no_pick = None
        self._no_menu = None
        self._bindings = None
        self._from = None
        self._axis = None
        self._home = None

    @property
    def previous(self):
        return self._previous

    @property
    def orbit(self) -> bool:
        return self._orbit

    @property
    def interest(self):
        return self._interest

    @staticmethod
    def viewport():
        try:
            from omni.kit.viewport.utility import get_active_viewport
            return get_active_viewport()
        except Exception as e:
            print(f"[ebs] viewport utility unavailable: {e}")
            return None

    def exists(self, stage) -> bool:
        if stage is None:
            return False
        prim = stage.GetPrimAtPath(CAMERA_PATH)
        return bool(prim.IsValid() and UsdGeom.Camera(prim))

    def make(self, stage) -> bool:
        if stage is None or self.exists(stage):
            return False
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera = UsdGeom.Camera.Define(stage, CAMERA_PATH)
            camera.CreateFocalLengthAttr(FOCAL)
            camera.CreateHorizontalApertureAttr(APERTURE_H)
            camera.CreateVerticalApertureAttr(APERTURE_V)
            camera.CreateClippingRangeAttr(Gf.Vec2f(0.1, 1.0e6))
            camera.GetPrim().CreateAttribute(
                "omni:kit:centerOfInterest",
                Sdf.ValueTypeNames.Vector3d).Set(Gf.Vec3d(0.0, 0.0, -100.0))
        return True

    def release(self, stage) -> None:
        self._drop()
        self._orbit = False
        self._interest = None
        if stage is None:
            return
        viewport = self.viewport()
        if viewport is not None and self._previous:
            try:
                viewport.camera_path = self._previous
            except Exception as e:
                print(f"[ebs] could not restore the viewport camera: {e}")
        self._previous = None

    def reset(self, stage) -> str:
        if stage is None or self._home is None:
            return ""
        cam_prim, camera = self._camera(stage)
        if camera is None:
            return ""
        x_cam, y_cam, z_cam, eye, distance, interest = self._home
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, distance)
        self._interest = interest
        self._from = self._axis = None
        return f"camera back to {distance:.2f} in front of the EBS"

    def remove(self, stage) -> None:
        self.release(stage)
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(CAMERA_PATH).IsValid():
                stage.RemovePrim(CAMERA_PATH)

    def place(self, stage, box, facing) -> str:
        viewport = self.viewport()
        if stage is None or viewport is None or box is None or facing is None:
            return ""
        cam_prim, camera = self._camera(stage)
        if camera is None:
            return ""
        self._take_viewport(viewport)

        x_cam, y_cam, z_cam = self._frame(stage, facing)
        low, high = box.GetMin(), box.GetMax()
        interest = Gf.Vec3d(*[(low[i] + high[i]) * 0.5 for i in range(3)])

        distance = CAMERA_BACK
        eye = interest + z_cam * distance
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, distance)
        self._interest = interest
        self._home = (x_cam, y_cam, z_cam, eye, distance, interest)
        self._orbit = True
        self._grab()
        return (f"camera {distance:.2f} back from the EBS centre, "
                f"orbiting ({interest[0]:.2f}, {interest[1]:.2f}, "
                f"{interest[2]:.2f}), near plane {CAMERA_NEAR:.2f}")

    def _camera(self, stage):
        if not self.exists(stage):
            self.make(stage)
        cam_prim = stage.GetPrimAtPath(CAMERA_PATH)
        camera = UsdGeom.Camera(cam_prim) if cam_prim.IsValid() else None
        if not camera:
            print("[ebs] could not create the camera")
            return cam_prim, None
        return cam_prim, camera

    def _take_viewport(self, viewport) -> None:
        if str(viewport.camera_path) == CAMERA_PATH:
            return
        self._previous = str(viewport.camera_path)
        try:
            viewport.camera_path = CAMERA_PATH
        except Exception as e:
            print(f"[ebs] could not switch the viewport camera: {e}")


    def _grab(self) -> bool:
        if self._catch is not None:
            return True
        window = viewport_window()
        if window is None:
            return False
        if not self._grab_sheet(window):
            return False
        self._silence(window)
        self._mute_selection()
        return True

    def _silence(self, window) -> None:
        say = lambda line: print(f"[ebs] input: {line}")
        try:
            from omni.kit.viewport.utility import disable_selection
            self._no_pick = disable_selection(window, disable_click=True)
            say("selection off")
        except Exception as e:
            self._no_pick = None
            say(f"selection NOT off: {type(e).__name__}: {e}")
        try:
            from omni.kit.viewport.utility import disable_context_menu
            self._no_menu = disable_context_menu(window)
            say("context menu off")
        except Exception as e:
            self._no_menu = None
            say(f"context menu NOT off: {type(e).__name__}: {e}")
        try:
            import carb.settings
            settings = carb.settings.get_settings()
            was = settings.get(CAMERA_BINDINGS)
            self._bindings = was if isinstance(was, dict) and was else dict(
                DEFAULT_BINDINGS)
            settings.set(CAMERA_BINDINGS, {})
            now = settings.get(CAMERA_BINDINGS)
            say(f"camera bindings {sorted(self._bindings)} -> {now}")
        except Exception as e:
            self._bindings = None
            say(f"camera bindings NOT cleared: {type(e).__name__}: {e}")

    def _restore(self) -> None:
        self._no_pick = None
        self._no_menu = None
        if self._bindings is not None:
            try:
                import carb.settings
                carb.settings.get_settings().set(CAMERA_BINDINGS, self._bindings)
            except Exception as e:
                print(f"[ebs] input: camera bindings NOT restored: {e}")
            self._bindings = None

    def _grab_sheet(self, window) -> bool:
        try:
            import omni.ui as ui
            self._frame_ui = window.get_frame(ORBIT_FRAME)
            with self._frame_ui:
                self._catch = ui.Rectangle(
                    width=ui.Percent(100), height=ui.Percent(100),
                    style={"background_color": 0x01000000})
            self._catch.set_mouse_pressed_fn(
                lambda x, y, button, mod: self._pressed(x, y, button))
            self._catch.set_mouse_moved_fn(
                lambda x, y, mod, held: self._moved(x, y))
            self._catch.set_mouse_released_fn(
                lambda x, y, button, mod: self._end_drag())
            self._catch.set_mouse_double_clicked_fn(
                lambda x, y, button, mod: self._double(x, y, button))
            self._catch.set_mouse_wheel_fn(
                lambda dx, dy, mod: self._wheel(dy))
        except Exception as e:
            print(f"[ebs] input: could not lay the sheet: {e}")
            self._drop()
            return False
        return True

    def _drop(self) -> None:
        self._from = self._axis = self._at = None
        self._selection = None
        self._restore()
        if self._frame_ui is not None:
            try:
                self._frame_ui.clear()
            except Exception:
                pass
        self._catch = None
        self._frame_ui = None

    def _mute_selection(self) -> None:
        try:
            import omni.usd
            self._selection = (omni.usd.get_context().get_stage_event_stream()
                               .create_subscription_to_pop(
                                   self._stage_event, name="ebs orbit"))
        except Exception as e:
            print(f"[ebs] prims can still be picked while orbiting: {e}")

    def _stage_event(self, event) -> None:
        if not self._orbit:
            return
        try:
            import omni.usd
            if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
                return
            picked = omni.usd.get_context().get_selection()
            if picked.get_selected_prim_paths():
                picked.clear_selected_prim_paths()
        except Exception:
            pass


    def _pressed(self, x, y, button) -> None:
        if button != LEFT_BUTTON:
            self._from = self._axis = None
            return
        self._start_drag()
        self._at = (x, y)

    def _moved(self, x, y) -> None:
        if self._from is None or self._at is None:
            return
        dx, dy = x - self._at[0], y - self._at[1]
        self._at = (x, y)
        self._drag(dx, dy)

    def _start_drag(self) -> None:
        self._from = (0.0, 0.0)
        self._axis = None

    def _end_drag(self) -> None:
        self._from = self._axis = self._at = None

    def _double(self, x: float, y: float, button: int = LEFT_BUTTON) -> None:
        if not self._orbit or button != LEFT_BUTTON:
            return
        ndc = self._ndc(x, y)
        viewport = self.viewport()
        if ndc is None or viewport is None:
            return
        try:
            pixel, inside = viewport.map_ndc_to_texture_pixel(ndc)
            if not inside:
                return
            viewport.request_query(pixel, self._picked)
        except Exception as e:
            print(f"[ebs] could not ask what is under the cursor: {e}")

    def _ndc(self, x: float, y: float):
        catch = self._catch
        if catch is None:
            return None
        try:
            width, height = catch.computed_width, catch.computed_height
            if width <= 0 or height <= 0:
                return None
            u = (x - catch.screen_position_x) / width
            v = (y - catch.screen_position_y) / height
        except Exception:
            return None
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None
        return (u * 2.0 - 1.0, 1.0 - v * 2.0)

    def _picked(self, path, position=None, *rest) -> None:
        if not path or position is None:
            return
        try:
            from .ebs_simulate import OURS, OURS_UNDER
        except Exception:
            OURS, OURS_UNDER = (CAMERA_PATH,), (CAMERA_PATH + "/",)
        path = str(path)
        if path in OURS or path.startswith(tuple(OURS_UNDER)):
            return
        self._look_at(Gf.Vec3d(position[0], position[1], position[2]))

    def _look_at(self, target) -> None:
        hold = self._hold()
        if hold is None:
            return
        self._interest = target
        self._settle(hold, hold[3], hold[4])

    def _wheel(self, notches: float = 0.0) -> None:
        if notches:
            self._zoom(notches)

    def _drag(self, dx: float, dy: float) -> None:
        if self._from is None or not self._orbit:
            return
        self._from = (self._from[0] + dx, self._from[1] + dy)
        if self._axis is None:
            total = self._from
            if max(abs(total[0]), abs(total[1])) < AXIS_LOCK:
                return
            self._axis = "yaw" if abs(total[0]) >= abs(total[1]) else "pitch"
        if self._axis == "yaw":
            self._turn(yaw=-dx * YAW_PER_PIXEL)
        else:
            self._turn(pitch=dy * PITCH_PER_PIXEL)


    def _hold(self):
        stage = self._stage()
        if stage is None or self._interest is None:
            return None
        cam_prim, camera = self._camera(stage)
        if camera is None:
            return None
        eye = self._eye(cam_prim)
        if eye is None:
            return None
        arm = eye - self._interest
        radius = arm.GetLength()
        if radius <= 1e-9:
            return None
        return stage, cam_prim, camera, arm, radius

    def _settle(self, hold, arm, radius: float) -> None:
        stage, cam_prim, camera = hold[0], hold[1], hold[2]
        up = self._up(stage)
        eye = self._interest + arm.GetNormalized() * radius
        z_cam = (eye - self._interest).GetNormalized()
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, radius)

    def _turn(self, yaw: float = 0.0, pitch: float = 0.0) -> None:
        hold = self._hold()
        if hold is None:
            return
        stage, arm, radius = hold[0], hold[3], hold[4]

        up = self._up(stage)
        if yaw:
            arm = Gf.Rotation(up, yaw).TransformDir(arm)
        if pitch:
            pitch = self._room(arm, up, pitch)
            if pitch:
                side = Gf.Cross(arm, up).GetNormalized()
                arm = Gf.Rotation(side, pitch).TransformDir(arm)
        self._settle(hold, arm, radius)

    def _zoom(self, notches: float) -> None:
        hold = self._hold()
        if hold is None:
            return
        arm, radius = hold[3], hold[4]
        want = radius * (ZOOM_PER_NOTCH ** notches)
        want = min(max(want, CAMERA_BACK * ZOOM_NEAREST),
                   CAMERA_BACK * ZOOM_FURTHEST)
        if abs(want - radius) < 1e-9:
            return
        self._settle(hold, arm, want)

    @staticmethod
    def _room(arm, up, pitch: float) -> float:
        height = Gf.Dot(arm.GetNormalized(), up)
        now = math.degrees(math.asin(max(-1.0, min(1.0, height))))
        return max(-PITCH_LIMIT, min(PITCH_LIMIT, now + pitch)) - now

    @staticmethod
    def _up(stage):
        if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y:
            return Gf.Vec3d(0.0, 1.0, 0.0)
        return Gf.Vec3d(0.0, 0.0, 1.0)

    @staticmethod
    def _eye(cam_prim):
        try:
            spot = UsdGeom.Xformable(cam_prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default()).ExtractTranslation()
            return Gf.Vec3d(spot[0], spot[1], spot[2])
        except Exception:
            return None

    @staticmethod
    def _stage():
        try:
            import omni.usd
            return omni.usd.get_context().get_stage()
        except Exception:
            return None

    @staticmethod
    def _frame(stage, facing) -> tuple:
        rot = UsdGeom.Xformable(facing).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()).ExtractRotationMatrix()
        up_row = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
        view = Gf.Vec3d(rot[1][0], rot[1][1], rot[1][2]).GetNormalized()
        up = Gf.Vec3d(rot[up_row][0], rot[up_row][1], rot[up_row][2]).GetNormalized()
        z_cam = -view
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()
        return x_cam, y_cam, z_cam

    @staticmethod
    def _write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye,
               distance: float) -> None:
        matrix = Gf.Matrix4d(
            x_cam[0], x_cam[1], x_cam[2], 0.0,
            y_cam[0], y_cam[1], y_cam[2], 0.0,
            z_cam[0], z_cam[1], z_cam[2], 0.0,
            eye[0],   eye[1],   eye[2],   1.0,
        )
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            xformable = UsdGeom.Xformable(cam_prim)
            transform_op = next(
                (op for op in xformable.GetOrderedXformOps()
                 if op.GetOpName() == "xformOp:transform"), None)
            if transform_op is not None:
                transform_op.Set(matrix)
            else:
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(matrix)
            camera.CreateClippingRangeAttr().Set(
                Gf.Vec2f(float(CAMERA_NEAR), float(CAMERA_FAR)))
            coi = cam_prim.GetAttribute("omni:kit:centerOfInterest")
            if coi and coi.IsValid():
                coi.Set(Gf.Vec3d(0.0, 0.0, -distance))
