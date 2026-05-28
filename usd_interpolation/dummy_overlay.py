import omni.ui as ui

from .UVMixer_service import UVMixerService

try:
    import morph.hytwin_viewportwidget_extension as _hytwin_vp_wg
except ImportError:
    _hytwin_vp_wg = None

OVERLAY_W = 260
OVERLAY_H = 80
_MARGIN = 8

_WINDOW_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR
    | ui.WINDOW_FLAGS_NO_RESIZE
    | ui.WINDOW_FLAGS_NO_SCROLLBAR
    | ui.WINDOW_FLAGS_NO_COLLAPSE
    | ui.WINDOW_FLAGS_NO_MOVE
)


def _find_vph(target_path: str):
    if _hytwin_vp_wg is None:
        return None
    for vph in _hytwin_vp_wg.ViewportWidgetHost.get_instances():
        if vph.prim_header_path.rstrip("/") == target_path.rstrip("/"):
            return vph
    return None


def _calc_overlay_pos(vph) -> tuple[int, int]:
    frame = vph.frame
    x = int(frame.screen_position_x + frame.computed_width  - OVERLAY_W - _MARGIN)
    y = int(frame.screen_position_y + frame.computed_height - OVERLAY_H - _MARGIN)
    return x, y


class ViewportOverlayPanel:

    def __init__(self, key: str, vph, mgr: 'OverlayManager'):
        self._key = key
        self._mgr = mgr
        self._vph = vph
        self._in_tick = False
        self._in_sync = False

        x, y = _calc_overlay_pos(vph)
        self._window = ui.Window(
            f"__overlay_{key}__",
            width=OVERLAY_W, height=OVERLAY_H,
            position_x=x, position_y=y,
            flags=_WINDOW_FLAGS,
        )
        self._window.frame.style = {"background_color": 0xCC151515}
        self._widgets: dict = {}
        self._build()

        vph.frame.set_computed_content_size_changed_fn(self._on_viewport_resized)

        sp = UVMixerService.shared_player
        sp.subscribe_tick(self._on_tick)
        sp.subscribe_stopped(self._on_stopped)

        mixer = UVMixerService.get_instance(key)
        if mixer:
            mixer.own_player.subscribe_tick(self._on_own_tick)
            mixer.own_player.subscribe_stopped(self._on_own_stopped)

    def _build(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=1, style={"margin": 2}):

                # 행1: ▶/■ | R ☐ | L ☐ | key label
                with ui.HStack(height=16, spacing=3):
                    btn_play = ui.Button("▶", width=22, height=14,
                                         clicked_fn=self._on_play,
                                         style={"font_size": 10})
                    rev_cb = ui.CheckBox(width=14, height=14)
                    rev_cb.model.add_value_changed_fn(self._on_reverse)
                    ui.Label("R", width=12, height=14,
                             style={"font_size": 10})
                    loop_cb = ui.CheckBox(width=14, height=14)
                    loop_cb.model.add_value_changed_fn(self._on_loop)
                    ui.Label("L", width=12, height=14,
                             style={"font_size": 10})
                    ui.Spacer()
                    key_str = self._key
                    ui.Label(key_str[-18:] if len(key_str) > 18 else key_str,
                             height=14,
                             style={"color": 0xFF888888, "font_size": 10})

                # 행2: t 슬라이더
                with ui.HStack(height=16, spacing=3):
                    t_label = ui.Label("t:.000", width=42,
                                       style={"font_size": 10})
                    slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    slider.model.add_value_changed_fn(self._on_slider)

                # 행3: Speed 슬라이더
                with ui.HStack(height=16, spacing=3):
                    spd_label = ui.Label("1.0x", width=30,
                                          style={"font_size": 10})
                    spd_sl = ui.FloatSlider(min=0.1, max=5.0, step=0.1)
                    spd_sl.model.set_value(1.0)
                    spd_sl.model.add_value_changed_fn(self._on_speed)

        self._widgets = {
            'btn_play': btn_play,
            'rev_cb':   rev_cb,
            'loop_cb':  loop_cb,
            't_label':  t_label,
            'slider':   slider,
            'spd_label': spd_label,
            'spd_sl':   spd_sl,
        }

    def _own_player(self):
        mixer = UVMixerService.get_instance(self._key)
        return mixer.own_player if mixer else None

    # ── player 콜백 ─────────────────────────────────────────────

    def _on_tick(self, t: float, correction: bool) -> None:
        if not UVMixerService.is_synced():
            return
        self._in_tick = True
        self._widgets['slider'].model.set_value(t)
        self._widgets['t_label'].text = f"t:{t:.3f}"
        self._in_tick = False

    def _on_own_tick(self, t: float, correction: bool) -> None:
        if UVMixerService.is_synced():
            return
        self._in_tick = True
        self._widgets['slider'].model.set_value(t)
        self._widgets['t_label'].text = f"t:{t:.3f}"
        self._in_tick = False

    def _on_stopped(self) -> None:
        if UVMixerService.is_synced():
            self._widgets['btn_play'].text = "▶"

    def _on_own_stopped(self) -> None:
        if not UVMixerService.is_synced():
            self._widgets['btn_play'].text = "▶"

    # ── 컨트롤 콜백 ────────────────────────────────────────────

    def _on_play(self) -> None:
        if UVMixerService.is_synced():
            sp = UVMixerService.shared_player
            if sp.is_playing():
                sp.stop()
            else:
                sp.play()
                self._widgets['btn_play'].text = "■"
                self._mgr._sync_play(self._key, playing=True)
        else:
            op = self._own_player()
            if not op:
                return
            if op.is_playing():
                op.stop()
            else:
                op.play()
                self._widgets['btn_play'].text = "■"

    def _on_slider(self, model) -> None:
        if self._in_tick:
            return
        t = model.get_value_as_float()
        if UVMixerService.is_synced():
            sp = UVMixerService.shared_player
            if not sp.is_playing():
                sp.set_t(t)
        else:
            op = self._own_player()
            if op and not op.is_playing():
                op.set_t(t)

    def _on_reverse(self, model) -> None:
        if self._in_sync:
            return
        reverse = model.get_value_as_bool()
        if UVMixerService.is_synced():
            UVMixerService.shared_player.set_forward(not reverse)
            self._mgr._sync_reverse(self._key, reverse)
        else:
            op = self._own_player()
            if op:
                op.set_forward(not reverse)

    def _on_loop(self, model) -> None:
        if self._in_sync:
            return
        loop = model.get_value_as_bool()
        if UVMixerService.is_synced():
            UVMixerService.shared_player.set_loop(loop)
            self._mgr._sync_loop(self._key, loop)
        else:
            op = self._own_player()
            if op:
                op.set_loop(loop)

    def _on_speed(self, model) -> None:
        if self._in_sync:
            return
        spd = model.get_value_as_float()
        self._widgets['spd_label'].text = f"{spd:.1f}x"
        if UVMixerService.is_synced():
            UVMixerService.shared_player.set_speed(spd)
            self._mgr._sync_speed(self._key, spd)
        else:
            op = self._own_player()
            if op:
                op.set_speed(spd)

    # ── sync 상태 변경 시 즉각 갱신 ─────────────────────────────

    def refresh_from_player(self) -> None:
        """sync ON/OFF 토글 후 현재 활성 player 상태를 위젯에 반영한다."""
        if UVMixerService.is_synced():
            p = UVMixerService.shared_player
        else:
            mixer = UVMixerService.get_instance(self._key)
            p = mixer.own_player if mixer else UVMixerService.shared_player
        self._in_sync = True
        self._in_tick = True
        self._widgets['rev_cb'].model.set_value(not p.forward)
        self._widgets['loop_cb'].model.set_value(p.loop)
        self._widgets['spd_sl'].model.set_value(p.speed)
        self._widgets['spd_label'].text = f"{p.speed:.1f}x"
        self._widgets['slider'].model.set_value(p.t)
        self._widgets['t_label'].text = f"t:{p.t:.3f}"
        self._in_sync = False
        self._in_tick = False

    # ── 외부 sync 수신 ──────────────────────────────────────────

    def sync_reverse(self, reverse: bool) -> None:
        self._in_sync = True
        self._widgets['rev_cb'].model.set_value(reverse)
        self._in_sync = False

    def sync_loop(self, loop: bool) -> None:
        self._in_sync = True
        self._widgets['loop_cb'].model.set_value(loop)
        self._in_sync = False

    def sync_speed(self, spd: float) -> None:
        self._in_sync = True
        self._widgets['spd_sl'].model.set_value(spd)
        self._widgets['spd_label'].text = f"{spd:.1f}x"
        self._in_sync = False

    def sync_play(self, playing: bool) -> None:
        self._widgets['btn_play'].text = "■" if playing else "▶"

    # ── 뷰포트 리사이즈 대응 ────────────────────────────────────

    def _on_viewport_resized(self) -> None:
        if self._window and self._vph:
            x, y = _calc_overlay_pos(self._vph)
            self._window.position_x = x
            self._window.position_y = y

    # ── 라이프사이클 ────────────────────────────────────────────

    def destroy(self) -> None:
        if self._vph:
            self._vph.frame.set_computed_content_size_changed_fn(None)
            self._vph = None
        sp = UVMixerService.shared_player
        sp.unsubscribe_tick(self._on_tick)
        sp.unsubscribe_stopped(self._on_stopped)
        mixer = UVMixerService.get_instance(self._key)
        if mixer:
            mixer.own_player.unsubscribe_tick(self._on_own_tick)
            mixer.own_player.unsubscribe_stopped(self._on_own_stopped)
        if self._window:
            self._window.destroy()
            self._window = None


class OverlayManager:

    def __init__(self):
        self._panels: dict[str, ViewportOverlayPanel] = {}

    def refresh_all(self) -> None:
        for panel in self._panels.values():
            panel.refresh_from_player()

    def on_mixer_loaded(self, key: str, target_path: str) -> None:
        self._remove_panel(key)
        vph = _find_vph(target_path)
        if vph is None:
            return
        self._panels[key] = ViewportOverlayPanel(key, vph, mgr=self)

    def on_mixer_destroyed(self, key: str) -> None:
        self._remove_panel(key)

    def _remove_panel(self, key: str) -> None:
        panel = self._panels.pop(key, None)
        if panel:
            panel.destroy()

    # ── 교차 패널 동기화 ────────────────────────────────────────

    def _sync_reverse(self, src: str, reverse: bool) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_reverse(reverse)

    def _sync_loop(self, src: str, loop: bool) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_loop(loop)

    def _sync_speed(self, src: str, spd: float) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_speed(spd)

    def _sync_play(self, src: str, playing: bool) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_play(playing)

    def clear_panels(self) -> None:
        """모든 패널을 destroy하고 레지스트리를 비운다 (mixer Clear 시 호출)."""
        for panel in list(self._panels.values()):
            panel.destroy()
        self._panels.clear()

    def destroy(self) -> None:
        for panel in list(self._panels.values()):
            panel.destroy()
        self._panels.clear()
