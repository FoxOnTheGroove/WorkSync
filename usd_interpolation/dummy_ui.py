from collections import Counter

from pxr import Usd, UsdGeom
import omni.ui as ui


def _get_attr(attr) -> object:
    """Get attribute value, falling back to first time sample if default is None."""
    val = attr.Get(Usd.TimeCode.Default())
    if val is None:
        samples = attr.GetTimeSamples()
        if samples:
            val = attr.Get(samples[0])
    return val


def _check(label: str, result: bool) -> bool:
    status = "OK" if result else "FAIL"
    print(f"[usd_interpolation]   [{status}] {label}")
    return result


def get_mesh_st_primvar(usd_file_path: str) -> dict | None:
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        print(f"[usd_interpolation] ERROR: Failed to open stage: {usd_file_path}")
        return None

    mesh_prim = None
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh_prim = prim
            break

    if mesh_prim is None:
        print(f"[usd_interpolation] ERROR: No Mesh prim found in {usd_file_path}")
        return None

    mesh = UsdGeom.Mesh(mesh_prim)
    primvars_api = UsdGeom.PrimvarsAPI(mesh_prim)

    fvc    = _get_attr(mesh.GetFaceVertexCountsAttr())
    fvi    = _get_attr(mesh.GetFaceVertexIndicesAttr())
    points = _get_attr(mesh.GetPointsAttr())
    st_pv  = primvars_api.GetPrimvar("st")

    if not st_pv or not st_pv.GetAttr().IsValid():
        print("[usd_interpolation] ERROR: 'st' primvar not found")
        return None

    st_raw = _get_attr(st_pv.GetAttr())
    if st_raw is None:
        print("[usd_interpolation] ERROR: st.Get() returned None")
        return None

    fvc_len = len(fvc)    if fvc    is not None else None
    fvi_len = len(fvi)    if fvi    is not None else None
    pt_len  = len(points) if points is not None else None
    st_len  = len(st_raw)
    fvc_sum = int(sum(fvc)) if fvc is not None else None
    interp  = st_pv.GetInterpolation()

    print(f"[usd_interpolation] ── Raw counts ──────────────────────")
    print(f"[usd_interpolation]   faces            (fvc len) : {fvc_len}")
    print(f"[usd_interpolation]   sum(fvc)                   : {fvc_sum}")
    print(f"[usd_interpolation]   faceVertexIndices (fvi len): {fvi_len}")
    print(f"[usd_interpolation]   points (vertices)          : {pt_len}")
    print(f"[usd_interpolation]   st values                  : {st_len}")
    print(f"[usd_interpolation]   st interpolation           : {interp}")
    print(f"[usd_interpolation]   fvc sample [:8]            : {list(fvc[:8]) if fvc is not None else None}")
    print(f"[usd_interpolation] ── Cross-validation ────────────────")

    checks = {}
    checks["sum(fvc) == fvi_len"] = _check(
        f"sum(fvc) {fvc_sum} == fvi_len {fvi_len}",
        fvc_sum is not None and fvi_len is not None and fvc_sum == fvi_len,
    )
    checks["all fvc >= 3"] = _check(
        "all faceVertexCounts >= 3 (valid polygons)",
        fvc is not None and all(c >= 3 for c in fvc),
    )
    checks["max(fvi) < pt_len"] = _check(
        f"max(fvi) {max(fvi) if fvi is not None else '?'} < pt_len {pt_len}",
        fvi is not None and pt_len is not None and int(max(fvi)) < pt_len,
    )
    if interp == UsdGeom.Tokens.faceVarying:
        checks["st_len == sum(fvc)"] = _check(
            f"st_len {st_len} == sum(fvc) {fvc_sum}",
            fvc_sum is not None and st_len == fvc_sum,
        )
    elif interp == UsdGeom.Tokens.vertex:
        checks["st_len == pt_len"] = _check(
            f"st_len {st_len} == pt_len {pt_len}",
            pt_len is not None and st_len == pt_len,
        )

    all_ok = all(checks.values())
    print(f"[usd_interpolation] ── Result: {'ALL OK' if all_ok else 'HAS FAILURES'} ──")

    # 유효 범위: faceVarying은 sum(fvc), vertex는 pt_len, 나머지는 st_len 그대로
    if interp == UsdGeom.Tokens.faceVarying:
        valid_count = fvc_sum if fvc_sum is not None else st_len
    elif interp == UsdGeom.Tokens.vertex:
        valid_count = pt_len if pt_len is not None else st_len
    else:
        valid_count = st_len

    valid_count = min(st_len, valid_count)
    counter = Counter(tuple(v) for v in st_raw[:valid_count])

    return {
        "prim_path":   str(mesh_prim.GetPath()),
        "interpolation": interp,
        "st_count":    st_len,
        "fvc_sum":     fvc_sum,
        "fvi_len":     fvi_len,
        "pt_len":      pt_len,
        "valid_count": valid_count,
        "all_ok":      all_ok,
        "checks":      checks,
        "counter":     counter,
    }


class UsdInterpolationUI:
    """
    로컬 USD 파일에서 primvars:st UV를 조회하는 더미 UI.
    - 파일 경로 입력 필드
    - Extract 버튼
    - 결과 출력 영역
    """

    def __init__(self):
        self._window: ui.Window | None = None
        self._result_label: ui.Label | None = None
        self._path_field: ui.StringField | None = None

    def build_ui(self):
        self._window = ui.Window("USD UV Extractor", width=480, height=320)
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 8}):
                ui.Label("USD File Path:", height=20)
                self._path_field = ui.StringField(height=24)
                self._path_field.model.set_value("/path/to/model.usd")

                ui.Button("Extract primvars:st", height=32, clicked_fn=self._on_extract)

                ui.Separator()
                ui.Label("Result:", height=20)
                self._result_label = ui.Label(
                    "Not extracted yet.",
                    word_wrap=True,
                    height=0,
                )

    def _on_extract(self):
        file_path = self._path_field.model.get_value_as_string().strip()
        if not file_path:
            self._set_result("[Error] Please enter a USD file path.")
            return

        data = get_mesh_st_primvar(file_path)
        if data is None:
            self._set_result(f"[Error] primvars:st not found in '{file_path}'. Check the console log for details.")
            return

        counter = data["counter"]
        unique  = len(counter)
        check_lines = "\n".join(
            f"  [{'OK' if v else 'FAIL'}] {k}" for k, v in data["checks"].items()
        )
        freq_lines = "\n".join(f"  {uv}: {cnt}" for uv, cnt in counter.items())
        text = (
            f"Mesh: {data['prim_path']}  |  Interp: {data['interpolation']}\n"
            f"ST: {data['st_count']}  fvc_sum: {data['fvc_sum']}  "
            f"fvi: {data['fvi_len']}  pts: {data['pt_len']}\n"
            f"\n[Validation {'ALL OK' if data['all_ok'] else 'HAS FAILURES'}]\n"
            f"{check_lines}\n"
            f"\n{unique} unique value(s) in {data['valid_count']} valid entries\n"
            f"\n{freq_lines}"
        )
        self._set_result(text)
        print(f"[usd_interpolation] all_ok={data['all_ok']}, unique={unique}, valid={data['valid_count']}")

    def _set_result(self, text: str):
        if self._result_label:
            self._result_label.text = text

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
