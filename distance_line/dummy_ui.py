from __future__ import annotations

import omni.ui as ui

from .distance_line_service import DistanceLineService, SnapMode

_SNAP_LEVELS = (
    ("Corner + Edge", SnapMode.ALL),
    ("Corner only", SnapMode.VERTEX),
    ("Edge only", SnapMode.EDGE),
    ("Surface only", SnapMode.NONE),
)

_MUTED = {"color": 0xFF999999}


def _toggle_wireframe():
    try:
        import omni.kit.actions.core

        omni.kit.actions.core.execute_action(
            "omni.kit.viewport.actions", "toggle_wireframe"
        )
    except Exception as exc:
        print(f"[distance_line] toggle_wireframe failed: {exc}")


class DistanceLineDummyUI:
    def __init__(self):
        self._window = None
        self._sub = None
        self._status_frame = None
        self._list_frame = None

    def build_ui(self):
        self._window = ui.Window("Distance Line (dummy)", width=320, height=470)
        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                self._status_frame = ui.Frame(build_fn=self._build_status, height=0)
                ui.Separator()
                self._build_snap_row()
                ui.Separator()
                self._build_actions()
                ui.Separator()
                ui.Label("Lines", height=20)
                with ui.ScrollingFrame(height=200):
                    self._list_frame = ui.Frame(build_fn=self._build_list)

        self._sub = DistanceLineService.subscribe_changed(self._on_changed)

    def _build_status(self):
        state = DistanceLineService.status()
        tabs = state["tabs"]
        active = state["active_tab"]
        selected = state["selected_viewport"]
        with ui.VStack(spacing=2, height=0):
            self._status_line("Active tab", active or "-")
            self._status_line(
                "Selected", selected or "- (first click decides)"
            )
            if not tabs:
                ui.Label(
                    "no tab registered - waiting for on_tab_created()",
                    height=18,
                    style=_MUTED,
                )
                return
            for tab_id, viewports in tabs.items():
                mark = " (active)" if tab_id == active else ""
                self._status_line("tab", f"{tab_id}{mark}")
                for viewport_id in viewports:
                    marks = " *" if viewport_id == selected else ""
                    if state["maximized"].get(tab_id) == viewport_id:
                        marks += " (max)"
                    self._status_line("", f"    {viewport_id}{marks}")
            if state["picking"]:
                ui.Label("picking...", height=18, style=_MUTED)

    def _status_line(self, label: str, value: str):
        with ui.HStack(height=18, spacing=6):
            ui.Label(label, width=72, style=_MUTED)
            ui.Label(value)

    def _build_snap_row(self):
        mode = DistanceLineService.status()["snap_mode"]
        index = next(
            (i for i, (_, m) in enumerate(_SNAP_LEVELS) if m == mode),
            0,
        )
        with ui.HStack(height=24, spacing=6):
            ui.Label("Snap", width=80)
            combo = ui.ComboBox(index, *[label for label, _ in _SNAP_LEVELS])
            combo.model.add_item_changed_fn(self._on_snap_picked)
        ui.Label(
            "Mesh outline only. Surface is always the fallback.",
            height=18,
            style=_MUTED,
        )
        with ui.HStack(height=24, spacing=6):
            ui.Label("Point cloud", width=80)
            box = ui.CheckBox(width=20)
            box.model.set_value(DistanceLineService.status()["cloud_snap"])
            box.model.add_value_changed_fn(
                lambda m: DistanceLineService.set_cloud_snap(m.get_value_as_bool())
            )
            ui.Label("snap to points", style=_MUTED)

    def _build_actions(self):
        with ui.HStack(height=26, spacing=6):
            ui.Button("Pick one", clicked_fn=self._pick_one)
            ui.Button("Cancel", clicked_fn=lambda: DistanceLineService.cancel_pick())
        with ui.HStack(height=26, spacing=6):
            ui.Button("Show all", clicked_fn=lambda: DistanceLineService.set_visible(True))
            ui.Button("Hide all", clicked_fn=lambda: DistanceLineService.set_visible(False))
            ui.Button("Clear all", clicked_fn=lambda: DistanceLineService.clear())
        with ui.HStack(height=26, spacing=6):
            ui.Button("Wireframe / Default", clicked_fn=_toggle_wireframe)
        ui.Label(
            "Wireframe shows the edges snapping aims at.",
            height=18,
            style=_MUTED,
        )

    def _on_snap_picked(self, model, _item):
        index = model.get_item_value_model().get_value_as_int()
        if 0 <= index < len(_SNAP_LEVELS):
            DistanceLineService.set_snap_mode(_SNAP_LEVELS[index][1])

    def _pick_one(self):
        DistanceLineService.pick_one()

    def _on_changed(self):
        if self._status_frame is not None:
            self._status_frame.rebuild()
        if self._list_frame is not None:
            self._list_frame.rebuild()

    def _build_list(self):
        lines = DistanceLineService.get_lines()
        with ui.VStack(spacing=2, height=0):
            if not lines:
                ui.Label("(none)", height=20, style={"color": 0xFF999999})
                return
            for line in lines:
                with ui.HStack(height=22, spacing=4):
                    ui.Label(f"#{line.number}", width=32)
                    ui.Label(f"{line.length_m:.3f} m", width=90)
                    ui.Button(
                        "hide" if line.visible else "show",
                        width=44,
                        clicked_fn=lambda i=line.id, v=line.visible: (
                            DistanceLineService.set_visible(not v, line_id=i)
                        ),
                    )
                    ui.Button(
                        "x",
                        width=24,
                        clicked_fn=lambda i=line.id: DistanceLineService.remove(i),
                    )

    def destroy(self):
        self._sub = None
        self._status_frame = None
        self._list_frame = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
