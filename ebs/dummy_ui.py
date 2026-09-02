import csv

import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService, FACES
from .ebs_simulate_overlay import EbsSimulateOverlay

__all__ = ["EbsDummyUI", "SweepLog"]

COLOR_HIT      = 0xFF3333DD      # collision (ABGR: red)
COLOR_CLEAR    = 0xFF4C7C4C      # clear
COLOR_DISABLED = 0xFF2A2A2A      # not evaluated
CHECKER_SHADE  = 0.72            # every other cell is dimmed, marking the grid


def shade(colour: int, dark: bool) -> int:
    """The same ABGR colour, dimmed a little, for the checkerboard's other square."""
    if not dark:
        return colour
    out = colour & 0xFF000000
    for shift in (0, 8, 16):
        out |= int(((colour >> shift) & 0xFF) * CHECKER_SHADE) << shift
    return out


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


class SweepLog:
    """The sweep's rows as a spreadsheet.

    Nothing here touches USD or the simulation: a sweep hands back a row per
    equipment, and this decides what a reader is shown - which columns, in what
    order, and what each verdict is called in the note. That is a question
    about the sheet, so it lives with the rest of the presentation rather than
    in the implementation.
    """

    # A blank column between what the equipment says and what the ports say, so
    # the two halves read apart. offset_diff is coord_diff at 100000 and the
    # other dropped columns are not what anyone reads the table for.
    COLUMNS = ("equipment", "pivot_ok", "axis",
               "pivot_coord", "pivot_offset", "pivot_offset_puls",
               "",
               "port_coord", "port_offset", "port_offset_puls",
               "puls_per_unit", "coord_diff", "off_axis_diff",
               "rail", "note")

    # What each pivot_ok says, in the note column. The run log keeps the long
    # version; the sheet only needs to say which bucket a row fell into.
    NOTES = {
        "TRUE": "",
        "FALSE": "depth 미달",
        "no-xml": "xml에 없음",
        "xml-invalid": "xml 값 사용 불가",
        "origin": "피봇이 원점",
        "shared": "다른 장비와 좌표 겹침",
    }
    WAYS = {"axis": "수평", "across": "수직"}   # which way a pivot is out by

    @classmethod
    def write(cls, path: str, rows: list) -> str:
        """Write the table where `path` points, and say where it went.

        Comma separated and BOM'd, so Excel opens it on a double click with the
        equipment names intact. No path or no rows writes nothing.
        """
        path = (path or "").strip()
        if not path or not rows:
            return ""
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=cls.COLUMNS,
                                    extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                out = {k: cls._cell(row.get(k, "")) for k in cls.COLUMNS}
                out["note"] = cls.note(row)
                writer.writerow(out)
        return path

    @classmethod
    def note(cls, row: dict) -> str:
        """One row's note: why it could not be read, or which bucket it is in."""
        state = str(row.get("pivot_ok", ""))
        why = row.get("why", "")
        # A row that fell over, and an unreadable XML row, each say what was
        # actually wrong; the rest say which bucket they fell into.
        if why and state in ("error", "xml-invalid"):
            return why
        if state.startswith("port"):
            return f"포트 {state[4:]}개"
        if not state.startswith("invalid:"):
            return cls.NOTES.get(state, state)

        parts = state[len("invalid:"):].split("+")
        said = []
        # Being out along the rail and across it is the one thing said twice
        # over, so the two go in one phrase rather than two.
        ways = [cls.WAYS[way] for way in ("axis", "across") if way in parts]
        if ways:
            said.append(f"좌표 벗어남({' && '.join(ways)})")
        said += [cls.NOTES.get(part, part) for part in parts
                 if part not in cls.WAYS]
        return ", ".join(said)

    @staticmethod
    def _cell(value):
        """Numbers rounded enough to read, everything else as it stands."""
        return f"{value:.4f}" if isinstance(value, float) else value


class EbsDummyUI:
    """Dummy UI driven only by the public API (EbsSimulateService)."""

    def __init__(self):
        self._window = None
        self._xml_field = None
        self._ebs2_field = None
        self._ebs3_field = None
        self._precision = None
        self._scale = None
        self._root_field = None
        self._rail_field = None
        self._report_field = None
        self._eqp_field = None
        self._status_label = None
        self._info_label = None
        self._log_stack = None
        self._grid_row = None
        self._cells = {}          # face -> list[ui.Rectangle]

    # -- build ---------------------------------------------------------------

    def build_ui(self):
        self._window = ui.Window("EBS Simulate", width=470, height=720)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                self._xml_field  = self._path_row("Port XML:")
                self._ebs2_field = self._path_row("EBS 2port:")
                self._ebs3_field = self._path_row("EBS 3port:")
                self._root_field = self._path_row("Search root:")
                self._rail_field = self._path_row("Rail root:")
                self._report_field = self._path_row("Report CSV:")

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Precision:", width=90)
                    # 'bbox' and 'mesh' are the same test, so only one is offered.
                    self._precision = ui.ComboBox(1, "box", "triangle", width=90)
                    ui.Label("Offset:", width=48)
                    # How an offset becomes a distance: one scale everywhere,
                    # each segment's length over its own distance-puls, or that
                    # again with port 1 slid onto the equipment's pivot.
                    self._scale = ui.ComboBox(0, "puls + snap", "fixed 100000",
                                              "length / puls", width=126)
                    ui.Label("Laser:", width=40)
                    # The port lasers Align used to draw every time. They are
                    # for checking the port maths against the drawing, so they
                    # are off unless you ask.
                    self._lasers = ui.CheckBox(width=20)
                    self._lasers.model.set_value(False)
                    ui.Spacer()

                ui.Separator(height=6)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Equipment:", width=90)
                    self._eqp_field = ui.StringField()
                    ui.Button("From Sel", width=64, clicked_fn=self._on_pick_selected)

                with ui.HStack(height=28, spacing=4):
                    ui.Button("INIT", width=70, clicked_fn=self._on_init)
                    ui.Button("SIM", clicked_fn=self._on_simulate)

                with ui.HStack(height=26, spacing=4):
                    ui.Button("1 Prepare", clicked_fn=self._on_prepare)
                    ui.Button("2 Align", clicked_fn=self._on_align)
                    ui.Button("3 Camera", clicked_fn=self._on_camera)
                    ui.Button("4 Collide", clicked_fn=self._on_collide)
                    ui.Button("Clear", width=54, clicked_fn=self._on_clear_markers)

                with ui.HStack(height=26, spacing=4):
                    # Every equipment under the search root, in one go: red
                    # where port 1 lands, green where the equipment sits.
                    ui.Button("Port 1 sweep", clicked_fn=self._on_sweep)

                self._status_label = ui.Label("Ready", height=20)
                self._info_label = ui.Label("", height=20,
                                            style={"color": 0xFF999999, "font_size": 12})

                ui.Separator(height=6)
                self._grid_row = ui.VStack(height=0)
                self._build_grids()

                ui.Label("Log", height=18, style={"font_size": 12, "color": 0xFFAAAAAA})
                with ui.ScrollingFrame(height=ui.Fraction(1), style=LOG_STYLE):
                    self._log_stack = ui.VStack(spacing=1)

    def _path_row(self, label: str):
        with ui.HStack(height=24, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
        return field

    def _build_grids(self, shape: dict = None, cells: dict = None,
                     distances: dict = None):
        """Draw one grid per face, sized as the run reported it.

        Cell counts differ per face and per EBS, so the squares are rebuilt each
        time rather than laid out once. Neighbouring cells alternate between the
        plain colour and a shaded one, which is what makes the grid readable
        without drawing lines.
        """
        if self._grid_row is None:
            return
        self._grid_row.clear()
        self._cells = {}
        with self._grid_row:
            with ui.HStack(spacing=16):
                for face in FACES:
                    rows, cols = (shape or {}).get(face, (1, 1))
                    values = (cells or {}).get(face) or []
                    found = (distances or {}).get(face)
                    with ui.VStack(width=cols * (CELL_SIZE + CELL_GAP),
                                   spacing=CELL_GAP):
                        ui.Label(f"{FACE_LABEL.get(face, face)} {rows}x{cols}",
                                 height=18, alignment=ui.Alignment.CENTER,
                                 style={"font_size": 13, "color": 0xFFCCCCCC})
                        ui.Label(self._face_note(found, any(values), cells),
                                 height=16, alignment=ui.Alignment.CENTER,
                                 word_wrap=True,
                                 style={"font_size": 11, "color": 0xFF9FD0A0})
                        rects = []
                        for r in range(rows):
                            with ui.HStack(height=CELL_SIZE, spacing=CELL_GAP):
                                for c in range(cols):
                                    i = r * cols + c
                                    blocked = bool(i < len(values) and values[i])
                                    if cells is None:
                                        colour = COLOR_DISABLED
                                    else:
                                        colour = shade(
                                            COLOR_HIT if blocked else COLOR_CLEAR,
                                            (r + c) % 2 == 1)
                                    rects.append(ui.Rectangle(
                                        width=CELL_SIZE,
                                        style={"background_color": colour,
                                               "border_radius": 2}))
                        self._cells[face] = rects

    @staticmethod
    def _face_note(found: dict, blocked: bool, cells) -> str:
        """One line under a grid: how far the nearest mesh is, or that it is blocked."""
        if cells is None:
            return " "
        if blocked:
            return "blocked"
        if not found:
            return "-"
        if found.get("distance") is None:
            return f"clear > {found.get('reach', 0):.2f}"
        return f"{found['distance']:.3f}  {found.get('prim', '').rsplit('/', 1)[-1]}"

    # -- handlers ------------------------------------------------------------

    def _on_pick_selected(self):
        path = EbsSimulateService.get_selected_equipment()
        if not path:
            self._set_status("No equipment found in selection")
            return
        self._eqp_field.model.set_value(path)
        self._set_status(f"Selected: {path}")

    def _on_init(self):
        self._apply_settings()
        self._render(EbsSimulateService.init())

    def _on_simulate(self):
        self._apply_settings()
        self._render(EbsSimulateService.simulate(
            self._eqp_field.model.get_value_as_string()))
        EbsSimulateOverlay.show()      # SIM runs the collide too

    def _on_prepare(self):
        self._apply_settings()
        self._render(EbsSimulateService.prepare(
            self._eqp_field.model.get_value_as_string()))
        # A verdict is about where the EBS was standing. Choosing another
        # machine, or moving it, leaves the panel saying so about nothing.
        EbsSimulateOverlay.hide()

    def _on_align(self):
        self._apply_settings()
        self._render(EbsSimulateService.align())
        EbsSimulateOverlay.hide()

    def _on_camera(self):
        self._render(EbsSimulateService.focus())

    def _on_sweep(self):
        self._apply_settings()
        result = EbsSimulateService.sweep_ports()
        self._render(result)
        self._write_report(result.get("rows", []))

    def _write_report(self, rows: list):
        """Put the sweep's rows on disk, and say so under the status line."""
        path = self._report_field.model.get_value_as_string()
        try:
            written = SweepLog.write(path, rows)
        except Exception as e:
            self._set_status(f"Could not write {path}: {e}")
            return
        if written:
            self._set_status(f"{len(rows)} rows written to {written}")

    def _on_clear_markers(self):
        EbsSimulateService.clear_markers()
        EbsSimulateService.clear_port_lasers()
        EbsSimulateService.clear_sweep()
        EbsSimulateService.release_camera()
        EbsSimulateService.hide_ebs()
        EbsSimulateOverlay.hide()
        self._set_status("Markers and lasers cleared, camera released, EBS hidden")

    def _on_collide(self):
        self._apply_settings()
        self._render(EbsSimulateService.collide())
        EbsSimulateOverlay.show()      # the verdict over the EBS

    def _apply_settings(self):
        EbsSimulateService.set_xml_path(self._xml_field.model.get_value_as_string())
        EbsSimulateService.set_ebs_paths(
            self._ebs2_field.model.get_value_as_string(),
            self._ebs3_field.model.get_value_as_string(),
        )
        EbsSimulateService.set_search_root(self._root_field.model.get_value_as_string())
        EbsSimulateService.set_rail_root(self._rail_field.model.get_value_as_string())
        modes = ("mesh", "triangle")
        index = self._precision.model.get_item_value_model().get_value_as_int()
        EbsSimulateService.set_precision(modes[max(0, min(index, 1))])
        scales = ("snap", "fixed", "puls")
        index = self._scale.model.get_item_value_model().get_value_as_int()
        EbsSimulateService.set_offset_scale(scales[max(0, min(index, 2))])
        EbsSimulateService.set_show_lasers(self._lasers.model.get_value_as_bool())

    # -- display -------------------------------------------------------------

    def _render(self, result: dict):
        if not result:
            self._set_status("No result")
            return

        ok = result.get("ok", False)
        cells = result.get("cells")
        # Steps before the collision check carry no cells: grey the grids out so a
        # stale result is never read as current.
        self._build_grids(result.get("grid") or EbsSimulateService.get_grid_shape(),
                          cells if ok else None,
                          result.get("distances") if ok else None)

        self._set_status(result.get("reason", ""))
        port = result.get("port_count")
        # The equipment is not part of the face grids, so its own verdict says
        # itself or the line reads as if nothing was checked against it.
        meeting = result.get("equipment_hit") or {}
        through = ("  |  THROUGH THE EQUIPMENT" if meeting.get("hit")
                   else "  |  clear of the equipment" if meeting.get("tests")
                   else "")
        self._info_label.text = (
            f"{result.get('equipment_id', '')}  |  port {port if port is not None else '-'}"
            f"  |  {result.get('ebs', '') or '-'}{through}"
        )
        self._log_timings(result.get("timings", []), result.get("total_ms"),
                          result.get("notes", []))

    def _log_timings(self, timings: list, total_ms: float = None, notes: list = None):
        """Show what the run did: diagnostics first, then per-step timings."""
        if self._log_stack is None:
            return
        self._log_stack.clear()
        if not timings and not notes:
            with self._log_stack:
                ui.Label("(nothing recorded)", style={"color": 0xFF888888,
                                                      "font_size": 12})
            return

        with self._log_stack:
            for note in (notes or []):
                ui.Label(str(note), height=16,
                         style={"font_size": 12, "color": 0xFFCFCFA0})
            if notes and timings:
                ui.Separator(height=4)

        if not timings:
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
        EbsSimulateOverlay.destroy()   # it holds a scene view on the viewport
        self._cells = {}
        self._grid_row = None
        self._log_stack = None
        if self._window:
            self._window.destroy()
            self._window = None
