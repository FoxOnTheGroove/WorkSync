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

    HEIGHT = 50            # 바 패널 높이(px)
    WIDTH_RATIO = 2.0 / 3.0  # 바 패널 너비 = 타겟 프레임 너비의 2/3
    DESTROY_DELAY = 1.0    # destroy() 호출 후 실제 파괴까지 대기(초)

    # 패널 배경색 - 핑크골드 (RGB ~ E8B7AB), omni.ui 는 0xAABBGGRR
    BG_COLOR = 0xFFABB7E8

    # 이동/리사이즈/도킹/접기 등 마우스 조작 전부 비활성
    _WIN_FLAGS = (
        ui.WINDOW_FLAGS_NO_TITLE_BAR
        | ui.WINDOW_FLAGS_NO_RESIZE
        | ui.WINDOW_FLAGS_NO_SCROLLBAR
        | ui.WINDOW_FLAGS_NO_MOVE
        | ui.WINDOW_FLAGS_NO_DOCKING
        | ui.WINDOW_FLAGS_NO_COLLAPSE
        | ui.WINDOW_FLAGS_NO_CLOSE
    )

    # key -> (window, bar, label)
    _items: dict = {}

    @classmethod
    def create(cls, key: str, frame: ui.Frame):
        """frame 하단·가로중앙에 progress 오버레이 생성. 같은 key 는 교체."""
        cls.destroy_immediate(key)

        win_w = frame.computed_width * cls.WIDTH_RATIO

        win = ui.Window(
            f"progress_{key}",
            width=win_w,
            height=cls.HEIGHT,
            flags=cls._WIN_FLAGS,
        )
        # 가로 중앙 정렬 + 프레임 하단에 붙임
        win.position_x = frame.screen_position_x + (frame.computed_width - win_w) / 2.0
        win.position_y = frame.screen_position_y + frame.computed_height - cls.HEIGHT

        with win.frame:
            with ui.ZStack():
                # 핑크골드 배경
                ui.Rectangle(style={"background_color": cls.BG_COLOR})
                with ui.VStack(spacing=2):
                    label = ui.Label("")
                    bar = ui.ProgressBar()
                    bar.model.set_value(0.0)

        # 더미 UI 등 다른 윈도우 위로 올림
        if hasattr(win, "focus"):
            win.focus()

        cls._items[key] = (win, bar, label)

    @classmethod
    def update(cls, key: str, value: float, desc: str = ""):
        """value(0.0~1.0) 반영 + desc 표기."""
        item = cls._items.get(key)
        if not item:
            return
        _, bar, label = item
        bar.model.set_value(max(0.0, min(1.0, value)))
        label.text = desc

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
        """destroy 명령 후 DESTROY_DELAY(1초) 뒤에 제거."""
        omni.kit.async_engine.run_coroutine(cls._destroy_delayed(key))

    @classmethod
    async def _destroy_delayed(cls, key: str):
        await asyncio.sleep(cls.DESTROY_DELAY)
        cls.destroy_immediate(key)

    @classmethod
    def destroy_immediate(cls, key: str):
        """해당 key 오버레이를 즉시 제거."""
        item = cls._items.pop(key, None)
        if item:
            item[0].destroy()

    @classmethod
    def destroy_all(cls):
        """모든 오버레이 즉시 제거 (셧다운 정리용)."""
        for key in list(cls._items.keys()):
            cls.destroy_immediate(key)
