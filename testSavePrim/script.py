from pxr import Usd, Gf

# ── CONFIG ────────────────────────────────────────────────────────────────────
USD_FILE_PATH = r"C:\path\to\testSavePrim\sphere_blue.usda"  # ← 실제 경로로 교체
PRIM_PATH     = "/BlueSphere"
NEW_TRANSLATE = (100.0, 0.0, 0.0)  # 테스트: X축으로 100 이동
# ─────────────────────────────────────────────────────────────────────────────


def main():
    stage = Usd.Stage.Open(USD_FILE_PATH)
    if not stage:
        print(f"[ERROR] 파일을 열 수 없습니다: {USD_FILE_PATH}")
        return

    prim = stage.GetPrimAtPath(PRIM_PATH)
    if not prim.IsValid():
        print(f"[ERROR] Prim을 찾을 수 없습니다: {PRIM_PATH}")
        return

    attr = prim.GetAttribute("xformOp:translate")
    print(f"[BEFORE] translate = {attr.Get()}")

    attr.Set(Gf.Vec3f(*NEW_TRANSLATE))
    print(f"[AFTER]  translate = {attr.Get()}")

    # 전체 stage가 아닌 이 USD 파일의 레이어만 저장
    root_layer = stage.GetRootLayer()
    root_layer.Save()
    print(f"[SAVED]  {root_layer.identifier}")


main()
