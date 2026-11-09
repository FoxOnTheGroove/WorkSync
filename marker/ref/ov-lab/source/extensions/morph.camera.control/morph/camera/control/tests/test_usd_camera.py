"""카메라 prim 읽기/쓰기 테스트 (omni.usd 스테이지, 뷰포트 없음)."""
import math

import omni.kit.test
from pxr import Gf, Sdf, Usd, UsdGeom

from morph.camera.control import camera_math as cm
from morph.camera.control import usd_camera

from ._helpers import assert_vec, define_camera, new_stage

EYE = (3.0, 4.0, 5.0)
TARGET = (1.0, 0.0, -2.0)


class TestUsdCamera(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = await new_stage("Y")

    def _roundtrip(self, path, eye=EYE, target=TARGET):
        up = usd_camera.stage_up_vector(self.stage)
        usd_camera.write_camera_pose(self.stage, path, eye, target, up)
        pose = usd_camera.read_camera_pose(self.stage, path)
        self.assertIsNotNone(pose)
        assert_vec(self, pose[0], eye)
        assert_vec(self, pose[1], target)
        return pose

    def _op_names(self, path):
        xf = UsdGeom.Xformable(self.stage.GetPrimAtPath(path))
        return [op.GetOpName() for op in xf.GetOrderedXformOps()]

    async def test_read_initial_pose(self):
        define_camera(self.stage, "/World/Cam")
        eye, target, fov = usd_camera.read_camera_pose(self.stage, "/World/Cam")
        assert_vec(self, eye, (0.0, 0.0, 10.0))
        assert_vec(self, target, (0.0, 0.0, 0.0))
        self.assertGreater(fov, 0.0)

    async def test_read_without_coi_uses_default_distance(self):
        define_camera(self.stage, "/World/Cam", coi_distance=None)
        eye, target, _ = usd_camera.read_camera_pose(self.stage, "/World/Cam")
        self.assertAlmostEqual(cm.distance(eye, target), usd_camera.DEFAULT_TARGET_DISTANCE, places=6)
        assert_vec(self, target, (0.0, 0.0, 10.0 - usd_camera.DEFAULT_TARGET_DISTANCE))

    async def test_roundtrip_keeps_translate_rotate_yxz_ops(self):
        define_camera(self.stage, "/World/Cam", ops="yxz")
        self._roundtrip("/World/Cam")
        self.assertEqual(self._op_names("/World/Cam"), ["xformOp:translate", "xformOp:rotateYXZ"])

    async def test_roundtrip_keeps_translate_rotate_xyz_ops(self):
        define_camera(self.stage, "/World/Cam", ops="xyz")
        self._roundtrip("/World/Cam")
        self.assertEqual(self._op_names("/World/Cam"), ["xformOp:translate", "xformOp:rotateXYZ"])

    async def test_roundtrip_matrix_op(self):
        define_camera(self.stage, "/World/Cam", ops="matrix")
        self._roundtrip("/World/Cam")

    async def test_roundtrip_no_ops(self):
        define_camera(self.stage, "/World/Cam", ops="none", coi_distance=None)
        self._roundtrip("/World/Cam")

    async def test_roundtrip_looking_straight_down(self):
        define_camera(self.stage, "/World/Cam")
        self._roundtrip("/World/Cam", eye=(0.0, 10.0, 0.0), target=(0.0, 0.0, 0.0))

    async def test_roundtrip_under_transformed_parent(self):
        parent = UsdGeom.Xform.Define(self.stage, "/World/Rig")
        pxf = UsdGeom.Xformable(parent.GetPrim())
        pxf.AddTranslateOp().Set(Gf.Vec3d(10, 0, 0))
        pxf.AddRotateYOp().Set(90.0)
        define_camera(self.stage, "/World/Rig/Cam")
        self._roundtrip("/World/Rig/Cam")

    async def test_z_up_stage_keeps_camera_upright(self):
        UsdGeom.SetStageUpAxis(self.stage, UsdGeom.Tokens.z)
        self.assertEqual(usd_camera.stage_up_vector(self.stage), (0.0, 0.0, 1.0))
        define_camera(self.stage, "/World/Cam")
        self._roundtrip("/World/Cam", eye=(10.0, -10.0, 5.0), target=(0.0, 0.0, 0.0))
        world = UsdGeom.Xformable(self.stage.GetPrimAtPath("/World/Cam")).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        cam_up = world.TransformDir(Gf.Vec3d(0, 1, 0))
        self.assertGreater(cam_up[2], 0.0)

    async def test_fov_horizontal(self):
        define_camera(self.stage, "/World/Cam")
        cam = UsdGeom.Camera(self.stage.GetPrimAtPath("/World/Cam"))
        cam.GetHorizontalApertureAttr().Set(20.955)
        cam.GetFocalLengthAttr().Set(18.147)
        _, _, fov = usd_camera.read_camera_pose(self.stage, "/World/Cam")
        expected = math.degrees(2.0 * math.atan(20.955 / (2.0 * 18.147)))
        self.assertAlmostEqual(fov, expected, places=3)

    async def test_invalid_paths(self):
        UsdGeom.Xform.Define(self.stage, "/World/NotCam")
        self.assertIsNone(usd_camera.read_camera_pose(self.stage, "/Nope"))
        self.assertIsNone(usd_camera.read_camera_pose(self.stage, ""))
        self.assertIsNone(usd_camera.read_camera_pose(self.stage, "not a path"))
        self.assertFalse(usd_camera.is_camera(self.stage, "/World/NotCam"))
        self.assertFalse(usd_camera.is_camera(self.stage, "/Nope"))
        with self.assertRaises(ValueError):
            usd_camera.write_camera_pose(self.stage, "/Nope", EYE, TARGET, (0.0, 1.0, 0.0))

    def _scaled_parent_camera(self, s):
        parent = UsdGeom.Xform.Define(self.stage, "/World/Scaled")
        pxf = UsdGeom.Xformable(parent.GetPrim())
        pxf.AddTranslateOp().Set(Gf.Vec3d(1, 2, 3))
        pxf.AddRotateYOp().Set(30.0)
        pxf.AddScaleOp().Set(Gf.Vec3f(s, s, s))
        return define_camera(self.stage, "/World/Scaled/Cam")

    async def _check_scaled_parent(self, s):
        path = self._scaled_parent_camera(s)
        for _ in range(3):  # 반복 왕복에도 거리 누적 없음
            self._roundtrip(path)
        world = UsdGeom.Xformable(self.stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        for i in range(3):  # 카메라 월드 행렬은 scale 1 (부모 scale 상쇄)
            self.assertAlmostEqual(Gf.Vec3d(world[i][0], world[i][1], world[i][2]).GetLength(), 1.0, places=4)

    async def test_roundtrip_under_parent_scale_small(self):
        await self._check_scaled_parent(0.01)

    async def test_roundtrip_under_parent_scale_large(self):
        await self._check_scaled_parent(2.0)

    async def test_user_camera_with_session_over_written_to_edit_target(self):
        define_camera(self.stage, "/World/Cam")
        session = self.stage.GetSessionLayer()
        with Usd.EditContext(self.stage, session):  # 관계없는 세션 over
            self.stage.GetPrimAtPath("/World/Cam").CreateAttribute("morph:test", Sdf.ValueTypeNames.Float).Set(1.0)
        self._roundtrip("/World/Cam")
        self.assertIsNone(session.GetPropertyAtPath("/World/Cam.xformOp:translate"))
        self.assertIsNotNone(self.stage.GetRootLayer().GetPropertyAtPath("/World/Cam.xformOp:translate"))

    async def test_kit_session_camera_prefix_written_to_session_layer(self):
        define_camera(self.stage, "/OmniverseKit_MorphTestCam")  # 루트 레이어에 정의
        self._roundtrip("/OmniverseKit_MorphTestCam")
        session = self.stage.GetSessionLayer()
        self.assertIsNotNone(session.GetPropertyAtPath("/OmniverseKit_MorphTestCam.xformOp:translate"))

    async def test_session_layer_camera_written_to_session_layer(self):
        session = self.stage.GetSessionLayer()
        with Usd.EditContext(self.stage, session):
            define_camera(self.stage, "/SessionCam")
        self._roundtrip("/SessionCam")
        self.assertIsNone(self.stage.GetRootLayer().GetPrimAtPath("/SessionCam"))
        self.assertIsNotNone(session.GetPrimAtPath("/SessionCam"))
