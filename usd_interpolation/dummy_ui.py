import omni.usd
import omni.ui as ui

from .interpolation import UVMixer

NUM_FILES = 5


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None
        self._fields: list[ui.StringField] = []
        self._btn_play: ui.Button | None = None
        self._btn_reverse: ui.Button | None = None
        self._btn_loop: ui.Button | None = None
        self._btn_rev_loop: ui.Button | None = None

    def build_ui(self):
        UVMixer.init(num_slots=NUM_FILES, play_duration=2.5)
        UVMixer.subscribe(self._on_t_changed)

        self._window = ui.Window("USD UV Interpolator", width=500, height=60 * NUM_FILES + 100)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                for i in range(NUM_FILES):
                    with ui.HStack(height=24, spacing=4):
                        ui.Label(f"File {i}:", width=50)
                        field = ui.StringField()
                        field.model.set_value(f"/path/to/file{i}.usd")
                        self._fields.append(field)
                        idx = i
                        ui.Button("Load", width=50,
                                  clicked_fn=lambda _idx=idx: self._on_load(_idx))

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
                    self._btn_loop = ui.Button("Loop ↺", width=74,
                                               clicked_fn=self._on_loop_clicked)
                    self._btn_rev_loop = ui.Button("Rev Loop ↺", width=95,
                                                   clicked_fn=self._on_rev_loop_clicked)
                    ui.Button("Refresh", width=70,
                              clicked_fn=self._on_refresh_clicked)

    def _on_load(self, idx: int):
        path = self._fields[idx].model.get_value_as_string().strip()
        if idx == 0:
            omni.usd.get_context().open_stage(path)
        if not UVMixer.load(path, idx):
            self._set_status(f"ERROR: failed to load File {idx}")
            return
        loaded = UVMixer.get_loaded_slots()
        self._set_status(f"File {idx} loaded  slots:{loaded}")
        self._slider.enabled = len(loaded) >= 2

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

    def _on_loop_clicked(self):
        if UVMixer.is_playing():
            UVMixer.stop()
        else:
            UVMixer.play(forward=True, loop=True)
            self._btn_loop.text = "Stop ■"

    def _on_rev_loop_clicked(self):
        if UVMixer.is_playing():
            UVMixer.stop()
        else:
            UVMixer.play(forward=False, loop=True)
            self._btn_rev_loop.text = "Stop ■"

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
            if self._btn_loop:
                self._btn_loop.text = "Loop ↺"
            if self._btn_rev_loop:
                self._btn_rev_loop.text = "Rev Loop ↺"

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        UVMixer.unsubscribe(self._on_t_changed)
        UVMixer.destroy()
        if self._window:
            self._window.destroy()
            self._window = None
