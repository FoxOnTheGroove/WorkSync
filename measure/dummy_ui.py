"""Test UI for the measure tool.

Deliberately built on measure_service only. If something needed here cannot be
done through MeasureService, the service API is incomplete: fix the API rather
than reaching into measure.py.
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
        self._viewport_id = ""
        self._viewport_ids = ()
        self._sub = None
        self._snap_checks = {}
        self._enabled_check = None
        self._id_field = None
        self._viewport_frame = None
        self._tab_id = ""
        self._tab_ids = ()
        self._tab_frame = None
        self._list_frame = None

    def build_ui(self):
        self._window = ui.Window("Measure (dummy)", width=340, height=460)
        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                self._build_tab_row()
                ui.Separator()
                self._build_viewport_row()
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

    def _build_tab_row(self):
        with ui.HStack(height=24, spacing=6):
            ui.Label("Active tab", width=76)
            self._tab_frame = ui.Frame(build_fn=self._build_tab_combo)
            ui.Button("R", width=28, clicked_fn=self._refresh_tabs)
        with ui.HStack(height=26, spacing=6):
            ui.Button("Enable tab", clicked_fn=lambda: self._set_tab_enabled(True))
            ui.Button("Disable tab", clicked_fn=lambda: self._set_tab_enabled(False))
            ui.Button(
                "Clear tab",
                clicked_fn=lambda: MeasureService.clear(tab_id=self._tab_id),
            )

    def _build_tab_combo(self):
        self._tab_ids = MeasureService.list_tabs()
        if not self._tab_ids:
            ui.Label("(no tab registered)", style={"color": 0xFF999999})
            return
        if self._tab_id not in self._tab_ids:
            self._tab_id = self._tab_ids[0]
        combo = ui.ComboBox(self._tab_ids.index(self._tab_id), *self._tab_ids)
        combo.model.add_item_changed_fn(self._on_tab_picked)

    def _on_tab_picked(self, model, _item):
        index = model.get_item_value_model().get_value_as_int()
        if 0 <= index < len(self._tab_ids):
            self._tab_id = self._tab_ids[index]
            MeasureService.set_active_tab(self._tab_id)

    def _set_tab_enabled(self, enabled: bool):
        if self._tab_id:
            MeasureService.set_tab_enabled(self._tab_id, enabled)

    def _refresh_tabs(self):
        if self._tab_frame is not None:
            self._tab_frame.rebuild()

    def _build_viewport_row(self):
        # The field is authoritative so an id can always be typed in directly;
        # the combo below is only a convenience that fills it.
        with ui.HStack(height=24, spacing=6):
            ui.Label("Viewport id", width=76)
            self._id_field = ui.StringField()
            self._id_field.model.add_value_changed_fn(
                lambda m: setattr(self, "_viewport_id", m.get_value_as_string().strip())
            )
        with ui.HStack(height=24, spacing=6):
            ui.Label("Known", width=76)
            self._viewport_frame = ui.Frame(build_fn=self._build_viewport_combo)
            ui.Button("R", width=28, clicked_fn=self._refresh_viewports)
        with ui.HStack(height=24, spacing=6):
            ui.Label("Enabled", width=64)
            self._enabled_check = ui.CheckBox()
            self._enabled_check.model.add_value_changed_fn(
                lambda m: MeasureService.set_enabled(
                    self._viewport_id, m.get_value_as_bool()
                )
            )

    def _build_viewport_combo(self):
        self._viewport_ids = MeasureService.list_viewport_ids()
        if not self._viewport_ids:
            ui.Label("(none discovered - type an id above)", style={"color": 0xFF999999})
            return
        index = (
            self._viewport_ids.index(self._viewport_id)
            if self._viewport_id in self._viewport_ids
            else 0
        )
        combo = ui.ComboBox(index, *self._viewport_ids)
        combo.model.add_item_changed_fn(self._on_viewport_picked)
        if not self._viewport_id:
            self._set_viewport_id(self._viewport_ids[0])

    def _on_viewport_picked(self, model, _item):
        index = model.get_item_value_model().get_value_as_int()
        if 0 <= index < len(self._viewport_ids):
            self._set_viewport_id(self._viewport_ids[index])

    def _set_viewport_id(self, viewport_id: str):
        self._viewport_id = viewport_id
        if self._id_field is not None:
            self._id_field.model.set_value(viewport_id)
        if self._enabled_check is not None:
            self._enabled_check.model.set_value(MeasureService.is_enabled(viewport_id))

    def _refresh_viewports(self):
        if self._viewport_frame is not None:
            self._viewport_frame.rebuild()

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
                self._snap_checks[flag] = check
        ui.Label(
            "Surface is always on as the fallback.",
            height=18,
            style={"color": 0xFF999999},
        )

    def _build_actions(self):
        with ui.HStack(height=26, spacing=6):
            ui.Button("Pick one", clicked_fn=self._pick_one)
            ui.Button(
                "Cancel",
                clicked_fn=lambda: MeasureService.cancel_pick(self._viewport_id),
            )
        with ui.HStack(height=26, spacing=6):
            ui.Button(
                "Hide viewport",
                clicked_fn=lambda: MeasureService.set_visible(
                    False, viewport_id=self._viewport_id
                ),
            )
            ui.Button(
                "Show viewport",
                clicked_fn=lambda: MeasureService.set_visible(
                    True, viewport_id=self._viewport_id
                ),
            )
        with ui.HStack(height=26, spacing=6):
            ui.Button("Hide all", clicked_fn=lambda: MeasureService.set_visible(False))
            ui.Button("Show all", clicked_fn=lambda: MeasureService.set_visible(True))
        with ui.HStack(height=26, spacing=6):
            ui.Button(
                "Clear viewport",
                clicked_fn=lambda: MeasureService.clear(self._viewport_id),
            )
            ui.Button("Clear all", clicked_fn=lambda: MeasureService.clear())

    # --------------------------------------------------------------- actions

    def _toggle_snap(self, flag: SnapMode, on: bool):
        mode = MeasureService.get_snap_mode()
        MeasureService.set_snap_mode(mode | flag if on else mode & ~flag)

    def _pick_one(self):
        if not MeasureService.is_enabled(self._viewport_id):
            MeasureService.set_enabled(self._viewport_id, True)
            if self._enabled_check is not None:
                self._enabled_check.model.set_value(True)
        MeasureService.pick_one(self._viewport_id, on_done=self._on_line_done)

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
                    ui.Label(line.tab_id or "-", width=52)
                    ui.Label(line.viewport_id, width=60)
                    ui.Label(f"{line.length_m:.3f} m", width=80)
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
        self._snap_checks.clear()
        self._enabled_check = None
        self._id_field = None
        self._viewport_frame = None
        self._tab_id = ""
        self._tab_ids = ()
        self._tab_frame = None
        self._list_frame = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
