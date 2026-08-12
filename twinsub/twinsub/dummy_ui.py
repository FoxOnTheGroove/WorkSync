"""동작 확인용 더미 UI.

원칙: 이 파일은 twinview_service 의 API만 쓴다. twinview 를 직접 import 하지 않는다.
service 만으로 뷰어를 다 굴릴 수 있는지 검증하는 역할도 겸한다.
"""

import omni.ui as ui

from . import twinview_service as tv


class TwinViewUI:

    WINDOW_TITLE = "TwinSub"

    def __init__(self):
        self._window = None
        self._source_model = None
        self._prim_path_model = None
        self._status_label = None

    # ------------------------------------------------------------ 구성

    def build_ui(self):
        self._window = ui.Window(self.WINDOW_TITLE, width=420, height=220)

        with self._window.frame:
            with ui.VStack(spacing=6, height=0):

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Source", width=70)
                    self._source_model = ui.StringField().model

                with ui.HStack(spacing=6, height=24):
                    ui.Label("Prim Path", width=70)
                    self._prim_path_model = ui.StringField().model
                    self._prim_path_model.set_value(tv.get_prim_path())

                with ui.HStack(spacing=6, height=28):
                    ui.Button("Load", clicked_fn=self._on_load)
                    ui.Button("Unload", clicked_fn=self._on_unload)
                    ui.Button("Clear", clicked_fn=self._on_clear)

                self._status_label = ui.Label("")

        # 상태 변화는 훅으로 받는다 — 폴링하지 않는다.
        tv.set_on_changed(self._refresh)
        self._refresh()

    def destroy(self):
        # 훅부터 끊는다. 창이 사라진 뒤 콜백이 들어오면 죽은 위젯을 만진다.
        tv.set_on_changed(None)

        self._status_label = None
        self._source_model = None
        self._prim_path_model = None

        if self._window:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------ 핸들러

    def _on_load(self):
        tv.set_prim_path(self._prim_path_model.get_value_as_string())

        source = self._source_model.get_value_as_string()
        if not tv.load(source):
            self._set_status("load 실패: source 를 확인할 것")

    def _on_unload(self):
        tv.unload()

    def _on_clear(self):
        tv.clear()
        self._refresh()

    # ------------------------------------------------------------ 갱신

    def _refresh(self):
        self._set_status(tv.get_status())

    def _set_status(self, text):
        if self._status_label:
            self._status_label.text = text
