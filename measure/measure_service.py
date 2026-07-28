"""Public API for the measure tool.

Everything outside this package should go through MeasureService and nothing
else. The classmethods are pure delegation; all state lives in measure.py.

Viewports are keyed by ViewportAPI.id and grouped by the tab that owns them.
A tab holds 1, 2 or 4 viewport widget hosts. Hook the host events and nothing
else needs wiring:

    MeasureService.on_tab_created(tab_id, vphs)
    MeasureService.on_tab_activated(tab_id)
    MeasureService.on_tab_closed(tab_id)
    MeasureService.on_viewport_selected(viewport_id)
    MeasureService.on_viewport_maximized(viewport_id)
    MeasureService.on_viewport_minimized(viewport_id)

Then measuring is two calls:

    MeasureService.set_snap_mode(SnapMode.VERTEX | SnapMode.EDGE)
    MeasureService.pick_one()                    # next two clicks make a line

set_enabled() is a single global switch, and only gates new picks: hiding,
removing and clearing lines work whether it is on or off.
"""

from __future__ import annotations

from .measure import (  # re-exported so callers never import measure.py
    Line,
    MeasureCore,
    SnapKind,
    SnapMode,
    SnapPoint,
    Subscription,
)

__all__ = [
    "MeasureService",
    "SnapMode",
    "SnapKind",
    "SnapPoint",
    "Line",
    "Subscription",
]


class MeasureService:

    # --------------------------------------------------------- a. on / off

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        """Global on/off for measuring. There is no per-viewport switch.

        Off only stops new picks. Existing lines stay on screen, and
        set_visible / remove / clear all keep working.
        """
        MeasureCore.set_enabled(enabled)

    @classmethod
    def is_enabled(cls) -> bool:
        return MeasureCore.is_enabled()

    @classmethod
    def list_viewport_ids(cls, tab_id=None) -> tuple:
        """Every registered viewport id, or only those belonging to one tab."""
        return MeasureCore.list_viewport_ids(tab_id)

    @classmethod
    def register_vph(cls, vph) -> str:
        """Register one viewport widget host. Returns its viewport id.

        Reads vph.viewport_api.id, vph.tab_id and vph.ui_frame, and builds the
        overlay. Normally reached through on_tab_created().
        """
        return MeasureCore.register_vph(vph)

    @classmethod
    def register_tab(cls, tab_id: str, vphs) -> tuple:
        """Register a tab and all of its hosts. Returns the viewport ids."""
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def unregister_tab(cls, tab_id: str) -> None:
        """Tab closed: drops its viewports and every line drawn in them."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def unregister_viewport(cls, viewport_id: str) -> None:
        """Drop one viewport and its lines."""
        MeasureCore.unregister_viewport(viewport_id)

    # ------------------------------------------------------- host events
    #
    # Call these from the tab/viewport events. They are the whole integration
    # surface: everything below is available directly, but nothing else has to
    # be wired up if these are hooked in.

    @classmethod
    def on_tab_created(cls, tab_id: str, vphs) -> tuple:
        """A tab was built. Registers its hosts and builds their overlays.

        This does not steal viewport input: the overlay only captures clicks
        between pick_one() and the second click.
        """
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def on_tab_activated(cls, tab_id: str) -> None:
        """A tab came to the front. Everything else stops drawing."""
        MeasureCore.set_active_tab(tab_id)

    @classmethod
    def on_tab_closed(cls, tab_id: str) -> None:
        """A tab went away for good. Its lines go with it."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def on_viewport_selected(cls, viewport_id: str) -> None:
        """Becomes the target of pick_one() when called without an id."""
        MeasureCore.set_selected_viewport(viewport_id)

    @classmethod
    def on_viewport_maximized(cls, viewport_id: str) -> None:
        """One viewport grew over its siblings; only it draws in that tab."""
        MeasureCore.set_maximized(viewport_id)

    @classmethod
    def on_viewport_minimized(cls, viewport_id: str) -> None:
        """Back to the normal grid; every viewport in the tab draws again."""
        MeasureCore.clear_maximized(MeasureCore.get_tab_of(viewport_id))

    # ------------------------------------------------------------- a2. tabs

    @classmethod
    def set_active_tab(cls, tab_id) -> None:
        """Only the active tab draws and accepts clicks.

        Pass None to lift the filter so every tab draws.
        """
        MeasureCore.set_active_tab(tab_id)

    @classmethod
    def get_active_tab(cls):
        return MeasureCore.get_active_tab()

    @classmethod
    def list_tabs(cls) -> tuple:
        return MeasureCore.list_tabs()

    @classmethod
    def get_tab_of(cls, viewport_id: str) -> str:
        return MeasureCore.get_tab_of(viewport_id)

    @classmethod
    def get_maximized(cls, tab_id: str):
        """The viewport eclipsing its siblings in that tab, or None."""
        return MeasureCore.get_maximized(tab_id)

    @classmethod
    def get_selected_viewport(cls) -> str:
        return MeasureCore.get_selected_viewport()

    # ------------------------------------------------------ b. snap mode

    @classmethod
    def set_snap_mode(cls, mode: SnapMode) -> None:
        """Global, not per viewport. SURFACE is always the fallback."""
        MeasureCore.set_snap_mode(mode)

    @classmethod
    def get_snap_mode(cls) -> SnapMode:
        return MeasureCore.get_snap_mode()

    @classmethod
    def get_current_snap(cls, viewport_id: str):
        """Last resolved hover snap, or None. Cheap: reads a cached value."""
        return MeasureCore.get_current_snap(viewport_id)

    # ----------------------------------------------------------- c. pick

    @classmethod
    def pick_one(cls, viewport_id=None, on_done=None) -> None:
        """Arm the viewport: the next two clicks place one line.

        Defaults to the viewport last passed to on_viewport_selected().

        The line is drawn and registered on its own. on_done(line) is an
        optional hook for extra work at completion; it is not called if the
        pick is cancelled.
        """
        MeasureCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id=None) -> None:
        MeasureCore.cancel_pick(viewport_id)

    # --------------------------------------------------------- d. lines

    @classmethod
    def get_lines(cls, viewport_id=None, tab_id=None) -> tuple:
        """All lines, narrowed by viewport and/or tab."""
        return MeasureCore.get_lines(viewport_id, tab_id)

    @classmethod
    def remove(cls, line_id: int) -> bool:
        """Remove one line by its unique id. False if there was no such line."""
        return MeasureCore.remove(line_id)

    @classmethod
    def clear(cls, viewport_id=None, tab_id=None) -> None:
        """Remove every line, narrowed by viewport and/or tab."""
        MeasureCore.clear(viewport_id, tab_id)

    # ---------------------------------------------------- e. visibility

    @classmethod
    def set_visible(
        cls, visible: bool, line_id=None, viewport_id=None, tab_id=None
    ) -> None:
        """Four tiers, most specific first:

        line_id     -> that one line
        viewport_id -> everything in that viewport
        tab_id      -> everything in that tab
        none of them-> everything, everywhere

        This is user intent. The active tab gates drawing on top of it, so a
        line made visible here still stays hidden while its tab is inactive.
        """
        MeasureCore.set_visible(
            visible, line_id=line_id, viewport_id=viewport_id, tab_id=tab_id
        )

    # -------------------------------------------------------- change feed

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        """Called after any add / remove / clear / visibility change.

        Carries no payload: re-read get_lines(). Keep the returned handle alive,
        dropping it unsubscribes.
        """
        return MeasureCore.subscribe_changed(fn)
