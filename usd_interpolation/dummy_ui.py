import omni.usd
import omni.ui as ui

from .interpolation import UVMixer

NUM_FILES = 5

_DIRTY_OPTIONS = ["none", "fvli", "faceVertexIndices", "faceVertexCounts"]


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None
        self._field: ui.StringField | None = None
        self._btn_play: ui.Button | None = None
        self._btn_reverse: ui.Button | None = None
        self._radio_collection: ui.RadioCollection | None = None

    def build_ui(self):
        UVMixer.init(num_slots=NUM_FILES, play_duration=2.5, dirty_attr="fvli")
        UVMixer.subscribe(self._on_t_changed)

        self._window = ui.Window("USD UV Interpolator", width=500, height=280)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                ui.Label("Paths (space or newline separated):", height=18)
                self._field = ui.StringField(height=24)
                self._field.model.set_value("/path/to/file0.usd /path/to/file1.usd")

                with ui.HStack(height=24, spacing=4):
                    ui.Button("Load All", width=80, clicked_fn=self._on_load_all)
                    ui.Spacer(width=8)
                    self._radio_collection = ui.RadioCollection()
                    for i, opt in enumerate(_DIRTY_OPTIONS):
                        with ui.HStack(width=0, spacing=2):
                            ui.RadioButton(
                                radio_collection=self._radio_collection,
                                width=20, height=20,
                            )
                            ui.Label(opt, width=ui.Pixel(len(opt) * 7 + 4), height=20)
                    self._radio_collection.model.set_value(
                        _DIRTY_OPTIONS.index("fvli")
                    )
                    self._radio_collection.model.add_value_changed_fn(
                        self._on_dirty_changed
                    )

                self._status_label = ui.Label("Status: Not loaded", height=20)

                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.000", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

                with ui.HStack(height=24, spacing=8):
                    self._btn_play = ui.Button("Play ▶", width=80,
                                               clicked_fn=self._on_play_clicked)
                    self._btn_reverse = ui.Button("Reverse ◄", width=90,
                                                  clicked_fn=self._on_reverse_clicked)
                    ui.Button("Refresh", width=70,
                              clicked_fn=self._on_refresh_clicked)

    def _on_load_all(self):
        raw = self._field.model.get_value_as_string()
        paths = [p for p in raw.split() if p]
        if not paths:
            self._set_status("ERROR: no paths")
            return
        if paths[0]:
            omni.usd.get_context().open_stage(paths[0])
        ok = 0
        for idx, path in enumerate(paths[:NUM_FILES]):
            if UVMixer.load(path, idx):
                ok += 1
            else:
                self._set_status(f"ERROR: failed slot {idx} ({path})")
                return
        loaded = UVMixer.get_loaded_slots()
        self._set_status(f"{ok} file(s) loaded  slots:{loaded}")
        self._slider.enabled = len(loaded) >= 2

    def _on_dirty_changed(self, model):
        attr = _DIRTY_OPTIONS[model.get_value_as_int()]
        UVMixer.set_dirty_attr(attr)
        self._set_status(f"dirty_attr → {attr}")

    def _on_refresh_clicked(self):
        UVMixer.set_t(UVMixer.get_t())

    def _on_play_clicked(self):
        if UVMixer.is_playing():
            UVMixer.stop()
        else:
            UVMixer.play(forward=True)
            self._btn_play.text = "Stop ■"

    def _on_reverse_clicked(self):
        if UVMixer.is_playing():
            UVMixer.stop()
        else:
            UVMixer.play(forward=False)
            self._btn_reverse.text = "Stop ■"

    def _on_slider_changed(self, model):
        if UVMixer.is_playing():
            return
        t = model.get_value_as_float()
        self._t_label.text = f"t: {t:.3f}"
        UVMixer.set_t(t)

    def _on_t_changed(self, t: float):
        if self._slider:
            self._slider.model.set_value(t)
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        if not UVMixer.is_playing():
            if self._btn_play:
                self._btn_play.text = "Play ▶"
            if self._btn_reverse:
                self._btn_reverse.text = "Reverse ◄"

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        UVMixer.unsubscribe(self._on_t_changed)
        UVMixer.destroy()
        if self._window:
            self._window.destroy()
            self._window = None


    def _on_play_clicked(self):
        if UVMixer.is_playing():
            UVMixer.stop()
        else:
            UVMixer.play(forward=True)
            self._btn_play.text = "Stop ■"

    def _on_reverse_clicked(self):
        if UVMixer.is_playing():
            UVMixer.stop()
        else:
            UVMixer.play(forward=False)
            self._btn_reverse.text = "Stop ■"

    def _on_slider_changed(self, model):
        if UVMixer.is_playing():
            return
        t = model.get_value_as_float()
        self._t_label.text = f"t: {t:.3f}"
        UVMixer.set_t(t)

    def _on_t_changed(self, t: float):
        if self._slider:
            self._slider.model.set_value(t)
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        if not UVMixer.is_playing():
            if self._btn_play:
                self._btn_play.text = "Play ▶"
            if self._btn_reverse:
                self._btn_reverse.text = "Reverse ◄"

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        UVMixer.unsubscribe(self._on_t_changed)
        UVMixer.destroy()
        if self._window:
            self._window.destroy()
            self._window = None
