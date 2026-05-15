import asyncio
from typing import Callable

import numpy as np
from pxr import Usd, UsdGeom
import usdrt
import omni.kit.app
import omni.usd


class UVMixer:

    # ── Configuration ──────────────────────────────────────────────────────────
    _num_slots: int = 5
    _play_duration: float = 2.5

    # ── State ──────────────────────────────────────────────────────────────────
    _maps: list = [None] * 5
    _t: float = 0.0
    _play_task: object = None
    _subscribers: list = []

    # ── usdrt 캐시 (로드 시 1회 구성) ─────────────────────────────────────────
    _rt_stage: object = None
    _rt_st_attrs: dict = {}   # prim_path → usdrt Attribute (primvars:st)

    # ── Public API ─────────────────────────────────────────────────────────────

    @classmethod
    def init(cls, *,
             num_slots: int | None = None,
             play_duration: float | None = None) -> None:
        if num_slots is not None and num_slots != cls._num_slots:
            cls._maps = [None] * num_slots
            cls._num_slots = num_slots
        if play_duration is not None:
            cls._play_duration = play_duration

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
        cls._build_rt_cache()
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
        cls._apply_lerp(t)
        cls._notify(t)

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
    def _build_rt_cache(cls) -> None:
        stage_id = omni.usd.get_context().get_stage_id()
        cls._rt_stage = usdrt.Usd.Stage.Attach(stage_id)
        prim_paths = set()
        for m in cls._maps:
            if m:
                prim_paths.update(m.keys())
        cls._rt_st_attrs.clear()
        for prim_path in prim_paths:
            rt_prim = cls._rt_stage.GetPrimAtPath(usdrt.Sdf.Path(prim_path))
            if not rt_prim.IsValid():
                continue
            rt_st = rt_prim.GetAttribute("primvars:st")
            if rt_st and rt_st.IsValid():
                cls._rt_st_attrs[prim_path] = rt_st
        print(f"[UVMixer] rt cache built ({len(cls._rt_st_attrs)} attrs)")

    @classmethod
    def _apply_lerp(cls, t: float) -> None:
        loaded = [(i, m) for i, m in enumerate(cls._maps) if m is not None]
        n = len(loaded)
        if n < 2 or not cls._rt_st_attrs:
            return
        pos = t * (n - 1)
        idx = min(int(pos), n - 2)
        frac = pos - idx
        map_a = loaded[idx][1]
        map_b = loaded[idx + 1][1]
        for prim_path, rt_st in cls._rt_st_attrs.items():
            st_a = map_a.get(prim_path)
            st_b = map_b.get(prim_path)
            if st_a is None or st_b is None or st_a.shape != st_b.shape:
                continue
            st = (st_a * (1.0 - frac) + st_b * frac).astype(np.float32)
            rt_st.Set(usdrt.Vt.Vec2fArray(st.reshape(-1, 2).tolist()))

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
        try:
            while True:
                await omni.kit.app.get_app().next_update_async()
                elapsed += 1.0 / 60.0
                frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                new_t = start_t + (frac if forward else -frac)
                new_t = max(0.0, min(1.0, new_t))
                cls.set_t(new_t)
                if (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                    break
        except asyncio.CancelledError:
            return
        finally:
            cls._play_task = None
