"""Point-to-point measurement core.

Owns all state. Everything public goes through measure_service.MeasureService.

Snap resolution per click/hover:

    cursor pixel -> Ray -> submit_raycast_query
        -> hit_position / normal / usd_path
        -> the hit mesh's outline: edges used by a single face, and the
           boundary vertices where that outline turns a corner
             vertex : each outline corner
             edge   : closest point on each outline edge
        -> project candidates to screen, keep those within the snap radius
        -> pick by priority VERTEX > EDGE, else fall back to SURFACE

Interior vertices and interior edges are deliberately not snappable: a
subdivided plane should offer its four corners, not its whole grid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

import carb
import numpy as np
import omni.usd
from pxr import Gf, Usd, UsdGeom

from .measure_overlay import MeasureOverlay

# Screen-space snap capture radius. Fixed on purpose: a pixel radius keeps the
# feel identical at every zoom level.
SNAP_RADIUS_PX = 12.0

# How many faces nearest the hit get scored when searching for its face.
_FACE_SHORTLIST = 64

# A hit this close to a face's plane and inside its bounds is on that face.
_ON_FACE_EPS = 1e-6

# Cosine above which a boundary vertex counts as a corner rather than a point
# partway along a straight run of the outline. -cos(15 degrees).
_CORNER_COS = -0.9659

# Points closer than this fraction of the mesh's extent are the same point.
_WELD_TOLERANCE = 1e-5

# How far from the struck point a candidate may sit, as a multiple of the snap
# radius converted to world units there. Keeps the far side of a solid out.
_HIT_REACH = 3.0

# Two faces meeting at more than this angle form a crease worth snapping to.
# Below it they read as one smooth surface. cos(30 degrees); a cylinder wall
# only reaches this when it has fewer than 12 sides.
_CREASE_COS = 0.8660

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
    EDGE = 1
    VERTEX = 2


class SnapMode(IntFlag):
    """Which snap classes are active. SURFACE is absent: it is the floor."""

    NONE = 0
    EDGE = 1 << 0
    VERTEX = 1 << 1
    ALL = EDGE | VERTEX


# Highest priority first, so resolution can stop at the first class that hits.
_FLAG_TO_KIND = (
    (SnapMode.VERTEX, SnapKind.VERTEX),
    (SnapMode.EDGE, SnapKind.EDGE),
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

        surface = SnapPoint(hit, SnapKind.SURFACE, path, -1, -1, normal)
        if cls._snap_mode == SnapMode.NONE:
            _trace("snap: mode is NONE, surface only")
            return surface

        time = _time_of(state.viewport_api)
        geom = cls._mesh_entry(path, hit, time)
        if geom is None:
            return surface
        outline = geom.boundary
        cursor_px = np.asarray(_ndc_to_px(ndc, state.viewport_api))
        view_proj = _matrix_np(view * proj)
        width, height = _resolution(state.viewport_api)

        # The screen radius alone cannot tell front from back: the far side of a
        # cylinder projects right under the near side. A candidate must also lie
        # near the point the ray actually struck, so convert the pixel radius
        # into a world one at that depth.
        reach = cls._snap_radius * _HIT_REACH / _pixel_scale(
            view, proj, hit, geom.extent, state.viewport_api
        )
        _trace(
            f"snap: path='{path}' corners={len(outline.corners)} "
            f"edges={len(outline.edge_a)} cursor_px={cursor_px.round(1)} "
            f"radius={cls._snap_radius} reach={reach:.4g} time={time}"
        )

        # Priority is absolute: the first class with a candidate in range wins,
        # distance only breaks ties inside that class.
        for flag, kind in _FLAG_TO_KIND:
            if not (cls._snap_mode & flag):
                continue
            if kind == SnapKind.VERTEX:
                points = outline.corners
                if not len(points):
                    continue
                pixels, valid = _project_px(points, view_proj, width, height)
                screen = np.linalg.norm(pixels - cursor_px, axis=1)
            else:
                if not len(outline.edge_a):
                    continue
                points, screen, valid = _nearest_on_edges(
                    outline.edge_a, outline.edge_b, cursor_px, view_proj, width, height
                )

            near_hit = np.linalg.norm(points - _as_np(hit), axis=1) <= reach
            usable = valid & near_hit
            if not usable.any():
                _trace(f"snap:   {kind.name} none within reach of the hit")
                continue
            distances = np.where(usable, screen, np.inf)
            index = int(np.argmin(distances))
            nearest = float(distances[index])
            _trace(
                f"snap:   {kind.name} {int(usable.sum())}/{len(points)} in reach "
                f"nearest={nearest:.1f} hit={nearest <= cls._snap_radius}"
            )
            if nearest <= cls._snap_radius:
                return SnapPoint(
                    Gf.Vec3d(*points[index]), kind, path, -1, index, normal
                )
        return surface


    @classmethod
    def _mesh_entry(cls, prim_path: str, hit=None, time=None):
        """(points, counts, indices, offsets, xform) for the hit geometry.

        Cached by the prim it resolves to, never by the path that was hit:
        one path can cover several meshes and the hit decides which.
        """
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            _trace("snap: no stage")
            return None

        prim, why = _resolve_mesh_prim(stage, prim_path, hit, time)
        if why:
            _trace(f"snap: {why}")
        if prim is None:
            return None

        key = (_stage_key(stage), str(prim.GetPath()), str(time))
        entry = cls._mesh_cache.get(key)
        if entry is not None:
            return entry or None

        entry = _build_entry(prim, time)
        if entry is None:
            _trace(f"snap: '{prim.GetPath()}' has no points")
            cls._mesh_cache[key] = ()
            return None
        if entry.counts is None:
            mesh = UsdGeom.Mesh(prim)
            counts_attr = mesh.GetFaceVertexCountsAttr() if mesh else None
            _trace(
                f"snap: '{prim.GetPath()}' has {len(entry.points)} points but no "
                f"face topology; vertex snapping only. "
                f"faceVertexCounts: {_attr_report(counts_attr)}"
            )
        cls._mesh_cache[key] = entry
        return entry


    @classmethod
    def invalidate_mesh_cache(cls, prim_path=None):
        if prim_path is None:
            cls._mesh_cache.clear()
        else:
            # Keyed by (stage, path, time), so drop every matching entry.
            for key in [k for k in cls._mesh_cache if k[1] == prim_path]:
                cls._mesh_cache.pop(key, None)

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


def _stage_key(stage) -> str:
    if stage is None:
        return ""
    try:
        return stage.GetRootLayer().identifier
    except Exception:
        return str(id(stage))


def _time_of(viewport_api):
    """The time the viewport is showing, as a Usd.TimeCode."""
    time = getattr(viewport_api, "time", None)
    if time is not None:
        return time
    try:
        import omni.timeline

        stage = omni.usd.get_context().get_stage()
        seconds = omni.timeline.get_timeline_interface().get_current_time()
        return Usd.TimeCode(seconds * stage.GetTimeCodesPerSecond())
    except Exception:
        return Usd.TimeCode.Default()


def _times_to_try(attr, time):
    """Current time first, then default, then the first authored sample.

    Points are often authored only at time samples, so reading at the default
    time code comes back empty even though the mesh clearly has geometry.
    """
    times = []
    if time is not None:
        times.append(time)
    times.append(Usd.TimeCode.Default())
    times.append(Usd.TimeCode(0.0))
    try:
        samples = attr.GetTimeSamples()
        if samples:
            times.append(Usd.TimeCode(samples[0]))
    except Exception:
        pass
    return times


def _attr_value(attr, time=None):
    """Read an attribute, trying the times it might actually be authored at."""
    if not attr:
        return None
    for when in _times_to_try(attr, time):
        value = attr.Get(when)
        if value:
            return value
    return None


def _points_of(prim, time=None):
    """Points of any point-based prim, or None.

    PointBased rather than Mesh, so Points and BasisCurves work too. Implicit
    gprims (Cube, Sphere, Cylinder, ...) are procedural and have none.
    """
    try:
        return _attr_value(UsdGeom.PointBased(prim).GetPointsAttr(), time)
    except Exception:
        return None


def _has_points(prim, time=None) -> bool:
    return _points_of(prim, time) is not None


def _descendants(prim):
    """Children including instance proxies, which a plain range skips."""
    try:
        return list(Usd.PrimRange(prim, Usd.TraverseInstanceProxies()))
    except Exception:
        return list(Usd.PrimRange(prim))


def _as_np(vec) -> np.ndarray:
    return np.asarray([vec[0], vec[1], vec[2]], dtype=float)


def _face_normals(position, index, nxt, starts) -> np.ndarray:
    """One unit normal per face, by Newell's method so n-gons work.

    Only the angle between two faces matters here, so which way the winding
    sends them is irrelevant.
    """
    cross = np.cross(position[index], position[nxt])
    normals = np.add.reduceat(cross, starts, axis=0)
    lengths = np.linalg.norm(normals, axis=1)
    return normals / np.where(lengths > 1e-20, lengths, 1.0)[:, None]


def _feature_edges(index, nxt, counts, normals):
    """(edges, total) - edges worth snapping to, as welded index pairs.

    An edge qualifies two ways. It is a boundary, used by a single face. Or it
    is a crease: the faces either side meet at a sharp angle. Creases matter
    because a solid has no boundary at all - a capped cylinder's rim is shared
    by the cap and the wall, yet it is exactly the circle you want to measure
    to, while the wall's own vertical edges are nearly flat and are not.
    """
    pairs = np.sort(np.stack([index, nxt], axis=1), axis=1)
    face_of = np.repeat(np.arange(len(counts)), counts)
    keep = pairs[:, 0] != pairs[:, 1]  # a face may fold back on itself
    pairs, face_of = pairs[keep], face_of[keep]
    if len(pairs) == 0:
        return np.empty((0, 2), dtype=np.int64), 0

    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    pairs, face_of = pairs[order], face_of[order]
    fresh = np.ones(len(pairs), dtype=bool)
    fresh[1:] = np.any(pairs[1:] != pairs[:-1], axis=1)
    group = np.flatnonzero(fresh)
    sizes = np.diff(np.append(group, len(pairs)))

    boundary = group[sizes == 1]
    tangled = group[sizes > 2]  # non-manifold, always interesting
    shared = group[sizes == 2]
    creased = shared
    if len(shared):
        left, right = normals[face_of[shared]], normals[face_of[shared + 1]]
        cosine = np.abs(np.einsum("ij,ij->i", left, right))
        creased = shared[cosine < _CREASE_COS]

    chosen = np.concatenate([boundary, creased, tangled])
    return pairs[chosen], len(group)


def _nearest_on_edges(starts, ends, cursor_px, view_proj, width, height):
    """(points, screen distances, usable) for edges against a cursor.

    Solved on screen, not in space: what matters is the point of the edge that
    appears under the cursor. Picking the point nearest the hit in 3D instead
    put the answer somewhere else along the edge and measured its distance from
    there, which is why edges felt unreliable to grab.
    """
    px_a, ok_a = _project_px(starts, view_proj, width, height)
    px_b, ok_b = _project_px(ends, view_proj, width, height)

    spans = px_b - px_a
    lengths = np.einsum("ij,ij->i", spans, spans)
    safe = np.where(lengths > 1e-12, lengths, 1.0)
    t = np.einsum("ij,ij->i", cursor_px - px_a, spans) / safe
    t = np.clip(np.where(lengths > 1e-12, t, 0.0), 0.0, 1.0)

    screen = np.linalg.norm(px_a + spans * t[:, None] - cursor_px, axis=1)
    points = starts + (ends - starts) * t[:, None]  # same fraction along in 3D
    return points, screen, ok_a & ok_b


def _pixel_scale(view, proj, point, extent, viewport_api) -> float:
    """Pixels per world unit at `point`, so a screen radius becomes a real one."""
    step = max(extent, 1e-9) * 1e-3
    across = _as_np(view.GetInverse().TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)))
    pair = np.stack([_as_np(point), _as_np(point) + across * step])
    width, height = _resolution(viewport_api)
    pixels, valid = _project_px(pair, _matrix_np(view * proj), width, height)
    if not valid.all():
        return 1.0
    moved = float(np.linalg.norm(pixels[1] - pixels[0]))
    return moved / step if moved > 1e-9 else 1.0


def _matrix_np(matrix) -> np.ndarray:
    return np.array([[matrix[r][c] for c in range(4)] for r in range(4)], dtype=float)


def _project_px(world: np.ndarray, view_proj: np.ndarray, width, height):
    """(pixels, valid) for many world points at once.

    The per-point Python version made a hover on a dense mesh unusable, since
    it ran a 4x4 transform per point per mouse move.
    """
    count = len(world)
    homogeneous = np.hstack([world, np.ones((count, 1))])
    clip = homogeneous @ view_proj  # USD is row-vector: p * M
    w = clip[:, 3]
    valid = np.abs(w) > 1e-12
    ndc = np.zeros((count, 3))
    np.divide(clip[:, :3], w[:, None], out=ndc, where=valid[:, None])
    pixels = np.empty((count, 2))
    pixels[:, 0] = (ndc[:, 0] * 0.5 + 0.5) * width
    pixels[:, 1] = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * height
    return pixels, valid & (np.abs(ndc[:, 2]) <= 1.0)


class _Outline:
    """A mesh's snappable corners and feature edges, in world space."""

    def __init__(self, corners, edge_a, edge_b):
        self.corners = corners
        self.edge_a = edge_a
        self.edge_b = edge_b

    @classmethod
    def empty(cls):
        return cls(np.empty((0, 3)), np.empty((0, 3)), np.empty((0, 3)))


class _Geom:
    """Geometry of one prim, prepared for repeated screen-space queries."""

    def __init__(self, points, counts, indices, offsets, xform):
        self.points = points
        self.counts = counts
        self.indices = indices
        self.offsets = offsets
        self.xform = xform
        local = np.asarray(points, dtype=float)
        matrix = _matrix_np(xform)
        self.world = np.hstack([local, np.ones((len(local), 1))]) @ matrix
        self.world = self.world[:, :3] / self.world[:, 3:4]
        span = self.world.max(axis=0) - self.world.min(axis=0)
        self.extent = float(np.linalg.norm(span)) or 1.0
        self._centroids = None
        self._boundary = None

    def _topology(self):
        """Welded (position, index, next-in-face, face starts).

        Each vertex is paired with the next one in its own face, wrapping at
        that face's end rather than running on into the next face.
        """
        position, remap = self._weld()
        index = remap[np.asarray(self.indices, dtype=np.int64)]
        starts = np.asarray(self.offsets[:-1], dtype=np.int64)
        ends = np.asarray(self.offsets[1:], dtype=np.int64) - 1
        nxt = np.empty_like(index)
        nxt[:-1] = index[1:]
        nxt[ends] = index[starts]
        return position, index, nxt, starts

    @property
    def centroids(self):
        """One point per face, for narrowing a face search cheaply."""
        if self._centroids is None and self.counts is not None:
            index = np.asarray(self.indices, dtype=np.int64)
            starts = np.asarray(self.offsets[:-1], dtype=np.int64)
            sizes = np.asarray(self.counts, dtype=np.int64)
            sums = np.add.reduceat(self.world[index], starts, axis=0)
            self._centroids = sums / sizes[:, None]
        return self._centroids

    def face_verts(self, face: int) -> list:
        start = self.offsets[face]
        end = start + self.counts[face]
        return [Gf.Vec3d(*self.world[i]) for i in self.indices[start:end]]

    @property
    def boundary(self):
        """(corner points, outline edge starts, outline edge ends) in world space.

        Only the outline is snappable. An edge shared by two faces is interior,
        and a boundary vertex whose two edges run straight through it lies
        partway along the outline rather than at a corner of it.
        """
        if self._boundary is None:
            self._boundary = self._find_boundary()
        return self._boundary

    def _weld(self):
        """Merge points that sit at the same position, returning (pos, remap).

        Exported meshes routinely repeat a point per face. Left alone, an
        interior edge shows up as two different index pairs, each used by one
        face, so every edge looks like an outline and every vertex like a
        corner.
        """
        world = self.world
        tolerance = self.extent * _WELD_TOLERANCE
        keys = np.round(world / tolerance).astype(np.int64)
        _, first, remap, counts = np.unique(
            keys, axis=0, return_index=True, return_inverse=True, return_counts=True
        )
        remap = remap.ravel()
        # Average each group so a welded point sits at their common position.
        position = np.stack(
            [np.bincount(remap, world[:, axis], len(first)) for axis in range(3)],
            axis=1,
        ) / counts[:, None]
        return position, remap

    def _find_boundary(self):
        if self.counts is None:
            return _Outline.empty()

        position, index, nxt, starts = self._topology()
        normals = _face_normals(position, index, nxt, starts)
        edges, counted = _feature_edges(
            index, nxt, np.asarray(self.counts, dtype=np.int64), normals
        )
        _trace(
            f"snap:   {len(self.world)} pts welded to {len(position)}: "
            f"{len(edges)} feature of {counted} edges"
        )
        if len(edges) == 0:
            return _Outline.empty()

        neighbours = {}
        for a, b in edges:
            neighbours.setdefault(int(a), []).append(int(b))
            neighbours.setdefault(int(b), []).append(int(a))

        corners = []
        for vertex, around in neighbours.items():
            if len(around) != 2:
                corners.append(vertex)  # junction or dangling end
                continue
            here = position[vertex]
            first = position[around[0]] - here
            second = position[around[1]] - here
            n1, n2 = np.linalg.norm(first), np.linalg.norm(second)
            if n1 < 1e-12 or n2 < 1e-12:
                continue
            # Running straight through means the directions oppose, cos near -1.
            if float(np.dot(first / n1, second / n2)) > _CORNER_COS:
                corners.append(vertex)

        return _Outline(
            corners=position[np.asarray(sorted(corners), dtype=np.int64)]
            if corners
            else np.empty((0, 3)),
            edge_a=position[edges[:, 0]],
            edge_b=position[edges[:, 1]],
        )


def _build_entry(prim, time=None):
    """A _Geom for the prim, or None. counts is None without face topology."""
    points = _points_of(prim, time)
    if points is None:
        return None
    # Topology gets the same time treatment as the points: a mesh whose points
    # are time sampled almost always has its topology authored the same way,
    # and reading it at the default time code silently loses every face.
    mesh = UsdGeom.Mesh(prim)
    counts = _attr_value(mesh.GetFaceVertexCountsAttr(), time) if mesh else None
    indices = _attr_value(mesh.GetFaceVertexIndicesAttr(), time) if mesh else None
    offsets = None
    if counts and indices:
        offsets = [0]
        for c in counts:
            offsets.append(offsets[-1] + c)
    else:
        counts = indices = None
    xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
        time if time is not None else Usd.TimeCode.Default()
    )
    try:
        return _Geom(points, counts, indices, offsets, xform)
    except Exception as exc:
        carb.log_warn(f"[measure] cannot prepare geometry for {prim.GetPath()}: {exc}")
        return None


def _best_face_by_hit(geom, hit, cap=_FACE_SHORTLIST):
    """(score, face, verts) for the face the hit sits on. Ignores any face id.

    Only the faces whose centroids are nearest the hit get scored: scanning
    every face of a dense mesh on each mouse move is far too slow.
    """
    centroids = geom.centroids
    if centroids is None:
        return None
    target = _as_np(hit)
    distances = np.linalg.norm(centroids - target, axis=1)
    order = (
        np.argsort(distances)[:cap] if len(distances) > cap else range(len(distances))
    )
    best = None
    for face in order:
        verts = geom.face_verts(int(face))
        score = _face_score(verts, hit)
        if best is None or score < best[0]:
            best = (score, int(face), verts)
            if score <= _ON_FACE_EPS:
                break  # sitting on it, no better answer exists
    return best


def _mesh_hit_score(prim, hit, time=None) -> float:
    """How close the hit is to this prim's surface. Lower is better."""
    geom = _build_entry(prim, time)
    if geom is None:
        return float("inf")
    best = _best_face_by_hit(geom, hit)
    if best is not None:
        return best[0]
    return float(np.min(np.linalg.norm(geom.world - _as_np(hit), axis=1)))


def _attr_report(attr) -> str:
    if not attr:
        return "absent"
    try:
        samples = attr.GetTimeSamples()
        return (
            f"authored={attr.HasAuthoredValue()} samples={len(samples)}"
            f"{samples[:3] if samples else ''}"
        )
    except Exception as exc:
        return f"<unreadable: {exc}>"


def _describe(prim) -> str:
    try:
        kind = prim.GetTypeName() or "<untyped>"
        kids = [f"{c.GetName()}:{c.GetTypeName() or '?'}" for c in prim.GetChildren()]
        detail = f"type={kind} proxy={prim.IsInstanceProxy()} children={kids[:8]}"
        attr = UsdGeom.PointBased(prim).GetPointsAttr()
        if not attr:
            return detail + " points_attr=absent"
        samples = attr.GetTimeSamples()
        return (
            f"{detail} points_attr=present authored={attr.HasAuthoredValue()} "
            f"samples={len(samples)}{samples[:3] if samples else ''} "
            f"loaded={prim.IsLoaded()} active={prim.IsActive()}"
        )
    except Exception as exc:
        return f"<undescribable: {exc}>"


def _resolve_mesh_prim(stage, prim_path, hit=None, time=None):
    """(prim, note). Walks to the prim that actually carries the topology.

    A raycast can report a GeomSubset, an instance proxy or a wrapping Xform
    rather than the mesh itself. When several meshes sit under the reported
    path, the one the hit point lies on wins.
    """
    path = str(prim_path)
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None, (
            f"'{path}' ({type(prim_path).__name__}) not found on "
            f"stage '{_stage_key(stage)}'"
        )
    if _has_points(prim, time):
        return prim, ""

    detail = _describe(prim)
    parent = prim.GetParent()
    while parent and parent.IsValid() and not parent.IsPseudoRoot():
        if _has_points(parent, time):
            return parent, f"'{path}' had no points, used ancestor {parent.GetPath()}"
        parent = parent.GetParent()
    meshes = [d for d in _descendants(prim) if _has_points(d, time)]
    if len(meshes) == 1:
        return meshes[0], f"'{path}' had no points, used child {meshes[0].GetPath()}"
    if meshes:
        if hit is None:
            return None, f"'{path}' has {len(meshes)} meshes under it, no hit to pick by"
        # Intersecting planes overlap in space, so pick by distance to the
        # actual surface rather than by bounds.
        scored = sorted((_mesh_hit_score(m, hit, time), i) for i, m in enumerate(meshes))
        score, index = scored[0]
        chosen = meshes[index]
        if score == float("inf"):
            return None, f"'{path}': none of its {len(meshes)} meshes fit the hit"
        return chosen, (
            f"'{path}' has {len(meshes)} meshes, hit lands on "
            f"{chosen.GetPath()} (score {score:.6g})"
        )
    kind = str(prim.GetTypeName() or "<untyped>")
    if kind in ("Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane"):
        return None, (
            f"'{path}' is an implicit {kind}: procedural, so it has no vertices "
            f"to snap to. Surface only."
        )
    return None, f"'{path}' carries no points, and nothing near it does | {detail}"


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


def _format_length(meters: float) -> str:
    return f"{meters:.3f} m"
