import io
import os
import asyncio
import tempfile
from datetime import datetime

import omni.client
import omni.ui as ui
import omni.kit.app
import omni.renderer_capture
from PIL import Image


class ScreenCapture:
    _current_window = None
    _sem = asyncio.Semaphore(1)
    _prefix = "capture"
    _index = 0
    _last_second = ""
    _last_filename = None

    @classmethod
    def set_prefix(cls, prefix):
        cls._prefix = prefix

    @classmethod
    def _next_filename(cls):
        # 초가 바뀌면 인덱스 리셋, 같은 초 안에서는 00~99 증가
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if timestamp != cls._last_second:
            cls._last_second = timestamp
            cls._index = 0
        index = cls._index
        cls._index = (cls._index + 1) % 100
        return f"{cls._prefix}_{timestamp}_{index:02d}.png"

    @classmethod
    def _window_rect_px(cls):
        # 창 좌표/크기는 UI 포인트 단위 → DPI를 곱해 실제 픽셀 사각형으로 변환
        dpi = ui.Workspace.get_dpi_scale()
        left   = int(cls._current_window.position_x * dpi)
        top    = int(cls._current_window.position_y * dpi)
        width  = int(cls._current_window.width * dpi)
        height = int(cls._current_window.height * dpi)
        return left, top, width, height

    @classmethod
    async def capture_image(cls):
        # 캡처가 진행 중이면 끝날 때까지 대기 후 순서대로 실행
        async with cls._sem:
            img = await cls._do_capture()
            if img is not None:
                cls._last_filename = cls._next_filename()
            return img

    @classmethod
    async def _do_capture(cls):
        if cls._current_window is None:
            print("[capt] _current_window가 설정되지 않음")
            return None

        capture_iface = omni.renderer_capture.acquire_renderer_capture_interface()
        app = omni.kit.app.get_app()

        # 레이아웃 갱신을 위해 한 프레임 대기
        await app.next_update_async()

        tmp_path = os.path.join(tempfile.gettempdir(), f"capt_swapchain_{os.getpid()}.png")

        # 이전 실행이 남긴 파일을 지워야 폴링이 새 캡처를 기다림
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        capture_iface.capture_next_frame_swapchain(tmp_path)

        # 파일이 실제로 기록될 때까지 프레임마다 확인 (최대 60프레임)
        for _ in range(60):
            await app.next_update_async()
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                break
        else:
            print("[capt] 캡처 타임아웃: 스왑체인 파일이 생성되지 않음")
            return None

        left, top, width, height = cls._window_rect_px()
        img = Image.open(tmp_path)
        img_w, img_h = img.size

        # 창이 화면 밖으로 걸친 경우를 대비해 이미지 범위로 클램프
        l = max(0, min(left, img_w))
        t = max(0, min(top, img_h))
        r = max(l, min(left + width, img_w))
        b = max(t, min(top + height, img_h))

        cropped = img.crop((l, t, r, b))
        img.close()

        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return cropped

    @classmethod
    def save_to_nucleus(cls, img, folder_path):
        if img is None or cls._last_filename is None:
            print("[capt] 저장할 이미지가 없음")
            return False

        file_path = folder_path.rstrip("/") + "/" + cls._last_filename

        # 동일한 이름의 파일이 이미 있으면 저장 안 함
        result, _ = omni.client.stat(file_path)
        if result == omni.client.Result.OK:
            print(f"[capt] 이미 저장됨: {file_path}")
            return False

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        omni.client.write_file(file_path, memoryview(buf.getvalue()))
        print(f"[capt] nucleus 저장 -> {file_path}")
        return True
