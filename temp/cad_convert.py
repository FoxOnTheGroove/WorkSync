"""
Script Editor 에서 실행 - STEP -> USD 변환 + 진행도 추적 (HoopsCoreConverter)

구조:
  1. fd1(C stdout) tap 으로 로그 라인 수집
     (HOOPS 네이티브 진행 로그는 fd1 에만 나오므로 tap 이 유일한 경로)
  2. hoops_progress 라인만 ProgressLogConsumer 로 파싱해 상태로 저장
  3. 접근 API 두 가지:
     - on_progress_changed(cb) : 갱신 즉시 콜백 (push) -> [반응형]
     - get_current_state()     : 원할 때 조회 (pull)  -> [매프레임]

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
import omni.kit.app
import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod
from omni.kit.converter.common import ProgressLogConsumer


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================


# file_format_args 는 dict[str, str] - 값 전부 문자열
CONVERT_OPTIONS = {
    "upAxis"            : "1",      # 1=Y-up
    "tessLOD"           : "2",      # 2=Medium
    "bInstancing"       : "false",  # 인스턴싱 비활성화
    "useMaterials"      : "false",  # 재질 없음
    "dMetersPerUnit"    : "1.0",
    # 진행 로그(*step*/*prog*) 는 이 옵션에 게이트됨 - 진행률 추적하려면 필수
    "reportProgress"    : "true",
    "reportProgressFreq": "10.0",   # 초당 리포팅 상한 [1~10]
}


# 콘솔 인코딩 - Kit 의 sys.stderr.encoding 은 utf-8 로 잡히므로 믿으면 안 됨.
# Windows 콘솔의 실제 출력 코드페이지를 API 로 조회 (한국어 콘솔 = 949)
def _detect_console_enc() -> str:
    if os.name == "nt":
        try:
            import ctypes
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            if cp:
                return f"cp{cp}"       # 949 -> cp949, 65001 -> cp65001(=utf-8)
        except Exception:
            pass
    return locale.getpreferredencoding(False) or "utf-8"


_CONSOLE_ENC = _detect_console_enc()


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
    """변환 진행 로그를 파싱해 ret[0,1,2] 상태를 관리하는 클래스.

    extract_line 반환 (확인됨):
      ret = [ProgressStepType, desc(str), prog(0.0~1.0, PROGRESS 타입일 때만)]

    접근 방법 두 가지:
      get_current_state()          : 호출 시점의 (step_type, desc, value) 반환 (pull)
      on_progress_changed(callback): 상태 갱신마다 callback(step_type, desc, value) 호출 (push)
    """

    PREFIX = "[omni.converter.hoops_progress]"

    def __init__(self):
        self._consumer = ProgressLogConsumer(self.PREFIX)
        self._callbacks = []
        self.step_type = None   # ret[0] - ProgressStepType
        self.desc = ""          # ret[1] - 현재 과정
        self.value = 0.0        # ret[2] - 진행도 0.0 ~ 1.0
        self.step = ""          # 단계 인덱스 (예: "1:2")
        self.ended = False

    # ---------- 접근 API ----------

    def get_current_state(self):
        """원할 때 현재 상태 조회 (매프레임 루프 등)."""
        return self.step_type, self.desc, self.value

    def on_progress_changed(self, callback):
        """상태 갱신 시마다 callback(step_type, desc, value) 호출 등록 (반응형)."""
        self._callbacks.append(callback)

    def _notify(self):
        for cb in self._callbacks:
            try:
                cb(self.step_type, self.desc, self.value)
            except Exception:
                pass

    # ---------- 로그 유입 ----------

    def feed(self, text: str):
        if "hoops_progress" not in text:
            return
        try:
            # 파서는 프리픽스로 시작하는 라인을 기대함 - 프리픽스 위치부터 자름.
            idx = text.find(self.PREFIX)
            line = text[idx:] if idx >= 0 else f"{self.PREFIX} {text}"
            parts = [p.strip() for p in line.split("*")]

            if "end" in parts:
                self.ended = True
                self.value = 1.0
                self.desc = "convert complete"
                self.step_type = "end"
            else:
                ret = self._consumer.extract_line(line)
                if not ret:
                    return

                self.step_type = ret[0]

                # step 인덱스("1:2")가 바뀌면 새 단계 시작 - 진행도 리셋
                if "step" in parts:
                    i = parts.index("step")
                    if i + 1 < len(parts) and parts[i + 1] != self.step:
                        self.step = parts[i + 1]
                        self.value = 0.0

                if len(ret) > 2:                     # PROGRESS - 0.0~1.0
                    self.value = float(ret[2])
                if len(ret) > 1 and isinstance(ret[1], str) and ret[1]:
                    self.desc = ret[1]               # 현재 과정

            self._notify()
        except Exception:
            pass


# ---------------- 메인 ----------------

async def _convert():
    converter = hoops_mod.get_instance()
    watcher = ProgressWatcher()

    # 반응형 접근: 상태 갱신 즉시 호출되는 콜백 등록
    watcher.on_progress_changed(
        lambda step_type, desc, value: _err(f"[반응형] {desc} {value * 100:.1f}%")
    )

    # 진행 로그는 네이티브가 fd1(stdout)에 직접 쓰므로 tap 이 유일한 수집 경로
    tap = StdoutTap(watcher.feed)
    tap.start()

    # tap 중 콘솔 스트림 보호 (WinError 1 방지)
    shields = [_shield_write(sys.stdout), _shield_write(sys.stderr)]

    conv = asyncio.ensure_future(
        converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    )
    app = omni.kit.app.get_app()

    try:
        while not conv.done():
            # 원할 때 접근: 현재 상태 조회
            _, desc, value = watcher.get_current_state()
            _err(f"[매프레임] {desc} {value * 100:.1f}%")
            await app.next_update_async()
    finally:
        for s in shields:
            _unshield_write(s)
        tap.stop()

    try:
        result = conv.result()
    except Exception as e:
        print(f"[실패] {type(e).__name__}: {e}")
        return

    print(f"[완료] {result}")


omni.kit.async_engine.run_coroutine(_convert())
