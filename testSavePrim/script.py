import omni.usd

# ── CONFIG ─────────────────────────────────────────
TARGET_PATH = r"C:\path\to\testSavePrim\sphere_blue.usda"
# ───────────────────────────────────────────────

stage = omni.usd.get_context().get_stage()
stage.GetRootLayer().Export(TARGET_PATH)
print(f"[SAVED] {TARGET_PATH}")
