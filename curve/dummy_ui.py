"""streamline 복셀 최적화 + 다중 데이터셋 보간 익스텐션 UI.

- 최대 5개 USD 를 공통 격자로 복셀화해 씬에 로드
- Sphere Radius 슬라이더로 전체 구 크기 실시간 일괄 조정
- Interpolate 슬라이더로 1~5 데이터셋 사이를 공간 기준 크로스페이드
실제 처리 로직은 모두 lines_optimize.py 에 있다.
"""

import asyncio

import omni.ui as ui

from .lines_optimize import (
    build_snapshots_async, author_snapshot, InterpSession,
    set_sphere_radius, _parse_group_paths,
)

NUM_PATHS = 5
COLOR_LEVELS = 16      # 고정 기본값
VOXEL_SIZE = 0.0       # 0 = resolution 기준 자동


class LinesOptimizeUI:
    def __init__(self):
        self._window: ui.Window | None = None
        self._groups_field: ui.StringField | None = None
        self._res_field: ui.IntField | None = None
        self._path_fields: list[ui.StringField] = []
        self._radius_slider: ui.FloatSlider | None = None
        self._interp_slider: ui.FloatSlider | None = None
        self._status: ui.Label | None = None
        self._run_btn: ui.Button | None = None
        self._task: asyncio.Task | None = None

        self._snapshots = None
        self._grid = None
        self._session = None
        self._busy = False

    def build_ui(self):
        self._window = ui.Window("Streamline Voxel Optimizer", width=600, height=420)
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

                self._path_fields = []
                for i in range(NUM_PATHS):
                    with ui.HStack(height=24, spacing=4):
                        ui.Label(f"USD Path {i + 1}:", width=100)
                        f = ui.StringField()
                        f.model.set_value("")
                        self._path_fields.append(f)

                self._run_btn = ui.Button(
                    "Voxelize & Load", height=30, clicked_fn=self._on_run)

                with ui.HStack(height=24, spacing=6):
                    ui.Label("Sphere Radius:", width=100)
                    self._radius_slider = ui.FloatSlider(min=0.05, max=2.0, step=0.01)
                    self._radius_slider.model.set_value(0.5)
                    self._radius_slider.model.add_value_changed_fn(
                        self._on_radius_changed)

                with ui.HStack(height=24, spacing=6):
                    ui.Label("Interpolate:", width=100)
                    self._interp_slider = ui.FloatSlider(
                        min=1.0, max=float(NUM_PATHS), step=0.01)
                    self._interp_slider.model.set_value(1.0)
                    self._interp_slider.enabled = False
                    self._interp_slider.model.add_value_changed_fn(
                        self._on_interp_changed)

                self._status = ui.Label("Status: idle", word_wrap=True)

    # ------------------------------------------------------------------
    def _on_run(self):
        if self._task and not self._task.done():
            self._set_status("Already running...")
            return
        paths = [f.model.get_value_as_string().strip() for f in self._path_fields]
        paths = [p for p in paths if p]
        if not paths:
            self._set_status("ERROR: enter at least one USD Path")
            return
        groups = _parse_group_paths(self._groups_field.model.get_value_as_string())
        res = self._res_field.model.get_value_as_int()
        self._task = asyncio.ensure_future(self._run_async(paths, res, groups))

    async def _run_async(self, paths, res, groups):
        if self._run_btn:
            self._run_btn.enabled = False
        self._set_status("Processing...")
        try:
            snaps, grid, msg = await build_snapshots_async(
                paths, resolution=res, group_paths=groups,
                voxel_size=VOXEL_SIZE, progress=self._set_status)
            self._snapshots, self._grid = snaps, grid
            self._session = None  # 스냅샷 바뀌면 세션 초기화
            if snaps:
                radius = self._radius_slider.model.get_value_as_float()
                msg = author_snapshot(snaps[0], grid, radius, COLOR_LEVELS) \
                    + f" | {len(snaps)} dataset(s)"
                # 보간 슬라이더는 데이터셋 수에 맞춰 활성화
                self._interp_slider.min = 1.0
                self._interp_slider.max = float(max(len(snaps), 1))
                self._interp_slider.model.set_value(1.0)
                self._interp_slider.enabled = len(snaps) > 1
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
            self._set_status(set_sphere_radius(model.get_value_as_float()))
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: {e}")

    def _on_interp_changed(self, model):
        if not self._snapshots or len(self._snapshots) < 2 or self._busy:
            return
        self._busy = True
        try:
            s = model.get_value_as_float()          # 1 .. N
            n = len(self._snapshots)
            seg = min(max(int(s) - 1, 0), n - 2)    # 0 .. N-2
            t = s - (seg + 1)                        # 0 .. 1 (s==N → seg=N-2, t=1)
            radius = self._radius_slider.model.get_value_as_float()
            # 같은 구간이면 세션 재사용(프림 유지, 색/스케일만 갱신) → 빠름
            if self._session is None or self._session.seg != (seg, seg + 1):
                self._session = InterpSession(self._grid, COLOR_LEVELS)
                self._session.prepare(
                    self._snapshots[seg], self._snapshots[seg + 1],
                    (seg, seg + 1), radius)
            n_vox = self._session.update(t, radius, COLOR_LEVELS)
            self._set_status(f"interp {s:.2f} (seg {seg + 1}->{seg + 2}, "
                             f"t={t:.2f}) | instances {n_vox}")
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._busy = False

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
