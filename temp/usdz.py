import os
import re
import json
import zipfile
from pxr import Usd, UsdUtils, Sdf


# USD 식별자 규칙: 영숫자/언더스코어, 숫자로 시작 불가, ':' 로 네임스페이스 구분
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(:[A-Za-z_][A-Za-z0-9_]*)*$")

# JSON 값 -> USD 타입 (단일 값만. bool 은 int 의 하위형이라 먼저 검사해야 함)
def _usd_type_of(v):
    if isinstance(v, bool):
        return Sdf.ValueTypeNames.Bool
    if isinstance(v, (int, float)):
        return Sdf.ValueTypeNames.Double
    if isinstance(v, str):
        return Sdf.ValueTypeNames.String
    return None


def package_usdz_with_parts(root_usd, out_usdz, parts_subdir="parts",
                            table_json="table.json"):
    root_usd = os.path.abspath(root_usd)
    anchor_dir = os.path.dirname(root_usd)
    parts_dir = os.path.join(anchor_dir, parts_subdir)

    # --- table.json 의 key-value 를 최상위 프림에 각인 (원본 USD 는 건드리지 않음) ---
    table = _load_table(os.path.join(anchor_dir, table_json))
    stamped = _stamp_and_export(root_usd, table) if table else None

    src_usd = stamped or root_usd
    try:
        # usdz 내부의 루트 레이어 이름은 원본 파일명으로 고정
        UsdUtils.CreateNewUsdzPackage(src_usd, out_usdz,
                                      os.path.basename(root_usd))
    finally:
        if stamped and os.path.exists(stamped):
            os.remove(stamped)

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


def _load_table(path):
    """table.json 을 dict 로 읽음. 없으면 조용히 None, 못 읽으면 경고 후 None."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] 유효하지 않은 table.json: {e}")
        return None
    if not isinstance(data, dict):
        print("[warn] 유효하지 않은 table.json: 최상위가 객체가 아님")
        return None
    return data


def _stamp_and_export(root_usd, table):
    """최상위 프림에 table 의 key-value 를 각인하고 임시 USD 로 내보냄.

    원본은 Save() 하지 않으므로 디스크 상태 그대로 유지된다.
    임시 파일은 parts/material 상대참조가 깨지지 않게 같은 폴더, 같은 확장자로 만든다.
    실패하면 None 을 돌려 원본으로 패킹하게 한다.
    """
    stage = Usd.Stage.Open(root_usd)
    if not stage:
        print(f"[warn] USD 열기 실패, 각인 건너뜀: {root_usd}")
        return None

    prim = stage.GetDefaultPrim()
    if not prim or not prim.IsValid():
        children = stage.GetPseudoRoot().GetChildren()
        prim = children[0] if children else None
    if not prim:
        print("[warn] 최상위 프림 없음, 각인 건너뜀")
        return None

    n = 0
    for key, value in table.items():
        if value is None:                       # null 은 값 없음으로 보고 스킵
            continue
        if not _IDENT_RE.match(key):
            print(f"[warn] 프로퍼티 이름으로 쓸 수 없는 키, 건너뜀: {key!r}")
            continue
        type_name = _usd_type_of(value)
        if type_name is None:
            print(f"[warn] 지원하지 않는 값 타입, 건너뜀: {key}={value!r}")
            continue
        prim.CreateAttribute(key, type_name, custom=True).Set(value)
        n += 1

    base, ext = os.path.splitext(root_usd)
    stamped = f"{base}.__stamped_{os.getpid()}__{ext}"
    stage.GetRootLayer().Export(stamped)
    print(f"[info] 각인 {n}개 -> {prim.GetPath()}")
    return stamped


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
