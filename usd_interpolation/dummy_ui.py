import numpy as np
import omni.timeline
import omni.usd
import omni.ui as ui
from pxr import Gf, Usd, UsdGeom, UsdShade, Vt

from .interpolation import UV_INTERP_MODE
from .interpolation_service import UVMixerService

LOAD_TEST_ROOT = "/World/LoadTest"


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None
        self._field: ui.StringField | None = None
        self._btn_play: ui.Button | None = None
        self._reverse_cb: ui.CheckBox | None = None
        self._loop_cb: ui.CheckBox | None = None
        self._correction_cb: ui.CheckBox | None = None
        self._target_path_field: ui.StringField | None = None
        self._speed_label: ui.Label | None = None
        self._dup_field: ui.IntField | None = None
        self._stage_field: ui.StringField | None = None

        # mixer 인스턴스는 UVMixerService가 보유한다. UI는 src_paths만 캐시한다.
        # 재생은 UVMixerService._shared_clock이 담당한다.
        self._src_paths: dict[str, list[str]] = {}  # key별 소스 경로
        self._n_frames: int = 0                    # _seek_timeline용 프레임 수(=소스 개수)
        # shared_clock tick/stopped를 UI에서 수신
        sc = UVMixerService._shared_clock
        sc.subscribe_tick(self._on_clock_tick)
        sc.subscribe_stopped(self._on_clock_stopped)

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

                # ── 타겟 경로 (자동 기입 또는 수동 수정) ──────────────────
                ui.Spacer(height=4)
                ui.Label("Target Path:", height=18)
                self._target_path_field = ui.StringField(height=24)
                self._target_path_field.model.set_value("")

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
                    ui.Spacer(width=8)
                    self._reverse_cb = ui.CheckBox(width=20, height=20)
                    self._reverse_cb.model.add_value_changed_fn(self._on_reverse_changed)
                    ui.Label("Reverse", width=60, height=20)
                    self._loop_cb = ui.CheckBox(width=20, height=20)
                    self._loop_cb.model.add_value_changed_fn(self._on_loop_changed)
                    ui.Label("Loop", width=40, height=20)

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

    def _base_keys(self) -> list[str]:
        """Load All로 등록된 mixer key들(복제 loadtest 제외)."""
        return [k for k in UVMixerService._instances if not k.startswith("loadtest_")]

    def _seek_timeline(self, t: float) -> None:
        """timeline 모드: 전역 USD 타임라인을 t(0..1) 위치로 이동.
        omni 의존성이 있으므로 PlaybackClock 내부가 아닌 UI에 남긴다.
        PlaybackClock tick 콜백(_on_clock_tick)에서 호출된다."""
        if UV_INTERP_MODE != 'timeline' or self._n_frames < 2:
            return
        timeline = omni.timeline.get_timeline_interface()
        tps = timeline.get_time_codes_per_second()
        timeline.set_current_time(t * (self._n_frames - 1) / tps)

    # ── clock 콜백 ───────────────────────────────────────

    def _on_clock_tick(self, t: float, correction: bool) -> None:
        """shared_clock tick 수신 — timeline seek + 슬라이더/레이블 갱신."""
        self._seek_timeline(t)
        if self._slider:
            self._slider.model.set_value(t)
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"

    def _on_clock_stopped(self) -> None:
        """shared_clock 재생 종료 수신 — 버튼 리셋."""
        if self._btn_play:
            self._btn_play.text = "Play ▶"

    # ── 콜백 ───────────────────────────────────────────

    def _on_load_target_prim(self):
        usd_path = self._stage_field.model.get_value_as_string().strip()
        if not usd_path:
            self._set_status("ERROR: no USD file path")
            return
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            self._set_status("ERROR: no active stage")
            return
        # 임시로 열어 default prim 경로 파악 (현재 스테이지 교체 없음)
        tmp_stage = Usd.Stage.Open(usd_path)
        if not tmp_stage:
            self._set_status(f"ERROR: failed to read: {usd_path}")
            return
        default_prim = tmp_stage.GetDefaultPrim()
        if default_prim and default_prim.IsValid():
            prim_name = default_prim.GetName()
        else:
            children = tmp_stage.GetPseudoRoot().GetChildren()
            prim_name = children[0].GetName() if children else "TargetPrim"
        # /World/ 아래에 마운트해 이동 시 ancestral prim 충돌 방지
        target = f"/World/{prim_name}"
        prim = stage.DefinePrim(target)
        prim.GetReferences().AddReference(usd_path)
        if self._target_path_field:
            self._target_path_field.model.set_value(target)
        self._set_status(f"Loaded: {usd_path} → {target}")

    def _on_load_all(self):
        raw = self._field.model.get_value_as_string()
        paths = [p for p in raw.split() if p]
        if not paths:
            self._set_status("ERROR: no paths")
            return
        if len(paths) < 2:
            self._set_status("ERROR: need at least 2 paths")
            return

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            self._set_status("ERROR: no active stage")
            return

        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True

        target_path = self._target_path_field.model.get_value_as_string().strip() \
            if self._target_path_field else None
        target_path = target_path or None
        instances = UVMixerService._instances
        key = target_path or f"mixer_{len(instances)}"

        # 동일 target이면 기존 mixer 재사용(load가 _clear_baked 후 재bake), 아니면 동등하게 추가.
        if key not in instances:
            mixer = UVMixerService.create(target_path, key=key)
            mixer.join_clock(UVMixerService._shared_clock)

        warnings = UVMixerService.load(key, *paths)
        self._src_paths[key] = list(paths)
        self._n_frames = len(paths)
        UVMixerService.set_correction(key, use_correction)
        self._slider.enabled = True
        UVMixerService._shared_clock.set_t(0.0)

        src = UVMixerService.get_instance(key)
        n_meshes = len(src._st_maps[0]) if src and src._st_maps else 0
        status = f"{len(instances)} mixer(s) — {n_meshes} mesh(es), {len(paths)} src"
        if warnings:
            status += f" | {len(warnings)} skipped"
        self._set_status(status)

    def _on_correction_changed(self, model):
        enabled = bool(model.get_value_as_bool())
        for k in UVMixerService._instances:
            UVMixerService.set_correction(k, enabled)
        if UVMixerService._instances:
            UVMixerService._shared_clock.set_t(self._slider.model.get_value_as_float())

    def _on_play_clicked(self):
        if not UVMixerService._instances:
            return
        sc = UVMixerService._shared_clock
        if sc.is_playing():
            sc.stop()
        else:
            sc.play()
            if self._btn_play:
                self._btn_play.text = "Stop ■"

    def _on_reverse_changed(self, model):
        UVMixerService._shared_clock.set_forward(not model.get_value_as_bool())

    def _on_loop_changed(self, model):
        UVMixerService._shared_clock.set_loop(model.get_value_as_bool())

    def _on_slider_changed(self, model):
        if UVMixerService._shared_clock.is_playing():
            return
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        UVMixerService._shared_clock.set_t(t)

    def _on_speed_changed(self, model):
        speed = model.get_value_as_float()
        UVMixerService._shared_clock.set_speed(speed)
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

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        sc = UVMixerService._shared_clock
        sc.unsubscribe_tick(self._on_clock_tick)
        sc.unsubscribe_stopped(self._on_clock_stopped)
        sc.stop()
        UVMixerService.destroy_all()
        self._src_paths = {}
        self._n_frames = 0
        if self._window:
            self._window.destroy()
            self._window = None

    # ── 디버그 헬퍼 (메쉬 복제 / 정리) ────────────────────

    def _duplicate_meshes(self, n: int) -> int:
        pxr_stage = omni.usd.get_context().get_stage()
        base_keys = self._base_keys()
        if pxr_stage is None or not base_keys:
            return 0
        ref_key = base_keys[-1]                      # 가장 최근 로드된 비-loadtest mixer 기준
        ref_src_paths = self._src_paths.get(ref_key)
        if not ref_src_paths:
            return 0
        orig_mesh_paths = UVMixerService.get_mesh_paths(ref_key)
        if not orig_mesh_paths:
            return 0

        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True
        session = pxr_stage.GetSessionLayer()
        grid_cols = max(1, int(n ** 0.5))
        spacing = 80.0
        added = 0

        # load(*paths)의 _remap 결과와 경로가 일치하도록 서브구조를 보존한다.
        # ref가 target_path로 remap됐으면 그 길이, 아니면 source root 기준으로 strip.
        target = UVMixerService.get_target_path(ref_key)

        def _subpath(p: str) -> str:
            if target:
                return p[len(target):]
            source_root = '/' + p.split('/')[1]
            return p[len(source_root):]

        with Usd.EditContext(pxr_stage, session):
            for copy_idx in range(1, n):
                col = copy_idx % grid_cols
                row = copy_idx // grid_cols
                group_path = f"{LOAD_TEST_ROOT}/copy_{copy_idx:04d}"
                grp = UsdGeom.Xform.Define(pxr_stage, group_path)
                UsdGeom.XformCommonAPI(grp).SetTranslate(
                    Gf.Vec3d(col * spacing, 0.0, row * spacing)
                )

                for orig_path in orig_mesh_paths:
                    dst_path = group_path + _subpath(orig_path)
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

                # 소스 경로를 시프트해 load → 타임코드 시프트 효과
                shift = copy_idx % len(ref_src_paths)
                shifted = ref_src_paths[shift:] + ref_src_paths[:shift]
                key = f"loadtest_{copy_idx:04d}"
                dup_mixer = UVMixerService.create(group_path, key=key)
                UVMixerService.load(key, *shifted)
                UVMixerService.set_correction(key, use_correction)
                dup_mixer.join_clock(UVMixerService._shared_clock)
                self._src_paths[key] = list(shifted)
                added += 1

        # 복제 mixer도 동등 참여 — 현재 t를 shared_clock으로 재적용
        UVMixerService._shared_clock.set_t(self._slider.model.get_value_as_float())
        return added

    def _clear_load_test_prims(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            pxr_stage.RemovePrim(LOAD_TEST_ROOT)
        for k in [k for k in list(UVMixerService._instances) if k.startswith("loadtest_")]:
            UVMixerService.destroy(k)
            self._src_paths.pop(k, None)
