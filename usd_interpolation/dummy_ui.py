import asyncio
import numpy as np

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


def load_st_map(usd_file_path: str) -> dict[str, np.ndarray] | None:
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


def apply_lerped_st_all(map_a: dict, map_b: dict, t: float) -> list:
    # [분석] GetAttr().Set() vs st_pv.Set():
    # 인덱스드 primvar에서 compact 배열 크기가 바뀌면 indices가 out-of-range가 될 수 있으나,
    # 우리는 len(st_a)==len(st_b) 체크 후에만 lerp하므로 크기 변경은 없다.
    # changedInfoOnly 문제(Hydra GPU 버퍼 미갱신)는 어느 쪽 API를 써도 동일하게 발생.
    #
    # [분석] 메시 지오메트리 영향 여부:
    # primvars:st 쓰기는 UV 좌표만 바꾼다. points/faceVertexCounts/faceVertexIndices는
    # 별개 attribute이므로 st Set() 호출만으로는 절대 영향받지 않는다.
    # 깨져 보이는 현상은 지오메트리 손상이 아니라 Hydra GPU UV 버퍼가 stale 상태인 것.
    #
    # [분석] [u, 0.5] 구조:
    # st_a[i]=(u_i,0.5), st_b[i]=(u_i',0.5) 이면
    # lerped[i]=(u_i+t*(u_i'-u_i), 0.5+t*0.0)=(lerped_u, 0.5)
    # V=0.5는 수학적으로 보존. 단, st_b의 V가 0.5가 아니면 drift 발생.

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print("[usd_interpolation] ERROR: No editor stage found")
        return []

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
            writes.append((st_pv, prim, Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(chosen))))
            continue
        t32    = np.float32(t)
        lerped = np.ascontiguousarray(st_a + t32 * (st_b - st_a))
        writes.append((st_pv, prim, Vt.Vec2fArray.FromNumpy(lerped)))

    if not writes:
        return []

    session_layer = stage.GetSessionLayer()
    with Usd.EditContext(stage, session_layer):
        with Sdf.ChangeBlock():
            for st_pv, _, result in writes:
                st_pv.GetAttr().Set(result)

    written_prims = [p for _, p, _ in writes]
    print(f"[usd_interpolation] Applied lerp t={t:.2f} to {len(writes)} mesh(es)")
    return written_prims


class _ResyncScheduler:
    """Kit update event stream으로 SetActive(F→T)를 3개의 별개 tick에서 실행.

    Phase 0: MakeInvisible만 (SetActive 없음) — Hydra가 mesh를 invisible로 렌더
    Phase 1: SetActive(False) — 이미 invisible이라 blank frame 없음
    Phase 2: SetActive(True) + MakeVisible — Hydra UV 버퍼 갱신 후 mesh 재표시
    """

    def __init__(self, stage, prims: list):
        self._stage = stage
        self._prims = [p for p in prims if p.IsValid()]
        self._invisible_prims: list = []
        self._deactivated: list = []
        self._phase = 0
        self._cancelled: bool = False
        self._sub = omni.kit.app.get_app().get_update_event_stream() \
            .create_subscription_to_pop(self._on_tick, name="usd_interp_resync")

    def cancel(self):
        self._cancelled = True
        self._sub = None
        session = self._stage.GetSessionLayer()
        with Usd.EditContext(self._stage, session):
            with Sdf.ChangeBlock():
                for p in self._deactivated:
                    if p.IsValid():
                        p.SetActive(True)
                        UsdGeom.Imageable(p).MakeVisible()
                for p in self._invisible_prims:
                    if p.IsValid():
                        UsdGeom.Imageable(p).MakeVisible()
        self._deactivated = []
        self._invisible_prims = []

    def _on_tick(self, _event):
        if self._cancelled:
            return
        session = self._stage.GetSessionLayer()
        if self._phase == 0:
            with Usd.EditContext(self._stage, session):
                with Sdf.ChangeBlock():
                    for p in self._prims:
                        if p.IsValid():
                            UsdGeom.Imageable(p).MakeInvisible()
                            self._invisible_prims.append(p)
            self._phase = 1
        elif self._phase == 1:
            with Usd.EditContext(self._stage, session):
                with Sdf.ChangeBlock():
                    for p in self._invisible_prims:
                        if p.IsValid():
                            p.SetActive(False)
                            self._deactivated.append(p)
            self._invisible_prims = []
            self._phase = 2
        else:
            with Usd.EditContext(self._stage, session):
                with Sdf.ChangeBlock():
                    for p in self._deactivated:
                        if p.IsValid():
                            p.SetActive(True)
                            UsdGeom.Imageable(p).MakeVisible()
            self._deactivated = []
            self._sub = None


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
        self._resync: _ResyncScheduler | None = None
        self._is_animating: bool = False
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
                        idx = i
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
                    self._btn_reverse = ui.Button("Reverse ◄", width=90,
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
        for i in range(NUM_FILES - 1):
            if self._maps[i] is not None and self._maps[i + 1] is not None:
                self._slider.enabled = True
                return
        self._slider.enabled = False

    def _start_resync(self, prims: list):
        if self._resync:
            self._resync.cancel()
        stage = omni.usd.get_context().get_stage()
        if stage and prims:
            self._resync = _ResyncScheduler(stage, prims)

    def _on_refresh_clicked(self):
        written = self._refresh(self._pending_t)
        if written:
            self._start_resync(written)

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
            self._btn_reverse.text = "Reverse ◄"

    async def _animate(self, forward: bool):
        DURATION = 2.5
        if self._btn_play:
            self._btn_play.text = "Stop ■" if forward else "Play ▶"
        if self._btn_reverse:
            self._btn_reverse.text = "Reverse ◄" if forward else "Stop ■"

        start_t = self._pending_t
        target = 1.0 if forward else 0.0
        travel = abs(target - start_t)
        elapsed = 0.0
        dt_scale = travel / DURATION if travel > 0.0 else 0.0

        self._is_animating = True  # _on_slider_changed 에서 resync 스킵하도록
        try:
            while True:
                await omni.kit.app.get_app().next_update_async()
                elapsed += 1.0 / 60.0
                frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                new_t = start_t + (frac if forward else -frac)
                new_t = max(0.0, min(1.0, new_t))

                # set_value → _on_slider_changed 호출되지만 _is_animating=True 이므로 resync 생략
                self._slider.model.set_value(new_t)

                if new_t == target or (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                    break
        except asyncio.CancelledError:
            return
        finally:
            self._is_animating = False
            self._stop_play()

    def _on_slider_changed(self, model):
        t = model.get_value_as_float()
        if self._t_label:
            self._t_label.text = f"t: {t:.3f}"
        self._pending_t = t
        written = self._refresh(t)
        if written and not self._is_animating:
            self._start_resync(written)

    def _refresh(self, t: float) -> list:
        raw = t * (NUM_FILES - 1)
        seg = min(int(raw), NUM_FILES - 2)
        local_t = min(raw - seg, 1.0)
        print(f"[usd_interpolation] t={t:.4f} | seg={seg}→{seg+1} | local_t={local_t:.4f}")
        map_a = self._maps[seg]
        map_b = self._maps[seg + 1]
        if map_a is None or map_b is None:
            self._set_status(f"Segment {seg}→{seg+1} not loaded yet")
            return []
        return apply_lerped_st_all(map_a, map_b, local_t)

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        self._stop_play()
        if self._resync:
            self._resync.cancel()
            self._resync = None
        if self._window:
            self._window.destroy()
            self._window = None
