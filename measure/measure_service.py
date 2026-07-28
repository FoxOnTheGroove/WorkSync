"""measure 툴의 공개 API.

패키지 밖에서는 MeasureService 만 쓰면 됩니다. 상태는 전부 measure.py 가
들고 있고 여기는 위임만 합니다.

뷰포트는 ViewportAPI.id 로 구분하고, 그 뷰포트를 소유한 탭으로 묶입니다.
탭 하나에 vph(뷰포트 위젯 호스트)가 1, 2, 4개 들어갑니다.

호스트 이벤트만 물리면 나머지는 알아서 돕니다:

    MeasureService.on_tab_created(tab_id, vphs)
    MeasureService.on_tab_activated(tab_id)
    MeasureService.on_tab_closed(tab_id)
    MeasureService.on_viewport_selected(viewport_id)
    MeasureService.on_viewport_maximized(viewport_id)
    MeasureService.on_viewport_minimized(viewport_id)

클릭을 다른 익스텐션이 이미 점유하고 있으면 클릭만 넘겨주면 됩니다.
hover 는 오버레이가 자체적으로 처리합니다:

    MeasureService.on_viewport_click(vp_id, sender.gesture_payload.mouse)

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

    # ------------------------------------------------------------ a. on/off

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        """전역 on/off. 뷰포트별 스위치는 없습니다.

        끄면 새 측정만 막힙니다. 기존 직선은 화면에 남고 set_visible /
        remove / clear 는 그대로 동작합니다.
        """
        MeasureCore.set_enabled(enabled)

    @classmethod
    def is_enabled(cls) -> bool:
        return MeasureCore.is_enabled()

    # -------------------------------------------------------- 등록 / 해제

    @classmethod
    def register_vph(cls, vph) -> str:
        """vph 하나를 등록하고 뷰포트 id 를 반환합니다.

        vph.viewport_api.id / vph.tab_id / vph.ui_frame 을 읽고 오버레이를
        만듭니다. 보통은 on_tab_created 를 통해 불립니다.
        """
        return MeasureCore.register_vph(vph)

    @classmethod
    def register_tab(cls, tab_id: str, vphs) -> tuple:
        """탭과 그 vph 들을 한꺼번에 등록. 뷰포트 id 들을 반환합니다."""
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def unregister_tab(cls, tab_id: str) -> None:
        """탭의 뷰포트와 거기 그려진 직선을 모두 제거합니다."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def unregister_viewport(cls, viewport_id: str) -> None:
        """뷰포트 하나와 그 직선들을 제거합니다."""
        MeasureCore.unregister_viewport(viewport_id)

    @classmethod
    def list_viewport_ids(cls, tab_id=None) -> tuple:
        """등록된 뷰포트 id 전체, 또는 특정 탭에 속한 것만."""
        return MeasureCore.list_viewport_ids(tab_id)

    # ------------------------------------------------------- 호스트 이벤트
    #
    # 탭/뷰포트 이벤트 안에서 호출하세요. 연동에 필요한 건 이 묶음뿐입니다.

    @classmethod
    def on_tab_created(cls, tab_id: str, vphs) -> tuple:
        """탭 생성됨. vph 들을 등록하고 오버레이를 만듭니다.

        뷰포트 입력을 가져가지 않습니다. 오버레이가 클릭을 잡는 건
        pick_one 이후 두 번째 클릭까지뿐입니다.

        활성 탭은 여기서 정하지 않습니다. on_tab_activated 를 따로 부르세요.
        """
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def on_tab_activated(cls, tab_id: str) -> None:
        """탭이 앞으로 나옴. 나머지 탭은 그리기를 멈춥니다."""
        MeasureCore.set_active_tab(tab_id)

    @classmethod
    def on_tab_closed(cls, tab_id: str) -> None:
        """탭이 닫힘. 그 탭의 직선도 함께 사라집니다."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def on_viewport_selected(cls, viewport_id: str) -> None:
        """id 없이 부른 pick_one 의 대상이 됩니다."""
        MeasureCore.set_selected_viewport(viewport_id)

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
        """커서 이동을 넘깁니다. 보통 필요 없습니다.

        hover 는 오버레이가 자체 처리하므로, 그쪽이 안 먹는 경우에만
        쓰세요. 측정 중이 아니면 아무 일도 하지 않습니다.
        """
        MeasureCore.on_external_hover(viewport_id, x, y, space)

    @classmethod
    def on_viewport_maximized(cls, viewport_id: str) -> None:
        """뷰포트가 형제들을 덮고 커짐. 그 탭에선 이것만 그립니다."""
        MeasureCore.set_maximized(viewport_id)

    @classmethod
    def on_viewport_minimized(cls, viewport_id: str) -> None:
        """원래 배치로 복귀. 탭의 모든 뷰포트가 다시 그립니다."""
        MeasureCore.clear_maximized(MeasureCore.get_tab_of(viewport_id))

    # ------------------------------------------------------------- 탭 상태

    @classmethod
    def set_active_tab(cls, tab_id) -> None:
        """활성 탭만 그리고 클릭을 받습니다. None 이면 필터 해제."""
        MeasureCore.set_active_tab(tab_id)

    @classmethod
    def get_active_tab(cls):
        return MeasureCore.get_active_tab()

    @classmethod
    def list_tabs(cls) -> tuple:
        return MeasureCore.list_tabs()

    @classmethod
    def get_tab_of(cls, viewport_id: str) -> str:
        return MeasureCore.get_tab_of(viewport_id)

    @classmethod
    def get_maximized(cls, tab_id: str):
        """그 탭에서 형제를 덮고 있는 뷰포트, 없으면 None."""
        return MeasureCore.get_maximized(tab_id)

    @classmethod
    def get_selected_viewport(cls) -> str:
        return MeasureCore.get_selected_viewport()

    @classmethod
    def set_host_input(cls, host_input: bool) -> None:
        """입력 소유권을 명시적으로 지정. on_viewport_click 이 알아서 켭니다."""
        MeasureCore.set_host_input(host_input)

    @classmethod
    def is_host_input(cls) -> bool:
        return MeasureCore.is_host_input()

    # ------------------------------------------------------------ b. 스냅

    @classmethod
    def set_snap_mode(cls, mode: SnapMode) -> None:
        """전역입니다. SURFACE 는 항상 폴백으로 깔려 있습니다."""
        MeasureCore.set_snap_mode(mode)

    @classmethod
    def get_snap_mode(cls) -> SnapMode:
        return MeasureCore.get_snap_mode()

    @classmethod
    def get_current_snap(cls, viewport_id: str):
        """직전 hover 스냅 결과. 캐시를 읽으므로 쌉니다."""
        return MeasureCore.get_current_snap(viewport_id)

    # -------------------------------------------------------------- c. 픽

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> None:
        """다음 두 번의 클릭으로 직선 하나를 놓습니다.

        id 를 안 주면 on_viewport_selected 로 들어온 뷰포트를 씁니다.
        직선은 알아서 그려지고 등록됩니다. on_done(line) 은 완료 시점에
        추가 동작이 필요할 때만 쓰는 훅이고, 취소되면 불리지 않습니다.
        """
        MeasureCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id=None) -> None:
        MeasureCore.cancel_pick(viewport_id)

    # ------------------------------------------------------------ d. 직선

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

    # ---------------------------------------------------------- e. 가시성

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

    # ------------------------------------------------------------ 변경 통지

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        """추가/제거/비우기/가시성/등록/탭 변경 후에 불립니다.

        인자는 없습니다. get_lines 를 다시 읽으세요. 반환된 핸들을 살려
        두어야 하고, 놓으면 구독이 해지됩니다.
        """
        return MeasureCore.subscribe_changed(fn)
