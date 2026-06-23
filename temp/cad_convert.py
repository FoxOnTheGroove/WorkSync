"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod
from pxr import Usd, UsdGeom


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================


# dict[str, str] 강제 - 값 전부 문자열
CONVERT_OPTIONS = {
    "tessLOD"        : "2",
    "instancingStyle": "1",
    "dMetersPerUnit" : "1.0",
}


async def _convert():
    converter = hoops_mod.get_instance()
    result = await converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    print("[변환 결과]", result)

    # Y-up 은 옵션 미적용 대비 USD 직접 강제
    stage = Usd.Stage.Open(DEST_PATH)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.GetRootLayer().Save()
    print("[완료] Y-up 적용:", DEST_PATH)


omni.kit.async_engine.run_coroutine(_convert())
