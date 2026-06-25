import io
import os
import asyncio
import tempfile
from datetime import datetime

import omni.client
import omni.ui as ui
import omni.kit.app
import omni.renderer_capture


class Capture:
    _prefix = "capture"
    _index = 0
    _last_second = ""
    _sem = asyncio.Semaphore(1)

    @classmethod
    def set_prefix(cls, prefix):
        cls._prefix = prefix

    @classmethod
    def get_window(cls):
        return None

    @classmethod
    def _is_nucleus(cls, path):
        return path.startswith("omniverse://")

    @classmethod
    def _make_folder(cls, folder_path):
        if cls._is_nucleus(folder_path):
            omni.client.make_folder(folder_path)
        else:
            os.makedirs(folder_path, exist_ok=True)

    @classmethod
    def _save_image(cls, img, file_path):
        if cls._is_nucleus(file_path):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            omni.client.write_file(file_path, memoryview(buf.getvalue()))
        else:
            img.save(file_path)

    @classmethod
    def _join_path(cls, folder, name):
        if cls._is_nucleus(folder):
            return folder.rstrip("/") + "/" + name
        return os.path.join(folder, name)

    @classmethod
    def capture_to_folder(cls, folder_path=None):
        window = cls.get_window()
        if window is None:
            print("[capt] capture_to_folder: no window")
            return False

        # 경로가 없으면 사용자 다운로드 폴더로 저장
        if not folder_path:
            folder_path = os.path.join(os.path.expanduser("~"), "Downloads")

        cls._make_folder(folder_path)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 초가 바뀌면 인덱스 리셋, 같은 초 안에서는 00~99 증가
        if timestamp != cls._last_second:
            cls._last_second = timestamp
            cls._index = 0
        index = cls._index
        cls._index = (cls._index + 1) % 100
        file_name = f"{cls._prefix}_{timestamp}_{index:02d}.png"
        file_path = cls._join_path(folder_path, file_name)

        asyncio.ensure_future(cls._capture_async(window, file_path))
        return True

    @classmethod
    def _window_rect_px(cls, window):
        # 창 좌표/크기는 UI 포인트 단위 → DPI를 곱해 실제 픽셀 사각형으로 변환
        dpi = ui.Workspace.get_dpi_scale()
        left = int(window.position_x * dpi)
        top = int(window.position_y * dpi)
        width = int(window.width * dpi)
        height = int(window.height * dpi)
        return left, top, width, height

    @classmethod
    async def _capture_async(cls, window, file_path):
        # 캡처가 진행 중이면 끝날 때까지 대기 후 순서대로 실행
        async with cls._sem:
            await cls._do_capture(window, file_path)

    @classmethod
    async def _do_capture(cls, window, file_path):
        capture_iface = omni.renderer_capture.acquire_renderer_capture_interface()
        app = omni.kit.app.get_app()

        # 레이아웃 갱신을 위해 한 프레임 대기
        await app.next_update_async()

        # 시스템 임시 경로 (Kit이 직접 생성하도록 mkstemp 사용 안 함)
        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, f"capt_swapchain_{os.getpid()}.png")

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
            return

        left, top, width, height = cls._window_rect_px(window)

        from PIL import Image

        img = Image.open(tmp_path)
        img_w, img_h = img.size

        # 창이 화면 밖으로 걸친 경우를 대비해 이미지 범위로 클램프
        l = max(0, min(left, img_w))
        t = max(0, min(top, img_h))
        r = max(l, min(left + width, img_w))
        b = max(t, min(top + height, img_h))

        cropped = img.crop((l, t, r, b))
        cls._save_image(cropped, file_path)
        img.close()

        try:
            os.remove(tmp_path)
        except OSError:
            pass

        print(f"[capt] captured -> {file_path}")
