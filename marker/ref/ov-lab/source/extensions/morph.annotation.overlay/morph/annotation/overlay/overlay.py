"""AnnotationOverlay: annotation list, events, sidecar load/save."""

__all__ = [
    "EXT_ID",
    "EVENT_CHANGED",
    "EVENT_SELECTION_CHANGED",
    "EVENT_FACE_SELECTED",
    "MSG_STATE",
    "SETTINGS_ROOT",
    "AnnotationOverlay",
    "get_overlay",
]

import asyncio
import copy
import math
import os
from typing import Callable, List, Optional

import carb
import carb.settings

from .face import FaceComputer, normalize_face, point_face
from .model import AnnotationLimitError, next_id, normalize
from .pick import Picker, toward_camera, validate_pick_args
from .sidecar import DEFAULT_SUFFIX, read_sidecar, sidecar_path, write_sidecar

EXT_ID = "morph.annotation.overlay"
EVENT_CHANGED = "morph.annotation.changed"
EVENT_SELECTION_CHANGED = "morph.annotation.selection_changed"
EVENT_FACE_SELECTED = "morph.annotation.face_selected"
MSG_STATE = f"{EXT_ID}.state"
SETTINGS_ROOT = f"/exts/{EXT_ID}"
S_VISIBLE = f"{SETTINGS_ROOT}/visible"
S_AUTOLOAD = f"{SETTINGS_ROOT}/autoLoad"
S_SUFFIX = f"{SETTINGS_ROOT}/sidecarSuffix"
S_TEXT_SCALE = f"{SETTINGS_ROOT}/textScale"
S_MAX = f"{SETTINGS_ROOT}/maxAnnotations"
S_SHOW_DESC = f"{SETTINGS_ROOT}/showDescription"
S_EDITOR_WINDOW = f"{SETTINGS_ROOT}/editorWindow"

DEFAULT_MAX = 512
TEXT_SCALE_MIN = 0.25
TEXT_SCALE_MAX = 4.0
SHOW_DESC_VALUES = ("always", "never")


def text_scale_setting() -> float:
    """``textScale`` setting, clamped to 0.25..4 (bad value = 1.0)."""
    v = carb.settings.get_settings().get(S_TEXT_SCALE)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return 1.0
    return min(TEXT_SCALE_MAX, max(TEXT_SCALE_MIN, float(v)))


def show_description_setting() -> bool:
    """``showDescription`` setting: ``"never"`` (or false) hides descriptions, anything else shows them."""
    v = carb.settings.get_settings().get(S_SHOW_DESC)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() != "never"
    return True

_instance: Optional["AnnotationOverlay"] = None


def get_overlay() -> Optional["AnnotationOverlay"]:
    """Singleton (valid after startup, None after shutdown)."""
    return _instance


def _set_instance(inst: Optional["AnnotationOverlay"]) -> None:
    global _instance
    _instance = inst


def _viewport_stage(viewport_api):
    """Stage shown by a viewport (``viewport_api.stage``, else its USD context's stage)."""
    stage = getattr(viewport_api, "stage", None)
    if stage is not None:
        return stage
    try:
        import omni.usd

        name = getattr(viewport_api, "usd_context_name", "") or ""
        ctx = omni.usd.get_context(name) if isinstance(name, str) else omni.usd.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:  # noqa: BLE001
        return None


def _viewport_time(viewport_api, stage):
    """Current time code: the viewport's, else the timeline's, else Default."""
    from pxr import Usd

    t = getattr(viewport_api, "time", None)
    if isinstance(t, Usd.TimeCode):
        return t
    try:
        import omni.timeline

        seconds = omni.timeline.get_timeline_interface().get_current_time()
        tcps = stage.GetTimeCodesPerSecond() if stage is not None else 24.0
        return Usd.TimeCode(float(seconds) * float(tcps))
    except Exception:  # noqa: BLE001
        return Usd.TimeCode.Default()


def _default_scene_file() -> Optional[str]:
    """Local file path of the current stage's root layer, or None (new / anonymous / non-file)."""
    try:
        import omni.usd

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if stage is None:
            return None
        layer = stage.GetRootLayer()
        if layer is None or layer.anonymous:
            return None
        path = layer.realPath
        if not path or "://" in path:
            return None
        return os.path.normpath(path)
    except Exception:  # noqa: BLE001
        return None


class AnnotationOverlay:
    """Annotation store. All methods are main-thread only.

    ``sync_settings=True`` is used by the extension singleton: ``set_visible`` also writes the
    ``visible`` setting. Test instances leave it False so they never touch the singleton.
    """

    def __init__(
        self,
        *,
        sync_settings: bool = False,
        scene_file_provider: Optional[Callable[[], Optional[str]]] = None,
        viewport_resolver: Optional[Callable] = None,
    ):
        self._settings = carb.settings.get_settings()
        self._sync_settings = sync_settings
        self._scene_file_provider = scene_file_provider or _default_scene_file
        self._anns: List[dict] = []
        self._visible = True
        if sync_settings and self._settings.get(S_VISIBLE) is not None:
            self._visible = bool(self._settings.get(S_VISIBLE))
        self._listeners: List[Callable[[str], None]] = []
        self._closed = False
        self._dispatch_warned = False
        self._selected: Optional[str] = None
        self._picker = Picker(viewport_resolver)
        self._faces = FaceComputer()
        self._face_tasks = set()  # pick_face: face computations in flight (cancelled on shutdown)
        self._face: Optional[dict] = None  # selected face (not an annotation, never saved)
        self._view_pick = None  # editor "pick in view" (extension): ``is_picking`` / ``cancel_pick()``

    # ------------------------------------------------------------------ settings
    def _max(self) -> int:
        v = self._settings.get(S_MAX)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return DEFAULT_MAX
        return v

    def _suffix(self) -> str:
        v = self._settings.get(S_SUFFIX)
        return v if isinstance(v, str) and v else DEFAULT_SUFFIX

    # ------------------------------------------------------------------ internal hooks
    def _add_listener(self, cb: Callable[[str], None]) -> None:
        """Internal: drawing / web are told about every change (reason)."""
        self._listeners.append(cb)

    def _remove_listener(self, cb: Callable[[str], None]) -> None:
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    def _snapshot(self) -> List[dict]:
        """Internal: the live list (read only, no copy) for drawing."""
        return self._anns

    def _shutdown(self) -> None:
        self._closed = True
        self._picker.shutdown()  # pending picks are cancelled (web replies "unavailable")
        for task in list(self._face_tasks):  # faces still being computed (worker builds are dropped)
            task.cancel()
        self._face_tasks.clear()
        self._faces.shutdown()
        self._view_pick = None
        self._listeners = []

    def _dispatch(self, event: str, payload: dict) -> None:
        try:
            from carb.eventdispatcher import get_eventdispatcher

            get_eventdispatcher().dispatch_event(event, payload=payload)
        except Exception as e:  # noqa: BLE001
            if not self._dispatch_warned:
                self._dispatch_warned = True
                carb.log_warn(f"[{EXT_ID}] dispatching {event} failed: {e}")

    def _emit(self, reason: str, ids: List[str], selection_was: Optional[str] = None) -> None:
        """Publish ``changed`` (+ listeners), then ``selection_changed`` if the change dropped the
        selection (``selection_was`` = the id selected before the change)."""
        self._dispatch(EVENT_CHANGED, {"reason": reason, "ids": list(ids), "count": len(self._anns)})
        self._notify(reason)
        if selection_was is not None and self._selected is None:
            self._emit_selection()

    def _emit_selection(self) -> None:
        self._dispatch(EVENT_SELECTION_CHANGED, {"id": self._selected})
        self._notify("select")

    def _drop_selection(self, keep_if_present: bool = False) -> Optional[str]:
        """Clear the selection (silently). Returns the previous id when it was cleared."""
        was = self._selected
        if was is None:
            return None
        if keep_if_present and self._index(was) >= 0:
            return None
        self._selected = None
        return was

    def _notify(self, reason: str) -> None:
        for cb in list(self._listeners):
            try:
                cb(reason)
            except Exception as e:  # noqa: BLE001
                carb.log_error(f"[{EXT_ID}] change listener failed: {e}")

    def _index(self, ann_id: str) -> int:
        for i, a in enumerate(self._anns):
            if a["id"] == ann_id:
                return i
        return -1

    # ------------------------------------------------------------------ public API
    def list(self) -> List[dict]:  # noqa: A003
        return copy.deepcopy(self._anns)

    def get(self, ann_id: str) -> Optional[dict]:
        i = self._index(ann_id)
        return copy.deepcopy(self._anns[i]) if i >= 0 else None

    def add(self, ann: dict) -> str:
        if len(self._anns) >= self._max():
            raise AnnotationLimitError(f"maxAnnotations {self._max()} reached")
        new = normalize(ann)
        taken = {a["id"] for a in self._anns}
        if "id" in new:
            if new["id"] in taken:
                raise ValueError(f"duplicate id {new['id']!r}")
        else:
            new = {"id": next_id(new["kind"], taken), **new}
        self._anns.append(new)
        self._emit("add", [new["id"]])
        return new["id"]

    def update(self, ann_id: str, **fields) -> dict:
        i = self._index(ann_id)
        if i < 0:
            raise KeyError(ann_id)
        old = self._anns[i]
        for key in ("id", "kind"):
            if key in fields and fields[key] != old[key]:
                raise ValueError(f"'{key}' cannot be changed")
        merged = dict(old)
        for key, value in fields.items():
            if value is None:
                merged.pop(key, None)  # None = back to default (required fields then fail)
            else:
                merged[key] = value
        merged["id"] = old["id"]
        merged["kind"] = old["kind"]
        new = normalize(merged, require_id=True)
        self._anns[i] = new
        self._emit("update", [ann_id])
        return copy.deepcopy(new)

    def remove(self, ann_id: str) -> None:
        i = self._index(ann_id)
        if i < 0:
            raise KeyError(ann_id)
        del self._anns[i]
        was = self._drop_selection(keep_if_present=True)
        self._emit("remove", [ann_id], was)

    def clear(self) -> None:
        self._anns = []
        was = self._drop_selection()
        self._emit("clear", [], was)

    def set_all(self, anns: list) -> None:
        if not isinstance(anns, (list, tuple)):
            raise ValueError("annotations must be a list")
        if len(anns) > self._max():
            raise AnnotationLimitError(f"{len(anns)} annotations exceed maxAnnotations {self._max()}")
        normalized = []
        for i, a in enumerate(anns):
            try:
                normalized.append(normalize(a))
            except ValueError as e:
                raise ValueError(f"annotations[{i}]: {e}") from None
        explicit = [a["id"] for a in normalized if "id" in a]
        if len(explicit) != len(set(explicit)):
            dup = sorted({x for x in explicit if explicit.count(x) > 1})
            raise ValueError(f"duplicate id(s): {', '.join(dup)}")
        taken = set(explicit)
        out = []
        for a in normalized:
            if "id" not in a:
                a = {"id": next_id(a["kind"], taken), **a}
                taken.add(a["id"])
            out.append(a)
        self._anns = out
        was = self._drop_selection(keep_if_present=True)
        self._emit("set_all", [], was)

    @property
    def visible(self) -> bool:
        return self._visible

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._visible:
            return
        self._visible = visible
        if self._sync_settings:
            self._settings.set_bool(S_VISIBLE, visible)
        self._emit("visible", [])

    @property
    def current_file(self) -> Optional[str]:
        return sidecar_path(self._scene_file_provider(), self._suffix())

    def load(self, path: Optional[str] = None) -> int:
        target = path if path is not None else self.current_file
        if not target:
            raise RuntimeError("no sidecar path: the current stage is not a file")
        anns, warnings = read_sidecar(target, self._max())  # ValueError keeps the current list
        for w in warnings:
            carb.log_warn(f"[{EXT_ID}] {target}: {w}")
        self._anns = anns or []
        was = self._drop_selection()
        self._emit("load", [], was)
        return len(self._anns)

    def save(self, path: Optional[str] = None) -> str:
        target = path if path is not None else self.current_file
        if not target:
            raise RuntimeError("no sidecar path: the current stage is not a file")
        return write_sidecar(target, self._anns)

    # ------------------------------------------------------------------ selection
    @property
    def selected_id(self) -> Optional[str]:
        return self._selected

    def select(self, ann_id: Optional[str]) -> None:
        if ann_id is not None and self._index(ann_id) < 0:
            raise KeyError(ann_id)
        if ann_id == self._selected:
            return
        self._selected = ann_id
        self._emit_selection()

    # ------------------------------------------------------------------ pick
    def pick(self, viewport_name: str, x: float, y: float):
        """World hit under normalized (x, y) of a viewport's rendered image (origin top-left).

        Returns an awaitable resolving to ``{"position": [..], "normal": [..] | None}`` or ``None``
        (empty space). ``KeyError`` (no viewport) and ``ValueError`` (x / y) are raised right away.
        """
        return self._picker.pick(viewport_name, x, y)

    def _pick_api(self, viewport_api, x: float, y: float):
        """Internal: pick on a viewport API (editor click path)."""
        return self._picker.pick_api(viewport_api, x, y)

    def _set_view_pick(self, provider) -> None:
        """Internal: the editor controller (has ``is_picking`` and ``cancel_pick()``), or None."""
        self._view_pick = provider

    @property
    def is_picking(self) -> bool:
        """True while the editor's [Pick in view] waits for a viewport click or for its query."""
        vp = self._view_pick
        try:
            return bool(vp is not None and vp.is_picking)
        except Exception:  # noqa: BLE001
            return False

    def cancel_pick(self) -> None:
        """Cancel the editor's pending [Pick in view] (Kit click selection is restored). No-op otherwise.

        Does not touch futures returned by ``pick()``.
        """
        vp = self._view_pick
        if vp is None:
            return
        try:
            vp.cancel_pick()
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] cancelling the view pick failed: {e}")

    # ------------------------------------------------------------------ faces
    def pick_face(self, viewport_name: str, x: float, y: float):
        """Face under normalized (x, y) of a viewport's rendered image (same query as ``pick``).

        Returns an awaitable resolving to ``{"position": [..], "face": Face}`` or ``None`` (empty
        space). Does not select the face. Errors as ``pick``.
        """
        x, y = validate_pick_args(x, y)
        api = self._picker.resolve(viewport_name)
        return self._pick_face_api(api, x, y)

    def _pick_face_api(self, viewport_api, x: float, y: float):
        """Internal: face pick on a viewport API (editor click path)."""
        inner = self._picker.pick_api(viewport_api, x, y, raw=True)  # only ValueError right away
        loop = asyncio.get_event_loop()
        outer = loop.create_future()
        holder = {}

        def task_done(t) -> None:
            self._face_tasks.discard(t)
            if outer.done():
                return
            if t.cancelled():
                outer.cancel()
                return
            exc = t.exception()
            if exc is not None:
                outer.set_exception(exc)
                return
            outer.set_result(t.result())

        def done(f) -> None:
            if outer.done():
                return
            if f.cancelled():
                outer.cancel()
                return
            exc = f.exception()
            if exc is not None:
                outer.set_exception(exc)
                return
            hit = f.result()
            if hit is None:
                outer.set_result(None)
                return
            if self._closed:
                outer.cancel()
                return
            # the face (large mesh: built on a worker thread) is finished on the main thread
            task = asyncio.ensure_future(self._face_result(viewport_api, hit))
            self._face_tasks.add(task)
            holder["task"] = task
            task.add_done_callback(task_done)

        def outer_done(o) -> None:
            if not o.cancelled():
                return
            if not inner.done():
                inner.cancel()  # caller gave up: drop the query job too
            task = holder.get("task")
            if task is not None and not task.done():
                task.cancel()

        if inner.done():
            done(inner)  # failed to start: the returned future is already done (like pick)
        else:
            inner.add_done_callback(done)
        outer.add_done_callback(outer_done)
        return outer

    async def _face_result(self, viewport_api, hit: dict) -> dict:
        """``{"position", "face"}`` of a raw hit (``position``, ``normal``, ``path``). The path is used
        here only (and as the in-memory mesh cache key)."""
        pos = hit["position"]
        toward = toward_camera(viewport_api, pos)
        try:
            stage = _viewport_stage(viewport_api)
            time = _viewport_time(viewport_api, stage)
            face = await self._faces.compute_async(stage, hit.get("path"), pos, hit.get("normal"), toward, time)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] face lookup failed, using the hit point: {e}")
            face = point_face(pos, hit.get("normal"), toward)
        return {"position": list(pos), "face": face}

    @property
    def selected_face(self) -> Optional[dict]:
        return copy.deepcopy(self._face)

    def select_face(self, face: Optional[dict]) -> None:
        """Select a face (``None`` = clear): highlighted with its centre on every viewport.

        ``ValueError`` for a malformed face (nothing changes). Same value: no event.
        """
        new = None if face is None else normalize_face(face)
        if new == self._face:
            return
        self._face = new
        self._dispatch(EVENT_FACE_SELECTED, {"face": copy.deepcopy(new)})
        self._notify("face")

    def _reset_faces(self) -> None:
        """Stage opened / closed: drop the mesh cache and the face selection."""
        self._faces.clear()
        if self._face is not None:
            self.select_face(None)

    # ------------------------------------------------------------------ stage hooks (extension)
    def _on_stage_opened(self) -> None:
        """autoLoad on stage OPENED: clear, then read the sidecar (missing / bad file = empty)."""
        target = self.current_file
        anns: List[dict] = []
        if target:
            try:
                read, warnings = read_sidecar(target, self._max())
                for w in warnings:
                    carb.log_warn(f"[{EXT_ID}] {target}: {w}")
                anns = read or []
            except (ValueError, OSError) as e:
                carb.log_warn(f"[{EXT_ID}] sidecar {target} not loaded: {e}")
        self._anns = anns
        was = self._drop_selection()
        self._emit("load", [], was)

    def _on_stage_closed(self) -> None:
        if not self._anns:
            return
        self._anns = []
        was = self._drop_selection()
        self._emit("load", [], was)

