import os
import zipfile
import tempfile
from pxr import Usd, UsdUtils


def package_usdz_with_parts(root_usd: str, out_usdz: str, parts_subdir: str = "parts"):
    """
    CreateNewUsdzPackage로 변환 후,
    parts 폴더의 모든 USD를 스캔해서 누락분을 강제로 채워넣음.
    (해시 파일명이 매번 바뀌어도 폴더만 보면 되므로 안전)
    """
    root_usd = os.path.abspath(root_usd)
    anchor_dir = os.path.dirname(root_usd)
    parts_dir = os.path.join(anchor_dir, parts_subdir)

    # --- 1차 변환 ---
    UsdUtils.CreateNewUsdzPackage(root_usd, out_usdz)

    # --- parts 폴더 전체 스캔이 진실의 원천 ---
    USD_EXTS = (".usd", ".usda", ".usdc", ".usdz")
    expected = {}  # arcname -> abs source path
    for dirpath, _, files in os.walk(parts_dir):
        for fn in files:
            if fn.lower().endswith(USD_EXTS):
                abs_path = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_path, anchor_dir).replace(os.sep, "/")
                expected[rel] = abs_path

    # 메인 파일 자신도 포함되어야 하지만 그건 1차 변환이 이미 넣음
    with zipfile.ZipFile(out_usdz) as z:
        inside = set(z.namelist())

    missing = {arc: src for arc, src in expected.items() if arc not in inside}

    if not missing:
        print(f"[ok] parts 누락 없음. {len(inside)}개 파일.")
        return out_usdz

    print(f"[fix] 누락 {len(missing)}개 추가:")
    for arc in missing:
        print(f"   + {arc}")

    # --- 전체 재작성하며 누락분 추가 ---
    tmp = out_usdz + ".tmp.usdz"
    with Usd.ZipFileWriter.CreateNew(tmp) as zfw:
        with zipfile.ZipFile(out_usdz) as z:
            for name in z.namelist():
                _add_bytes(zfw, name, z.read(name))
        for arc, src in missing.items():
            zfw.AddFile(src, arc)
    os.replace(tmp, out_usdz)

    # --- 검증 ---
    with zipfile.ZipFile(out_usdz) as z:
        final = set(z.namelist())
    still = [arc for arc in expected if arc not in final]
    print(f"[done] 최종 {len(final)}개. 여전히 누락: {still or '없음'}")
    return out_usdz


def _add_bytes(zfw, arcname, data):
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        tmp_path = f.name
    try:
        zfw.AddFile(tmp_path, arcname)
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    package_usdz_with_parts("main.usd", "out.usdz")
