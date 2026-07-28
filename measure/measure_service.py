"""Public API for the measure tool.

Everything outside this package should go through MeasureService and nothing
else. The classmethods are pure delegation; all state lives in measure.py.

Viewports are keyed by ViewportAPI.id and grouped by the tab that owns them.
A tab holds 1, 2 or 4 viewport widget hosts; register them when the tab is
built, and tell the service which tab is active so only that tab draws.

    MeasureService.register_tab(tab.id, tab.vphs)
    MeasureService.set_tab_enabled(tab.id, True)
    MeasureService.set_active_tab(tab.id)        # on tab switch

    MeasureService.set_snap_mode(SnapMode.VERTEX | SnapMode.EDGE)
    MeasureService.pick_one(vp)                  # next two clicks make a line
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
    def set_enabled(cls, viewport_id: str, enabled: bool) -> None:
        """Turn the tool on or off for one viewport."""
        MeasureCore.set_enabled(viewport_id, enabled)

    @classmethod
    def is_enabled(cls, viewport_id: str) -> bool:
        return MeasureCore.is_enabled(viewport_id)

    @classmethod
    def list_viewport_ids(cls, tab_id=None) -> tuple:
        """Every known viewport id, or only those belonging to one tab."""
        return MeasureCore.list_viewport_ids(tab_id)

    @classmethod
    def register_vph(cls, vph) -> str:
        """Register one viewport widget host. Returns its viewport id.

        Reads vph.viewport_api.id, vph.tab_id and vph.ui_frame.
        """
        return MeasureCore.register_vph(vph)

    @classmethod
    def register_tab(cls, tab_id: str, vphs) -> tuple:
        """Register a tab and all of its hosts at creation time.

            ids = MeasureService.register_tab(tab.id, tab.vphs)
            MeasureService.set_tab_enabled(tab.id, True)

        Returns the viewport ids, in the order the hosts were given.
        """
        return MeasureCore.register_tab(tab_id, vphs)

    @classmethod
    def unregister_tab(cls, tab_id: str) -> None:
        """Tab closed: drops its viewports and every line drawn in them."""
        MeasureCore.unregister_tab(tab_id)

    @classmethod
    def unregister_viewport(cls, viewport_id: str) -> None:
        """Drop one viewport, disabling it and removing its lines."""
        MeasureCore.unregister_viewport(viewport_id)

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
    def set_tab_enabled(cls, tab_id: str, enabled: bool) -> None:
        """Turn the tool on or off for every viewport in a tab."""
        MeasureCore.set_tab_enabled(tab_id, enabled)

    @classmethod
    def get_tab_of(cls, viewport_id: str) -> str:
        return MeasureCore.get_tab_of(viewport_id)

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
    def pick_one(cls, viewport_id: str, on_done=None) -> None:
        """Arm the viewport: the next two clicks place one line.

        The line is drawn and registered on its own. on_done(line) is an
        optional hook for extra work at completion; it is not called if the
        pick is cancelled.
        """
        MeasureCore.pick_one(viewport_id, on_done)

    @classmethod
    def cancel_pick(cls, viewport_id: str) -> None:
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
