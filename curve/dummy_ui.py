"""streamline 복셀 최적화 익스텐션 UI.

파일 경로를 입력하고 Voxelize & Load 를 누르면 lines_optimize 가 자동 처리하여
복셀 다운샘플링된 PointInstancer 가 현재 씬에 로드된다.
실제 처리 로직은 모두 lines_optimize.py 에 있다.
"""

import omni.ui as ui

from .lines_optimize import optimize_and_load, inspect_source


class LinesOptimizeUI:
    def __init__(self):
        self._window: ui.Window | None = None
        self._path_field: ui.StringField | None = None
        self._voxel_field: ui.FloatField | None = None
        self._res_field: ui.IntField | None = None
        self._radius_field: ui.FloatField | None = None
        self._levels_field: ui.IntField | None = None
        self._density_cb: ui.CheckBox | None = None
        self._status: ui.Label | None = None

    def build_ui(self):
        self._window = ui.Window("Streamline Voxel Optimizer", width=560, height=280)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                with ui.HStack(height=24, spacing=4):
                    ui.Label("USD Path:", width=80)
                    self._path_field = ui.StringField()
                    self._path_field.model.set_value("/path/to/streamline.usd")

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Voxel Size:", width=80)
                    self._voxel_field = ui.FloatField(width=90)
                    self._voxel_field.model.set_value(0.0)  # 0 = 자동
                    ui.Label("(0=auto)", width=55)
                    ui.Label("Resolution:", width=70)
                    self._res_field = ui.IntField(width=70)
                    self._res_field.model.set_value(128)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Radius x:", width=80)
                    self._radius_field = ui.FloatField(width=90)
                    self._radius_field.model.set_value(0.5)
                    ui.Label("Color Levels:", width=85)
                    self._levels_field = ui.IntField(width=60)
                    self._levels_field.model.set_value(4)
                    ui.Label("Density→Scale:", width=100)
                    self._density_cb = ui.CheckBox(width=24)

                with ui.HStack(height=30, spacing=6):
                    ui.Button("Inspect", width=90, clicked_fn=self._on_inspect)
                    ui.Button("Voxelize & Load", clicked_fn=self._on_run)

                self._status = ui.Label("Status: 대기 중", word_wrap=True)

    def _on_inspect(self):
        path = self._path_field.model.get_value_as_string().strip()
        self._set_status("진단 중...")
        try:
            msg = inspect_source(path)
        except Exception as e:  # noqa: BLE001
            msg = f"ERROR: {e}"
            import traceback
            traceback.print_exc()
        self._set_status(msg)

    def _on_run(self):
        path = self._path_field.model.get_value_as_string().strip()
        voxel = self._voxel_field.model.get_value_as_float()
        res = self._res_field.model.get_value_as_int()
        radius = self._radius_field.model.get_value_as_float()
        levels = self._levels_field.model.get_value_as_int()
        density = self._density_cb.model.get_value_as_bool()
        self._set_status("처리 중...")
        try:
            msg = optimize_and_load(
                path, voxel_size=voxel, resolution=res,
                radius_factor=radius, density_to_scale=density,
                color_levels=levels)
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
