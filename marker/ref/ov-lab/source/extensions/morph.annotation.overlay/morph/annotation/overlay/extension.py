"""Extension entry: wires overlay data, stage events, settings, drawing, web."""

__all__ = ["AnnotationOverlayExtension"]

import carb
import carb.settings
import omni.ext

from .drawing import OverlayDrawing
from .editor import EditorController
from .fonts import warn_app_font_once
from .overlay import (
    EXT_ID,
    S_AUTOLOAD,
    S_EDITOR_WINDOW,
    S_SHOW_DESC,
    S_TEXT_SCALE,
    S_VISIBLE,
    AnnotationOverlay,
    _set_instance,
)
from .web import WebBridge


class AnnotationOverlayExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._settings = carb.settings.get_settings()
        warn_app_font_once()  # Hangul needs the app font settings (CONTRACT "한글 표시")
        self._overlay = AnnotationOverlay(sync_settings=True)
        _set_instance(self._overlay)

        self._drawing = OverlayDrawing(self._overlay)
        self._drawing.start()
        self._web = WebBridge(self._overlay)
        self._web.start()

        # Desktop editor: Window menu entry (if omni.kit.menu.utils is loaded) + open at startup
        self._editor = EditorController(self._overlay, self._drawing)
        self._overlay._set_view_pick(self._editor)  # AnnotationOverlay.is_picking / cancel_pick
        self._editor.add_menu()
        if bool(self._settings.get(S_EDITOR_WINDOW)):
            try:
                self._editor.show()
            except Exception as e:  # noqa: BLE001  (headless: no omni.ui window)
                carb.log_warn(f"[{EXT_ID}] cannot open the editor window: {e}")

        self._setting_subs = [
            self._settings.subscribe_to_node_change_events(S_VISIBLE, self._on_visible_setting),
            self._settings.subscribe_to_node_change_events(S_TEXT_SCALE, self._on_option_setting),
            self._settings.subscribe_to_node_change_events(S_SHOW_DESC, self._on_option_setting),
        ]

        from carb.eventdispatcher import get_eventdispatcher
        import omni.kit.app
        import omni.usd

        disp = get_eventdispatcher()
        ctx = omni.usd.get_context()
        self._stage_subs = [
            disp.observe_event(
                observer_name=f"{EXT_ID}:stage_opened",
                event_name=ctx.stage_event_name(omni.usd.StageEventType.OPENED),
                on_event=self._on_stage_opened,
            ),
            disp.observe_event(
                observer_name=f"{EXT_ID}:stage_closed",
                event_name=ctx.stage_event_name(omni.usd.StageEventType.CLOSED),
                on_event=self._on_stage_closed,
            ),
        ]
        self._update_sub = disp.observe_event(
            observer_name=f"{EXT_ID}:update",
            event_name=omni.kit.app.GLOBAL_EVENT_UPDATE,
            on_event=self._on_update,
        )

        # A stage may already be open (e.g. a startup scene opened before us).
        if self._autoload() and ctx.get_stage() is not None:
            self._overlay._on_stage_opened()

    def _autoload(self) -> bool:
        v = self._settings.get(S_AUTOLOAD)
        return True if v is None else bool(v)

    def _on_stage_opened(self, _e) -> None:
        self._overlay._reset_faces()  # mesh cache + face selection belong to the old scene
        if self._autoload():
            self._overlay._on_stage_opened()
        else:
            self._drawing.mark_dirty()  # stage up axis may differ
        self._web.request_push()  # file name changed

    def _on_stage_closed(self, _e) -> None:
        self._overlay._reset_faces()
        if self._autoload():
            self._overlay._on_stage_closed()
        self._web.request_push()

    def _on_visible_setting(self, _item, _event_type) -> None:
        v = self._settings.get(S_VISIBLE)
        self._overlay.set_visible(True if v is None else bool(v))

    def _on_option_setting(self, _item, _event_type) -> None:
        # textScale / showDescription: redraw and tell the web (state carries both)
        if self._drawing is not None:
            self._drawing.mark_dirty()
        if self._web is not None:
            self._web.request_push()

    def _on_update(self, _e) -> None:
        try:
            self._drawing.flush()
        except Exception as e:  # noqa: BLE001
            carb.log_error(f"[{EXT_ID}] drawing flush failed: {e}")
        self._web.flush()

    def on_shutdown(self):
        self._update_sub = None
        self._stage_subs = []
        for sub in getattr(self, "_setting_subs", []):
            try:
                self._settings.unsubscribe_to_change_events(sub)
            except Exception:  # noqa: BLE001
                pass
        self._setting_subs = []
        editor = getattr(self, "_editor", None)
        if editor is not None:
            if getattr(self, "_overlay", None) is not None:
                self._overlay._set_view_pick(None)
            editor.destroy()
            self._editor = None
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            overlay._shutdown()
        if getattr(self, "_web", None) is not None:
            self._web.stop()
            self._web = None
        if getattr(self, "_drawing", None) is not None:
            self._drawing.stop()
            self._drawing = None
        _set_instance(None)
        self._overlay = None
