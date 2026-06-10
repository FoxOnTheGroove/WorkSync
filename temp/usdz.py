import os
import zipfile
from pxr import UsdUtils


def package_usdz_with_parts(root_usd, out_usdz, parts_subdir="parts"):
    root_usd = os.path.abspath(root_usd)
    anchor_dir = os.path.dirname(root_usd)
    parts_dir = os.path.join(anchor_dir, parts_subdir)

    UsdUtils.CreateNewUsdzPackage(root_usd, out_usdz)

    USD_EXTS = (".usd", ".usda", ".usdc")
    # parts 폴더의 모든 USD: basename -> abs path
    parts_files = {}
    for dp, _, files in os.walk(parts_dir):
        for fn in files:
            if fn.lower().endswith(USD_EXTS):
                parts_files[fn] = os.path.join(dp, fn)

    # 이미 패키지에 든 파일의 basename 집합
    with zipfile.ZipFile(out_usdz) as z:
        inside_names = z.namelist()
    inside_basenames = {os.path.basename(n) for n in inside_names}

    # basename이 패키지에 없는 것만 진짜 누락
    missing = {fn: src for fn, src in parts_files.items()
               if fn not in inside_basenames}

    if not missing:
        print(f"[ok] 누락 없음. {len(inside_names)}개.")
        return out_usdz

    print(f"[fix] 누락 {len(missing)}개:")
    for fn in missing:
        print(f"   + {fn}")

    # 누락분의 arcname은 패키지의 기존 parts 경로 규칙을 따라감
    arc_prefix = _detect_parts_prefix(inside_names, parts_files, inside_basenames)
    print(f"[info] 누락분 추가 위치: '{arc_prefix}'")

    tmp = out_usdz + ".tmp.usdz"
    with zipfile.ZipFile(out_usdz) as zin, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zout:
        for name in zin.namelist():
            zout.writestr(name, zin.read(name))
        for fn, src in missing.items():
            arc = arc_prefix + fn if arc_prefix else fn
            with open(src, "rb") as f:
                zout.writestr(arc, f.read())
    os.replace(tmp, out_usdz)

    with zipfile.ZipFile(out_usdz) as z:
        final_basenames = {os.path.basename(n) for n in z.namelist()}
    still = [fn for fn in parts_files if fn not in final_basenames]
    print(f"[done] 여전히 누락: {still or '없음'}")
    return out_usdz


def _detect_parts_prefix(inside_names, parts_files, inside_basenames):
    """패키지에 이미 들어간 parts 파일이 어떤 경로 프리픽스로 저장됐는지 추출."""
    for n in inside_names:
        if os.path.basename(n) in parts_files and os.path.basename(n) in inside_basenames:
            # 같은 폴더에 들어간 형제 파일의 경로 규칙을 그대로 차용
            d = os.path.dirname(n)
            return (d + "/") if d else ""
    return ""  # 단서 없으면 루트에


if __name__ == "__main__":
    package_usdz_with_parts("main.usd", "out.usdz")
