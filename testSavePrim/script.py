from pxr import Usd, Sdf, UsdUtils
import omni.usd, omni.client
import zipfile, os, tempfile

stage = omni.usd.get_context().get_stage()
prim_path = "/World/MyPrim"
out_dir = "omniverse://server/Projects/out"
base = prim_path.split("/")[-1]

# 1) 원본 usdz 경로 찾기
src_usdz = None
for layer in stage.GetUsedLayers():
    ident = layer.identifier
    if ident.endswith(".usdz") or ".usdz[" in ident:
        src_usdz = ident.split(".usdz[")[0] + ".usdz" if ".usdz[" in ident else ident
        break
print("원본 usdz:", src_usdz)

# 2) Nucleus면 로컬로 다운로드
work = tempfile.mkdtemp()
if src_usdz.startswith("omniverse://"):
    local_usdz = os.path.join(work, "src.usdz")
    res = omni.client.copy(src_usdz, local_usdz,
                           behavior=omni.client.CopyBehavior.OVERWRITE)
    print("download:", res)
else:
    local_usdz = src_usdz

# 3) usdz 압축 해제
unpack = os.path.join(work, "unpacked")
os.makedirs(unpack, exist_ok=True)
with zipfile.ZipFile(local_usdz) as z:
    z.extractall(unpack)
    names = z.namelist()
print("내용물:", names)

# 4) 풀린 메인 레이어 찾기 (보통 첫 .usd/.usdc/.usda)
main_layer = next(os.path.join(unpack, n) for n in names
                  if n.endswith((".usd", ".usdc", ".usda")))
print("메인 레이어:", main_layer)

# 5) 현재 stage의 flatten에서 prim 오버라이드를 unpack 레이어에 in-place 복사
flat = stage.Flatten()
src_stage = Usd.Stage.Open(main_layer)

src_default = src_stage.GetDefaultPrim()
src_root_path = src_default.GetPath() if src_default else next(
    p.GetPath() for p in src_stage.GetPseudoRoot().GetChildren())

Sdf.CopySpec(flat, Sdf.Path(prim_path), src_stage.GetRootLayer(), src_root_path)

# 6) unpack 폴더 안에서 in-place Save → 텍스처 상대경로 유지
src_stage.GetRootLayer().Save()
print("수정 저장:", main_layer)

# 7) unpack 폴더를 그대로 재압축 → usdz 내부 경로 유지
# usdz 스펙: 내부 파일은 반드시 ZIP_STORED (압축 금지)
out_usdz = os.path.join(work, f"{base}.usdz")
with zipfile.ZipFile(out_usdz, 'w', zipfile.ZIP_STORED) as zf:
    for root, dirs, files in os.walk(unpack):
        for f in files:
            abs_path = os.path.join(root, f)
            arcname = os.path.relpath(abs_path, unpack)  # 아카이브 내 상대경로 유지
            zf.write(abs_path, arcname)
print("usdz 생성:", out_usdz)

# 8) 독립 .usd 폴더로도 export (선택)
# main_layer가 unpack 안에 있으므로 텍스처 상대경로 정상
final_dir = os.path.join(work, "final")
UsdUtils.LocalizeAsset(main_layer, final_dir)
print("usd 결과 폴더:", final_dir)

# 9) Nucleus로 업로드 (선택)
omni.client.copy(out_usdz, f"{out_dir}/{base}.usdz",
                 behavior=omni.client.CopyBehavior.OVERWRITE)
for f in os.listdir(final_dir):
    omni.client.copy(os.path.join(final_dir, f), f"{out_dir}/{f}",
                     behavior=omni.client.CopyBehavior.OVERWRITE)
