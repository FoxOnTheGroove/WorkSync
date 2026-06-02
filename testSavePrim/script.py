from pxr import Usd, Sdf, UsdUtils, Ar
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

# 5) 풀린 원본을 열고, 현재 stage의 변형(오버라이드)을 덮어씌움
src_stage = Usd.Stage.Open(main_layer)

# 현재 stage에서 해당 프림의 오버라이드만 추출
flat = stage.Flatten()

# 풀린 stage의 루트 프림 경로 확인
src_default = src_stage.GetDefaultPrim()
src_root_path = src_default.GetPath() if src_default else next(
    p.GetPath() for p in src_stage.GetPseudoRoot().GetChildren())

# 현재 프림의 합성 결과를 풀린 stage 위에 복사 (변형 반영)
Sdf.CopySpec(flat, prim_path, src_stage.GetRootLayer(), src_root_path)

# 6) 독립 .usd로 export (텍스처는 unpack 폴더의 일반 파일 상대경로)
usd_path = os.path.join(work, f"{base}.usd")
src_stage.GetRootLayer().Export(usd_path)

# 7) 텍스처까지 묶어 최종 폴더로 localize
resolver = Ar.GetResolver()
ctx = resolver.CreateDefaultContextForAsset(usd_path)
with Ar.ResolverContextBinder(ctx):
    UsdUtils.LocalizeAsset(Sdf.AssetPath(usd_path), os.path.join(work, "final"))

print("결과 폴더:", os.path.join(work, "final"))

# 8) Nucleus 출력 폴더로 업로드 (선택)
final = os.path.join(work, "final")
for f in os.listdir(final):
    omni.client.copy(os.path.join(final, f), f"{out_dir}/{f}",
                     behavior=omni.client.CopyBehavior.OVERWRITE)
