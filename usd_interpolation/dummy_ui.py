import numpy as np
from collections import Counter

from pxr import Usd, UsdGeom, Vt
import omni.usd
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
        "prim_path":      str(mesh_prim.GetPath()),
        "interpolation":  interp,
        "valid_count":    valid_count,
        "all_ok":         all(checks.values()),
        "unique_values":  len(unique_set),
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


def load_st_array(usd_file_path: str) -> tuple[str, Vt.Vec2fArray] | None:
    """파일에서 첫 번째 Mesh의 prim_path와 st VtArray를 반환."""
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        print(f"[usd_interpolation] ERROR: Failed to open: {usd_file_path}")
        return None

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st_pv or not st_pv.GetAttr().IsValid():
            continue
        st_raw = _get_attr(st_pv.GetAttr())
        if st_raw is not None:
            print(f"[usd_interpolation] Loaded st from {prim.GetPath()}, count={len(st_raw)}")
            return str(prim.GetPath()), st_raw

    print(f"[usd_interpolation] ERROR: No mesh with st found in {usd_file_path}")
    return None


def apply_lerped_st(st_a: Vt.Vec2fArray, st_b: Vt.Vec2fArray, t: float, prim_path: str) -> bool:
    """numpy로 st를 보간해 에디터 스테이지의 메시 primvar에 적용."""
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print("[usd_interpolation] ERROR: No editor stage found")
        return False

    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        print(f"[usd_interpolation] ERROR: Prim not found in editor stage: {prim_path}")
        return False

    a_np = np.array(st_a, dtype=np.float32)  # (N, 2)
    b_np = np.array(st_b, dtype=np.float32)
    lerped = a_np + t * (b_np - a_np)

    result = Vt.Vec2fArray.FromNumpy(lerped)
    st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
    if not st_pv or not st_pv.GetAttr().IsValid():
        print(f"[usd_interpolation] ERROR: No st primvar on {prim_path}")
        return False

    st_pv.Set(result)
    return True


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._field_a: ui.StringField | None = None
        self._field_b: ui.StringField | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None

        self._st_a: Vt.Vec2fArray | None = None
        self._st_b: Vt.Vec2fArray | None = None
        self._prim_path: str | None = None

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=480, height=240)
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 8}):
                with ui.HStack(height=24, spacing=4):
                    ui.Label("File A:", width=50)
                    self._field_a = ui.StringField()
                    self._field_a.model.set_value("/path/to/a.usd")
                    ui.Button("Load A", width=60, clicked_fn=self._on_load_a)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("File B:", width=50)
                    self._field_b = ui.StringField()
                    self._field_b.model.set_value("/path/to/b.usd")
                    ui.Button("Load B", width=60, clicked_fn=self._on_load_b)

                self._status_label = ui.Label("Status: Not loaded", height=20)

                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.00", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.01)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

    def _on_load_a(self):
        path = self._field_a.model.get_value_as_string().strip()
        # 에디터 스테이지에 File A를 직접 오픈
        omni.usd.get_context().open_stage(path)
        result = load_st_array(path)
        if result is None:
            self._set_status("ERROR: Failed to load File A")
            return
        self._prim_path, self._st_a = result
        self._set_status(f"A loaded into stage: {len(self._st_a)} values  |  prim: {self._prim_path}")
        self._try_enable_slider()

    def _on_load_b(self):
        path = self._field_b.model.get_value_as_string().strip()
        result = load_st_array(path)
        if result is None:
            self._set_status("ERROR: Failed to load File B")
            return
        _, self._st_b = result
        self._set_status(f"B loaded: {len(self._st_b)} values")
        self._try_enable_slider()

    def _try_enable_slider(self):
        if self._st_a is None or self._st_b is None:
            return
        if len(self._st_a) != len(self._st_b):
            self._set_status(f"ERROR: Length mismatch — A={len(self._st_a)}, B={len(self._st_b)}")
            self._slider.enabled = False
            return
        self._set_status(f"Ready — {len(self._st_a)} values  |  prim: {self._prim_path}")
        self._slider.enabled = True

    def _on_slider_changed(self, model):
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.2f}"
        if self._st_a is None or self._st_b is None or self._prim_path is None:
            return
        ok = apply_lerped_st(self._st_a, self._st_b, t, self._prim_path)
        if not ok:
            self._set_status("ERROR: Failed to apply to editor stage. Check console.")

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
