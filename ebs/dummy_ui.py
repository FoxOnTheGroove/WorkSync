import asyncio
import csv
import pprint
import time

import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService
from .ebs_simulate_overlay import EbsSimulateOverlay

__all__ = ["EbsDummyUI", "SweepLog"]

MIN_SIDE    = 0.6
MIN_CEILING = 0.1

DOCK_NEXT = ("Property", "Stage", "Layer", "Content")

PRESET = {"usd": "", "xml": "", "ebs2": "", "ebs3": "", "root": "", "rail": ""}

VIEW_PATHS = (("Ceiling:", ""),
              ("Floor:", ""),
              ("Structure:", ""),
              ("Other 1:", "/World/Group_01/Foups"),
              ("Other 2:", ""),
              ("Other 3:", ""))


class SweepLog:
    """The sweep's rows as a spreadsheet."""

    COLUMNS = ("equipment", "pivot_ok", "axis",
               "pivot_coord", "pivot_offset", "pivot_offset_puls",
               "",
               "port_coord", "port_offset", "port_offset_puls",
               "puls_per_unit", "coord_diff", "off_axis_diff",
               "rail", "note")

    NOTES = {
        "TRUE": "",
        "FALSE": "depth 미달",
        "no-xml": "xml에 없음",
        "xml-invalid": "xml 값 사용 불가",
        "origin": "피봇이 원점",
        "shared": "다른 장비와 좌표 겹침",
    }
    WAYS = {"axis": "수평", "across": "수직"}

    @classmethod
    def write(cls, path: str, rows: list) -> str:
        """Write the table where `path` points, and say where it went."""
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
        if why and state in ("error", "xml-invalid"):
            return why
        if state.startswith("port"):
            return f"포트 {state[4:]}개"
        if not state.startswith("invalid:"):
            return cls.NOTES.get(state, state)

        parts = state[len("invalid:"):].split("+")
        said = []
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
        """위젯 참조 자리만. 구성은 build_ui"""
        self._window = None
        self._usd_field = None
        self._xml_field = None
        self._ebs2_field = None
        self._ebs3_field = None
        self._root_field = None
        self._rail_field = None
        self._views = {}
        self._docked = False
        self._eqp_field = None
        self._side_field = None
        self._ceiling_field = None
        self._status_label = None
        self._overlay_button = None
        self._overlay_on = False
        self._task = None


    def build_ui(self):
        """창 하나에 경로 입력, 설정, 버튼 줄, 상태 줄을 쌓는다"""
        self._window = ui.Window("EBS Simulate", width=520, height=470,
                                 dockPreference=ui.DockPreference.RIGHT_BOTTOM)
        with self._window.frame:
            with ui.VStack(spacing=5, style={"margin": 3}):
                with ui.VStack(spacing=1, height=0):
                    self._usd_field  = self._path_row("Stage USD:", PRESET["usd"])
                    self._xml_field  = self._path_row("Port XML:", PRESET["xml"])
                    self._ebs2_field = self._path_row("EBS 2port:", PRESET["ebs2"])
                    self._ebs3_field = self._path_row("EBS 3port:", PRESET["ebs3"])
                    self._root_field = self._path_row("Search root:", PRESET["root"])
                    self._rail_field = self._view_row("Rail root:", PRESET["rail"])
                    for label, value in VIEW_PATHS:
                        self._view_row(label, value)

                with ui.HStack(height=22, spacing=4):
                    # 잠시 접어 둔 것. 서비스는 triangle + snap 으로 돈다
                    # ui.Label("Precision:", width=90)
                    # self._precision = ui.ComboBox(1, "box", "triangle", width=90)
                    # ui.Label("Offset:", width=48)
                    # self._scale = ui.ComboBox(0, "puls + snap", "fixed 100000",
                    #                           "length / puls", width=126)
                    ui.Label("Debug laser:", width=76)
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
                    ui.Button("Get", width=56, clicked_fn=self._on_get_result)

                with ui.HStack(height=26, spacing=4):
                    ui.Button("1 Align", clicked_fn=self._on_align)
                    ui.Button("2 Collide", clicked_fn=self._on_collide)
                    ui.Button("3 Camera", clicked_fn=self._on_camera)
                    ui.Button("Refresh", width=60, clicked_fn=self._on_refresh)
                    ui.Button("Clear", width=54, clicked_fn=self._on_clear_markers)
                    self._overlay_button = ui.Button(
                        "Col UI", width=64, clicked_fn=self._on_toggle_overlay)

                self._status_label = ui.Label("Ready", height=20)

    def _path_row(self, label: str, value: str = ""):
        """라벨 + 입력칸 한 줄"""
        with ui.HStack(height=20, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
            if value:
                field.model.set_value(value)
        return field

    def auto_init(self) -> dict:
        """버튼 없이 도는 init. 입력칸의 사전값을 그대로 쓴다"""
        self._apply_settings()
        result = EbsSimulateService.init()
        self._render(result)
        return result

    def _view_row(self, label: str, value: str = ""):
        """경로 한 줄 뒤에 보임 체크박스를 붙인다"""
        with ui.HStack(height=20, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
            if value:
                field.model.set_value(value)
            box = ui.CheckBox(width=20)
            box.model.set_value(True)
            box.model.add_value_changed_fn(
                lambda model, f=field: self._on_view_changed(f, model))
        self._views[id(field)] = box
        return field

    def _on_view_changed(self, field, model):
        """체크가 바뀐 그 자리에서 그 경로를 켜거나 끈다"""
        path = field.model.get_value_as_string().strip()
        if not path:
            self._set_status("Path is empty")
            return
        on = model.get_value_as_bool()
        touched = EbsSimulateService.set_visible(path, on)
        self._set_status(f"{path}: {'visible' if on else 'hidden'} ({touched})")

    def dock_right(self) -> bool:
        """우측 패널에 붙인다. 붙을 창이 아직 없으면 False"""
        if self._docked:
            return True
        for title in DOCK_NEXT:
            other = ui.Workspace.get_window(title)
            if other is None or self._window is None:
                continue
            self._window.dock_in(other, ui.DockPosition.SAME)
            self._window.focus()
            self._docked = True
            return True
        return False

    def _on_pick_selected(self):
        """뷰포트 선택에서 장비 이름을 가져와 입력칸에 넣는다"""
        path = EbsSimulateService.get_selected_equipment()
        if not path:
            self._set_status("No equipment found in selection")
            return
        name = str(path).rstrip("/").rsplit("/", 1)[-1]
        self._eqp_field.model.set_value(name)
        self._set_status(f"Selected: {name}")

    def _on_init(self):
        """설정을 넘기고 init 한다. 보임은 건드리지 않는다"""
        self.auto_init()

    def _on_simulate(self):
        """align + collide + camera. collide 가 길어 프레임에 나눠 돈다"""
        self._apply_settings()
        self._start(self._simulate_task())

    async def _simulate_task(self):
        """도는 동안 진행률을 적고, 끝나면 오버레이를 켠다"""
        self._render(await self._watched(EbsSimulateService.simulate_async(
            self._eqp_field.model.get_value_as_string())))
        EbsSimulateOverlay.show()
        self._overlay_on = True
        self._mark_overlay()

    def _on_get_result(self):
        """입력칸의 장비 이름으로 적어 둔 판정을 꺼내 콘솔에 찍는다"""
        name = self._eqp_field.model.get_value_as_string().strip()
        found = EbsSimulateService.get_result(name)
        print(f"[ebs] get_result({name!r}):")
        print(pprint.pformat(found, width=100, sort_dicts=False))
        if found["equipment"]:
            self._set_status(f"{found['equipment']}: {found['reason']}")
            return
        known = EbsSimulateService.list_results()
        self._set_status(f"No result for {name!r}. Have: "
                         + (", ".join(known) if known else "nothing yet"))

    def _on_align(self):
        """1단계. EBS 를 놓는다. 오버레이는 끈다"""
        self._apply_settings()
        self._render(EbsSimulateService.align(
            self._eqp_field.model.get_value_as_string()))
        EbsSimulateOverlay.hide()
        self._overlay_on = False
        self._mark_overlay()

    def _on_camera(self):
        """3단계. 카메라를 잡고 오버레이를 화면에 앉힌다"""
        self._render(EbsSimulateService.focus())
        EbsSimulateOverlay.reveal()
        self._overlay_on = True
        self._mark_overlay()

    def _on_toggle_overlay(self):
        """판정 오버레이를 손으로 켜고 끈다"""
        self._overlay_on = not self._overlay_on
        if self._overlay_on:
            EbsSimulateOverlay.show()
        else:
            EbsSimulateOverlay.hide()
        self._mark_overlay()
        self._set_status("Overlay on" if self._overlay_on else "Overlay off")

    def _mark_overlay(self):
        """Col UI 버튼 글자를 켜짐/꺼짐에 맞춘다"""
        if self._overlay_button:
            self._overlay_button.text = "Col UI ON" if self._overlay_on else "Col UI"

    def _on_refresh(self):
        """카메라만 원래 자리로 되돌린다"""
        self._render(EbsSimulateService.refresh_camera())

    def _on_clear_markers(self):
        """그린 것, 레이저, 카메라, EBS, 오버레이를 전부 놓는다"""
        EbsSimulateService.clear_markers()
        EbsSimulateService.clear_port_lasers()
        EbsSimulateService.clear_sweep()
        EbsSimulateService.release_camera()
        EbsSimulateService.hide_ebs()
        EbsSimulateOverlay.hide()
        self._overlay_on = False
        self._mark_overlay()
        self._set_status("Markers and lasers cleared, camera released, EBS hidden")

    def _on_collide(self):
        """2단계. 충돌을 재고 오버레이는 그리기만 해 둔다"""
        self._apply_settings()
        self._start(self._collide_task())

    async def _collide_task(self):
        """도는 동안 진행률을 적는다"""
        self._render(await self._watched(EbsSimulateService.collide_async()))
        EbsSimulateOverlay.build()

    def _start(self, work):
        """코루틴 하나를 띄운다. 이미 도는 것이 있으면 무시한다"""
        if self._task is not None and not self._task.done():
            self._set_status("Busy")
            return
        self._task = asyncio.ensure_future(work)

    async def _watched(self, work):
        """일이 도는 동안 진행률과 흐른 시간을 상태 줄에 적는다"""
        import omni.kit.app
        started = time.monotonic()
        task = asyncio.ensure_future(work)
        while not task.done():
            self._set_status(
                f"Working {EbsSimulateService.get_progress():6.2f}%"
                f"   {time.monotonic() - started:.1f}s")
            await omni.kit.app.get_app().next_update_async()
        return task.result()

    def _apply_settings(self):
        """입력칸과 콤보의 값을 서비스 설정으로 넘긴다"""
        EbsSimulateService.set_usd_path(self._usd_field.model.get_value_as_string())
        EbsSimulateService.set_xml_path(self._xml_field.model.get_value_as_string())
        EbsSimulateService.set_ebs_paths(
            self._ebs2_field.model.get_value_as_string(),
            self._ebs3_field.model.get_value_as_string(),
        )
        EbsSimulateService.set_search_root(self._root_field.model.get_value_as_string())
        EbsSimulateService.set_rail_root(self._rail_field.model.get_value_as_string())
        # 콤보를 접어 둔 동안은 서비스 기본값을 그대로 쓴다
        # modes = ("mesh", "triangle")
        # index = self._precision.model.get_item_value_model().get_value_as_int()
        # EbsSimulateService.set_precision(modes[max(0, min(index, 1))])
        # scales = ("snap", "fixed", "puls")
        # index = self._scale.model.get_item_value_model().get_value_as_int()
        # EbsSimulateService.set_offset_scale(scales[max(0, min(index, 2))])
        EbsSimulateService.set_show_lasers(self._lasers.model.get_value_as_bool())
        EbsSimulateService.set_min_gaps(self._number(self._side_field, MIN_SIDE),
                                        self._number(self._ceiling_field, MIN_CEILING))

    @staticmethod
    def _number(field, fallback: float) -> float:
        """입력칸을 숫자로. 비었거나 이상하면 기본값"""
        try:
            return float(field.model.get_value_as_string().strip())
        except (AttributeError, TypeError, ValueError):
            return fallback


    def _render(self, result: dict):
        """결과에서 reason 만 상태 줄로. 나머지는 콘솔이 받는다"""
        self._set_status((result or {}).get("reason", "") or "No result")

    def _set_status(self, text: str):
        """상태 줄. 아직 안 만들었으면 넘어간다"""
        if self._status_label:
            self._status_label.text = text


    def destroy(self):
        """오버레이와 창을 닫는다"""
        EbsSimulateOverlay.destroy()
        if self._window:
            self._window.destroy()
            self._window = None
