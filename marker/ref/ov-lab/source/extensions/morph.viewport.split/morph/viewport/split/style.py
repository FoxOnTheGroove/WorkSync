"""분할 뷰 창 꾸밈: 도킹 탭바 숨김, 창 안쪽 여백, 도킹 분할선 두께.

값 해석·적용 규칙만 여기 둔다. 어떤 창에 언제 적용할지는 `adapters.KitWindowAdapter`가 정한다.
Kit 의존은 지연 import(`omni.kit.imgui`)뿐이라 순수 파이썬으로 단위 테스트할 수 있다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import carb

__all__ = ["ViewStyle", "read_view_style", "window_attrs", "apply_window_attrs", "DockSplitter"]

_TAG = "[morph.viewport.split]"


def _log_warn(message: str) -> None:
    # 테스트가 patch로 가로챈다.
    carb.log_warn(message)


@dataclass(frozen=True)
class ViewStyle:
    """설정 `hideTabBar` / `viewPadding` / `dockSplitterSize`. None = 건드리지 않음."""

    hide_tab_bar: bool = False
    padding: float | None = None
    splitter_size: float | None = None

    @property
    def styles_windows(self) -> bool:
        return self.hide_tab_bar or self.padding is not None

    @property
    def active(self) -> bool:
        return self.styles_windows or self.splitter_size is not None


def _non_negative_number(value, name: str, warn: Callable[[str], None]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        warn(f"{_TAG} setting {name}={value!r} is not a non-negative number; ignored")
        return None
    return float(value)


def read_view_style(get: Callable[[str], object], root: str, warn: Callable[[str], None] = _log_warn) -> ViewStyle:
    """`get(path)`(carb settings get)로 설정을 읽는다. 잘못된 값은 경고 후 '건드리지 않음'."""
    hide = get(f"{root}/hideTabBar")
    return ViewStyle(
        hide_tab_bar=bool(hide) if hide is not None else False,
        padding=_non_negative_number(get(f"{root}/viewPadding"), "viewPadding", warn),
        splitter_size=_non_negative_number(get(f"{root}/dockSplitterSize"), "dockSplitterSize", warn),
    )


def window_attrs(style: ViewStyle) -> dict[str, object]:
    """뷰 창(omni.ui.Window)에 쓸 속성. 쓰는 순서대로 (탭바는 숨긴 뒤 비활성)."""
    attrs: dict[str, object] = {}
    if style.hide_tab_bar:
        attrs["dock_tab_bar_visible"] = False
        attrs["dock_tab_bar_enabled"] = False  # 사용자가 다시 켜지 못하게
    if style.padding is not None:
        attrs["padding_x"] = style.padding
        attrs["padding_y"] = style.padding
    return attrs


def apply_window_attrs(window, attrs: dict[str, object]) -> tuple[dict[str, object], list[tuple[str, BaseException]]]:
    """값이 다른 속성만 쓴다. 반환: (바꾸기 전 값 — 실제로 바꾼 속성만, 실패 목록).

    읽기·쓰기 예외는 실패 목록에 담고 나머지 속성은 계속 적용한다 (호출자가 경고).
    """
    before: dict[str, object] = {}
    failures: list[tuple[str, BaseException]] = []
    for name, value in attrs.items():
        try:
            current = getattr(window, name)
            if current == value:
                continue
            setattr(window, name, value)
            before[name] = current
        except Exception as exc:  # noqa: BLE001 - 창 API 실패는 레이아웃을 막지 않는다
            failures.append((name, exc))
    return before, failures


def _kit_imgui():
    """(imgui, StyleVar.DockSplitterSize). omni.kit.imgui가 없으면 ImportError."""
    import omni.kit.imgui as kimgui  # noqa: PLC0415 - 선택 의존 (omni.kit.renderer.imgui)

    return kimgui.acquire_imgui(), kimgui.StyleVar.DockSplitterSize


class DockSplitter:
    """ImGui 전역 스타일 `DockSplitterSize`를 한 번 push하고, 정리 때 한 번 pop한다.

    push한 값은 다음 프레임부터 앱의 **모든** 도킹 분할선에 적용된다 (ImGui 전역 스타일).
    pop은 스택 맨 위를 되돌리므로 그 사이 다른 코드가 pop 없이 push한 스타일 변수가 있으면 그쪽이 되돌려진다.
    실패(omni.kit.imgui 없음, API 예외)는 경고 1회 후 다시 시도하지 않는다.
    """

    def __init__(self, size: float | None, imgui_factory: Callable[[], tuple[object, object]] | None = None):
        self.size = size
        self._factory = imgui_factory or _kit_imgui
        self._imgui = None
        self._failed = False

    @property
    def pushed(self) -> bool:
        return self._imgui is not None

    def push(self) -> None:
        if self.size is None or self._imgui is not None or self._failed:
            return
        try:
            imgui, var = self._factory()
            imgui.push_style_var_float(var, float(self.size))
        except Exception as exc:  # noqa: BLE001 - ImportError 포함
            self._failed = True
            _log_warn(f"{_TAG} dockSplitterSize={self.size} not applied (omni.kit.imgui unavailable or failed): {exc!r}")
            return
        self._imgui = imgui

    def pop(self) -> None:
        imgui, self._imgui = self._imgui, None
        if imgui is None:
            return
        try:
            imgui.pop_style_var()
        except Exception as exc:  # noqa: BLE001
            _log_warn(f"{_TAG} restoring dock splitter size failed: {exc!r}")
