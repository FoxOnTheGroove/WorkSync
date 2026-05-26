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
    def create(cls, target_path: 'str | None') -> 'UVMixer':
        """빈 UVMixer를 생성한다. 소스는 load(*paths)로 주입한다.
        target_path가 None이면 소스 경로를 그대로 사용한다."""
        inst = cls.__new__(cls)
        inst._target_path = target_path
        inst._st_maps = []
        inst._baked_paths = []
        inst._t = 0.0
        inst._play_task = None
        inst._speed = 1.0
        inst._forward = True
        inst._loop = False
        inst._use_correction = True
        inst._subscribers = []
        inst._fvli_cache = {}
        return inst

    def load(self, *st_paths: str) -> 'list[str]':
        """소스 USD 파일들을 읽어 보간 데이터를 주입한다.
        재호출 시 이전 bake를 청소하고 다시 굽는다. 구독자는 유지된다.
        유효하지 않은 메쉬는 경고 후 스킵되며, 경고 메시지 목록을 반환한다."""
        if len(st_paths) < 2:
            raise ValueError(f"[UVMixer] need at least 2 source paths, got {len(st_paths)}")
        maps_per_file = [self.make_st_map(p) for p in st_paths]
        common = set(maps_per_file[0].keys())
        for m in maps_per_file[1:]:
            common &= set(m.keys())
        if not common:
            raise ValueError(f"[UVMixer] no common mesh paths found across source files")
        st_maps = [{path: maps_per_file[i][path] for path in common}
                   for i in range(len(st_paths))]

        warnings, valid = self._validate(st_maps)
        for w in warnings:
            print(f"[UVMixer] {w}")
        if not valid:
            raise ValueError("[UVMixer] no valid meshes remain after validation")
        st_maps = [{p: frame[p] for p in valid} for frame in st_maps]

        self.stop()
        self._clear_baked()
        self._st_maps = self._remap(st_maps, self._target_path)
        self._t = 0.0
        self._bake_timesamples()
        return warnings

    # ── 재생 ─────────────────────────────────────────────────────

    def play(self) -> None:
        if not self._st_maps:
            return
        self.stop()
        self._play_task = asyncio.ensure_future(self._animate())

    def stop(self) -> None:
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
            self._play_task = None

    def is_playing(self) -> bool:
        return self._play_task is not None and not self._play_task.done()

    # ── 위치 ─────────────────────────────────────────────────────

    def set_value(self, t: float, *, _correction: bool = True, drive_timeline: bool = True) -> None:
        if not self._st_maps:
            return
        t = max(0.0, min(1.0, t))
        self._t = t
        if drive_timeline:
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

    def get_value(self) -> float:
        return self._t

    # ── 설정 ──────────────────────────────────────────────────

    def set_forward(self, forward: bool) -> None:
        self._forward = bool(forward)

    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, float(speed))

    def set_correction(self, enabled: bool) -> None:
        self._use_correction = bool(enabled)
        if enabled:
            self._refresh_fvli_cache()
        else:
            self._fvli_cache = {}

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

    def _validate(self, st_maps: 'list[dict[str, np.ndarray]]') -> 'tuple[list[str], set[str]]':
        """소스 간 UV 길이 불일치, 리맵 후 경로 부재 메쉬를 경고하고 유효 경로만 반환한다."""
        warnings: list[str] = []
        valid: set[str] = set()
        if not st_maps:
            return warnings, valid

        all_paths = set(st_maps[0].keys())
        stage = omni.usd.get_context().get_stage() if self._target_path else None
        source_root: str | None = None
        if self._target_path and all_paths:
            source_root = self._source_root(next(iter(all_paths)))

        for path in sorted(all_paths):
            lengths = [len(frame[path]) for frame in st_maps]
            if len(set(lengths)) > 1:
                warnings.append(
                    f"skip '{path}': UV count differs across sources {lengths}"
                )
                continue

            if self._target_path and source_root and stage:
                remapped = self._target_path + path[len(source_root):]
                prim = stage.GetPrimAtPath(remapped)
                if not prim.IsValid() or not prim.IsA(UsdGeom.Mesh):
                    warnings.append(
                        f"skip '{path}': remapped path '{remapped}' not found in stage"
                    )
                    continue

            valid.add(path)

        return warnings, valid

    def _refresh_fvli_cache(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        self._fvli_cache = {}
        if not self._use_correction or pxr_stage is None or not self._st_maps:
            return
        for mesh_path in self._st_maps[0]:
            pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
            if not pxr_prim.IsValid():
                continue
            attr = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
            val = attr.Get() if (attr and attr.IsValid()) else None
            self._fvli_cache[mesh_path] = str(val) if val is not None else "cornersPlus1"

    def _clear_baked(self) -> None:
        """이전에 bake한 메쉬들의 st 타임샘플을 session layer에서 제거한다."""
        if not self._baked_paths:
            return
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            self._baked_paths = []
            return
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            for mesh_path in self._baked_paths:
                pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                if not pxr_prim.IsValid():
                    continue
                st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                if st_pv and st_pv.GetAttr().IsValid():
                    st_pv.GetAttr().Clear()
        self._baked_paths = []

    def _bake_timesamples(self) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None or not self._st_maps:
            return

        self._refresh_fvli_cache()

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
        self._baked_paths = list(self._st_maps[0].keys())

    def _notify(self, t: float) -> None:
        for cb in list(self._subscribers):
            cb(t)

    async def _animate(self) -> None:
        try:
            last_time = time.monotonic()
            while True:
                await omni.kit.app.get_app().next_update_async()
                now = time.monotonic()
                step = (now - last_time) * self._speed / _PLAY_DURATION
                last_time = now

                new_t = self._t + (step if self._forward else -step)
                new_t = max(0.0, min(1.0, new_t))
                self.set_value(new_t, _correction=False)

                reached_end = (self._forward and new_t >= 1.0) or \
                              (not self._forward and new_t <= 0.0)
                if reached_end:
                    self.apply_correction()
                    self._notify(self._t)
                    if not self._loop:
                        break
                    self.set_value(0.0 if self._forward else 1.0, _correction=False)
        except asyncio.CancelledError:
            self.apply_correction()
            self._notify(self._t)
            return
        finally:
            self._play_task = None

    @staticmethod
    def _source_root(path: str) -> str:
        return '/' + path.split('/')[1]

    @staticmethod
    def _remap(st_maps: 'list[dict[str, np.ndarray]]',
               target_path: 'str | None') -> 'list[dict[str, np.ndarray]]':
        """소스 st_maps의 최상위 루트를 target_path로 교체한다. None이면 그대로 반환."""
        if not target_path:
            return list(st_maps)
        source_root = UVMixer._source_root(next(iter(st_maps[0])))
        return [
            {target_path + src[len(source_root):]: arr for src, arr in frame.items()}
            for frame in st_maps
        ]

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
