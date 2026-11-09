"""카메라 수학 (순수 파이썬, USD/뷰포트 의존 없음).

좌표는 모두 월드 좌표 `(x, y, z)` 튜플. 카메라는 USD 관례대로 로컬 -Z를 바라보고 +Y가 위다.
"""
from __future__ import annotations

import math
from typing import Sequence, Tuple

Vec3 = Tuple[float, float, float]

EPS = 1e-9
MIN_DISTANCE = 1e-4  # eye-target 최소 거리
MAX_PITCH_DEG = 89.0  # 오빗 시 up 축과 이루는 고도각 한계


def vec3(v: Sequence[float]) -> Vec3:
    """길이 3 시퀀스를 유한한 float 튜플로 변환. 아니면 ValueError."""
    try:
        if len(v) != 3:
            raise ValueError(f"expected 3 components, got {len(v)}")
        out = (float(v[0]), float(v[1]), float(v[2]))
    except TypeError as e:
        raise ValueError(f"expected a 3-component vector: {e}") from None
    if not all(math.isfinite(c) for c in out):
        raise ValueError("vector components must be finite")
    return out


def is_finite(v: Sequence[float]) -> bool:
    return all(math.isfinite(c) for c in v)


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec3, fallback: Vec3 = (0.0, 0.0, -1.0)) -> Vec3:
    n = length(a)
    if n < EPS:
        return fallback
    return (a[0] / n, a[1] / n, a[2] / n)


def any_perpendicular(u: Vec3) -> Vec3:
    """u에 수직인 단위 벡터 하나."""
    axis = (1.0, 0.0, 0.0) if abs(u[0]) < 0.9 else (0.0, 1.0, 0.0)
    return normalize(cross(u, axis))


def camera_basis(eye: Vec3, target: Vec3, up: Vec3) -> Tuple[Vec3, Vec3, Vec3]:
    """(right, camera_up, forward) 정규 직교 기저. forward = eye→target.

    forward가 up과 평행하면 right는 up에 수직인 임의 축.
    """
    u = normalize(up, (0.0, 1.0, 0.0))
    f = normalize(sub(target, eye))
    r = cross(f, u)
    if length(r) < 1e-6:
        r = any_perpendicular(u)
    r = normalize(r)
    cu = normalize(cross(r, f))
    return r, cu, f


def distance(a: Vec3, b: Vec3) -> float:
    return length(sub(a, b))


def orbit(eye: Vec3, target: Vec3, up: Vec3, d_yaw: float, d_pitch: float) -> Tuple[Vec3, Vec3]:
    """target을 중심으로 eye를 회전. target은 그대로.

    - `d_yaw` (deg): up 축 기준 회전. 양수 = up 축 위에서 내려다볼 때 반시계 방향(오른손 법칙).
    - `d_pitch` (deg): 고도각 변화. 양수 = eye가 위로 (아래를 더 내려다봄).
      고도각은 ±MAX_PITCH_DEG를 넘어가지 않는다 (이미 넘어 있으면 더 멀어지지만 않게).
    """
    offset = sub(eye, target)
    r = length(offset)
    if r < EPS:
        return eye, target
    u = normalize(up, (0.0, 1.0, 0.0))
    d = scale(offset, 1.0 / r)
    sin_e = max(-1.0, min(1.0, dot(d, u)))
    elev = math.degrees(math.asin(sin_e))
    h = sub(d, scale(u, sin_e))
    if length(h) < 1e-6:
        h = any_perpendicular(u)
    h = normalize(h)

    yaw = math.radians(d_yaw)
    h = add(scale(h, math.cos(yaw)), scale(cross(u, h), math.sin(yaw)))

    new_elev = elev + d_pitch
    if d_pitch > 0:
        new_elev = min(new_elev, max(MAX_PITCH_DEG, elev))
    elif d_pitch < 0:
        new_elev = max(new_elev, min(-MAX_PITCH_DEG, elev))
    e = math.radians(new_elev)
    new_offset = scale(add(scale(h, math.cos(e)), scale(u, math.sin(e))), r)
    return add(target, new_offset), target


def pan(eye: Vec3, target: Vec3, up: Vec3, dx: float, dy: float) -> Tuple[Vec3, Vec3]:
    """화면 기준 평행 이동 (월드 단위). eye와 target을 같이 옮긴다.

    양수 dx = 카메라가 화면 오른쪽으로, 양수 dy = 카메라가 화면 위쪽으로.
    """
    r, cu, _ = camera_basis(eye, target, up)
    delta = add(scale(r, dx), scale(cu, dy))
    return add(eye, delta), add(target, delta)


def zoom(eye: Vec3, target: Vec3, factor: float) -> Tuple[Vec3, Vec3]:
    """target까지 거리에 factor를 곱한다 (factor < 1 = 다가감). 최소 거리 MIN_DISTANCE."""
    offset = sub(eye, target)
    r = length(offset)
    if r < EPS:
        return eye, target
    new_r = max(MIN_DISTANCE, r * factor)
    return add(target, scale(offset, new_r / r)), target


def ease(s: float) -> float:
    """smoothstep 이징 (0→0, 1→1)."""
    s = max(0.0, min(1.0, s))
    return s * s * (3.0 - 2.0 * s)


def lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def rotate_about(v: Vec3, axis: Vec3, angle_rad: float) -> Vec3:
    """단위 축 axis 기준 회전 (Rodrigues)."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return add(add(scale(v, c), scale(cross(axis, v), s)), scale(axis, dot(axis, v) * (1.0 - c)))


def interpolate(
    start_eye: Vec3,
    start_target: Vec3,
    end_eye: Vec3,
    end_target: Vec3,
    s: float,
    up: Vec3 = (0.0, 1.0, 0.0),
) -> Tuple[Vec3, Vec3]:
    """진행률 s(0..1)에서의 (eye, target). smoothstep 이징.

    target은 직선 보간. eye는 target 기준 방향을 구면 보간(slerp)하고 거리를 선형 보간하므로,
    eye 경로가 target을 관통해 eye == target이 되는 일이 없다. 방향이 정반대면 up 축 쪽으로 돈다.
    """
    t = ease(s)
    target = lerp(start_target, end_target, t)
    d0, d1 = sub(start_eye, start_target), sub(end_eye, end_target)
    r0, r1 = length(d0), length(d1)
    if r0 < EPS or r1 < EPS:
        return lerp(start_eye, end_eye, t), target
    u0, u1 = scale(d0, 1.0 / r0), scale(d1, 1.0 / r1)
    c = max(-1.0, min(1.0, dot(u0, u1)))
    angle = math.acos(c)
    if angle < 1e-6:
        direction = normalize(lerp(u0, u1, t), u0)
    else:
        axis = cross(u0, u1)
        if length(axis) < 1e-6:  # 정반대: up 방향으로 지나가도록 up의 u0 수직 성분과의 평면에서 회전
            upn = normalize(up, (0.0, 1.0, 0.0))
            perp = sub(upn, scale(u0, dot(upn, u0)))
            if length(perp) < 1e-6:
                perp = any_perpendicular(u0)
            axis = cross(u0, normalize(perp))
        direction = rotate_about(u0, normalize(axis), angle * t)
    return add(target, scale(direction, r0 + (r1 - r0) * t)), target


def vec_close(a: Vec3, b: Vec3, tol: float = 1e-6) -> bool:
    """크기 비례 허용오차로 비교."""
    mag = max(1.0, length(a), length(b))
    return length(sub(a, b)) <= tol * mag
