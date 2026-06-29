"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import omni.usd
import omni.kit.commands
import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"
DEST_PATH   = r"C:/data/out/model.usd"
# ==============================

LOAD_AFTER_CONVERT = True   # 변환 후 현재 스테이지에 reference 로 로드


# file_format_args 는 dict[str, str] - 값 전부 문자열
CONVERT_OPTIONS = {
    "upAxis"         : "1",      # 1=Y-up
    "tessLOD"        : "2",      # 2=Medium (기본값)
    "bInstancing"    : "false",  # 인스턴싱 비활성화
    "useMaterials"   : "false",  # 재질 없음
    "dMetersPerUnit" : "1.0",
}


def _load_into_stage(usd_path: str):
    """변환된 USD 를 현재 스테이지에 reference 로 추가."""
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    prim_path = omni.usd.get_stage_next_free_path(stage, "/World/Imported", False)
    omni.kit.commands.execute(
        "CreateReference",
        path_to=prim_path,
        asset_path=usd_path,
        usd_context=ctx,
    )
    print("[로드]", prim_path, "<-", usd_path)


async def _convert():
    converter = hoops_mod.get_instance()
    result = await converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    print("[완료]", result)

    if LOAD_AFTER_CONVERT:
        _load_into_stage(DEST_PATH)


omni.kit.async_engine.run_coroutine(_convert())
