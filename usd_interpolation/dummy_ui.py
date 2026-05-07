import asyncio
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
            result[str(prim.GetPath())] = np.array(st_raw, dtype=np.float32).reshape(-1, 2)
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
        if st_b is None:
            print(f"[usd_interpolation] SKIP {prim_path}: not in map_b")
            continue
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            continue
        st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st_pv or not st_pv.GetAttr().IsValid():
            continue
        if len(st_a) != len(st_b):
            print(f"[usd_interpolation] SNAP {prim_path}: len_a={len(st_a)} len_b={len(st_b)} t={t:.3f}")
            chosen = st_a if t < 0.5 else st_b
            writes.append((st_pv, Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(chosen))))
            continue
        t32    = np.float32(t)
        lerped = np.ascontiguousarray(st_a + t32 * (st_b - st_a))
        writes.append((st_pv, Vt.Vec2fArray.FromNumpy(lerped)))

    session_layer = stage.GetSessionLayer()

    # ChangeBlock A: 빈 배열로 primvar 크기를 0으로 만듦.
    # 0→N 크기 변화는 Hydra가 UV 버퍼를 강제로 재할당하는 구조적 변화로 인식.
    with Usd.EditContext(stage, session_layer):
        with Sdf.ChangeBlock():
            for st_pv, _ in writes:
                st_pv.GetAttr().Set(Vt.Vec2fArray())

    # ChangeBlock B: 실제 lerped 값 적용.
    # 두 ChangeBlock 사이에 await 없으므로 렌더는 B 이후 한 번만 발생 (flicker 없음).
    with Usd.EditContext(stage, session_layer):
        with Sdf.ChangeBlock():
            for st_pv, result in writes:
                st_pv.GetAttr().Set(result)

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
        self._pending_write: bool = False
        self._play_task: asyncio.Task | None = None
        self._btn_play: ui.Button | None = None
        self._btn_reverse: ui.Button | None = None

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

                with ui.HStack(height=24, spacing=8):
                    self._btn_play = ui.Button("Play ▶", width=80,
                                               clicked_fn=self._on_play_clicked)
                    self._btn_reverse = ui.Button("Reverse ◀", width=90,
                                                  clicked_fn=self._on_reverse_clicked)
                    ui.Button("Refresh", width=70,
                              clicked_fn=self._on_refresh_clicked)

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

    def _on_refresh_clicked(self):
        self._refresh(self._pending_t)

    def _on_play_clicked(self):
        if self._play_task and not self._play_task.done():
            self._stop_play()
        else:
            self._play_task = asyncio.ensure_future(self._animate(forward=True))

    def _on_reverse_clicked(self):
        if self._play_task and not self._play_task.done():
            self._stop_play()
        else:
            self._play_task = asyncio.ensure_future(self._animate(forward=False))

    def _stop_play(self):
        if self._play_task:
            self._play_task.cancel()
            self._play_task = None
        if self._btn_play:
            self._btn_play.text = "Play ▶"
        if self._btn_reverse:
            self._btn_reverse.text = "Reverse ◀"

    async def _animate(self, forward: bool):
        DURATION = 2.5
        if self._btn_play:
            self._btn_play.text = "Stop ■" if forward else "Play ▶"
        if self._btn_reverse:
            self._btn_reverse.text = "Reverse ◀" if forward else "Stop ■"

        start_t = self._pending_t
        target = 1.0 if forward else 0.0
        travel = abs(target - start_t)
        elapsed = 0.0
        dt_scale = travel / DURATION if travel > 0.0 else 0.0

        try:
            while True:
                await omni.kit.app.get_app().next_update_async()
                elapsed += 1.0 / 60.0
                frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                new_t = start_t + (frac if forward else -frac)
                new_t = max(0.0, min(1.0, new_t))
                self._slider.model.set_value(new_t)
                if new_t == target or (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                    break
        except asyncio.CancelledError:
            return
        finally:
            self._stop_play()

    def _on_slider_changed(self, model):
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        self._pending_t = t
        if not self._pending_write:
            self._pending_write = True
            asyncio.ensure_future(self._write_next_frame())

    async def _write_next_frame(self):
        await omni.kit.app.get_app().next_update_async()
        self._pending_write = False
        ok = self._refresh(self._pending_t)
        if not ok:
            self._set_status("ERROR: Failed to apply. Check console.")

    def _refresh(self, t: float) -> bool:
        """주어진 t에 대한 UV를 계산해 스테이지에 적용한다."""
        raw = t * (NUM_FILES - 1)
        seg = min(int(raw), NUM_FILES - 2)
        local_t = min(raw - seg, 1.0)
        print(f"[usd_interpolation] t={t:.4f} | seg={seg}→{seg+1} | w[{seg}]={1-local_t:.4f} w[{seg+1}]={local_t:.4f}")
        map_a = self._maps[seg]
        map_b = self._maps[seg + 1]
        if map_a is None or map_b is None:
            self._set_status(f"Segment {seg}→{seg+1} not loaded yet")
            return False
        return apply_lerped_st_all(map_a, map_b, local_t)

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        self._stop_play()
        if self._window:
            self._window.destroy()
            self._window = None

