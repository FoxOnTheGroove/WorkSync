import asyncio
import numpy as np

import usdrt
from pxr import Usd, UsdGeom, Vt, Sdf
import omni.kit.app
import omni.usd
import omni.ui as ui


def _get_attr(attr) -> object:
    val = attr.Get(Usd.TimeCode.Default())
    if val is None:
        samples = attr.GetTimeSamples()
        if samples:
            val = attr.Get(samples[0])
    return val


def load_st_map(usd_file_path: str) -> dict[str, np.ndarray] | None:
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        print(f"[usd_interpolation] ERROR: Failed to open: {usd_file_path}")
        return None

    result = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st_pv or not st_pv.GetAttr().IsValid():
            continue
        tc = Usd.TimeCode.Default()
        st_raw = st_pv.ComputeFlattened(tc)
        if st_raw is None:
            samples = st_pv.GetTimeSamples()
            if samples:
                st_raw = st_pv.ComputeFlattened(samples[0])
        if st_raw is not None:
            result[str(prim.GetPath())] = np.array(st_raw, dtype=np.float32).reshape(-1, 2)

    if not result:
        print(f"[usd_interpolation] ERROR: No mesh with st found in {usd_file_path}")
        return None

    return result


def apply_lerped_st_all(map_a: dict, map_b: dict, t: float) -> list:
    stage_id = omni.usd.get_context().get_stage_id()
    usdrt_stage = usdrt.Usd.Stage.Attach(stage_id)

    written = []
    for prim_path, st_a in map_a.items():
        st_b = map_b.get(prim_path)
        if st_b is None:
            continue
        usdrt_prim = usdrt_stage.GetPrimAtPath(usdrt.Sdf.Path(prim_path))
        if not usdrt_prim.IsValid():
            continue
        usdrt_attr = usdrt_prim.GetAttribute("primvars:st")
        if not usdrt_attr:
            continue
        if len(st_a) != len(st_b):
            chosen = st_a if t < 0.5 else st_b
            usdrt_attr.Set(Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(chosen)))
        else:
            t32 = np.float32(t)
            lerped = np.ascontiguousarray(st_a + t32 * (st_b - st_a))
            usdrt_attr.Set(Vt.Vec2fArray.FromNumpy(lerped))
        written.append(prim_path)

    print(f"[usd_interpolation] Applied lerp t={t:.2f} to {len(written)} mesh(es) via Fabric")
    return written


NUM_FILES = 5


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None

        self._fields: list[ui.StringField] = []
        self._maps: list[dict | None] = [None] * NUM_FILES

        self._pending_t: float = 0.0
        self._is_animating: bool = False
        self._play_task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self._btn_play: ui.Button | None = None
        self._btn_reverse: ui.Button | None = None

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=500, height=60 * NUM_FILES + 100)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                for i in range(NUM_FILES):
                    with ui.HStack(height=24, spacing=4):
                        ui.Label(f"File {i}:", width=50)
                        field = ui.StringField()
                        field.model.set_value(f"/path/to/file{i}.usd")
                        self._fields.append(field)
                        idx = i
                        ui.Button("Load", width=50,
                                  clicked_fn=lambda _idx=idx: self._on_load(_idx))

                self._status_label = ui.Label("Status: Not loaded", height=20)

                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.00", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

                with ui.HStack(height=24, spacing=8):
                    self._btn_play = ui.Button("Play ▶", width=80,
                                               clicked_fn=self._on_play_clicked)
                    self._btn_reverse = ui.Button("Reverse ◄", width=90,
                                                  clicked_fn=self._on_reverse_clicked)
                    ui.Button("Refresh", width=70,
                              clicked_fn=self._on_refresh_clicked)

    def _on_load(self, idx: int):
        path = self._fields[idx].model.get_value_as_string().strip()
        if idx == 0:
            omni.usd.get_context().open_stage(path)
        st_map = load_st_map(path)
        if st_map is None:
            self._set_status(f"ERROR: Failed to load File {idx}")
            return
        self._maps[idx] = st_map
        loaded_indices = [i for i, m in enumerate(self._maps) if m is not None]
        self._set_status(f"File {idx} loaded ({len(st_map)} mesh(es))  |  Loaded: {loaded_indices}")
        self._try_enable_slider()

    def _try_enable_slider(self):
        loaded = [m for m in self._maps if m is not None]
        self._slider.enabled = len(loaded) >= 2

    def _on_refresh_clicked(self):
        self._refresh(self._pending_t)

    def _on_play_clicked(self):
        if self._play_task and not self._play_task.done():
            self._stop_play()
        else:
            self._play_task = asyncio.ensure_future(self._animate(forward=True))

    def _on_reverse_clicked(self):
        if self._play_task and not self._play_task.done():
            self._stop_play()
        else:
            self._play_task = asyncio.ensure_future(self._animate(forward=False))

    def _stop_play(self):
        if self._play_task:
            self._play_task.cancel()
            self._play_task = None
        if self._btn_play:
            self._btn_play.text = "Play ▶"
        if self._btn_reverse:
            self._btn_reverse.text = "Reverse ◄"

    async def _animate(self, forward: bool):
        DURATION = 2.5
        if self._btn_play:
            self._btn_play.text = "Stop ■" if forward else "Play ▶"
        if self._btn_reverse:
            self._btn_reverse.text = "Reverse ◄" if forward else "Stop ■"

        start_t = self._pending_t
        target = 1.0 if forward else 0.0
        travel = abs(target - start_t)
        elapsed = 0.0
        dt_scale = travel / DURATION if travel > 0.0 else 0.0

        self._is_animating = True
        try:
            while True:
                await omni.kit.app.get_app().next_update_async()
                elapsed += 1.0 / 60.0
                frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                new_t = start_t + (frac if forward else -frac)
                new_t = max(0.0, min(1.0, new_t))

                self._slider.model.set_value(new_t)

                if new_t == target or (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                    break
        except asyncio.CancelledError:
            return
        finally:
            self._is_animating = False
            self._stop_play()

    def _on_slider_changed(self, model):
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        self._pending_t = t
        self._refresh(t)

    def _refresh(self, t: float) -> list:
        loaded = [m for m in self._maps if m is not None]
        n = len(loaded)
        if n < 2:
            self._set_status("Need at least 2 files loaded")
            return []
        raw = t * (n - 1)
        seg = min(int(raw), n - 2)
        local_t = min(raw - seg, 1.0)
        map_a = loaded[seg]
        map_b = loaded[seg + 1]
        result = apply_lerped_st_all(map_a, map_b, local_t)
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = asyncio.ensure_future(
            self._flush_followup(map_a, map_b, local_t, n=40)
        )
        return result

    async def _flush_followup(self, map_a, map_b, local_t, n=40):
        try:
            for i in range(n):
                await omni.kit.app.get_app().next_update_async()
                apply_lerped_st_all(map_a, map_b, local_t)
        except asyncio.CancelledError:
            pass

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        self._stop_play()
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        if self._window:
            self._window.destroy()
            self._window = None
