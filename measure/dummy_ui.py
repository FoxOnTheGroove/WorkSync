"""Test UI for the measure tool.

Deliberately built on measure_service only. If something needed here cannot be
done through MeasureService, the service API is incomplete: fix the API rather
than reaching into measure.py.

It knows nothing about tabs or viewports. Those arrive through the host events
the extension wires up, and pick_one() follows whichever viewport was last
selected.
"""

from __future__ import annotations

import omni.ui as ui

from .measure_service import MeasureService, SnapMode

_SNAP_FLAGS = (
    ("Vertex", SnapMode.VERTEX),
    ("Edge", SnapMode.EDGE),
    ("Mid-point", SnapMode.MIDPOINT),
)


class MeasureDummyUI:
    def __init__(self):
        self._window = None
        self._sub = None
        self._list_frame = None

    def build_ui(self):
        self._window = ui.Window("Measure (dummy)", width=320, height=420)
        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                self._build_enabled_row()
                ui.Separator()
                self._build_snap_row()
                ui.Separator()
                self._build_actions()
                ui.Separator()
                ui.Label("Lines", height=20)
                with ui.ScrollingFrame(height=200):
                    # build_fn + rebuild() so the list is rebuilt on the next
                    # frame. Rebuilding it inline would mean replacing the
                    # container from inside its own button callback, which
                    # omni.ui warns about as "addChild during draw callback".
                    self._list_frame = ui.Frame(build_fn=self._build_list)

        self._sub = MeasureService.subscribe_changed(self._refresh_list)

    # ------------------------------------------------------------------ rows

    def _build_enabled_row(self):
        with ui.HStack(height=24, spacing=6):
            ui.Label("Enabled", width=80)
            check = ui.CheckBox()
            check.model.set_value(MeasureService.is_enabled())
            check.model.add_value_changed_fn(
                lambda m: MeasureService.set_enabled(m.get_value_as_bool())
            )
        ui.Label(
            "Off stops new picks only. Hide and clear keep working.",
            height=18,
            style={"color": 0xFF999999},
        )

    def _build_snap_row(self):
        ui.Label("Snap (global)", height=20)
        mode = MeasureService.get_snap_mode()
        for label, flag in _SNAP_FLAGS:
            with ui.HStack(height=22, spacing=6):
                ui.Label(label, width=80)
                check = ui.CheckBox()
                check.model.set_value(bool(mode & flag))
                check.model.add_value_changed_fn(
                    lambda m, f=flag: self._toggle_snap(f, m.get_value_as_bool())
                )
        ui.Label(
            "Surface is always on as the fallback.",
            height=18,
            style={"color": 0xFF999999},
        )

    def _build_actions(self):
        with ui.HStack(height=26, spacing=6):
            ui.Button("Pick one", clicked_fn=self._pick_one)
            ui.Button("Cancel", clicked_fn=lambda: MeasureService.cancel_pick())
        with ui.HStack(height=26, spacing=6):
            ui.Button("Show all", clicked_fn=lambda: MeasureService.set_visible(True))
            ui.Button("Hide all", clicked_fn=lambda: MeasureService.set_visible(False))
            ui.Button("Clear all", clicked_fn=lambda: MeasureService.clear())

    # --------------------------------------------------------------- actions

    def _toggle_snap(self, flag: SnapMode, on: bool):
        mode = MeasureService.get_snap_mode()
        MeasureService.set_snap_mode(mode | flag if on else mode & ~flag)

    def _pick_one(self):
        MeasureService.pick_one(on_done=self._on_line_done)

    def _on_line_done(self, line):
        print(f"[measure] line {line.id} on '{line.viewport_id}': {line.length_m:.3f} m")

    # ------------------------------------------------------------------ list

    def _refresh_list(self):
        if self._list_frame is not None:
            self._list_frame.rebuild()

    def _build_list(self):
        lines = MeasureService.get_lines()
        with ui.VStack(spacing=2, height=0):
            if not lines:
                ui.Label("(none)", height=20, style={"color": 0xFF999999})
                return
            for line in lines:
                with ui.HStack(height=22, spacing=4):
                    ui.Label(f"#{line.id}", width=32)
                    ui.Label(f"{line.length_m:.3f} m", width=90)
                    ui.Button(
                        "hide" if line.visible else "show",
                        width=44,
                        clicked_fn=lambda i=line.id, v=line.visible: (
                            MeasureService.set_visible(not v, line_id=i)
                        ),
                    )
                    ui.Button(
                        "x",
                        width=24,
                        clicked_fn=lambda i=line.id: MeasureService.remove(i),
                    )

    # -------------------------------------------------------------- teardown

    def destroy(self):
        self._sub = None
        self._list_frame = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
