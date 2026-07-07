"""
Script Editor 에서 실행 - STEP -> USD 변환 + 진행도 추적 (HoopsCoreConverter)

구조:
  1. carb 로거 + fd1(C stdout) tap 으로 나오는 모든 로그 라인을 수집
  2. hoops_progress 라인만 ProgressLogConsumer 로 파싱해 캐시에 저장 (push)
  3. 매 프레임 루프가 캐시된 현재 진행도를 출력 (pull)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os
import sys
import asyncio
import threading
import carb.logging
import omni.usd
import omni.kit.app
import omni.kit.commands
import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod
from omni.kit.converter.common import ProgressLogConsumer
from pxr import UsdGeom


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================

LOAD_AFTER_CONVERT = True   # 변환 후 현재 스테이지에 reference 로 로드
LOAD_PRIM_PATH     = None   # None 이면 /World 바로 아래에 파일명으로 생성


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


# ---------------- 스테이지 로드 ----------------

def _sanitize(name: str) -> str:
    """USD prim 이름으로 쓸 수 있게 정리 (영숫자/언더스코어만)."""
    s = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return ("_" + s) if s and s[0].isdigit() else (s or "Imported")


def _load_into_stage(usd_path: str, prim_path: str = None):
    """변환된 USD 를 현재 스테이지에 reference 로 추가."""
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()

    if prim_path is None:
        if not stage.GetPrimAtPath("/World").IsValid():
            UsdGeom.Xform.Define(stage, "/World")
        base = _sanitize(os.path.splitext(os.path.basename(usd_path))[0])
        prim_path = f"/World/{base}"

    final_path = prim_path
    i = 1
    while stage.GetPrimAtPath(final_path).IsValid():
        final_path = f"{prim_path}_{i:02d}"
        i += 1

    omni.kit.commands.execute(
        "CreateReference",
        path_to=final_path,
        asset_path=usd_path,
        usd_context=ctx,
    )
    print("[로드]", final_path, "<-", usd_path)


# ---------------- 로그 수집 ----------------

class StdoutTap:
    """C 레벨 stdout(fd 1)을 파이프로 복제해 라인 콜백으로 전달.

    네이티브(HOOPS)가 fd1 에 직접 쓰는 진행 로그는 carb/py stdout 캡처에
    안 잡히므로 fd1 자체를 가로챈다. 원본 콘솔로는 그대로 통과.
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
        sys.stdout.flush()
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


# ---------------- 진행도 캐시 ----------------

class ProgressWatcher:
    """들어오는 로그 라인을 ProgressLogConsumer 로 파싱해 최신 진행도를 캐시.

    feed()  : 로그 라인 유입 (push) - 어느 스레드에서 불려도 안전한 단순 대입만 수행
    step / value : 현재 단계명 / 진행도(0.0~1.0) 조회 (pull)
    """

    PREFIX = "[omni.converter.hoops_progress]"

    def __init__(self):
        self._consumer = ProgressLogConsumer(self.PREFIX)
        self.step = ""
        self.value = 0.0

    def feed(self, text: str):
        if "hoops_progress" not in text:
            return
        try:
            line = text if self.PREFIX in text else f"{self.PREFIX} {text}"
            ret = self._consumer.extract_line(line)
            if not ret:
                return
            type_name = getattr(ret[0], "name", str(ret[0])).lower()
            if len(ret) > 2 and "progress" in type_name:
                self.value = float(ret[2])            # 0.0 ~ 1.0
            elif len(ret) > 1:
                self.step = " ".join(str(x) for x in ret[1])
                self.value = 0.0                      # 새 단계 시작
        except Exception:
            pass


# ---------------- 메인 ----------------

async def _convert():
    converter = hoops_mod.get_instance()
    watcher = ProgressWatcher()

    # 수집 경로 1: carb 로거 (py stdout 에코 포함, Kit 로그 전부)
    def _on_log(source, level, filename, line_number, message):
        watcher.feed(str(message))

    logging = carb.logging.acquire_logging()
    handle = logging.add_logger(_on_log)

    # 수집 경로 2: fd1 tap (네이티브가 stdout 에 직접 쓰는 라인)
    tap = StdoutTap(watcher.feed)
    tap.start()

    # 변환을 별도 태스크로 돌리고, 매 프레임 캐시된 진행도 출력
    conv = asyncio.ensure_future(
        converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    )
    app = omni.kit.app.get_app()

    try:
        while not conv.done():
            # stdout 은 tap 중이므로 stderr(fd2) 로 출력
            os.write(2, f"[진행도] {watcher.step} {watcher.value * 100:.1f}%\n".encode())
            await app.next_update_async()
    finally:
        tap.stop()
        logging.remove_logger(handle)

    result = conv.result()
    print(f"[완료] {watcher.step} 100.0% | {result}")

    if LOAD_AFTER_CONVERT:
        _load_into_stage(DEST_PATH, LOAD_PRIM_PATH)


omni.kit.async_engine.run_coroutine(_convert())
