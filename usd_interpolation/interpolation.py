import asyncio
import time
from typing import Callable

import numpy as np
from pxr import Sdf, Usd, UsdGeom, Vt
import omni.kit.app
import omni.timeline
import omni.usd


class UVMixer:

    _registry: dict[str, 'UVMixer'] = {}

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def create(cls,
               target_prim_path: str,
               *st_paths: str,
               key: str | None = None,
               play_duration: float = 1.0,
               use_correction: bool = True) -> 'UVMixer':
        """
        Create a UVMixer for target_prim_path, reading st data from each USD file.
        Requires at least 2 source paths.
        If key is given, stores the instance in the registry for later retrieval.
        """
        if len(st_paths) < 2:
            raise ValueError(f"[UVMixer] need at least 2 source paths, got {len(st_paths)}")
        st_maps = []
        for path in st_paths:
            file_map = cls.read_st_file(path)
            st = file_map.get(target_prim_path)
            if st is None:
                raise ValueError(f"[UVMixer] no st for {target_prim_path} in {path}")
            st_maps.append(st)
        return cls._init(target_prim_path, st_maps,
                         key=key, play_duration=play_duration, use_correction=use_correction)

    @classmethod
    def _from_maps(cls,
                   target_prim_path: str,
                   st_maps: list,
                   *,
                   key: str | None = None,
                   play_duration: float = 1.0,
                   use_correction: bool = True) -> 'UVMixer':
        """Create a UVMixer from pre-loaded st arrays (e.g. for duplicated prims)."""
        return cls._init(target_prim_path, list(st_maps),
                         key=key, play_duration=play_duration, use_correction=use_correction)

    @classmethod
    def _init(cls, target, st_maps, *, key, play_duration, use_correction) -> 'UVMixer':
        inst = cls.__new__(cls)
        inst._target = target
        inst._st_maps = st_maps
        inst._t = 0.0
        inst._play_task = None
        inst._speed = 1.0
        inst._play_duration = play_duration
        inst._use_correction = use_correction
        inst._key = key
        inst._subscribers = []
        if key is not None:
            cls._registry[key] = inst
        inst._bake_timesamples()
        return inst

    @classmethod
    def get(cls, key: str) -> 'UVMixer | None':
        """Retrieve a previously created mixer by key."""
        return cls._registry.get(key)

    # ── Playback ─────────────────────────────────────────────────────

    def play(self, *, forward: bool = True, loop: bool = False) -> None:
        self.stop()
        self._play_task = asyncio.ensure_future(self._animate(forward, loop))

    def stop(self) -> None:
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            self._play_task = None

    def is_playing(self) -> bool:
        return self._play_task is not None and not self._play_task.done()

    # ── Position ─────────────────────────────────────────────────────

    def seek(self, t: float) -> None:
        t = max(0.0, min(1.0, t))
        self._t = t
        n = len(self._st_maps)
        stage = omni.usd.get_context().get_stage()
        tps = stage.GetTimeCodesPerSecond() if stage else 24.0
        omni.timeline.get_timeline_interface().set_current_time(t * (n - 1) / tps)
        self._notify(t)

    def position(self) -> float:
        return self._t

    # ── Configuration ──────────────────────────────────────────────────

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, float(speed))

    def get_speed(self) -> float:
        return self._speed

    def set_correction(self, enabled: bool) -> None:
        self._use_correction = bool(enabled)
        self._bake_timesamples()

    # ── Callbacks ────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[float], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[float], None]) -> None:
        self._subscribers = [c for c in self._subscribers if c != callback]

    # ── Lifecycle ────────────────────────────────────────────────────

    def destroy(self) -> None:
        self.stop()
        self._subscribers.clear()
        if self._key is not None:
            UVMixer._registry.pop(self._key, None)
            self._key = None

    # ── Internal ─────────────────────────────────────────────────────

    def _bake_timesamples(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        pxr_prim = pxr_stage.GetPrimAtPath(self._target)
        if not pxr_prim.IsValid():
            return

        fvli_val = None
        if self._use_correction:
            attr = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
            val = attr.Get() if (attr and attr.IsValid()) else None
            fvli_val = str(val) if val is not None else "cornersPlus1"

        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            with Sdf.ChangeBlock():
                for tc, st_data in enumerate(self._st_maps):
                    st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                    if st_pv and st_pv.GetAttr().IsValid():
                        st_pv.GetAttr().Set(
                            Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(st_data)), tc)
                    if fvli_val is not None:
                        mesh = UsdGeom.Mesh(pxr_prim)
                        fvli = mesh.GetFaceVaryingLinearInterpolationAttr()
                        if not fvli or not fvli.IsValid():
                            fvli = mesh.CreateFaceVaryingLinearInterpolationAttr()
                        if fvli and fvli.IsValid():
                            fvli.Set(fvli_val, tc)

    def _notify(self, t: float) -> None:
        for cb in list(self._subscribers):
            cb(t)

    async def _animate(self, forward: bool, loop: bool = False) -> None:
        try:
            while True:
                start_t = 0.0 if (loop and forward) else (1.0 if (loop and not forward) else self._t)
                target = 1.0 if forward else 0.0
                travel = abs(target - start_t)
                pass_start = time.monotonic()

                while True:
                    await omni.kit.app.get_app().next_update_async()
                    elapsed = time.monotonic() - pass_start
                    eff_duration = self._play_duration / max(self._speed, 0.01)
                    dt_scale = (travel / eff_duration) if (travel > 0.0 and eff_duration > 0.0) else 0.0
                    frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                    new_t = start_t + (frac if forward else -frac)
                    new_t = max(0.0, min(1.0, new_t))
                    self.seek(new_t)
                    if (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                        break

                if not loop:
                    break
        except asyncio.CancelledError:
            return
        finally:
            self._play_task = None

    @staticmethod
    def read_st_file(file_path: str) -> 'dict[str, np.ndarray]':
        """Open a USD file once and return {prim_path: st_array} for every mesh
        with a valid st primvar. Combines mesh discovery and st storage so the
        source file is opened exactly once regardless of how many meshes exist."""
        stage = Usd.Stage.Open(file_path)
        if not stage:
            print(f"[UVMixer] failed to open: {file_path}")
            return {}
        result: dict = {}
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
            if st_raw is None:
                continue
            result[str(prim.GetPath())] = np.array(st_raw, dtype=np.float32).reshape(-1, 2)
        return result
