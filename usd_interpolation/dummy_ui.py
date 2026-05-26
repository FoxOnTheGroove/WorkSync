import numpy as np
import omni.usd
import omni.ui as ui
from pxr import Gf, Usd, UsdGeom, UsdShade, Vt

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

        # primary가 타임라인을 구동한다; 나머지 mixer는 데이터만 baked.
        # mixer 인스턴스는 UVMixerService가 보유하고, UI는 key만 들고 있는다.
        self._primary_key: str | None = None
        self._load_test_keys: list[str] = []  # 복제된 테스트 prim mixer key 들
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

    def _all_keys(self) -> list[str]:
        keys = [self._primary_key] if self._primary_key else []
        return keys + self._load_test_keys

    def _apply_load_test_correction(self) -> None:
        # 복제 메쉬에 대한 보정은 primary가 트리거하지 못하므로
        # 더미 UI 쪽에서 직접 호출한다.
        for k in self._load_test_keys:
            UVMixerService.apply_correction(k)

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

        # load-test 정리
        for k in self._load_test_keys:
            UVMixerService.destroy(k)
        self._load_test_keys = []

        self._src_paths = list(paths)
        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True

        target_path = self._target_path_field.model.get_value_as_string().strip() \
            if self._target_path_field else None
        target_path = target_path or None
        key = target_path or "__primary__"

        # 타겟이 바뀌면 이전 primary 폐기, 아니면 같은 mixer 재로드(구독 유지)
        if self._primary_key and self._primary_key != key:
            UVMixerService.unsubscribe(self._primary_key, self._on_t_changed)
            UVMixerService.destroy(self._primary_key)
            self._primary_key = None
        if self._primary_key is None:
            UVMixerService.create(target_path, key=key)
            UVMixerService.subscribe(key, self._on_t_changed)
            self._primary_key = key

        warnings = UVMixerService.load(key, *paths)
        UVMixerService.set_correction(key, use_correction)
        self._slider.enabled = True
        UVMixerService.set_value(key, 0.0)

        src = UVMixerService.get_instance(key)
        n_meshes = len(src._st_maps[0]) if src and src._st_maps else 0
        status = f"1 mixer ({n_meshes} mesh(es), {len(paths)} source(s))"
        if warnings:
            status += f" | {len(warnings)} skipped"
        self._set_status(status)

    def _on_correction_changed(self, model):
        enabled = bool(model.get_value_as_bool())
        for k in self._all_keys():
            UVMixerService.set_correction(k, enabled)
        if self._primary_key:
            UVMixerService.set_value(self._primary_key,
                                     UVMixerService.get_value(self._primary_key))

    def _on_play_clicked(self):
        if not self._primary_key:
            return
        if UVMixerService.is_playing(self._primary_key):
            UVMixerService.stop(self._primary_key)
            self._apply_load_test_correction()
            self._btn_play.text = "Play ▶"
        else:
            UVMixerService.play(self._primary_key)
            self._btn_play.text = "Stop ■"

    def _on_reverse_changed(self, model):
        if self._primary_key:
            UVMixerService.set_forward(self._primary_key, not model.get_value_as_bool())

    def _on_loop_changed(self, model):
        if self._primary_key:
            UVMixerService.set_loop(self._primary_key, model.get_value_as_bool())

    def _on_slider_changed(self, model):
        if self._primary_key and UVMixerService.is_playing(self._primary_key):
            return
        t = model.get_value_as_float()
        self._t_label.text = f"t: {t:.3f}"
        if self._primary_key:
            UVMixerService.set_value(self._primary_key, t)

    def _on_speed_changed(self, model):
        speed = model.get_value_as_float()
        if self._primary_key:
            UVMixerService.set_speed(self._primary_key, speed)
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
        if not self._primary_key:
            return
        if not UVMixerService.is_playing(self._primary_key):
            # 수동 seek (슬라이더 등) — 복제 메쉬 보정
            self._apply_load_test_correction()
            if self._btn_play:
                self._btn_play.text = "Play ▶"
        else:
            # 재생 중 — 패스 끝 또는 보정 알림 시점에만 load_test 보정
            if t <= 0.0 or t >= 1.0:
                self._apply_load_test_correction()

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        if self._primary_key:
            UVMixerService.unsubscribe(self._primary_key, self._on_t_changed)
        UVMixerService.destroy_all()
        self._primary_key = None
        self._load_test_keys = []
        if self._window:
            self._window.destroy()
            self._window = None

    # ── 디버그 헬퍼 (메쉬 복제 / 정리) ────────────────────

    def _duplicate_meshes(self, n: int) -> int:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None or not self._primary_key or not self._src_paths:
            return 0
        orig_mesh_paths = UVMixerService.get_mesh_paths(self._primary_key)
        if not orig_mesh_paths:
            return 0

        use_correction = bool(self._correction_cb.model.get_value_as_bool()) if self._correction_cb else True
        session = pxr_stage.GetSessionLayer()
        grid_cols = max(1, int(n ** 0.5))
        spacing = 80.0
        added = 0

        # load(*paths)의 _remap 결과와 경로가 일치하도록 서브구조를 보존한다.
        # primary가 target_path로 remap됐으면 그 길이, 아니면 source root 기준으로 strip.
        target = UVMixerService.get_target_path(self._primary_key)

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
                shift = copy_idx % len(self._src_paths)
                shifted = self._src_paths[shift:] + self._src_paths[:shift]
                key = f"loadtest_{copy_idx:04d}"
                UVMixerService.create(group_path, key=key)
                UVMixerService.load(key, *shifted)
                UVMixerService.set_correction(key, use_correction)
                self._load_test_keys.append(key)
                added += 1

        if self._primary_key:
            UVMixerService.set_value(self._primary_key,
                                     UVMixerService.get_value(self._primary_key))
        return added

    def _clear_load_test_prims(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            pxr_stage.RemovePrim(LOAD_TEST_ROOT)
        for k in self._load_test_keys:
            UVMixerService.destroy(k)
        self._load_test_keys = []
