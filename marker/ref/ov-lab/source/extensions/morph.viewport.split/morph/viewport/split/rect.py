"""뷰 화면 영역(rect) 계산 (순수 파이썬, Kit 의존 없음).

웹 `morph.viewport.split.state`의 `View.rect`: 앱 메인 창 크기 기준 0..1 정규화, 원점 좌상단.
"""

from __future__ import annotations

import math
from typing import Sequence

__all__ = ["RECT_EPSILON", "RECT_DIGITS", "normalize_rect", "rects_differ"]

# 이 값 미만의 변화는 무시한다 (분할선 드래그 중 흔들림, 부동소수 오차).
RECT_EPSILON = 0.002
# 송신 값의 소수 자릿수 (1e-5 ≈ 4K 폭에서 0.04px)
RECT_DIGITS = 5


def _finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def normalize_rect(x, y, w, h, main_w, main_h) -> dict | None:
    """창 영역(x, y, w, h)을 메인 창(main_w × main_h) 기준 0..1로. 단위는 둘이 같아야 한다.

    메인 창 밖으로 나간 부분은 잘라낸다. 값이 숫자가 아니거나 유한하지 않거나, 크기가 0 이하이거나,
    잘라낸 뒤 남는 영역이 없으면 None.
    """
    x, y, w, h, main_w, main_h = (_finite(v) for v in (x, y, w, h, main_w, main_h))
    if None in (x, y, w, h, main_w, main_h):
        return None
    if main_w <= 0 or main_h <= 0 or w <= 0 or h <= 0:
        return None
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(main_w, x + w), min(main_h, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return {
        "x": round(x0 / main_w, RECT_DIGITS),
        "y": round(y0 / main_h, RECT_DIGITS),
        "w": round((x1 - x0) / main_w, RECT_DIGITS),
        "h": round((y1 - y0) / main_h, RECT_DIGITS),
    }


def _rect_differs(a: dict | None, b: dict | None, eps: float) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return any(abs(float(a[k]) - float(b[k])) >= eps for k in ("x", "y", "w", "h"))


def rects_differ(
    old: Sequence[dict | None] | None, new: Sequence[dict | None], eps: float = RECT_EPSILON
) -> bool:
    """뷰 rect 목록이 의미 있게 바뀌었는지. 개수가 다르거나, null↔값, 어느 성분이든 `eps` 이상 차이."""
    if old is None or len(old) != len(new):
        return True
    return any(_rect_differs(a, b, eps) for a, b in zip(old, new))
