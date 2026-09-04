import csv

import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService
from .ebs_simulate_overlay import EbsSimulateOverlay

__all__ = ["EbsDummyUI", "SweepLog"]

MIN_SIDE    = 0.6      # 최소 여유 입력칸의 기본값, m. 실제 기본은 구현부
MIN_CEILING = 0.1      # (MIN_GAP_SIDE / MIN_GAP_CEILING) 이고 여기는 표시용


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
        self._usd_field = None
        self._xml_field = None
        self._ebs2_field = None
        self._ebs3_field = None
        self._precision = None
        self._scale = None
        self._root_field = None
        self._rail_field = None
        self._eqp_field = None
        self._side_field = None
        self._ceiling_field = None
        self._status_label = None

    # -- build ---------------------------------------------------------------

    def build_ui(self):
        self._window = ui.Window("EBS Simulate", width=470, height=330)
        with self._window.frame:
            with ui.VStack(spacing=5, style={"margin": 8}):
                # 경로 칸 여섯은 한 번 채우고 안 건드린다. 저희끼리 붙여 둔다
                with ui.VStack(spacing=1, height=0):
                    self._usd_field  = self._path_row("Stage USD:")
                    self._xml_field  = self._path_row("Port XML:")
                    self._ebs2_field = self._path_row("EBS 2port:")
                    self._ebs3_field = self._path_row("EBS 3port:")
                    self._root_field = self._path_row("Search root:")
                    self._rail_field = self._path_row("Rail root:")

                with ui.HStack(height=22, spacing=4):
                    ui.Label("Precision:", width=90)
                    # 'bbox' and 'mesh' are the same test, so only one is offered.
                    self._precision = ui.ComboBox(1, "box", "triangle", width=90)
                    ui.Label("Offset:", width=48)
                    # How an offset becomes a distance: one scale everywhere,
                    # each segment's length over its own distance-puls, or that
                    # again with port 1 slid onto the equipment's pivot.
                    self._scale = ui.ComboBox(0, "puls + snap", "fixed 100000",
                                              "length / puls", width=126)
                    ui.Label("Debug laser:", width=76)
                    # The port lasers Align used to draw every time. They are
                    # for checking the port maths against the drawing, so they
                    # are off unless you ask.
                    self._lasers = ui.CheckBox(width=20)
                    self._lasers.model.set_value(False)
                    ui.Spacer()

                ui.Separator(height=4)

                with ui.HStack(height=22, spacing=4):
                    ui.Label("Equipment:", width=90)
                    self._eqp_field = ui.StringField()
                    ui.Button("From Sel", width=64, clicked_fn=self._on_pick_selected)

                with ui.HStack(height=22, spacing=4):
                    ui.Label("Min gap m:", width=90)
                    ui.Label("side", width=30)
                    self._side_field = ui.StringField(width=64)
                    self._side_field.model.set_value(f"{MIN_SIDE:.3f}")
                    ui.Label("ceiling", width=48)
                    self._ceiling_field = ui.StringField(width=64)
                    self._ceiling_field.model.set_value(f"{MIN_CEILING:.3f}")
                    ui.Spacer()

                with ui.HStack(height=28, spacing=4):
                    ui.Button("INIT", width=70, clicked_fn=self._on_init)
                    ui.Button("SIM", clicked_fn=self._on_simulate)

                with ui.HStack(height=26, spacing=4):
                    ui.Button("1 Align", clicked_fn=self._on_align)
                    ui.Button("2 Collide", clicked_fn=self._on_collide)
                    ui.Button("3 Camera", clicked_fn=self._on_camera)
                    ui.Button("Refresh", width=60, clicked_fn=self._on_refresh)
                    ui.Button("Clear", width=54, clicked_fn=self._on_clear_markers)

                self._status_label = ui.Label("Ready", height=20)

    def _path_row(self, label: str):
        with ui.HStack(height=20, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
        return field

    # -- handlers ------------------------------------------------------------

    def _on_pick_selected(self):
        path = EbsSimulateService.get_selected_equipment()
        if not path:
            self._set_status("No equipment found in selection")
            return
        name = str(path).rstrip("/").rsplit("/", 1)[-1]
        self._eqp_field.model.set_value(name)
        self._set_status(f"Selected: {name}")

    def _on_init(self):
        self._apply_settings()
        self._render(EbsSimulateService.init())

    def _on_simulate(self):
        self._apply_settings()
        self._render(EbsSimulateService.simulate(
            self._eqp_field.model.get_value_as_string()))
        EbsSimulateOverlay.show()      # SIM runs the collide too

    def _on_align(self):
        self._apply_settings()
        self._render(EbsSimulateService.align(
            self._eqp_field.model.get_value_as_string()))
        # A verdict is about where the EBS was standing. Choosing another
        # machine, or moving it, leaves the panel saying so about nothing.
        EbsSimulateOverlay.hide()

    def _on_camera(self):
        self._render(EbsSimulateService.focus())
        # 시점이 옮겨간 뒤에 켠다. Collide 가 만들어 둔 것이 여기서 보인다.
        EbsSimulateOverlay.reveal()

    def _on_refresh(self):
        # 돌려본 카메라를 Camera 가 놓았던 자리로. 궤도 모드는 켜진 채다.
        self._render(EbsSimulateService.refresh_camera())

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
        # 만들어만 둔다. 옛 시점에 판이 떴다가 카메라를 따라 미끄러지는 것보다,
        # 시점이 자리잡은 뒤 한 번에 뜨는 편이 낫다 -- Camera 가 켠다.
        EbsSimulateOverlay.build()

    def _apply_settings(self):
        EbsSimulateService.set_usd_path(self._usd_field.model.get_value_as_string())
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
        EbsSimulateService.set_min_gaps(self._number(self._side_field, MIN_SIDE),
                                        self._number(self._ceiling_field, MIN_CEILING))

    @staticmethod
    def _number(field, fallback: float) -> float:
        try:
            return float(field.model.get_value_as_string().strip())
        except (AttributeError, TypeError, ValueError):
            return fallback

    # -- display -------------------------------------------------------------

    def _render(self, result: dict):
        """한 줄만 남긴다. 그 아래에 있던 그리드와 타이밍 목록은 뷰포트의
        패널이 같은 것을 더 잘 말한다 — 노트와 타이밍은 콘솔에 그대로 간다."""
        self._set_status((result or {}).get("reason", "") or "No result")

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = text

    # -- teardown ------------------------------------------------------------

    def destroy(self):
        EbsSimulateOverlay.destroy()   # it holds a scene view on the viewport
        if self._window:
            self._window.destroy()
            self._window = None
