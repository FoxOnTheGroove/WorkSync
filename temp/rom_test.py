import numpy as np
import omni.usd
from pxr import UsdGeom, Vt, Sdf

# ==== 경로 수정 ====
ROM_DIR   = r"C:\path\to\rom"
POINTS    = ROM_DIR + r"\points.bin"
SNAPSHOTS = [(3, ROM_DIR + r"\scenario1\snapshot1.bin"),
             (4, ROM_DIR + r"\scenario1\snapshot2.bin")]
PRIM_PATH = "/World/rom_cloud"
# ==================

# --- points 로드 (헤더 8바이트 스킵) ---
raw = np.fromfile(POINTS, dtype=np.float64)
header = raw[0]
pts = raw[1:].reshape(-1, 3)
N = len(pts)
print(f"[ROM] header={header}, N={N}")
print(f"[ROM] bounds min={pts.min(axis=0)}, max={pts.max(axis=0)}")

# --- prim 생성 ---
stage = omni.usd.get_context().get_stage()
if stage.GetPrimAtPath(PRIM_PATH):
    stage.RemovePrim(PRIM_PATH)

geom = UsdGeom.Points.Define(stage, Sdf.Path(PRIM_PATH))
geom.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(pts.astype(np.float32)))

# 포인트 크기: 바운딩박스 대각선의 0.2%
diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
w = diag * 0.002
geom.GetWidthsAttr().Set(Vt.FloatArray([w] * N))

# --- 스냅샷 -> displayColor time samples ---
def to_colors(f):
    """스칼라 -> jet 유사 컬러맵 (N,3)"""
    t = (f - f.min()) / (np.ptp(f) or 1.0)
    r = np.clip(1.5 - abs(4*t - 3), 0, 1)
    g = np.clip(1.5 - abs(4*t - 2), 0, 1)
    b = np.clip(1.5 - abs(4*t - 1), 0, 1)
    return np.stack([r, g, b], axis=1).astype(np.float32)

color_attr = geom.GetDisplayColorAttr()
for t_code, path in SNAPSHOTS:
    f = np.fromfile(path, dtype=np.float64, offset=8)
    assert f.size == N, f"{path}: size {f.size} != N {N}"
    print(f"[ROM] t={t_code}: field min={f.min():.4g}, max={f.max():.4g}")
    color_attr.Set(Vt.Vec3fArray.FromNumpy(to_colors(f)), time=t_code)

geom.GetDisplayColorPrimvar().SetInterpolation(UsdGeom.Tokens.vertex)
print(f"[ROM] done: {PRIM_PATH}")
