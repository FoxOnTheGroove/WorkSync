"""measure 툴의 공개 API.

패키지 밖에서는 MeasureService 만 쓰면 됩니다. 상태는 전부 measure.py 가
들고 있고 여기는 위임만 합니다.

뷰포트는 ViewportAPI.id 로 구분하고, 그 뷰포트를 소유한 탭으로 묶입니다.
탭 하나에 vph(뷰포트 위젯 호스트)가 1, 2, 4개 들어갑니다.

연동은 호스트 이벤트를 물리는 것뿐입니다:

    MeasureService.on_tab_created(tab_id, vphs)
    MeasureService.on_tab_activated(tab_id)
    MeasureService.on_tab_closed(tab_id)
    MeasureService.on_viewport_selected(viewport_id)
    MeasureService.on_viewport_maximized(viewport_id, True/False)
    MeasureService.on_viewport_click(viewport_id, sender.gesture_payload.mouse)

측정은 두 줄입니다:

    MeasureService.set_snap_mode(SnapMode.VERTEX | SnapMode.EDGE)
    MeasureService.pick_one()          # 다음 두 번의 클릭으로 직선 하나
"""

from __future__ import annotations

from .measure import (
    Line,
    MeasureCore,
    SnapKind,
    SnapMode,
    SnapPoint,
    Subscription,
)

__all__ = [
    "MeasureService",
    "SnapMode",
    "SnapKind",
    "SnapPoint",
    "Line",
    "Subscription",
]


class MeasureService:
    @classmethod
    def on_tab_created(cls, tab_id: str, vphs) -> tuple:
        """탭 생성. vph 들을 등록하고 오버레이를 만들며 활성 탭이 됩니다."""
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def on_tab_activated(cls, tab_id) -> None:
        """활성 탭만 그리고 클릭을 받습니다."""
        MeasureCore.set_active_tab(tab_id)

    @classmethod
    def on_tab_closed(cls, tab_id: str) -> None:
        """탭의 뷰포트와 거기 그려진 직선을 모두 제거합니다."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def on_viewport_selected(cls, viewport_id: str) -> None:
        """내부 참고용. pick_one 은 이제 활성 탭 전체를 대상으로 합니다."""
        MeasureCore.set_selected_viewport(viewport_id)

    @classmethod
    def on_viewport_maximized(cls, viewport_id: str, maximized: bool = True) -> None:
        """한 뷰포트가 형제들을 덮고 커졌는지. 그 탭 안에서만 적용됩니다."""
        if maximized:
            MeasureCore.set_maximized(viewport_id)
        else:
            MeasureCore.clear_maximized(MeasureCore.get_tab_of(viewport_id))

    @classmethod
    def on_viewport_click(cls, viewport_id: str, x, y=None, space="ndc") -> None:
        """호스트가 잡은 클릭을 넘깁니다.

        좌표는 sender.gesture_payload.mouse 를 그대로 주면 됩니다(NDC).
        픽셀이면 space="pixel". 무장되지 않은 클릭은 무시하니 매번
        넘겨도 됩니다.
        """
        MeasureCore.on_external_click(viewport_id, x, y, space)

    @classmethod
    def on_viewport_hover(cls, viewport_id: str, x, y=None, space="ndc") -> None:
        """커서 이동. 스냅 마커와 미리보기용이며 보통은 필요 없습니다."""
        MeasureCore.on_external_hover(viewport_id, x, y, space)

    @classmethod
    def set_snap_mode(cls, mode: SnapMode) -> None:
        """전역. VERTEX 는 메시 외곽선의 꼭지점, EDGE 는 외곽선 위의 점입니다.

        내부 정점과 내부 엣지는 후보가 아닙니다. SURFACE 가 항상 폴백입니다.
        """
        MeasureCore.set_snap_mode(mode)

    @classmethod
    def set_snap_radius(cls, pixels: float) -> None:
        """스냅 반경(픽셀). 기본 12."""
        MeasureCore.set_snap_radius(pixels)

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> None:
        """다음 두 번의 클릭으로 직선 하나를 놓습니다.

        활성 탭의 모든 뷰포트에서 시작할 수 있고, 첫 점이 찍힌 뷰포트만
        두 번째 점을 받습니다. id 를 주면 그 뷰포트만 무장합니다.
        on_done(line) 은 완료 시점 훅이며 취소되면 불리지 않습니다.
        """
        MeasureCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id=None) -> None:
        """진행 중인 픽을 취소합니다. id 를 안 주면 전부."""
        MeasureCore.cancel_pick(viewport_id)

    @classmethod
    def get_lines(cls, viewport_id=None, tab_id=None) -> tuple:
        """전체, 또는 뷰포트/탭으로 좁힌 목록.

        Line.id 는 영구 키(지목용), Line.number 는 뷰포트 내 순번(표시용)
        입니다. number 는 삭제되면 뒤가 당겨집니다.
        """
        return MeasureCore.get_lines(viewport_id, tab_id)

    @classmethod
    def remove(cls, line_id: int) -> bool:
        """고유 id 로 하나 제거. 없으면 False."""
        return MeasureCore.remove(line_id)

    @classmethod
    def clear(cls, viewport_id=None, tab_id=None) -> None:
        """전체, 또는 뷰포트/탭 단위로 비우기."""
        MeasureCore.clear(viewport_id, tab_id)

    @classmethod
    def set_visible(
        cls, visible: bool, line_id=None, viewport_id=None, tab_id=None
    ) -> None:
        """좁은 것부터: line_id / viewport_id / tab_id / 전역.

        사용자 의도만 정합니다. 비활성 탭은 별도로 그리기가 막힙니다.
        """
        MeasureCore.set_visible(
            visible, line_id=line_id, viewport_id=viewport_id, tab_id=tab_id
        )

    @classmethod
    def status(cls) -> dict:
        """현재 상태 전부를 dict 하나로.

        snap_mode, snap_radius, host_input, active_tab, selected_viewport,
        selected_line, picking, tabs, maximized
        """
        return MeasureCore.status()

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        """추가/제거/가시성/탭 변경 후 호출됩니다. 인자 없음.

        반환된 핸들을 살려 두어야 하고, 놓으면 구독이 해지됩니다.
        """
        return MeasureCore.subscribe_changed(fn)
