from pxr import Usd, UsdGeom, Gf
import omni.usd
from morph.hytwin_orbit_extension import Orbit


AXIS_VECTORS: dict[str, Gf.Vec3d] = {
    "x":  Gf.Vec3d( 1,  0,  0),
    "-x": Gf.Vec3d(-1,  0,  0),
    "y":  Gf.Vec3d( 0,  1,  0),
    "-y": Gf.Vec3d( 0, -1,  0),
    "z":  Gf.Vec3d( 0,  0,  1),
    "-z": Gf.Vec3d( 0,  0, -1),
}


class AxisControl:

    _initialized: bool = False

    # ------------------------------------------------------------------ 공개 API

    @classmethod
    def initialize(cls) -> bool:
        if not Orbit._cam_info:
            print("[AxisControl] Orbit._cam_info가 비어 있습니다.")
            cls._initialized = False
            return False
        cls._initialized = True
        print("[AxisControl] 초기화 완료.")
        return True

    @classmethod
    def get_cameras(cls) -> list[Usd.Prim]:
        if not cls._initialized:
            return []
        stage = cls._get_stage()
        if not stage:
            return []
        result = []
        for item in Orbit._cam_info:
            prim = stage.GetPrimAtPath(item["cam_path"])
            if prim.IsValid():
                result.append(prim)
        return result

    @classmethod
    def set_camera(cls, camera_prim: Usd.Prim, axis: str) -> None:
        if not cls._initialized:
            print("[AxisControl] 초기화되지 않았습니다. AxisControl.initialize()를 먼저 호출하세요.")
            return

        if axis not in AXIS_VECTORS:
            print(f"[AxisControl] 잘못된 축: '{axis}'. 유효값: {list(AXIS_VECTORS.keys())}")
            return

        if not camera_prim or not camera_prim.IsValid():
            print("[AxisControl] 유효하지 않은 카메라 prim.")
            return

        # COI 로컬 → 월드 변환으로 orbit 중심과 거리 계산
        orbit_center, distance = cls._get_coi_world_and_distance(camera_prim)

        if orbit_center is None:
            # COI 미설정 시 타겟 prim 위치로 폴백
            target_prim = cls._get_target_for_camera(camera_prim)
            if target_prim is None or not target_prim.IsValid():
                print(f"[AxisControl] 카메라 '{camera_prim.GetName()}'에 유효한 타겟이 없습니다.")
                return
            cam_pos     = cls._get_world_translation(camera_prim)
            orbit_center = cls._get_world_translation(target_prim)
            distance    = (cam_pos - orbit_center).GetLength()
            if distance < 1e-6:
                distance = 100.0

        eye    = orbit_center + AXIS_VECTORS[axis] * distance
        matrix = cls._build_lookat_matrix(eye, orbit_center, axis)

        cls._apply_lookat(camera_prim, matrix, axis)
        cls._set_coi(camera_prim, distance)

    # ------------------------------------------------------------------ 내부 메서드

    @classmethod
    def _get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    @classmethod
    def _get_target_for_camera(cls, camera_prim: Usd.Prim) -> "Usd.Prim | None":
        cam_path = str(camera_prim.GetPath())
        for item in Orbit._cam_info:
            if item["cam_path"] == cam_path:
                return Orbit.find_target_prim(item["id"])
        return None

    @classmethod
    def _get_world_translation(cls, prim: Usd.Prim) -> Gf.Vec3d:
        xformable = UsdGeom.Xformable(prim)
        world_xform = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        return world_xform.ExtractTranslation()

    @classmethod
    def _get_coi_world_and_distance(
        cls, camera_prim: Usd.Prim
    ) -> "tuple[Gf.Vec3d, float] | tuple[None, None]":
        # COI는 카메라 로컬 좌표계 기준 관심점 벡터 → 월드로 변환해야 실제 orbit 중심
        attr = camera_prim.GetAttribute("omni:kit:centerOfInterest")
        if not attr.IsValid():
            return None, None
        coi = attr.Get()
        if coi is None:
            return None, None
        coi_local = Gf.Vec3d(float(coi[0]), float(coi[1]), float(coi[2]))
        distance = coi_local.GetLength()
        if distance < 1e-6:
            return None, None
        # 카메라 로컬 → 월드 변환 (포인트 변환이므로 평행이동 포함)
        cam_xform = UsdGeom.Xformable(camera_prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        coi_world = cam_xform.Transform(coi_local)
        return coi_world, distance

    @classmethod
    def _set_coi(cls, camera_prim: Usd.Prim, distance: float) -> None:
        # 이동 후 카메라는 -Z 방향으로 orbit 중심을 정면으로 바라보므로 COI = (0, 0, -distance)
        attr = camera_prim.GetAttribute("omni:kit:centerOfInterest")
        if not attr.IsValid():
            return
        coi = attr.Get()
        if coi is None:
            return
        # 원본 정밀도 유지 (Vec3f vs Vec3d)
        if isinstance(coi, Gf.Vec3f):
            attr.Set(Gf.Vec3f(0.0, 0.0, float(-distance)))
        else:
            attr.Set(Gf.Vec3d(0.0, 0.0, -distance))

    @classmethod
    def _build_lookat_matrix(
        cls, eye: Gf.Vec3d, target: Gf.Vec3d, axis: str
    ) -> Gf.Matrix4d:
        # X/Z 축은 Y-up; Y 방향 시점은 짐벌락 방지를 위해 Z-up
        world_up = Gf.Vec3d(0, 0, 1) if axis in ("y", "-y") else Gf.Vec3d(0, 1, 0)

        # USD 카메라는 로컬 -Z를 봄; +Z = orbit 중심 반대방향
        forward = (eye - target).GetNormalized()

        right = Gf.Cross(world_up, forward)
        if right.GetLength() < 1e-6:
            world_up = Gf.Vec3d(1, 0, 0)
            right = Gf.Cross(world_up, forward)
        right = right.GetNormalized()

        up = Gf.Cross(forward, right).GetNormalized()

        # USD row-vector 규약 (p' = p * M) 에 맞춘 row-major 배치
        m = Gf.Matrix4d()
        m.SetRow(0, Gf.Vec4d(right[0],   right[1],   right[2],   0))
        m.SetRow(1, Gf.Vec4d(up[0],      up[1],      up[2],      0))
        m.SetRow(2, Gf.Vec4d(forward[0], forward[1], forward[2], 0))
        m.SetRow(3, Gf.Vec4d(eye[0],     eye[1],     eye[2],     1))
        return m

    @classmethod
    def _apply_lookat(cls, camera_prim: Usd.Prim, matrix: Gf.Matrix4d, axis: str) -> None:
        time = Usd.TimeCode.Default()

        # translate는 double3, rotateXYZ/scale/pivot은 float3
        translation = matrix.ExtractTranslation()  # Gf.Vec3d

        # +Y/-Y 축은 pitch=90° 짐벌락 → Decompose 결과가 틀리므로 직접 계산된 값 사용
        # 검증: Rx(90)·Rz(180) = [(-1,0,0),(0,0,1),(0,1,0)] (+Y 행렬과 일치)
        #       Rx(90)         = [(1,0,0),(0,0,1),(0,-1,0)] (-Y 행렬과 일치)
        if axis == "y":
            rotation = Gf.Vec3f(90.0, 0.0, 180.0)
        elif axis == "-y":
            rotation = Gf.Vec3f(90.0, 0.0, 0.0)
        else:
            euler_d  = matrix.ExtractRotation().Decompose(
                Gf.Vec3d(1, 0, 0),
                Gf.Vec3d(0, 1, 0),
                Gf.Vec3d(0, 0, 1),
            )
            rotation = Gf.Vec3f(float(euler_d[0]), float(euler_d[1]), float(euler_d[2]))

        common = UsdGeom.XformCommonAPI(camera_prim)
        ok = common.SetXformVectors(
            translation,                           # Vec3d
            rotation,                              # Vec3f
            Gf.Vec3f(1.0, 1.0, 1.0),              # Vec3f
            Gf.Vec3f(0.0, 0.0, 0.0),              # Vec3f
            UsdGeom.XformCommonAPI.RotationOrderXYZ,
            time,
        )

        if not ok:
            # 폴백: 4×4 transform op 직접 기록
            xformable = UsdGeom.Xformable(camera_prim)
            xformable.ClearXformOpOrder()
            xformable.AddTransformOp().Set(matrix, time)
