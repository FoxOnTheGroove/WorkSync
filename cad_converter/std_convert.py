"""
STEP/CAD -> USD 변환 로직 (HoopsCoreConverter 직접 사용) + 스테이지 로드.

UI(dummy_ui.py) 에서 호출하는 순수 기능 모듈.
사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os

import omni.usd
import omni.kit.commands
import omni.kit.converter.hoops_core as hoops_mod
from pxr import UsdGeom


# ====== UI 콤보박스용 옵션 정의 (라벨 -> file_format_args 값) ======
UP_AXIS_CHOICES = {
    "Y-up":    "1",
    "Z-up":    "2",
    "Default": "0",
}

TESS_LOD_CHOICES = {
    "ExtraLow":  "0",
    "Low":       "1",
    "Medium":    "2",
    "High":      "3",
    "ExtraHigh": "4",
}

# dMetersPerUnit: 스테이지 단위 스케일. 0.0 = 변환 단위 그대로 유지
METERS_PER_UNIT_CHOICES = {
    "Meter (1.0)":        "1.0",
    "Centimeter (0.01)":  "0.01",
    "Millimeter (0.001)": "0.001",
    "Keep Original (0.0)": "0.0",
}


def build_options(
    up_axis: str = "1",
    tess_lod: str = "2",
    instancing: bool = False,
    use_materials: bool = False,
    meters_per_unit: str = "1.0",
) -> dict:
    """file_format_args 는 dict[str, str] - 값 전부 문자열."""
    return {
        "upAxis"        : up_axis,
        "tessLOD"       : tess_lod,
        "bInstancing"   : "true" if instancing else "false",
        "useMaterials"  : "true" if use_materials else "false",
        "dMetersPerUnit": meters_per_unit,
    }


async def convert_async(src_path: str, dest_path: str, options: dict):
    """STEP -> USD 변환. 완료 후 결과 반환."""
    converter = hoops_mod.get_instance()
    result = await converter.create_converter_task(src_path, dest_path, options)
    print(f"[cad_converter] convert done: {result}")
    return result


def _sanitize(name: str) -> str:
    """USD prim 이름으로 쓸 수 있게 정리 (영숫자/언더스코어만)."""
    s = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return ("_" + s) if s and s[0].isdigit() else (s or "Imported")


def load_into_stage(usd_path: str, prim_path: str = None) -> str:
    """변환된 USD 를 현재 스테이지에 reference 로 추가.

    prim_path 지정 시 그 경로에, None 이면 /World 바로 아래에 파일명으로 생성.
    충돌 시 _01, _02 로 유니크 처리. 생성된 prim 경로 반환.
    """
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    if stage is None:
        print("[cad_converter] ERROR: no stage")
        return ""

    if prim_path:
        target = prim_path
    else:
        if not stage.GetPrimAtPath("/World").IsValid():
            UsdGeom.Xform.Define(stage, "/World")
        base = _sanitize(os.path.splitext(os.path.basename(usd_path))[0])
        target = f"/World/{base}"

    final_path = target
    i = 1
    while stage.GetPrimAtPath(final_path).IsValid():
        final_path = f"{target}_{i:02d}"
        i += 1

    omni.kit.commands.execute(
        "CreateReference",
        path_to=final_path,
        asset_path=usd_path,
        usd_context=ctx,
    )
    print(f"[cad_converter] loaded {final_path} <- {usd_path}")
    return final_path


def remove_prims(prim_paths: list) -> None:
    """로드로 생성된 prim 들을 스테이지에서 제거."""
    valid = [p for p in prim_paths if p]
    if not valid:
        return
    omni.kit.commands.execute("DeletePrims", paths=valid)
    print(f"[cad_converter] removed {valid}")
