import asyncio

import omni.ui as ui

from .capture_service import ScreenCaptureService


class ScreenCaptureUI:

    def __init__(self):
        self._window = None
        self._path_model = None
        self._last_image = None
        self._preview_provider = None

    def build_ui(self):
        self._window = ui.Window("Screen Capture", width=420, height=420)
        self._preview_provider = ui.ByteImageProvider()

        with self._window.frame:
            with ui.VStack(spacing=6):

                # 상단 한 줄: 주소 입력창 + 캡처 버튼 (세로로 안 늘어나게 고정)
                with ui.HStack(spacing=4, height=28):
                    self._path_model = ui.StringField().model
                    self._path_model.set_value("omniverse://")
                    ui.Button("캡처", clicked_fn=self._on_capture, width=70)

                # 미리보기: 남는 공간을 모두 차지
                # ImageWithProvider는 FillPolicy가 아니라 IwpFillPolicy를 사용
                ui.ImageWithProvider(
                    self._preview_provider,
                    fill_policy=ui.IwpFillPolicy.IWP_PRESERVE_ASPECT_FIT,
                    height=ui.Fraction(1),
                )

                # 하단 한 줄: 저장 버튼들 (항상 보이게 고정)
                with ui.HStack(spacing=4, height=32):
                    ui.Button("뉴클리어스에 저장", clicked_fn=self._on_save_nucleus)
                    ui.Button("다운로드에 저장", clicked_fn=lambda: None, enabled=False)

    def _on_capture(self):
        asyncio.ensure_future(self._capture_async())

    async def _capture_async(self):
        img = await ScreenCaptureService.capture_image()
        if img is None:
            return
        self._last_image = img
        rgba = img.convert("RGBA")
        self._preview_provider.set_bytes_data(
            list(rgba.tobytes()), [rgba.width, rgba.height]
        )

    def _on_save_nucleus(self):
        folder_path = self._path_model.get_value_as_string()
        ScreenCaptureService.save_to_nucleus(self._last_image, folder_path)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
