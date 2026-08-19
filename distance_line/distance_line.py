from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

import carb
import numpy as np
import omni.usd
from pxr import Gf, Usd, UsdGeom

from .distance_line_overlay import DistanceLineOverlay, plate_hit_size

SNAP_RADIUS_PX = 12.0          # 스냅 반경(픽셀)
_FACE_SHORTLIST = 64           # 히트 지점 근처에서 점수를 매길 면 개수
_ON_FACE_EPS = 1e-6            # 이보다 가까우면 그 면 위에 있다고 본다
_CORNER_COS = -0.9659          # 외곽선이 15도 이상 꺾이면 꼭지점
_WELD_TOLERANCE = 1e-5         # 메시 크기 대비 이 비율 안이면 같은 점
_HIT_REACH = 3.0               # 히트 지점에서 반경의 몇 배까지 후보로 볼지
_CREASE_COS = 0.8660           # 두 면이 30도 이상 벌어지면 크리스
_VERIFY_LIMIT = 4              # 도달 검사를 해볼 후보 개수
_VERIFY_SLACK = 1e-3           # 이 비율 안에 들어오면 막힌 게 아니라 그 점에 닿은 것
_NEAR_MARGIN = 3.0             # reach 의 몇 배까지 미리 잘라 두고 재사용할지
_AIR_REACH_PX = 50.0           # 허공일 때 이 픽셀 안의 메시 외곽선에 붙는다
_CLOUD_REACH_PX = 50.0         # 허공일 때 이 픽셀 안의 포인트 클라우드 점에 붙는다

_GEOM_CACHE: dict = {}         # (스테이지, 경로, 타임코드) -> _Geom
_PRIM_CACHE: dict = {}         # (스테이지, 히트 경로, 타임코드) -> 해소 결과
_CLOUD_CACHE: dict = {}        # (스테이지, 타임코드) -> 포인트 클라우드 목록


def _log(msg: str):
    print(f"[distance_line] {msg}")


class SnapKind(IntEnum):
    SURFACE = 0
    EDGE = 1
    VERTEX = 2


class SnapMode(IntFlag):
    NONE = 0
    EDGE = 1 << 0
    VERTEX = 1 << 1
    ALL = EDGE | VERTEX


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
    element_index: int = -1
    normal: Gf.Vec3d = field(default_factory=lambda: Gf.Vec3d(0, 1, 0))


@dataclass
class Line:
    id: int
    viewport_id: str
    tab_id: str
    start: SnapPoint
    end: SnapPoint
    length_m: float
    visible: bool = True
    number: int = 0


class Subscription:
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
    def __init__(self, viewport_id: str, tab_id: str, viewport_api, overlay: DistanceLineOverlay):
        self.viewport_id = viewport_id
        self.tab_id = tab_id
        self.viewport_api = viewport_api
        self.overlay = overlay
        self.armed = False
        self.pending: SnapPoint | None = None
        self.on_done = None
        self.line_id = 0
        self.current_snap: SnapPoint | None = None
        self.visible = True
        self.snap_busy = False
        self.snap_queued = None
        self.last_mesh = None
        self.hover_ndc = None


class DistanceLineCore:
    _started = False
    _viewports: dict = {}
    _lines: dict = {}
    _next_line_id = 1
    _snap_mode = SnapMode.ALL
    _changed_callbacks: list = []
    _snap_radius = SNAP_RADIUS_PX
    _registered: dict = {}
    _tabs: dict = {}
    _active_tab = None
    _maximized: dict = {}
    _selected = ""
    _host_input = False
    _hover_seen = False
    _pending_any = False
    _pending_done = None
    _pending_viewports: tuple = ()
    _pending_line_id = 0
    _selected_line = None
    _probe_cache: dict = {}
    _probe_camera = None
    _probe_rays = 0
    _probe_saved = 0

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
        cls._selected_line = None
        cls._lines.clear()
        cls._changed_callbacks.clear()
        _drop_caches()
        cls._next_line_id = 1
        cls._started = False

    @classmethod
    def _require_started(cls):
        if not cls._started:
            raise RuntimeError("distance line extension is not started")

    @classmethod
    def status(cls) -> dict:
        return {
            "snap_mode": cls._snap_mode,
            "snap_radius": cls._snap_radius,
            "host_input": cls._host_input,
            "active_tab": cls._active_tab,
            "selected_viewport": cls._selected,
            "selected_line": cls._selected_line,
            "picking": cls._pending_any
            or any(s.armed for s in cls._viewports.values()),
            "tabs": {tab: tuple(members) for tab, members in cls._tabs.items()},
            "maximized": dict(cls._maximized),
            "probe_rays": cls._probe_rays,
            "probe_saved": cls._probe_saved,
        }

    @classmethod
    def set_host_input(cls, host_input: bool):
        cls._host_input = bool(host_input)
        if cls._host_input:
            for state in cls._viewports.values():
                state.overlay.set_click_active(False)

    @classmethod
    def is_host_input(cls) -> bool:
        return cls._host_input

    @classmethod
    def on_external_click(cls, viewport_id: str, x, y=None, space: str = "ndc"):
        if not cls._host_input:
            cls.set_host_input(True)
        ndc = cls._to_ndc(viewport_id, x, y, space)
        if ndc is not None:
            cls._on_click(viewport_id, ndc)

    @classmethod
    def on_external_hover(cls, viewport_id: str, x, y=None, space: str = "ndc"):
        ndc = cls._to_ndc(viewport_id, x, y, space)
        if ndc is not None:
            cls._on_hover(viewport_id, ndc)

    @classmethod
    def _to_ndc(cls, viewport_id: str, x, y, space: str):
        if y is None:
            try:
                x, y = x[0], x[1]
            except (TypeError, IndexError, KeyError):
                carb.log_warn(f"[distance_line] cannot read mouse coords: {x!r}")
                return None
        x, y = float(x), float(y)
        if space == "ndc":
            return (x, y)
        state = cls._viewports.get(viewport_id)
        if state is None:
            return None
        width, height = _screen_size(state)
        if width <= 0 or height <= 0:
            return None
        return ((x / width) * 2.0 - 1.0, 1.0 - (y / height) * 2.0)

    @classmethod
    def register_vph(cls, vph) -> str:
        cls._require_started()
        viewport_api = vph.viewport_api
        viewport_id = str(getattr(viewport_api, "id", "") or "")
        if not viewport_id:
            raise ValueError("vph.viewport_api has no usable id")
        tab_id = str(vph.tab_id)
        frame = vph.ui_frame
        if frame is None:
            raise ValueError(f"vph '{viewport_id}' has no ui_frame to draw into")

        old = cls._viewports.pop(viewport_id, None)
        if old is not None:
            old.overlay.destroy()

        cls._registered[viewport_id] = (viewport_api, frame, tab_id)
        members = cls._tabs.setdefault(tab_id, [])
        if viewport_id not in members:
            members.append(viewport_id)

        overlay = DistanceLineOverlay(
            viewport_id,
            viewport_api,
            frame,
            on_hover=lambda ndc, v=viewport_id: cls._on_hover(v, ndc),
            on_click=lambda ndc, v=viewport_id: cls._on_click(v, ndc),
            on_label_click=cls._on_label_click,
        )
        cls._viewports[viewport_id] = _ViewportState(
            viewport_id, tab_id, viewport_api, overlay
        )
        cls._refresh(viewport_id)
        cls._notify()
        return viewport_id

    @classmethod
    def register_tab(cls, tab_id: str, vphs) -> tuple:
        vphs = list(vphs)
        ids = []
        for vph in vphs:
            if str(vph.tab_id) != str(tab_id):
                carb.log_warn(
                    f"[distance_line] vph reports tab '{vph.tab_id}', expected '{tab_id}'"
                )
            ids.append(cls.register_vph(vph))
        cls._tabs.setdefault(str(tab_id), [])
        cls.set_active_tab(tab_id)
        return tuple(ids)

    @classmethod
    def unregister_tab(cls, tab_id: str):
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
        cls._renumber()

    @classmethod
    def set_active_tab(cls, tab_id):
        cls._active_tab = None if tab_id is None else str(tab_id)
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
        tab_id = cls.get_tab_of(viewport_id)
        cls._maximized[tab_id] = viewport_id
        for vp in list(cls._tabs.get(tab_id, [])):
            cls._refresh(vp)

    @classmethod
    def clear_maximized(cls, tab_id: str):
        tab_id = str(tab_id)
        cls._maximized.pop(tab_id, None)
        for vp in list(cls._tabs.get(tab_id, [])):
            cls._refresh(vp)

    @classmethod
    def get_maximized(cls, tab_id: str):
        return cls._maximized.get(str(tab_id))

    @classmethod
    def set_selected_viewport(cls, viewport_id: str):
        cls._selected = str(viewport_id or "")
        cls._notify()

    @classmethod
    def get_selected_viewport(cls) -> str:
        return cls._selected

    @classmethod
    def list_viewport_ids(cls, tab_id=None) -> tuple:
        if tab_id is not None:
            return tuple(cls._tabs.get(str(tab_id), []))
        return tuple(cls._registered)

    @classmethod
    def get_tab_of(cls, viewport_id: str) -> str:
        entry = cls._registered.get(viewport_id)
        return entry[2] if entry is not None else ""

    @classmethod
    def set_snap_mode(cls, mode: SnapMode):
        cls._snap_mode = SnapMode(mode)

    @classmethod
    def get_snap_mode(cls) -> SnapMode:
        return cls._snap_mode

    @classmethod
    def set_snap_radius(cls, pixels: float):
        cls._snap_radius = max(1.0, float(pixels))

    @classmethod
    def get_snap_radius(cls) -> float:
        return cls._snap_radius

    @classmethod
    def get_current_snap(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        return state.current_snap if state else None

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> int:
        cls._require_started()
        _drop_caches()

        if viewport_id:
            state = cls._viewports.get(viewport_id)
            if state is None:
                carb.log_warn(f"[distance_line] pick_one: no viewport '{viewport_id}'")
                return 0
            line_id = cls._reserve_line_id()
            cls._arm(state, on_done, line_id)
            return line_id

        candidates = cls._pick_candidates()
        if not candidates:
            carb.log_warn(
                "[distance_line] pick_one: no viewport to pick in. The host must call "
                "DistanceLineService.on_tab_created(tab_id, vphs) first."
            )
            return 0
        cls._pending_any = True
        cls._pending_done = on_done
        cls._pending_viewports = candidates
        cls._pending_line_id = cls._reserve_line_id()
        if not cls._host_input:
            for vp in candidates:
                cls._viewports[vp].overlay.set_click_active(True)
        return cls._pending_line_id

    @classmethod
    def _reserve_line_id(cls) -> int:
        line_id = cls._next_line_id
        cls._next_line_id += 1
        return line_id

    @classmethod
    def _pick_candidates(cls) -> tuple:
        if cls._active_tab is not None:
            members = cls._tabs.get(cls._active_tab, ())
            return tuple(vp for vp in members if vp in cls._viewports)
        return tuple(cls._viewports)

    @classmethod
    def _arm(cls, state, on_done, line_id):
        cls._pending_any = False
        cls._pending_done = None
        cls._pending_viewports = ()
        cls._pending_line_id = 0
        state.armed = True
        state.pending = None
        state.on_done = on_done
        state.line_id = line_id
        state.snap_busy = False
        state.snap_queued = None
        for other in cls._viewports.values():
            if not cls._host_input:
                other.overlay.set_click_active(other is state)
            elif other is not state:
                other.overlay.set_snap_marker(None)
                other.current_snap = None
    @classmethod
    def cancel_pick(cls, viewport_id=None):
        cls._pending_any = False
        cls._pending_done = None
        cls._pending_viewports = ()
        cls._pending_line_id = 0
        targets = (
            [cls._viewports[viewport_id]]
            if viewport_id in cls._viewports
            else list(cls._viewports.values())
        )
        for state in targets:
            state.armed = False
            state.pending = None
            state.on_done = None
            state.line_id = 0
            state.snap_busy = False
            state.snap_queued = None
            state.overlay.set_preview(None, None, "")
            state.overlay.set_click_active(False)

    @classmethod
    def _renumber(cls):
        counts: dict = {}
        for line in sorted(cls._lines.values(), key=lambda ln: ln.id):
            counts[line.viewport_id] = counts.get(line.viewport_id, 0) + 1
            line.number = counts[line.viewport_id]

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
        cls._renumber()
        _log(f"removed line {line.id} from '{line.viewport_id}'")
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
        cls._renumber()
        for vp in {ln.viewport_id for ln in doomed}:
            cls._refresh(vp)
        cls._notify()

    @classmethod
    def set_visible(cls, visible: bool, line_id=None, viewport_id=None, tab_id=None):
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

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        return Subscription(cls._changed_callbacks, fn)

    @classmethod
    def _notify(cls):
        for fn in list(cls._changed_callbacks):
            try:
                fn()
            except Exception as exc:
                carb.log_error(f"[distance_line] changed callback failed: {exc}")

    @classmethod
    def _on_label_click(cls, line_id: int):
        if cls._selected_line == line_id:
            cls._selected_line = None
            cls.remove(line_id)
            return
        cls._selected_line = line_id
        for viewport_id in list(cls._viewports):
            cls._refresh(viewport_id)
        cls._notify()

    @classmethod
    def _clear_selection(cls):
        if cls._selected_line is None:
            return
        cls._selected_line = None
        for viewport_id in list(cls._viewports):
            cls._refresh(viewport_id)
        cls._notify()

    @classmethod
    def _try_label_click(cls, state, ndc) -> bool:
        view, proj = _camera_matrices(state.viewport_api)
        if view is None:
            return False
        lines = [
            line
            for line in cls._lines.values()
            if line.viewport_id == state.viewport_id and line.visible
        ]
        if not lines:
            return False

        size = _screen_size(state)
        cursor = np.asarray(_ndc_to_px(ndc, size))
        view_proj = _matrix_np(view * proj)
        width, height = size
        middles = np.asarray(
            [
                (_as_np(line.start.position) + _as_np(line.end.position)) * 0.5
                for line in lines
            ]
        )
        pixels, valid = _project_px(middles, view_proj, width, height)
        camera = _camera_position(view)
        under_cursor = []
        for index, line in enumerate(lines):
            if not valid[index]:
                continue
            plate_w, plate_h = plate_hit_size(_format_length(line.length_m))
            offset = np.abs(pixels[index] - cursor)
            if offset[0] <= plate_w * 0.5 and offset[1] <= plate_h * 0.5:
                depth = float(np.linalg.norm(middles[index] - camera))
                under_cursor.append((depth, line.id))
        if not under_cursor:
            return False
        under_cursor.sort()
        cls._on_label_click(under_cursor[0][1])
        return True

    @classmethod
    def _on_hover(cls, viewport_id: str, ndc):
        state = cls._viewports.get(viewport_id)
        if state is None or not cls._listening(viewport_id, state):
            return
        cls._hover_seen = True
        state.hover_ndc = ndc
        cls._resolve_snap(state, ndc, lambda snap: cls._apply_hover(state, snap))

    @classmethod
    def _listening(cls, viewport_id: str, state) -> bool:
        if state.armed:
            return True
        return cls._pending_any and viewport_id in cls._pending_viewports

    @classmethod
    def _apply_hover(cls, state, snap):
        state.current_snap = snap
        state.overlay.set_snap_marker(snap)
        if state.pending is None:
            return
        if snap is not None:
            length = cls._length_m(state.pending.position, snap.position)
            state.overlay.set_preview(
                state.pending.position, snap.position, _format_length(length)
            )
            return
        drifting = cls._point_in_air(state)
        state.overlay.set_preview(
            None if drifting is None else state.pending.position, drifting, ""
        )

    @classmethod
    def _point_in_air(cls, state):
        """스냅이 없을 때 고무줄 선의 끝점.

        첫 점을 지나며 카메라를 향하는 평면에 커서 레이를 맞춘다. 지오메트리
        위의 점이 아니므로 길이는 표시하지 않고 클릭도 받지 않는다. 선만
        커서를 따라간다.
        """
        if state.hover_ndc is None or state.pending is None:
            return None
        view, proj = _camera_matrices(state.viewport_api)
        if view is None:
            return None
        origin, direction = _ndc_to_ray(state.hover_ndc, view, proj)
        origin, direction = _as_np(origin), _as_np(direction)
        towards = _as_np(state.pending.position) - origin
        span = float(np.linalg.norm(towards))
        if span <= 0.0:
            return None
        ahead = float(np.dot(direction, towards / span))
        if ahead <= 1e-6:
            return None
        return Gf.Vec3d(*(origin + direction * (span / ahead)))

    @classmethod
    def _on_click(cls, viewport_id: str, ndc):
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        if not state.armed and cls._listening(viewport_id, state):
            cls._selected = viewport_id
            cls._arm(state, cls._pending_done, cls._pending_line_id)
        if not state.armed:
            if not cls._try_label_click(state, ndc):
                cls._clear_selection()
            return
        cls._clear_selection()
        cls._resolve_snap_now(state, ndc, lambda snap: cls._apply_click(state, snap))

    @classmethod
    def _apply_click(cls, state, snap):
        if snap is None:
            return
        state.overlay.set_snap_marker(snap)
        if state.pending is None:
            state.pending = snap
            return

        line = Line(
            id=state.line_id or cls._reserve_line_id(),
            viewport_id=state.viewport_id,
            tab_id=state.tab_id,
            start=state.pending,
            end=snap,
            length_m=cls._length_m(state.pending.position, snap.position),
        )
        cls._lines[line.id] = line
        cls._renumber()
        _log(f"line {line.id} (#{line.number}) on '{line.viewport_id}': {line.length_m:.3f} m")

        on_done = state.on_done
        state.armed = False
        state.pending = None
        state.on_done = None
        state.line_id = 0
        state.overlay.set_preview(None, None, "")
        state.overlay.set_click_active(False)
        cls._refresh(state.viewport_id)
        cls._notify()

        if on_done is not None:
            try:
                on_done(line)
            except Exception as exc:
                carb.log_error(f"[distance_line] on_done callback failed: {exc}")

    @classmethod
    def _resolve_snap(cls, state, ndc, on_result):
        if state.snap_busy:
            state.snap_queued = (ndc, on_result)
            return
        state.snap_busy = True
        cls._begin_snap(
            state, ndc, lambda snap: cls._finish_snap(state, snap, on_result)
        )

    @classmethod
    def _resolve_snap_now(cls, state, ndc, on_result):
        cls._begin_snap(state, ndc, on_result)

    @classmethod
    def _finish_snap(cls, state, snap, on_result):
        state.snap_busy = False
        queued = state.snap_queued
        state.snap_queued = None
        try:
            on_result(snap)
        finally:
            if queued is not None:
                cls._resolve_snap(state, queued[0], queued[1])

    @classmethod
    def _begin_snap(cls, state, ndc, deliver):
        try:
            import omni.kit.raycast.query as rq
        except ImportError:
            carb.log_error("[distance_line] omni.kit.raycast.query is not available")
            deliver(None)
            return

        view, proj = _camera_matrices(state.viewport_api)
        if view is None:
            deliver(None)
            return
        origin, direction = _ndc_to_ray(ndc, view, proj)

        def _on_hit(_ray, result):
            surface, ranked = cls._candidates_from_hit(state, result, ndc, view, proj)
            cls._probe(rq, _camera_position(view), ranked, 0, surface, deliver)

        iface = rq.acquire_raycast_query_interface()
        iface.submit_raycast_query(
            rq.Ray(
                (origin[0], origin[1], origin[2]),
                (direction[0], direction[1], direction[2]),
            ),
            _on_hit,
        )

    @classmethod
    def _probe(cls, rq, camera, ranked, slot, surface, deliver):
        memo = cls._probe_memo(camera)
        while slot < len(ranked):
            known = cls._recall(memo, ranked[slot])
            if known is None:
                break
            cls._probe_saved += 1
            if known:
                deliver(ranked[slot])
                return
            slot += 1

        if slot >= len(ranked):
            deliver(surface)
            return

        point = ranked[slot]
        delta = _as_np(point.position) - camera
        span = float(np.linalg.norm(delta))
        if span <= 0.0:
            deliver(point)
            return

        def _on_probe(_ray, result):
            reached = _reached(result, camera, span)
            cls._remember(memo, point, reached)
            if reached:
                deliver(point)
            else:
                cls._probe(rq, camera, ranked, slot + 1, surface, deliver)

        cls._probe_rays += 1
        rq.acquire_raycast_query_interface().submit_raycast_query(
            rq.Ray(
                (camera[0], camera[1], camera[2]),
                tuple(delta / span),
            ),
            _on_probe,
        )

    @classmethod
    def _probe_memo(cls, camera) -> dict:
        """이 카메라 위치에서의 검증 결과 모음.

        가려짐은 카메라에서 후보까지의 선분에만 달려 있으므로 제자리 회전으로는
        바뀌지 않는다. 위치가 움직이면 통째로 버린다.
        """
        key = np.asarray(camera, dtype=float).tobytes()
        if cls._probe_camera != key:
            cls._probe_camera = key
            cls._probe_cache = {}
        return cls._probe_cache

    @staticmethod
    def _key_of(point):
        """꼭지점만 캐시한다. 엣지 위의 점은 커서를 따라 미끄러져 고정되지 않는다."""
        if point.kind != SnapKind.VERTEX or point.element_index < 0:
            return None
        return (point.prim_path, point.element_index)

    @classmethod
    def _recall(cls, memo: dict, point):
        key = cls._key_of(point)
        return None if key is None else memo.get(key)

    @classmethod
    def _remember(cls, memo: dict, point, reached: bool):
        key = cls._key_of(point)
        if key is not None:
            memo[key] = reached

    @classmethod
    def _candidates_from_hit(cls, state, result, ndc, view, proj):
        if not getattr(result, "valid", False):
            return cls._candidates_in_air(state, ndc, view, proj)

        hit = Gf.Vec3d(*result.hit_position)
        normal = Gf.Vec3d(*getattr(result, "normal", (0.0, 1.0, 0.0)))
        path = result.get_target_usd_path()

        surface = SnapPoint(hit, SnapKind.SURFACE, path, -1, -1, normal)
        if cls._snap_mode == SnapMode.NONE:
            return surface, []

        time = _time_of(state.viewport_api)
        geom = cls._mesh_entry(path, hit, time)
        if geom is None:
            return surface, []
        geom.calibrate(hit, normal)
        state.last_mesh = (path, geom)
        size = _screen_size(state)
        cursor_px = np.asarray(_ndc_to_px(ndc, size))
        view_proj = _matrix_np(view * proj)
        width, height = size

        reach = cls._snap_radius * _HIT_REACH / _pixel_scale(
            view, proj, hit, geom.extent, size
        )

        outline, edge_ids, corner_ids = geom.boundary.near(_as_np(hit), reach)
        seen_edges, seen_corners = outline.visibility(
            _camera_position(view), geom.orientation
        )

        batches = []
        if cls._snap_mode & SnapMode.VERTEX and len(outline.corners):
            pixels, valid = _project_px(outline.corners, view_proj, width, height)
            batches.append((
                SnapKind.VERTEX,
                outline.corners,
                np.linalg.norm(pixels - cursor_px, axis=1),
                valid,
                seen_corners,
                corner_ids,
            ))
        if cls._snap_mode & SnapMode.EDGE and len(outline.edge_a):
            points, screen, valid = _nearest_on_edges(
                outline.edge_a, outline.edge_b, cursor_px, view_proj, width, height
            )
            batches.append(
                (SnapKind.EDGE, points, screen, valid, seen_edges, edge_ids)
            )

        ranked = cls._rank(
            batches, cls._snap_radius, path, normal, hit=_as_np(hit), reach=reach
        )
        return surface, ranked

    @classmethod
    def _rank(cls, batches, radius, path, normal, hit=None, reach=None):
        """후보를 종류 우선순위 다음 화면 거리 순으로 줄 세운다."""
        ranked = []
        for kind, points, screen, valid, seen, ids in batches:
            usable = valid & seen
            if hit is not None:
                usable = usable & (np.linalg.norm(points - hit, axis=1) <= reach)
            if not usable.any():
                continue
            distances = np.where(usable, screen, np.inf)
            for index in np.argsort(distances)[: _VERIFY_LIMIT - len(ranked)]:
                if float(distances[index]) > radius:
                    break
                ranked.append(
                    SnapPoint(
                        Gf.Vec3d(*points[index]), kind, path, -1, int(ids[index]), normal
                    )
                )
            if len(ranked) >= _VERIFY_LIMIT:
                break
        return ranked

    @classmethod
    def _candidates_in_air(cls, state, ndc, view, proj):
        """프림에 안 맞았을 때. 화면상 가장 가까운 후보에 붙는다.

        메시와 포인트 클라우드는 후보를 만드는 방식이 전혀 다르므로 각자
        따로 모으고, 여기서는 합쳐서 줄만 세운다. 종류 우선순위는 쓰지 않는다.
        """
        size = _screen_size(state)
        cursor_px = np.asarray(_ndc_to_px(ndc, size))
        view_proj = _matrix_np(view * proj)
        width, height = size

        batches = cls._mesh_batches(
            state, cursor_px, view_proj, width, height, _camera_position(view)
        )
        batches += cls._cloud_batches(cursor_px, view_proj, width, height)
        if not batches:
            return None, []
        return None, cls._nearest_first(batches, Gf.Vec3d(0, 1, 0))

    @classmethod
    def _mesh_batches(cls, state, cursor_px, view_proj, width, height, camera):
        """마지막에 맞았던 메시의 외곽선에서 뽑은 후보."""
        if state.last_mesh is None or cls._snap_mode == SnapMode.NONE:
            return []
        path, geom = state.last_mesh
        outline = geom.boundary
        if not len(outline.edge_a):
            return []

        seen_edges, seen_corners = outline.visibility(camera, geom.orientation)
        corner_px, edge_a_px, edge_b_px = outline.screen(view_proj, width, height)

        batches = []
        if cls._snap_mode & SnapMode.VERTEX and len(outline.corners):
            pixels, valid = corner_px
            batches.append((
                SnapKind.VERTEX,
                outline.corners,
                np.linalg.norm(pixels - cursor_px, axis=1),
                valid,
                seen_corners,
                np.arange(len(outline.corners)),
                path,
                _AIR_REACH_PX,
            ))
        if cls._snap_mode & SnapMode.EDGE:
            points, screen, valid = _edge_nearest(
                outline.edge_a, outline.edge_b, cursor_px,
                edge_a_px[0], edge_a_px[1], edge_b_px[0], edge_b_px[1],
            )
            batches.append((
                SnapKind.EDGE, points, screen, valid, seen_edges,
                np.arange(len(outline.edge_a)), path, _AIR_REACH_PX,
            ))
        return batches

    @classmethod
    def _cloud_batches(cls, cursor_px, view_proj, width, height):
        """포인트 클라우드는 면도 엣지도 없으니 점 자체가 후보다.

        투영본을 x 로 정렬해 두고 커서 좌우 구간만 잘라 보므로, 비용이 전체
        점 개수가 아니라 그 구간에 걸리는 점 개수에 비례한다.
        """
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return []
        batches = []
        for path, geom in _point_clouds(stage):
            pixels, valid, order, sorted_x = geom.screen_sorted(
                view_proj, width, height
            )
            low = np.searchsorted(sorted_x, cursor_px[0] - _CLOUD_REACH_PX, "left")
            high = np.searchsorted(sorted_x, cursor_px[0] + _CLOUD_REACH_PX, "right")
            if low >= high:
                continue
            picked = order[low:high]
            spread = pixels[picked] - cursor_px
            picked = picked[np.abs(spread[:, 1]) <= _CLOUD_REACH_PX]
            if not len(picked):
                continue
            batches.append((
                SnapKind.SURFACE,
                geom.world[picked],
                np.linalg.norm(pixels[picked] - cursor_px, axis=1),
                valid[picked],
                np.ones(len(picked), dtype=bool),
                picked,
                path,
                _CLOUD_REACH_PX,
            ))
        return batches

    @classmethod
    def _nearest_first(cls, batches, normal):
        """종류 우선순위 없이 화면 거리만으로 줄 세운다.

        꼭지점 우선은 오브젝트 위 좁은 반경에서나 맞는 규칙이다. 허공의 넓은
        반경에 그대로 쓰면 멀리 있는 꼭지점이 바로 옆 엣지를 이겨 버린다.
        """
        pool = []
        for kind, points, screen, valid, seen, ids, path, radius in batches:
            usable = valid & seen
            if not usable.any():
                continue
            distances = np.where(usable, screen, np.inf)
            for index in np.argsort(distances)[:_VERIFY_LIMIT]:
                gap = float(distances[index])
                if gap > radius:
                    break
                pool.append((gap, kind, points[index], int(ids[index]), path))
        pool.sort(key=lambda item: item[0])
        return [
            SnapPoint(Gf.Vec3d(*position), kind, source, -1, element, normal)
            for _gap, kind, position, element, source in pool[:_VERIFY_LIMIT]
        ]

    @classmethod
    def _mesh_entry(cls, prim_path: str, hit=None, time=None):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None

        prim, why = _resolve_mesh_prim(stage, prim_path, hit, time)
        if prim is None:
            return None
        return _geom_for(prim, time)

    @classmethod
    def invalidate_mesh_cache(cls, prim_path=None):
        _drop_caches(prim_path)

    @classmethod
    def _refresh(cls, viewport_id: str):
        state = cls._viewports.get(viewport_id)
        if state is None:
            return
        tab_active = cls._active_tab is None or state.tab_id == cls._active_tab
        eclipsed = cls._maximized.get(state.tab_id) not in (None, viewport_id)
        shown = state.visible and tab_active and not eclipsed
        state.overlay.set_scene_visible(shown)
        lines = [
            ln
            for ln in cls._lines.values()
            if ln.viewport_id == viewport_id and ln.visible
        ]
        state.overlay.set_lines(lines, _format_length, cls._selected_line)

    @classmethod
    def _length_m(cls, a: Gf.Vec3d, b: Gf.Vec3d) -> float:
        stage = omni.usd.get_context().get_stage()
        mpu = UsdGeom.GetStageMetersPerUnit(stage) if stage else 1.0
        return float((b - a).GetLength()) * (mpu or 1.0)


def _stage_key(stage) -> str:
    if stage is None:
        return ""
    try:
        return stage.GetRootLayer().identifier
    except Exception:
        return str(id(stage))


def _time_of(viewport_api):
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
    if not attr:
        return None
    for when in _times_to_try(attr, time):
        value = attr.Get(when)
        if value:
            return value
    return None


def _points_of(prim, time=None):
    try:
        return _attr_value(UsdGeom.PointBased(prim).GetPointsAttr(), time)
    except Exception:
        return None


def _has_points(prim, time=None) -> bool:
    return _points_of(prim, time) is not None


def _point_clouds(stage, time=None):
    """스테이지의 포인트 클라우드.

    면이 없어 레이캐스트에 맞지 않는 경우가 많다. 히트로는 찾을 수 없으므로
    스테이지를 직접 훑는다.
    """
    key = (_stage_key(stage), str(time))
    found = _CLOUD_CACHE.get(key)
    if found is None:
        found = []
        try:
            walk = Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies())
        except Exception:
            walk = stage.Traverse()
        for prim in walk:
            if str(prim.GetTypeName()) != "Points":
                continue
            geom = _geom_for(prim, time)
            if geom is not None and len(geom.world):
                found.append((str(prim.GetPath()), geom))
        _CLOUD_CACHE[key] = found
    return found


def _geom_for(prim, time=None):
    key = (_stage_key(prim.GetStage()), str(prim.GetPath()), str(time))
    entry = _GEOM_CACHE.get(key)
    if entry is None:
        entry = _build_entry(prim, time) or ()
        _GEOM_CACHE[key] = entry
        DistanceLineCore._probe_camera = None
    return entry or None


def _drop_caches(prim_path=None):
    DistanceLineCore._probe_camera = None
    DistanceLineCore._probe_cache = {}
    if prim_path is None:
        _CLOUD_CACHE.clear()
    for cache in (_GEOM_CACHE, _PRIM_CACHE):
        if prim_path is None:
            cache.clear()
        else:
            for key in [k for k in cache if k[1] == prim_path]:
                cache.pop(key, None)


def _descendants(prim):
    try:
        return list(Usd.PrimRange(prim, Usd.TraverseInstanceProxies()))
    except Exception:
        return list(Usd.PrimRange(prim))


def _as_np(vec) -> np.ndarray:
    return np.asarray([vec[0], vec[1], vec[2]], dtype=float)


def _face_normals(position, index, nxt, starts) -> np.ndarray:
    cross = np.cross(position[index], position[nxt])
    normals = np.add.reduceat(cross, starts, axis=0)
    lengths = np.linalg.norm(normals, axis=1)
    return normals / np.where(lengths > 1e-20, lengths, 1.0)[:, None]


def _feature_edges(index, nxt, counts, normals):
    pairs = np.sort(np.stack([index, nxt], axis=1), axis=1)
    face_of = np.repeat(np.arange(len(counts)), counts)
    keep = pairs[:, 0] != pairs[:, 1]
    pairs, face_of = pairs[keep], face_of[keep]
    if len(pairs) == 0:
        return np.empty((0, 2), dtype=np.int64), np.empty((0, 2, 3)), 0

    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    pairs, face_of = pairs[order], face_of[order]
    fresh = np.ones(len(pairs), dtype=bool)
    fresh[1:] = np.any(pairs[1:] != pairs[:-1], axis=1)
    group = np.flatnonzero(fresh)
    sizes = np.diff(np.append(group, len(pairs)))

    boundary = group[sizes == 1]
    tangled = group[sizes > 2]
    shared = group[sizes == 2]
    creased = shared
    if len(shared):
        left, right = normals[face_of[shared]], normals[face_of[shared + 1]]
        cosine = np.abs(np.einsum("ij,ij->i", left, right))
        creased = shared[cosine < _CREASE_COS]

    chosen = np.concatenate([boundary, creased, tangled])
    single = np.isin(chosen, boundary)
    other = np.where(single, chosen, chosen + 1)
    sides = np.stack([normals[face_of[chosen]], normals[face_of[other]]], axis=1)
    return pairs[chosen], sides, len(group)


def _nearest_on_edges(starts, ends, cursor_px, view_proj, width, height):
    px_a, ok_a = _project_px(starts, view_proj, width, height)
    px_b, ok_b = _project_px(ends, view_proj, width, height)
    return _edge_nearest(starts, ends, cursor_px, px_a, ok_a, px_b, ok_b)


def _edge_nearest(starts, ends, cursor_px, px_a, ok_a, px_b, ok_b):
    spans = px_b - px_a
    lengths = np.einsum("ij,ij->i", spans, spans)
    safe = np.where(lengths > 1e-12, lengths, 1.0)
    t = np.einsum("ij,ij->i", cursor_px - px_a, spans) / safe
    t = np.clip(np.where(lengths > 1e-12, t, 0.0), 0.0, 1.0)

    screen = np.linalg.norm(px_a + spans * t[:, None] - cursor_px, axis=1)
    points = starts + (ends - starts) * t[:, None]
    return points, screen, ok_a & ok_b


def _pixel_scale(view, proj, point, extent, size) -> float:
    step = max(extent, 1e-9) * 1e-3
    across = _as_np(view.GetInverse().TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)))
    pair = np.stack([_as_np(point), _as_np(point) + across * step])
    width, height = size
    pixels, valid = _project_px(pair, _matrix_np(view * proj), width, height)
    if not valid.all():
        return 1.0
    moved = float(np.linalg.norm(pixels[1] - pixels[0]))
    return moved / step if moved > 1e-9 else 1.0


def _camera_position(view) -> np.ndarray:
    return _as_np(view.GetInverse().ExtractTranslation())


def _matrix_np(matrix) -> np.ndarray:
    return np.array([[matrix[r][c] for c in range(4)] for r in range(4)], dtype=float)


def _project_px(world: np.ndarray, view_proj: np.ndarray, width, height):
    count = len(world)
    homogeneous = np.hstack([world, np.ones((count, 1))])
    clip = homogeneous @ view_proj
    w = clip[:, 3]
    # w 가 음수면 카메라 뒤다. 나누면 좌표가 뒤집혀 화면 앞쪽에 그럴듯하게 찍히므로
    # 부호를 봐야 한다. 직교 투영은 w 가 항상 1 이라 영향이 없다.
    valid = w > 1e-12
    ndc = np.zeros((count, 3))
    np.divide(clip[:, :3], w[:, None], out=ndc, where=valid[:, None])
    pixels = np.empty((count, 2))
    pixels[:, 0] = (ndc[:, 0] * 0.5 + 0.5) * width
    pixels[:, 1] = (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * height
    return pixels, valid & (np.abs(ndc[:, 2]) <= 1.0)


class _Outline:
    def __init__(self, corners, edge_a, edge_b, edge_normals, corner_of):
        self.corners = corners
        self.edge_a = edge_a
        self.edge_b = edge_b
        self.edge_normals = edge_normals
        self.corner_of = corner_of
        self._along = None
        self._near = None
        self._screen = None

    @classmethod
    def empty(cls):
        return cls(
            np.empty((0, 3)),
            np.empty((0, 3)),
            np.empty((0, 3)),
            np.empty((0, 2, 3)),
            np.empty((0, 2), dtype=np.int64),
        )

    def near(self, hit, reach):
        """히트 근처 후보만 남긴 부분집합과 원래 인덱스.

        멀리 있는 후보는 어차피 reach 에서 탈락한다. 그걸 매번 전부 투영하면
        hover 비용이 외곽선 전체 크기에 비례해 커진다. 여유를 두고 잘라 두고
        커서가 그 여유를 벗어날 때만 다시 자른다.
        """
        cached = self._near
        if cached is not None:
            base, margin, result = cached
            if float(np.linalg.norm(hit - base)) + reach <= margin:
                return result

        margin = reach * _NEAR_MARGIN
        edges = np.flatnonzero(self._segment_gap(hit) <= margin * margin)
        if len(self.corners):
            corners = np.flatnonzero(
                _square_lengths(self.corners - hit) <= margin * margin
            )
        else:
            corners = np.empty(0, dtype=np.int64)

        if len(edges) == len(self.edge_a) and len(corners) == len(self.corners):
            result = self, np.arange(len(self.edge_a)), np.arange(len(self.corners))
        else:
            slot = np.full(len(self.corners) or 1, -1, dtype=np.int64)
            slot[corners] = np.arange(len(corners))
            picked = self.corner_of[edges]
            result = (
                _Outline(
                    self.corners[corners],
                    self.edge_a[edges],
                    self.edge_b[edges],
                    self.edge_normals[edges],
                    np.where(picked >= 0, slot[picked], -1),
                ),
                edges,
                corners,
            )
        self._near = (np.array(hit, dtype=float), margin, result)
        return result

    def screen(self, view_proj, width, height):
        """카메라별 화면 투영. 히트가 없을 때는 후보를 줄일 수 없어 한 번만 해 둔다."""
        key = (view_proj.tobytes(), width, height)
        if self._screen is None or self._screen[0] != key:
            self._screen = (
                key,
                _project_px(self.corners, view_proj, width, height),
                _project_px(self.edge_a, view_proj, width, height),
                _project_px(self.edge_b, view_proj, width, height),
            )
        return self._screen[1], self._screen[2], self._screen[3]

    def _segment_gap(self, hit):
        """히트에서 각 엣지까지의 거리 제곱.

        끝점까지의 거리로 어림하면 긴 엣지가 전부 통과해 걸러지지 않는다.
        엣지 위 가장 가까운 점은 화면에서 고르든 3D 로 고르든 이 값보다 멀 수
        없으므로, 이 값으로 자르면 살아남을 후보를 놓치지 않는다.
        """
        if self._along is None:
            direction = self.edge_b - self.edge_a
            self._along = (direction, 1.0 / np.maximum(_square_lengths(direction), 1e-30))
        direction, inverse = self._along
        offset = hit - self.edge_a
        t = np.clip(np.einsum("ij,ij->i", offset, direction) * inverse, 0.0, 1.0)
        return _square_lengths(offset - direction * t[:, None])

    def visibility(self, camera, orientation=1.0):
        towards = camera - (self.edge_a + self.edge_b) * 0.5
        facing = np.einsum("ijk,ik->ij", self.edge_normals, towards)
        edges = (facing * (orientation or 1.0) > 0.0).any(axis=1)

        count = len(self.corners)
        corners = np.zeros(count, dtype=bool)
        for end in (0, 1):
            slots = self.corner_of[:, end]
            known = slots >= 0
            if not known.any():
                continue
            corners |= (
                np.bincount(slots[known], weights=edges[known], minlength=count)[:count]
                > 0
            )
        return edges, corners


class _Geom:
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
        self._face_normals = None
        self._screen = None
        self.orientation = 0.0

    def screen_sorted(self, view_proj, width, height):
        """점 전체를 화면에 투영해 x 순으로 정렬해 둔다.

        카메라가 그대로면 재사용한다. 덕분에 hover 비용이 전체 점 개수가 아니라
        커서 좌우에 걸리는 점 개수에만 비례한다.
        """
        key = (view_proj.tobytes(), width, height)
        if self._screen is None or self._screen[0] != key:
            pixels, valid = _project_px(self.world, view_proj, width, height)
            order = np.argsort(pixels[:, 0], kind="stable")
            self._screen = (key, pixels, valid, order, pixels[order, 0])
        return self._screen[1:]

    def calibrate(self, hit, hit_normal):
        if self.orientation or self.counts is None:
            return
        found = _best_face_by_hit(self, hit)
        reference = _as_np(hit_normal)
        if found is None or not np.linalg.norm(reference):
            return
        ours = self.face_normal(found[1])
        self.orientation = -1.0 if float(np.dot(ours, reference)) < 0.0 else 1.0

    def face_normal(self, face: int) -> np.ndarray:
        if self._face_normals is None:
            position, index, nxt, starts = self._topology()
            self._face_normals = _face_normals(position, index, nxt, starts)
        return self._face_normals[face]

    def _topology(self):
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
        if self._boundary is None:
            self._boundary = self._find_boundary()
        return self._boundary

    def _weld(self):
        world = self.world
        tolerance = self.extent * _WELD_TOLERANCE
        keys = np.round(world / tolerance).astype(np.int64)
        _, first, remap, counts = np.unique(
            keys, axis=0, return_index=True, return_inverse=True, return_counts=True
        )
        remap = remap.ravel()
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
        edges, sides, counted = _feature_edges(
            index, nxt, np.asarray(self.counts, dtype=np.int64), normals
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
                corners.append(vertex)
                continue
            here = position[vertex]
            first = position[around[0]] - here
            second = position[around[1]] - here
            n1, n2 = np.linalg.norm(first), np.linalg.norm(second)
            if n1 < 1e-12 or n2 < 1e-12:
                continue
            if float(np.dot(first / n1, second / n2)) > _CORNER_COS:
                corners.append(vertex)

        corners = sorted(corners)
        slot_of = {vertex: slot for slot, vertex in enumerate(corners)}
        corner_of = np.array(
            [[slot_of.get(int(a), -1), slot_of.get(int(b), -1)] for a, b in edges],
            dtype=np.int64,
        ).reshape(len(edges), 2)

        return _Outline(
            corners=position[np.asarray(corners, dtype=np.int64)]
            if corners
            else np.empty((0, 3)),
            edge_a=position[edges[:, 0]],
            edge_b=position[edges[:, 1]],
            edge_normals=sides,
            corner_of=corner_of,
        )


def _build_entry(prim, time=None):
    points = _points_of(prim, time)
    if points is None:
        return None
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
        carb.log_warn(f"[distance_line] cannot prepare geometry for {prim.GetPath()}: {exc}")
        return None


def _square_lengths(delta: np.ndarray) -> np.ndarray:
    return np.einsum("ij,ij->i", delta, delta)


def _reached(result, camera, span) -> bool:
    if not getattr(result, "valid", False):
        return True
    distance = float(getattr(result, "hit_t", 0.0) or 0.0)
    if distance <= 0.0:
        position = getattr(result, "hit_position", None)
        if position is None:
            return True
        distance = float(np.linalg.norm(np.asarray(position, dtype=float) - camera))
    return distance >= span * (1.0 - _VERIFY_SLACK)


def _best_face_by_hit(geom, hit, cap=_FACE_SHORTLIST):
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
                break
    return best


def _mesh_hit_score(prim, hit, time=None) -> float:
    geom = _geom_for(prim, time)
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
    key = (_stage_key(stage), str(prim_path), str(time))
    found = _PRIM_CACHE.get(key)
    if found is None:
        found = _find_mesh_prim(stage, prim_path, time)
        _PRIM_CACHE[key] = found
    meshes, why = found
    if len(meshes) == 1:
        return meshes[0], why
    if not meshes:
        return None, why
    if hit is None:
        return None, f"'{prim_path}' has {len(meshes)} meshes under it, no hit to pick by"
    scored = sorted((_mesh_hit_score(m, hit, time), i) for i, m in enumerate(meshes))
    score, index = scored[0]
    if score == float("inf"):
        return None, f"'{prim_path}': none of its {len(meshes)} meshes fit the hit"
    chosen = meshes[index]
    return chosen, (
        f"'{prim_path}' has {len(meshes)} meshes, hit lands on "
        f"{chosen.GetPath()} (score {score:.6g})"
    )


def _find_mesh_prim(stage, prim_path, time=None):
    path = str(prim_path)
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return (), (
            f"'{path}' ({type(prim_path).__name__}) not found on "
            f"stage '{_stage_key(stage)}'"
        )
    if _has_points(prim, time):
        return (prim,), ""

    detail = _describe(prim)
    parent = prim.GetParent()
    while parent and parent.IsValid() and not parent.IsPseudoRoot():
        if _has_points(parent, time):
            return (parent,), f"'{path}' had no points, used ancestor {parent.GetPath()}"
        parent = parent.GetParent()
    meshes = tuple(d for d in _descendants(prim) if _has_points(d, time))
    if len(meshes) == 1:
        return meshes, f"'{path}' had no points, used child {meshes[0].GetPath()}"
    if meshes:
        return meshes, ""
    kind = str(prim.GetTypeName() or "<untyped>")
    if kind in ("Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane"):
        return (), (
            f"'{path}' is an implicit {kind}: procedural, so it has no vertices "
            f"to snap to. Surface only."
        )
    return (), f"'{path}' carries no points, and nothing near it does | {detail}"


def _face_score(verts, hit: Gf.Vec3d) -> float:
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
        carb.log_warn(f"[distance_line] cannot read camera matrices: {exc}")
        return None, None


def _ndc_to_ray(ndc, view: Gf.Matrix4d, proj: Gf.Matrix4d):
    inv = (view * proj).GetInverse()
    near = inv.Transform(Gf.Vec3d(ndc[0], ndc[1], -1.0))
    far = inv.Transform(Gf.Vec3d(ndc[0], ndc[1], 1.0))
    return near, (far - near).GetNormalized()


def _screen_size(state):
    return state.overlay.screen_size()


def _ndc_to_px(ndc, size):
    w, h = size
    return ((ndc[0] * 0.5 + 0.5) * w, (1.0 - (ndc[1] * 0.5 + 0.5)) * h)


def _format_length(meters: float) -> str:
    return f"{meters:.3f} m"
