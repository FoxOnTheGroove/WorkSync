"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os
import sys
import threading
import carb.logging
import omni.usd
import omni.kit.commands
import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod
from omni.kit.converter.common import ProgressLogConsumer, ProgressStepType
from pxr import UsdGeom


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================

LOAD_AFTER_CONVERT = True   # 변환 후 현재 스테이지에 reference 로 로드
LOAD_PRIM_PATH     = None   # None 이면 /World 바로 아래에 파일명으로 생성, 경로 지정 시 그대로 사용


# file_format_args 는 dict[str, str] - 값 전부 문자열
CONVERT_OPTIONS = {
    "upAxis"            : "1",      # 1=Y-up
    "tessLOD"           : "2",      # 2=Medium (기본값)
    "bInstancing"       : "false",  # 인스턴싱 비활성화
    "useMaterials"      : "false",  # 재질 없음
    "dMetersPerUnit"    : "1.0",
    # 진행 로그(*Begin*/*step*/*prog*) 는 이 옵션에 게이트됨 - 신규/레거시 키 둘 다 지정
    "reportProgress"    : "true",
    "bReportProgress"   : "true",
    "reportProgressFreq": "10.0",   # 초당 리포팅 횟수 [1~10]
}


def _sanitize(name: str) -> str:
    """USD prim 이름으로 쓸 수 있게 정리 (영숫자/언더스코어만)."""
    s = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return ("_" + s) if s and s[0].isdigit() else (s or "Imported")


def _load_into_stage(usd_path: str, prim_path: str = None):
    """변환된 USD 를 현재 스테이지에 reference 로 추가.

    prim_path 지정 시 그 경로에, None 이면 /World 바로 아래에 파일명으로 생성.
    """
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()

    if prim_path is None:
        # /World 바로 아래에 USD 파일명으로
        if not stage.GetPrimAtPath("/World").IsValid():
            UsdGeom.Xform.Define(stage, "/World")
        base = _sanitize(os.path.splitext(os.path.basename(usd_path))[0])
        prim_path = f"/World/{base}"

    # 충돌 시 유니크 처리
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


class StdoutTap:
    """C 레벨 stdout(fd 1)을 파이프로 복제해 라인 콜백으로 전달.

    네이티브(HOOPS)가 fd1 에 직접 쓰는 진행 로그는 carb/py stdout 캡처에
    안 잡히므로, fd1 자체를 가로챈다. 원본 콘솔로는 그대로 통과시킴.
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
            os.dup2(self._saved_fd, 1)    # fd1 원복 -> 파이프 write 끝 닫힘 -> pump 종료
            os.close(self._saved_fd)
            self._saved_fd = None
        if self._read_fd is not None:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = None


async def _convert():
    converter = hoops_mod.get_instance()

    # HOOPS 진행 로그 파서 (프리픽스는 컨버터별로 다름)
    PREFIX = "[omni.converter.hoops_progress]"
    consumer = ProgressLogConsumer(PREFIX)

    # step: 현재 단계명 / raw: 원본 라인 확인용 카운터
    state = {"step": "", "raw": 0}

    # 공용 라인 핸들러 - carb 로거(py print 경로)와 fd tap(네이티브 경로) 둘 다 여기로
    # extract_line 반환: [ProgressStepType, decoded_msg(list)]
    #   PROGRESS 타입이면 [ProgressStepType, decoded_msg, 진행률(0~1 float)]
    def _handle_line(text: str, origin: str):
        if "hoops_progress" not in text:
            return
        if state["raw"] < 5:
            state["raw"] += 1
            os.write(2, f"[hoops raw/{origin}] {text!r}\n".encode())

        try:
            line = text if PREFIX in text else f"{PREFIX} {text}"
            ret = consumer.extract_line(line)
            if not ret:
                return
            type_name = getattr(ret[0], "name", str(ret[0])).lower()

            if len(ret) > 2 and "progress" in type_name:
                out = f"[hoops%] {state['step']} {ret[2] * 100:.1f}%"
            elif len(ret) > 1:
                state["step"] = " ".join(str(x) for x in ret[1])
                out = f"[hoops step] {state['step']}"
            else:
                out = f"[hoops ret] {ret}"
            # tap 스레드에서도 안전하게 stderr 로 출력 (stdout 은 지금 가로채는 중)
            os.write(2, (out + "\n").encode())
        except Exception as e:
            os.write(2, f"[hoops% 오류] {e} | {text!r}\n".encode())

    def _on_log(source, level, filename, line_number, message):
        _handle_line(str(message), "carb")

    logging = carb.logging.acquire_logging()
    handle = logging.add_logger(_on_log)

    tap = StdoutTap(lambda line: _handle_line(line, "fd1"))
    tap.start()

    try:
        result = await converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    finally:
        tap.stop()
        logging.remove_logger(handle)

    print("[완료]", result)

    if LOAD_AFTER_CONVERT:
        _load_into_stage(DEST_PATH, LOAD_PRIM_PATH)


omni.kit.async_engine.run_coroutine(_convert())
