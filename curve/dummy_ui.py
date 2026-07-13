"""streamline 복셀 최적화 익스텐션 UI.

파일 경로를 입력하고 Voxelize & Load 를 누르면 lines_optimize 가 자동 처리하여
복셀 다운샘플링된 PointInstancer 가 현재 씬에 로드된다.
Group Paths 로 지정한 대상 밖(ungrouped) 곡선은 연산하지 않고 원본을 그대로
가져오며, Load 버튼 우측 체크박스로 그 표시 여부를 즉시 토글할 수 있다.
Sphere Radius 슬라이더로 로드 후에도 전체 구 크기를 실시간 일괄 조정한다.
실제 처리 로직은 모두 lines_optimize.py 에 있다.
"""

import asyncio
import time

import omni.ui as ui

from .lines_optimize import (
    optimize_and_load_async, set_sphere_radius, set_raw_visible,
    _parse_group_paths,
)


class LinesOptimizeUI:
    def __init__(self):
        self._window: ui.Window | None = None
        self._groups_field: ui.StringField | None = None
        self._res_field: ui.IntField | None = None
        self._path_field: ui.StringField | None = None
        self._raw_visible_cb: ui.CheckBox | None = None
        self._radius_slider: ui.FloatSlider | None = None
        self._status: ui.Label | None = None
        self._run_btn: ui.Button | None = None
        self._task: asyncio.Task | None = None

    def build_ui(self):
        self._window = ui.Window("Streamline Voxel Optimizer", width=580, height=260)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Group Paths:", width=100)
                    self._groups_field = ui.StringField()
                    self._groups_field.model.set_value("")  # /root/target, /root/target2

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Resolution:", width=100)
                    self._res_field = ui.IntField(width=80)
                    self._res_field.model.set_value(128)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("USD Path:", width=100)
                    self._path_field = ui.StringField()
                    self._path_field.model.set_value("")

                with ui.HStack(height=30, spacing=8):
                    self._run_btn = ui.Button(
                        "Voxelize & Load", width=150, clicked_fn=self._on_run)
                    ui.Label("Show ungrouped:", width=110)
                    self._raw_visible_cb = ui.CheckBox(width=24)
                    self._raw_visible_cb.model.set_value(True)
                    self._raw_visible_cb.model.add_value_changed_fn(
                        self._on_raw_visible_changed)

                with ui.HStack(height=24, spacing=6):
                    ui.Label("Sphere Radius:", width=100)
                    self._radius_slider = ui.FloatSlider(min=0.05, max=2.0, step=0.01)
                    self._radius_slider.model.set_value(0.5)
                    self._radius_slider.model.add_value_changed_fn(
                        self._on_radius_changed)

                self._status = ui.Label("Status: idle", word_wrap=True)

    # ------------------------------------------------------------------
    def _on_run(self):
        if self._task and not self._task.done():
            self._set_status("Already running...")
            return
        path = self._path_field.model.get_value_as_string().strip()
        if not path:
            self._set_status("ERROR: enter a USD Path")
            return
        groups = _parse_group_paths(self._groups_field.model.get_value_as_string())
        res = self._res_field.model.get_value_as_int()
        radius = self._radius_slider.model.get_value_as_float()
        raw_visible = self._raw_visible_cb.model.get_value_as_bool()
        self._task = asyncio.ensure_future(
            self._run_async(path, res, groups, radius, raw_visible))

    async def _run_async(self, path, res, groups, radius, raw_visible):
        if self._run_btn:
            self._run_btn.enabled = False
        self._set_status("Processing...")
        start = time.perf_counter()
        try:
            msg = await optimize_and_load_async(
                path, resolution=res, radius_factor=radius,
                group_paths=groups, raw_visible=raw_visible,
                progress=self._set_status)
            elapsed = time.perf_counter() - start
            msg = f"{msg} | {elapsed:.2f}s"
        except Exception as e:  # noqa: BLE001
            elapsed = time.perf_counter() - start
            msg = f"ERROR: {e} | {elapsed:.2f}s"
            import traceback
            traceback.print_exc()
        finally:
            if self._run_btn:
                self._run_btn.enabled = True
        self._set_status(msg)

    def _on_radius_changed(self, model):
        try:
            self._set_status(set_sphere_radius(model.get_value_as_float()))
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: {e}")

    def _on_raw_visible_changed(self, model):
        try:
            self._set_status(set_raw_visible(model.get_value_as_bool()))
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: {e}")

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
