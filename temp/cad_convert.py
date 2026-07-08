"""
Script Editor 에서 실행 - STEP -> USD 변환 + 진행도 추적 (HoopsCoreConverter)

구조:
  1. fd1(C stdout) tap + carb 로거로 모든 로그 라인 수집
     (HOOPS 네이티브 진행 로그는 fd1 에만 나오므로 tap 이 필수)
  2. hoops_progress 라인만 ProgressLogConsumer 로 파싱해 캐시에 저장 (push)
     - 갱신 즉시 [반응형] 출력
  3. 매 프레임 루프가 캐시된 현재 진행도를 [매프레임] 으로 출력 (pull)

fd1 을 파이프로 돌리는 동안 파이썬 콘솔 스트림이 WriteConsoleW 를 파이프에
호출하면 OSError(WinError 1) 가 나므로, sys.stdout/stderr 의 write 를
보호막으로 감싸 변환 태스크가 죽지 않게 한다.

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os
import sys
import locale
import asyncio
import threading
import carb.logging
import omni.kit.app
import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod
from omni.kit.converter.common import ProgressLogConsumer


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================


# file_format_args 는 dict[str, str] - 값 전부 문자열
# 진행/상태 로그가 최대한 많이 나오도록 리포팅 옵션 전부 켬 (신규/레거시 키 둘 다)
CONVERT_OPTIONS = {
    "upAxis"            : "1",      # 1=Y-up
    "tessLOD"           : "2",      # 2=Medium
    "bInstancing"       : "false",  # 인스턴싱 비활성화
    "useMaterials"      : "false",  # 재질 없음
    "dMetersPerUnit"    : "1.0",
    "reportProgress"    : "true",
    "bReportProgress"   : "true",
    "reportProgressFreq": "10.0",   # 초당 리포팅 횟수 [1~10] 최대
}


# 콘솔 인코딩 (Windows 한국어 콘솔은 cp949 - UTF-8 고정으로 쓰면 한글 깨짐)
_CONSOLE_ENC = (getattr(sys.stderr, "encoding", None)
                or locale.getpreferredencoding(False)
                or "utf-8")


def _err(text: str):
    """stderr(fd2) 로 콘솔 인코딩에 맞춰 한 줄 출력 (tap 중에도 안전)."""
    os.write(2, (text + "\n").encode(_CONSOLE_ENC, "replace"))


# ---------------- fd1 tap ----------------

class StdoutTap:
    """C 레벨 stdout(fd 1)을 파이프로 복제해 라인 콜백으로 전달.

    HOOPS 네이티브 진행 로그는 fd1 에 직접 쓰므로 이 방법으로만 잡힌다.
    읽은 내용은 원본 콘솔로 그대로 통과시킨다.
    """

    def __init__(self, on_line):
        self._on_line = on_line
        self._saved_fd = None
        self._read_fd = None
        self._thread = None

    def start(self):
        sys.stdout.flush()
        self._saved_fd = os.dup(1)
        r, w = os.pipe()
        os.dup2(w, 1)
        os.close(w)
        self._read_fd = r
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        buf = b""
        while True:
            try:
                chunk = os.read(self._read_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            os.write(self._saved_fd, chunk)   # 원본 콘솔로 그대로 통과
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    self._on_line(line.decode("utf-8", "ignore"))
                except Exception:
                    pass

    def stop(self):
        if self._saved_fd is not None:
            os.dup2(self._saved_fd, 1)    # fd1 원복 -> pump 종료
            os.close(self._saved_fd)
            self._saved_fd = None
        if self._read_fd is not None:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = None


def _shield_write(stream):
    """stream.write 에서 나는 OSError(WinError 1 등)를 삼키는 보호막. 복원용 튜플 반환."""
    orig = getattr(stream, "write", None)
    if orig is None:
        return None
    def _safe(s, _orig=orig):
        try:
            return _orig(s)
        except OSError:
            return len(s)
    try:
        stream.write = _safe
    except (AttributeError, TypeError):
        return None   # C 구현 스트림이면 패치 불가 - 그대로 둠
    return (stream, orig)


def _unshield_write(saved):
    if saved:
        stream, orig = saved
        try:
            stream.write = orig
        except (AttributeError, TypeError):
            pass


# ---------------- 진행도 캐시 ----------------

class ProgressWatcher:
    """들어오는 로그 라인을 ProgressLogConsumer 로 파싱해 최신 진행도를 캐시.

    로그 한 라인에 필드가 * 구분자로 같이 들어옴:
      [omni.converter.hoops_progress]*step*1:2*prog*100.0*Completed Reading CAD model...
      [omni.converter.hoops_progress]*end*0*

    feed()  : 로그 라인 유입 (push) - 갱신 시 [반응형] 즉시 출력
    step / value / desc : 단계(예 1:2) / 진행도(0.0~1.0) / 설명 조회 (pull)
    """

    PREFIX = "[omni.converter.hoops_progress]"
    _MARKERS = {"step", "prog", "Begin", "end"}

    def __init__(self):
        self._consumer = ProgressLogConsumer(self.PREFIX)
        self.step = ""     # 예: "1:2"
        self.value = 0.0   # 0.0 ~ 1.0
        self.desc = ""     # 예: "Completed Reading CAD model..."
        self.ended = False

    @staticmethod
    def _is_number(s: str) -> bool:
        try:
            float(s)
            return True
        except ValueError:
            return False

    def feed(self, text: str, origin: str = "?"):
        if "hoops_progress" not in text:
            return
        try:
            # 파서는 프리픽스로 시작하는 라인을 기대함.
            # carb 경유 라인은 앞에 "py stdout: " 등이 붙으므로 프리픽스 위치부터 자름.
            idx = text.find(self.PREFIX)
            line = text[idx:] if idx >= 0 else f"{self.PREFIX} {text}"
            ret = self._consumer.extract_line(line)
            if not ret:
                return

            # PROGRESS 타입이면 파서가 계산해준 0~1 값
            if len(ret) > 2:
                self.value = float(ret[2])

            # decoded_msg 에서 필드 직접 추출
            msg = ret[1] if len(ret) > 1 else None
            if isinstance(msg, (list, tuple)):
                m = [str(x).strip() for x in msg]
                if "step" in m:
                    i = m.index("step")
                    if i + 1 < len(m):
                        self.step = m[i + 1]                  # "1:2"
                if "end" in m:
                    self.ended = True
                    self.value = 1.0
                    self.desc = "end"
                elif m and m[-1] and m[-1] not in self._MARKERS and not self._is_number(m[-1]):
                    self.desc = m[-1]                         # 설명문 (마지막 필드)

            # 반응형: 로그가 도착해 상태가 갱신된 순간 즉시 출력. origin 은 유입 경로(carb/fd1)
            _err(f"[반응형/{origin}] step {self.step} {self.value * 100:.1f}% | {self.desc}")
        except Exception:
            pass


# ---------------- 메인 ----------------

async def _convert():
    converter = hoops_mod.get_instance()
    watcher = ProgressWatcher()

    # 수집 경로 1: carb 로거 (py stdout 에코 등)
    def _on_log(source, level, filename, line_number, message):
        watcher.feed(str(message), "carb")

    logging = carb.logging.acquire_logging()
    handle = logging.add_logger(_on_log)

    # 수집 경로 2: fd1 tap (네이티브가 stdout 에 직접 쓰는 라인)
    tap = StdoutTap(lambda line: watcher.feed(line, "fd1"))
    tap.start()

    # tap 중 콘솔 스트림 보호 (WinError 1 방지)
    shields = [_shield_write(sys.stdout), _shield_write(sys.stderr)]

    conv = asyncio.ensure_future(
        converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    )
    app = omni.kit.app.get_app()

    try:
        while not conv.done():
            _err(f"[매프레임] step {watcher.step} {watcher.value * 100:.1f}% | {watcher.desc}")
            await app.next_update_async()
    finally:
        for s in shields:
            _unshield_write(s)
        tap.stop()
        logging.remove_logger(handle)

    try:
        result = conv.result()
    except Exception as e:
        print(f"[실패] {type(e).__name__}: {e}")
        return

    print(f"[완료] {result}")


omni.kit.async_engine.run_coroutine(_convert())
