"""
Script Editor 에서 실행 - STEP -> USD 변환 + 진행도 추적 (HoopsCoreConverter)

구조:
  1. carb 로거로 들어오는 모든 로그 라인 수집 (py stdout 에코 포함)
  2. hoops_progress 라인만 ProgressLogConsumer 로 파싱해 캐시에 저장 (push)
  3. 매 프레임 루프가 캐시된 현재 진행도를 출력 (pull)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import asyncio
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


class ProgressWatcher:
    """들어오는 로그 라인을 ProgressLogConsumer 로 파싱해 최신 진행도를 캐시.

    feed()  : 로그 라인 유입 (push) - 단순 대입만 수행
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


async def _convert():
    converter = hoops_mod.get_instance()
    watcher = ProgressWatcher()

    def _on_log(source, level, filename, line_number, message):
        watcher.feed(str(message))

    logging = carb.logging.acquire_logging()
    handle = logging.add_logger(_on_log)

    # 변환을 별도 태스크로 돌리고, 매 프레임 캐시된 진행도 출력
    conv = asyncio.ensure_future(
        converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    )
    app = omni.kit.app.get_app()

    try:
        while not conv.done():
            print(f"[진행도] {watcher.step} {watcher.value * 100:.1f}%")
            await app.next_update_async()
    finally:
        logging.remove_logger(handle)

    try:
        result = conv.result()
    except Exception as e:
        print(f"[실패] {type(e).__name__}: {e}")
        return

    print(f"[완료] {watcher.step} 100.0% | {result}")


omni.kit.async_engine.run_coroutine(_convert())
