"""레이캐스트가 빗나갔을 때 포인트 클라우드에 스냅한다.

points 프림은 면이 없어 레이캐스트에 잘 맞지 않는다. result.valid 가 False 여도
어느 프림인지 알고 있다면, 화면상 커서에 가장 가까운 점을 찾아 맞은 것처럼 쓸 수
있다. Hit.index 가 result.primitive_id 자리를 대신한다.

    snap = PointCloudSnap()
    hit = snap.at_ndc(prim, viewport_api, sender.gesture_payload.mouse)
    if hit:
        hit.index, hit.position, hit.color

스테이지가 바뀌면 invalidate() 를 부를 것. 스스로 감지하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pxr import Gf, Usd, UsdGeom

REACH_PX = 50.0                # 이 픽셀 안의 점만 잡는다
_EPS = 1e-12


@dataclass(frozen=True)
class Hit:
    index: int                 # 점 번호. result.primitive_id 에 대응한다
    position: Gf.Vec3d         # 월드 좌표
    screen_gap: float          # 커서에서 몇 픽셀 떨어져 있는지
    color: Gf.Vec3f | None     # displayColor. 없으면 None


class PointCloudSnap:
    def __init__(self, reach_px: float = REACH_PX):
        self.reach_px = float(reach_px)
        self._points: dict = {}     # 프림 경로 -> (타임코드, 월드 좌표, displayColor)
        self._screen: dict = {}     # 프림 경로 -> (카메라 키, 픽셀, 유효, x 정렬 순서)

    # ------------------------------------------------------------------

    def at_ndc(self, prim, viewport_api, ndc, time=None) -> Hit | None:
        """제스처 payload 의 NDC 좌표로 스냅한다."""
        size = _viewport_size(viewport_api)
        cursor = (
            (float(ndc[0]) * 0.5 + 0.5) * size[0],
            (1.0 - (float(ndc[1]) * 0.5 + 0.5)) * size[1],
        )
        return self.at_pixel(prim, viewport_api, cursor, time)

    def at_pixel(self, prim, viewport_api, cursor_px, time=None) -> Hit | None:
        """뷰포트 픽셀 좌표로 스냅한다."""
        matrices = _view_projection(viewport_api)
        if matrices is None:
            return None
        return self.query(prim, cursor_px, matrices, _viewport_size(viewport_api), time)

    def query(self, prim, cursor_px, view_proj, size, time=None) -> Hit | None:
        """카메라 행렬을 직접 넘기는 경로. 위 두 메소드가 이걸 부른다.

        view_proj 는 (뷰 x 투영) 을 곱한 4x4, size 는 뷰포트 픽셀 크기.
        """
        if prim is None or not prim.IsValid():
            return None
        world, colors = self._world_points(prim, time)
        if world is None or not len(world):
            return None

        pixels, valid, order = self._projected(prim, world, view_proj, size)
        near = self._within_reach(pixels, valid, order, cursor_px)
        if near is None:
            return None
        index, gap = near
        return Hit(
            index=int(index),
            position=Gf.Vec3d(*world[index]),
            screen_gap=float(gap),
            color=_colour_at(colors, int(index), len(world)),
        )

    def invalidate(self, prim=None) -> None:
        """프림 하나, 또는 전부. 클라우드가 바뀌거나 스테이지가 바뀔 때 부를 것."""
        if prim is None:
            self._points.clear()
            self._screen.clear()
            return
        path = str(prim.GetPath())
        self._points.pop(path, None)
        self._screen.pop(path, None)

    # ------------------------------------------------------------------

    def _world_points(self, prim, time):
        """월드 좌표와 displayColor. 프림당 한 번만 읽는다."""
        path = str(prim.GetPath())
        cached = self._points.get(path)
        if cached is not None and cached[0] == str(time):
            return cached[1], cached[2]

        local = _attr_value(UsdGeom.PointBased(prim).GetPointsAttr(), time)
        if local is None:
            self._points[path] = (str(time), None, None)
            return None, None

        local = np.asarray(local, dtype=float)
        matrix = _matrix_np(
            UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
                time if time is not None else Usd.TimeCode.Default()
            )
        )
        stacked = np.hstack([local, np.ones((len(local), 1))]) @ matrix
        world = stacked[:, :3] / stacked[:, 3:4]
        colors = _attr_value(UsdGeom.Gprim(prim).GetDisplayColorAttr(), time)
        self._points[path] = (str(time), world, colors)
        return world, colors

    def _projected(self, prim, world, view_proj, size):
        """화면 투영을 x 순으로 정렬해 둔다. 카메라가 그대로면 재사용한다."""
        path = str(prim.GetPath())
        key = (np.asarray(view_proj, dtype=float).tobytes(), tuple(size))
        cached = self._screen.get(path)
        if cached is not None and cached[0] == key:
            return cached[1], cached[2], cached[3]

        pixels, valid = _project_px(world, view_proj, size)
        order = np.argsort(pixels[:, 0], kind="stable")
        self._screen[path] = (key, pixels, valid, order)
        return pixels, valid, order

    def _within_reach(self, pixels, valid, order, cursor_px):
        """x 로 정렬된 배열에서 커서 주변만 잘라 최근접을 고른다.

        전체 점 개수가 아니라 커서 주변에 걸리는 점 개수에만 비례한다.
        """
        cursor = np.asarray(cursor_px, dtype=float)
        sorted_x = pixels[order, 0]
        low = np.searchsorted(sorted_x, cursor[0] - self.reach_px, "left")
        high = np.searchsorted(sorted_x, cursor[0] + self.reach_px, "right")
        if low >= high:
            return None

        picked = order[low:high]
        picked = picked[valid[picked]]
        if not len(picked):
            return None
        spread = pixels[picked] - cursor
        picked = picked[np.abs(spread[:, 1]) <= self.reach_px]
        if not len(picked):
            return None

        gaps = np.linalg.norm(pixels[picked] - cursor, axis=1)
        best = int(np.argmin(gaps))
        if float(gaps[best]) > self.reach_px:
            return None
        return picked[best], gaps[best]


# ----------------------------------------------------------------------


def _project_px(world: np.ndarray, view_proj, size):
    """월드 좌표를 뷰포트 픽셀로. 카메라 뒤의 점은 무효로 표시한다.

    w 가 음수면 카메라 뒤다. 그대로 나누면 좌표가 뒤집혀 화면 앞쪽 그럴듯한
    자리에 찍히고, 실제로 커서 밑에 있는 점을 이겨 버린다.
    """
    matrix = np.asarray(view_proj, dtype=float)
    count = len(world)
    clip = np.hstack([world, np.ones((count, 1))]) @ matrix
    w = clip[:, 3]
    valid = w > _EPS
    ndc = np.zeros((count, 3))
    np.divide(clip[:, :3], w[:, None], out=ndc, where=valid[:, None])
    pixels = np.empty((count, 2))
    pixels[:, 0] = (ndc[:, 0] * 0.5 + 0.5) * size[0]
    pixels[:, 1] = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * size[1]
    return pixels, valid & (np.abs(ndc[:, 2]) <= 1.0)


def _attr_value(attr, time=None):
    """타임코드를 차례로 시도한다. 샘플된 속성은 기본 타임코드에서 비어 있다."""
    if not attr:
        return None
    times = []
    if time is not None:
        times.append(time)
    times.append(Usd.TimeCode.Default())
    times.append(Usd.TimeCode(0.0))
    try:
        samples = attr.GetTimeSamples()
        if samples:
            times.append(Usd.TimeCode(samples[0]))
    except Exception:
        pass
    for when in times:
        value = attr.Get(when)
        if value is not None and len(value):
            return value
    return None


def _colour_at(colors, index: int, count: int):
    """displayColor 는 점마다 하나일 수도, 전체에 하나일 수도 있다."""
    if colors is None or not len(colors):
        return None
    if len(colors) == 1:
        return Gf.Vec3f(colors[0])
    if index < len(colors):
        return Gf.Vec3f(colors[index])
    return Gf.Vec3f(colors[0]) if count else None


def _matrix_np(matrix) -> np.ndarray:
    return np.array([[matrix[r][c] for c in range(4)] for r in range(4)], dtype=float)


def _view_projection(viewport_api):
    """뷰 x 투영. 행벡터 규약이라 곱하는 순서가 이렇다."""
    try:
        view = Gf.Matrix4d(viewport_api.view)
        projection = Gf.Matrix4d(viewport_api.projection)
    except Exception:
        return None
    return _matrix_np(view * projection)


def _viewport_size(viewport_api):
    try:
        resolution = viewport_api.resolution
        return float(resolution[0]), float(resolution[1])
    except Exception:
        return 1920.0, 1080.0
