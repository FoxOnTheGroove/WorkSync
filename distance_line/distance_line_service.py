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
        """전역. VERTEX 는 메시 외곽선의 꼭지점, EDGE 는 외곽선 위의 점입니다.

        내부 정점과 내부 엣지는 후보가 아닙니다. SURFACE 가 항상 폴백입니다.
        """
        DistanceLineCore.set_snap_mode(mode)

    @classmethod
    def set_snap_radius(cls, pixels: float) -> None:
        """스냅 반경(픽셀). 기본 12."""
        DistanceLineCore.set_snap_radius(pixels)

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> int:
        """다음 두 번의 클릭으로 직선 하나를 놓고, 그 직선의 키를 반환합니다.

        키는 미리 떼어 주므로 클릭을 기다리지 않고 바로 받습니다. 취소되면
        그 번호는 비게 됩니다. 무장에 실패하면 0 입니다.

        활성 탭의 모든 뷰포트에서 시작할 수 있고, 첫 점이 찍힌 뷰포트만
        두 번째 점을 받습니다. id 를 주면 그 뷰포트만 무장합니다.
        on_done(line) 은 완료 시점 훅이며 취소되면 불리지 않습니다.
        """
        return DistanceLineCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id=None) -> None:
        """진행 중인 픽을 취소합니다. id 를 안 주면 전부."""
        DistanceLineCore.cancel_pick(viewport_id)

    @classmethod
    def get_lines(cls, viewport_id=None, tab_id=None) -> tuple:
        """전체, 또는 뷰포트/탭으로 좁힌 목록.

        Line.id 는 영구 키(지목용), Line.number 는 뷰포트 내 순번(표시용)
        입니다. number 는 삭제되면 뒤가 당겨집니다.
        """
        return DistanceLineCore.get_lines(viewport_id, tab_id)

    @classmethod
    def remove(cls, line_id: int) -> bool:
        """고유 id 로 하나 제거. 없으면 False."""
        return DistanceLineCore.remove(line_id)

    @classmethod
    def clear(cls, viewport_id=None, tab_id=None) -> None:
        """전체, 또는 뷰포트/탭 단위로 비우기."""
        DistanceLineCore.clear(viewport_id, tab_id)

    @classmethod
    def set_visible(
        cls, visible: bool, line_id=None, viewport_id=None, tab_id=None
    ) -> None:
        """좁은 것부터: line_id / viewport_id / tab_id / 전역.

        사용자 의도만 정합니다. 비활성 탭은 별도로 그리기가 막힙니다.
        """
        DistanceLineCore.set_visible(
            visible, line_id=line_id, viewport_id=viewport_id, tab_id=tab_id
        )

    @classmethod
    def status(cls) -> dict:
        """현재 상태 전부를 dict 하나로.

        snap_mode, snap_radius, host_input, active_tab, selected_viewport,
        selected_line, picking, tabs, maximized
        """
        return DistanceLineCore.status()

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        """추가/제거/가시성/탭 변경 후 호출됩니다. 인자 없음.

        반환된 핸들을 살려 두어야 하고, 놓으면 구독이 해지됩니다.
        """
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
