import omni.ui as ui

from .UVMixer_service import UVMixerService

try:
    import morph.hytwin_viewportwidget_extension as _hytwin_vp_wg
except ImportError:
    _hytwin_vp_wg = None

OVERLAY_W = 240
OVERLAY_H = 92
_MARGIN = 8

_WINDOW_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR
    | ui.WINDOW_FLAGS_NO_RESIZE
    | ui.WINDOW_FLAGS_NO_SCROLLBAR
    | ui.WINDOW_FLAGS_NO_COLLAPSE
)


def _find_viewport_api(target_path: str):
    if _hytwin_vp_wg is None:
        return None
    for vph in _hytwin_vp_wg.ViewportWidgetHost.get_instances():
        if vph.prim_header_path == target_path:
            return vph.viewport_api
    return None


def _calc_overlay_pos(viewport_api) -> tuple[int, int]:
    win = viewport_api.window
    x = int(win.position_x + win.width  - OVERLAY_W - _MARGIN)
    y = int(win.position_y + win.height - OVERLAY_H - _MARGIN)
    return x, y


class ViewportOverlayPanel:

    def __init__(self, key: str, viewport_api):
        self._key = key
        self._in_tick = False
        self._in_sync = False

        x, y = _calc_overlay_pos(viewport_api)
        self._window = ui.Window(
            f"__overlay_{key}__",
            width=OVERLAY_W, height=OVERLAY_H,
            position_x=x, position_y=y,
            flags=_WINDOW_FLAGS,
        )
        self._window.frame.style = {"background_color": 0xCC151515}
        self._widgets: dict = {}
        self._build()

        sp = UVMixerService._shared_player
        sp.subscribe_tick(self._on_tick)
        sp.subscribe_stopped(self._on_stopped)

    def _build(self) -> None:
        key = self._key
        with self._window.frame:
            with ui.VStack(spacing=2, style={"margin": 4}):

                # 행1: Play/Stop | Rev | Loop | key label
                with ui.HStack(height=20, spacing=4):
                    btn_play = ui.Button("Play ▶", width=60, height=18,
                                         clicked_fn=self._on_play,
                                         style={"font_size": 11})
                    rev_cb = ui.CheckBox(width=16, height=16)
                    rev_cb.model.add_value_changed_fn(self._on_reverse)
                    ui.Label("Rev", width=24, height=16,
                             style={"font_size": 11})
                    loop_cb = ui.CheckBox(width=16, height=16)
                    loop_cb.model.add_value_changed_fn(self._on_loop)
                    ui.Label("Loop", width=30, height=16,
                             style={"font_size": 11})
                    ui.Spacer()
                    ui.Label(key[-16:] if len(key) > 16 else key,
                             height=16,
                             style={"color": 0xFF888888, "font_size": 10})

                # 행2: t 슬라이더
                with ui.HStack(height=20, spacing=4):
                    t_label = ui.Label("t:0.000", width=50,
                                       style={"font_size": 11})
                    slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    slider.model.add_value_changed_fn(self._on_slider)

                # 행3: Speed 슬라이더
                with ui.HStack(height=20, spacing=4):
                    spd_label = ui.Label("spd:1.0x", width=58,
                                          style={"font_size": 11})
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

    # ── player 콜백 ─────────────────────────────────────────────

    def _on_tick(self, t: float, correction: bool) -> None:
        self._in_tick = True
        self._widgets['slider'].model.set_value(t)
        self._widgets['t_label'].text = f"t:{t:.3f}"
        self._in_tick = False

    def _on_stopped(self) -> None:
        self._widgets['btn_play'].text = "Play ▶"

    # ── 컨트롤 콜백 ────────────────────────────────────────────

    def _on_play(self) -> None:
        sp = UVMixerService._shared_player
        if sp.is_playing():
            sp.stop()
        else:
            sp.play()
            self._widgets['btn_play'].text = "Stop ■"

    def _on_slider(self, model) -> None:
        if self._in_tick or UVMixerService._shared_player.is_playing():
            return
        UVMixerService._shared_player.set_t(model.get_value_as_float())

    def _on_reverse(self, model) -> None:
        if self._in_sync:
            return
        UVMixerService._shared_player.set_forward(not model.get_value_as_bool())

    def _on_loop(self, model) -> None:
        if self._in_sync:
            return
        UVMixerService._shared_player.set_loop(model.get_value_as_bool())

    def _on_speed(self, model) -> None:
        if self._in_sync:
            return
        spd = model.get_value_as_float()
        UVMixerService._shared_player.set_speed(spd)
        self._widgets['spd_label'].text = f"spd:{spd:.1f}x"

    # ── 라이프사이클 ────────────────────────────────────────────

    def destroy(self) -> None:
        sp = UVMixerService._shared_player
        sp.unsubscribe_tick(self._on_tick)
        sp.unsubscribe_stopped(self._on_stopped)
        if self._window:
            self._window.destroy()
            self._window = None


class OverlayManager:

    def __init__(self):
        self._panels: dict[str, ViewportOverlayPanel] = {}

    def on_mixer_loaded(self, key: str, target_path: str) -> None:
        self._remove_panel(key)
        vp_api = _find_viewport_api(target_path)
        if vp_api is None:
            return
        self._panels[key] = ViewportOverlayPanel(key, vp_api)

    def on_mixer_destroyed(self, key: str) -> None:
        self._remove_panel(key)

    def _remove_panel(self, key: str) -> None:
        panel = self._panels.pop(key, None)
        if panel:
            panel.destroy()

    def destroy(self) -> None:
        for panel in list(self._panels.values()):
            panel.destroy()
        self._panels.clear()
