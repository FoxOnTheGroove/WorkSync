import asyncio

import numpy as np
import omni.kit.app
import omni.usd
import omni.ui as ui
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt

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

        # _primary drives the timeline; all other mixers just have their data baked.
        self._primary: UVMixer | None = None
        self._mixers: list[UVMixer] = []        # all base prims
        self._load_test_mixers: list[UVMixer] = []  # duplicated test prims
        self._src_paths: list[str] = []

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=520, height=340)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):

                # ── Paths ────────────────────────────────────────────
                ui.Label("Paths (space or newline separated):", height=18)
                self._field = ui.StringField(height=24)
                self._field.model.set_value("/path/to/file0.usd /path/to/file1.usd")

                # ── Load + correction checkbox ────────────────────────────
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Load All", width=80, clicked_fn=self._on_load_all)
                    ui.Spacer(width=8)
                    self._correction_cb = ui.CheckBox(width=20, height=20)
                    self._correction_cb.model.set_value(True)
                    self._correction_cb.model.add_value_changed_fn(self._on_correction_changed)
                    ui.Label("Correction", width=100, height=20)

                # ── Status ───────────────────────────────────────────
                self._status_label = ui.Label("Status: Not loaded", height=20)

                # ── t slider ───────────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.000", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

                # ── Play controls ─────────────────────────────────────
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

                # ── Speed slider ──────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("Speed:", width=44)
                    self._speed_label = ui.Label("1.0x", width=34)
                    speed_slider = ui.FloatSlider(min=0.1, max=5.0, step=0.1)
                    speed_slider.model.set_value(1.0)
                    speed_slider.model.add_value_changed_fn(self._on_speed_changed)

                # ── Load test (debug) ─────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("N:", width=18)
                    self._dup_field = ui.IntField(width=50)
                    self._dup_field.model.set_value(10)
                    ui.Button("Duplicate N", width=100,
                              clicked_fn=self._on_duplicate_clicked)
                    ui.Button("Clear", width=60,
                              clicked_fn=self._on_clear_clicked)

    # ── Helpers ─────────────────────────────────────────────────────

    def _all_mixers(self) -> list[UVMixer]:
        return self._mixers + self._load_test_mixers

    # ── Callbacks ─────────────────────────────────────────────────

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
        if len(paths) < 2:
            self._set_status("ERROR: need at least 2 paths")
            return

        # Tear down previous state
        if self._primary:
            self._primary.unsubscribe(self._on_t_changed)
        for m in self._all_mixers():
            m.destroy()
        self._mixers = []
        self._load_test_mixers = []
        self._primary = None

        omni.usd.get_context().open_stage(paths[0])
        self._set_status("Loading...")
        asyncio.ensure_future(self._load_async(paths))

    async def _load_async(self, paths: list[str]):
        # open_stage() is processed asynchronously in Kit; wait for Hydra to initialize
        for _ in range(3):
            await omni.kit.app.get_app().next_update_async()

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            self._set_status("ERROR: no stage")
            return
        self._ensure_dome_light(stage)
        self._src_paths = list(paths)

        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True
        ok = skipped = 0
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            prim_path = str(prim.GetPath())
            try:
                mixer = UVMixer.create(prim_path, *paths, use_correction=use_correction)
                self._mixers.append(mixer)
                ok += 1
            except ValueError:
                skipped += 1

        if not self._mixers:
            self._set_status(f"ERROR: no usable mesh found (skipped {skipped})")
            if self._slider:
                self._slider.enabled = False
            return

        self._primary = self._mixers[0]
        self._primary.subscribe(self._on_t_changed)
        if self._slider:
            self._slider.enabled = True
        self._primary.seek(0.0)
        msg = f"{ok} mixer(s) created"
        if skipped:
            msg += f", {skipped} skipped"
        self._set_status(msg)

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

    def _on_t_changed(self, t: float):
        if self._slider:
            self._slider.model.set_value(t)
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        if self._primary and not self._primary.is_playing():
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
        for m in self._all_mixers():
            m.destroy()
        self._mixers = []
        self._load_test_mixers = []
        self._primary = None
        if self._window:
            self._window.destroy()
            self._window = None

    # ── Debug helpers (mesh duplicate / clear) ────────────────────────────

    def _duplicate_meshes(self, n: int) -> int:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None or not self._mixers or not self._src_paths:
            return 0

        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True
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
                for mesh_idx, src_mixer in enumerate(self._mixers):
                    src_prim = pxr_stage.GetPrimAtPath(src_mixer._target)
                    if not src_prim.IsValid():
                        continue
                    dst_path = f"{group_path}/m{mesh_idx:04d}"
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

                    # Copy st primvar definition (values will be overwritten by UVMixer)
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

                    # Create UVMixer with cyclically shifted st maps for visual variety
                    maps = src_mixer._st_maps
                    shift = copy_idx % len(maps)
                    shifted_maps = maps[shift:] + maps[:shift]
                    mixer = UVMixer._from_maps(dst_path, shifted_maps,
                                               use_correction=use_correction)
                    self._load_test_mixers.append(mixer)
                    added += 1

        # Sync load-test mixers to current position via timeline (already set by primary)
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
