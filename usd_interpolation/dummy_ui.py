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

        # _primary가 타임라인을 구동한다; 나머지 mixer는 데이터만 baked.
        self._primary: UVMixer | None = None
        self._mixers: list[UVMixer] = []        # 원본 prim 들
        self._load_test_mixers: list[UVMixer] = []  # 복제된 테스트 prim 들
        self._src_paths: list[str] = []

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=520, height=340)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):

                # ── 타겟 prim 스테이지 ─────────────────────────
                ui.Label("Target Prim USD:", height=18)
                with ui.HStack(height=24, spacing=4):
                    self._stage_field = ui.StringField(height=24)
                    self._stage_field.model.set_value("/path/to/target.usd")
                    ui.Button("Load Target Prim", width=120, clicked_fn=self._on_load_target_prim)

                # ── 경로 입력 ───────────────────────────────────────
                ui.Label("UV Paths (space or newline separated):", height=18)
                self._field = ui.StringField(height=24)
                self._field.model.set_value("/path/to/file0.usd /path/to/file1.usd")

                # ── 로드 + correction 체크박스 ────────────────────
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Load All", width=80, clicked_fn=self._on_load_all)
                    ui.Spacer(width=8)
                    self._correction_cb = ui.CheckBox(width=20, height=20)
                    self._correction_cb.model.set_value(True)
                    self._correction_cb.model.add_value_changed_fn(self._on_correction_changed)
                    ui.Label("Correction", width=100, height=20)

                # ── 상태 표시 ─────────────────────────────────────
                self._status_label = ui.Label("Status: Not loaded", height=20)

                # ── t 슬라이더 ─────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.000", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

                # ── 재생 컨트롤 ─────────────────────────
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

                # ── 속도 슬라이더 ────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("Speed:", width=44)
                    self._speed_label = ui.Label("1.0x", width=34)
                    speed_slider = ui.FloatSlider(min=0.1, max=5.0, step=0.1)
                    speed_slider.model.set_value(1.0)
                    speed_slider.model.add_value_changed_fn(self._on_speed_changed)

                # ── 로드 테스트 (디버그) ──────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("N:", width=18)
                    self._dup_field = ui.IntField(width=50)
                    self._dup_field.model.set_value(10)
                    ui.Button("Duplicate N", width=100,
                              clicked_fn=self._on_duplicate_clicked)
                    ui.Button("Clear", width=60,
                              clicked_fn=self._on_clear_clicked)

    # ── 헬퍼 ────────────────────────────────────────────

    def _all_mixers(self) -> list[UVMixer]:
        return self._mixers + self._load_test_mixers

    def _apply_load_test_correction(self) -> None:
        # 복제 메쉬에 대한 보정은 primary가 트리거하지 못하므로
        # 더미 UI 쪽에서 직접 호출한다.
        for m in self._load_test_mixers:
            m.apply_correction()

    # ── 콜백 ───────────────────────────────────────────

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

        # 이전 상태 정리
        if self._primary:
            self._primary.unsubscribe(self._on_t_changed)
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

        # 1) 각 소스 파일을 한 번씩 열어 모든 메쉬의 st 데이터를 읽는다
        maps_per_file = [UVMixer.make_st_map(p) for p in paths]

        # 2) 모든 소스 파일에 공통으로 존재하는 메쉬만 사용
        common_paths = set(maps_per_file[0].keys())
        for m in maps_per_file[1:]:
            common_paths &= set(m.keys())

        if not common_paths:
            self._set_status("ERROR: no mesh found in all source files")
            self._slider.enabled = False
            return

        # 3) 모든 메쉬를 하나의 mixer로 묶어서 생성
        st_maps = [{path: maps_per_file[i][path] for path in common_paths}
                   for i in range(len(paths))]
        mixer = UVMixer.create(st_maps, use_correction=use_correction)
        self._mixers = [mixer]

        self._primary = mixer
        self._primary.subscribe(self._on_t_changed)
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
            self._apply_load_test_correction()
            self._btn_play.text = "Play ▶"
        else:
            self._primary.play(forward=True)
            self._btn_play.text = "Stop ■"

    def _on_reverse_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._apply_load_test_correction()
            self._btn_reverse.text = "Reverse ◄"
        else:
            self._primary.play(forward=False)
            self._btn_reverse.text = "Stop ■"

    def _on_loop_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._apply_load_test_correction()
            self._btn_loop.text = "Loop ↺"
        else:
            self._primary.play(forward=True, loop=True)
            self._btn_loop.text = "Stop ■"

    def _on_rev_loop_clicked(self):
        if not self._primary:
            return
        if self._primary.is_playing():
            self._primary.stop()
            self._apply_load_test_correction()
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
        if not self._primary:
            return
        if not self._primary.is_playing():
            # 수동 seek (슬라이더 등) — 복제 메쉬 보정
            self._apply_load_test_correction()
            if self._btn_play:
                self._btn_play.text = "Play ▶"
            if self._btn_reverse:
                self._btn_reverse.text = "Reverse ◄"
            if self._btn_loop:
                self._btn_loop.text = "Loop ↺"
            if self._btn_rev_loop:
                self._btn_rev_loop.text = "Rev Loop ↺"
        else:
            # 재생 중 — 매 프레임 load_test 보정 (primary와 동일한 빈도로 fvli 토글)
            self._apply_load_test_correction()

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

    # ── 디버그 헬퍼 (메쉬 복제 / 정리) ────────────────────

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

                # 원본 메쉬 경로 → 복제 경로 매핑
                path_map: dict[str, str] = {}
                for mesh_idx, orig_path in enumerate(orig_mesh_paths):
                    dst_path = f"{group_path}/m{mesh_idx:04d}"
                    path_map[orig_path] = dst_path
                    src_prim = pxr_stage.GetPrimAtPath(orig_path)
                    if not src_prim.IsValid():
                        continue
                    dst_mesh = UsdGeom.Mesh.Define(pxr_stage, dst_path)
                    dst_prim = dst_mesh.GetPrim()

                    # 머티리얼 바인딩 복사
                    binding = UsdShade.MaterialBindingAPI(src_prim).GetDirectBinding()
                    mat_path = binding.GetMaterialPath()
                    if mat_path:
                        mat_prim = pxr_stage.GetPrimAtPath(mat_path)
                        if mat_prim.IsValid():
                            UsdShade.MaterialBindingAPI.Apply(dst_prim).Bind(
                                UsdShade.Material(mat_prim)
                            )

                    # 지오메트리 어트리뷰트 복사
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

                    # fvli 어트리뷰트 복사 — 원본 메쉬와 동일한 토글 동작을 위해
                    # dst_prim에도 fvli가 실제로 authored 되어 있어야 한다.
                    src_fvli = UsdGeom.Mesh(src_prim).GetFaceVaryingLinearInterpolationAttr()
                    fvli_val = src_fvli.Get() if (src_fvli and src_fvli.IsValid()) else None
                    dst_fvli = UsdGeom.Mesh(dst_prim).CreateFaceVaryingLinearInterpolationAttr()
                    if fvli_val is not None:
                        dst_fvli.Set(fvli_val)

                    # st primvar 스켈레톤 복사 (값은 UVMixer가 덮어씀)
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

                # 복제 경로로 재매핑하면서 타임코드를 시프트한 st_maps 구성
                maps = src_mixer._st_maps
                shift = copy_idx % len(maps)
                shifted_orig = maps[shift:] + maps[:shift]
                shifted_maps = [{path_map[op]: arr
                                 for op, arr in tc_map.items() if op in path_map}
                                for tc_map in shifted_orig]
                mixer = UVMixer.create(shifted_maps, use_correction=use_correction)
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
