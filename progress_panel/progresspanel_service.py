"""
Progress Panel 공개 API.

다른 익스텐션/로직이 자신의 진행도를 표기할 때 이 클래스만 사용한다.
구현 세부는 progresspanel.ProgressPanel 에 위임.

사용 예:
    from progress_panel.progresspanel_service import ProgressPanelService

    ProgressPanelService.create("my_task", target_frame)
    ProgressPanelService.update("my_task", 0.5, "loading...")
    ProgressPanelService.update("my_task", 1.0, "done")   # 1.5초 뒤 자동 제거
    # hide/show: 가리기/다시 보이기(객체 유지) / destroy: 완전 제거
    ProgressPanelService.hide("my_task")
    ProgressPanelService.show("my_task")
    ProgressPanelService.destroy("my_task")
"""

import omni.ui as ui

from .progresspanel import ProgressPanel


class ProgressPanelService:

    @classmethod
    def create(cls, tab_key: str, key: str, frame: ui.Frame):
        """frame 위치에 progress 오버레이를 생성한다. tab_key 는 그룹 식별자."""
        ProgressPanel.create(tab_key, key, frame)

    @classmethod
    def update(cls, key: str, value: float, desc: str = ""):
        """진행도(0.0~1.0)와 설명 문구를 갱신한다. value=1.0 이면 자동 종료."""
        ProgressPanel.update(key, value, desc)

    @classmethod
    def set_color(cls, key: str, color: int):
        """progress bar fill 색을 변경한다. color 는 omni.ui 정수 (0xAABBGGRR)."""
        ProgressPanel.set_color(key, color)

    @classmethod
    def hide(cls, key: str):
        """progress 오버레이를 visible off 한다 (객체는 유지)."""
        ProgressPanel.hide(key)

    @classmethod
    def show(cls, key: str):
        """hide 한 오버레이를 다시 보이게 한다."""
        ProgressPanel.show(key)

    @classmethod
    def hide_all(cls, tab_key: str):
        """해당 tab_key 그룹의 오버레이만 visible off 한다."""
        ProgressPanel.hide_all(tab_key)

    @classmethod
    def show_all(cls, tab_key: str):
        """해당 tab_key 그룹의 오버레이만 visible on 한다."""
        ProgressPanel.show_all(tab_key)

    @classmethod
    def panel_on(cls):
        """[전역 setting] 패널 노출을 켠다."""
        ProgressPanel.panel_on()

    @classmethod
    def panel_off(cls):
        """[전역 setting] 패널 노출을 끈다. off 면 어떤 패널도 안 보인다."""
        ProgressPanel.panel_off()

    @classmethod
    def destroy(cls, key: str):
        """progress 오버레이를 1초 뒤에 제거한다."""
        ProgressPanel.destroy(key)

    @classmethod
    def destroy_immediate(cls, key: str):
        """progress 오버레이를 즉시 제거한다."""
        ProgressPanel.destroy_immediate(key)

    @classmethod
    def destroy_all(cls):
        """모든 progress 오버레이를 즉시 제거한다 (셧다운 정리용)."""
        ProgressPanel.destroy_all()
