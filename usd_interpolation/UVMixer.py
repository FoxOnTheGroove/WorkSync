import asyncio
from typing import Callable

import numpy as np
from pxr import Usd, UsdGeom, Vt
import omni.timeline
import omni.usd

from .UVMixer_player import UVMixerPlayer

_FVLI_ALT = {
    "cornersPlus1": "cornersPlus2",
    "cornersPlus2": "cornersPlus1",
    "cornersOnly":  "none",
    "none":         "cornersOnly",
    "boundaries":   "cornersPlus1",
    "all":          "cornersPlus1",
}

# ── 보간 모드 스위치 ─────────────────────────────────────────────────
# 'timeline' : st를 타임코드로 bake → USD 네이티브 보간 (빠름, 전역 타임라인 공유)
# 'direct'   : 매 프레임 Python lerp 후 st 직접 write (타임라인 독립)
UV_INTERP_MODE: str = 'timeline'


class UVMixer:
    """하나의 타겟 prim 아래 있는 모든 메쉬의 UV 보간을 관리한다.

    _st_maps: 소스 파일(=타임코드)별 {mesh_path: st_array} 딕셔너리 리스트.

    각 UVMixer는 own_player(UVMixerPlayer)를 소유한다.
    join_player(shared_player)로 공유 플레이어에 구독 전환 가능 (sync).
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
        inst._n_frames: int = 0
        inst._t = 0.0          # 마지막으로 적용된 t (get_value용 캐시)
        inst._use_correction = True
        inst._correction_mode: str = 'boundary'  # 'none' | 'boundary' | 'all'
        inst._subscribers = []
        inst._fvli_cache = {}
        # 각 mixer는 자신의 own_player를 가진다.
        # join_player(shared_player)로 공유 플레이어에 구독 전환 가능.
        inst.own_player: UVMixerPlayer = UVMixerPlayer()
        inst._active_player: UVMixerPlayer = inst.own_player
        inst.own_player.subscribe_tick(inst._apply_t)
        return inst

    async def load(self, st_paths: 'list[str]') -> 'list[str]':
        """소스 USD 파일들을 읽어 보간 데이터를 주입한다.
        재호출 시 이전 bake를 청소하고 다시 굽는다. 구독자는 유지된다.
        유효하지 않은 메쉬는 경고 후 스킵되며, 경고 메시지 목록을 반환한다.
        각 소스가 접근 가능해질 때까지 비동기로 대기한 뒤 읽는다."""
        if len(st_paths) < 2:
            raise ValueError(f"[UVMixer] need at least 2 source paths, got {len(st_paths)}")
        await self._await_accessible(st_paths)
        self._n_frames = len(st_paths)
        maps_per_file = [self.make_st_map(p) for p in st_paths]
        if maps_per_file and maps_per_file[0]:
            ref_root = self._source_root(next(iter(maps_per_file[0])))
            normed = []
            for m in maps_per_file:
                if m:
                    file_root = self._source_root(next(iter(m)))
                    if file_root != ref_root:
                        m = {ref_root + path[len(file_root):]: arr
                             for path, arr in m.items()}
                normed.append(m)
            maps_per_file = normed
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

        self.own_player.stop()   # own_player만 멈춤. shared_player는 건드리지 않음.
        self._clear_baked()
        self._st_maps = self._remap(st_maps, self._target_path)
        self._t = 0.0
        self._bake_timesamples()
        return warnings

    # ── 재생 ─────────────────────────────────────────────────────

    def play(self) -> None:
        """독립 재생(own_player). 공유 플레이어에 합류된 상태여도 own_player를 따로 시작한다."""
        if not self._st_maps:
            return
        self.own_player.play()

    def stop(self) -> None:
        """own_player 정지. 공유 플레이어는 건드리지 않는다."""
        self.own_player.stop()

    def is_playing(self) -> bool:
        return self.own_player.is_playing()

    # ── 위치 ─────────────────────────────────────────────────────

    def set_value(self, t: float, *, correction: bool = True,
                  drive_timeline: bool = True) -> None:
        """UV 속성을 t(0.0~1.0) 위치로 쓴다."""
        if not self._st_maps:
            return
        t = max(0.0, min(1.0, t))
        self._t = t
        if UV_INTERP_MODE == 'direct':
            self._write_st_direct(t)
        elif drive_timeline and self._n_frames >= 2:
            tl = omni.timeline.get_timeline_interface()
            tl.set_current_time(t / tl.get_time_codes_per_second())
        if correction:
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
        self.own_player.set_forward(forward)

    def set_loop(self, loop: bool) -> None:
        self.own_player.set_loop(loop)

    def set_speed(self, speed: float) -> None:
        self.own_player.set_speed(speed)

    def set_correction(self, enabled: bool) -> None:
        self.set_correction_mode('boundary' if enabled else 'none')

    def set_correction_mode(self, mode: str) -> None:
        """mode: 'none' | 'boundary' | 'all'"""
        self._correction_mode = mode
        self._use_correction = (mode != 'none')
        if self._use_correction:
            self._refresh_fvli_cache()
        else:
            self._fvli_cache = {}

    # ── 콜백 ────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[float], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[float], None]) -> None:
        self._subscribers = [c for c in self._subscribers if c != callback]

    # ── 플레이어 구독 전환 ───────────────────────────────────────

    def _apply_t(self, t: float, correction: bool) -> None:
        if self._correction_mode == 'all':
            correction = True
        self.set_value(t, correction=correction, drive_timeline=True)

    def join_player(self, player: UVMixerPlayer) -> None:
        """공유 플레이어에 구독 추가. own_player의 _apply_t는 그대로 유지한다."""
        if self._active_player is player:
            return
        self._active_player = player
        player.subscribe_tick(self._apply_t)

    def leave_player(self) -> None:
        """own_player로 복귀. 공유 플레이어에서만 _apply_t를 제거한다."""
        if self._active_player is self.own_player:
            return
        self._active_player.unsubscribe_tick(self._apply_t)
        self.own_player.copy_from(self._active_player)
        self._active_player = self.own_player

    # ── 라이프사이클 ────────────────────────────────────────────────────

    def destroy(self) -> None:
        if self._active_player is not self.own_player:
            self._active_player.unsubscribe_tick(self._apply_t)
            self._active_player = self.own_player
        self.own_player.unsubscribe_tick(self._apply_t)
        self.own_player.stop()
        self._clear_baked()
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
        if not self._st_maps:
            return
        # _baked_paths는 모드 무관하게 항상 설정 — _clear_baked가 어느 모드든 정리 가능하게.
        self._baked_paths = list(self._st_maps[0].keys())
        self._refresh_fvli_cache()

        if UV_INTERP_MODE == 'direct':
            return  # direct 모드는 bake 없이 set_value마다 직접 write

        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return

        # timeline 모드: session layer에 bake
        # timecode를 0~1 정규화값으로 bake → 다른 n_frames mixer와 timeline을 공유 가능
        n = len(self._st_maps)
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            pxr_stage.GetSessionLayer().timeCodesPerSecond = pxr_stage.GetTimeCodesPerSecond()
            for i, mesh_map in enumerate(self._st_maps):
                tc = i / (n - 1) if n > 1 else 0.0
                for mesh_path, st_data in mesh_map.items():
                    pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                    if not pxr_prim.IsValid():
                        continue
                    st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                    if st_pv and st_pv.GetAttr().IsValid():
                        st_pv.GetAttr().Set(
                            Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(st_data)), tc)

    def _write_st_direct(self, t: float) -> None:
        """direct 모드 전용: t 위치의 UV를 Python lerp로 계산해 st primvar에 직접 쓴다."""
        n = len(self._st_maps)
        idx = t * (n - 1)
        i = min(int(idx), n - 2)
        frac = idx - i
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            for mesh_path, st_a in self._st_maps[i].items():
                st_b = self._st_maps[i + 1][mesh_path]
                interp = st_a + frac * (st_b - st_a)
                pxr_prim = pxr_stage.GetPrimAtPath(mesh_path)
                if not pxr_prim.IsValid():
                    continue
                st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                if st_pv and st_pv.GetAttr().IsValid():
                    st_pv.GetAttr().Set(
                        Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(interp)))

    def _notify(self, t: float) -> None:
        for cb in list(self._subscribers):
            cb(t)

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
    async def _await_accessible(paths: 'list[str]',
                                timeout: float = 30.0,
                                interval: float = 0.2) -> None:
        """각 소스가 서버에서 stat OK가 될 때까지 메인 스레드를 양보하며 대기한다.
        omni.client 부재(헤드리스/로컬) 시 즉시 통과한다."""
        try:
            import omni.client
        except Exception:
            return
        for url in paths:
            elapsed = 0.0
            while True:
                res, _ = await omni.client.stat_async(url)
                if res == omni.client.Result.OK:
                    break
                if elapsed >= timeout:
                    print(f"[UVMixer] timeout waiting for source: {url}")
                    break
                await asyncio.sleep(interval)
                elapsed += interval

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
