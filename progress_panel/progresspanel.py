"""
Progress Panel 구현부 전체.

target frame 의 화면 위치/크기를 읽어, 그 자리에 떠 있는 표시 전용
progress bar 오버레이(별도 ui.Window)를 key 단위로 생성/갱신/파괴한다.

모든 기능은 ProgressPanel 의 @classmethod 로 구현.
외부에서는 progresspanel_service.ProgressPanelService 를 통해 사용할 것.
"""

import asyncio

import omni.ui as ui
import omni.kit.async_engine


class ProgressPanel:

    HEIGHT = 24
    AUTO_DESTROY_DELAY = 1.5   # value 완료(>=1.0) 후 자동 파괴까지 대기(초)

    # key -> (window, bar, label)
    _items: dict = {}

    @classmethod
    def create(cls, key: str, frame: ui.Frame):
        """frame 위치(하단)에 progress 오버레이 생성. 같은 key 는 교체."""
        cls.destroy(key)

        win = ui.Window(
            f"progress_{key}",
            width=frame.computed_width,
            height=cls.HEIGHT,
            flags=ui.WINDOW_FLAGS_NO_TITLE_BAR
                  | ui.WINDOW_FLAGS_NO_RESIZE
                  | ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        win.position_x = frame.screen_position_x
        win.position_y = frame.screen_position_y + frame.computed_height - cls.HEIGHT

        with win.frame:
            with ui.VStack(spacing=2):
                label = ui.Label("")
                bar = ui.ProgressBar()
                bar.model.set_value(0.0)

        cls._items[key] = (win, bar, label)

    @classmethod
    def update(cls, key: str, value: float, desc: str = ""):
        """value(0.0~1.0) 반영 + desc 표기. 완료 시 자동 파괴 예약."""
        item = cls._items.get(key)
        if not item:
            return
        _, bar, label = item
        bar.model.set_value(max(0.0, min(1.0, value)))
        label.text = desc
        if value >= 1.0:
            omni.kit.async_engine.run_coroutine(cls._auto_destroy(key))

    @classmethod
    async def _auto_destroy(cls, key: str):
        await asyncio.sleep(cls.AUTO_DESTROY_DELAY)
        cls.destroy(key)

    @classmethod
    def set_color(cls, key: str, color: int):
        """progress bar fill 색 변경. color 는 omni.ui 정수 (0xAABBGGRR)."""
        item = cls._items.get(key)
        if item:
            item[1].set_style({"color": color})

    @classmethod
    def hide(cls, key: str):
        """해당 key 오버레이를 visible off (제거하지 않음)."""
        item = cls._items.get(key)
        if item:
            item[0].visible = False

    @classmethod
    def show(cls, key: str):
        """hide 한 오버레이를 다시 visible on."""
        item = cls._items.get(key)
        if item:
            item[0].visible = True

    @classmethod
    def hide_all(cls):
        """모든 오버레이를 visible off."""
        for key in list(cls._items.keys()):
            cls.hide(key)

    @classmethod
    def show_all(cls):
        """모든 오버레이를 visible on."""
        for key in list(cls._items.keys()):
            cls.show(key)

    @classmethod
    def destroy(cls, key: str):
        """해당 key 오버레이를 완전히 제거."""
        item = cls._items.pop(key, None)
        if item:
            item[0].destroy()

    @classmethod
    def destroy_all(cls):
        """모든 오버레이 제거 (셧다운 정리용)."""
        for key in list(cls._items.keys()):
            cls.destroy(key)
