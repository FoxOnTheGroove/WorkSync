import omni.usd
from pxr import Gf

# ── CONFIG ─────────────────────────────────────────
SHADER_PATH = "/BlueSphere/Mat/PBR"
NEW_COLOR   = (0.0, 0.8, 0.2)  # ← 원하는 색으로 교체
# ───────────────────────────────────────────────

stage = omni.usd.get_context().get_stage()

shader = stage.GetPrimAtPath(SHADER_PATH)
shader.GetAttribute("inputs:diffuseColor").Set(Gf.Vec3f(*NEW_COLOR))

layer = stage.GetRootLayer()
layer.Save()
print(f"[SAVED] {layer.identifier}")
