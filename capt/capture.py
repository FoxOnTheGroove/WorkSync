import os
import asyncio

import omni.ui as ui
import omni.kit.app
import omni.renderer_capture


class Capture:
    """Capture a ui.Window's on-screen region and export it to an image file."""

    @staticmethod
    def get_window():
        # Implemented by the user: return the target ui.Window to capture.
        return None

    @staticmethod
    def capture_to_file(file_path):
        """Capture the window returned by get_window() to file_path.

        Returns True if the capture was scheduled, False if there is no window.
        The image is written asynchronously a couple of frames later.
        """
        window = Capture.get_window()
        if window is None:
            print("[capt] capture_to_file: no window")
            return False

        asyncio.ensure_future(Capture._capture_async(window, file_path))
        return True

    @staticmethod
    def _window_rect_px(window):
        """Window rectangle in framebuffer pixels: (left, top, width, height).

        Window position/size are in UI points, so scale by the DPI to get the
        actual pixel rectangle inside the swapchain image.
        """
        dpi = ui.Workspace.get_dpi_scale()
        left = int(window.position_x * dpi)
        top = int(window.position_y * dpi)
        width = int(window.width * dpi)
        height = int(window.height * dpi)
        return left, top, width, height

    @staticmethod
    async def _capture_async(window, file_path):
        full_path = file_path + ".full.png"
        capture_iface = omni.renderer_capture.acquire_renderer_capture_interface()
        app = omni.kit.app.get_app()

        # Let one frame render so the window layout is up to date.
        await app.next_update_async()

        # Grab the whole app window (swapchain) on the next rendered frame.
        capture_iface.capture_next_frame_swapchain(full_path)
        await app.next_update_async()
        capture_iface.wait_async_capture()

        # Crop the swapchain image down to the window's rectangle.
        left, top, width, height = Capture._window_rect_px(window)

        try:
            from PIL import Image
        except ImportError:
            print("[capt] PIL not available; saved full swapchain instead")
            os.replace(full_path, file_path)
            return

        img = Image.open(full_path)
        img_w, img_h = img.size

        # Clamp to image bounds in case the window is partly off-screen.
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
