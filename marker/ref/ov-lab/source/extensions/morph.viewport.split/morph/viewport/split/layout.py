"""레이아웃 계산 (순수 파이썬, Kit 의존 없음)."""

from __future__ import annotations

import math
from typing import Sequence

__all__ = [
    "LAYOUTS",
    "SESSION_CAMERAS",
    "DEFAULT_SIDE_RATIO",
    "SIDE_RATIO_MIN",
    "SIDE_RATIO_MAX",
    "check_layout",
    "view_count",
    "dock_plan",
    "side_ratio_from_setting",
    "check_side_ratio",
    "default_camera",
]

# 지원 레이아웃. 격자 "<행>x<열>"(view_id 행 우선)과 "1+3"(큰 뷰 하나 + 오른쪽 열 셋).
LAYOUTS: tuple[str, ...] = ("1x1", "1x2", "2x1", "2x2", "1+3")

# 레이아웃별 뷰 개수. 문자열을 해석하지 않고 이 표로만 정한다 (격자가 아닌 레이아웃도 있으므로).
_VIEW_COUNTS: dict[str, int] = {"1x1": 1, "1x2": 2, "2x1": 2, "2x2": 4, "1+3": 4}

# "1+3" 오른쪽 열 폭 (창 전체 폭 대비). 설정 /exts/morph.viewport.split/sideRatio
DEFAULT_SIDE_RATIO = 0.25
SIDE_RATIO_MIN = 0.1
SIDE_RATIO_MAX = 0.5

# Kit 세션 카메라. defaultCameras가 비거나 모자랄 때 view_id 순으로 쓴다.
SESSION_CAMERAS: tuple[str, ...] = (
    "/OmniverseKit_Persp",
    "/OmniverseKit_Top",
    "/OmniverseKit_Front",
    "/OmniverseKit_Right",
)

DockStep = tuple[int, int, str, float]


def check_layout(layout: object) -> str:
    """지원 레이아웃이면 그대로 돌려준다. `LAYOUTS`에 없으면 `ValueError`."""
    if not isinstance(layout, str) or layout not in LAYOUTS:
        raise ValueError(f"unsupported layout {layout!r}; expected one of {LAYOUTS}")
    return layout


def view_count(layout: str) -> int:
    return _VIEW_COUNTS[check_layout(layout)]


def _clamp_side(side_ratio: float) -> float:
    return min(SIDE_RATIO_MAX, max(SIDE_RATIO_MIN, float(side_ratio)))


def dock_plan(layout: str, side_ratio: float = DEFAULT_SIDE_RATIO) -> tuple[DockStep, ...]:
    """추가 뷰(1~)를 어디에 도킹할지. `(view_id, 기준 view_id, 위치, 비율)`을 이 순서대로 하나씩 도킹한다.

    위치는 `"RIGHT"`/`"BOTTOM"`. 비율은 omni.ui `dock_in(target, position, ratio)`의 ratio로,
    **새로 붙는 창(view_id)의 몫**이다 (기준 창이 차지하던 영역 중). omni.ui `workspace_utils`의
    복원 코드가 `ratio = second / (first + second)`로 두 번째(붙는) 창의 몫을 넘긴다.
    `side_ratio`는 "1+3"의 오른쪽 열 폭이며 0.1..0.5로 자른다.
    """
    check_layout(layout)
    if layout == "1x1":
        return ()
    if layout == "1x2":
        return ((1, 0, "RIGHT", 0.5),)
    if layout == "2x1":
        return ((1, 0, "BOTTOM", 0.5),)
    if layout == "2x2":
        # 0 1 / 2 3
        return ((1, 0, "RIGHT", 0.5), (2, 0, "BOTTOM", 0.5), (3, 1, "BOTTOM", 0.5))
    # "1+3": 0 | 1/2/3 (오른쪽 열을 세로로 3등분)
    return (
        (1, 0, "RIGHT", _clamp_side(side_ratio)),
        (2, 1, "BOTTOM", 2.0 / 3.0),  # 1이 차지한 열의 아래 2/3 → 1은 1/3
        (3, 2, "BOTTOM", 0.5),  # 2가 차지한 2/3의 아래 절반 → 2, 3 각 1/3
    )


def side_ratio_from_setting(value: object) -> tuple[float, str | None]:
    """설정 값 → (적용 비율, 경고 문구 또는 None).

    None(설정 없음) → 기본값. 숫자가 아니거나(bool 포함) 유한하지 않으면 기본값 + 경고.
    0.1..0.5 밖이면 잘라내고 경고.
    """
    if value is None:
        return DEFAULT_SIDE_RATIO, None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return DEFAULT_SIDE_RATIO, f"sideRatio {value!r} is not a number; using {DEFAULT_SIDE_RATIO}"
    ratio = float(value)
    clamped = _clamp_side(ratio)
    if clamped != ratio:
        return clamped, f"sideRatio {value!r} is out of range {SIDE_RATIO_MIN}..{SIDE_RATIO_MAX}; using {clamped}"
    return ratio, None


def check_side_ratio(value: object) -> float:
    """API 입력(`set_side_ratio`) → 적용 비율. 숫자가 아니거나(bool 포함) 유한하지 않으면 `ValueError`.

    0.1..0.5 밖이면 경고 없이 잘라서 돌려준다 (설정 값 해석 `side_ratio_from_setting`과 달리 기본값으로 바꾸지 않는다).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"side ratio must be a number, got {value!r}")
    if isinstance(value, int):
        value = max(0, min(1, value))  # 아주 큰 정수도 float 변환 전에 범위로 (OverflowError 방지)
    ratio = float(value)
    if not math.isfinite(ratio):
        raise ValueError(f"side ratio must be finite, got {value!r}")
    return _clamp_side(ratio)


def default_camera(view_id: int, default_cameras: Sequence[str] = ()) -> str:
    """새 뷰·기본값 복귀에 쓰는 카메라. defaultCameras 우선, 없으면 세션 카메라."""
    if 0 <= view_id < len(default_cameras):
        path = default_cameras[view_id]
        if isinstance(path, str) and path:
            return path
    if 0 <= view_id < len(SESSION_CAMERAS):
        return SESSION_CAMERAS[view_id]
    return SESSION_CAMERAS[0]
