import os
import zipfile
import shutil
from pxr import UsdUtils


def package_usdz_with_parts(root_usd: str, out_usdz: str, parts_subdir: str = "parts"):
    root_usd = os.path.abspath(root_usd)
    anchor_dir = os.path.dirname(root_usd)
    parts_dir = os.path.join(anchor_dir, parts_subdir)

    # 1차 변환
    UsdUtils.CreateNewUsdzPackage(root_usd, out_usdz)

    # parts 폴더 전체 = 진실의 원천
    USD_EXTS = (".usd", ".usda", ".usdc")
    expected = {}
    for dp, _, files in os.walk(parts_dir):
        for fn in files:
            if fn.lower().endswith(USD_EXTS):
                ap = os.path.join(dp, fn)
                rel = os.path.relpath(ap, anchor_dir).replace(os.sep, "/")
                expected[rel] = ap

    with zipfile.ZipFile(out_usdz) as z:
        inside = set(z.namelist())

    missing = {arc: src for arc, src in expected.items() if arc not in inside}
    if not missing:
        print(f"[ok] 누락 없음. {len(inside)}개.")
        return out_usdz

    print(f"[fix] 누락 {len(missing)}개:")
    for arc in missing:
        print(f"   + {arc}")

    # 전체 재작성 (비압축 = ZIP_STORED 유지)
    tmp = out_usdz + ".tmp.usdz"
    with zipfile.ZipFile(out_usdz) as zin, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zout:
        # 기존 파일 먼저 (순서 유지 = 첫 파일이 default layer로 남음)
        for name in zin.namelist():
            zout.writestr(name, zin.read(name))
        # 누락분 추가
        for arc, src in missing.items():
            with open(src, "rb") as f:
                zout.writestr(arc, f.read())
    os.replace(tmp, out_usdz)

    with zipfile.ZipFile(out_usdz) as z:
        final = set(z.namelist())
    still = [a for a in expected if a not in final]
    print(f"[done] 최종 {len(final)}개. 여전히 누락: {still or '없음'}")
    return out_usdz


if __name__ == "__main__":
    package_usdz_with_parts("main.usd", "out.usdz")
