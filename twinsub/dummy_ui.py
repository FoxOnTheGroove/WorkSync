import omni.ui as ui

from . import twinview_service as tv


class DummyUI:

    WINDOW_TITLE = "TwinSub"

    def __init__(self):
        self._window = None
        self._uri_model = None
        self._path_model = None
        self._status_label = None

    def build_ui(self):
        self._window = ui.Window(self.WINDOW_TITLE, width=560, height=220)

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

                self._status_label = ui.Label("")

    def destroy(self):
        self._uri_model = None
        self._path_model = None
        self._status_label = None

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

    def _set_status(self, text):
        if self._status_label:
            self._status_label.text = text
