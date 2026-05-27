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

PLAY_DURATION = 1.0

# ── 보간 모드 스위치 ─────────────────────────────────────────────────
# 'timeline' : st를 타임코드로 bake → USD 네이티브 보간 (빠름, 전역 타임라인 공유)
# 'direct'   : 매 프레임 Python lerp 후 st 직접 write (타임라인 독립)
UV_INTERP_MODE: str = 'timeline'


class PlaybackClock:
    """재생 루프와 tick 알림을 담당하는 클럭.
    omni 의존성 없음 — seek_timeline 등 외부 동작은 콜백으로 주입한다.
    tick 콜백 시그니처: (t: float, correction: bool)
    stopped 콜백 시그니처: ()"""

    def __init__(self) -> None:
        self._t: float = 0.0
        self._speed: float = 1.0
        self._forward: bool = True
        self._loop: bool = False
        self._play_task: asyncio.Task | None = None
        self._tick_cbs: list[Callable[[float, bool], None]] = []
        self._stopped_cbs: list[Callable[[], None]] = []

    # ── 재생 제어 ──────────────────────────────────────────────────────

    def play(self) -> None:
        self.stop()
        self._play_task = asyncio.ensure_future(self._animate())

    def stop(self) -> None:
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()
        self._play_task = None

    def is_playing(self) -> bool:
        return self._play_task is not None and not self._play_task.done()

    # ── t / 설정 ───────────────────────────────────────────────────────

    def set_t(self, t: float, *, correction: bool = True) -> None:
        self._t = max(0.0, min(1.0, t))
        self._notify_tick(self._t, correction)

    def set_speed(self, v: float) -> None:
        self._speed = max(0.1, float(v))

    def set_forward(self, v: bool) -> None:
        self._forward = bool(v)

    def set_loop(self, v: bool) -> None:
        self._loop = bool(v)

    # ── 상태 복사 (sync 진입 시) ───────────────────────────────────────

    def copy_from(self, other: 'PlaybackClock') -> None:
        """다른 클럭의 t/speed/forward/loop를 복사한다. play_task는 복사하지 않는다."""
        self._t = other._t
        self._speed = other._speed
        self._forward = other._forward
        self._loop = other._loop

    # ── 구독 ───────────────────────────────────────────────────────────

    def subscribe_tick(self, cb: Callable[[float, bool], None]) -> None:
        if cb not in self._tick_cbs:
            self._tick_cbs.append(cb)

    def unsubscribe_tick(self, cb: Callable[[float, bool], None]) -> None:
        self._tick_cbs = [c for c in self._tick_cbs if c != cb]

    def subscribe_stopped(self, cb: Callable[[], None]) -> None:
        if cb not in self._stopped_cbs:
            self._stopped_cbs.append(cb)

    def unsubscribe_stopped(self, cb: Callable[[], None]) -> None:
        self._stopped_cbs = [c for c in self._stopped_cbs if c != cb]

    # ── 내부 ───────────────────────────────────────────────────────────

    def _notify_tick(self, t: float, correction: bool) -> None:
        for cb in list(self._tick_cbs):
            cb(t, correction)

    def _notify_stopped(self) -> None:
        for cb in list(self._stopped_cbs):
            cb()

    async def _animate(self) -> None:
        """델타-타임 스텝·루프·경계 correction. dummy_ui._animate_all을 대체한다."""
        cur_t = self._t
        try:
            last = time.monotonic()
            while True:
                await omni.kit.app.get_app().next_update_async()
                now = time.monotonic()
                step = (now - last) * self._speed / PLAY_DURATION
                last = now
                cur_t = max(0.0, min(1.0, cur_t + (step if self._forward else -step)))
                self._t = cur_t
                self._notify_tick(cur_t, False)
                reached_end = (self._forward and cur_t >= 1.0) or \
                              (not self._forward and cur_t <= 0.0)
                if reached_end:
                    self._notify_tick(cur_t, True)
                    if not self._loop:
                        break
                    cur_t = 0.0 if self._forward else 1.0
                    self._notify_tick(cur_t, False)
        except asyncio.CancelledError:
            self._notify_tick(cur_t, True)
        finally:
            self._play_task = None
            self._notify_stopped()


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
        inst._t = 0.0          # 마지막으로 적용된 t (get_value용 캐시)
        inst._use_correction = True
        inst._subscribers = []
        inst._fvli_cache = {}
        # 각 mixer는 자신의 own_clock을 가진다.
        # join_clock(shared_clock)으로 공유 클럭에 구독 전환 가능.
        inst.own_clock: PlaybackClock = PlaybackClock()
        inst._active_clock: PlaybackClock = inst.own_clock
        inst.own_clock.subscribe_tick(inst._apply_t)
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

        self.own_clock.stop()
        self._clear_baked()
        self._st_maps = self._remap(st_maps, self._target_path)
        self._t = 0.0
        self._bake_timesamples()
        return warnings

    # ── 재생 ─────────────────────────────────────────────────────

    def play(self) -> None:
        """독립 재생(own_clock). 공유 클럭에 합류된 상태여도 own_clock을 따로 시작한다."""
        if not self._st_maps:
            return
        self.own_clock.play()

    def stop(self) -> None:
        """own_clock 정지. 공유 클럭은 건드리지 않는다."""
        self.own_clock.stop()

    def is_playing(self) -> bool:
        return self.own_clock.is_playing()

    # ── 위치 ─────────────────────────────────────────────────────

    def set_value(self, t: float, *, correction: bool = True,
                  drive_timeline: bool = True) -> None:
        """UV 속성을 t(0.0~1.0) 위치로 쓴다.
        drive_timeline은 하위호환용으로 남겨두나, timeline seek는 이제
        PlaybackClock tick 구독자(dummy_ui._on_clock_tick)가 담당한다."""
        if not self._st_maps:
            return
        t = max(0.0, min(1.0, t))
        self._t = t
        if UV_INTERP_MODE == 'direct':
            self._write_st_direct(t)
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
        self.own_clock.set_forward(forward)

    def set_loop(self, loop: bool) -> None:
        self.own_clock.set_loop(loop)

    def set_speed(self, speed: float) -> None:
        self.own_clock.set_speed(speed)

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

    # ── 클럭 구독 전환 ──────────────────────────────────────────────────

    def _apply_t(self, t: float, correction: bool) -> None:
        """PlaybackClock tick 콜백 — UV 속성 write만 담당."""
        self.set_value(t, correction=correction, drive_timeline=False)

    def join_clock(self, clock: PlaybackClock) -> None:
        """공유 클럭에 구독 전환한다. 이전 클럭에서 unsubscribe."""
        if self._active_clock is clock:
            return
        self._active_clock.unsubscribe_tick(self._apply_t)
        self._active_clock = clock
        clock.subscribe_tick(self._apply_t)

    def leave_clock(self) -> None:
        """own_clock으로 복귀한다. 공유 클럭의 현재 t를 이어받아 점프 없음."""
        if self._active_clock is self.own_clock:
            return
        self._active_clock.unsubscribe_tick(self._apply_t)
        self.own_clock.copy_from(self._active_clock)
        self._active_clock = self.own_clock
        self.own_clock.subscribe_tick(self._apply_t)

    # ── 라이프사이클 ────────────────────────────────────────────────────

    def destroy(self) -> None:
        # 공유 클럭에 합류된 상태라면 구독 해제 (공유 클럭은 정지하지 않음)
        if self._active_clock is not self.own_clock:
            self._active_clock.unsubscribe_tick(self._apply_t)
            self._active_clock = self.own_clock
        self.own_clock.stop()
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
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            # session layer tcps를 stage와 맞춰 timeSample 자동 스케일링 방지.
            # (session 기본 24 vs stage 60이면 샘플이 2.5배로 늘어나 t=1이 40%에서 멈춤)
            pxr_stage.GetSessionLayer().timeCodesPerSecond = pxr_stage.GetTimeCodesPerSecond()
            for tc, mesh_map in enumerate(self._st_maps):
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
        i = min(int(idx), n - 2)   # floor 프레임 인덱스
        frac = idx - i              # 보간 비율 [0, 1)
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
