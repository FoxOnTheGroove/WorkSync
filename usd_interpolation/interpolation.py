import asyncio
import time
from typing import Callable

import numpy as np
from pxr import Usd, UsdGeom
import usdrt
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

    _st_maps: 소스 파일(=보간 키프레임)별 {mesh_path: st_array} 딕셔너리 리스트.
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
            raise ValueError("[UVMixer] no common mesh paths found across source files")
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
        inst._bake_fvli()
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
        self._write_uv(t)
        if _correction:
            self._trigger_dirty()
        self._notify(t)

    def position(self) -> float:
        return self._t

    # ── 설정 ──────────────────────────────────────────────────

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, float(speed))

    def set_correction(self, enabled: bool) -> None:
        self._use_correction = bool(enabled)
        self._bake_fvli()

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

    def _bake_fvli(self) -> None:
        """fvli를 타임코드에 bake한다. UV는 seek 시 직접 쓰므로 여기서 다루지 않는다."""
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None or not self._st_maps:
            return

        # 이전 fvli 타임샘플 제거 (correction 토글 시 잔존 방지)
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            for mesh_path in self._st_maps[0]:
                pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                if not pxr_prim.IsValid():
                    continue
                fvli = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
                if fvli and fvli.IsValid():
                    fvli.ClearAtTime(0)
                    fvli.ClearAtTime(1)

        self._fvli_cache = {}
        if not self._use_correction:
            return

        # 각 메쉬의 원본 fvli 값 기록
        for mesh_path in self._st_maps[0]:
            pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
            if not pxr_prim.IsValid():
                continue
            attr = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
            val = attr.Get() if (attr and attr.IsValid()) else None
            self._fvli_cache[mesh_path] = str(val) if val is not None else "cornersPlus1"

        # tc=0 → alt 값, tc=1 → orig 값: 타임라인 이동이 DirtyTopology를 발생시킨다
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            for mesh_path, orig_val in self._fvli_cache.items():
                pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                if not pxr_prim.IsValid():
                    continue
                fvli = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
                if not fvli or not fvli.IsValid():
                    fvli = UsdGeom.Mesh(pxr_prim).CreateFaceVaryingLinearInterpolationAttr()
                if fvli and fvli.IsValid():
                    fvli.Set(_FVLI_ALT.get(orig_val, orig_val), 0)
                    fvli.Set(orig_val, 1)

    def _write_uv(self, t: float) -> None:
        """CPU lerp로 UV를 계산한 뒤 UsdRt(Fabric)으로 직접 기입한다."""
        n = len(self._st_maps)
        if n == 0:
            return
        if n == 1:
            i, alpha = 0, 0.0
        else:
            pos = t * (n - 1)
            i = min(int(pos), n - 2)
            alpha = pos - i

        stage_id = omni.usd.get_context().get_stage_id()
        if not stage_id:
            return
        rt_stage = usdrt.Usd.Stage.Attach(stage_id)

        for mesh_path in self._st_maps[0]:
            a = self._st_maps[i].get(mesh_path)
            if a is None:
                continue
            if alpha == 0.0 or n == 1:
                uv = np.ascontiguousarray(a, dtype=np.float32)
            else:
                b = self._st_maps[i + 1].get(mesh_path)
                if b is None:
                    continue
                uv = np.ascontiguousarray((1.0 - alpha) * a + alpha * b, dtype=np.float32)

            rt_prim = rt_stage.GetPrimAtPath(mesh_path)
            if not rt_prim.IsValid():
                continue
            attr = rt_prim.GetAttribute("primvars:st")
            if attr and attr.IsValid():
                attr.Set(usdrt.Vt.Vec2fArray.FromNumpy(uv))

    def _trigger_dirty(self) -> None:
        """타임라인을 alt→orig 순으로 이동해 fvli DirtyTopology를 발생시킨다.
        씬의 모든 bake된 prim에 동시 적용된다.
        """
        if not self._use_correction or not self._fvli_cache:
            return
        stage = omni.usd.get_context().get_stage()
        tps = stage.GetTimeCodesPerSecond() if stage else 24.0
        tl = omni.timeline.get_timeline_interface()
        tl.set_current_time(0.0)           # tc=0: alt fvli 경유
        tl.set_current_time(1.0 / tps)    # tc=1: orig fvli 복귀

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

                self._trigger_dirty()

                if not loop:
                    break
        except asyncio.CancelledError:
            self._trigger_dirty()
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
