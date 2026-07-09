"""streamline 최적화 익스텐션 UI.

파일 경로를 입력하고 Optimize 를 누르면 lines_optimize 가 자동 처리하여
병합된 단일 BasisCurves prim 이 현재 씬에 로드된다.
실제 처리 로직은 모두 lines_optimize.py 에 있다.
"""

import omni.ui as ui

from .lines_optimize import optimize_and_load, MERGED_PATH


class LinesOptimizeUI:
    def __init__(self):
        self._window: ui.Window | None = None
        self._path_field: ui.StringField | None = None
        self._eps_field: ui.FloatField | None = None
        self._width_field: ui.FloatField | None = None
        self._status: ui.Label | None = None

    def build_ui(self):
        self._window = ui.Window("Streamline Optimizer", width=520, height=180)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                with ui.HStack(height=24, spacing=4):
                    ui.Label("USD Path:", width=70)
                    self._path_field = ui.StringField()
                    self._path_field.model.set_value("/path/to/streamline.usd")

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Decimate ε:", width=70)
                    self._eps_field = ui.FloatField(width=90)
                    self._eps_field.model.set_value(0.0)
                    ui.Label("Width:", width=50)
                    self._width_field = ui.FloatField(width=90)
                    self._width_field.model.set_value(0.1)

                ui.Button("Optimize & Load", height=30,
                          clicked_fn=self._on_optimize)

                self._status = ui.Label("Status: 대기 중", word_wrap=True)

    def _on_optimize(self):
        path = self._path_field.model.get_value_as_string().strip()
        eps = self._eps_field.model.get_value_as_float()
        width = self._width_field.model.get_value_as_float()
        self._set_status("처리 중...")
        try:
            msg = optimize_and_load(path, epsilon=eps, width=width)
        except Exception as e:  # noqa: BLE001
            msg = f"ERROR: {e}"
            import traceback
            traceback.print_exc()
        self._set_status(msg)

    def _set_status(self, text: str):
        if self._status:
            self._status.text = f"Status: {text}"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
