"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import asyncio
import omni.kit.app
import omni.kit.converter.hoops_core as hoops_mod


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================


CONVERT_OPTIONS = {
    "iUpAxis"        : 1,     # 0=default, 1=Y-up, 2=Z-up
    "tessLOD"        : 2,     # 0=ExtraLow ~ 4=ExtraHigh (기본 Medium)
    "instancingStyle": 1,     # 1=Reference, 2=InstanceableReference
    "dMetersPerUnit" : 1.0,
}


async def _convert():
    converter = hoops_mod.get_instance()
    task = converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)

    # Kit 업데이트 루프에 양보하면서 완료 대기
    while not task.finished():
        await omni.kit.app.get_app().next_update_async()

    print("[결과]", task.get_result())


asyncio.ensure_future(_convert())
