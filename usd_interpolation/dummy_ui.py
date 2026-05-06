import numpy as np
from collections import Counter

from pxr import Usd, UsdGeom, Vt, Sdf
import omni.kit.app
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

    # lerp 결과 사전 계산
    writes = []
    for prim_path, st_a in map_a.items():
        st_b = map_b.get(prim_path)
        if st_b is None or len(st_a) != len(st_b):
            continue
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            continue
        st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st_pv or not st_pv.GetAttr().IsValid():
            continue
        a_np = np.array(st_a, dtype=np.float32)
        b_np = np.array(st_b, dtype=np.float32)
        writes.append((st_pv, Vt.Vec2fArray.FromNumpy(a_np + t * (b_np - a_np))))

    # 모든 메시 write를 하나의 change block으로 묶어 뷰포트 갱신을 1회로 제한
    with Sdf.ChangeBlock():
        for st_pv, result in writes:
            st_pv.Set(result)

    print(f"[usd_interpolation] Applied lerp t={t:.2f} to {len(writes)} mesh(es)")
    return len(writes) > 0


NUM_FILES = 5


class UsdInterpolationUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._status_label: ui.Label | None = None
        self._slider: ui.FloatSlider | None = None
        self._t_label: ui.Label | None = None

        self._fields: list[ui.StringField] = []
        self._maps: list[dict | None] = [None] * NUM_FILES

        self._pending_t: float = 0.0
        self._dirty: bool = False
        self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._on_update, name="usd_interpolation_update"
        )

    def build_ui(self):
        self._window = ui.Window("USD UV Interpolator", width=500, height=60 * NUM_FILES + 100)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                for i in range(NUM_FILES):
                    with ui.HStack(height=24, spacing=4):
                        ui.Label(f"File {i}:", width=50)
                        field = ui.StringField()
                        field.model.set_value(f"/path/to/file{i}.usd")
                        self._fields.append(field)
                        idx = i  # capture
                        ui.Button("Load", width=50,
                                  clicked_fn=lambda _idx=idx: self._on_load(_idx))

                self._status_label = ui.Label("Status: Not loaded", height=20)

                with ui.HStack(height=24, spacing=8):
                    self._t_label = ui.Label("t: 0.00", width=60)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.005)
                    self._slider.enabled = False
                    self._slider.model.add_value_changed_fn(self._on_slider_changed)

    def _on_load(self, idx: int):
        path = self._fields[idx].model.get_value_as_string().strip()
        if idx == 0:
            omni.usd.get_context().open_stage(path)
        st_map = load_st_map(path)
        if st_map is None:
            self._set_status(f"ERROR: Failed to load File {idx}")
            return
        self._maps[idx] = st_map
        loaded = [i for i, m in enumerate(self._maps) if m is not None]
        self._set_status(f"File {idx} loaded ({len(st_map)} mesh(es))  |  Loaded: {loaded}")
        self._try_enable_slider()

    def _try_enable_slider(self):
        # 연속된 두 파일이 하나라도 있으면 활성화
        for i in range(NUM_FILES - 1):
            if self._maps[i] is not None and self._maps[i + 1] is not None:
                self._slider.enabled = True
                return
        self._slider.enabled = False

    def _on_slider_changed(self, model):
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        # 값만 저장, 실제 write는 다음 프레임에 한 번만
        self._pending_t = t
        self._dirty = True

    def _on_update(self, _event):
        if not self._dirty:
            return
        self._dirty = False
        t = self._pending_t

        seg = min(int(t * (NUM_FILES - 1)), NUM_FILES - 2)
        local_t = t * (NUM_FILES - 1) - seg

        print(f"[usd_interpolation] t={t:.4f} | seg={seg}→{seg+1} | w[{seg}]={1-local_t:.4f} w[{seg+1}]={local_t:.4f}")

        map_a = self._maps[seg]
        map_b = self._maps[seg + 1]

        if map_a is None or map_b is None:
            self._set_status(f"Segment {seg}→{seg+1} not loaded yet")
            return

        ok = apply_lerped_st_all(map_a, map_b, local_t)
        if not ok:
            self._set_status("ERROR: Failed to apply. Check console.")

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        self._update_sub = None
        if self._window:
            self._window.destroy()
            self._window = None

