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


def _analyze_mesh(mesh_prim) -> dict | None:
    mesh = UsdGeom.Mesh(mesh_prim)
    primvars_api = UsdGeom.PrimvarsAPI(mesh_prim)

    fvc    = _get_attr(mesh.GetFaceVertexCountsAttr())
    fvi    = _get_attr(mesh.GetFaceVertexIndicesAttr())
    points = _get_attr(mesh.GetPointsAttr())
    st_pv  = primvars_api.GetPrimvar("st")

    if not st_pv or not st_pv.GetAttr().IsValid():
        print(f"[usd_interpolation] SKIP {mesh_prim.GetPath()}: no 'st' primvar")
        return None

    st_raw = _get_attr(st_pv.GetAttr())
    if st_raw is None:
        print(f"[usd_interpolation] SKIP {mesh_prim.GetPath()}: st.Get() returned None")
        return None

    fvc_sum = int(sum(fvc)) if fvc is not None else None
    fvi_len = len(fvi)      if fvi is not None else None
    pt_len  = len(points)   if points is not None else None
    st_len  = len(st_raw)
    interp  = st_pv.GetInterpolation()

    print(f"[usd_interpolation] {mesh_prim.GetPath()} faces={len(fvc) if fvc else None} sum(fvc)={fvc_sum} fvi={fvi_len} pts={pt_len} st={st_len} interp={interp}")

    checks = {}
    checks["sum(fvc)==fvi_len"] = _check(f"sum(fvc) {fvc_sum} == fvi_len {fvi_len}", fvc_sum is not None and fvi_len is not None and fvc_sum == fvi_len)
    checks["all fvc>=3"]        = _check("all fvc >= 3", fvc is not None and all(c >= 3 for c in fvc))
    checks["max(fvi)<pt_len"]   = _check(f"max(fvi) {int(max(fvi)) if fvi is not None else '?'} < pt_len {pt_len}", fvi is not None and pt_len is not None and int(max(fvi)) < pt_len)
    if interp == UsdGeom.Tokens.faceVarying:
        checks["st==sum(fvc)"] = _check(f"st {st_len} == sum(fvc) {fvc_sum}", fvc_sum is not None and st_len == fvc_sum)
    elif interp == UsdGeom.Tokens.vertex:
        checks["st==pt_len"]   = _check(f"st {st_len} == pt_len {pt_len}", pt_len is not None and st_len == pt_len)

    if interp == UsdGeom.Tokens.faceVarying:
        valid_count = min(st_len, fvc_sum) if fvc_sum is not None else st_len
    elif interp == UsdGeom.Tokens.vertex:
        valid_count = min(st_len, pt_len) if pt_len is not None else st_len
    else:
        valid_count = st_len

    unique_set = set()
    index_counter: Counter = Counter()
    for v in st_raw[:valid_count]:
        unique_set.add((v[0], v[1]))
        index_counter[max(0, min(255, int(v[0] * 256)))] += 1

    return {
        "prim_path":     str(mesh_prim.GetPath()),
        "interpolation": interp,
        "valid_count":   valid_count,
        "all_ok":        all(checks.values()),
        "unique_values": len(unique_set),
        "unique_indices": len(index_counter),
    }


def get_all_mesh_data(usd_file_path: str) -> list[dict]:
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        print(f"[usd_interpolation] ERROR: Failed to open stage: {usd_file_path}")
        return []

    results = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            data = _analyze_mesh(prim)
            if data is not None:
                results.append(data)

    print(f"[usd_interpolation] Found {len(results)} mesh(es) with st primvar")
    return results


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
                ui.Label("Result:", height=20)
                with ui.ScrollingFrame():
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

        if file_path == self._last_file and self._last_text:
            self._set_result(self._last_text)
            return

        results = get_all_mesh_data(file_path)
        if not results:
            self._set_result("[Error] No mesh with st primvar found. Check console log.")
            return

        blocks = []
        for d in results:
            blocks.append(
                f"Mesh: {d['prim_path']}  [{d['interpolation']}]  {'OK' if d['all_ok'] else 'FAIL'}\n"
                f"  Unique values : {d['unique_values']}\n"
                f"  Unique indices: {d['unique_indices']} (256-pixel)\n"
                f"  Total length  : {d['valid_count']}"
            )
        text = "\n\n".join(blocks)

        self._last_file = file_path
        self._last_text = text
        self._set_result(text)
        print(f"[usd_interpolation] Done. {len(results)} mesh(es) displayed.")

    def _set_result(self, text: str):
        if self._result_label:
            self._result_label.text = text

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
