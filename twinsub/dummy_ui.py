import omni.ui as ui

from . import twinview_service as tv


class DummyUI:

    WINDOW_TITLE = "TwinSub"
    DEFAULT_PRIM_PATH = "/World"

    def __init__(self):
        self._window = None
        self._uri_model = None
        self._path_model = None
        self._prim_path_model = None
        self._deform_model = None
        self._step_model = None
        self._eval_time_label = None
        self._status_label = None

        # 로드하며 필드에 값을 넣을 때 변경 콜백이 되받아치는 걸 막는다.
        self._suppress = False

        # 로드 전엔 무엇이 있는지 모른다. 로드 후에 채운다.
        self._io_frame = None
        self._input_models = {}
        self._output_labels = {}

    def build_ui(self):
        self._window = ui.Window(self.WINDOW_TITLE, width=560, height=520)

        with self._window.frame:
            with ui.VStack(spacing=6, height=0):

                # --- S3 로드: 받은 경로를 아래 Path 필드에 넣기만 한다 ---
                with ui.HStack(spacing=6, height=24):
                    ui.Label("S3 URI", width=60)
                    self._uri_model = ui.StringField().model

                ui.Button("Load", height=28, clicked_fn=self._on_s3_load)

                ui.Separator(height=8)

                # --- 로컬 경로 로드: 여기서 실제로 러너를 세운다 ---
                with ui.HStack(spacing=6, height=24):
                    ui.Label("Path", width=60)
                    self._path_model = ui.StringField().model

                ui.Button("Load", height=28, clicked_fn=self._on_path_load)

                ui.Separator(height=8)

                # --- 표시: 이 prim 아래에 rom 이름의 포인트 클라우드가 생긴다 ---
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

                self._eval_time_label = ui.Label("")
                self._status_label = ui.Label("")

                ui.Separator(height=8)

                # 로드된 트윈의 입출력 목록. 로드 때마다 다시 그린다.
                self._io_frame = ui.Frame(height=0)

        # 재생 중 값이 도는 것은 틱 신호로 받는다.
        tv.set_on_updated(self._on_tick)

    def destroy(self):
        # 훅부터 끊는다. 창이 사라진 뒤 틱이 들어오면 죽은 위젯을 만진다.
        tv.set_on_updated(None)

        self._uri_model = None
        self._path_model = None
        self._prim_path_model = None
        self._deform_model = None
        self._step_model = None
        self._eval_time_label = None
        self._status_label = None

        # 모델을 붙들고 있으면 창이 사라진 뒤에도 콜백이 살아 있다.
        self._input_models = {}
        self._output_labels = {}
        self._io_frame = None

        if self._window:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------ 핸들러

    def _on_s3_load(self):
        s3_uri = self._uri_model.get_value_as_string()

        # 잘못된 uri, 자격증명 없음, 없는 키 전부 여기로 떨어진다.
        # 더미 UI라 구분하지 않고 이유만 그대로 띄운다.
        try:
            local_path = tv.download_twin(s3_uri)
        except Exception as exc:  # noqa: BLE001
            self._set_status("download 실패: {}".format(exc))
            return

        # 받기만 하고 로드는 하지 않는다. 로드는 아래 Load 버튼이 맡는다.
        self._path_model.set_value(local_path)
        self._set_status("downloaded: {}".format(local_path))

    def _on_path_load(self):
        path = self._path_model.get_value_as_string()

        try:
            tv.load_twin(path)
        except Exception as exc:  # noqa: BLE001
            self._set_status("load 실패: {}".format(exc))
            return

        self._set_status("loaded: {}".format(path))
        self._refresh_scalars()
        self._rebuild_io()

    def _on_show(self):
        prim_path = self._prim_path_model.get_value_as_string()

        try:
            shown = tv.rom_show(prim_path)
        except Exception as exc:  # noqa: BLE001
            self._set_status("show 실패: {}".format(exc))
            return

        if not shown:
            self._set_status("show 실패: rom 선택이 거부됐다")
            return

        self._set_status("shown under {}".format(prim_path))

    def _on_play(self):
        try:
            started = tv.play()
        except Exception as exc:  # noqa: BLE001
            self._set_status("play 실패: {}".format(exc))
            return

        self._set_status("playing" if started else "이미 재생 중")
        self._refresh_eval_time()
        self._refresh_outputs()

    def _on_stop(self):
        try:
            stopped = tv.stop()
        except Exception as exc:  # noqa: BLE001
            self._set_status("stop 실패: {}".format(exc))
            return

        self._set_status("stopped" if stopped else "재생 중이 아님")
        self._refresh_eval_time()
        self._refresh_outputs()

    def _on_deform_changed(self, model):
        # 로드하며 넣은 값이 되돌아온 것은 무시한다.
        if self._suppress:
            return

        try:
            tv.set_deform_scale(model.get_value_as_float())
        except Exception as exc:  # noqa: BLE001
            self._set_status("deform scale 실패: {}".format(exc))

    def _on_step_changed(self, model):
        if self._suppress:
            return

        try:
            tv.set_step_size(model.get_value_as_float())
        except Exception as exc:  # noqa: BLE001
            self._set_status("step size 실패: {}".format(exc))

    # ------------------------------------------------------------ 갱신

    def _on_tick(self):
        """재생 중 매 틱. 도는 값들만 다시 읽는다."""
        self._refresh_eval_time()
        self._refresh_outputs()

    def _refresh_scalars(self):
        """로드된 트윈의 deform scale 과 step size 를 필드에 넣는다."""
        if self._deform_model is None or self._step_model is None:
            return

        try:
            deform = tv.get_deform_scale()
            step = tv.get_step_size()
        except Exception as exc:  # noqa: BLE001
            self._set_status("조회 실패: {}".format(exc))
            return

        self._suppress = True
        try:
            self._deform_model.set_value(deform)
            self._step_model.set_value(step)
        finally:
            self._suppress = False

    def _refresh_eval_time(self):
        if self._eval_time_label is None:
            return

        try:
            value = tv.get_evaluation_time()
        except Exception:  # noqa: BLE001
            return

        self._eval_time_label.text = "evaluation time: {:.3f}".format(value)

    # ------------------------------------------------------------ 입출력 목록

    def _rebuild_io(self):
        """로드된 트윈의 입력/출력 목록을 다시 그린다."""
        self._input_models = {}
        self._output_labels = {}

        if self._io_frame is None:
            return

        self._io_frame.clear()

        try:
            inputs = tv.get_inputs()
            outputs = tv.get_outputs()
        except Exception as exc:  # noqa: BLE001
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

                        # 콜백은 초기값을 넣은 뒤에 단다. 먼저 달면 여기서
                        # set_input 이 한 번 헛돌아 러너 값을 덮어쓴다.
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
        except Exception as exc:  # noqa: BLE001
            self._set_status("set_input 실패: {}".format(exc))
            return

        self._refresh_outputs()

    def _refresh_outputs(self):
        if not self._output_labels:
            return

        try:
            outputs = tv.get_outputs()
        except Exception:  # noqa: BLE001
            return

        for name, label in self._output_labels.items():
            if name in outputs:
                label.text = str(outputs[name])

    @staticmethod
    def _as_float(value):
        # 입력이 숫자가 아닌 트윈도 있을 수 있다. 필드가 못 받으면 0 으로 둔다.
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _set_status(self, text):
        if self._status_label:
            self._status_label.text = text
