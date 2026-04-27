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
    size = bbox.GetMax() - bbox.GetMin()

    w, h = viewport_api.resolution
    aspect = w / h

    prim = stage.GetPrimAtPath(viewport_api.camera_path)
    fov = UsdGeom.Camera(prim).GetCamera().GetFieldOfView(Gf.Camera.FOVVertical)
    half_fov_rad = math.radians(fov / 2.0)

    radius_v = size[1] * 0.5
    radius_h = size[0] * 0.5 / aspect
    radius = max(radius_v, radius_h)

    fit_distance = radius / math.tan(half_fov_rad)

    return fit_distance / fill_ratio


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
