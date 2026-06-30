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
    BORDER_RADIUS = 8   # 모서리 곡률(px)

    # 이동/리사이즈/도킹/접기 등 마우스 조작 전부 비활성 + 윈도우 기본 배경 제거
    # (NO_BACKGROUND 로 프레임 바깥 배경을 없애야 둥근 모서리가 깔끔하게 남음)
    _WIN_FLAGS = (
        ui.WINDOW_FLAGS_NO_TITLE_BAR
        | ui.WINDOW_FLAGS_NO_RESIZE
        | ui.WINDOW_FLAGS_NO_SCROLLBAR
        | ui.WINDOW_FLAGS_NO_MOVE
        | ui.WINDOW_FLAGS_NO_DOCKING
        | ui.WINDOW_FLAGS_NO_COLLAPSE
        | ui.WINDOW_FLAGS_NO_CLOSE
        | ui.WINDOW_FLAGS_NO_BACKGROUND
    )

    # key -> (window, bar, label, tab_key)
    _items: dict = {}
    # 개별 hide 된 key 집합
    _hidden: set = set()
    # 전역 패널 on/off (setting). off 면 어떤 패널도 노출 안 함
    _enabled: bool = True

    @classmethod
    def _apply_visibility(cls, key: str):
        """실제 표시 = 전역 enabled AND 개별 미숨김."""
        item = cls._items.get(key)
        if item:
            item[0].visible = cls._enabled and (key not in cls._hidden)

    @classmethod
    def create(cls, tab_key: str, key: str, frame: ui.Frame):
        """frame 하단·가로중앙에 progress 오버레이 생성. 같은 key 는 교체.

        tab_key: 그룹 식별자. hide_all/show_all 이 이 단위로 작용.
        """
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

        # 핑크골드 배경 + 둥근 모서리 (frame style 직접 지정)
        win.frame.set_style({
            "background_color": cls.BG_COLOR,
            "border_radius": cls.BORDER_RADIUS,
        })
        with win.frame:
            with ui.VStack(spacing=2):
                label = ui.Label("")
                bar = ui.ProgressBar()
                bar.model.set_value(0.0)

        cls._items[key] = (win, bar, label, tab_key)
        cls._hidden.discard(key)
        cls._apply_visibility(key)   # 전역 off 면 생성해도 노출 안 됨

        # 노출 상태일 때만 다른 윈도우 위로 올림
        if cls._enabled and hasattr(win, "focus"):
            win.focus()

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
        """해당 key 오버레이를 일시적으로 가림 (제거하지 않음)."""
        cls._hidden.add(key)
        cls._apply_visibility(key)

    @classmethod
    def show(cls, key: str):
        """hide 한 오버레이를 다시 표시 (전역 off 면 여전히 안 보임)."""
        cls._hidden.discard(key)
        cls._apply_visibility(key)

    @classmethod
    def hide_all(cls, tab_key: str):
        """해당 tab_key 그룹의 오버레이만 일시적으로 가림."""
        for key, item in list(cls._items.items()):
            if item[3] == tab_key:
                cls.hide(key)

    @classmethod
    def show_all(cls, tab_key: str):
        """해당 tab_key 그룹의 오버레이만 다시 표시."""
        for key, item in list(cls._items.items()):
            if item[3] == tab_key:
                cls.show(key)

    @classmethod
    def panel_on(cls):
        """[전역 setting] 패널 노출 켬. 개별 hide 상태는 유지."""
        cls._enabled = True
        for key in list(cls._items.keys()):
            cls._apply_visibility(key)

    @classmethod
    def panel_off(cls):
        """[전역 setting] 패널 노출 끔. 모든 패널 숨김 (객체는 유지)."""
        cls._enabled = False
        for key in list(cls._items.keys()):
            cls._apply_visibility(key)

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
        cls._hidden.discard(key)
        item = cls._items.pop(key, None)
        if item:
            item[0].destroy()

    @classmethod
    def destroy_all(cls):
        """모든 오버레이 즉시 제거 (셧다운 정리용)."""
        for key in list(cls._items.keys()):
            cls.destroy_immediate(key)
