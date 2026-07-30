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

from .measure import (  # 호출자가 measure.py 를 직접 import 하지 않도록 재수출
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
    """공개 API 전부. 이 목록 밖으로는 아무것도 노출하지 않습니다."""

    # ------------------------------------------------------- 호스트 이벤트

    @classmethod
    def on_tab_created(cls, tab_id: str, vphs) -> tuple:
        """탭 생성됨. vph 들을 등록하고 오버레이를 만들고 활성 탭으로 만듭니다.

        vph.viewport_api.id / vph.tab_id / vph.ui_frame 을 읽습니다.
        뷰포트 입력을 가져가지 않습니다 - 오버레이가 클릭을 잡는 건
        pick_one 이후 두 번째 클릭까지뿐입니다.

        뷰포트 id 들을 반환합니다.
        """
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def on_tab_activated(cls, tab_id) -> None:
        """활성 탭만 그리고 클릭을 받습니다. None 이면 필터 해제."""
        MeasureCore.set_active_tab(tab_id)

    @classmethod
    def on_tab_closed(cls, tab_id: str) -> None:
        """탭의 뷰포트와 거기 그려진 직선을 모두 제거합니다."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def on_viewport_selected(cls, viewport_id: str) -> None:
        """id 없이 부른 pick_one 의 기본 대상이 됩니다."""
        MeasureCore.set_selected_viewport(viewport_id)

    @classmethod
    def on_viewport_maximized(cls, viewport_id: str, maximized: bool = True) -> None:
        """한 뷰포트가 형제들을 덮고 커졌는지 여부. 그 탭 안에서만 적용됩니다."""
        if maximized:
            MeasureCore.set_maximized(viewport_id)
        else:
            MeasureCore.clear_maximized(MeasureCore.get_tab_of(viewport_id))

    @classmethod
    def on_viewport_click(cls, viewport_id: str, x, y=None, space="ndc") -> None:
        """호스트가 잡은 클릭을 넘깁니다.

        좌표는 씬 제스처의 sender.gesture_payload.mouse 를 그대로 주면
        됩니다(NDC). 픽셀이면 space="pixel". (x, y) 개별 인자도 받습니다.

        무장되지 않은 뷰포트의 클릭은 무시하므로 매 클릭 그냥 넘기면 됩니다.
        첫 호출 시 입력 소유권이 호스트로 넘어가고, 오버레이는 그 뒤로
        클릭을 잡지 않습니다.
        """
        MeasureCore.on_external_click(viewport_id, x, y, space)

    @classmethod
    def on_viewport_hover(cls, viewport_id: str, x, y=None, space="ndc") -> None:
        """커서 이동. 스냅 마커와 미리보기 선에만 쓰입니다.

        오버레이가 자체 처리하므로 보통 필요 없습니다. 측정 중이 아니면
        아무 일도 하지 않으니 매 프레임 넘겨도 됩니다.
        """
        MeasureCore.on_external_hover(viewport_id, x, y, space)

    # ---------------------------------------------------------------- 제어

    @classmethod
    def set_snap_mode(cls, mode: SnapMode) -> None:
        """전역입니다. SURFACE 는 항상 폴백으로 깔려 있습니다.

        VERTEX 는 메시 외곽선의 꼭지점, EDGE 는 외곽선 위의 점입니다.
        내부 정점과 내부 엣지는 후보가 아닙니다 - 분할된 plane 이면
        격자점이 아니라 네 모서리만 잡힙니다.
        """
        MeasureCore.set_snap_mode(mode)

    @classmethod
    def set_snap_radius(cls, pixels: float) -> None:
        """스냅 반경(렌더 픽셀). 기본 12. 스냅이 안 걸리면 올려보세요."""
        MeasureCore.set_snap_radius(pixels)

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> None:
        """다음 두 번의 클릭으로 직선 하나를 놓습니다.

        id 를 안 주면 on_viewport_selected 로 들어온 뷰포트를 쓰고, 그것도
        없으면 무장만 해두고 첫 클릭이 들어온 뷰포트가 가져갑니다.

        직선은 알아서 그려지고 등록됩니다. on_done(line) 은 완료 시점에
        추가 동작이 필요할 때만 쓰는 훅이고, 취소되면 불리지 않습니다.
        """
        MeasureCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id=None) -> None:
        """진행 중인 픽을 취소합니다. id 를 안 주면 전부."""
        MeasureCore.cancel_pick(viewport_id)

    # ---------------------------------------------------------------- 직선

    @classmethod
    def get_lines(cls, viewport_id=None, tab_id=None) -> tuple:
        """전체, 또는 뷰포트/탭으로 좁힌 목록."""
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
        """좁은 것부터 4분위:

        line_id     -> 그 직선 하나
        viewport_id -> 그 뷰포트 전체
        tab_id      -> 그 탭 전체
        아무것도 없음 -> 전역

        여기서 정하는 건 사용자 의도뿐입니다. 활성 탭 여부가 별도로
        그리기를 막으므로, 비활성 탭의 직선은 True 로 둬도 안 보입니다.
        """
        MeasureCore.set_visible(
            visible, line_id=line_id, viewport_id=viewport_id, tab_id=tab_id
        )

    # ---------------------------------------------------------------- 조회

    @classmethod
    def status(cls) -> dict:
        """현재 상태 전부를 dict 하나로. 개별 getter 는 두지 않았습니다.

        snap_mode, snap_radius, host_input, active_tab, selected_viewport,
        picking, tabs {탭 id: (뷰포트 id...)}, maximized
        """
        return MeasureCore.status()

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        """추가/제거/비우기/가시성/등록/탭 변경 후에 불립니다.

        인자는 없습니다. get_lines 나 status 를 다시 읽으세요. 반환된 핸들을
        살려 두어야 하고, 놓으면 구독이 해지됩니다.
        """
        return MeasureCore.subscribe_changed(fn)
