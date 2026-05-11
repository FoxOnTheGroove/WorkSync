import math
from pxr import UsdGeom, Usd, Gf
import omni.usd


def get_fit_distance(target_prim, viewport_api, fill_ratio=0.9):
    stage = omni.usd.get_context().get_stage()

    bound = UsdGeom.Imageable(target_prim).ComputeWorldBound(
        Usd.TimeCode.Default(),
        UsdGeom.Tokens.default_
    )
    bbox = bound.ComputeAlignedBox()
    bb_min = bbox.GetMin()
    bb_max = bbox.GetMax()

    w, h = viewport_api.resolution
    aspect = w / h

    cam_prim = stage.GetPrimAtPath(viewport_api.camera_path)
    cam_xform = UsdGeom.Xformable(cam_prim).GetLocalTransformation()
    fov = UsdGeom.Camera(cam_prim).GetCamera().GetFieldOfView(Gf.Camera.FOVVertical)

    half_vfov = math.radians(fov / 2.0)
    tan_v = math.tan(half_vfov)
    tan_h = math.tan(math.atan(tan_v * aspect))

    coi_local = cam_prim.GetAttribute('omni:kit:centerOfInterest').Get()
    coi_world = cam_xform.Transform(coi_local)

    rot = cam_xform.ExtractRotationMatrix()

    corners = [
        Gf.Vec3d(x, y, z)
        for x in (bb_min[0], bb_max[0])
        for y in (bb_min[1], bb_max[1])
        for z in (bb_min[2], bb_max[2])
    ]

    d_min = 0.0
    for corner in corners:
        offset = corner - coi_world
        # Project world offset into camera space via dot with each row of R
        # p_cam = (q_x, q_y, q_z - d), so corner fits when:
        #   d >= q_z + |q_y| / tan_v  (vertical)
        #   d >= q_z + |q_x| / tan_h  (horizontal)
        q_x = rot[0][0]*offset[0] + rot[0][1]*offset[1] + rot[0][2]*offset[2]
        q_y = rot[1][0]*offset[0] + rot[1][1]*offset[1] + rot[1][2]*offset[2]
        q_z = rot[2][0]*offset[0] + rot[2][1]*offset[1] + rot[2][2]*offset[2]
        d_corner = q_z + max(abs(q_y) / tan_v, abs(q_x) / tan_h)
        if d_corner > d_min:
            d_min = d_corner

    return d_min / fill_ratio


def apply_zoom(viewport_api, distance):
    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(viewport_api.camera_path)

    xform = UsdGeom.Xformable(prim).GetLocalTransformation()
    rot_matrix = xform.ExtractRotationMatrix()

    # USD row-vector convention: local -Z axis is row[2]
    forward = Gf.Vec3d(-rot_matrix[2][0], -rot_matrix[2][1], -rot_matrix[2][2])

    # omni:kit:centerOfInterest is a Vec3d in camera-local space
    coi_local = prim.GetAttribute('omni:kit:centerOfInterest').Get()
    coi_world = xform.Transform(coi_local)
    new_pos = coi_world - forward * distance

    prim.GetAttribute('xformOp:translate').Set(new_pos)
