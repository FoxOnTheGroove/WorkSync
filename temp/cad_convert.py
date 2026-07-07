"""
Script Editor 에서 실행 - STEP -> USD 변환 (HoopsCoreConverter 직접 사용)

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os
import asyncio
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


def _snapshot(consumer) -> dict:
    """consumer 의 public 비호출 속성만 모아서 반환 (상태 확인용)."""
    snap = {}
    for a in dir(consumer):
        if a.startswith("_"):
            continue
        try:
            v = getattr(consumer, a)
        except Exception:
            continue
        if not callable(v):
            snap[a] = v
    return snap


async def _convert():
    converter = hoops_mod.get_instance()

    # HOOPS 진행 로그 파서 (프리픽스는 컨버터별로 다름)
    consumer = ProgressLogConsumer("[omni.converter.hoops_progress]")
    print("[consumer 메소드]", [a for a in dir(consumer) if not a.startswith("_")])

    # 변환을 별도 태스크로 돌리고, 끝날 때까지 1프레임마다 consumer 상태 출력
    conv = asyncio.ensure_future(
        converter.create_converter_task(TARGET_PATH, DEST_PATH, CONVERT_OPTIONS)
    )
    app = omni.kit.app.get_app()
    while not conv.done():
        print("[progress]", _snapshot(consumer))
        await app.next_update_async()

    result = conv.result()
    print("[완료]", result)

    if hasattr(consumer, "destroy"):
        consumer.destroy()

    if LOAD_AFTER_CONVERT:
        _load_into_stage(DEST_PATH, LOAD_PRIM_PATH)


omni.kit.async_engine.run_coroutine(_convert())
