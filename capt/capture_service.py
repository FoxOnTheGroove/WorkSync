import io
from datetime import datetime

import omni.client

from .capture import ScreenCapture


class ScreenCaptureService:
    _prefix = "capture"
    _index = 0
    _last_second = ""

    @classmethod
    def set_prefix(cls, prefix):
        ScreenCapture._prefix = prefix

    @classmethod
    async def capture_image(cls):
        return await ScreenCapture.capture_image()

    @classmethod
    def save_to_nucleus(cls, img, folder_path):
        if img is None:
            print("[capt] 저장할 이미지가 없음")
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if timestamp != cls._last_second:
            cls._last_second = timestamp
            cls._index = 0
        index = cls._index
        cls._index = (cls._index + 1) % 100

        file_name = f"{cls._prefix}_{timestamp}_{index:02d}.png"
        file_path = folder_path.rstrip("/") + "/" + file_name

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        omni.client.write_file(file_path, memoryview(buf.getvalue()))
        print(f"[capt] nucleus 저장 -> {file_path}")
        return True
