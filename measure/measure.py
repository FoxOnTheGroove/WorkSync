"""Point-to-point measurement core.

Owns all state. Everything public goes through measure_service.MeasureService.

Snap resolution per click/hover:

    cursor pixel -> Ray -> submit_raycast_query
        -> hit_position / normal / usd_path / face index
        -> candidates built from that face only
             vertex   : each face vertex
             edge     : closest point on each face edge
             midpoint : midpoint of each face edge
        -> project candidates to screen, keep those within SNAP_RADIUS_PX
        -> pick by priority VERTEX > EDGE > MIDPOINT, else fall back to SURFACE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

import carb
import omni.usd
from pxr import Gf, Usd, UsdGeom

from .measure_overlay import MeasureOverlay

# Screen-space snap capture radius. Fixed on purpose: a pixel radius keeps the
# feel identical at every zoom level.
SNAP_RADIUS_PX = 12.0


class SnapKind(IntEnum):
    """Snap class. Higher value wins when several candidates are in range."""

    SURFACE = 0  # fallback, always available, cannot be masked off
    MIDPOINT = 1
    EDGE = 2
    VERTEX = 3


class SnapMode(IntFlag):
    """Which snap classes are active. SURFACE is absent: it is the floor."""

    NONE = 0
    MIDPOINT = 1 << 0
    EDGE = 1 << 1
    VERTEX = 1 << 2
    ALL = MIDPOINT | EDGE | VERTEX


# Highest priority first, so resolution can stop at the first class that hits.
_FLAG_TO_KIND = (
    (SnapMode.VERTEX, SnapKind.VERTEX),
    (SnapMode.EDGE, SnapKind.EDGE),
    (SnapMode.MIDPOINT, SnapKind.MIDPOINT),
)


@dataclass(frozen=True)
class SnapPoint:
    position: Gf.Vec3d
    kind: SnapKind
    prim_path: str = ""
    face_index: int = -1
    element_index: int = -1  # vertex or edge index within the face, -1 for SURFACE
    normal: Gf.Vec3d = field(default_factory=lambda: Gf.Vec3d(0, 1, 0))


@dataclass
class Line:
    id: int
    viewport_id: str
    start: SnapPoint
    end: SnapPoint
    length_m: float
    visible: bool = True


class Subscription:
    """Handle returned by subscribe_changed. Dropping it unsubscribes."""

    def __init__(self, store: list, fn):
        self._store = store
        self._fn = fn
        store.append(fn)

    def unsubscribe(self):
        if self._fn is not None and self._fn in self._store:
            self._store.remove(self._fn)
        self._fn = None

    def __del__(self):
        try:
            self.unsubscribe()
        except Exception:
            pass


class _ViewportState:
    """Per-viewport tool state. One of these per enabled viewport."""

    def __init__(self, viewport_id: str, viewport_api, overlay: MeasureOverlay):
        self.viewport_id = viewport_id
        self.viewport_api = viewport_api
        self.overlay = overlay
        self.armed = False  # pick_one issued, waiting for clicks
        self.pending: SnapPoint | None = None  # first click landed
        self.on_done = None
        self.current_snap: SnapPoint | None = None
        self.visible = True


class MeasureCore:
    """Singleton. All state lives here as class attributes."""

    _started = False
    _viewports: dict = {}
    _lines: dict = {}
    _next_line_id = 1
    _snap_mode = SnapMode.ALL  # global, not per viewport
    _changed_callbacks: list = []
    _mesh_cache: dict = {}

    # ------------------------------------------------------------------ life

    @classmethod
    def startup(cls):
        cls._started = True

    @classmethod
    def shutdown(cls):
        for state in list(cls._viewports.values()):
            state.overlay.destroy()
        cls._viewports.clear()
        cls._lines.clear()
        cls._changed_callbacks.clear()
        cls._mesh_cache.clear()
        cls._next_line_id = 1
        cls._started = False

    @classmethod
    def _require_started(cls):
        if not cls._started:
            raise RuntimeError("measure extension is not started")

    # --------------------------------------------------------------- enable

    @classmethod
    def set_enabled(cls, viewport_id: str, enabled: bool):
        cls._require_started()
        if enabled:
            if viewport_id in cls._viewports:
                return
            viewport_api = _resolve_viewport_api(viewport_id)
            if viewport_api is None:
                carb.log_warn(f"[measure] unknown viewport '{viewport_id}'")
                return
            overlay = MeasureOverlay(
                viewport_id,
                viewport_api,
                on_hover=lambda ndc, v=viewport_id: cls._on_hover(v, ndc),
                on_click=lambda ndc, v=viewport_id: cls._on_click(v, ndc),
            )
            cls._viewports[viewport_id] = _ViewportState(viewport_id, viewport_api, overlay)
            cls._refresh(viewport_id)
        else:
            state = cls._viewports.pop(viewport_id, None)
            if state is not None:
                state.overlay.destroy()

    @classmethod
    def is_enabled(cls, viewport_id: str) -> bool:
        return viewport_id in cls._viewports

    # ----------------------------------------------------------------- snap

    @classmethod
    def set_snap_mode(cls, mode: SnapMode):
        cls._snap_mode = SnapMode(mode)

    @classmethod
    def get_snap_mode(cls) -> SnapMode:
        return cls._snap_mode

    @classmethod
    def get_current_snap(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        return state.current_snap if state else None

    # ----------------------------------------------------------------- pick

    @classmethod
    def pick_one(cls, viewport_id: str, on_done=None):
        cls._require_started()
        state = cls._viewports.get(viewport_id)
        if state is None:
            carb.log_warn(f"[measure] pick_one on disabled viewport '{viewport_id}'")
            return
        # Points and transforms may have changed since the last pick.
        cls._mesh_cache.clear()
        state.armed = True
        state.pending = None
        state.on_done = on_done

    @classmethod
    def cancel_pick(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        state.armed = False
        state.pending = None
        state.on_done = None
        state.overlay.set_preview(None, None, "")

    # ---------------------------------------------------------------- lines

    @classmethod
    def get_lines(cls, viewport_id=None) -> tuple:
        lines = cls._lines.values()
        if viewport_id is not None:
            lines = [ln for ln in lines if ln.viewport_id == viewport_id]
        return tuple(lines)

    @classmethod
    def remove(cls, line_id: int) -> bool:
        line = cls._lines.pop(line_id, None)
        if line is None:
            return False
        cls._refresh(line.viewport_id)
        cls._notify()
        return True

    @classmethod
    def clear(cls, viewport_id=None):
        if viewport_id is None:
            touched = {ln.viewport_id for ln in cls._lines.values()}
            cls._lines.clear()
        else:
            touched = {viewport_id}
            cls._lines = {
                i: ln for i, ln in cls._lines.items() if ln.viewport_id != viewport_id
            }
        for vp in touched:
            cls._refresh(vp)
        cls._notify()

    @classmethod
    def set_visible(cls, visible: bool, line_id=None, viewport_id=None):
        """Three tiers: one line, one viewport, or everything."""
        if line_id is not None:
            line = cls._lines.get(line_id)
            if line is None:
                return
            line.visible = visible
            cls._refresh(line.viewport_id)
        elif viewport_id is not None:
            state = cls._viewports.get(viewport_id)
            if state is not None:
                state.visible = visible
            cls._refresh(viewport_id)
        else:
            for state in cls._viewports.values():
                state.visible = visible
            for vp in list(cls._viewports):
                cls._refresh(vp)
        cls._notify()

    # --------------------------------------------------------------- events

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        return Subscription(cls._changed_callbacks, fn)

    @classmethod
    def _notify(cls):
        for fn in list(cls._changed_callbacks):
            try:
                fn()
            except Exception as exc:  # a bad listener must not break the tool
                carb.log_error(f"[measure] changed callback failed: {exc}")

    # ---------------------------------------------------------------- input

    @classmethod
    def _on_hover(cls, viewport_id: str, ndc):
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        cls._resolve_snap(state, ndc, lambda snap: cls._apply_hover(state, snap))

    @classmethod
    def _apply_hover(cls, state, snap):
        state.current_snap = snap
        state.overlay.set_snap_marker(snap)
        if state.pending is not None and snap is not None:
            length = cls._length_m(state.pending.position, snap.position)
            state.overlay.set_preview(
                state.pending.position, snap.position, _format_length(length)
            )

    @classmethod
    def _on_click(cls, viewport_id: str, ndc):
        state = cls._viewports.get(viewport_id)
        if state is None or not state.armed:
            return
        cls._resolve_snap(state, ndc, lambda snap: cls._apply_click(state, snap))

    @classmethod
    def _apply_click(cls, state, snap):
        if snap is None:  # clicked empty space, nothing to anchor to
            return
        if state.pending is None:
            state.pending = snap
            return

        line = Line(
            id=cls._next_line_id,
            viewport_id=state.viewport_id,
            start=state.pending,
            end=snap,
            length_m=cls._length_m(state.pending.position, snap.position),
        )
        cls._next_line_id += 1
        cls._lines[line.id] = line

        on_done = state.on_done
        state.armed = False
        state.pending = None
        state.on_done = None
        state.overlay.set_preview(None, None, "")
        cls._refresh(state.viewport_id)
        cls._notify()

        if on_done is not None:
            try:
                on_done(line)
            except Exception as exc:
                carb.log_error(f"[measure] on_done callback failed: {exc}")

    # ----------------------------------------------------------------- snap

    @classmethod
    def _resolve_snap(cls, state, ndc, on_result):
        """Fire a raycast, then reduce the hit to a snapped point."""
        try:
            import omni.kit.raycast.query as rq
        except ImportError:
            carb.log_error("[measure] omni.kit.raycast.query is not available")
            on_result(None)
            return

        view, proj = _camera_matrices(state.viewport_api)
        if view is None:
            on_result(None)
            return
        origin, direction = _ndc_to_ray(ndc, view, proj)

        def _on_hit(_ray, result):
            on_result(cls._snap_from_hit(state, result, ndc, view, proj))

        iface = rq.acquire_raycast_query_interface()
        iface.submit_raycast_query(
            rq.Ray(
                (origin[0], origin[1], origin[2]),
                (direction[0], direction[1], direction[2]),
            ),
            _on_hit,
        )

    @classmethod
    def _snap_from_hit(cls, state, result, ndc, view, proj):
        if not getattr(result, "valid", False):
            return None

        hit = Gf.Vec3d(*result.hit_position)
        normal = Gf.Vec3d(*getattr(result, "normal", (0.0, 1.0, 0.0)))
        path = result.get_target_usd_path()
        face_index = _face_index_of(result)

        surface = SnapPoint(hit, SnapKind.SURFACE, path, face_index, -1, normal)
        if cls._snap_mode == SnapMode.NONE or face_index < 0:
            return surface

        verts = cls._face_vertices(path, face_index)
        if not verts:
            return surface

        cursor_px = _ndc_to_px(ndc, state.viewport_api)

        # Priority is absolute: the first class with any candidate in range wins,
        # distance only breaks ties inside that class.
        for flag, kind in _FLAG_TO_KIND:
            if not (cls._snap_mode & flag):
                continue
            best = None
            for index, candidate in _candidates(kind, verts, hit):
                dist = _pixel_distance(candidate, cursor_px, view, proj, state.viewport_api)
                if dist is None or dist > SNAP_RADIUS_PX:
                    continue
                if best is None or dist < best[0]:
                    best = (dist, index, candidate)
            if best is not None:
                return SnapPoint(best[2], kind, path, face_index, best[1], normal)

        return surface

    @classmethod
    def _face_vertices(cls, prim_path: str, face_index: int) -> list:
        """World-space vertices of one face. Cached per prim."""
        entry = cls._mesh_cache.get(prim_path)
        if entry is None:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return []
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsA(UsdGeom.Mesh):
                return []
            mesh = UsdGeom.Mesh(prim)
            points = mesh.GetPointsAttr().Get()
            counts = mesh.GetFaceVertexCountsAttr().Get()
            indices = mesh.GetFaceVertexIndicesAttr().Get()
            if not points or not counts or not indices:
                return []
            offsets = [0]
            for c in counts:
                offsets.append(offsets[-1] + c)
            xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default()
            )
            entry = (points, counts, indices, offsets, xform)
            cls._mesh_cache[prim_path] = entry

        points, counts, indices, offsets, xform = entry
        if face_index < 0 or face_index >= len(counts):
            return []
        start = offsets[face_index]
        end = start + counts[face_index]
        return [xform.Transform(Gf.Vec3d(points[i])) for i in indices[start:end]]

    @classmethod
    def invalidate_mesh_cache(cls, prim_path=None):
        if prim_path is None:
            cls._mesh_cache.clear()
        else:
            cls._mesh_cache.pop(prim_path, None)

    # ---------------------------------------------------------------- draw

    @classmethod
    def _refresh(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        state.overlay.set_scene_visible(state.visible)
        lines = [
            ln
            for ln in cls._lines.values()
            if ln.viewport_id == viewport_id and ln.visible
        ]
        state.overlay.set_lines(lines, _format_length)

    @classmethod
    def _length_m(cls, a: Gf.Vec3d, b: Gf.Vec3d) -> float:
        stage = omni.usd.get_context().get_stage()
        mpu = UsdGeom.GetStageMetersPerUnit(stage) if stage else 1.0
        return float((b - a).GetLength()) * (mpu or 1.0)


# --------------------------------------------------------------------- utils


def _candidates(kind: SnapKind, verts: list, hit: Gf.Vec3d):
    """Yield (element_index, world position) for one snap class."""
    n = len(verts)
    if kind == SnapKind.VERTEX:
        for i, v in enumerate(verts):
            yield i, v
    elif kind == SnapKind.MIDPOINT:
        for i in range(n):
            yield i, (verts[i] + verts[(i + 1) % n]) * 0.5
    elif kind == SnapKind.EDGE:
        for i in range(n):
            a, b = verts[i], verts[(i + 1) % n]
            ab = b - a
            denom = ab.GetLength() ** 2
            if denom <= 1e-12:
                continue
            t = max(0.0, min(1.0, Gf.Dot(hit - a, ab) / denom))
            yield i, a + ab * t


def _face_index_of(result) -> int:
    """primitive_id naming differs between Kit builds; degrade to surface-only."""
    for name in ("primitive_id", "primitiveId", "face_id"):
        value = getattr(result, name, None)
        if isinstance(value, int):
            return value
    getter = getattr(result, "get_primitive_id", None)
    if callable(getter):
        try:
            return int(getter())
        except Exception:
            pass
    return -1


def _camera_matrices(viewport_api):
    try:
        return Gf.Matrix4d(viewport_api.view), Gf.Matrix4d(viewport_api.projection)
    except Exception as exc:
        carb.log_warn(f"[measure] cannot read camera matrices: {exc}")
        return None, None


def _ndc_to_ray(ndc, view: Gf.Matrix4d, proj: Gf.Matrix4d):
    """USD is row-vector: clip = world * view * proj."""
    inv = (view * proj).GetInverse()
    near = inv.Transform(Gf.Vec3d(ndc[0], ndc[1], -1.0))
    far = inv.Transform(Gf.Vec3d(ndc[0], ndc[1], 1.0))
    return near, (far - near).GetNormalized()


def _resolution(viewport_api):
    try:
        res = viewport_api.resolution
        return float(res[0]), float(res[1])
    except Exception:
        return 1920.0, 1080.0


def _ndc_to_px(ndc, viewport_api):
    w, h = _resolution(viewport_api)
    return ((ndc[0] * 0.5 + 0.5) * w, (1.0 - (ndc[1] * 0.5 + 0.5)) * h)


def _pixel_distance(world: Gf.Vec3d, cursor_px, view, proj, viewport_api):
    """None when the point is off screen or behind the camera."""
    clip = (view * proj).Transform(world)
    if abs(clip[2]) > 1.0:
        return None
    px = _ndc_to_px((clip[0], clip[1]), viewport_api)
    return ((px[0] - cursor_px[0]) ** 2 + (px[1] - cursor_px[1]) ** 2) ** 0.5


def _resolve_viewport_api(viewport_id: str):
    from omni.kit.viewport.utility import get_viewport_from_window_name

    try:
        return get_viewport_from_window_name(viewport_id)
    except Exception as exc:
        carb.log_warn(f"[measure] viewport lookup failed for '{viewport_id}': {exc}")
        return None


def _format_length(meters: float) -> str:
    return f"{meters:.3f} m"
