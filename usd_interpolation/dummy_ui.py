import asyncio

import omni.usd
import omni.ui as ui

from . import UVMixer as _uv_mod
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

        self._correction_idx: int = 1
        self._in_sync_change: bool = False

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=420, height=230)
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
                        "Timeline", width=86, clicked_fn=self._on_mode_timeline)
                    self._mode_direct_btn = ui.Button(
                        "Direct", width=72, clicked_fn=self._on_mode_direct)
                    ui.Spacer()
                    self._sync_cb = ui.CheckBox(width=20, height=20)
                    self._sync_cb.model.set_value(True)
                    self._sync_cb.model.add_value_changed_fn(self._on_sync_changed)
                    ui.Label("Sync", width=36, height=20)
                self._refresh_mode_buttons()

                ui.Spacer()

                # ── Clear ─────────────────────────────────────────────
                ui.Button("Clear All Mixers", height=28, clicked_fn=self._on_clear)

    # ── 헬퍼 ─────────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def _current_correction_mode(self) -> str:
        return _CORRECTION_MODES[self._correction_idx]

    def _refresh_mode_buttons(self) -> None:
        is_tl = (_uv_mod.UV_INTERP_MODE == 'timeline')
        if self._mode_timeline_btn:
            self._mode_timeline_btn.text = "● Timeline" if is_tl else "Timeline"
            self._mode_timeline_btn.enabled = not is_tl
        if self._mode_direct_btn:
            self._mode_direct_btn.text = "● Direct" if not is_tl else "Direct"
            self._mode_direct_btn.enabled = is_tl
        if self._sync_cb:
            self._sync_cb.enabled = not is_tl

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

        key = target_path or f"mixer_{len(UVMixerService.keys())}"

        prim_changed = (
            UVMixerService.get_instance(key) is not None
            and UVMixerService.get_target_path(key) != target_path
        )
        if prim_changed:
            UVMixerService.destroy(key)

        if UVMixerService.get_instance(key) is None:
            UVMixerService.create(target_path, key=key)

        self._set_status("Loading…")
        asyncio.ensure_future(self._load_async(key, paths))

    async def _load_async(self, key: str, paths: 'list[str]') -> None:
        try:
            warnings = await UVMixerService.load(key, paths, panel_on=True)
        except Exception as e:
            self._set_status(f"ERROR: {e}")
            return
        UVMixerService.set_correction_mode(key, self._current_correction_mode())
        UVMixerService.shared_player.set_t(0.0)

        n_meshes = len(UVMixerService.get_mesh_paths(key))
        all_keys = UVMixerService.keys()
        status = f"{len(all_keys)} mixer(s) — {n_meshes} mesh(es), {len(paths)} src"
        if warnings:
            status += f" | {len(warnings)} skipped"
        self._set_status(status)

    def _on_correction_changed(self, model, item) -> None:
        idx = model.get_item_value_model(item).get_value_as_int()
        self._correction_idx = idx
        mode = _CORRECTION_MODES[idx]
        for k in UVMixerService.keys():
            UVMixerService.set_correction_mode(k, mode)
        if UVMixerService.keys():
            UVMixerService.reapply()

    # ── 콜백: 모드 ───────────────────────────────────────────────────

    def _on_mode_timeline(self) -> None:
        _uv_mod.UV_INTERP_MODE = 'timeline'
        self._refresh_mode_buttons()

    def _on_mode_direct(self) -> None:
        _uv_mod.UV_INTERP_MODE = 'direct'
        self._refresh_mode_buttons()

    # ── 콜백: sync ───────────────────────────────────────────────────

    def _on_sync_changed(self, model) -> None:
        if self._in_sync_change:
            return
        synced = model.get_value_as_bool()
        ref_key = self._target_path_field.model.get_value_as_string().strip() \
            if self._target_path_field else ""
        ok = UVMixerService.set_sync_all(synced, ref_key=ref_key)
        if not ok:
            self._in_sync_change = True
            model.set_value(False)
            self._in_sync_change = False

    # ── 콜백: clear ──────────────────────────────────────────────────

    def _on_clear(self) -> None:
        UVMixerService.destroy_all()
        UVMixerService.reset()
        self._set_status("Cleared")

    # ── 라이프사이클 ─────────────────────────────────────────────────

    def destroy(self):
        # 서비스 라이프사이클은 extension이 책임진다. UI는 자기 윈도우만 정리.
        if self._window:
            self._window.destroy()
            self._window = None
