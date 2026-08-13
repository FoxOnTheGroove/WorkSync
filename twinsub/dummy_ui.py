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
        self._sim_time_label = None
        self._status_label = None

        self._suppress = False

        self._io_frame = None
        self._input_models = {}
        self._output_labels = {}

    def build_ui(self):
        self._window = ui.Window(self.WINDOW_TITLE, width=560, height=520)

        with self._window.frame:
            with ui.VStack(spacing=6, height=0):

                with ui.HStack(spacing=6, height=24):
                    ui.Label("S3 URI", width=60)
                    self._uri_model = ui.StringField().model

                ui.Button("Load", height=28, clicked_fn=self._on_s3_load)

                ui.Separator(height=8)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Path", width=60)
                    self._path_model = ui.StringField().model

                ui.Button("Load", height=28, clicked_fn=self._on_path_load)

                ui.Separator(height=8)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Prim Path", width=60)
                    self._prim_path_model = ui.StringField().model
                    self._prim_path_model.set_value(self.DEFAULT_PRIM_PATH)

                ui.Button("Show", height=28, clicked_fn=self._on_show)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Deform", width=60)
                    self._deform_model = ui.FloatField().model
                    self._deform_model.add_value_changed_fn(self._on_deform_changed)

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Step Size", width=60)
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
        self._sim_time_label = None
        self._status_label = None

        self._input_models = {}
        self._output_labels = {}
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
            local_path = await tv.download_twin_async(s3_uri, prim_path)
        except Exception as exc:
            self._sync_path_field()
            self._set_status("s3 load 실패: {}".format(exc))
            return

        self._sync_path_field()
        self._set_status("loaded: {} -> {}".format(local_path, prim_path))

    def _sync_path_field(self):
        if self._path_model is None:
            return

        path = tv.get_local_path()
        if path:
            self._path_model.set_value(path)

    def _on_path_load(self):
        asyncio.ensure_future(
            self._path_load_async(self._path_model.get_value_as_string(),
                                  self._prim_path_model.get_value_as_string()))

    async def _path_load_async(self, path, prim_path):
        self._set_status("loading ...")

        try:
            await tv.load_twin_async(path, prim_path)
        except Exception as exc:
            self._set_status("load 실패: {}".format(exc))
            return

        self._set_status("loaded: {} -> {}".format(path, prim_path))

    def _on_show(self):
        prim_path = self._prim_path_model.get_value_as_string()

        try:
            shown = tv.rom_show(prim_path)
        except Exception as exc:
            self._set_status("show 실패: {}".format(exc))
            return

        if not shown:
            self._set_status("show 실패: rom 선택이 거부됐다")
            return

        self._set_status("shown under {}".format(prim_path))

    def _on_play(self):
        try:
            started = tv.play()
        except Exception as exc:
            self._set_status("play 실패: {}".format(exc))
            return

        self._set_status("playing" if started else "이미 재생 중")
        self._refresh_sim_time()
        self._refresh_outputs()

    def _on_stop(self):
        try:
            stopped = tv.stop()
        except Exception as exc:
            self._set_status("stop 실패: {}".format(exc))
            return

        self._set_status("stopped" if stopped else "재생 중이 아님")
        self._refresh_sim_time()
        self._refresh_outputs()

    def _on_deform_changed(self, model):
        if self._suppress:
            return

        try:
            tv.set_deform_scale(model.get_value_as_float())
        except Exception as exc:
            self._set_status("deform scale 실패: {}".format(exc))

    def _on_step_changed(self, model):
        if self._suppress:
            return

        try:
            tv.set_step_size(model.get_value_as_float())
        except Exception as exc:
            self._set_status("step size 실패: {}".format(exc))

    def _sync_from_service(self):
        if self._path_model is None:
            return

        if not tv.is_loaded():
            return

        self._suppress = True
        try:
            self._path_model.set_value(tv.get_local_path())

            prim_path = tv.get_prim_path()
            if prim_path:
                self._prim_path_model.set_value(prim_path)
        finally:
            self._suppress = False

        self._refresh_scalars()
        self._rebuild_io()

    def _refresh_scalars(self):
        if self._deform_model is None or self._step_model is None:
            return

        try:
            deform = tv.get_deform_scale()
            step = tv.get_step_size()
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
        if self._sim_time_label is None:
            return

        try:
            value = tv.get_simulation_time()
        except Exception:
            return

        self._sim_time_label.text = "sim time: {:.3f}".format(value)

    def _rebuild_io(self):
        self._input_models = {}
        self._output_labels = {}

        if self._io_frame is None:
            return

        self._io_frame.clear()

        try:
            inputs = tv.get_inputs()
            outputs = tv.get_outputs()
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
        try:
            tv.set_input(name, model.get_value_as_float())
        except Exception as exc:
            self._set_status("set_input 실패: {}".format(exc))
            return

        self._refresh_outputs()

    def _refresh_outputs(self):
        if not self._output_labels:
            return

        try:
            outputs = tv.get_outputs()
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
