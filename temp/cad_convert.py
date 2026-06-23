"""
Kit Script Editor 에서 바로 실행하는 STEP -> USD 변환.

사용법:
  아래 TARGET_PATH, DEST_PATH 두 개만 수정하고 Script Editor 에서 Run.

  - 변환: omni.kit.asset_converter (in-process, hoops_core 활성화 시 STEP 처리)
  - Up Axis = Y: 변환된 USD 에 UsdGeom 으로 강제 적용

사전 조건:
  Extension Manager 에서 아래가 켜져 있어야 함
    omni.kit.converter.hoops_core
    omni.services.convert.cad   (또는 omni.kit.converter.cad 묶음)
"""

import asyncio
import omni.kit.asset_converter as asset_converter
from pxr import Usd, UsdGeom


# ====== 여기 두 개만 입력 ======
TARGET_PATH = r"C:/data/model.stp"        # 변환할 STEP 파일
DEST_PATH   = r"C:/data/out/model.usd"    # 출력 USD 경로
# ==============================


async def _convert(target_path: str, dest_path: str):
    ctx = asset_converter.AssetConverterContext()  # 기본 옵션 그대로

    task = asset_converter.get_instance().create_converter_task(
        target_path,
        dest_path,
        lambda p, s: print(f"[{p*100:5.1f}%] {s}"),
        ctx,
    )

    ok = await task.wait_until_finished()
    if not ok:
        print("[실패]", task.get_status(), task.get_error_message())
        return

    # --- Up Axis = Y 강제 ---
    stage = Usd.Stage.Open(dest_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.GetRootLayer().Save()

    print(f"[완료] {dest_path}  (Up Axis = Y)")


asyncio.ensure_future(_convert(TARGET_PATH, DEST_PATH))
