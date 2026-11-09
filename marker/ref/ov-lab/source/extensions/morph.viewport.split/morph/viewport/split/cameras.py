"""고를 수 있는 카메라 목록 (CONTRACT "카메라 목록", 1.4.0).

순서 규칙(`sort_cameras`)은 순수 파이썬, 스테이지 순회(`list_camera_prims`)는 pxr만 쓴다 (창 의존 없음).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pxr import Usd, UsdGeom

from .layout import SESSION_CAMERAS

__all__ = ["CameraInfo", "BUILTIN_CAMERAS", "sort_cameras", "list_camera_prims"]

# Kit 기본(세션 레이어) 카메라. 목록에서는 이 순서로 사용자 카메라 뒤에 온다.
BUILTIN_CAMERAS: tuple[str, ...] = SESSION_CAMERAS
_BUILTIN_RANK = {path: i for i, path in enumerate(BUILTIN_CAMERAS)}


@dataclass(frozen=True)
class CameraInfo:
    """카메라 prim 하나. `builtin`은 Kit 기본 카메라(`BUILTIN_CAMERAS` 경로) 여부."""

    path: str
    name: str
    builtin: bool

    def to_dict(self) -> dict:
        return {"path": self.path, "name": self.name, "builtin": self.builtin}


def sort_cameras(cameras: Iterable[CameraInfo]) -> list[CameraInfo]:
    """사용자 카메라(경로 문자열 오름차순) 먼저, 기본 카메라(Persp, Top, Front, Right 순) 나중. 중복 경로 제거."""
    seen: set[str] = set()
    unique = []
    for cam in cameras:
        if cam.path not in seen:
            seen.add(cam.path)
            unique.append(cam)
    user = sorted((c for c in unique if not c.builtin), key=lambda c: c.path)
    builtin = sorted((c for c in unique if c.builtin), key=lambda c: _BUILTIN_RANK.get(c.path, len(_BUILTIN_RANK)))
    return user + builtin


def list_camera_prims(stage) -> list[CameraInfo]:
    """스테이지의 `UsdGeom.Camera` prim 전부 (`sort_cameras` 순서).

    `Usd.PrimRange` 기본 predicate(활성·로드됨·정의됨·추상 아님)로 순회한다. instance proxy는 따라가지 않는다
    (인스턴스 안의 카메라는 목록에 없다). 세션 레이어의 카메라(Kit 기본 카메라)도 합성 결과에 있으므로 포함된다.
    목록의 prim은 `adapters.validate_camera_prim`을 통과한다 (절대 prim 경로, 유효, IsA Camera).
    """
    if stage is None:
        return []
    found = []
    for prim in Usd.PrimRange(stage.GetPseudoRoot()):
        if prim.IsA(UsdGeom.Camera):
            path = prim.GetPath().pathString
            found.append(CameraInfo(path=path, name=prim.GetName(), builtin=path in _BUILTIN_RANK))
    return sort_cameras(found)
