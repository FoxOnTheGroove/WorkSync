import asyncio
from typing import Callable

import numpy as np
from pxr import Usd, UsdGeom, Vt
import omni.kit.app
import omni.timeline
import omni.usd


class UVMixer:

    # ── Configuration ──────────────────────────────────────────────────────────
    _num_slots: int = 5
    _play_duration: float = 2.5
    _dirty_attr: str = "faceVertexCounts"  # "none" | "faceVertexIndices" | "faceVertexCounts" | "orientation" | "faceVaryingLinearInterpolation"

    # ── State ──────────────────────────────────────────────────────────────────
    _maps: list = [None] * 5
    _t: float = 0.0
    _play_task: object = None
    _subscribers: list = []

    # ── Public API ─────────────────────────────────────────────────────────────

    @classmethod
    def init(cls, *,
             num_slots: int | None = None,
             play_duration: float | None = None,
             dirty_attr: str | None = None) -> None:
        if num_slots is not None and num_slots != cls._num_slots:
            cls._maps = [None] * num_slots
            cls._num_slots = num_slots
        if play_duration is not None:
            cls._play_duration = play_duration
        if dirty_attr is not None:
            cls._dirty_attr = dirty_attr

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
            stage = omni.usd.get_context().get_stage()
            tps = stage.GetTimeCodesPerSecond() if stage else 24.0
            omni.timeline.get_timeline_interface().set_current_time(
                t * (loaded_count - 1) / tps)
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
    def _bake_timesamples(cls) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        loaded = [(i, m) for i, m in enumerate(cls._maps) if m is not None]
        if len(loaded) < 2:
            return
        # faceVaryingLinearInterpolation: tc별 교번 값으로 dirty 보장 (token이라 값 변화 필요)
        _FVLI = cls._dirty_attr == "faceVaryingLinearInterpolation"
        _FVLI_PAIR = ("none", "cornersPlus1")

        dirty_name = {
            "faceVertexCounts": "faceVertexCounts",
            "faceVertexIndices": "faceVertexIndices",
            "orientation":       "orientation",
        }.get(cls._dirty_attr)  # None → "none" 또는 faceVaryingLinearInterpolation 모드

        dirty_cache: dict = {}
        if dirty_name:
            for prim_path in loaded[0][1].keys():
                pxr_prim = pxr_stage.GetPrimAtPath(prim_path)
                if not pxr_prim.IsValid():
                    continue
                pxr_dirty = pxr_prim.GetAttribute(dirty_name)
                if not pxr_dirty or not pxr_dirty.IsValid():
                    continue
                val = pxr_dirty.Get(Usd.TimeCode.Default())
                if val is None:
                    samples = pxr_dirty.GetTimeSamples()
                    if samples:
                        val = pxr_dirty.Get(samples[0])
                if val is None:
                    continue
                dirty_cache[prim_path] = val

        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            for tc, (_, st_map) in enumerate(loaded):
                for prim_path, st_data in st_map.items():
                    pxr_prim = pxr_stage.GetPrimAtPath(prim_path)
                    if not pxr_prim.IsValid():
                        continue
                    st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                    if st_pv and st_pv.GetAttr().IsValid():
                        st_pv.GetAttr().Set(
                            Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(st_data)), tc)
                    if _FVLI:
                        mesh = UsdGeom.Mesh(pxr_prim)
                        fvli_attr = mesh.GetFaceVaryingLinearInterpolationAttr()
                        if not fvli_attr or not fvli_attr.IsValid():
                            fvli_attr = mesh.CreateFaceVaryingLinearInterpolationAttr()
                        if fvli_attr and fvli_attr.IsValid():
                            fvli_attr.Set(_FVLI_PAIR[tc % 2], tc)
                    elif dirty_name and prim_path in dirty_cache:
                        pxr_dirty = pxr_prim.GetAttribute(dirty_name)
                        if pxr_dirty and pxr_dirty.IsValid():
                            pxr_dirty.Set(dirty_cache[prim_path], tc)
        print(f"[UVMixer] baked {len(loaded)} timesamples (tc 0..{len(loaded)-1}), dirty={cls._dirty_attr}")

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
