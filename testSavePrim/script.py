import omni.usd
from pxr import Sdf

# ── CONFIG ─────────────────────────────────────────
PRIM_PATH   = "/BlueSphere"
TARGET_PATH = r"C:\path\to\sphere_blue.usda"  # ← 실제 경로로 교체
# ───────────────────────────────────────────────

stage = omni.usd.get_context().get_stage()

# 세션 레이어 포함 모든 오버라이드가 합산된 단일 레이어
flat = stage.Flatten()

# 해당 prim spec만 빈 레이어로 복사
dst_layer = Sdf.Layer.CreateAnonymous(".usda")
Sdf.CopySpec(flat, Sdf.Path(PRIM_PATH), dst_layer, Sdf.Path(PRIM_PATH))
dst_layer.defaultPrim = Sdf.Path(PRIM_PATH).name

dst_layer.Export(TARGET_PATH)
print(f"[SAVED] {PRIM_PATH} → {TARGET_PATH}")
