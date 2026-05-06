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


def load_st_map(usd_file_path: str) -> dict[str, Vt.Vec2fArray] | None:
    """파일 내 모든 Mesh의 {prim_path: st VtArray} 반환."""
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        print(f"[usd_interpolation] ERROR: Failed to open: {usd_file_path}")
        return None

    result = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st_pv or not st_pv.GetAttr().IsValid():
            continue
        st_raw = _get_attr(st_pv.GetAttr())
        if st_raw is not None:
            result[str(prim.GetPath())] = st_raw
            print(f"[usd_interpolation] Loaded st from {prim.GetPath()}, count={len(st_raw)}")

    if not result:
        print(f"[usd_interpolation] ERROR: No mesh with st found in {usd_file_path}")
        return None

    print(f"[usd_interpolation] Loaded {len(result)} mesh(es) from {usd_file_path}")
    return result


def apply_lerped_st_all(map_a: dict, map_b: dict, t: float) -> bool:
    """공통 prim_path에 대해 st를 보간해 에디터 스테이지 전체에 적용."""
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print("[usd_interpolation] ERROR: No editor stage found")
        return False

    ok_count = 0
    for prim_path, st_a in map_a.items():
        st_b = map_b.get(prim_path)
        if st_b is None:
            print(f"[usd_interpolation] SKIP {prim_path}: not found in File B")
            continue
        if len(st_a) != len(st_b):
            print(f"[usd_interpolation] SKIP {prim_path}: length mismatch {len(st_a)} vs {len(st_b)}")
            continue

        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            print(f"[usd_interpolation] SKIP {prim_path}: not found in editor stage")
            continue

        st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st_pv or not st_pv.GetAttr().IsValid():
            print(f"[usd_interpolation] SKIP {prim_path}: no st primvar in stage")
            continue

        a_np = np.array(st_a, dtype=np.float32)
        b_np = np.array(st_b, dtype=np.float32)
        st_pv.Set(Vt.Vec2fArray.FromNumpy(a_np + t * (b_np - a_np)))
        ok_count += 1

    print(f"[usd_interpolation] Applied lerp t={t:.2f} to {ok_count} mesh(es)")
    return ok_count > 0


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._field_a: ui.StringField | None = None
        self._field_b: ui.StringField | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None

        self._map_a: dict | None = None
        self._map_b: dict | None = None

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
        omni.usd.get_context().open_stage(path)
        self._map_a = load_st_map(path)
        if self._map_a is None:
            self._set_status("ERROR: Failed to load File A")
            return
        self._set_status(f"A loaded into stage: {len(self._map_a)} mesh(es)")
        self._try_enable_slider()

    def _on_load_b(self):
        path = self._field_b.model.get_value_as_string().strip()
        self._map_b = load_st_map(path)
        if self._map_b is None:
            self._set_status("ERROR: Failed to load File B")
            return
        self._set_status(f"B loaded: {len(self._map_b)} mesh(es)")
        self._try_enable_slider()

    def _try_enable_slider(self):
        if self._map_a is None or self._map_b is None:
            return
        common = set(self._map_a) & set(self._map_b)
        if not common:
            self._set_status("ERROR: No matching prim paths between A and B")
            self._slider.enabled = False
            return
        self._set_status(f"Ready — {len(common)} mesh(es) in common")
        self._slider.enabled = True

    def _on_slider_changed(self, model):
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.2f}"
        if self._map_a is None or self._map_b is None:
            return
        ok = apply_lerped_st_all(self._map_a, self._map_b, t)
        if not ok:
            self._set_status("ERROR: Failed to apply to editor stage. Check console.")

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
