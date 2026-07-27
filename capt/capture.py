import io
import ctypes
import asyncio
from datetime import datetime
from typing import Optional, Tuple

import omni.client
import omni.ui as ui
import omni.kit.app
import omni.renderer_capture
from PIL import Image as PILImage


class ScreenCapture:
    _current_window: Optional[ui.Window] = None
    _sem: asyncio.Semaphore = asyncio.Semaphore(1)
    _prefix: str = "capture"
    _index: int = 0
    _last_second: str = ""
    _last_filename: Optional[str] = None
    _last_image: Optional[PILImage.Image] = None
    # 스왑체인 채널 순서. 색이 뒤집혀 보이면 "RGBA"로 변경
    _raw_mode: str = "BGRA"
    _s3_bucket: Optional[str] = None
    _s3_prefix: str = ""
    _s3_client = None

    @classmethod
    def set_prefix(cls, prefix: str) -> None:
        cls._prefix = prefix

    @classmethod
    def set_s3(cls, bucket: str, prefix: str = "") -> None:
        cls._s3_bucket = bucket
        cls._s3_prefix = prefix.strip("/")
        # 설정이 바뀌면 클라이언트를 다시 만들도록 초기화
        cls._s3_client = None

    @classmethod
    def _next_filename(cls) -> str:
        # 초가 바뀌면 인덱스 리셋, 같은 초 안에서는 00~99 증가
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if timestamp != cls._last_second:
            cls._last_second = timestamp
            cls._index = 0
        index = cls._index
        cls._index = (cls._index + 1) % 100
        return f"{cls._prefix}_{timestamp}_{index:02d}.png"

    @classmethod
    def _window_rect_px(cls) -> Tuple[int, int, int, int]:
        # 창 좌표/크기는 UI 포인트 단위 → DPI를 곱해 실제 픽셀 사각형으로 변환
        dpi = ui.Workspace.get_dpi_scale()
        left   = int(cls._current_window.position_x * dpi)
        top    = int(cls._current_window.position_y * dpi)
        width  = int(cls._current_window.width * dpi)
        height = int(cls._current_window.height * dpi)
        return left, top, width, height

    @classmethod
    def _buffer_to_image(cls, buffer, buffer_size: int, width: int, height: int) -> PILImage.Image:
        # 콜백 버퍼는 PyCapsule로 전달되므로 원시 주소를 꺼내야 함
        ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
        ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
        ptr = ctypes.pythonapi.PyCapsule_GetPointer(buffer, None)
        data = bytes(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_byte * buffer_size)).contents)

        # 행 패딩이 있을 수 있으므로 실제 stride를 buffer_size에서 역산
        stride = buffer_size // height if height else width * 4
        return PILImage.frombytes("RGBA", (width, height), data, "raw", cls._raw_mode, stride)

    @classmethod
    async def capture_image(cls) -> Optional[PILImage.Image]:
        # 캡처가 진행 중이면 끝날 때까지 대기 후 순서대로 실행
        async with cls._sem:
            img = await cls._do_capture()
            if img is not None:
                cls._last_image = img
                cls._last_filename = cls._next_filename()
            return img

    @classmethod
    async def _do_capture(cls) -> Optional[PILImage.Image]:
        if cls._current_window is None:
            print("[capt] _current_window가 설정되지 않음")
            return None

        capture_iface = omni.renderer_capture.acquire_renderer_capture_interface()
        app = omni.kit.app.get_app()

        # 레이아웃 갱신을 위해 한 프레임 대기
        await app.next_update_async()

        # 크롭 좌표는 캡처를 요청하는 시점에 읽어야 찍힌 프레임과 어긋나지 않음
        left, top, width, height = cls._window_rect_px()

        done = {}

        def _on_capture(buffer, buffer_size, img_w, img_h, format_):
            # 버퍼는 콜백이 반환되는 순간 무효해지므로 여기서 즉시 복사
            try:
                done["image"] = cls._buffer_to_image(buffer, buffer_size, img_w, img_h)
            except Exception as exc:
                done["error"] = exc

        capture_iface.capture_next_frame_swapchain_callback(_on_capture)

        # 콜백이 올 때까지 프레임을 돌림 (최대 60프레임)
        for _ in range(60):
            await app.next_update_async()
            if done:
                break
        else:
            print("[capt] 캡처 타임아웃: 스왑체인 콜백이 호출되지 않음")
            return None

        if "error" in done:
            print(f"[capt] 버퍼 변환 실패: {done['error']}")
            return None

        img = done["image"]
        img_w, img_h = img.size

        # 창이 화면 밖으로 걸친 경우를 대비해 이미지 범위로 클램프
        l = max(0, min(left, img_w))
        t = max(0, min(top, img_h))
        r = max(l, min(left + width, img_w))
        b = max(t, min(top + height, img_h))
        if r <= l or b <= t:
            print("[capt] 크롭 영역이 비어 있음: 창이 화면 밖에 있음")
            return None

        return img.crop((l, t, r, b))

    @classmethod
    def save_to_nucleus(cls, folder_path: str) -> bool:
        if cls._last_image is None or cls._last_filename is None:
            print("[capt] 저장할 이미지가 없음")
            return False

        file_path = folder_path.rstrip("/") + "/" + cls._last_filename

        # 동일한 이름의 파일이 이미 있으면 저장 안 함
        result, _ = omni.client.stat(file_path)
        if result == omni.client.Result.OK:
            print(f"[capt] 이미 저장됨: {file_path}")
            return False

        buf = io.BytesIO()
        cls._last_image.save(buf, format="PNG")
        omni.client.write_file(file_path, memoryview(buf.getvalue()))
        print(f"[capt] nucleus 저장 -> {file_path}")
        return True

    @classmethod
    def _get_s3_client(cls):
        # boto3가 없는 Kit 환경에서도 익스텐션이 로드되도록 지연 임포트
        if cls._s3_client is None:
            import boto3
            cls._s3_client = boto3.client("s3")
        return cls._s3_client

    @classmethod
    def _upload_and_presign(cls, buf: io.BytesIO, key: str, expires_in: int) -> str:
        client = cls._get_s3_client()
        # upload_file은 경로를 받지만 upload_fileobj는 파일 객체를 받음 → 메모리에서 바로 전송
        client.upload_fileobj(
            buf, cls._s3_bucket, key, ExtraArgs={"ContentType": "image/png"}
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": cls._s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    @classmethod
    async def upload_s3_async(
        cls,
        image: Optional[PILImage.Image],
        filename: str,
        expires_in: int = 3600,
    ) -> Optional[str]:
        if image is None:
            print("[capt] 업로드할 이미지가 없음")
            return None
        if not cls._s3_bucket:
            print("[capt] S3 버킷이 설정되지 않음: set_s3() 먼저 호출")
            return None

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        key = f"{cls._s3_prefix}/{filename}" if cls._s3_prefix else filename

        # boto3는 블로킹 호출이므로 스레드로 넘겨 렌더 루프를 막지 않음
        loop = asyncio.get_event_loop()
        try:
            url = await loop.run_in_executor(
                None, cls._upload_and_presign, buf, key, expires_in
            )
        except Exception as exc:
            print(f"[capt] S3 업로드 실패: {exc}")
            return None

        print(f"[capt] S3 업로드 -> s3://{cls._s3_bucket}/{key}")
        return url
