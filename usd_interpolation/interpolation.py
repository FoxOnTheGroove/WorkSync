import asyncio
import time
from typing import Callable

import numpy as np
from pxr import Usd, UsdGeom, Vt
import omni.kit.app
import omni.timeline
import omni.usd

_FVLI_ALT = {
    "cornersPlus1": "cornersPlus2",
    "cornersPlus2": "cornersPlus1",
    "cornersOnly":  "none",
    "none":         "cornersOnly",
    "boundaries":   "cornersPlus1",
    "all":          "cornersPlus1",
}

_PLAY_DURATION = 1.0


class UVMixer:
    """하나의 타겟 prim 아래 있는 모든 메쉬의 UV 보간을 관리한다.

    _st_maps: 소스 파일(=타임코드)별 {mesh_path: st_array} 딕셔너리 리스트.
    """

    # ── 팩토리 ──────────────────────────────────────────────────────

    @classmethod
    def create(cls,
               st_maps: 'list[dict[str, np.ndarray]]',
               *,
               use_correction: bool = True) -> 'UVMixer':
        """이미 빌드된 st_maps(리스트 of {mesh_path: array})로부터 UVMixer를 생성한다."""
        return cls._init(list(st_maps), use_correction=use_correction)

    @classmethod
    def create_with_maps(cls,
                         *st_paths: str,
                         use_correction: bool = True) -> 'UVMixer':
        """각 소스 USD 파일에서 모든 메쉬를 읽어 UVMixer를 생성한다.
        모든 파일에 공통으로 존재하는 메쉬 경로만 사용한다.
        """
        if len(st_paths) < 2:
            raise ValueError(f"[UVMixer] need at least 2 source paths, got {len(st_paths)}")
        maps_per_file = [cls.make_st_map(p) for p in st_paths]
        common = set(maps_per_file[0].keys())
        for m in maps_per_file[1:]:
            common &= set(m.keys())
        if not common:
            raise ValueError(f"[UVMixer] no common mesh paths found across source files")
        st_maps = [{path: maps_per_file[i][path] for path in common}
                   for i in range(len(st_paths))]
        return cls.create(st_maps, use_correction=use_correction)

    @classmethod
    def _init(cls, st_maps, *, use_correction) -> 'UVMixer':
        inst = cls.__new__(cls)
        inst._st_maps = st_maps
        inst._t = 0.0
        inst._play_task = None
        inst._speed = 1.0
        inst._use_correction = use_correction
        inst._subscribers = []
        inst._fvli_cache: dict[str, str] = {}
        inst._bake_timesamples()
        return inst

    # ── 재생 ─────────────────────────────────────────────────────

    def play(self, *, forward: bool = True, loop: bool = False) -> None:
        self.stop()
        self._play_task = asyncio.ensure_future(self._animate(forward, loop))

    def stop(self) -> None:
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            self._play_task = None

    def is_playing(self) -> bool:
        return self._play_task is not None and not self._play_task.done()

    # ── 위치 ─────────────────────────────────────────────────────

    def seek(self, t: float, *, _correction: bool = True) -> None:
        t = max(0.0, min(1.0, t))
        self._t = t
        n = len(self._st_maps)
        stage = omni.usd.get_context().get_stage()
        tps = stage.GetTimeCodesPerSecond() if stage else 24.0
        omni.timeline.get_timeline_interface().set_current_time(t * (n - 1) / tps)
        if _correction:
            self.apply_correction()
        self._notify(t)

    def apply_correction(self) -> None:
        if not self._use_correction or not self._fvli_cache:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for mesh_path, orig_val in self._fvli_cache.items():
                pxr_prim = stage.GetPrimAtPath(mesh_path)
                if not pxr_prim.IsValid():
                    continue
                fvli = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
                if not fvli or not fvli.IsValid():
                    fvli = UsdGeom.Mesh(pxr_prim).CreateFaceVaryingLinearInterpolationAttr()
                if fvli and fvli.IsValid():
                    fvli.Set(_FVLI_ALT.get(orig_val, orig_val))
                    fvli.Set(orig_val)

    def position(self) -> float:
        return self._t

    # ── 설정 ──────────────────────────────────────────────────

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, float(speed))

    def set_correction(self, enabled: bool) -> None:
        self._use_correction = bool(enabled)
        self._bake_timesamples()

    # ── 콜백 ────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[float], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[float], None]) -> None:
        self._subscribers = [c for c in self._subscribers if c != callback]

    # ── 라이프사이클 ────────────────────────────────────────────────────

    def destroy(self) -> None:
        self.stop()
        self._subscribers.clear()

    # ── 내부 ─────────────────────────────────────────────────────

    def _bake_timesamples(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None or not self._st_maps:
            return

        self._fvli_cache = {}
        if self._use_correction:
            for mesh_path in self._st_maps[0]:
                pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                if not pxr_prim.IsValid():
                    continue
                attr = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
                val = attr.Get() if (attr and attr.IsValid()) else None
                self._fvli_cache[mesh_path] = str(val) if val is not None else "cornersPlus1"

        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            for tc, mesh_map in enumerate(self._st_maps):
                for mesh_path, st_data in mesh_map.items():
                    pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                    if not pxr_prim.IsValid():
                        continue
                    st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                    if st_pv and st_pv.GetAttr().IsValid():
                        st_pv.GetAttr().Set(
                            Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(st_data)), tc)

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
                    eff_duration = _PLAY_DURATION / max(self._speed, 0.01)
                    dt_scale = (travel / eff_duration) if (travel > 0.0 and eff_duration > 0.0) else 0.0
                    frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                    new_t = start_t + (frac if forward else -frac)
                    new_t = max(0.0, min(1.0, new_t))
                    self.seek(new_t, _correction=False)
                    if (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                        break

                self.apply_correction()

                if not loop:
                    break
        except asyncio.CancelledError:
            self.apply_correction()
            return
        finally:
            self._play_task = None

    @staticmethod
    def make_st_map(file_path: str) -> 'dict[str, np.ndarray]':
        """USD 파일을 한 번 열어 모든 메쉬의 {prim_path: st_array}를 반환한다."""
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
