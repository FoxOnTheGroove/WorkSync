from pxr import Usd, Sdf
import omni.usd, omni.client
import zipfile, os, tempfile, shutil

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
    omni.client.copy(src_usdz, local_usdz, behavior=omni.client.CopyBehavior.OVERWRITE)
else:
    local_usdz = src_usdz

# 3) usdz 압축 해제
unpack = os.path.join(work, "unpacked")
os.makedirs(unpack, exist_ok=True)
with zipfile.ZipFile(local_usdz) as z:
    z.extractall(unpack)
    names = z.namelist()
print("내용물:", names)

# 4) 메인 레이어 찾기
main_layer_name = next(n for n in names if n.endswith((".usd", ".usdc", ".usda")))
main_layer_path = os.path.join(unpack, main_layer_name)
print("메인 레이어:", main_layer_path)

# 5) 씨의 루트 레이어 오버라이드만 언팩 레이어에 병합
# Flatten 사용 안 함 → 텍스처 경로를 절대경로로 곹쓰는 문제 방지
scene_root = stage.GetRootLayer()
src_stage = Usd.Stage.Open(main_layer_path)
src_default = src_stage.GetDefaultPrim()
src_root_path = src_default.GetPath() if src_default else next(
    p.GetPath() for p in src_stage.GetPseudoRoot().GetChildren())

def merge_overrides(src_layer, src_sdf_path, dst_layer, dst_sdf_path):
    src_spec = src_layer.GetObjectAtPath(src_sdf_path)
    if not isinstance(src_spec, Sdf.PrimSpec):
        return
    dst_spec = dst_layer.GetObjectAtPath(dst_sdf_path)
    if not dst_spec:
        return
    # 속성 오버라이드만 복사 (원본 텍스처 경로 유지)
    for attr_name, attr_spec in src_spec.attributes.items():
        if attr_spec.HasDefaultValue():
            dst_attr = dst_spec.attributes.get(attr_name)
            if dst_attr:
                dst_attr.default = attr_spec.default
            else:
                new_attr = Sdf.AttributeSpec(dst_spec, attr_name, attr_spec.typeName)
                new_attr.default = attr_spec.default
    # 자식 프림 재귀
    for child_name in list(src_spec.nameChildren.keys()):
        merge_overrides(
            src_layer, src_sdf_path.AppendChild(child_name),
            dst_layer, dst_sdf_path.AppendChild(child_name)
        )

merge_overrides(scene_root, Sdf.Path(prim_path),
                src_stage.GetRootLayer(), src_root_path)
print("오버라이드 병합 완료")

# 6) unpack 폴더 안에서 in-place Save
src_stage.GetRootLayer().Save()

# 7) usdz 재압축 (ZIP_STORED: usdz 스펙상 압축 금지)
out_usdz = os.path.join(work, f"{base}.usdz")
with zipfile.ZipFile(out_usdz, 'w', zipfile.ZIP_STORED) as zf:
    for root, dirs, files in os.walk(unpack):
        for f in files:
            abs_path = os.path.join(root, f)
            arcname = os.path.relpath(abs_path, unpack)
            zf.write(abs_path, arcname)
print("usdz:", out_usdz)

# 8) 독립 usd 폴더 (unpack 그대로 복사 → 텍스처 상대경로 이미 정상)
final_dir = os.path.join(work, "final")
shutil.copytree(unpack, final_dir)
print("usd 폴더:", final_dir)

# 9) Nucleus 업로드
omni.client.copy(out_usdz, f"{out_dir}/{base}.usdz",
                 behavior=omni.client.CopyBehavior.OVERWRITE)
for root, dirs, files in os.walk(final_dir):
    for f in files:
        abs_path = os.path.join(root, f)
        rel = os.path.relpath(abs_path, final_dir)
        omni.client.copy(abs_path, f"{out_dir}/{base}/{rel}",
                         behavior=omni.client.CopyBehavior.OVERWRITE)
