import omni.usd
from pxr import Sdf

# ── CONFIG ─────────────────────────────────────────
PRIM_NAME   = "BlueSphere"   # prim 이름 (경로 아님)
TARGET_PATH = r"C:\path\to\sphere_blue.usda"  # ← 실제 경로로 교체
# ───────────────────────────────────────────────

stage = omni.usd.get_context().get_stage()
flat  = stage.Flatten()

# 이름으로 실제 경로 탐색 (Omniverse가 /World 아래 배치할 수 있음)
src_path = None
for root_spec in flat.rootPrims:
    if root_spec.name == PRIM_NAME:
        src_path = root_spec.path
        break
    for child in root_spec.nameChildren.values():
        if child.name == PRIM_NAME:
            src_path = child.path
            break
    if src_path:
        break

if not src_path:
    raise RuntimeError(f"'{PRIM_NAME}' not found. root prims: {[s.name for s in flat.rootPrims]}")

# 항상 루트에 re-root하여 복사 → 부모 스펙 불필요
dst_path = Sdf.Path("/" + PRIM_NAME)

dst_layer = Sdf.Layer.FindOrOpen(TARGET_PATH)
if dst_layer:
    dst_layer.Clear()
else:
    dst_layer = Sdf.Layer.CreateNew(TARGET_PATH)

Sdf.CopySpec(flat, src_path, dst_layer, dst_path)
dst_layer.defaultPrim = PRIM_NAME
dst_layer.Save()
print(f"[SAVED] {src_path} → {dst_path} ({TARGET_PATH})")
