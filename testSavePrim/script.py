from pxr import Usd, Gf

# ── CONFIG ────────────────────────────────────────────────────────────────────
USD_FILE_PATH = r"C:\path\to\testSavePrim\sphere_blue.usda"  # ← 실제 경로로 교체
PRIM_PATH     = "/BlueSphere"

MODIFY_TRANSLATE = True
NEW_TRANSLATE    = (100.0, 0.0, 0.0)

MODIFY_RADIUS = False
NEW_RADIUS    = 80.0

MODIFY_COLOR = False
NEW_COLOR    = (1.0, 0.2, 0.0)  # RGB (0~1)
# ─────────────────────────────────────────────────────────────────────────────


def _modify(prim, attr_name, value):
    attr = prim.GetAttribute(attr_name)
    if not attr or not attr.IsValid():
        print(f"  [SKIP] 속성 없음: {attr_name}")
        return
    before = attr.Get()
    attr.Set(value)
    print(f"  {attr_name}: {before}  →  {attr.Get()}")


def main():
    # 1. 오브젝트 USD 열기
    stage = Usd.Stage.Open(USD_FILE_PATH)
    if not stage:
        print(f"[ERROR] 파일을 열 수 없습니다: {USD_FILE_PATH}")
        return

    # 2. Prim 가져오기
    prim = stage.GetPrimAtPath(PRIM_PATH)
    if not prim.IsValid():
        print(f"[ERROR] Prim 없음: {PRIM_PATH}")
        return

    # 3. 수정
    print(f"[MODIFY] {PRIM_PATH}")
    if MODIFY_TRANSLATE:
        _modify(prim, "xformOp:translate", Gf.Vec3f(*NEW_TRANSLATE))
    if MODIFY_RADIUS:
        _modify(prim, "radius", NEW_RADIUS)
    if MODIFY_COLOR:
        shader = stage.GetPrimAtPath(PRIM_PATH + "/Mat/PBR")
        _modify(shader, "inputs:diffuseColor", Gf.Vec3f(*NEW_COLOR))

    # 4. 오브젝트 USD만 덮어쓰기 (stage 전체 저장 아님)
    root_layer = stage.GetRootLayer()
    root_layer.Save()
    print(f"[SAVED]  {root_layer.identifier}")


main()
