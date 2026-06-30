"""
Progress Panel 공개 API.

다른 익스텐션/로직이 자신의 진행도를 표기할 때 이 클래스만 사용한다.
구현 세부는 progresspanel.ProgressPanel 에 위임.

사용 예:
    from progress_panel.progresspanel_service import ProgressPanelService

    ProgressPanelService.show("my_task", target_frame)
    ProgressPanelService.update("my_task", 0.5, "loading...")
    ProgressPanelService.update("my_task", 1.0, "done")   # 1.5초 뒤 자동 제거
    # hide: 잠깐 가리기(객체 유지) / destroy: 완전 제거
    ProgressPanelService.hide("my_task")
    ProgressPanelService.destroy("my_task")
"""

import omni.ui as ui

from .progresspanel import ProgressPanel


class ProgressPanelService:

    @classmethod
    def show(cls, key: str, frame: ui.Frame):
        """frame 위치에 progress 오버레이를 띄운다."""
        ProgressPanel.create(key, frame)

    @classmethod
    def update(cls, key: str, value: float, desc: str = ""):
        """진행도(0.0~1.0)와 설명 문구를 갱신한다. value=1.0 이면 자동 종료."""
        ProgressPanel.update(key, value, desc)

    @classmethod
    def hide(cls, key: str):
        """progress 오버레이를 visible off 한다 (객체는 유지)."""
        ProgressPanel.hide(key)

    @classmethod
    def show_again(cls, key: str):
        """hide 한 오버레이를 다시 보이게 한다."""
        ProgressPanel.show_again(key)

    @classmethod
    def destroy(cls, key: str):
        """progress 오버레이를 완전히 제거한다."""
        ProgressPanel.destroy(key)

    @classmethod
    def destroy_all(cls):
        """모든 progress 오버레이를 제거한다."""
        ProgressPanel.destroy_all()
