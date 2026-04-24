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

    # ------------------------------------------------------------------ public API

    @classmethod
    def initialize(cls) -> bool:
        if not Orbit._cam_info:
            print("[AxisControl] Orbit._cam_info is empty.")
            cls._initialized = False
            return False
        cls._initialized = True
        print("[AxisControl] Initialized.")
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
            print("[AxisControl] Not initialized. Call AxisControl.initialize() first.")
            return

        if axis not in AXIS_VECTORS:
            print(f"[AxisControl] Invalid axis: '{axis}'. Valid: {list(AXIS_VECTORS.keys())}")
            return

        if not camera_prim or not camera_prim.IsValid():
            print("[AxisControl] Invalid camera prim.")
            return

        target_prim = cls._get_target_for_camera(camera_prim)
        if target_prim is None or not target_prim.IsValid():
            print(f"[AxisControl] No valid target for camera '{camera_prim.GetName()}'.")
            return

        cam_pos    = cls._get_world_translation(camera_prim)
        target_pos = cls._get_world_translation(target_prim)

        # Prefer COI distance (orbit radius); fall back to current cam-target gap
        distance = cls._get_coi_distance(camera_prim)
        if distance is None or distance < 1e-6:
            distance = (cam_pos - target_pos).GetLength()
        if distance < 1e-6:
            distance = 100.0

        eye    = target_pos + AXIS_VECTORS[axis] * distance
        matrix = cls._build_lookat_matrix(eye, target_pos, axis)

        cls._apply_lookat(camera_prim, matrix)
        cls._set_coi(camera_prim, distance)

    # ------------------------------------------------------------------ internals

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
    def _get_coi_distance(cls, camera_prim: Usd.Prim) -> "float | None":
        # omni:kit:centerOfInterest is in camera-local space; its length = orbit radius
        attr = camera_prim.GetAttribute("omni:kit:centerOfInterest")
        if not attr.IsValid():
            return None
        coi = attr.Get()
        if coi is None:
            return None
        return Gf.Vec3d(float(coi[0]), float(coi[1]), float(coi[2])).GetLength()

    @classmethod
    def _set_coi(cls, camera_prim: Usd.Prim, distance: float) -> None:
        # After repositioning the camera looks along -Z straight at the target,
        # so the new COI in camera-local space is (0, 0, -distance).
        attr = camera_prim.GetAttribute("omni:kit:centerOfInterest")
        if not attr.IsValid():
            return
        coi = attr.Get()
        if coi is None:
            return
        # Preserve original precision (Vec3f vs Vec3d)
        if isinstance(coi, Gf.Vec3f):
            attr.Set(Gf.Vec3f(0.0, 0.0, float(-distance)))
        else:
            attr.Set(Gf.Vec3d(0.0, 0.0, -distance))

    @classmethod
    def _build_lookat_matrix(
        cls, eye: Gf.Vec3d, target: Gf.Vec3d, axis: str
    ) -> Gf.Matrix4d:
        # Y-up for X/Z axes; Z-up when looking along Y (avoids gimbal singularity)
        world_up = Gf.Vec3d(0, 0, 1) if axis in ("y", "-y") else Gf.Vec3d(0, 1, 0)

        # USD cameras look along local -Z; camera +Z points away from target
        forward = (eye - target).GetNormalized()

        right = Gf.Cross(world_up, forward)
        if right.GetLength() < 1e-6:
            world_up = Gf.Vec3d(1, 0, 0)
            right = Gf.Cross(world_up, forward)
        right = right.GetNormalized()

        up = Gf.Cross(forward, right).GetNormalized()

        # Row-major layout for USD row-vector convention (p' = p * M)
        m = Gf.Matrix4d()
        m.SetRow(0, Gf.Vec4d(right[0],   right[1],   right[2],   0))
        m.SetRow(1, Gf.Vec4d(up[0],      up[1],      up[2],      0))
        m.SetRow(2, Gf.Vec4d(forward[0], forward[1], forward[2], 0))
        m.SetRow(3, Gf.Vec4d(eye[0],     eye[1],     eye[2],     1))
        return m

    @classmethod
    def _apply_lookat(cls, camera_prim: Usd.Prim, matrix: Gf.Matrix4d) -> None:
        time = Usd.TimeCode.Default()

        # translation → double (xformOp:translate is double3)
        # rotation/scale/pivot → float (xformOp:rotateXYZ / scale / pivot are float3)
        translation = matrix.ExtractTranslation()          # Gf.Vec3d
        euler_d     = matrix.ExtractRotation().Decompose(
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
            # Fallback: write a raw 4×4 transform op
            xformable = UsdGeom.Xformable(camera_prim)
            xformable.ClearXformOpOrder()
            xformable.AddTransformOp().Set(matrix, time)
