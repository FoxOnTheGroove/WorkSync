import os
import asyncio

import omni.ui as ui
import omni.kit.app
import omni.renderer_capture


class Capture:

    @classmethod
    def get_window(cls):
        return None

    @classmethod
    def capture_to_file(cls, file_path):
        window = cls.get_window()
        if window is None:
            print("[capt] capture_to_file: no window")
            return False

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
        full_path = file_path + ".full.png"
        capture_iface = omni.renderer_capture.acquire_renderer_capture_interface()
        app = omni.kit.app.get_app()

        # 레이아웃 갱신을 위해 한 프레임 대기
        await app.next_update_async()

        # 앱 창 전체(스왑체인)를 캡처, 콜백으로 파일 쓰기 완료 확인
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        def _on_done():
            loop.call_soon_threadsafe(future.set_result, None)

        capture_iface.capture_next_frame_swapchain_callback(full_path, _on_done)
        await future

        left, top, width, height = cls._window_rect_px(window)

        try:
            from PIL import Image
        except ImportError:
            print("[capt] PIL not available; saved full swapchain instead")
            os.replace(full_path, file_path)
            return

        img = Image.open(full_path)
        img_w, img_h = img.size

        # 창이 화면 밖으로 걸친 경우를 대비해 이미지 범위로 클램프
        l = max(0, min(left, img_w))
        t = max(0, min(top, img_h))
        r = max(l, min(left + width, img_w))
        b = max(t, min(top + height, img_h))

        cropped = img.crop((l, t, r, b))
        cropped.save(file_path)
        img.close()

        try:
            os.remove(full_path)
        except OSError:
            pass

        print(f"[capt] captured window -> {file_path}")
