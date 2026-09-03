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


class EbsSimulateCamera:
    """/EbsCamera 하나를 만들고, 놓고, 되돌린다.

    스테이지와 대상 상자를 받기만 한다 — 무엇을 볼지는 부르는 쪽이 정하고,
    여기서는 그 점을 어떻게 보느냐만 한다.
    """

    def __init__(self):
        self._previous = None      # 우리 카메라로 바꾸기 전에 뷰포트가 보던 것

    @property
    def previous(self):
        return self._previous

    @staticmethod
    def viewport():
        try:
            from omni.kit.viewport.utility import get_active_viewport
            return get_active_viewport()
        except Exception as e:
            print(f"[ebs] viewport utility unavailable: {e}")
            return None

    def make(self, stage) -> bool:
        if stage is None:
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
        if stage is None:
            return
        viewport = self.viewport()
        if viewport is not None and self._previous:
            try:
                viewport.camera_path = self._previous
            except Exception as e:
                print(f"[ebs] could not restore the viewport camera: {e}")
        self._previous = None
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
        return (f"camera {distance:.2f} back from the EBS centre, "
                f"near plane {CAMERA_NEAR:.2f}, nothing culled")

    def _camera(self, stage):
        """카메라 프림. 없으면 만든다.

        프림이 유효하냐가 아니라 카메라냐를 묻는다 — Clear 로 지운 뒤에도
        뷰포트가 그 경로를 보는 동안 Kit 이 제 상태를 써서 타입 없는 over 가
        남는다. 그것도 IsValid 는 참이고, 그대로 쓰면 clippingRange 에서
        empty typename 이 난다. Define 이 그 over 에 타입을 얹는다.
        """
        cam_prim = stage.GetPrimAtPath(CAMERA_PATH)
        camera = UsdGeom.Camera(cam_prim) if cam_prim.IsValid() else None
        if camera:
            return cam_prim, camera
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
