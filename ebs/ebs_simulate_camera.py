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
UNIT_PIXELS = 500.0  # Screen 의 끌기 양을 픽셀로 옮기는 환산. 화면 폭이 대략 2
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
        self._scene = None         # 뷰포트 씬에 등록한 우리 제스처
        self._catch = None         # 등록이 안 될 때 까는 판
        self._at = None            # 마지막 마우스 자리
        self._selection = None     # 선택을 지우는 구독
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
        x_cam, y_cam, z_cam, eye, distance = self._home
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, distance)
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
        self._home = (x_cam, y_cam, z_cam, eye, distance)
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
        """뷰포트가 제 제스처를 처리하는 그 자리에 우리 제스처를 넣는다.

        따로 SceneView 를 세워봤자 아레나가 다르다 — 우리 것은 안 뜨고 Kit 것은
        안 막힌다. RegisterScene 은 뷰포트 자신의 씬 안에서 우리를 만들어 주므로
        같은 아레나에 선다. 거기서는 can_be_prevented 가 False 인 쪽이 이긴다:
        같은 버튼에 우리와 Kit 이 함께 걸리면 막힐 수 있는 Kit 쪽이 막힌다.

        그것마저 없는 빌드면 프레임에 판을 깔아 받기라도 한다. 판은 받을 뿐
        막지는 못한다 — 뷰포트의 조작은 omni.ui 를 안 거친다.
        """
        if self._catch is not None or self._scene is not None:
            return True
        if self._grab_arena():
            self._mute_selection()
            return True
        window = viewport_window()
        if window is None:
            return False
        if not self._grab_sheet(window):
            return False
        self._mute_selection()
        return True

    def _grab_arena(self) -> bool:
        try:
            from omni.kit.viewport.registry import RegisterScene
            from omni.ui import scene as sc
        except Exception as e:
            print(f"[ebs] viewport scene registry unavailable ({e}); "
                  f"falling back to a sheet that receives but cannot block")
            return False

        owner = self

        class Keep(sc.GestureManager):
            # 두 물음 다 '이 매니저를 단 제스처', 곧 우리 것에 대한 것이다.
            # 우리 것은 아무것도 안 막고, 아무한테도 안 막힌다. 같은 버튼에
            # 함께 걸리면 막힐 수 있는 Kit 쪽이 물러난다.
            def can_be_prevented(self, gesture) -> bool:
                return False

            def should_prevent(self, gesture, preventer) -> bool:
                return False

        class Ours:
            """뷰포트가 제 씬을 지을 때 우리를 하나 만들어 준다."""

            def __init__(self, desc):
                self.__screen = sc.Screen(gestures=owner._gestures(sc, Keep()))

            @property
            def visible(self) -> bool:
                return True

            @visible.setter
            def visible(self, value) -> None:
                pass

            @property
            def categories(self) -> tuple:
                return ("manipulator",)

            @property
            def name(self) -> str:
                return "EBS orbit"

            def destroy(self) -> None:
                self.__screen = None

        try:
            self._scene = RegisterScene(Ours, "ebs.orbit")
        except Exception as e:
            print(f"[ebs] could not register the orbit scene ({e}); "
                  f"falling back to a sheet")
            self._scene = None
            return False
        return True

    def _gestures(self, sc, keeper) -> list:
        """쓸 것과, 받기만 하고 아무것도 안 하는 것들.

        오른쪽과 가운데도 우리가 가져가야 Kit 이 그 버튼으로 카메라를 못 옮긴다.
        빌드에 없는 이름은 건너뛴다 — 하나 때문에 전부 안 붙으면 뷰포트가 통째로
        Kit 손에 남는다.
        """
        made = []

        def add(name, **kw):
            kind = getattr(sc, name, None)
            if kind is None:
                return
            try:
                made.append(kind(manager=keeper, **kw))
            except Exception as e:
                print(f"[ebs] {name} not taken: {e}")

        add("DragGesture", mouse_button=LEFT_BUTTON,
            on_began_fn=lambda g: self._start_drag(),
            on_changed_fn=lambda g: self._dragged(g),
            on_ended_fn=lambda g: self._end_drag())
        add("ClickGesture", mouse_button=LEFT_BUTTON,
            on_ended_fn=lambda g: None)
        add("DoubleClickGesture", mouse_button=LEFT_BUTTON,
            on_ended_fn=lambda g: self._double())
        add("ScrollGesture", on_ended_fn=lambda g: self._wheel())
        for button in (RIGHT_BUTTON, MIDDLE_BUTTON):
            add("DragGesture", mouse_button=button)
            add("ClickGesture", mouse_button=button)
        return made

    def _dragged(self, gesture) -> None:
        """Screen 위의 끌기. 화면 폭이 대략 2 라 픽셀로 환산해 쓴다."""
        try:
            moved = gesture.gesture_payload.moved
        except Exception:
            return
        self._drag(moved[0] * UNIT_PIXELS, -moved[1] * UNIT_PIXELS)

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
                lambda x, y, button, mod: self._double())
            self._catch.set_mouse_wheel_fn(
                lambda dx, dy, mod: self._wheel())
        except Exception as e:
            print(f"[ebs] could not take the viewport input: {e}")
            self._drop()
            return False
        return True

    def _drop(self) -> None:
        self._from = self._axis = self._at = None
        self._selection = None
        if self._scene is not None:
            try:
                self._scene.destroy()
            except Exception:
                pass
        if self._frame_ui is not None:
            try:
                self._frame_ui.clear()
            except Exception:
                pass
        self._scene = None
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

    def _double(self) -> None:
        pass                     # 아직 정해진 것이 없다

    def _wheel(self) -> None:
        pass                     # 아직 정해진 것이 없다

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

    def _turn(self, yaw: float = 0.0, pitch: float = 0.0) -> None:
        """interest 둘레로 눈을 옮긴다. 반지름은 그대로, 롤은 없다."""
        stage = self._stage()
        if stage is None or self._interest is None:
            return
        cam_prim, camera = self._camera(stage)
        if camera is None:
            return
        eye = self._eye(cam_prim)
        if eye is None:
            return

        up = self._up(stage)
        arm = eye - self._interest
        radius = arm.GetLength()
        if radius <= 1e-9:
            return

        if yaw:
            arm = Gf.Rotation(up, yaw).TransformDir(arm)
        if pitch:
            pitch = self._room(arm, up, pitch)
            if pitch:
                # 양수 = 눈이 올라간다. _room 이 재는 고도와 부호가 같다.
                side = Gf.Cross(arm, up).GetNormalized()
                arm = Gf.Rotation(side, pitch).TransformDir(arm)

        eye = self._interest + arm.GetNormalized() * radius
        # 언제나 interest 를 월드 up 으로 바라본다. 롤이 생길 자리가 없다.
        z_cam = (eye - self._interest).GetNormalized()
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()
        self._write(stage, cam_prim, camera, x_cam, y_cam, z_cam, eye, radius)

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
