import asyncio
import time
from typing import Callable

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
import omni.kit.app
import omni.timeline
import omni.usd


class UVMixer:

    # ── Configuration ──────────────────────────────────────────────────────────
    _num_slots: int = 5
    _play_duration: float = 2.5
    _dirty_attr: str = "fvli"  # "none" | "fvli"
    _speed: float = 1.0
    _sync_mode: str = "default"  # "default" | "commit" | "viewport_frame"

    # ── State ──────────────────────────────────────────────────────────────────
    _maps: list = [None] * 5
    _t: float = 0.0
    _play_task: object = None
    _subscribers: list = []
    _dirty_cache: dict = {}  # prim_path → dirty attr value (read once at load)

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
    def set_sync_mode(cls, mode: str) -> None:
        if mode in ("default", "commit", "viewport_frame"):
            cls._sync_mode = mode
            print(f"[UVMixer] sync_mode → {mode}")

    @classmethod
    def get_sync_mode(cls) -> str:
        return cls._sync_mode

    @classmethod
    def set_speed(cls, speed: float) -> None:
        cls._speed = max(0.1, float(speed))

    @classmethod
    def get_speed(cls) -> float:
        return cls._speed

    @classmethod
    def set_dirty_attr(cls, attr: str) -> None:
        if attr not in ("none", "fvli", "faceVertexIndices", "faceVertexCounts"):
            return
        cls._dirty_attr = attr
        if any(m is not None for m in cls._maps):
            cls._rebuild_dirty_cache()
            cls.set_t(cls._t)

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
        cls._rebuild_dirty_cache()
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
        loaded = [(i, m) for i, m in enumerate(cls._maps) if m is not None]
        n = len(loaded)
        if n >= 2:
            pxr_stage = omni.usd.get_context().get_stage()
            if pxr_stage:
                idx_f = t * (n - 1)
                idx_a = min(int(idx_f), n - 2)
                alpha = float(idx_f - idx_a)
                _, map_a = loaded[idx_a]
                _, map_b = loaded[idx_a + 1]
                common = set(map_a.keys()) & set(map_b.keys())
                with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
                    with Sdf.ChangeBlock():
                        for prim_path in common:
                            pxr_prim = pxr_stage.GetPrimAtPath(prim_path)
                            if not pxr_prim.IsValid():
                                continue
                            st_a = map_a[prim_path]
                            st_b = map_b[prim_path]
                            lerped = (st_a + (st_b - st_a) * alpha).astype(np.float32)
                            st_pv = UsdGeom.PrimvarsAPI(pxr_prim).GetPrimvar("st")
                            if st_pv and st_pv.GetAttr().IsValid():
                                st_pv.GetAttr().Set(
                                    Vt.Vec2fArray.FromNumpy(np.ascontiguousarray(lerped)),
                                    Usd.TimeCode.Default(),
                                )
                            cls._write_dirty_attr(pxr_prim, prim_path)
        cls._notify(t)

    @classmethod
    def get_t(cls) -> float:
        return cls._t

    @classmethod
    def play(cls, forward: bool = True, loop: bool = False) -> None:
        cls.stop()
        cls._play_task = asyncio.ensure_future(cls._animate(forward, loop))

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
    def duplicate_for_load_test(cls, n: int) -> int:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return 0
        loaded = [(i, m) for i, m in enumerate(cls._maps) if m is not None]
        if not loaded:
            return 0
        orig_paths = sorted({p for _, m in loaded for p in m})
        if not orig_paths:
            return 0

        session = pxr_stage.GetSessionLayer()
        grid_cols = max(1, int(n ** 0.5))
        spacing = 200.0
        added = 0

        with Usd.EditContext(pxr_stage, session):
            for copy_idx in range(1, n):
                col = copy_idx % grid_cols
                row = copy_idx // grid_cols
                group_path = f"/World/LoadTest/copy_{copy_idx:04d}"
                grp = UsdGeom.Xform.Define(pxr_stage, group_path)
                UsdGeom.XformCommonAPI(grp).SetTranslate(
                    Gf.Vec3d(col * spacing, 0.0, row * spacing)
                )
                for mesh_idx, orig_path in enumerate(orig_paths):
                    src_prim = pxr_stage.GetPrimAtPath(orig_path)
                    if not src_prim.IsValid():
                        continue
                    dst_path = f"{group_path}/m{mesh_idx:04d}"
                    dst_mesh = UsdGeom.Mesh.Define(pxr_stage, dst_path)
                    dst_prim = dst_mesh.GetPrim()
                    binding = UsdShade.MaterialBindingAPI(src_prim).GetDirectBinding()
                    mat_path = binding.GetMaterialPath()
                    if mat_path:
                        mat_prim = pxr_stage.GetPrimAtPath(mat_path)
                        if mat_prim.IsValid():
                            UsdShade.MaterialBindingAPI.Apply(dst_prim).Bind(
                                UsdShade.Material(mat_prim)
                            )
                    for attr_name in ("points", "faceVertexCounts", "faceVertexIndices", "normals"):
                        src_attr = src_prim.GetAttribute(attr_name)
                        if not (src_attr and src_attr.IsValid()):
                            continue
                        val = src_attr.Get(Usd.TimeCode.Default())
                        if val is None:
                            ts = src_attr.GetTimeSamples()
                            if ts:
                                val = src_attr.Get(ts[0])
                        if val is not None:
                            dst_prim.CreateAttribute(attr_name, src_attr.GetTypeName()).Set(val)
                    src_st = UsdGeom.PrimvarsAPI(src_prim).GetPrimvar("st")
                    if src_st and src_st.GetAttr().IsValid():
                        val = src_st.ComputeFlattened(Usd.TimeCode.Default())
                        if val is None:
                            ts = src_st.GetTimeSamples()
                            if ts:
                                val = src_st.ComputeFlattened(ts[0])
                        if val is not None:
                            dst_st = UsdGeom.PrimvarsAPI(dst_prim).CreatePrimvar(
                                "st", src_st.GetTypeName(), src_st.GetInterpolation()
                            )
                            dst_st.Set(Vt.Vec2fArray.FromNumpy(
                                np.array(val, dtype=np.float32).reshape(-1, 2)
                            ))
                    for _, m in loaded:
                        if orig_path in m:
                            m[dst_path] = m[orig_path].copy()
                    added += 1

        if added > 0:
            cls._rebuild_dirty_cache()
            cls.set_t(cls._t)
        print(f"[UVMixer] duplicated {added} mesh prims ({n} copies)")
        return added

    @classmethod
    def clear_load_test(cls) -> None:
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            return
        root_path = "/World/LoadTest"
        with Usd.EditContext(pxr_stage, pxr_stage.GetSessionLayer()):
            pxr_stage.RemovePrim(root_path)
        for m in cls._maps:
            if m is None:
                continue
            for k in list(m.keys()):
                if k.startswith(root_path):
                    del m[k]
        if any(m is not None for m in cls._maps):
            cls._rebuild_dirty_cache()
            cls.set_t(cls._t)
        print("[UVMixer] load test prims cleared")

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
    def _rebuild_dirty_cache(cls) -> None:
        """Read dirty attr values from the live stage (root layer). Called at load / dirty_attr change."""
        pxr_stage = omni.usd.get_context().get_stage()
        if pxr_stage is None:
            cls._dirty_cache = {}
            return
        loaded = [(i, m) for i, m in enumerate(cls._maps) if m is not None]
        all_paths = {p for _, m in loaded for p in m}
        cache: dict = {}
        for prim_path in all_paths:
            pxr_prim = pxr_stage.GetPrimAtPath(prim_path)
            if not pxr_prim.IsValid():
                continue
            if cls._dirty_attr == "fvli":
                attr = UsdGeom.Mesh(pxr_prim).GetFaceVaryingLinearInterpolationAttr()
                val = attr.Get() if (attr and attr.IsValid()) else None
                cache[prim_path] = str(val) if val is not None else "cornersPlus1"
            elif cls._dirty_attr in ("faceVertexIndices", "faceVertexCounts"):
                a = pxr_prim.GetAttribute(cls._dirty_attr)
                if a and a.IsValid():
                    v = a.Get(Usd.TimeCode.Default())
                    if v is None:
                        ts = a.GetTimeSamples()
                        if ts:
                            v = a.Get(ts[0])
                    if v is not None:
                        cache[prim_path] = v
        cls._dirty_cache = cache
        print(f"[UVMixer] dirty_cache rebuilt ({len(cache)} prims, attr={cls._dirty_attr})")

    @classmethod
    def _write_dirty_attr(cls, pxr_prim, prim_path: str) -> None:
        """Write dirty attr at Default timecode to force Hydra topology re-eval."""
        if cls._dirty_attr == "none":
            return
        val = cls._dirty_cache.get(prim_path)
        if val is None:
            return
        if cls._dirty_attr == "fvli":
            mesh = UsdGeom.Mesh(pxr_prim)
            fvli = mesh.GetFaceVaryingLinearInterpolationAttr()
            if not (fvli and fvli.IsValid()):
                fvli = mesh.CreateFaceVaryingLinearInterpolationAttr()
            if fvli and fvli.IsValid():
                fvli.Set(val, Usd.TimeCode.Default())
        elif cls._dirty_attr in ("faceVertexIndices", "faceVertexCounts"):
            a = pxr_prim.GetAttribute(cls._dirty_attr)
            if a and a.IsValid():
                a.Set(val, Usd.TimeCode.Default())

    @classmethod
    def _notify(cls, t: float) -> None:
        for cb in list(cls._subscribers):
            try:
                cb(t)
            except Exception as e:
                print(f"[UVMixer] subscriber error: {e}")

    @classmethod
    async def _wait_next_frame(cls) -> None:
        if cls._sync_mode == "viewport_frame":
            try:
                from omni.kit.viewport.utility import next_viewport_frame_async
                vp = None
                try:
                    from omni.kit.viewport.utility import get_active_viewport
                    vp = get_active_viewport()
                except Exception:
                    vp = None
                if vp is not None:
                    await next_viewport_frame_async(vp)
                    return
            except Exception as e:
                print(f"[UVMixer] viewport_frame wait failed, fallback: {e}")
        await omni.kit.app.get_app().next_update_async()

    @classmethod
    async def _animate(cls, forward: bool, loop: bool = False) -> None:
        wall_start = time.monotonic()
        try:
            while True:
                start_t = 0.0 if (loop and forward) else (1.0 if (loop and not forward) else cls._t)
                target = 1.0 if forward else 0.0
                travel = abs(target - start_t)
                pass_start = time.monotonic()

                while True:
                    await cls._wait_next_frame()
                    elapsed = time.monotonic() - pass_start
                    eff_duration = cls._play_duration / max(cls._speed, 0.01)
                    dt_scale = (travel / eff_duration) if (travel > 0.0 and eff_duration > 0.0) else 0.0
                    frac = min(elapsed * dt_scale, travel) if dt_scale > 0 else travel
                    new_t = start_t + (frac if forward else -frac)
                    new_t = max(0.0, min(1.0, new_t))
                    cls.set_t(new_t)
                    if (forward and new_t >= 1.0) or (not forward and new_t <= 0.0):
                        break

                if not loop:
                    break
        except asyncio.CancelledError:
            return
        finally:
            print(f"[UVMixer] animate done, wall={time.monotonic() - wall_start:.2f}s  (target={cls._play_duration / max(cls._speed, 0.01):.2f}s)")
            cls._play_task = None
