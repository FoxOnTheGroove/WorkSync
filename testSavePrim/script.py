import omni.usd
from pxr import Sdf

# ── CONFIG ─────────────────────────────────────────
PRIM_PATH   = "/BlueSphere"
TARGET_PATH = r"C:\path\to\sphere_blue.usda"  # ← 실제 경로로 교체
# ───────────────────────────────────────────────

stage = omni.usd.get_context().get_stage()
flat  = stage.Flatten()

# 파일 기반 레이어로 목적지 생성 (익명 레이어는 CopySpec에서 실패함)
dst_layer = Sdf.Layer.FindOrOpen(TARGET_PATH)
if dst_layer:
    dst_layer.Clear()
else:
    dst_layer = Sdf.Layer.CreateNew(TARGET_PATH)

Sdf.CopySpec(flat, Sdf.Path(PRIM_PATH), dst_layer, Sdf.Path(PRIM_PATH))
dst_layer.defaultPrim = Sdf.Path(PRIM_PATH).name
dst_layer.Save()
print(f"[SAVED] {PRIM_PATH} → {TARGET_PATH}")
