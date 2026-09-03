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

# Kit 의 카메라 조작은 이 설정의 사전으로 정해진다. 비우면 아무 버튼도 카메라를
# 안 옮긴다. 원래 값은 되돌릴 때 쓰려고 쥐고 있고, 못 읽었으면 문서의 기본값.
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
    """뷰포트 창. 이 빌드가 어떤 도우미를 갖고 있든 하나는 찾는다.

    하나씩 따로 묻는다 — 같이 임포트하면 이 빌드에 없는 이름 하나가 전체를
    끌어내리고, "omni.kit 이 없다"는 말이 돌아온다. 멀쩡히 도는 Kit 을 두고.
    """
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
    """/EbsCamera 하나를 만들고, 놓고, 되돌린다.

    스테이지와 대상 상자를 받기만 한다 — 무엇을 볼지는 부르는 쪽이 정하고,
    여기서는 그 점을 어떻게 보느냐만 한다.
    """

    def __init__(self):
        self._previous = None      # 우리 카메라로 바꾸기 전에 뷰포트가 보던 것
        self._orbit = False        # interest 둘레를 도는 모드인가
        self._interest = None      # 그 점, 월드 좌표
        self._frame_ui = None      # 입력을 가로채는 프레임
        self._catch = None         # 그 안의 투명한 판
        self._at = None            # 마지막 마우스 자리
        self._selection = None     # 선택을 지우는 구독
        self._no_pick = None       # 쥐고 있는 동안 선택이 꺼진다
        self._no_menu = None       # 쥐고 있는 동안 우클릭 메뉴가 꺼진다
        self._bindings = None      # 비우기 전의 카메라 조작 바인딩
        self._from = None          # 끌기 시작한 화면 좌표
        self._axis = None          # 이번 끌기가 도는 축: 'yaw' 또는 'pitch'
        self._home = None          # place 가 놓았던 자리. refresh 가 돌아갈 곳

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
        """카메라가 있고, 카메라 타입인가.

        프림이 유효하냐로 묻지 않는다 — 뷰포트가 그 경로를 보는 동안 Kit 이
        제 상태를 써서 타입 없는 over 가 남을 수 있고, 그것도 IsValid 는
        참이다. 그대로 쓰면 clippingRange 에서 empty typename 이 난다.
        """
        if stage is None:
            return False
        prim = stage.GetPrimAtPath(CAMERA_PATH)
        return bool(prim.IsValid() and UsdGeom.Camera(prim))

    def make(self, stage) -> bool:
        """없을 때만 만든다. 한 번 만든 것은 세션 내내 그대로 쓴다."""
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
        """뷰포트를 돌려주고 궤도 모드를 끈다. 카메라는 남긴다.

        지우면 다음 Camera 가 새로 만들어야 하고, 그 사이 Kit 이 그 경로에
        써 둔 것이 남아 골치가 된다. 세션 내내 같은 하나를 쓴다.
        """
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
        """place 가 놓았던 그 자리로. 돌려놓은 것을 되돌린다."""
        if stage is None or self._home is None:
            return ""
        cam_prim, camera = self._camera(stage)
        if camera is None:
            return ""
        x_cam, y_cam, z_cam, eye, distance, interest = self._home
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, distance)
        self._interest = interest       # 더블클릭으로 옮겨 둔 중심도 제자리로
        self._from = self._axis = None
        return f"camera back to {distance:.2f} in front of the EBS"

    def remove(self, stage) -> None:
        """익스텐션이 내려갈 때만. 세션 중에는 부르지 않는다."""
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
        # 여기서부터 궤도 모드다. 카메라는 이 점을 계속 바라보고, 움직일 때는
        # 제자리에서 도는 것이 아니라 이 점 둘레를 돈다.
        self._interest = interest
        self._home = (x_cam, y_cam, z_cam, eye, distance, interest)
        self._orbit = True
        self._grab()
        return (f"camera {distance:.2f} back from the EBS centre, "
                f"orbiting ({interest[0]:.2f}, {interest[1]:.2f}, "
                f"{interest[2]:.2f}), near plane {CAMERA_NEAR:.2f}")

    def _camera(self, stage):
        """카메라 프림. 없으면 만든다. 보통은 init 이 이미 만들어 두었다."""
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

    # -- 입력 가로채기 --------------------------------------------------------

    def _grab(self) -> bool:
        """뷰포트의 마우스를 우리가 받고, Kit 은 못 받게 한다.

        받기는 프레임에 깐 판이 한다 — 이 빌드에서 확인된 유일한 경로다.
        막기는 Kit 이 제공하는 스위치 셋으로 한다. 판 위에 얹거나 제스처
        매니저로 겨루는 것으로는 안 막힌다 (다 해봤다):
          선택       omni.kit.viewport.utility.disable_selection
          우클릭 메뉴 omni.kit.viewport.utility.disable_context_menu
          카메라 조작 /exts/omni.kit.viewport.window/bindings/camera 를 비움
        앞의 둘은 핸들을 쥐고 있는 동안만 꺼진다. 놓으면 돌아온다.
        """
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
        self._no_pick = None       # 핸들을 놓으면 Kit 이 되돌린다
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
                # 크기를 안 주면 접힌다. 접힌 판은 아무것도 못 받는다.
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
        """궤도 모드에서는 프림이 안 골라진다.

        고르는 것 자체를 막지는 못해서, 골라지면 바로 지운다. 장비는 이름을
        적어 넣어 고르는 쪽으로 갈 것이라 선택이 남을 이유가 없다.
        """
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

    # -- 제스처 ---------------------------------------------------------------

    def _pressed(self, x, y, button) -> None:
        # 왼쪽만 쓴다. 나머지 버튼은 우리 쪽에서 아무것도 안 한다.
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
        """찍은 자리의 표면이 새 interest 가 된다. 카메라도 그만큼 따라 옮긴다.

        무엇이 찍혔는지는 뷰포트에게 묻는다 (request_query) — 화면 한 점이
        무엇에 닿는지는 뷰포트가 이미 알고 있고, 월드 좌표까지 돌려준다.
        omni.kit.raycast.query 는 광선을 우리가 만들어 쏘는 쪽이라 여기서는
        일이 더 많고, 익스텐션도 하나 더 켜야 한다.
        """
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
        """화면 좌표를 판 안의 -1..1 로. 판 밖이면 아무것도 아니다."""
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
            return                       # 허공을 찍었다
        try:
            from .ebs_simulate import OURS, OURS_UNDER
        except Exception:
            OURS, OURS_UNDER = (CAMERA_PATH,), (CAMERA_PATH + "/",)
        path = str(path)
        if path in OURS or path.startswith(tuple(OURS_UNDER)):
            return                       # 우리가 그린 것은 대상이 아니다
        self._look_at(Gf.Vec3d(position[0], position[1], position[2]))

    def _look_at(self, target) -> None:
        """interest 만 옮긴다. 팔과 반지름이 그대로라 눈은 같은 만큼 평행이동
        하고, 보던 방향도 그대로다. 찍은 점이 화면 한가운데로 온다."""
        hold = self._hold()
        if hold is None:
            return
        self._interest = target
        self._settle(hold, hold[3], hold[4])

    def _wheel(self, notches: float = 0.0) -> None:
        # 위로 굴리면 가까워진다.
        if notches:
            self._zoom(notches)

    def _drag(self, dx: float, dy: float) -> None:
        if self._from is None or not self._orbit:
            return
        self._from = (self._from[0] + dx, self._from[1] + dy)
        if self._axis is None:
            # 어느 쪽으로 더 끌었는지로 축을 한 번 정하고, 그 끌기 동안은 그
            # 축만 돈다. 요와 피치가 섞이는 일은 없다.
            total = self._from
            if max(abs(total[0]), abs(total[1])) < AXIS_LOCK:
                return
            self._axis = "yaw" if abs(total[0]) >= abs(total[1]) else "pitch"
        # 화면 y 는 아래가 양수다. 아래로 끌면 눈이 올라간다.
        if self._axis == "yaw":
            self._turn(yaw=-dx * YAW_PER_PIXEL)
        else:
            self._turn(pitch=dy * PITCH_PER_PIXEL)

    # -- 공전 -----------------------------------------------------------------

    def _hold(self):
        """지금 카메라가 interest 를 붙잡고 있는 팔. 돌리기와 줌이 같이 쓴다."""
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
        """그 팔 끝에 눈을 놓고, 언제나 interest 를 월드 up 으로 바라보게 한다.

        자세를 팔에서 매번 다시 세우므로 롤이 쌓일 자리가 없다.
        """
        stage, cam_prim, camera = hold[0], hold[1], hold[2]
        up = self._up(stage)
        eye = self._interest + arm.GetNormalized() * radius
        z_cam = (eye - self._interest).GetNormalized()
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, radius)

    def _turn(self, yaw: float = 0.0, pitch: float = 0.0) -> None:
        """interest 둘레로 눈을 옮긴다. 반지름은 그대로."""
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
                # 양수 = 눈이 올라간다. _room 이 재는 고도와 부호가 같다.
                side = Gf.Cross(arm, up).GetNormalized()
                arm = Gf.Rotation(side, pitch).TransformDir(arm)
        self._settle(hold, arm, radius)

    def _zoom(self, notches: float) -> None:
        """가까이 오거나 멀어진다. 방향은 그대로, 반지름만 바뀐다.

        칸마다 같은 비율로 곱한다 — 멀리서는 성큼, 가까이서는 자잘하게
        움직여야 어디서 굴리든 같은 손맛이 난다.
        """
        hold = self._hold()
        if hold is None:
            return
        arm, radius = hold[3], hold[4]
        want = radius * (ZOOM_PER_NOTCH ** notches)
        want = min(max(want, CAMERA_BACK * ZOOM_NEAREST),
                   CAMERA_BACK * ZOOM_FURTHEST)
        if abs(want - radius) < 1e-9:
            return                          # 끝에 닿았다
        self._settle(hold, arm, want)

    @staticmethod
    def _room(arm, up, pitch: float) -> float:
        """극을 넘지 않도록 남은 각도만 돌려준다. 넘으면 롤이 생긴다."""
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
        """대상이 보는 쪽을 카메라 축 셋으로. z 는 대상에서 카메라 쪽이다."""
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
            # 카메라 앞 distance 지점. Kit 의 궤도 회전이 도는 중심이 이것이다
            # — 제자리에서 방향만 도는 것이 아니라 이 점 둘레를 돈다.
            coi = cam_prim.GetAttribute("omni:kit:centerOfInterest")
            if coi and coi.IsValid():
                coi.Set(Gf.Vec3d(0.0, 0.0, -distance))
