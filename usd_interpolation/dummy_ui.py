import numpy as np
import omni.usd
import omni.ui as ui
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from .interpolation import UVMixer

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
        self._stage_field: ui.StringField | None = None

        # _primary drives the timeline; all other mixers just have their data baked.
        self._primary: UVMixer | None = None
        self._mixers: list[UVMixer] = []        # all base prims
        self._load_test_mixers: list[UVMixer] = []  # duplicated test prims
        self._src_paths: list[str] = []

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=520, height=340)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):

                # ── Target prim stage ─────────────────────────
                ui.Label("Target Prim USD:", height=18)
                with ui.HStack(height=24, spacing=4):
                    self._stage_field = ui.StringField(height=24)
                    self._stage_field.model.set_value("/path/to/target.usd")
                    ui.Button("Load Target Prim", width=120, clicked_fn=self._on_load_target_prim)

                # ── Paths ───────────────────────────────────────
                ui.Label("UV Paths (space or newline separated):", height=18)
                self._field = ui.StringField(height=24)
                self._field.model.set_value("/path/to/file0.usd /path/to/file1.usd")

                # ── Load + correction checkbox ────────────────────
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Load All", width=80, clicked_fn=self._on_load_all)
                    ui.Spacer(width=8)
                    self._correction_cb = ui.CheckBox(width=20, height=20)
                    self._correction_cb.model.set_value(True)
                    self._correction_cb.model.add_value_changed_fn(self._on_correction_changed)
                    ui.Label("Correction", width=100, height=20)

                # ── Status ─────────────────────────────────────
                self._status_label = ui.Label("Status: Not loaded", height=20)

                # ── t slider ─────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.000", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

                # ── Play controls ─────────────────────────
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

                # ── Speed slider ────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("Speed:", width=44)
                    self._speed_label = ui.Label("1.0x", width=34)
                    speed_slider = ui.FloatSlider(min=0.1, max=5.0, step=0.1)
                    speed_slider.model.set_value(1.0)
                    speed_slider.model.add_value_changed_fn(self._on_speed_changed)

                # ── Load test (debug) ──────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("N:", width=18)
                    self._dup_field = ui.IntField(width=50)
                    self._dup_field.model.set_value(10)
                    ui.Button("Duplicate N", width=100,
                              clicked_fn=self._on_duplicate_clicked)
                    ui.Button("Clear", width=60,
                              clicked_fn=self._on_clear_clicked)

    # ── Helpers ────────────────────────────────────────────

    def _all_mixers(self) -> list[UVMixer]:
        return self._mixers + self._load_test_mixers

    # ── Callbacks ───────────────────────────────────────────

    def _on_load_target_prim(self):
        path = self._stage_field.model.get_value_as_string().strip()
        if not path:
            self._set_status("ERROR: no target prim path")
            return
        omni.usd.get_context().open_stage(path)
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            self._set_status("ERROR: failed to open stage")
            return
        self._set_status(f"Target prim loaded: {path}")

    def _on_load_all(self):
        raw = self._field.model.get_value_as_string()
        paths = [p for p in raw.split() if p]
        if not paths:
            self._set_status("ERROR: no paths")
            return
        if len(paths) < 2:
            self._set_status("ERROR: need at least 2 paths")
            return

        # Tear down previous state
        if self._primary:
            self._primary.unsubscribe(self._on_t_changed)
            self._primary.unsubscribe_boundary(self._on_animate_boundary)
        for m in self._all_mixers():
            m.destroy()
        self._mixers = []
        self._load_test_mixers = []
        self._primary = None

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            self._set_status("ERROR: no stage — load target prim first")
            return
        self._src_paths = list(paths)
        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True

        # 1) Open each source file once, read all meshes' st
        maps_per_file = [UVMixer.read_st_file(p) for p in paths]

        # 2) Meshes present in all source files
        common_paths = set(maps_per_file[0].keys())
        for m in maps_per_file[1:]:
            common_paths &= set(m.keys())

        if not common_paths:
            self._set_status("ERROR: no mesh found in all source files")
            self._slider.enabled = False
            return

        # 3) Build one mixer for all meshes combined
        st_maps = [{path: maps_per_file[i][path] for path in common_paths}
                   for i in range(len(paths))]
        mixer = UVMixer._from_maps("primary", st_maps, use_correction=use_correction)
        self._mixers = [mixer]

        self._primary = mixer
        self._primary.subscribe(self._on_t_changed)
        self._primary.subscribe_boundary(self._on_animate_boundary)
        self._slider.enabled = True
        self._primary.seek(0.0)
        self._set_status(f"1 mixer ({len(common_paths)} mesh(es), {len(paths)} source(s))")

    def _on_correction_changed(self, model):
        enabled = bool(model.get_value_as_bool())
        for m in self._all_mixers():
            m.set_correction(enabled)
        if self._primary:
            self._primary.seek(self._primary.position())

    def _on_refresh_clicked(self):
        if self._primary:
            self._primary.seek(self._primary.position())

    def _on_play_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._btn_play.text = "Play ▶"
        else:
            self._primary.play(forward=True)
            self._btn_play.text = "Stop ■"

    def _on_reverse_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._btn_reverse.text = "Reverse ◄"
        else:
            self._primary.play(forward=False)
            self._btn_reverse.text = "Stop ■"

    def _on_loop_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._btn_loop.text = "Loop ↺"
        else:
            self._primary.play(forward=True, loop=True)
            self._btn_loop.text = "Stop ■"

    def _on_rev_loop_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._btn_rev_loop.text = "Rev Loop ↺"
        else:
            self._primary.play(forward=False, loop=True)
            self._btn_rev_loop.text = "Stop ■"

    def _on_slider_changed(self, model):
        if self._primary and self._primary.is_playing():
            return
        t = model.get_value_as_float()
        self._t_label.text = f"t: {t:.3f}"
        if self._primary:
            self._primary.seek(t)

    def _on_speed_changed(self, model):
        speed = model.get_value_as_float()
        if self._primary:
            self._primary.set_speed(speed)
        if self._speed_label:
            self._speed_label.text = f"{speed:.1f}x"

    def _on_duplicate_clicked(self):
        n = self._dup_field.model.get_value_as_int()
        if n <= 0:
            self._set_status("ERROR: N must be > 0")
            return
        added = self._duplicate_meshes(n)
        self._set_status(f"Duplicated {n} copies → {added} prim(s) added")

    def _on_clear_clicked(self):
        self._clear_load_test_prims()
        self._set_status("Load test prims cleared")

    def _on_animate_boundary(self) -> None:
        for m in self._load_test_mixers:
            m.apply_correction()

    def _on_t_changed(self, t: float):
        if self._slider:
            self._slider.model.set_value(t)
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        if self._primary and not self._primary.is_playing():
            for m in self._load_test_mixers:
                m.apply_correction()
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
        if self._primary:
            self._primary.unsubscribe(self._on_t_changed)
            self._primary.unsubscribe_boundary(self._on_animate_boundary)
        for m in self._all_mixers():
            m.destroy()
        self._mixers = []
        self._load_test_mixers = []
        self._primary = None
        if self._window:
            self._window.destroy()
            self._window = None

    # ── Debug helpers (mesh duplicate / clear) ────────────────────

    def _duplicate_meshes(self, n: int) -> int:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None or not self._mixers or not self._src_paths:
            return 0

        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True
        session = pxr_stage.GetSessionLayer()
        grid_cols = max(1, int(n ** 0.5))
        spacing = 80.0
        added = 0

        src_mixer = self._mixers[0]
        orig_mesh_paths = sorted(src_mixer._st_maps[0].keys())

        with Usd.EditContext(pxr_stage, session):
            for copy_idx in range(1, n):
                col = copy_idx % grid_cols
                row = copy_idx // grid_cols
                group_path = f"{LOAD_TEST_ROOT}/copy_{copy_idx:04d}"
                grp = UsdGeom.Xform.Define(pxr_stage, group_path)
                UsdGeom.XformCommonAPI(grp).SetTranslate(
                    Gf.Vec3d(col * spacing, 0.0, row * spacing)
                )

                # Map original mesh paths → dst paths
                path_map: dict[str, str] = {}
                for mesh_idx, orig_path in enumerate(orig_mesh_paths):
                    dst_path = f"{group_path}/m{mesh_idx:04d}"
                    path_map[orig_path] = dst_path
                    src_prim = pxr_stage.GetPrimAtPath(orig_path)
                    if not src_prim.IsValid():
                        continue
                    dst_mesh = UsdGeom.Mesh.Define(pxr_stage, dst_path)
                    dst_prim = dst_mesh.GetPrim()

                    # Copy material binding
                    binding = UsdShade.MaterialBindingAPI(src_prim).GetDirectBinding()
                    mat_path = binding.GetMaterialPath()
                    if mat_path:
                        mat_prim = pxr_stage.GetPrimAtPath(mat_path)
                        if mat_prim.IsValid():
                            UsdShade.MaterialBindingAPI.Apply(dst_prim).Bind(
                                UsdShade.Material(mat_prim)
                            )

                    # Copy geometry attributes
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

                    # Copy st primvar skeleton (values overwritten by UVMixer)
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

                # Build shifted st_maps with remapped dst paths
                maps = src_mixer._st_maps
                shift = copy_idx % len(maps)
                shifted_orig = maps[shift:] + maps[:shift]
                shifted_maps = [{path_map[op]: arr
                                 for op, arr in tc_map.items() if op in path_map}
                                for tc_map in shifted_orig]
                mixer = UVMixer._from_maps(group_path, shifted_maps,
                                           use_correction=use_correction)
                self._load_test_mixers.append(mixer)
                added += 1

        if self._primary:
            self._primary.seek(self._primary.position())
        return added

    def _clear_load_test_prims(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            pxr_stage.RemovePrim(LOAD_TEST_ROOT)
        for m in self._load_test_mixers:
            m.destroy()
        self._load_test_mixers = []
