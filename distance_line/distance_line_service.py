from __future__ import annotations

from .distance_line import (
    Line,
    DistanceLineCore,
    SnapKind,
    SnapMode,
    SnapPoint,
    Subscription,
)

__all__ = [
    "DistanceLineService",
    "SnapMode",
    "SnapKind",
    "SnapPoint",
    "Line",
    "Subscription",
]


class DistanceLineService:
    @classmethod
    def set_snap_mode(cls, mode: SnapMode) -> None:
        DistanceLineCore.set_snap_mode(mode)

    @classmethod
    def set_snap_radius(cls, pixels: float) -> None:
        DistanceLineCore.set_snap_radius(pixels)

    @classmethod
    def set_cloud_snap(cls, enabled: bool) -> None:
        """포인트 클라우드 스냅 on/off. 전역이며 스냅 모드와 별개입니다."""
        DistanceLineCore.set_cloud_snap(enabled)

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> int:
        """다음 두 번의 클릭으로 직선 하나. 그 직선의 key 를 미리 반환한다.

        활성 탭 전체에서 시작 가능, 첫 점을 받은 뷰포트만 두 번째 점을 받는다.
        """
        return DistanceLineCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id=None) -> None:
        DistanceLineCore.cancel_pick(viewport_id)

    @classmethod
    def get_lines(cls, viewport_id=None, tab_id=None) -> tuple:
        return DistanceLineCore.get_lines(viewport_id, tab_id)

    @classmethod
    def remove(cls, line_id: int) -> bool:
        return DistanceLineCore.remove(line_id)

    @classmethod
    def clear(cls, viewport_id=None, tab_id=None) -> None:
        """비우기 범위: viewport_id > tab_id > 전역."""
        DistanceLineCore.clear(viewport_id, tab_id)

    @classmethod
    def set_visible(
        cls, visible: bool, line_id=None, viewport_id=None, tab_id=None
    ) -> None:
        """적용 범위: line_id > viewport_id > tab_id > 전역. line_id 는 key."""
        DistanceLineCore.set_visible(
            visible, line_id=line_id, viewport_id=viewport_id, tab_id=tab_id
        )

    @classmethod
    def status(cls) -> dict:
        return DistanceLineCore.status()

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        return DistanceLineCore.subscribe_changed(fn)

    # extension.

    @classmethod
    def on_tab_created(cls, tab_id: str, vphs) -> tuple:
        return DistanceLineCore.register_tab(tab_id, vphs)

    @classmethod
    def on_tab_activated(cls, tab_id) -> None:
        DistanceLineCore.set_active_tab(tab_id)

    @classmethod
    def on_tab_closed(cls, tab_id: str) -> None:
        DistanceLineCore.unregister_tab(tab_id)

    @classmethod
    def on_viewport_selected(cls, viewport_id: str) -> None:
        DistanceLineCore.set_selected_viewport(viewport_id)

    @classmethod
    def on_viewport_maximized(cls, viewport_id: str, maximized: bool = True) -> None:
        if maximized:
            DistanceLineCore.set_maximized(viewport_id)
        else:
            DistanceLineCore.clear_maximized(DistanceLineCore.get_tab_of(viewport_id))

    @classmethod
    def on_viewport_click(cls, viewport_id: str, x, y=None, space="ndc") -> None:
        DistanceLineCore.on_external_click(viewport_id, x, y, space)

    @classmethod
    def on_viewport_hover(cls, viewport_id: str, x, y=None, space="ndc") -> None:
        DistanceLineCore.on_external_hover(viewport_id, x, y, space)
