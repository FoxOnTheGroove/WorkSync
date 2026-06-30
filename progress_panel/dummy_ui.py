"""
Progress Panel 데모/조작용 UI.

공개 API(ProgressPanelService) 만 사용해서 패널을 조작하는 예시.
Run 버튼을 누르면 target frame 위치에 progress 오버레이가 떠서 0->100% 진행.
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

    def build_ui(self):
        self._window = ui.Window("Progress Panel Demo", width=400, height=200)
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 2}):
                # 진행바가 떠오를 대상 영역
                self._target_frame = ui.Frame(height=120)
                with self._target_frame:
                    ui.Label("target area", alignment=ui.Alignment.CENTER)

                with ui.HStack(height=28, spacing=8):
                    ui.Button("Run", clicked_fn=self._on_run)
                    ui.Button("Hide", clicked_fn=self._on_hide)

    # ---------------- callbacks (공개 API 만 사용) ----------------

    def _on_run(self):
        omni.kit.async_engine.run_coroutine(self._fake_progress())

    def _on_hide(self):
        ProgressPanelService.hide(self.KEY)

    async def _fake_progress(self):
        ProgressPanelService.show(self.KEY, self._target_frame)
        for i in range(101):
            ProgressPanelService.update(self.KEY, i / 100.0, f"loading {i}%")
            await asyncio.sleep(0.02)

    # ---------------- lifecycle ----------------

    def destroy(self):
        ProgressPanelService.hide_all()
        if self._window:
            self._window.destroy()
            self._window = None
