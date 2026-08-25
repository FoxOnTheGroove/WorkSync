import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService, FACES, GRID

__all__ = ["EbsDummyUI"]

COLOR_HIT      = 0xFF3333DD      # collision (ABGR: red)
COLOR_CLEAR    = 0xFF4C4C4C      # clear
COLOR_DISABLED = 0xFF2A2A2A      # not evaluated
CELL_SIZE      = 26
CELL_GAP       = 2

LOG_STYLE = {
    "background_color": 0xFF1E1E1E,
    "border_color":     0xFF555555,
    "border_width":     1,
    "border_radius":    4,
    "padding":          4,
}

FACE_LABEL = {"left": "Left", "ceiling": "Ceiling", "right": "Right"}


class EbsDummyUI:
    """Dummy UI driven only by the public API (EbsSimulateService)."""

    def __init__(self):
        self._window = None
        self._xml_field = None
        self._ebs2_field = None
        self._ebs3_field = None
        self._clearance_field = None
        self._eqp_field = None
        self._status_label = None
        self._info_label = None
        self._log_stack = None
        self._cells = {}          # face -> list[ui.Rectangle]

    # -- build ---------------------------------------------------------------

    def build_ui(self):
        self._window = ui.Window("EBS Simulate", width=470, height=630)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                self._xml_field  = self._path_row("Port XML:")
                self._ebs2_field = self._path_row("EBS 2port:")
                self._ebs3_field = self._path_row("EBS 3port:")

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Clearance:", width=90)
                    self._clearance_field = ui.FloatField(width=70)
                    self._clearance_field.model.set_value(1.0)
                    ui.Spacer()

                ui.Separator(height=6)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Equipment:", width=90)
                    self._eqp_field = ui.StringField()
                    ui.Button("From Sel", width=64, clicked_fn=self._on_pick_selected)

                with ui.HStack(height=28, spacing=4):
                    ui.Button("SIM", clicked_fn=self._on_simulate)
                    ui.Button("Rebuild Index", width=110, clicked_fn=self._on_rebuild_index)

                with ui.HStack(height=26, spacing=4):
                    ui.Button("1 Prepare", clicked_fn=self._on_prepare)
                    ui.Button("2 Camera", clicked_fn=self._on_camera)
                    ui.Button("3 Align", clicked_fn=self._on_align)
                    ui.Button("4 Collide", clicked_fn=self._on_collide)

                self._status_label = ui.Label("Ready", height=20)
                self._info_label = ui.Label("", height=20,
                                            style={"color": 0xFF999999, "font_size": 12})

                ui.Separator(height=6)
                self._build_grids()

                ui.Label("Timing", height=18, style={"font_size": 12, "color": 0xFFAAAAAA})
                with ui.ScrollingFrame(height=ui.Fraction(1), style=LOG_STYLE):
                    self._log_stack = ui.VStack(spacing=1)

    def _path_row(self, label: str):
        with ui.HStack(height=24, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
        return field

    def _build_grids(self):
        self._cells = {}
        with ui.HStack(height=GRID * (CELL_SIZE + CELL_GAP) + 24, spacing=16):
            for face in FACES:
                with ui.VStack(width=GRID * (CELL_SIZE + CELL_GAP), spacing=CELL_GAP):
                    ui.Label(FACE_LABEL.get(face, face), height=18,
                             alignment=ui.Alignment.CENTER,
                             style={"font_size": 13, "color": 0xFFCCCCCC})
                    rects = []
                    for _ in range(GRID):
                        with ui.HStack(height=CELL_SIZE, spacing=CELL_GAP):
                            for _ in range(GRID):
                                rects.append(ui.Rectangle(
                                    width=CELL_SIZE,
                                    style={"background_color": COLOR_DISABLED,
                                           "border_radius": 2},
                                ))
                    self._cells[face] = rects

    # -- handlers ------------------------------------------------------------

    def _on_pick_selected(self):
        path = EbsSimulateService.get_selected_equipment()
        if not path:
            self._set_status("No equipment found in selection")
            return
        self._eqp_field.model.set_value(path)
        self._set_status(f"Selected: {path}")

    def _on_rebuild_index(self):
        self._apply_settings()
        count = EbsSimulateService.build_index()
        ports = EbsSimulateService.load_ports()
        self._set_status(f"Equipment: {count}  |  XML port entries: {ports}")
        self._log_timings(EbsSimulateService.get_timings())

    def _on_simulate(self):
        self._apply_settings()
        self._render(EbsSimulateService.simulate(
            self._eqp_field.model.get_value_as_string()))

    def _on_prepare(self):
        self._apply_settings()
        self._render(EbsSimulateService.prepare(
            self._eqp_field.model.get_value_as_string()))

    def _on_camera(self):
        self._render(EbsSimulateService.focus())

    def _on_align(self):
        self._render(EbsSimulateService.align())

    def _on_collide(self):
        self._apply_settings()
        self._render(EbsSimulateService.collide())

    def _apply_settings(self):
        EbsSimulateService.set_xml_path(self._xml_field.model.get_value_as_string())
        EbsSimulateService.set_ebs_paths(
            self._ebs2_field.model.get_value_as_string(),
            self._ebs3_field.model.get_value_as_string(),
        )
        EbsSimulateService.set_clearance(self._clearance_field.model.get_value_as_float())

    # -- display -------------------------------------------------------------

    def _render(self, result: dict):
        if not result:
            self._set_status("No result")
            return

        ok = result.get("ok", False)
        cells = result.get("cells")
        # Steps before the collision check carry no cells: grey the grids out so a
        # stale result is never read as current.
        for face, rects in self._cells.items():
            values = (cells or {}).get(face, [])
            for i, rect in enumerate(rects):
                if not ok or cells is None:
                    color = COLOR_DISABLED
                else:
                    color = COLOR_HIT if (i < len(values) and values[i]) else COLOR_CLEAR
                rect.style = {"background_color": color, "border_radius": 2}

        self._set_status(result.get("reason", ""))
        port = result.get("port_count")
        self._info_label.text = (
            f"{result.get('equipment_id', '')}  |  port {port if port is not None else '-'}"
            f"  |  {result.get('ebs', '') or '-'}"
        )
        self._log_timings(result.get("timings", []), result.get("total_ms"))

    def _log_timings(self, timings: list, total_ms: float = None):
        """Show how long each step took, slowest step highlighted."""
        if self._log_stack is None:
            return
        self._log_stack.clear()
        if not timings:
            with self._log_stack:
                ui.Label("(no timing recorded)", style={"color": 0xFF888888,
                                                        "font_size": 12})
            return

        slowest = max(t[1] for t in timings)
        with self._log_stack:
            for label, elapsed in timings:
                color = 0xFF6699FF if elapsed >= slowest else 0xFFBBBBBB
                with ui.HStack(height=16):
                    ui.Label(str(label), style={"font_size": 12, "color": color})
                    ui.Label(f"{elapsed:8.1f} ms", width=90,
                             alignment=ui.Alignment.RIGHT_CENTER,
                             style={"font_size": 12, "color": color})
            if total_ms is not None:
                ui.Separator(height=4)
                with ui.HStack(height=16):
                    ui.Label("total", style={"font_size": 12, "color": 0xFFFFFFFF})
                    ui.Label(f"{total_ms:8.1f} ms", width=90,
                             alignment=ui.Alignment.RIGHT_CENTER,
                             style={"font_size": 12, "color": 0xFFFFFFFF})

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = text

    # -- teardown ------------------------------------------------------------

    def destroy(self):
        self._cells = {}
        self._log_stack = None
        if self._window:
            self._window.destroy()
            self._window = None
