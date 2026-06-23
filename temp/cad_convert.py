"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================


# file_format_args 는 dict[str, str] - 값 전부 문자열
CONVERT_OPTIONS = {
    "upAxis"         : "1",   # 0=default, 1=Y-up, 2=Z-up
    "tessLOD"        : "2",   # 0=ExtraLow ~ 4=ExtraHigh
    "instancingStyle": "1",
    "dMetersPerUnit" : "1.0",
}


async def _convert():
    converter = hoops_mod.get_instance()
    result = await converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    print("[완료]", result)


omni.kit.async_engine.run_coroutine(_convert())
