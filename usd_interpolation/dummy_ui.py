import asyncio
import os
import numpy as np

from pxr import Usd, UsdGeom, UsdShade, Vt, Sdf
import omni.kit.app
import omni.usd
import omni.ui as ui

_MDL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uv_lerp.mdl")
_BLEND_SHADERS: dict = {}   # prim_path → UsdShade.Shader
_CURRENT_SEG: int = -1


def _get_attr(attr) -> object:
    val = attr.Get(Usd.TimeCode.Default())
    if val is None:
        samples = attr.GetTimeSamples()
        if samples:
            val = attr.Get(samples[0])
    return val


def _get_mesh_texture(stage, prim_path: str) -> str:
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return ""
    mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if not mat:
        return ""
    surf, _, _ = mat.ComputeSurfaceSource()
    if not surf:
        return ""
    inp = surf.GetInput("diffuse_texture")
    if not inp:
        return ""
    val = inp.Get()
    if val is None:
        return ""
    return val.path if hasattr(val, "path") else str(val)


def _create_blend_material(stage, session_layer, prim_path: str, prim, texture_path: str):
    safe = prim_path.replace("/", "_").lstrip("_")
    mat_path = f"/_uvblend/{safe}"
    with Usd.EditContext(stage, session_layer):
        mat = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path + "/Shader")
        shader.SetSourceAsset(Sdf.AssetPath(_MDL_PATH), "mdl")
        shader.SetSourceAssetSubIdentifier("uv_lerp_mat", "mdl")
        shader.CreateInput("diffuse_texture", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(texture_path))
        shader.CreateInput("t", Sdf.ValueTypeNames.Float).Set(0.0)
        mat.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out")
        UsdShade.MaterialBindingAPI(prim).Bind(mat)
    _BLEND_SHADERS[prim_path] = shader
    print(f"[usd_interpolation] Bound blend material: {prim_path}, tex={texture_path or 'none'}")


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
        st_raw = _get_attr(st_pv.GetAttr())
        if st_raw is not None:
            result[str(prim.GetPath())] = np.array(st_raw, dtype=np.float32).reshape(-1, 2)
            print(f"[usd_interpolation] Loaded st from {prim.GetPath()}, count={len(st_raw)}")

    if not result:
        print(f"[usd_interpolation] ERROR: No mesh with st found in {usd_file_path}")
        return None

    print(f"[usd_interpolation] Loaded {len(result)} mesh(es) from {usd_file_path}")
    return result


def _setup_segment(stage, map_a: dict, map_b: dict):
    """Write primvars:st_a and st_b for all prims in this segment, create blend materials."""
    session_layer = stage.GetSessionLayer()
    with Usd.EditContext(stage, session_layer):
        with Sdf.ChangeBlock():
            for prim_path, st_a in map_a.items():
                st_b = map_b.get(prim_path)
                if st_b is None:
                    continue
                prim = stage.GetPrimAtPath(prim_path)
                if not prim.IsValid():
                    continue
                pv_api = UsdGeom.PrimvarsAPI(prim)
                orig_pv = pv_api.GetPrimvar("st")
                interp = orig_pv.GetInterpolation() if orig_pv else UsdGeom.Tokens.vertex
                indices = orig_pv.GetIndices() if (orig_pv and orig_pv.IsIndexed()) else None
                for name, arr in [("st_a", st_a), ("st_b", st_b)]:
                    pv = pv_api.GetPrimvar(name)
                    if not pv or not pv.GetAttr().IsValid():
                        pv = pv_api.CreatePrimvar(
                            name, Sdf.ValueTypeNames.Float2Array, interp)
                    pv.GetAttr().Set(Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(arr)))
                    if indices is not None:
                        pv.SetIndices(indices)

                if prim_path not in _BLEND_SHADERS:
                    tex = _get_mesh_texture(stage, prim_path)
                    _create_blend_material(stage, session_layer, prim_path, prim, tex)


def apply_blend_t(map_a: dict, t: float) -> list:
    """Update material.inputs:t only — no primvar write, no resync needed."""
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return []
    written = []
    session_layer = stage.GetSessionLayer()
    with Usd.EditContext(stage, session_layer):
        with Sdf.ChangeBlock():
            for prim_path in map_a:
                shader = _BLEND_SHADERS.get(prim_path)
                if shader is None:
                    continue
                t_inp = shader.GetInput("t")
                if t_inp:
                    t_inp.Set(float(t))
                    prim = stage.GetPrimAtPath(prim_path)
                    if prim.IsValid():
                        written.append(prim)
    if written:
        print(f"[usd_interpolation] Blend t={t:.3f} → {len(written)} mesh(es)")
    return written


class _ResyncScheduler:
    """3-phase: MakeInvisible(ph0) → SetActive(F)(ph1) → SetActive(T)+MakeVisible(ph2).

    Used only on segment boundary when st_a/st_b primvars are (re)written.
    """

    def __init__(self, stage, prims: list):
        self._stage = stage
        self._prims = [p for p in prims if p.IsValid()]
        self._invisible_prims: list = []
        self._deactivated: list = []
        self._phase = 0
        self._cancelled: bool = False
        self._sub = omni.kit.app.get_app().get_update_event_stream() \
            .create_subscription_to_pop(self._on_tick, name="usd_interp_resync")

    def cancel(self):
        self._cancelled = True
        self._sub = None
        session = self._stage.GetSessionLayer()
        with Usd.EditContext(self._stage, session):
            with Sdf.ChangeBlock():
                for p in self._deactivated:
                    if p.IsValid():
                        p.SetActive(True)
                        UsdGeom.Imageable(p).MakeVisible()
                for p in self._invisible_prims:
                    if p.IsValid():
                        UsdGeom.Imageable(p).MakeVisible()
        self._deactivated = []
        self._invisible_prims = []

    def _on_tick(self, _event):
        if self._cancelled:
            return
        session = self._stage.GetSessionLayer()
        if self._phase == 0:
            with Usd.EditContext(self._stage, session):
                with Sdf.ChangeBlock():
                    for p in self._prims:
                        if p.IsValid():
                            UsdGeom.Imageable(p).MakeInvisible()
                            self._invisible_prims.append(p)
            self._phase = 1
        elif self._phase == 1:
            with Usd.EditContext(self._stage, session):
                with Sdf.ChangeBlock():
                    for p in self._invisible_prims:
                        if p.IsValid():
                            p.SetActive(False)
                            self._deactivated.append(p)
            self._invisible_prims = []
            self._phase = 2
        else:
            with Usd.EditContext(self._stage, session):
                with Sdf.ChangeBlock():
                    for p in self._deactivated:
                        if p.IsValid():
                            p.SetActive(True)
                            UsdGeom.Imageable(p).MakeVisible()
            self._deactivated = []
            self._sub = None


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
        self._resync: _ResyncScheduler | None = None
        self._is_animating: bool = False
        self._play_task: asyncio.Task | None = None
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
        global _BLEND_SHADERS, _CURRENT_SEG
        path = self._fields[idx].model.get_value_as_string().strip()
        if idx == 0:
            omni.usd.get_context().open_stage(path)
            _BLEND_SHADERS = {}
            _CURRENT_SEG = -1
        st_map = load_st_map(path)
        if st_map is None:
            self._set_status(f"ERROR: Failed to load File {idx}")
            return
        self._maps[idx] = st_map
        loaded = [i for i, m in enumerate(self._maps) if m is not None]
        self._set_status(f"File {idx} loaded ({len(st_map)} mesh(es))  |  Loaded: {loaded}")
        self._try_enable_slider()

    def _try_enable_slider(self):
        for i in range(NUM_FILES - 1):
            if self._maps[i] is not None and self._maps[i + 1] is not None:
                self._slider.enabled = True
                return
        self._slider.enabled = False

    def _start_resync(self, prims: list):
        if self._resync:
            self._resync.cancel()
        stage = omni.usd.get_context().get_stage()
        if stage and prims:
            self._resync = _ResyncScheduler(stage, prims)

    def _on_refresh_clicked(self):
        global _CURRENT_SEG
        _CURRENT_SEG = -1   # force segment re-setup + resync
        written = self._refresh(self._pending_t)
        if written:
            self._start_resync(written)

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
        written = self._refresh(t)
        if written and not self._is_animating:
            self._start_resync(written)

    def _refresh(self, t: float) -> list:
        global _CURRENT_SEG
        raw = t * (NUM_FILES - 1)
        seg = min(int(raw), NUM_FILES - 2)
        local_t = min(raw - seg, 1.0)
        print(f"[usd_interpolation] t={t:.4f} | seg={seg}→{seg+1} | local_t={local_t:.4f}")

        map_a = self._maps[seg]
        map_b = self._maps[seg + 1]
        if map_a is None or map_b is None:
            self._set_status(f"Segment {seg}→{seg+1} not loaded yet")
            return []

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return []

        seg_changed = (seg != _CURRENT_SEG)
        if seg_changed:
            _CURRENT_SEG = seg
            _setup_segment(stage, map_a, map_b)
            print(f"[usd_interpolation] Segment → {seg}→{seg+1}")

        written = apply_blend_t(map_a, local_t)
        # Resync only on segment change so Hydra rebuilds st_a/st_b GPU buffers
        return written if seg_changed else []

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        self._stop_play()
        if self._resync:
            self._resync.cancel()
            self._resync = None
        if self._window:
            self._window.destroy()
            self._window = None
