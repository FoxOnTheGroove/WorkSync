import numpy as np
import omni.usd
import omni.ui as ui
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt

from .interpolation import UVMixer
from .interpolation_api import UVMixer_api

NUM_FILES = 5
LOAD_TEST_ROOT = "/World/LoadTest"


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None
        self._field: ui.StringField | None = None
        self._btn_play: ui.Button | None = None
        self._btn_reverse: ui.Button | None = None
        self._btn_loop: ui.Button | None = None
        self._btn_rev_loop: ui.Button | None = None
        self._correction_cb: ui.CheckBox | None = None
        self._speed_label: ui.Label | None = None
        self._dup_field: ui.IntField | None = None

    def build_ui(self):
        UVMixer_api.init(num_slots=NUM_FILES, play_duration=2.5, use_correction=True)
        UVMixer_api.subscribe(self._on_t_changed)

        self._window = ui.Window("USD UV Interpolator", width=520, height=340)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):

                # ── Paths ────────────────────────────────────────────────────
                ui.Label("Paths (space or newline separated):", height=18)
                self._field = ui.StringField(height=24)
                self._field.model.set_value("/path/to/file0.usd /path/to/file1.usd")

                # ── Load + correction checkbox ────────────────────────────────
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Load All", width=80, clicked_fn=self._on_load_all)
                    ui.Spacer(width=8)
                    self._correction_cb = ui.CheckBox(width=20, height=20)
                    self._correction_cb.model.set_value(True)
                    self._correction_cb.model.add_value_changed_fn(self._on_correction_changed)
                    ui.Label("Correction (fvli)", width=140, height=20)

                # ── Status ───────────────────────────────────────────────────
                self._status_label = ui.Label("Status: Not loaded", height=20)

                # ── t slider ─────────────────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.000", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

                # ── Play controls ─────────────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    self._btn_play = ui.Button("Play ▶", width=80,
                                               clicked_fn=self._on_play_clicked)
                    self._btn_reverse = ui.Button("Reverse ◄", width=90,
                                                  clicked_fn=self._on_reverse_clicked)
                    self._btn_loop = ui.Button("Loop ↺", width=74,
                                               clicked_fn=self._on_loop_clicked)
                    self._btn_rev_loop = ui.Button("Rev Loop ↺", width=90,
                                                   clicked_fn=self._on_rev_loop_clicked)
                    ui.Button("Refresh", width=70,
                              clicked_fn=self._on_refresh_clicked)

                # ── Speed slider ──────────────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("Speed:", width=44)
                    self._speed_label = ui.Label("1.0x", width=34)
                    speed_slider = ui.FloatSlider(min=0.1, max=5.0, step=0.1)
                    speed_slider.model.set_value(1.0)
                    speed_slider.model.add_value_changed_fn(self._on_speed_changed)

                # ── Load test (debug) ─────────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("N:", width=18)
                    self._dup_field = ui.IntField(width=50)
                    self._dup_field.model.set_value(10)
                    ui.Button("Duplicate N", width=100,
                              clicked_fn=self._on_duplicate_clicked)
                    ui.Button("Clear", width=60,
                              clicked_fn=self._on_clear_clicked)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ensure_dome_light(stage) -> None:
        has_light = any(prim.HasAPI(UsdLux.LightAPI) for prim in stage.Traverse())
        if has_light:
            return
        dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
        dome.GetPrim().CreateAttribute(
            "visibleInPrimaryRay", Sdf.ValueTypeNames.Bool
        ).Set(False)

    def _on_load_all(self):
        raw = self._field.model.get_value_as_string()
        paths = [p for p in raw.split() if p]
        if not paths:
            self._set_status("ERROR: no paths")
            return
        if paths[0]:
            omni.usd.get_context().open_stage(paths[0])
        stage = omni.usd.get_context().get_stage()
        if stage:
            self._ensure_dome_light(stage)
        ok = 0
        for idx, path in enumerate(paths[:NUM_FILES]):
            if UVMixer_api.load(path, idx):
                ok += 1
            else:
                self._set_status(f"ERROR: failed slot {idx} ({path})")
                return
        loaded = UVMixer_api.get_loaded_slots()
        self._set_status(f"{ok} file(s) loaded  slots:{loaded}")
        self._slider.enabled = len(loaded) >= 2

    def _on_correction_changed(self, model):
        UVMixer_api.set_correction(bool(model.get_value_as_bool()))

    def _on_refresh_clicked(self):
        UVMixer_api.set_t(UVMixer_api.get_t())

    def _on_play_clicked(self):
        if UVMixer_api.is_playing():
            UVMixer_api.stop()
            self._btn_play.text = "Play ▶"
        else:
            UVMixer_api.play(forward=True)
            self._btn_play.text = "Stop ■"

    def _on_reverse_clicked(self):
        if UVMixer_api.is_playing():
            UVMixer_api.stop()
            self._btn_reverse.text = "Reverse ◄"
        else:
            UVMixer_api.play(forward=False)
            self._btn_reverse.text = "Stop ■"

    def _on_loop_clicked(self):
        if UVMixer_api.is_playing():
            UVMixer_api.stop()
            self._btn_loop.text = "Loop ↺"
        else:
            UVMixer_api.play(forward=True, loop=True)
            self._btn_loop.text = "Stop ■"

    def _on_rev_loop_clicked(self):
        if UVMixer_api.is_playing():
            UVMixer_api.stop()
            self._btn_rev_loop.text = "Rev Loop ↺"
        else:
            UVMixer_api.play(forward=False, loop=True)
            self._btn_rev_loop.text = "Stop ■"

    def _on_slider_changed(self, model):
        if UVMixer_api.is_playing():
            return
        t = model.get_value_as_float()
        self._t_label.text = f"t: {t:.3f}"
        UVMixer_api.set_t(t)

    def _on_speed_changed(self, model):
        speed = model.get_value_as_float()
        UVMixer_api.set_speed(speed)
        if self._speed_label:
            self._speed_label.text = f"{speed:.1f}x"

    def _on_duplicate_clicked(self):
        n = self._dup_field.model.get_value_as_int()
        if n <= 0:
            self._set_status("ERROR: N must be > 0")
            return
        added = self._duplicate_meshes(n)
        self._set_status(f"Duplicated {n} copies → {added} prim(s) added")
        self._slider.enabled = len(UVMixer_api.get_loaded_slots()) >= 2

    def _on_clear_clicked(self):
        self._clear_load_test_prims()
        self._set_status("Load test prims cleared")

    def _on_t_changed(self, t: float):
        if self._slider:
            self._slider.model.set_value(t)
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        if not UVMixer_api.is_playing():
            if self._btn_play:
                self._btn_play.text = "Play ▶"
            if self._btn_reverse:
                self._btn_reverse.text = "Reverse ◄"
            if self._btn_loop:
                self._btn_loop.text = "Loop ↺"
            if self._btn_rev_loop:
                self._btn_rev_loop.text = "Rev Loop ↺"

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        UVMixer_api.unsubscribe(self._on_t_changed)
        UVMixer_api.destroy()
        if self._window:
            self._window.destroy()
            self._window = None

    # ── Debug helpers (mesh duplicate / clear) ────────────────────────────────

    @staticmethod
    def _duplicate_meshes(n: int) -> int:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return 0
        loaded = [(i, m) for i, m in enumerate(UVMixer._maps) if m is not None]
        if not loaded:
            return 0
        orig_paths = sorted({p for _, m in loaded for p in m})
        if not orig_paths:
            return 0

        session = pxr_stage.GetSessionLayer()
        grid_cols = max(1, int(n ** 0.5))
        spacing = 200.0
        added = 0

        with Usd.EditContext(pxr_stage, session):
            for copy_idx in range(1, n):
                col = copy_idx % grid_cols
                row = copy_idx // grid_cols
                group_path = f"{LOAD_TEST_ROOT}/copy_{copy_idx:04d}"
                grp = UsdGeom.Xform.Define(pxr_stage, group_path)
                UsdGeom.XformCommonAPI(grp).SetTranslate(
                    Gf.Vec3d(col * spacing, 0.0, row * spacing)
                )
                for mesh_idx, orig_path in enumerate(orig_paths):
                    src_prim = pxr_stage.GetPrimAtPath(orig_path)
                    if not src_prim.IsValid():
                        continue
                    dst_path = f"{group_path}/m{mesh_idx:04d}"
                    dst_mesh = UsdGeom.Mesh.Define(pxr_stage, dst_path)
                    dst_prim = dst_mesh.GetPrim()
                    binding = UsdShade.MaterialBindingAPI(src_prim).GetDirectBinding()
                    mat_path = binding.GetMaterialPath()
                    if mat_path:
                        mat_prim = pxr_stage.GetPrimAtPath(mat_path)
                        if mat_prim.IsValid():
                            UsdShade.MaterialBindingAPI.Apply(dst_prim).Bind(
                                UsdShade.Material(mat_prim)
                            )
                    for attr_name in ("points", "faceVertexCounts", "faceVertexIndices", "normals"):
                        src_attr = src_prim.GetAttribute(attr_name)
                        if not (src_attr and src_attr.IsValid()):
                            continue
                        val = src_attr.Get(Usd.TimeCode.Default())
                        if val is None:
                            ts = src_attr.GetTimeSamples()
                            if ts:
                                val = src_attr.Get(ts[0])
                        if val is not None:
                            dst_prim.CreateAttribute(attr_name, src_attr.GetTypeName()).Set(val)
                    src_st = UsdGeom.PrimvarsAPI(src_prim).GetPrimvar("st")
                    if src_st and src_st.GetAttr().IsValid():
                        val = src_st.ComputeFlattened(Usd.TimeCode.Default())
                        if val is None:
                            ts = src_st.GetTimeSamples()
                            if ts:
                                val = src_st.ComputeFlattened(ts[0])
                        if val is not None:
                            dst_st = UsdGeom.PrimvarsAPI(dst_prim).CreatePrimvar(
                                "st", src_st.GetTypeName(), src_st.GetInterpolation()
                            )
                            dst_st.Set(Vt.Vec2fArray.FromNumpy(
                                np.array(val, dtype=np.float32).reshape(-1, 2)
                            ))
                    for _, m in loaded:
                        if orig_path in m:
                            m[dst_path] = m[orig_path].copy()
                    added += 1

        if added > 0:
            UVMixer._bake_timesamples()
            UVMixer_api.set_t(UVMixer_api.get_t())
        return added

    @staticmethod
    def _clear_load_test_prims() -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            pxr_stage.RemovePrim(LOAD_TEST_ROOT)
        for m in UVMixer._maps:
            if m is None:
                continue
            for k in list(m.keys()):
                if k.startswith(LOAD_TEST_ROOT):
                    del m[k]
        if any(m is not None for m in UVMixer._maps):
            UVMixer._bake_timesamples()
            UVMixer_api.set_t(UVMixer_api.get_t())
