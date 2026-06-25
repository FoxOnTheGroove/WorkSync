from .capture import ScreenCapture


class ScreenCaptureService:

    @classmethod
    def set_prefix(cls, prefix):
        return ScreenCapture.set_prefix(prefix)

    @classmethod
    async def capture_image(cls):
        return await ScreenCapture.capture_image()

    @classmethod
    def save_to_nucleus(cls, img, folder_path):
        return ScreenCapture.save_to_nucleus(img, folder_path)
