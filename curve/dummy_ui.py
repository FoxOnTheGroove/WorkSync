"""streamline 복셀 최적화 익스텐션 UI.

파일 경로를 입력하고 Voxelize & Load 를 누르면 lines_optimize 가 자동 처리하여
복셀 다운샘플링된 PointInstancer 가 현재 씬에 로드된다.
최적화 후 Radius 슬라이더로 전체 스피어 크기를 실시간 일괄 조정할 수 있다.
실제 처리 로직은 모두 lines_optimize.py 에 있다.
"""

import asyncio

import omni.ui as ui

from .lines_optimize import (
    optimize_and_load_async, inspect_source, set_sphere_radius,
    _parse_group_paths,
)


class LinesOptimizeUI:
    def __init__(self):
        self._window: ui.Window | None = None
        self._path_field: ui.StringField | None = None
        self._res_field: ui.IntField | None = None
        self._groups_field: ui.StringField | None = None
        self._voxel_field: ui.FloatField | None = None
        self._levels_field: ui.IntField | None = None
        self._density_cb: ui.CheckBox | None = None
        self._radius_slider: ui.FloatSlider | None = None
        self._status: ui.Label | None = None
        self._run_btn: ui.Button | None = None
        self._task: asyncio.Task | None = None

    def build_ui(self):
        self._window = ui.Window("Streamline Voxel Optimizer", width=580, height=360)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                # --- 입력 소스 ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("USD Path:", width=90)
                    self._path_field = ui.StringField()
                    self._path_field.model.set_value("/path/to/streamline.usd")

                # --- 1) 기본: Resolution / Group 대상 경로 ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Resolution:", width=90)
                    self._res_field = ui.IntField(width=70)
                    self._res_field.model.set_value(128)
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Group Paths:", width=90)
                    self._groups_field = ui.StringField()
                    self._groups_field.model.set_value("")  # e.g. /root/target, /root/target2

                # --- 2) 세부 옵션 ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Voxel Size:", width=90)
                    self._voxel_field = ui.FloatField(width=80)
                    self._voxel_field.model.set_value(0.0)  # 0 = 자동
                    ui.Label("(0=auto)", width=55)
                    ui.Label("Color Levels:", width=85)
                    self._levels_field = ui.IntField(width=55)
                    self._levels_field.model.set_value(8)
                    ui.Label("Density→Scale:", width=100)
                    self._density_cb = ui.CheckBox(width=24)

                # --- 실행 ---
                with ui.HStack(height=30, spacing=6):
                    ui.Button("Inspect", width=90, clicked_fn=self._on_inspect)
                    self._run_btn = ui.Button(
                        "Voxelize & Load", clicked_fn=self._on_run)

                # --- 3) 사후 조정: Radius 슬라이더 (라이브) ---
                with ui.HStack(height=24, spacing=6):
                    ui.Label("Sphere Radius:", width=90)
                    self._radius_slider = ui.FloatSlider(min=0.05, max=2.0, step=0.01)
                    self._radius_slider.model.set_value(0.5)
                    self._radius_slider.model.add_value_changed_fn(
                        self._on_radius_changed)

                self._status = ui.Label("Status: idle", word_wrap=True)

    # ------------------------------------------------------------------
    def _on_inspect(self):
        path = self._path_field.model.get_value_as_string().strip()
        self._set_status("Inspecting...")
        try:
            msg = inspect_source(path)
        except Exception as e:  # noqa: BLE001
            msg = f"ERROR: {e}"
            import traceback
            traceback.print_exc()
        self._set_status(msg)

    def _on_run(self):
        if self._task and not self._task.done():
            self._set_status("Already running...")
            return
        path = self._path_field.model.get_value_as_string().strip()
        res = self._res_field.model.get_value_as_int()
        groups = _parse_group_paths(
            self._groups_field.model.get_value_as_string())
        voxel = self._voxel_field.model.get_value_as_float()
        levels = self._levels_field.model.get_value_as_int()
        density = self._density_cb.model.get_value_as_bool()
        radius = self._radius_slider.model.get_value_as_float()
        # 비동기 태스크로 실행 → 메인 스레드(Kit UI)가 멈추지 않음
        self._task = asyncio.ensure_future(self._run_async(
            path, voxel, res, radius, levels, groups, density))

    async def _run_async(self, path, voxel, res, radius, levels, groups, density):
        if self._run_btn:
            self._run_btn.enabled = False
        self._set_status("Processing...")
        try:
            msg = await optimize_and_load_async(
                path, voxel_size=voxel, resolution=res,
                radius_factor=radius, density_to_scale=density,
                color_levels=levels, group_paths=groups,
                progress=self._set_status)
        except Exception as e:  # noqa: BLE001
            msg = f"ERROR: {e}"
            import traceback
            traceback.print_exc()
        finally:
            if self._run_btn:
                self._run_btn.enabled = True
        self._set_status(msg)

    def _on_radius_changed(self, model):
        try:
            msg = set_sphere_radius(model.get_value_as_float())
        except Exception as e:  # noqa: BLE001
            msg = f"ERROR: {e}"
        self._set_status(msg)

    # ------------------------------------------------------------------
    def _set_status(self, text: str):
        if self._status:
            self._status.text = f"Status: {text}"

    def destroy(self):
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        if self._window:
            self._window.destroy()
            self._window = None
