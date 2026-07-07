"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os
import inspect
import carb.logging
import omni.usd
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
LOAD_PRIM_PATH     = None   # None 이면 /World 바로 아래에 파일명으로 생성, 경로 지정 시 그대로 사용


# file_format_args 는 dict[str, str] - 값 전부 문자열
CONVERT_OPTIONS = {
    "upAxis"         : "1",      # 1=Y-up
    "tessLOD"        : "2",      # 2=Medium (기본값)
    "bInstancing"    : "false",  # 인스턴싱 비활성화
    "useMaterials"   : "false",  # 재질 없음
    "dMetersPerUnit" : "1.0",
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


async def _convert():
    converter = hoops_mod.get_instance()

    # HOOPS 진행 로그 파서 (프리픽스는 컨버터별로 다름)
    consumer = ProgressLogConsumer("[omni.converter.hoops_progress]")

    # 클래스 소스 1회 출력 - extract_line 반환/내부 상태 확인용
    try:
        print(inspect.getsource(type(consumer)))
    except Exception as e:
        print("[source 확인 실패]", e)

    # carb 로그 리스너: hoops_progress 라인만 골라 extract_line 에 먹임
    def _on_log(source, level, filename, line_number, message):
        if "hoops_progress" not in message:
            return
        try:
            ret = consumer.extract_line(message)
            # 반환값 + 내부 상태를 같이 출력 (프리픽스 겹치지 않게 [hoops%] 사용)
            print("[hoops%]", ret, vars(consumer))
        except Exception as e:
            print("[hoops% 오류]", e)

    logging = carb.logging.acquire_logging()
    handle = logging.add_logger(_on_log)

    try:
        result = await converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
        print("[완료]", result)
    finally:
        logging.remove_logger(handle)

    if LOAD_AFTER_CONVERT:
        _load_into_stage(DEST_PATH, LOAD_PRIM_PATH)


omni.kit.async_engine.run_coroutine(_convert())
