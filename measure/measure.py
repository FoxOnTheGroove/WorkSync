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

# Cap on the whole-mesh vertex scan used when no face id is available.
_MAX_SCAN_POINTS = 200_000

# 임시 진단 로그. 연동이 안정되면 False 로 끄면 됩니다.
TRACE = True

_dumped_result_attrs = False


def _trace(msg: str):
    if TRACE:
        print(f"[measure] {msg}")


def _dump_result_attrs(result):
    """Print the raycast result's fields once, to identify the face id name."""
    global _dumped_result_attrs
    if _dumped_result_attrs or not TRACE:
        return
    _dumped_result_attrs = True
    names = [n for n in dir(result) if not n.startswith("_")]
    print(f"[measure] raycast result fields: {names}")
    for name in names:
        try:
            value = getattr(result, name)
        except Exception:
            continue
        if not callable(value):
            print(f"[measure]   {name} = {value!r}")


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
    tab_id: str
    start: SnapPoint
    end: SnapPoint
    length_m: float
    visible: bool = True  # user intent; the active tab gates drawing on top


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

    def __init__(self, viewport_id: str, tab_id: str, viewport_api, overlay: MeasureOverlay):
        self.viewport_id = viewport_id
        self.tab_id = tab_id
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
    _world_cache: dict = {}
    _snap_radius = SNAP_RADIUS_PX
    # viewport id -> (viewport_api, frame, tab_id), from register_vph.
    # ViewportWidget has no ViewportWindow to enumerate or to draw into, so the
    # host supplies both.
    _registered: dict = {}
    _tabs: dict = {}  # tab id -> [viewport id]
    _active_tab = None  # None means "no tab filter", every tab draws
    _maximized: dict = {}  # tab id -> the one viewport id eclipsing its siblings
    _selected = ""  # viewport pick_one defaults to
    # Global. Gates making new measurements only: show/hide/remove/clear keep
    # working while off, and existing lines stay on screen.
    _enabled = True
    # True once the host feeds clicks in. The overlay then never grabs input,
    # so it cannot fight the extension that already owns the mouse.
    _host_input = False
    _hover_seen = False  # has any hover event reached us at all

    # ------------------------------------------------------------------ life

    @classmethod
    def startup(cls):
        cls._started = True

    @classmethod
    def shutdown(cls):
        for state in list(cls._viewports.values()):
            state.overlay.destroy()
        cls._viewports.clear()
        cls._registered.clear()
        cls._tabs.clear()
        cls._maximized.clear()
        cls._active_tab = None
        cls._selected = ""
        cls._lines.clear()
        cls._changed_callbacks.clear()
        cls._mesh_cache.clear()
        cls._world_cache.clear()
        cls._next_line_id = 1
        cls._started = False

    @classmethod
    def _require_started(cls):
        if not cls._started:
            raise RuntimeError("measure extension is not started")

    # --------------------------------------------------------------- enable

    @classmethod
    def set_enabled(cls, enabled: bool):
        """Global. Off means no new picks; everything else keeps working."""
        cls._enabled = bool(enabled)
        if not cls._enabled:
            for viewport_id in list(cls._viewports):
                cls.cancel_pick(viewport_id)

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    # ----------------------------------------------------------- host input

    @classmethod
    def set_host_input(cls, host_input: bool):
        """Hand mouse input over to the host, or take it back.

        While on, the overlay never activates its own click capture; clicks
        arrive only through on_external_click().
        """
        cls._host_input = bool(host_input)
        _trace(f"set_host_input({cls._host_input}) - hover stays on the overlay")
        if cls._host_input:
            for state in cls._viewports.values():
                state.overlay.set_click_active(False)

    @classmethod
    def is_host_input(cls) -> bool:
        return cls._host_input

    @classmethod
    def on_external_click(cls, viewport_id: str, x, y=None, space: str = "ndc"):
        """A click the host captured. Ignored unless that viewport is armed."""
        if not cls._host_input:
            cls.set_host_input(True)  # first external click settles ownership
        state = cls._viewports.get(viewport_id)
        ndc = cls._to_ndc(viewport_id, x, y, space)
        _trace(
            f"on_viewport_click vp='{viewport_id}' raw={x!r},{y!r} space={space} "
            f"-> ndc={ndc} armed={getattr(state, 'armed', None)}"
        )
        if ndc is not None:
            cls._on_click(viewport_id, ndc)

    @classmethod
    def on_external_hover(cls, viewport_id: str, x, y=None, space: str = "ndc"):
        """Cursor moved. Drives the snap marker and the rubber-band preview."""
        ndc = cls._to_ndc(viewport_id, x, y, space)
        if ndc is not None:
            cls._on_hover(viewport_id, ndc)

    @classmethod
    def _to_ndc(cls, viewport_id: str, x, y, space: str):
        """(x, y) 또는 (x, y) 시퀀스를 받습니다. space 는 'ndc' 또는 'pixel'."""
        if y is None:
            try:
                x, y = x[0], x[1]
            except (TypeError, IndexError, KeyError):
                carb.log_warn(f"[measure] cannot read mouse coords: {x!r}")
                return None
        x, y = float(x), float(y)
        if space == "ndc":
            return (x, y)
        state = cls._viewports.get(viewport_id)
        if state is None:
            return None
        width, height = _resolution(state.viewport_api)
        if width <= 0 or height <= 0:
            return None
        # Pixels are measured from the viewport's top-left.
        return ((x / width) * 2.0 - 1.0, 1.0 - (y / height) * 2.0)

    # --------------------------------------------------------- registration

    @classmethod
    def register_vph(cls, vph) -> str:
        """Register one viewport widget host. Returns its viewport id.

        Reads vph.viewport_api.id, vph.tab_id and vph.ui_frame, and builds the
        overlay straight away. Registration is the only way a viewport becomes
        known: there is nothing to discover for a ViewportWidget.
        """
        cls._require_started()
        viewport_api = vph.viewport_api
        viewport_id = str(getattr(viewport_api, "id", "") or "")
        if not viewport_id:
            raise ValueError("vph.viewport_api has no usable id")
        tab_id = str(vph.tab_id)
        frame = vph.ui_frame
        _trace(
            f"register_vph id='{viewport_id}' tab='{tab_id}' "
            f"frame={type(frame).__name__ if frame is not None else None}"
        )
        if frame is None:
            raise ValueError(f"vph '{viewport_id}' has no ui_frame to draw into")

        old = cls._viewports.pop(viewport_id, None)
        if old is not None:  # re-registered, e.g. the tab was rebuilt
            old.overlay.destroy()

        cls._registered[viewport_id] = (viewport_api, frame, tab_id)
        members = cls._tabs.setdefault(tab_id, [])
        if viewport_id not in members:
            members.append(viewport_id)

        overlay = MeasureOverlay(
            viewport_id,
            viewport_api,
            frame,
            on_hover=lambda ndc, v=viewport_id: cls._on_hover(v, ndc),
            on_click=lambda ndc, v=viewport_id: cls._on_click(v, ndc),
        )
        cls._viewports[viewport_id] = _ViewportState(
            viewport_id, tab_id, viewport_api, overlay
        )
        cls._refresh(viewport_id)
        cls._notify()
        return viewport_id

    @classmethod
    def register_tab(cls, tab_id: str, vphs) -> tuple:
        """Register a whole tab at creation time. Returns its viewport ids.

        tab_id is taken from each vph, so it only has to agree with what the
        hosts report; a mismatch is a caller bug and is logged.
        """
        vphs = list(vphs)
        _trace(f"on_tab_created tab='{tab_id}' vph count={len(vphs)}")
        ids = []
        for vph in vphs:
            if str(vph.tab_id) != str(tab_id):
                carb.log_warn(
                    f"[measure] vph reports tab '{vph.tab_id}', expected '{tab_id}'"
                )
            ids.append(cls.register_vph(vph))
        cls._tabs.setdefault(str(tab_id), [])
        # A freshly created tab is the one on screen, so make it active.
        cls.set_active_tab(tab_id)
        _trace(
            f"on_tab_created done tab='{tab_id}' ids={tuple(ids)} "
            f"all tabs={tuple(cls._tabs)} active={cls._active_tab!r}"
        )
        return tuple(ids)

    @classmethod
    def unregister_tab(cls, tab_id: str):
        """Tab closed: drop its viewports and every line drawn in them."""
        tab_id = str(tab_id)
        for viewport_id in list(cls._tabs.get(tab_id, [])):
            cls.unregister_viewport(viewport_id)
        cls._tabs.pop(tab_id, None)
        cls._maximized.pop(tab_id, None)
        if cls._active_tab == tab_id:
            cls._active_tab = None
        cls._notify()

    @classmethod
    def unregister_viewport(cls, viewport_id: str):
        state = cls._viewports.pop(viewport_id, None)
        if state is not None:
            state.overlay.destroy()
        entry = cls._registered.pop(viewport_id, None)
        if entry is not None:
            members = cls._tabs.get(entry[2])
            if members and viewport_id in members:
                members.remove(viewport_id)
        cls._lines = {
            i: ln for i, ln in cls._lines.items() if ln.viewport_id != viewport_id
        }

    # ------------------------------------------------------------------ tabs

    @classmethod
    def set_active_tab(cls, tab_id):
        """Only the active tab draws. None lifts the filter entirely."""
        cls._active_tab = None if tab_id is None else str(tab_id)
        _trace(
            f"on_tab_activated active='{cls._active_tab}' "
            f"members={tuple(cls._tabs.get(cls._active_tab or '', ()))}"
        )
        for viewport_id in list(cls._viewports):
            cls._refresh(viewport_id)
        cls._notify()

    @classmethod
    def get_active_tab(cls):
        return cls._active_tab

    @classmethod
    def list_tabs(cls) -> tuple:
        return tuple(cls._tabs)

    @classmethod
    def set_maximized(cls, viewport_id: str):
        """One viewport eclipses its siblings inside its own tab."""
        tab_id = cls.get_tab_of(viewport_id)
        cls._maximized[tab_id] = viewport_id
        for vp in list(cls._tabs.get(tab_id, [])):
            cls._refresh(vp)

    @classmethod
    def clear_maximized(cls, tab_id: str):
        """Back to the normal grid: every viewport in the tab draws again."""
        tab_id = str(tab_id)
        cls._maximized.pop(tab_id, None)
        for vp in list(cls._tabs.get(tab_id, [])):
            cls._refresh(vp)

    @classmethod
    def get_maximized(cls, tab_id: str):
        return cls._maximized.get(str(tab_id))

    @classmethod
    def set_selected_viewport(cls, viewport_id: str):
        """The viewport pick_one() targets when called without an id."""
        cls._selected = str(viewport_id or "")
        _trace(
            f"on_viewport_selected '{cls._selected}' "
            f"registered={cls._selected in cls._viewports}"
        )
        cls._notify()

    @classmethod
    def get_selected_viewport(cls) -> str:
        return cls._selected

    @classmethod
    def list_viewport_ids(cls, tab_id=None) -> tuple:
        """Every registered viewport id, or only one tab's."""
        if tab_id is not None:
            return tuple(cls._tabs.get(str(tab_id), []))
        return tuple(cls._registered)

    @classmethod
    def get_tab_of(cls, viewport_id: str) -> str:
        entry = cls._registered.get(viewport_id)
        return entry[2] if entry is not None else ""

    # ----------------------------------------------------------------- snap

    @classmethod
    def set_snap_mode(cls, mode: SnapMode):
        cls._snap_mode = SnapMode(mode)

    @classmethod
    def get_snap_mode(cls) -> SnapMode:
        return cls._snap_mode

    @classmethod
    def set_snap_radius(cls, pixels: float):
        """Capture radius in render pixels. Raise it if snapping feels dead."""
        cls._snap_radius = max(1.0, float(pixels))

    @classmethod
    def get_snap_radius(cls) -> float:
        return cls._snap_radius

    @classmethod
    def get_current_snap(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        return state.current_snap if state else None

    # ----------------------------------------------------------------- pick

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None):
        cls._require_started()
        if not cls._enabled:
            carb.log_warn("[measure] pick_one ignored: the tool is disabled")
            return
        viewport_id = viewport_id or cls._selected
        state = cls._viewports.get(viewport_id)
        if state is None:
            carb.log_warn(
                f"[measure] pick_one: no viewport '{viewport_id}'. "
                f"registered: {tuple(cls._viewports) or '(none)'}. "
                f"The host must call MeasureService.on_tab_created(tab_id, vphs) "
                f"and on_viewport_selected(viewport_id)."
            )
            return
        # Points and transforms may have changed since the last pick.
        cls._mesh_cache.clear()
        cls._world_cache.clear()
        state.armed = True
        state.pending = None
        state.on_done = on_done
        if not cls._host_input:
            state.overlay.set_click_active(True)
        if not cls._hover_seen:
            _trace(
                "pick_one: no hover has ever arrived. The snap marker and the "
                "preview line need on_viewport_hover(vp_id, coords) forwarded "
                "when the host owns viewport input. Clicks snap regardless."
            )

    @classmethod
    def cancel_pick(cls, viewport_id=None):
        viewport_id = viewport_id or cls._selected
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        state.armed = False
        state.pending = None
        state.on_done = None
        state.overlay.set_preview(None, None, "")
        state.overlay.set_click_active(False)

    # ---------------------------------------------------------------- lines

    @classmethod
    def get_lines(cls, viewport_id=None, tab_id=None) -> tuple:
        lines = list(cls._lines.values())
        if viewport_id is not None:
            lines = [ln for ln in lines if ln.viewport_id == viewport_id]
        if tab_id is not None:
            lines = [ln for ln in lines if ln.tab_id == str(tab_id)]
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
    def clear(cls, viewport_id=None, tab_id=None):
        doomed = [
            ln
            for ln in cls._lines.values()
            if (viewport_id is None or ln.viewport_id == viewport_id)
            and (tab_id is None or ln.tab_id == str(tab_id))
        ]
        for line in doomed:
            cls._lines.pop(line.id, None)
        for vp in {ln.viewport_id for ln in doomed}:
            cls._refresh(vp)
        cls._notify()

    @classmethod
    def set_visible(cls, visible: bool, line_id=None, viewport_id=None, tab_id=None):
        """Four tiers, most specific first: line, viewport, tab, everything.

        This is user intent only. The active tab gates drawing independently,
        so making something visible here does not show it in an inactive tab.
        """
        if line_id is not None:
            line = cls._lines.get(line_id)
            if line is None:
                return
            line.visible = visible
            cls._refresh(line.viewport_id)
        else:
            if viewport_id is not None:
                targets = [viewport_id]
            elif tab_id is not None:
                targets = list(cls._tabs.get(str(tab_id), []))
            else:
                targets = list(cls._viewports)
            for vp in targets:
                state = cls._viewports.get(vp)
                if state is not None:
                    state.visible = visible
                cls._refresh(vp)
        cls._notify()

    # --------------------------------------------------------------- events

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        return Subscription(cls._changed_callbacks, fn)

    @classmethod
    def _notify(cls):
        _trace(f"notify -> {len(cls._changed_callbacks)} listener(s)")
        for fn in list(cls._changed_callbacks):
            try:
                fn()
            except Exception as exc:  # a bad listener must not break the tool
                carb.log_error(f"[measure] changed callback failed: {exc}")

    # ---------------------------------------------------------------- input

    @classmethod
    def _on_hover(cls, viewport_id: str, ndc):
        state = cls._viewports.get(viewport_id)
        if state is None or not state.armed:
            return  # no raycast per mouse move unless a pick is in progress
        cls._hover_seen = True
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
            _trace("click resolved: nothing hit")
            return
        _trace(
            f"click resolved: kind={snap.kind.name} elem={snap.element_index} "
            f"pos={tuple(round(v, 4) for v in snap.position)}"
        )
        # Show where it landed even when hover never reaches us.
        state.overlay.set_snap_marker(snap)
        if state.pending is None:
            state.pending = snap
            return

        line = Line(
            id=cls._next_line_id,
            viewport_id=state.viewport_id,
            tab_id=state.tab_id,
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
        state.overlay.set_click_active(False)
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
            _trace("snap: raycast miss")
            return None

        _dump_result_attrs(result)

        hit = Gf.Vec3d(*result.hit_position)
        normal = Gf.Vec3d(*getattr(result, "normal", (0.0, 1.0, 0.0)))
        path = result.get_target_usd_path()
        primitive_id = _face_index_of(result)

        surface = SnapPoint(hit, SnapKind.SURFACE, path, primitive_id, -1, normal)
        if cls._snap_mode == SnapMode.NONE:
            _trace("snap: mode is NONE, surface only")
            return surface

        face_index, verts, note = cls._face_vertices(path, primitive_id, hit)
        cursor_px = _ndc_to_px(ndc, state.viewport_api)
        _trace(
            f"snap: path='{path}' id={primitive_id} face={face_index} "
            f"face_verts={len(verts)} cursor_px={cursor_px} "
            f"radius={cls._snap_radius}"
            f"{' | ' + note if note else ''}"
        )

        if verts:
            # Priority is absolute: the first class with any candidate in range
            # wins, distance only breaks ties inside that class.
            for flag, kind in _FLAG_TO_KIND:
                if not (cls._snap_mode & flag):
                    continue
                best = None
                nearest = None
                for index, candidate in _candidates(kind, verts, hit):
                    dist = _pixel_distance(
                        candidate, cursor_px, view, proj, state.viewport_api
                    )
                    if dist is None:
                        continue
                    if nearest is None or dist < nearest:
                        nearest = dist
                    if dist > cls._snap_radius:
                        continue
                    if best is None or dist < best[0]:
                        best = (dist, index, candidate)
                _trace(f"snap:   {kind.name} nearest={nearest} hit={best is not None}")
                if best is not None:
                    return SnapPoint(best[2], kind, path, face_index, best[1], normal)
            return surface

        # No face topology available, usually because the raycast result does
        # not expose a face id on this Kit build. Vertex snapping still works by
        # scanning the whole mesh; edge and mid-point need a face.
        if cls._snap_mode & SnapMode.VERTEX:
            snap = cls._snap_any_vertex(path, cursor_px, view, proj, state)
            if snap is not None:
                return snap
        return surface

    @classmethod
    def _snap_any_vertex(cls, prim_path, cursor_px, view, proj, state):
        """Nearest vertex over the whole mesh, within the snap radius."""
        points = cls._world_points(prim_path)
        if not points:
            return None
        best = None
        for index, candidate in enumerate(points):
            dist = _pixel_distance(candidate, cursor_px, view, proj, state.viewport_api)
            if dist is None or dist > cls._snap_radius:
                continue
            if best is None or dist < best[0]:
                best = (dist, index, candidate)
        _trace(f"snap:   whole-mesh VERTEX over {len(points)} pts hit={best is not None}")
        if best is None:
            return None
        return SnapPoint(best[2], SnapKind.VERTEX, prim_path, -1, best[1])

    @classmethod
    def _world_points(cls, prim_path: str) -> list:
        """Every mesh point in world space. Cached; skips very heavy meshes."""
        key = (_stage_key(omni.usd.get_context().get_stage()), prim_path)
        cached = cls._world_cache.get(key)
        if cached is not None:
            return cached
        entry = cls._mesh_entry(prim_path)
        if entry is None:
            cls._world_cache[key] = []
            return []
        points, _counts, _indices, _offsets, xform = entry
        if len(points) > _MAX_SCAN_POINTS:
            carb.log_warn(
                f"[measure] '{prim_path}' has {len(points)} points, too many to "
                f"scan without a face id; snapping stays on the surface"
            )
            cls._world_cache[key] = []
            return []
        world = [xform.Transform(Gf.Vec3d(p)) for p in points]
        cls._world_cache[key] = world
        return world

    @classmethod
    def _mesh_entry(cls, prim_path: str):
        """(points, counts, indices, offsets, xform) for a mesh prim, cached."""
        stage = omni.usd.get_context().get_stage()
        key = (_stage_key(stage), prim_path)
        entry = cls._mesh_cache.get(key)
        if entry is not None:
            return entry or None
        if stage is None:
            _trace("snap: no stage")
            return None

        prim, why = _resolve_mesh_prim(stage, prim_path)
        if prim is None:
            _trace(f"snap: {why}")
            cls._mesh_cache[key] = ()
            return None

        points = _points_of(prim)
        if points is None:
            _trace(f"snap: '{prim.GetPath()}' has no points")
            cls._mesh_cache[key] = ()
            return None

        # Topology is Mesh-only. Without it, vertex snapping still works and
        # edge / mid-point do not, so keep the entry rather than discard it.
        mesh = UsdGeom.Mesh(prim)
        counts = mesh.GetFaceVertexCountsAttr().Get() if mesh else None
        indices = mesh.GetFaceVertexIndicesAttr().Get() if mesh else None
        if not counts or not indices:
            _trace(
                f"snap: '{prim.GetPath()}' has {len(points)} points but no face "
                f"topology; vertex snapping only"
            )
            counts = indices = None
            offsets = None
        else:
            offsets = [0]
            for c in counts:
                offsets.append(offsets[-1] + c)
        xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        if why:
            _trace(f"snap: {why}")
        entry = (points, counts, indices, offsets, xform)
        cls._mesh_cache[key] = entry
        return entry

    @classmethod
    def _face_vertices(cls, prim_path: str, primitive_id: int, hit=None):
        """Resolve a raycast primitive id to one face's world-space vertices.

        Returns (face_index, verts, reason). The id is not always a USD face
        index: renderers fan-triangulate, so on a quad or n-gon mesh it can be
        a triangle index instead. Both readings are tried and the one the hit
        point actually sits on wins.
        """
        if primitive_id < 0:
            return -1, [], "no face id"
        entry = cls._mesh_entry(prim_path)
        if entry is None:
            return -1, [], "no geometry found for this hit"
        points, counts, indices, offsets, xform = entry
        if counts is None:
            return -1, [], "point-based but not a mesh; vertex snapping only"

        candidates = []
        if primitive_id < len(counts):
            candidates.append(primitive_id)
        as_triangle = _triangle_to_face(counts, primitive_id)
        if as_triangle is not None and as_triangle not in candidates:
            candidates.append(as_triangle)
        if not candidates:
            return (
                -1,
                [],
                f"id {primitive_id} out of range "
                f"(faces={len(counts)}, triangles={_triangle_total(counts)})",
            )

        best = None
        for face in candidates:
            verts = _face_world_verts(points, counts, indices, offsets, xform, face)
            score = _face_score(verts, hit) if hit is not None else 0.0
            if best is None or score < best[0]:
                best = (score, face, verts)
        _, face, verts = best
        note = "" if len(candidates) == 1 else f"chose {face} from {candidates}"
        return face, verts, note

    @classmethod
    def invalidate_mesh_cache(cls, prim_path=None):
        if prim_path is None:
            cls._mesh_cache.clear()
            cls._world_cache.clear()
        else:
            # Caches are keyed by (stage, path), so drop it on every stage.
            for cache in (cls._mesh_cache, cls._world_cache):
                for key in [k for k in cache if k[1] == prim_path]:
                    cache.pop(key, None)

    # ---------------------------------------------------------------- draw

    @classmethod
    def _refresh(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        # Two independent gates: the active tab, and user intent. Hiding the
        # scene also stops gestures, so an inactive tab cannot be clicked into.
        tab_active = cls._active_tab is None or state.tab_id == cls._active_tab
        eclipsed = cls._maximized.get(state.tab_id) not in (None, viewport_id)
        shown = state.visible and tab_active and not eclipsed
        _trace(
            f"refresh vp='{viewport_id}' tab='{state.tab_id}' shown={shown} "
            f"(tab_active={tab_active} eclipsed={eclipsed} user={state.visible})"
        )
        state.overlay.set_scene_visible(shown)
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


def _stage_key(stage) -> str:
    if stage is None:
        return ""
    try:
        return stage.GetRootLayer().identifier
    except Exception:
        return str(id(stage))


def _points_of(prim):
    """Authored points of any point-based prim, or None.

    PointBased rather than Mesh, so Points and BasisCurves work too. Implicit
    gprims (Cube, Sphere, Cylinder, ...) are procedural and have none.
    """
    try:
        attr = UsdGeom.PointBased(prim).GetPointsAttr()
    except Exception:
        return None
    if not attr:
        return None
    points = attr.Get()
    return points if points else None


def _has_points(prim) -> bool:
    return _points_of(prim) is not None


def _descendants(prim):
    """Children including instance proxies, which a plain range skips."""
    try:
        return list(Usd.PrimRange(prim, Usd.TraverseInstanceProxies()))
    except Exception:
        return list(Usd.PrimRange(prim))


def _describe(prim) -> str:
    try:
        kind = prim.GetTypeName() or "<untyped>"
        kids = [f"{c.GetName()}:{c.GetTypeName() or '?'}" for c in prim.GetChildren()]
        return f"type={kind} proxy={prim.IsInstanceProxy()} children={kids[:8]}"
    except Exception as exc:
        return f"<undescribable: {exc}>"


def _resolve_mesh_prim(stage, prim_path):
    """(prim, note). Walks to the prim that actually carries the topology.

    A raycast can report a GeomSubset, an instance proxy or a wrapping Xform
    rather than the mesh itself.
    """
    path = str(prim_path)
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None, (
            f"'{path}' ({type(prim_path).__name__}) not found on "
            f"stage '{_stage_key(stage)}'"
        )
    if _has_points(prim):
        return prim, ""

    detail = _describe(prim)
    parent = prim.GetParent()
    while parent and parent.IsValid() and not parent.IsPseudoRoot():
        if _has_points(parent):
            return parent, f"'{path}' had no points, used ancestor {parent.GetPath()}"
        parent = parent.GetParent()
    meshes = [d for d in _descendants(prim) if _has_points(d)]
    if len(meshes) == 1:
        return meshes[0], f"'{path}' had no points, used child {meshes[0].GetPath()}"
    if meshes:
        return None, f"'{path}' has {len(meshes)} meshes under it, ambiguous | {detail}"
    kind = str(prim.GetTypeName() or "<untyped>")
    if kind in ("Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane"):
        return None, (
            f"'{path}' is an implicit {kind}: procedural, so it has no vertices "
            f"to snap to. Surface only."
        )
    return None, f"'{path}' carries no points, and nothing near it does | {detail}"


def _triangle_total(counts) -> int:
    return sum(max(1, c - 2) for c in counts)


def _triangle_to_face(counts, tri_index: int):
    """Fan triangulation: a face of n verts becomes n-2 triangles."""
    if tri_index < 0:
        return None
    seen = 0
    for face, count in enumerate(counts):
        seen += max(1, count - 2)
        if tri_index < seen:
            return face
    return None


def _face_world_verts(points, counts, indices, offsets, xform, face: int) -> list:
    start = offsets[face]
    end = start + counts[face]
    return [xform.Transform(Gf.Vec3d(points[i])) for i in indices[start:end]]


def _face_score(verts, hit: Gf.Vec3d) -> float:
    """How well the hit sits on this face. Lower is better.

    Distance to the face's plane, plus a penalty when the hit falls outside
    its bounding box. That separates the two readings of a primitive id.
    """
    if len(verts) < 3:
        return float("inf")
    normal = Gf.Cross(verts[1] - verts[0], verts[2] - verts[0])
    length = normal.GetLength()
    plane = abs(Gf.Dot(hit - verts[0], normal / length)) if length > 1e-12 else 0.0
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    diag = max(hi[i] - lo[i] for i in range(3)) or 1.0
    margin = diag * 0.01
    outside = any(hit[i] < lo[i] - margin or hit[i] > hi[i] + margin for i in range(3))
    return plane + (diag if outside else 0.0)


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


def _format_length(meters: float) -> str:
    return f"{meters:.3f} m"
