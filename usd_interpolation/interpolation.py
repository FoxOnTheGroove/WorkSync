import asyncio
from typing import Callable

import numpy as np
import carb.settings as _carb_settings
from pxr import Usd, UsdGeom, Vt, Sdf
import omni.kit.app
import omni.timeline
import omni.usd


class UVMixer:

    # ── Configuration ──────────────────────────────────────────────────────────
    _num_slots: int = 5
    _tbn_default: int = 0          # 0=auto, 2=gpu
    _tbn_enabled: bool = True
    _play_duration: float = 2.5
    _flip_every_n: int = 10

    # ── State ──────────────────────────────────────────────────────────────────
    _maps: list = [None] * 5
    _t: float = 0.0
    _is_animating: bool = False
    _anim_frame: int = 0
    _play_task: object = None
    _flush_task: object = None
    _subscribers: list = []

    # ── Constants ──────────────────────────────────────────────────────────────
    _TBN_PATH = "/rtx/hydra/TBNFrameMode"
    _TBN_GPU = 2
    _TBN_FORCE = 3

    # ── Public API ─────────────────────────────────────────────────────────────

    @classmethod
    def init(cls, *,
             num_slots: int | None = None,
             tbn_default: int | None = None,
             tbn_enabled: bool | None = None,
             play_duration: float | None = None,
             flip_every_n: int | None = None) -> None:
        if num_slots is not None and num_slots != cls._num_slots:
            cls._maps = [None] * num_slots
            cls._num_slots = num_slots
        if tbn_default is not None:
            cls._tbn_default = tbn_default
        if tbn_enabled is not None:
            cls._tbn_enabled = tbn_enabled
        if play_duration is not None:
            cls._play_duration = play_duration
        if flip_every_n is not None:
            cls._flip_every_n = flip_every_n
        try:
            import omni.kit.viewport.utility
            vp = omni.kit.viewport.utility.get_active_viewport()
            print(f"[UVMixer] viewport methods: {[m for m in dir(vp) if not m.startswith('_')]}")
        except Exception as e:
            print(f"[UVMixer] viewport dir: {e}")

    @classmethod
    def load(cls, path: str, slot: int) -> bool:
        if not (0 <= slot < cls._num_slots):
            print(f"[UVMixer] invalid slot {slot}")
            return False
        st_map = cls._load_st_map(path)
        if st_map is None:
            return False
        cls._maps[slot] = st_map
        print(f"[UVMixer] slot {slot} loaded ({len(st_map)} mesh)")
        cls._bake_timesamples()
        if cls._tbn_enabled and len(cls.get_loaded_slots()) >= 2:
            cls._schedule_trigger()
        return True

    @classmethod
    def unload(cls, slot: int) -> None:
        if 0 <= slot < cls._num_slots:
            cls._maps[slot] = None

    @classmethod
    def get_loaded_slots(cls) -> list[int]:
        return [i for i, m in enumerate(cls._maps) if m is not None]

    @classmethod
    def loads(cls, *paths: str) -> None:
        cls.init(num_slots=len(paths))
        for i, path in enumerate(paths):
            cls.load(path, i)

    @classmethod
    def set_t(cls, t: float) -> None:
        t = max(0.0, min(1.0, t))
        cls._t = t
        loaded_count = len([m for m in cls._maps if m is not None])
        if loaded_count >= 2:
            tc = t * (loaded_count - 1)
            stage = omni.usd.get_context().get_stage()
            tps = stage.GetTimeCodesPerSecond() if stage else 24.0
            omni.timeline.get_timeline_interface().set_current_time(tc / tps)
        cls._schedule_trigger()

    @classmethod
    def get_t(cls) -> float:
        return cls._t

    @classmethod
    def play(cls, forward: bool = True) -> None:
        cls.stop()
        cls._play_task = asyncio.ensure_future(cls._animate(forward))

    @classmethod
    def stop(cls) -> None:
        if cls._play_task and not cls._play_task.done():
            cls._play_task.cancel()
            cls._play_task = None

    @classmethod
    def is_playing(cls) -> bool:
        return cls._play_task is not None and not cls._play_task.done()

    @classmethod
    def subscribe(cls, callback: Callable[[float], None]) -> None:
        if callback not in cls._subscribers:
            cls._subscribers.append(callback)

    @classmethod
    def unsubscribe(cls, callback: Callable[[float], None]) -> None:
        cls._subscribers = [c for c in cls._subscribers if c != callback]

    @classmethod
    def destroy(cls) -> None:
        cls.stop()
        if cls._flush_task and not cls._flush_task.done():
            cls._flush_task.cancel()
            cls._flush_task = None
        cls._subscribers.clear()

    # ── Internal ───────────────────────────────────────────────────────────────

    @classmethod
    def _load_st_map(cls, path: str) -> dict | None:
        stage = Usd.Stage.Open(path)
        if not stage:
            print(f"[UVMixer] failed to open: {path}")
            return None
        result = {}
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
            if not st_pv or not st_pv.GetAttr().IsValid():
                continue
            st_raw = st_pv.ComputeFlattened(Usd.TimeCode.Default())
            if st_raw is None:
                samples = st_pv.GetTimeSamples()
                if samples:
                    st_raw = st_pv.ComputeFlattened(samples[0])
            if st_raw is not None:
                result[str(prim.GetPath())] = np.array(st_raw, dtype=np.float32).reshape(-1, 2)
        if not result:
            print(f"[UVMixer] no mesh with st: {path}")
            return None
        return result

    @classmethod
    def _bake_timesamples(cls) -> None:
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        loaded = [(i, m) for i, m in enumerate(cls._maps) if m is not None]
        if len(loaded) < 2:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for tc, (_, st_map) in enumerate(loaded):
                for prim_path, st_data in st_map.items():
                    prim = stage.GetPrimAtPath(prim_path)
                    if not prim.IsValid():
                        continue
                    st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
                    if not st_pv or not st_pv.GetAttr().IsValid():
                        continue
                    st_pv.GetAttr().Set(Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(st_data)), tc)
                    mesh = UsdGeom.Mesh(prim)
                    for attr in (
                        prim.GetAttribute("points"),
                        prim.GetAttribute("normals"),
                        mesh.GetFaceVertexIndicesAttr(),
                    ):
                        if not attr or not attr.IsValid():
                            continue
                        val = attr.Get(Usd.TimeCode.Default())
                        if val is not None:
                            attr.Set(val, tc)
        print(f"[UVMixer] baked {len(loaded)} timesamples (tc 0..{len(loaded)-1})")

    @classmethod
    def _apply_lerp(cls, t: float) -> int:
        loaded = [m for m in cls._maps if m is not None]
        n = len(loaded)
        if n < 2:
            return 0
        raw = t * (n - 1)
        seg = min(int(raw), n - 2)
        local_t = min(raw - seg, 1.0)
        map_a, map_b = loaded[seg], loaded[seg + 1]

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return 0

        count = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            with Sdf.ChangeBlock():
                for prim_path, st_a in map_a.items():
                    st_b = map_b.get(prim_path)
                    if st_b is None:
                        continue
                    prim = stage.GetPrimAtPath(prim_path)
                    if not prim.IsValid():
                        continue
                    st_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
                    if not st_pv or not st_pv.GetAttr().IsValid():
                        continue
                    if len(st_a) != len(st_b):
                        uv = np.ascontiguousarray(st_a if local_t < 0.5 else st_b)
                    else:
                        uv = np.ascontiguousarray(st_a + np.float32(local_t) * (st_b - st_a))
                    st_pv.GetAttr().Set(Vt.Vec2fArray.FromNumpy(uv))
                    count += 1
        return count

    @classmethod
    def _schedule_trigger(cls) -> None:
        if cls._flush_task and not cls._flush_task.done():
            cls._flush_task.cancel()
        cls._flush_task = asyncio.ensure_future(cls._trigger_rerender())

    @classmethod
    async def _trigger_rerender(cls) -> None:
        if cls._tbn_enabled:
            s = _carb_settings.get_settings()
            s.set(cls._TBN_PATH, cls._TBN_FORCE)
            await omni.kit.app.get_app().next_update_async()
            s.set(cls._TBN_PATH, cls._tbn_default)
        else:
            cls._touch_normals()
            await omni.kit.app.get_app().next_update_async()

    @classmethod
    def _touch_normals(cls) -> None:
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim_paths = set()
        for m in cls._maps:
            if m is not None:
                prim_paths.update(m.keys())
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for prim_path in prim_paths:
                prim = stage.GetPrimAtPath(prim_path)
                if not prim.IsValid():
                    continue
                mesh = UsdGeom.Mesh(prim)
                for attr in (prim.GetAttribute("points"), mesh.GetFaceVertexIndicesAttr()):
                    if not attr or not attr.IsValid():
                        continue
                    val = attr.Get(Usd.TimeCode.Default())
                    if val is None:
                        samples = attr.GetTimeSamples()
                        if samples:
                            val = attr.Get(samples[0])
                    if val is not None:
                        attr.Set(val)
                        print(f"[UVMixer] touched {attr.GetName()}: {prim_path}")

    @classmethod
    def _notify(cls, t: float) -> None:
        for cb in list(cls._subscribers):
            try:
                cb(t)
            except Exception as e:
                print(f"[UVMixer] subscriber error: {e}")

    @classmethod
    async def _animate(cls, forward: bool) -> None:
        start_t = cls._t
        target = 1.0 if forward else 0.0
        travel = abs(target - start_t)
        elapsed = 0.0
        dt_scale = travel / cls._play_duration if travel > 0.0 else 0.0

        cls._anim_frame = 0
        cls._is_animating = True
        if cls._tbn_enabled:
            _carb_settings.get_settings().set(cls._TBN_PATH, cls._TBN_GPU)
        try:
            while True:
                await omni.kit.app.get_app().next_update_async()
                elapsed += 1.0 / 60.0
                frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                new_t = start_t + (frac if forward else -frac)
                new_t = max(0.0, min(1.0, new_t))

                cls._t = new_t
                loaded_count = len([m for m in cls._maps if m is not None])
                if loaded_count >= 2:
                    stage = omni.usd.get_context().get_stage()
                    tps = stage.GetTimeCodesPerSecond() if stage else 24.0
                    omni.timeline.get_timeline_interface().set_current_time(new_t * (loaded_count - 1) / tps)
                cls._notify(new_t)

                if cls._tbn_enabled:
                    cls._anim_frame += 1
                    if cls._anim_frame % cls._flip_every_n == 0:
                        s = _carb_settings.get_settings()
                        cur = s.get(cls._TBN_PATH) or cls._TBN_GPU
                        s.set(cls._TBN_PATH, cls._TBN_FORCE if cur == cls._TBN_GPU else cls._TBN_GPU)
                else:
                    cls._touch_normals()

                if (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                    break
        except asyncio.CancelledError:
            return
        finally:
            cls._is_animating = False
            cls._play_task = None
            cls._notify(cls._t)

            async def _end_flush():
                s = _carb_settings.get_settings()
                s.set(cls._TBN_PATH, cls._TBN_FORCE)
                await omni.kit.app.get_app().next_update_async()
                s.set(cls._TBN_PATH, cls._tbn_default)

            if cls._tbn_enabled:
                if cls._flush_task and not cls._flush_task.done():
                    cls._flush_task.cancel()
                cls._flush_task = asyncio.ensure_future(_end_flush())
