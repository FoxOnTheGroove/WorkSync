import os
import zipfile
from pxr import UsdUtils


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
    with zipfile.ZipFile(out_usdz) as zin, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zout:
        for name in zin.namelist():
            _writestr_aligned(zout, name, zin.read(name))
        for arc, src in missing.items():
            with open(src, "rb") as f:
                _writestr_aligned(zout, arc, f.read())
    os.replace(tmp, out_usdz)

    # --- 검증 ---
    with zipfile.ZipFile(out_usdz) as z:
        final = set(z.namelist())
    still = [arc for arc in expected if arc not in final]
    print(f"[done] 최종 {len(final)}개. 여전히 누락: {still or '없음'}")
    return out_usdz


def _writestr_aligned(zf, arcname, data, alignment=64):
    """
    USDZ 규격에 맞게 무압축(ZIP_STORED) + 데이터 시작 오프셋 64바이트 정렬로 기록.
    Usd.ZipFileWriter가 없는 USD 빌드에서도 동작하도록 표준 zipfile만 사용.
    """
    zi = zipfile.ZipInfo(arcname)
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = 0o644 << 16
    offset = zf.fp.tell()
    data_start = offset + 30 + len(arcname.encode("utf-8"))
    pad = (alignment - data_start % alignment) % alignment
    if 0 < pad < 4:  # extra field는 최소 4바이트(TLV 헤더) 필요
        pad += alignment
    if pad:
        zi.extra = b"\x86\x19" + (pad - 4).to_bytes(2, "little") + b"\x00" * (pad - 4)
    zf.writestr(zi, data)


if __name__ == "__main__":
    package_usdz_with_parts("main.usd", "out.usdz")
