"""
Progress Panel 데모/조작용 UI.

공개 API(ProgressPanelService) 만 사용해서 패널을 조작/테스트.

컨트롤:
  - Create  : target frame 위치에 빈 progress 오버레이 생성
  - Run     : 0->100% 자동 진행 (UI 에서 구현하는 자동 동작)
  - Update  : value(0~1) + desc 를 직접 입력해 수동 갱신
  - Hide/Show : visible off / on
  - Set Color : fill 색 변경 (hex 입력)
  - Destroy : 완전 제거
"""

import asyncio

import omni.ui as ui
import omni.kit.async_engine

from .progresspanel_service import ProgressPanelService


class ProgressPanelDemoUI:

    KEY = "demo"

    def __init__(self):
        self._window: ui.Window | None = None
        self._target_frame: ui.Frame | None = None
        self._value_field: ui.FloatField | None = None
        self._desc_field: ui.StringField | None = None
        self._color_field: ui.StringField | None = None

    def build_ui(self):
        self._window = ui.Window("Progress Panel Demo", width=460, height=300)
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 2}):
                # 진행바가 떠오를 대상 영역 (사각형 선으로 프레임 시각화, 임시 타겟)
                self._target_frame = ui.Frame(height=120)
                with self._target_frame:
                    with ui.ZStack():
                        ui.Rectangle(style={
                            "background_color": 0x00000000,   # 투명
                            "border_color":     0xFF888888,
                            "border_width":     1,
                        })
                        ui.Label("target area", alignment=ui.Alignment.CENTER)

                # create / run
                with ui.HStack(height=26, spacing=8):
                    ui.Button("Create", clicked_fn=self._on_create)
                    ui.Button("Run", clicked_fn=self._on_run)

                # update (value + desc)
                with ui.HStack(height=26, spacing=6):
                    ui.Label("value", width=42)
                    self._value_field = ui.FloatField(width=70)
                    self._value_field.model.set_value(0.5)
                    ui.Label("desc", width=36)
                    self._desc_field = ui.StringField()
                    self._desc_field.model.set_value("loading...")
                    ui.Button("Update", width=70, clicked_fn=self._on_update)

                # set color (hex)
                with ui.HStack(height=26, spacing=6):
                    ui.Label("color", width=42)
                    self._color_field = ui.StringField(width=110)
                    self._color_field.model.set_value("0xFF33CC33")
                    ui.Button("Set Color", width=80, clicked_fn=self._on_set_color)

                # hide / show / destroy
                with ui.HStack(height=26, spacing=8):
                    ui.Button("Hide", clicked_fn=self._on_hide)
                    ui.Button("Show", clicked_fn=self._on_show)
                    ui.Button("Destroy (1s)", clicked_fn=self._on_destroy)
                    ui.Button("Destroy Now", clicked_fn=self._on_destroy_now)

                # 전역 setting: panel on/off
                with ui.HStack(height=26, spacing=8):
                    ui.Button("Panel ON", clicked_fn=self._on_panel_on)
                    ui.Button("Panel OFF", clicked_fn=self._on_panel_off)

    # ---------------- callbacks (공개 API 만 사용) ----------------

    def _on_create(self):
        ProgressPanelService.create(self.KEY, self._target_frame)

    def _on_run(self):
        omni.kit.async_engine.run_coroutine(self._run())

    def _on_update(self):
        value = self._value_field.model.get_value_as_float()
        desc = self._desc_field.model.get_value_as_string()
        ProgressPanelService.update(self.KEY, value, desc)

    def _on_set_color(self):
        text = self._color_field.model.get_value_as_string().strip()
        try:
            color = int(text, 0)   # "0xAABBGGRR" 파싱
        except ValueError:
            print(f"[progress_panel] invalid color: {text}")
            return
        ProgressPanelService.set_color(self.KEY, color)

    def _on_hide(self):
        ProgressPanelService.hide(self.KEY)

    def _on_show(self):
        ProgressPanelService.show(self.KEY)

    def _on_destroy(self):
        ProgressPanelService.destroy(self.KEY)

    def _on_destroy_now(self):
        ProgressPanelService.destroy_immediate(self.KEY)

    def _on_panel_on(self):
        ProgressPanelService.panel_on()

    def _on_panel_off(self):
        ProgressPanelService.panel_off()

    async def _run(self):
        ProgressPanelService.create(self.KEY, self._target_frame)
        for i in range(101):
            ProgressPanelService.update(self.KEY, i / 100.0, f"loading {i}%")
            await asyncio.sleep(0.02)

    # ---------------- lifecycle ----------------

    def destroy(self):
        ProgressPanelService.destroy_all()
        if self._window:
            self._window.destroy()
            self._window = None
