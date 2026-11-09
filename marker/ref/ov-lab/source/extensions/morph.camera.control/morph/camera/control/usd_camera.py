"""카메라 prim 읽기/쓰기 (USD). 뷰포트 없이 스테이지만으로 동작한다. 메인 스레드 전용."""
from __future__ import annotations

from typing import Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom

from . import camera_math as cm

COI_ATTR = "omni:kit:centerOfInterest"  # Kit 카메라 관례: 카메라 로컬 좌표의 타깃
DEFAULT_TARGET_DISTANCE = 100.0  # COI가 없을 때 가정하는 타깃 거리

_TIME = Usd.TimeCode.Default()

_AXES = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}
_ORDERS = {
    UsdGeom.XformCommonAPI.RotationOrderXYZ: "XYZ",
    UsdGeom.XformCommonAPI.RotationOrderXZY: "XZY",
    UsdGeom.XformCommonAPI.RotationOrderYXZ: "YXZ",
    UsdGeom.XformCommonAPI.RotationOrderYZX: "YZX",
    UsdGeom.XformCommonAPI.RotationOrderZXY: "ZXY",
    UsdGeom.XformCommonAPI.RotationOrderZYX: "ZYX",
}

Pose = Tuple[cm.Vec3, cm.Vec3, float]  # (eye, target, horizontal fov deg)


def stage_up_vector(stage: Usd.Stage) -> cm.Vec3:
    return (0.0, 0.0, 1.0) if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z else (0.0, 1.0, 0.0)


def is_camera(stage: Usd.Stage, path: str) -> bool:
    if not path or not Sdf.Path.IsValidPathString(path):
        return False
    prim = stage.GetPrimAtPath(path)
    return bool(prim) and prim.IsA(UsdGeom.Camera)


def read_camera_pose(stage: Usd.Stage, path: str) -> Optional[Pose]:
    """(eye, target, fov). prim이 없거나 Xformable이 아니면 None."""
    if not path or not Sdf.Path.IsValidPathString(path):
        return None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsA(UsdGeom.Xformable):
        return None
    world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(_TIME)
    eye = _t(world.ExtractTranslation())
    coi = Gf.Vec3d(0.0, 0.0, -DEFAULT_TARGET_DISTANCE)
    attr = prim.GetAttribute(COI_ATTR)
    if attr and attr.HasValue():
        val = attr.Get(_TIME)
        if val is not None:
            coi = Gf.Vec3d(val[0], val[1], val[2])
    target = _t(world.Transform(coi))
    if cm.distance(eye, target) < cm.MIN_DISTANCE:
        target = _t(world.Transform(Gf.Vec3d(0.0, 0.0, -DEFAULT_TARGET_DISTANCE)))
    fov = 0.0
    if prim.IsA(UsdGeom.Camera):
        fov = float(UsdGeom.Camera(prim).GetCamera(_TIME).GetFieldOfView(Gf.Camera.FOVHorizontal))
    return eye, target, fov


def look_at_matrix(eye: cm.Vec3, target: cm.Vec3, up: cm.Vec3) -> Gf.Matrix4d:
    """카메라 월드 행렬 (행 벡터 관례: 행0=right, 행1=up, 행2=back(-forward), 행3=eye)."""
    r, cu, f = cm.camera_basis(eye, target, up)
    return Gf.Matrix4d(
        r[0], r[1], r[2], 0.0,
        cu[0], cu[1], cu[2], 0.0,
        -f[0], -f[1], -f[2], 0.0,
        eye[0], eye[1], eye[2], 1.0,
    )


def write_camera_pose(stage: Usd.Stage, path: str, eye: cm.Vec3, target: cm.Vec3, up: cm.Vec3) -> None:
    """카메라 prim의 xform과 COI를 써서 (eye, target)을 만든다.

    기존 xform op가 XformCommonAPI 호환(translate/rotate/scale)이면 그 형식을 유지하고,
    아니면 단일 `xformOp:transform`으로 바꾼다. 부모에 scale이 있으면 카메라 local scale로 상쇄해
    카메라 월드 행렬이 정규 직교가 되게 한다. COI는 쓴 뒤의 월드 행렬 역변환으로 계산한다.
    쓰는 레이어는 `_use_session_layer` 참고.
    """
    prim = stage.GetPrimAtPath(path) if path and Sdf.Path.IsValidPathString(path) else None
    if not prim or not prim.IsA(UsdGeom.Xformable):
        raise ValueError(f"not a xformable prim: {path!r}")
    session = stage.GetSessionLayer()
    if session is not None and _use_session_layer(prim, session):
        with Usd.EditContext(stage, session):
            _write(prim, eye, target, up)
    else:
        _write(prim, eye, target, up)


def _use_session_layer(prim: Usd.Prim, session: Sdf.Layer) -> bool:
    """세션 레이어에 쓸지.

    - Kit 세션 카메라(`/OmniverseKit_*`)는 세션 레이어.
    - 그 외에는 `xformOpOrder`의 가장 강한 의견이 세션 레이어에 있을 때만 세션 레이어
      (그렇지 않으면 현재 편집 대상에 써서, 사용자 카메라에 세션 over를 만들어 Kit 내비게이션을 가리지 않게 한다).
    """
    if prim.GetPath().pathString.startswith("/OmniverseKit_"):
        return True
    attr = prim.GetAttribute("xformOpOrder")
    if not attr:
        return False
    stack = attr.GetPropertyStack(_TIME)
    return bool(stack) and stack[0].layer == session


def _write(prim: Usd.Prim, eye: cm.Vec3, target: cm.Vec3, up: cm.Vec3) -> None:
    xf = UsdGeom.Xformable(prim)
    world = look_at_matrix(eye, target, up)
    if xf.GetResetXformStack():
        local = world
    else:
        local = world * xf.ComputeParentToWorldTransform(_TIME).GetInverse()

    if not _write_common(prim, xf, local):
        xf.MakeMatrixXform().Set(local, _TIME)

    # COI는 카메라 로컬 좌표. 실제로 쓰인 월드 행렬 기준으로 target을 로컬로 옮겨 그 거리로 쓴다.
    written = xf.ComputeLocalToWorldTransform(_TIME)
    local_target = written.GetInverse().Transform(Gf.Vec3d(*target))
    dist = max(cm.MIN_DISTANCE, local_target.GetLength())
    attr = prim.GetAttribute(COI_ATTR)
    if not attr:
        attr = prim.CreateAttribute(COI_ATTR, Sdf.ValueTypeNames.Vector3d, True)
    attr.Set(Gf.Vec3d(0.0, 0.0, -dist), _TIME)


def _write_common(prim: Usd.Prim, xf: UsdGeom.Xformable, local: Gf.Matrix4d) -> bool:
    """XformCommonAPI 형식으로 쓰기. 결과를 USD로 다시 계산해 검증하고, 안 맞으면 False."""
    api = UsdGeom.XformCommonAPI(prim)
    if not api:
        return False
    vecs = api.GetXformVectors(_TIME)
    if not vecs:
        return False
    _translate, _rotate, cur_scale, pivot, order = vecs
    if Gf.Vec3d(pivot).GetLength() > 1e-9 or order not in _ORDERS:
        return False
    translation = local.ExtractTranslation()
    # 행 벡터 관례: local 3x3의 행 i = scale_i * 회전 행 i (shear가 없을 때). shear가 있으면 아래 검증에서 걸러진다.
    scale = Gf.Vec3d(*[Gf.Vec3d(local[i][0], local[i][1], local[i][2]).GetLength() for i in range(3)])
    rotation = local.RemoveScaleShear().ExtractRotation()
    need_scale = any(abs(scale[i] - cur_scale[i]) > 1e-6 * max(1.0, abs(scale[i])) for i in range(3))
    seq = _ORDERS[order]
    for axes in (seq, seq[::-1]):
        angles = rotation.Decompose(*[_AXES[a] for a in axes])
        by_axis = dict(zip(axes, angles))
        if not api.SetTranslate(Gf.Vec3d(translation), _TIME):
            return False
        if not api.SetRotate(Gf.Vec3f(by_axis["X"], by_axis["Y"], by_axis["Z"]), order, _TIME):
            return False
        if need_scale and not api.SetScale(Gf.Vec3f(scale[0], scale[1], scale[2]), _TIME):
            return False
        result = xf.GetLocalTransformation(_TIME)
        if _mat3_close(result, local) and cm.vec_close(_t(result.ExtractTranslation()), _t(translation), 1e-6):
            return True
    return False


def _mat3_close(a: Gf.Matrix4d, b: Gf.Matrix4d, rel_tol: float = 1e-5) -> bool:
    """상단 3x3(회전·scale) 비교. 허용오차는 원소 크기에 비례."""
    mag = max(1.0, max(abs(b[i][j]) for i in range(3) for j in range(3)))
    return all(abs(a[i][j] - b[i][j]) <= rel_tol * mag for i in range(3) for j in range(3))


def _t(v) -> cm.Vec3:
    return (float(v[0]), float(v[1]), float(v[2]))
