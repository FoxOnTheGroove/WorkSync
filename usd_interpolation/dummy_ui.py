import omni.timeline
import omni.usd
import omni.ui as ui

from . import UVMixer as _uv_mod          # 모듈 참조 (UV_INTERP_MODE 런타임 변경용)
from .UVMixer_service import UVMixerService

_CORRECTION_MODES = ['none', 'boundary', 'all']


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._field: ui.StringField | None = None
        self._correction_combo: ui.ComboBox | None = None
        self._target_path_field: ui.StringField | None = None
        self._mode_timeline_btn: ui.Button | None = None
        self._mode_direct_btn: ui.Button | None = None
        self._sync_cb: ui.CheckBox | None = None
        self._mixer_vstack: ui.VStack | None = None

        self._correction_idx: int = 1   # 0=none 1=boundary 2=all, 기본=boundary

        # per-mixer 행 위젯 참조
        # {key: {'t_label', 'slider', 'btn_play', 'speed_label', 'speed_sl',
        #        'reverse_cb', 'loop_cb'}}
        self._mixer_rows: dict[str, dict] = {}
        self._in_tick: bool = False          # t슬라이더 갱신 중 재진입 방지
        self._in_sync: bool = False          # speed/reverse/loop 동기화 중 재진입 방지
        self._n_frames: int = 0              # _seek_timeline 용

        # shared_player tick/stopped 구독
        sp = UVMixerService._shared_player
        sp.subscribe_tick(self._on_player_tick)
        sp.subscribe_stopped(self._on_player_stopped)

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=520, height=500)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):

                # ── Target Path ───────────────────────────────────────
                ui.Label("Target Path:", height=18)
                self._target_path_field = ui.StringField(height=24)
                self._target_path_field.model.set_value("")

                # ── UV Paths ──────────────────────────────────────────
                ui.Label("UV Paths (space or newline separated):", height=18)
                self._field = ui.StringField(height=24)
                self._field.model.set_value("/path/to/file0.usd /path/to/file1.usd")

                # ── Load All + Correction ─────────────────────────────
                with ui.HStack(height=24, spacing=4):
                    ui.Button("Load All", width=80, clicked_fn=self._on_load_all)
                    ui.Spacer(width=8)
                    ui.Label("Correction:", width=70, height=24)
                    self._correction_combo = ui.ComboBox(
                        1, "None", "Boundary", "All",
                        width=90, height=24,
                    )
                    self._correction_combo.model.add_item_changed_fn(
                        self._on_correction_changed)

                # ── Status ────────────────────────────────────────────
                self._status_label = ui.Label("Status: Not loaded", height=20)

                # ── Mode & Sync ───────────────────────────────────────
                with ui.HStack(height=24, spacing=8):
                    ui.Label("Mode:", width=40, height=24)
                    self._mode_timeline_btn = ui.Button(
                        "Timeline", width=72, clicked_fn=self._on_mode_timeline)
                    self._mode_direct_btn = ui.Button(
                        "Direct", width=72, clicked_fn=self._on_mode_direct)
                    ui.Spacer()
                    self._sync_cb = ui.CheckBox(width=20, height=20)
                    self._sync_cb.model.set_value(True)
                    self._sync_cb.model.add_value_changed_fn(self._on_sync_changed)
                    ui.Label("Sync", width=36, height=20)
                self._refresh_mode_buttons()

                # ── Mixer 행 (스크롤) ─────────────────────────────────
                ui.Label("Mixers:", height=16)
                scroll = ui.ScrollingFrame(
                    height=264,
                    horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                    style={"background_color": 0xFF1E1E1E,
                           "border_color": 0xFF444444, "border_width": 1},
                )
                with scroll:
                    self._mixer_vstack = ui.VStack(spacing=2)

    # ── 행 추가 ──────────────────────────────────────────────────────

    def _add_mixer_row(self, key: str) -> None:
        """_mixer_vstack 안에 mixer 한 행을 추가하고 _mixer_rows에 등록한다."""
        with self._mixer_vstack:
            with ui.VStack(spacing=1, style={"margin": 4, "margin_bottom": 6}):
                # key 레이블 (구분선 역할)
                ui.Label(f"── {key} ──", height=14,
                         style={"color": 0xFFAAAAAA, "font_size": 11})

                # Play / Reverse / Loop 행
                with ui.HStack(height=20, spacing=6):
                    btn_play = ui.Button("Play ▶", width=72,
                                         clicked_fn=lambda k=key: self._on_mixer_play(k))
                    rev_cb = ui.CheckBox(width=18, height=18)
                    rev_cb.model.add_value_changed_fn(
                        lambda m, k=key: self._on_mixer_reverse(m, k))
                    ui.Label("Reverse", width=56, height=18)
                    loop_cb = ui.CheckBox(width=18, height=18)
                    loop_cb.model.add_value_changed_fn(
                        lambda m, k=key: self._on_mixer_loop(m, k))
                    ui.Label("Loop", width=36, height=18)

                # t 슬라이더 행
                with ui.HStack(height=20, spacing=6):
                    t_label = ui.Label("t: 0.000", width=60)
                    slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    slider.model.add_value_changed_fn(
                        lambda m, k=key: self._on_mixer_slider(m, k))

                # 속도 행
                with ui.HStack(height=20, spacing=6):
                    ui.Label("Speed:", width=44)
                    speed_label = ui.Label("1.0x", width=36)
                    speed_sl = ui.FloatSlider(min=0.1, max=5.0, step=0.1)
                    speed_sl.model.set_value(1.0)
                    speed_sl.model.add_value_changed_fn(
                        lambda m, k=key: self._on_mixer_speed(m, k))

        self._mixer_rows[key] = {
            't_label':    t_label,
            'slider':     slider,
            'btn_play':   btn_play,
            'speed_label': speed_label,
            'speed_sl':   speed_sl,
            'reverse_cb': rev_cb,
            'loop_cb':    loop_cb,
        }

    # ── 헬퍼 ─────────────────────────────────────────────────────────

    def _seek_timeline(self, t: float) -> None:
        """timeline 모드: 전역 USD 타임라인을 t(0..1) 위치로 이동."""
        if _uv_mod.UV_INTERP_MODE != 'timeline' or self._n_frames < 2:
            return
        timeline = omni.timeline.get_timeline_interface()
        tps = timeline.get_time_codes_per_second()
        timeline.set_current_time(t * (self._n_frames - 1) / tps)

    def _set_status(self, text: str) -> None:
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def _current_correction_mode(self) -> str:
        return _CORRECTION_MODES[self._correction_idx]

    def _refresh_mode_buttons(self) -> None:
        """현재 모드 버튼을 시각 강조(활성=disabled 처리로 눌린 상태 표현)."""
        is_tl = (_uv_mod.UV_INTERP_MODE == 'timeline')
        if self._mode_timeline_btn:
            self._mode_timeline_btn.enabled = not is_tl
        if self._mode_direct_btn:
            self._mode_direct_btn.enabled = is_tl
        if self._sync_cb:
            self._sync_cb.enabled = not is_tl   # timeline 모드에서 sync 비활성

    # ── player 콜백 ──────────────────────────────────────────────────

    def _on_player_tick(self, t: float, correction: bool) -> None:
        """shared_player tick — timeline seek + 전체 행 슬라이더/레이블 갱신."""
        self._seek_timeline(t)
        self._in_tick = True
        for row in self._mixer_rows.values():
            row['slider'].model.set_value(t)
            row['t_label'].text = f"t: {t:.3f}"
        self._in_tick = False

    def _on_player_stopped(self) -> None:
        """shared_player 재생 종료 — 전체 행 Play 버튼 리셋."""
        for row in self._mixer_rows.values():
            row['btn_play'].text = "Play ▶"

    # ── 콜백: 로드 ───────────────────────────────────────────────────

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

        target_path = self._target_path_field.model.get_value_as_string().strip() \
            if self._target_path_field else None
        target_path = target_path or None

        instances = UVMixerService._instances
        key = target_path or f"mixer_{len(instances)}"

        prim_changed = (
            UVMixerService.get_instance(key) is not None
            and UVMixerService.get_target_path(key) != target_path
        )
        if prim_changed:
            UVMixerService.destroy(key)
            if self._overlay_mgr:
                self._overlay_mgr.on_mixer_destroyed(key)
            self._mixer_rows.pop(key, None)

        if key not in instances:
            UVMixerService.create(target_path, key=key)
            UVMixerService.apply_sync(key)
            self._add_mixer_row(key)

        warnings = UVMixerService.load(key, *paths)
        self._n_frames = len(paths)
        UVMixerService.set_correction_mode(key, self._current_correction_mode())
        UVMixerService._shared_player.set_t(0.0)

        if self._overlay_mgr:
            self._overlay_mgr.on_mixer_loaded(key, target_path or "")

        src = UVMixerService.get_instance(key)
        n_meshes = len(src._st_maps[0]) if src and src._st_maps else 0
        status = f"{len(instances)} mixer(s) — {n_meshes} mesh(es), {len(paths)} src"
        if warnings:
            status += f" | {len(warnings)} skipped"
        self._set_status(status)

    def _on_correction_changed(self, model, item) -> None:
        idx = model.get_item_value_model(item).get_value_as_int()
        self._correction_idx = idx
        mode = _CORRECTION_MODES[idx]
        for k in UVMixerService._instances:
            UVMixerService.set_correction_mode(k, mode)
        if UVMixerService._instances:
            t = UVMixerService._shared_player._t
            UVMixerService._shared_player.set_t(t)

    # ── 콜백: 모드 ───────────────────────────────────────────────────

    def _on_mode_timeline(self) -> None:
        _uv_mod.UV_INTERP_MODE = 'timeline'
        self._refresh_mode_buttons()

    def _on_mode_direct(self) -> None:
        _uv_mod.UV_INTERP_MODE = 'direct'
        self._refresh_mode_buttons()

    # ── 콜백: sync ───────────────────────────────────────────────────

    def _on_sync_changed(self, model) -> None:
        UVMixerService.set_sync_all(model.get_value_as_bool())

    # ── 콜백: per-mixer ──────────────────────────────────────────────

    def _on_mixer_play(self, key: str) -> None:
        sp = UVMixerService._shared_player
        if sp.is_playing():
            sp.stop()
        else:
            sp.play()
            for row in self._mixer_rows.values():
                row['btn_play'].text = "Stop ■"

    def _on_mixer_slider(self, model, key: str) -> None:
        if self._in_tick or UVMixerService._shared_player.is_playing():
            return
        t = model.get_value_as_float()
        UVMixerService._shared_player.set_t(t)

    def _on_mixer_reverse(self, model, key: str) -> None:
        if self._in_sync:
            return
        forward = not model.get_value_as_bool()
        UVMixerService._shared_player.set_forward(forward)
        self._in_sync = True
        for k, row in self._mixer_rows.items():
            if k != key:
                row['reverse_cb'].model.set_value(not forward)
        self._in_sync = False

    def _on_mixer_loop(self, model, key: str) -> None:
        if self._in_sync:
            return
        loop = model.get_value_as_bool()
        UVMixerService._shared_player.set_loop(loop)
        self._in_sync = True
        for k, row in self._mixer_rows.items():
            if k != key:
                row['loop_cb'].model.set_value(loop)
        self._in_sync = False

    def _on_mixer_speed(self, model, key: str) -> None:
        if self._in_sync:
            return
        speed = model.get_value_as_float()
        UVMixerService._shared_player.set_speed(speed)
        self._in_sync = True
        for k, row in self._mixer_rows.items():
            row['speed_label'].text = f"{speed:.1f}x"
            if k != key:
                row['speed_sl'].model.set_value(speed)
        self._in_sync = False

    # ── 라이프사이클 ─────────────────────────────────────────────────

    def destroy(self):
        sp = UVMixerService._shared_player
        sp.unsubscribe_tick(self._on_player_tick)
        sp.unsubscribe_stopped(self._on_player_stopped)
        sp.stop()
        UVMixerService.destroy_all()
        self._mixer_rows.clear()
        self._mixer_vstack = None
        self._n_frames = 0
        if self._window:
            self._window.destroy()
            self._window = None
