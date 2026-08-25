import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService, FACES, GRID

__all__ = ["EbsDummyUI"]

COLOR_HIT      = 0xFF3333DD      # collision (ABGR: red)
COLOR_CLEAR    = 0xFF4C4C4C      # clear
COLOR_DISABLED = 0xFF2A2A2A      # not evaluated
CELL_SIZE      = 26
CELL_GAP       = 2

FACE_LABEL = {"left": "Left", "ceiling": "Ceiling", "right": "Right"}


class EbsDummyUI:
    """공개 API(EbsSimulateService)만 사용하는 더미 UI."""

    def __init__(self):
        self._window = None
        self._xml_field = None
        self._ebs2_field = None
        self._ebs3_field = None
        self._clearance_field = None
        self._eqp_field = None
        self._status_label = None
        self._info_label = None
        self._cells = {}          # face -> list[ui.Rectangle]

    # ── 빌드 ─────────────────────────────────────────────────────────────────

    def build_ui(self):
        self._window = ui.Window("EBS Simulate", width=460, height=460)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                self._xml_field       = self._path_row("Port XML:", "")
                self._ebs2_field      = self._path_row("EBS 2port:", "")
                self._ebs3_field      = self._path_row("EBS 3port:", "")

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

                self._status_label = ui.Label("Ready", height=20)
                self._info_label = ui.Label("", height=20,
                                            style={"color": 0xFF999999, "font_size": 12})

                ui.Separator(height=6)
                self._build_grids()
                ui.Spacer()

    def _path_row(self, label: str, default: str):
        with ui.HStack(height=24, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
            field.model.set_value(default)
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

    # ── 핸들러 ───────────────────────────────────────────────────────────────

    def _on_pick_selected(self):
        path = EbsSimulateService.get_selected_equipment()
        if not path:
            self._set_status("No equipment found in selection")
            return
        self._eqp_field.model.set_value(path)
        self._set_status(f"Selected: {path}")

    def _on_rebuild_index(self):
        count = EbsSimulateService.build_index()
        ports = EbsSimulateService.load_ports()
        self._set_status(f"Equipment: {count}  |  XML port entries: {ports}")

    def _on_simulate(self):
        self._apply_settings()
        result = EbsSimulateService.simulate(
            self._eqp_field.model.get_value_as_string()
        )
        self._render(result)

    def _apply_settings(self):
        EbsSimulateService.set_xml_path(self._xml_field.model.get_value_as_string())
        EbsSimulateService.set_ebs_paths(
            self._ebs2_field.model.get_value_as_string(),
            self._ebs3_field.model.get_value_as_string(),
        )
        EbsSimulateService.set_clearance(self._clearance_field.model.get_value_as_float())

    # ── 표시 ─────────────────────────────────────────────────────────────────

    def _render(self, result: dict):
        if not result:
            self._set_status("No result")
            return

        ok = result.get("ok", False)
        cells = result.get("cells", {})
        for face, rects in self._cells.items():
            values = cells.get(face, [])
            for i, rect in enumerate(rects):
                if not ok:
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

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = text

    # ── 정리 ─────────────────────────────────────────────────────────────────

    def destroy(self):
        self._cells = {}
        if self._window:
            self._window.destroy()
            self._window = None
