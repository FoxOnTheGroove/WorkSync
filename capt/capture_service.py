from typing import Optional

from PIL import Image as PILImage

from .capture import ScreenCapture


class ScreenCaptureService:

    @classmethod
    def set_prefix(cls, prefix: str) -> None:
        return ScreenCapture.set_prefix(prefix)

    @classmethod
    async def capture_image(cls) -> Optional[PILImage.Image]:
        return await ScreenCapture.capture_image()

    @classmethod
    def save_to_nucleus(cls, folder_path: str) -> bool:
        return ScreenCapture.save_to_nucleus(folder_path)
