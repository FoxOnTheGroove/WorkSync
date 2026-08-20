import asyncio

import omni.ui as ui

from .twinview_service import TwinViewService as tv


class DummyUI:

    WINDOW_TITLE = "TwinSub"

    DEFAULT_PRIM_PATH = tv.DEFAULT_PRIM_PATH

    def __init__(self):
        self._window = None
        self._uri_model = None
        self._path_model = None
        self._prim_path_model = None
        self._deform_model = None
        self._step_model = None
        self._selected_label = None
        self._sim_time_label = None
        self._status_label = None

        self._key = ""
        self._suppress = False

        self._keys_frame = None
        self._io_frame = None
        self._input_models = {}
        self._output_labels = {}

    def build_ui(self):
        self._window = ui.Window(self.WINDOW_TITLE, width=560, height=620)

        with self._window.frame:
            with ui.VStack(spacing=6, height=0):

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Prim Path", width=70)
                    self._prim_path_model = ui.StringField().model
                    self._prim_path_model.set_value(self.DEFAULT_PRIM_PATH)

                ui.Separator(height=8)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("S3 URI", width=70)
                    self._uri_model = ui.StringField().model

                ui.Button("Load", height=28, clicked_fn=self._on_s3_load)

                ui.Separator(height=8)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Path", width=70)
                    self._path_model = ui.StringField().model

                ui.Button("Load", height=28, clicked_fn=self._on_path_load)

                ui.Separator(height=8)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Loaded")
                    ui.Button("Refresh", width=80, clicked_fn=self._on_refresh)

                self._keys_frame = ui.Frame(height=0)

                ui.Separator(height=8)

                self._selected_label = ui.Label("")

                ui.Button("Show", height=28, clicked_fn=self._on_show)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Deform", width=70)
                    self._deform_model = ui.FloatField().model
                    self._deform_model.add_value_changed_fn(self._on_deform_changed)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Step Size", width=70)
                    self._step_model = ui.FloatField().model
                    self._step_model.add_value_changed_fn(self._on_step_changed)

                with ui.HStack(spacing=6, height=28):
                    ui.Button("Play", clicked_fn=self._on_play)
                    ui.Button("Stop", clicked_fn=self._on_stop)

                self._sim_time_label = ui.Label("")
                self._status_label = ui.Label("")

                ui.Separator(height=8)

                self._io_frame = ui.Frame(height=0)

        tv.set_on_time(self._refresh_sim_time)
        tv.set_on_updated(self._refresh_outputs)

        tv.set_on_loaded(self._sync_from_service)

        self._sync_from_service()

    def destroy(self):
        tv.set_on_loaded(None)
        tv.set_on_time(None)
        tv.set_on_updated(None)

        self._uri_model = None
        self._path_model = None
        self._prim_path_model = None
        self._deform_model = None
        self._step_model = None
        self._selected_label = None
        self._sim_time_label = None
        self._status_label = None

        self._input_models = {}
        self._output_labels = {}
        self._keys_frame = None
        self._io_frame = None

        if self._window:
            self._window.destroy()
            self._window = None

    def _on_s3_load(self):
        asyncio.ensure_future(
            self._s3_load_async(self._uri_model.get_value_as_string(),
                                self._prim_path_model.get_value_as_string()))

    async def _s3_load_async(self, s3_uri, prim_path):
        self._set_status("downloading ...")

        try:
            key = await tv.download_twin_async(s3_uri, prim_path)
        except Exception as exc:
            self._set_status("s3 load 실패: {}".format(exc))
            return

        self._select(key)
        self._set_status("loaded: {} -> {}".format(s3_uri, key))

    def _on_path_load(self):
        asyncio.ensure_future(
            self._path_load_async(self._path_model.get_value_as_string(),
                                  self._prim_path_model.get_value_as_string()))

    async def _path_load_async(self, path, prim_path):
        self._set_status("loading ...")

        try:
            key = await tv.load_twin_async(path, prim_path)
        except Exception as exc:
            self._set_status("load 실패: {}".format(exc))
            return

        self._select(key)
        self._set_status("loaded: {} -> {}".format(path, key))

    def _on_show(self):
        if not self._key:
            self._set_status("선택된 트윈이 없다")
            return

        try:
            shown = tv.rom_show(self._key)
        except Exception as exc:
            self._set_status("show 실패: {}".format(exc))
            return

        if not shown:
            self._set_status("show 실패: rom 선택이 거부됐다")
            return

        self._set_status("shown at {}".format(self._key))

    def _on_unload(self, key):
        if key == self._key:
            self._key = ""

        try:
            tv.unload(key)
        except Exception as exc:
            self._set_status("unload 실패: {}".format(exc))
            return

        self._set_status("unloaded: {}".format(key))

    def _select(self, key):
        self._key = key
        self._sync_from_service()

    def _on_refresh(self):
        self._key = ""

        self._rebuild_keys()
        self._clear_controls()

        keys = tv.list_keys()
        self._set_status("{}개 추적 중. 행을 눌러 대상 선택".format(len(keys))
                         if keys else "로드된 트윈 없음")

    def _clear_controls(self):
        self._input_models = {}
        self._output_labels = {}

        if self._io_frame:
            self._io_frame.clear()

        if self._selected_label:
            self._selected_label.text = "selected: -"

        if self._sim_time_label:
            self._sim_time_label.text = ""

        self._suppress = True
        try:
            if self._deform_model:
                self._deform_model.set_value(0.0)
            if self._step_model:
                self._step_model.set_value(0.0)
        finally:
            self._suppress = False

    def _on_play(self):
        if not self._key:
            self._set_status("선택된 트윈이 없다")
            return

        try:
            started = tv.play(self._key)
        except Exception as exc:
            self._set_status("play 실패: {}".format(exc))
            return

        self._set_status("playing" if started else "이미 재생 중")
        self._rebuild_keys()
        self._refresh_sim_time()
        self._refresh_outputs()

    def _on_stop(self):
        if not self._key:
            self._set_status("선택된 트윈이 없다")
            return

        try:
            stopped = tv.stop(self._key)
        except Exception as exc:
            self._set_status("stop 실패: {}".format(exc))
            return

        self._set_status("stopped" if stopped else "재생 중이 아님")
        self._rebuild_keys()
        self._refresh_sim_time()
        self._refresh_outputs()

    def _on_deform_changed(self, model):
        if self._suppress or not self._key:
            return

        try:
            tv.set_deform_scale(self._key, model.get_value_as_float())
        except Exception as exc:
            self._set_status("deform scale 실패: {}".format(exc))

    def _on_step_changed(self, model):
        if self._suppress or not self._key:
            return

        try:
            tv.set_step_size(self._key, model.get_value_as_float())
        except Exception as exc:
            self._set_status("step size 실패: {}".format(exc))

    def _sync_from_service(self):
        if self._prim_path_model is None:
            return

        keys = tv.list_keys()
        if self._key not in keys:
            self._key = keys[0] if keys else ""

        self._rebuild_keys()

        if not self._key:
            self._clear_controls()
            return

        path = tv.get_file_path(self._key)

        if self._selected_label:
            self._selected_label.text = "selected: {} ({})".format(self._key, path)

        self._suppress = True
        try:
            self._prim_path_model.set_value(self._key)
            self._path_model.set_value(path)
        finally:
            self._suppress = False

        self._refresh_scalars()
        self._rebuild_io()

    def _rebuild_keys(self):
        if self._keys_frame is None:
            return

        self._keys_frame.clear()

        with self._keys_frame:
            with ui.VStack(spacing=2, height=0):
                for key in tv.list_keys():
                    with ui.HStack(spacing=6, height=22):
                        ui.Button(self._key_label(key),
                                  clicked_fn=lambda k=key: self._select(k))
                        ui.Button("Unload", width=70,
                                  clicked_fn=lambda k=key: self._on_unload(k))

    def _key_label(self, key):
        mark = "*" if key == self._key else " "
        state = " [playing]" if tv.is_playing(key) else ""
        return "{} {}{}".format(mark, key, state)

    def _refresh_scalars(self):
        if self._deform_model is None or self._step_model is None:
            return

        try:
            deform = tv.get_deform_scale(self._key)
            step = tv.get_step_size(self._key)
        except Exception as exc:
            self._set_status("조회 실패: {}".format(exc))
            return

        self._suppress = True
        try:
            self._deform_model.set_value(deform)
            self._step_model.set_value(step)
        finally:
            self._suppress = False

    def _refresh_sim_time(self):
        if self._sim_time_label is None or not self._key:
            return

        try:
            value = tv.get_simulation_time(self._key)
        except Exception:
            return

        self._sim_time_label.text = "sim time: {:.3f}".format(value)

    def _rebuild_io(self):
        self._input_models = {}
        self._output_labels = {}

        if self._io_frame is None:
            return

        self._io_frame.clear()

        if not self._key:
            return

        try:
            inputs = tv.get_inputs(self._key)
            outputs = tv.get_outputs(self._key)
        except Exception as exc:
            self._set_status("입출력 조회 실패: {}".format(exc))
            return

        with self._io_frame:
            with ui.VStack(spacing=4, height=0):

                ui.Label("Inputs")
                for name in sorted(inputs):
                    with ui.HStack(spacing=6, height=22):
                        ui.Label(name, width=180)

                        model = ui.FloatField().model
                        model.set_value(self._as_float(inputs[name]))

                        model.add_value_changed_fn(
                            lambda m, n=name: self._on_input_changed(n, m)
                        )
                        self._input_models[name] = model

                ui.Separator(height=6)

                ui.Label("Outputs")
                for name in sorted(outputs):
                    with ui.HStack(spacing=6, height=22):
                        ui.Label(name, width=180)
                        self._output_labels[name] = ui.Label(str(outputs[name]))

    def _on_input_changed(self, name, model):
        if self._suppress or not self._key:
            return

        try:
            tv.set_input(self._key, name, model.get_value_as_float())
        except Exception as exc:
            self._set_status("set_input 실패: {}".format(exc))
            return

        self._refresh_outputs()

    def _refresh_outputs(self):
        if not self._output_labels or not self._key:
            return

        try:
            outputs = tv.get_outputs(self._key)
        except Exception:
            return

        for name, label in self._output_labels.items():
            if name in outputs:
                label.text = str(outputs[name])

    @staticmethod
    def _as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _set_status(self, text):
        if self._status_label:
            self._status_label.text = text
