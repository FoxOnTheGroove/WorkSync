"""Web messages (via morph.stream.messaging 1.x) and the coalesced state push."""

__all__ = [
    "WebBridge",
    "MSG_LIST",
    "MSG_ADD",
    "MSG_UPDATE",
    "MSG_REMOVE",
    "MSG_SET_VISIBLE",
    "MSG_SET_OPTIONS",
    "MSG_SAVE",
    "MSG_RELOAD",
    "MSG_PICK",
    "MSG_SELECT",
    "MSG_PICK_FACE",
    "MSG_CLEAR_FACE",
]

import asyncio
import math
import os
from typing import Callable, Optional

import carb
import carb.settings

from .model import AnnotationLimitError
from .overlay import (
    EXT_ID,
    MSG_STATE,
    S_SHOW_DESC,
    S_TEXT_SCALE,
    TEXT_SCALE_MAX,
    TEXT_SCALE_MIN,
    AnnotationOverlay,
    show_description_setting,
    text_scale_setting,
)

MSG_LIST = f"{EXT_ID}.list"
MSG_ADD = f"{EXT_ID}.add"
MSG_UPDATE = f"{EXT_ID}.update"
MSG_REMOVE = f"{EXT_ID}.remove"
MSG_SET_VISIBLE = f"{EXT_ID}.set_visible"
MSG_SET_OPTIONS = f"{EXT_ID}.set_options"
MSG_SAVE = f"{EXT_ID}.save"
MSG_RELOAD = f"{EXT_ID}.reload"
MSG_PICK = f"{EXT_ID}.pick"
MSG_SELECT = f"{EXT_ID}.select"
MSG_PICK_FACE = f"{EXT_ID}.pick_face"
MSG_CLEAR_FACE = f"{EXT_ID}.clear_face"

EVENT_CLIENT_CONNECTED = "morph.stream.client_connected"

_EMPTY = {"type": "object"}
_PICK = {
    "type": "object",
    "required": ["viewport_name", "x", "y"],
    "properties": {"viewport_name": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}},
}
_ANN = {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}}}
SCHEMAS = {
    MSG_LIST: _EMPTY,
    MSG_ADD: {"type": "object", "required": ["annotation"], "properties": {"annotation": {"type": "object"}}},
    MSG_UPDATE: {
        "type": "object",
        "required": ["id", "fields"],
        "properties": {"id": {"type": "string"}, "fields": {"type": "object"}},
    },
    MSG_REMOVE: {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
    MSG_SET_VISIBLE: {
        "type": "object",
        "required": ["visible"],
        "properties": {"visible": {"type": "boolean"}, "id": {"type": "string"}},
    },
    MSG_SET_OPTIONS: {
        "type": "object",
        "properties": {"show_description": {"type": "boolean"}, "text_scale": {"type": "number"}},
    },
    MSG_SAVE: _EMPTY,
    MSG_RELOAD: _EMPTY,
    MSG_PICK: _PICK,
    MSG_SELECT: {"type": "object", "required": ["id"], "properties": {"id": {"type": ["string", "null"]}}},
    MSG_PICK_FACE: _PICK,
    MSG_CLEAR_FACE: _EMPTY,
}
STATE_SCHEMA = {
    "type": "object",
    "required": ["annotations", "visible", "file", "show_description", "text_scale", "selected_id", "face"],
    "properties": {
        "annotations": {"type": "array", "items": _ANN},
        "visible": {"type": "boolean"},
        "file": {"type": ["string", "null"]},
        "show_description": {"type": "boolean"},
        "text_scale": {"type": "number"},
        "selected_id": {"type": ["string", "null"]},
        "face": {"type": ["object", "null"]},
    },
}


def _file_label(path: Optional[str]) -> str:
    return os.path.basename(path) if path else "sidecar"


def _os_error_message(e: OSError, path: Optional[str]) -> str:
    """Web-safe OSError text: error class, ``strerror`` and the file *name* (never a directory)."""
    reason = e.strerror or "I/O error"
    return f"{type(e).__name__}: {reason} ({_file_label(path)})"


def _default_messaging():
    try:
        from morph.stream.messaging import get_messaging

        return get_messaging()
    except Exception:  # noqa: BLE001
        return None


class WebBridge:
    """Registers the 12 inbound types + the state push. ``messaging_getter`` is injectable for tests."""

    def __init__(self, overlay: AnnotationOverlay, messaging_getter: Optional[Callable] = None):
        self._overlay = overlay
        self._get_messaging = messaging_getter or _default_messaging
        self._subs = []
        self._conn_sub = None
        self._push_pending = False
        self._closed = False
        overlay._add_listener(self._on_changed)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        msg = self._get_messaging()
        if msg is None:
            carb.log_warn(f"[{EXT_ID}] morph.stream.messaging unavailable: web messages disabled")
            return
        handlers = {
            MSG_LIST: self._on_list,
            MSG_ADD: self._on_add,
            MSG_UPDATE: self._on_update,
            MSG_REMOVE: self._on_remove,
            MSG_SET_VISIBLE: self._on_set_visible,
            MSG_SET_OPTIONS: self._on_set_options,
            MSG_SAVE: self._on_save,
            MSG_RELOAD: self._on_reload,
            MSG_PICK: self._on_pick,
            MSG_SELECT: self._on_select,
            MSG_PICK_FACE: self._on_pick_face,
            MSG_CLEAR_FACE: self._on_clear_face,
        }
        self._subs.append(msg.register_outbound(MSG_STATE, schema=STATE_SCHEMA, owner=EXT_ID))
        for type_, handler in handlers.items():
            self._subs.append(msg.register_inbound(type_, handler, schema=SCHEMAS[type_], owner=EXT_ID))
        try:
            from carb.eventdispatcher import get_eventdispatcher

            self._conn_sub = get_eventdispatcher().observe_event(
                observer_name=f"{EXT_ID}:client_connected",
                event_name=EVENT_CLIENT_CONNECTED,
                on_event=lambda _e: self.request_push(),
            )
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] client_connected subscription failed: {e}")

    def stop(self) -> None:
        self._closed = True
        self._overlay._remove_listener(self._on_changed)
        for sub in self._subs:
            try:
                sub.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        self._subs = []
        self._conn_sub = None
        self._push_pending = False

    # ------------------------------------------------------------------ state push
    def _on_changed(self, _reason: str) -> None:
        self._push_pending = True

    def request_push(self) -> None:
        self._push_pending = True

    @property
    def push_pending(self) -> bool:
        return self._push_pending

    def state_payload(self) -> dict:
        return {
            "annotations": self._overlay.list(),
            "visible": self._overlay.visible,
            "file": self._file_name(),
            **self.options_payload(),
            "selected_id": self._overlay.selected_id,
            "face": self._overlay.selected_face,
        }

    @staticmethod
    def options_payload() -> dict:
        return {"show_description": show_description_setting(), "text_scale": text_scale_setting()}

    def flush(self) -> bool:
        """Called once per update: send at most one state push. Returns True if sent."""
        if not self._push_pending or self._closed:
            return False
        self._push_pending = False
        msg = self._get_messaging()
        if msg is None or not msg.is_connected:
            return False
        try:
            msg.send(MSG_STATE, self.state_payload())
        except ValueError as e:
            carb.log_error(f"[{EXT_ID}] state push failed: {e}")
            return False
        return True

    def _file_name(self) -> Optional[str]:
        path = self._overlay.current_file
        return os.path.basename(path) if path else None

    # ------------------------------------------------------------------ handlers
    def _unavailable(self, ctx) -> bool:
        if self._closed or self._overlay._closed:
            ctx.reply_error("unavailable", "annotation overlay is shutting down")
            return True
        return False

    def _on_list(self, payload, ctx):
        if self._unavailable(ctx):
            return
        ctx.reply_ok(**self.state_payload())

    def _on_add(self, payload, ctx):
        if self._unavailable(ctx):
            return
        ann = payload.get("annotation")
        try:
            new_id = self._overlay.add(ann)
        except AnnotationLimitError as e:
            ctx.reply_error("resource_exhausted", str(e))
            return
        except ValueError as e:
            ctx.reply_error("invalid_argument", str(e))
            return
        ctx.reply_ok(id=new_id)

    def _on_update(self, payload, ctx):
        if self._unavailable(ctx):
            return
        ann_id = payload.get("id")
        fields = payload.get("fields")
        if not isinstance(ann_id, str) or not isinstance(fields, dict):
            ctx.reply_error("invalid_argument", "'id' (string) and 'fields' (object) are required")
            return
        try:
            ann = self._overlay.update(ann_id, **fields)
        except KeyError:
            ctx.reply_error("not_found", f"no annotation {ann_id!r}")
            return
        except (ValueError, TypeError) as e:
            ctx.reply_error("invalid_argument", str(e))
            return
        ctx.reply_ok(annotation=ann)

    def _on_remove(self, payload, ctx):
        if self._unavailable(ctx):
            return
        ann_id = payload.get("id")
        try:
            self._overlay.remove(ann_id)
        except KeyError:
            ctx.reply_error("not_found", f"no annotation {ann_id!r}")
            return
        ctx.reply_ok(id=ann_id)

    def _on_set_visible(self, payload, ctx):
        if self._unavailable(ctx):
            return
        visible = payload.get("visible")
        if not isinstance(visible, bool):
            ctx.reply_error("invalid_argument", "'visible' must be a boolean")
            return
        ann_id = payload.get("id")
        if ann_id is None:
            self._overlay.set_visible(visible)
            ctx.reply_ok()
            return
        try:
            self._overlay.update(ann_id, visible=visible)
        except KeyError:
            ctx.reply_error("not_found", f"no annotation {ann_id!r}")
            return
        except (ValueError, TypeError) as e:
            ctx.reply_error("invalid_argument", str(e))
            return
        ctx.reply_ok()

    def _on_set_options(self, payload, ctx):
        if self._unavailable(ctx):
            return
        show = payload.get("show_description")
        scale = payload.get("text_scale")
        if show is not None and not isinstance(show, bool):
            ctx.reply_error("invalid_argument", "'show_description' must be a boolean")
            return
        if scale is not None:
            if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(float(scale)):
                ctx.reply_error("invalid_argument", "'text_scale' must be a finite number")
                return
            if not TEXT_SCALE_MIN <= float(scale) <= TEXT_SCALE_MAX:
                ctx.reply_error(
                    "invalid_argument", f"'text_scale' must be in {TEXT_SCALE_MIN}..{TEXT_SCALE_MAX}, got {scale}"
                )
                return
        settings = carb.settings.get_settings()
        if show is not None:
            settings.set_string(S_SHOW_DESC, "always" if show else "never")
        if scale is not None:
            settings.set_float(S_TEXT_SCALE, float(scale))
        if show is not None or scale is not None:
            self.request_push()  # the extension's setting observers also mark the drawing dirty
        ctx.reply_ok(**self.options_payload())

    def _on_save(self, payload, ctx):
        if self._unavailable(ctx):
            return
        target = self._overlay.current_file
        if not target:
            ctx.reply_error("failed_precondition", "the current stage is not a file")
            return
        try:
            path = self._overlay.save()
        except RuntimeError as e:
            ctx.reply_error("failed_precondition", str(e))
            return
        except OSError as e:
            carb.log_warn(f"[{EXT_ID}] saving {target} failed: {e}")  # full path in the log only
            ctx.reply_error("io_error", _os_error_message(e, target))
            return
        ctx.reply_ok(file=os.path.basename(path))

    def _on_reload(self, payload, ctx):
        if self._unavailable(ctx):
            return
        target = self._overlay.current_file
        try:
            count = self._overlay.load()
        except RuntimeError as e:
            ctx.reply_error("failed_precondition", str(e))
            return
        except ValueError as e:  # sidecar messages carry the file name only
            carb.log_warn(f"[{EXT_ID}] reloading {target} failed: {e}")
            ctx.reply_error("failed_precondition", str(e))
            return
        except OSError as e:
            carb.log_warn(f"[{EXT_ID}] reloading {target} failed: {e}")
            ctx.reply_error("io_error", _os_error_message(e, target))
            return
        ctx.reply_ok(count=count)

    def _on_select(self, payload, ctx):
        if self._unavailable(ctx):
            return
        ann_id = payload.get("id")
        if ann_id is not None and not isinstance(ann_id, str):
            ctx.reply_error("invalid_argument", "'id' must be a string or null")
            return
        try:
            self._overlay.select(ann_id)
        except KeyError:
            ctx.reply_error("not_found", f"no annotation {ann_id!r}")
            return
        ctx.reply_ok(selected_id=self._overlay.selected_id)

    def _on_pick(self, payload, ctx):
        """Deferred reply: answered once the viewport query finishes (or fails / times out)."""
        if self._unavailable(ctx):
            return
        name = payload.get("viewport_name")
        try:
            future = self._overlay.pick(name, payload.get("x"), payload.get("y"))
        except KeyError:
            ctx.reply_error("not_found", f"no viewport {name!r}")
            return
        except ValueError as e:
            ctx.reply_error("invalid_argument", str(e))
            return
        except Exception as e:  # noqa: BLE001  (safety net: query failures arrive on the future)
            ctx.reply_error("unavailable", f"pick failed: {e}")
            return
        future.add_done_callback(lambda f: self._reply_pick(ctx, f))

    def _reply_pick(self, ctx, future) -> None:
        if getattr(ctx, "replied", False):
            return
        try:
            if self._closed or future.cancelled():
                ctx.reply_error("unavailable", "pick cancelled (annotation overlay shutting down)")
                return
            exc = future.exception()
            if exc is not None:
                kind = "timed out" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "failed"
                ctx.reply_error("unavailable", f"pick {kind}: {exc}")
                return
            result = future.result()
            if result is None:
                ctx.reply_ok(hit=False)
            else:
                ctx.reply_ok(hit=True, position=result["position"], normal=result["normal"])
        except Exception as e:  # noqa: BLE001
            carb.log_error(f"[{EXT_ID}] pick reply failed: {e}")

    def _on_pick_face(self, payload, ctx):
        """Deferred reply like ``pick``. A hit selects that face, a miss clears the face selection."""
        if self._unavailable(ctx):
            return
        name = payload.get("viewport_name")
        try:
            future = self._overlay.pick_face(name, payload.get("x"), payload.get("y"))
        except KeyError:
            ctx.reply_error("not_found", f"no viewport {name!r}")
            return
        except ValueError as e:
            ctx.reply_error("invalid_argument", str(e))
            return
        except Exception as e:  # noqa: BLE001  (safety net: query failures arrive on the future)
            ctx.reply_error("unavailable", f"pick_face failed: {e}")
            return
        future.add_done_callback(lambda f: self._reply_pick_face(ctx, f))

    def _reply_pick_face(self, ctx, future) -> None:
        if getattr(ctx, "replied", False):
            return
        try:
            if self._closed or future.cancelled():
                ctx.reply_error("unavailable", "pick_face cancelled (annotation overlay shutting down)")
                return
            exc = future.exception()
            if exc is not None:
                kind = "timed out" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "failed"
                ctx.reply_error("unavailable", f"pick_face {kind}: {exc}")
                return
            result = future.result()
            if result is None:
                self._select_face(None)
                ctx.reply_ok(hit=False)
                return
            self._select_face(result["face"])
            ctx.reply_ok(hit=True, position=result["position"], face=result["face"])
        except Exception as e:  # noqa: BLE001
            carb.log_error(f"[{EXT_ID}] pick_face reply failed: {e}")

    def _select_face(self, face) -> None:
        try:
            self._overlay.select_face(face)
        except Exception as e:  # noqa: BLE001  (a computed face is always valid)
            carb.log_error(f"[{EXT_ID}] selecting the picked face failed: {e}")

    def _on_clear_face(self, payload, ctx):
        if self._unavailable(ctx):
            return
        self._overlay.select_face(None)
        ctx.reply_ok()
