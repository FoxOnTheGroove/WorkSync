"""Public API for the measure tool.

Everything outside this package should go through MeasureService and nothing
else. The classmethods are pure delegation; all state lives in measure.py.

Viewport ids are ViewportAPI.id, not window names. Discover them with
list_viewport_ids().

    vp = MeasureService.list_viewport_ids()[0]
    MeasureService.set_enabled(vp, True)
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
    def list_viewport_ids(cls) -> tuple:
        """Ids of every known viewport, in the form every other call expects."""
        return MeasureCore.list_viewport_ids()

    @classmethod
    def register_viewport(cls, viewport_api, frame=None) -> str:
        """Make a viewport addressable, and say where to draw its overlay.

        Needed for ViewportWidget, which has no ViewportWindow to discover or
        to host the overlay:

            widget = ViewportWidget(...)
            with ui.ZStack():
                ...                       # the widget
                overlay_frame = ui.Frame()
            vp = MeasureService.register_viewport(
                widget.viewport_api, overlay_frame
            )
            MeasureService.set_enabled(vp, True)

        Viewports inside a ViewportWindow are found on their own; registering
        one anyway is harmless and takes precedence.
        """
        return MeasureCore.register_viewport(viewport_api, frame)

    @classmethod
    def unregister_viewport(cls, viewport_id: str) -> None:
        """Drop a registration, disabling the tool there first."""
        MeasureCore.unregister_viewport(viewport_id)

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
    def get_lines(cls, viewport_id=None) -> tuple:
        """All lines, or only those belonging to one viewport."""
        return MeasureCore.get_lines(viewport_id)

    @classmethod
    def remove(cls, line_id: int) -> bool:
        """Remove one line by its unique id. False if there was no such line."""
        return MeasureCore.remove(line_id)

    @classmethod
    def clear(cls, viewport_id=None) -> None:
        """Remove every line, or every line in one viewport."""
        MeasureCore.clear(viewport_id)

    # ---------------------------------------------------- e. visibility

    @classmethod
    def set_visible(cls, visible: bool, line_id=None, viewport_id=None) -> None:
        """Three tiers, most specific first:

        line_id     -> that one line
        viewport_id -> everything in that viewport
        neither     -> everything, everywhere
        """
        MeasureCore.set_visible(visible, line_id=line_id, viewport_id=viewport_id)

    # -------------------------------------------------------- change feed

    @classmethod
    def subscribe_changed(cls, fn) -> Subscription:
        """Called after any add / remove / clear / visibility change.

        Carries no payload: re-read get_lines(). Keep the returned handle alive,
        dropping it unsubscribes.
        """
        return MeasureCore.subscribe_changed(fn)
