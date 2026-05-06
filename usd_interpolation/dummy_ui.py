from collections import Counter

from pxr import Usd, UsdGeom
import omni.ui as ui


def _get_attr(attr) -> object:
    val = attr.Get(Usd.TimeCode.Default())
    if val is None:
        samples = attr.GetTimeSamples()
        if samples:
            val = attr.Get(samples[0])
    return val


def _check(label: str, result: bool) -> bool:
    print(f"[usd_interpolation]   [{'OK' if result else 'FAIL'}] {label}")
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
        print("[usd_interpolation] ERROR: No Mesh prim found")
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

    print(f"[usd_interpolation] faces={fvc_len} sum(fvc)={fvc_sum} fvi={fvi_len} pts={pt_len} st={st_len} interp={interp}")
    print(f"[usd_interpolation] fvc[:8]={list(fvc[:8]) if fvc is not None else None}")

    checks = {}
    checks["sum(fvc) == fvi_len"] = _check(
        f"sum(fvc) {fvc_sum} == fvi_len {fvi_len}",
        fvc_sum is not None and fvi_len is not None and fvc_sum == fvi_len,
    )
    checks["all fvc >= 3"] = _check(
        "all faceVertexCounts >= 3",
        fvc is not None and all(c >= 3 for c in fvc),
    )
    checks["max(fvi) < pt_len"] = _check(
        f"max(fvi) {int(max(fvi)) if fvi is not None else '?'} < pt_len {pt_len}",
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
    print(f"[usd_interpolation] validation: {'ALL OK' if all_ok else 'HAS FAILURES'}")

    if interp == UsdGeom.Tokens.faceVarying:
        valid_count = min(st_len, fvc_sum) if fvc_sum is not None else st_len
    elif interp == UsdGeom.Tokens.vertex:
        valid_count = min(st_len, pt_len) if pt_len is not None else st_len
    else:
        valid_count = st_len

    # u값(첫 번째 컴포넌트)을 0~255 인덱스로 매핑 — VtArray 한 번만 순회
    index_counter = Counter(max(0, min(255, int(v[0] * 256))) for v in st_raw[:valid_count])

    return {
        "prim_path":     str(mesh_prim.GetPath()),
        "interpolation": interp,
        "st_count":      st_len,
        "valid_count":   valid_count,
        "all_ok":        all_ok,
        "checks":        checks,
        "index_counter": index_counter,
    }


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._result_label: ui.Label | None = None
        self._path_field: ui.StringField | None = None
        self._last_file: str = ""
        self._last_text: str = ""

    def build_ui(self):
        self._window = ui.Window("USD UV Extractor", width=480, height=360)
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 8}):
                ui.Label("USD File Path:", height=20)
                self._path_field = ui.StringField(height=24)
                self._path_field.model.set_value("/path/to/model.usd")
                ui.Button("Extract primvars:st", height=32, clicked_fn=self._on_extract)
                ui.Separator()
                ui.Label("Result:", height=20)
                with ui.ScrollingFrame(height=260):
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

        # 같은 파일이면 재계산 없이 캐시 표시
        if file_path == self._last_file and self._last_text:
            self._set_result(self._last_text)
            return

        data = get_mesh_st_primvar(file_path)
        if data is None:
            self._set_result("[Error] primvars:st not found. Check console log.")
            return

        ic = data["index_counter"]
        unique_idx = len(ic)
        valid = data["valid_count"]

        idx_lines = "\n".join(f"  [{i:3d}]: {c}" for i, c in sorted(ic.items()))

        text = (
            f"Mesh: {data['prim_path']}\n"
            f"Interp: {data['interpolation']}  |  ST: {data['st_count']}  |  Valid: {valid}\n"
            f"\n{valid} values  →  {unique_idx} unique index(es) (256-pixel mapping)\n"
            f"\n{idx_lines}"
        )

        self._last_file = file_path
        self._last_text = text
        self._set_result(text)
        print(f"[usd_interpolation] unique_indices={unique_idx}, valid={valid}, all_ok={data['all_ok']}")

    def _set_result(self, text: str):
        if self._result_label:
            self._result_label.text = text

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
