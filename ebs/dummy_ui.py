import asyncio
import csv
import time

import omni.ui as ui

from .ebs_simulate_shared import (WORK_ALIGN, WORK_CAMERA, WORK_COLLIDE,
                                  WORK_REFRESH)
from .ebs_simulate_service import EbsSimulateService
from .ebs_simulate_overlay import EbsSimulateOverlay

__all__ = ["EbsDummyUI", "SweepLog"]

MIN_SIDE    = 0.4
MIN_CEILING = 0.05

DOCK_NEXT = ("Property", "Stage", "Layer", "Content")

PRESET = {"usd": "", "xml": "", "ebs2": "", "ebs3": "", "root": "", "rail": ""}

VIEW_PATHS = (("Ceiling:", "", False),
              ("Floor:", "", True),
              ("Structure:", "", True),
              ("Other 1:", "/World/Group_01/Foups", False),
              ("Other 2:", "", False),
              ("Other 3:", "", False))

EQP_PREFIX = "EQP_"
SKIN_URL   = ""

NEAR_SPAN = 2.5
NEAR_MIN, NEAR_MAX = 0.0, 5.0
NUDGE_LEFT  = "◀ 왼쪽"
NUDGE_RIGHT = "▶ 오른쪽"
NUDGE_HOME  = "제자리"
NUDGE_BUSY  = " · 갱신 중"


class SweepLog:
    """스윕이 낸 줄을 표로 떨군다. 검증용이라 단계와 무관하다"""

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
        """적어 둔 자리에 표를 쓰고 어디에 썼는지 돌려준다"""
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
        """한 줄에 붙일 비고. 왜 못 읽었는지, 아니면 어느 갈래인지"""
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
        """숫자는 읽을 만큼만 반올림하고 나머지는 그대로"""
        return f"{value:.4f}" if isinstance(value, float) else value


class EbsDummyUI:
    """서비스 API 만 보고 도는 시험용 창. 속을 직접 만지지 않는다"""

    def __init__(self, simulate):
        """위젯 참조 자리만. 구성은 build_ui

        simulate  디버그 버튼과 체크박스가 직접 잡는 속. 서비스 표면에는
                  사용자가 부르는 것만 남기려고 이쪽으로 뚫어 둔다
        """
        self._sim = simulate
        self._window = None
        self._usd_field = None
        self._xml_field = None
        self._ebs2_field = None
        self._ebs3_field = None
        self._root_field = None
        self._rail_field = None
        self._views = []
        self._docked = False
        self._eqp_field = None
        self._skin_field = None
        self._side_field = None
        self._ceiling_field = None
        self._status_label = None
        self._near_slider = None
        self._nudge_label = None
        self._nudge_for = ""
        self._task = None


    def build_ui(self):
        """창 하나에 경로 입력, 설정, 버튼 줄, 상태 줄을 쌓는다"""
        self._window = ui.Window("EBS Simulate", width=520, height=360,
                                 dockPreference=ui.DockPreference.RIGHT_BOTTOM)
        with self._window.frame:
            with ui.VStack(spacing=5, style={"margin": 3}):
                with ui.CollapsableFrame("Paths", collapsed=True, height=0):
                    with ui.VStack(spacing=1, height=0):
                        self._usd_field  = self._path_row("Stage USD:", PRESET["usd"])
                        self._xml_field  = self._path_row("Port XML:", PRESET["xml"])
                        self._ebs2_field = self._path_row("EBS 2port:", PRESET["ebs2"])
                        self._ebs3_field = self._path_row("EBS 3port:", PRESET["ebs3"])
                        self._root_field = self._path_row("Search root:", PRESET["root"])
                        self._rail_field = self._view_row("Rail root:", PRESET["rail"])
                        for label, value, on in VIEW_PATHS:
                            self._view_row(label, value, on)

                with ui.HStack(height=22, spacing=4):
                    # 잠시 접어 둔 것. 서비스는 triangle + snap 으로 돈다
                    # ui.Label("Precision:", width=90)
                    # self._precision = ui.ComboBox(1, "box", "triangle", width=90)
                    # ui.Label("Offset:", width=48)
                    # self._scale = ui.ComboBox(0, "puls + snap", "fixed 100000",
                    #                           "length / puls", width=126)
                    ui.Label("Collide:", width=90)
                    ui.Label("Debug laser:", width=76)
                    self._lasers = ui.CheckBox(width=20)
                    self._lasers.model.set_value(False)
                    ui.Spacer()

                ui.Separator(height=4)

                with ui.HStack(height=22, spacing=4):
                    ui.Label("Material:", width=90)
                    ui.Label("mdl / prim / rgb", width=104)
                    self._skin_field = ui.StringField()
                    self._skin_field.model.set_value(SKIN_URL)

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
                    ui.Button("SIM", clicked_fn=self._on_simulate)
                    ui.Button("Refresh", width=70, clicked_fn=self._on_refresh)
                    ui.Button("Clear", width=64, clicked_fn=self._on_clear_markers)

                with ui.HStack(height=26, spacing=4):
                    ui.Label("near", width=30)
                    self._near_slider = ui.FloatSlider(min=NEAR_MIN, max=NEAR_MAX,
                                                       step=0.1, width=110)
                    self._near_slider.model.set_value(NEAR_SPAN)
                    self._near_slider.model.add_value_changed_fn(
                        lambda model: self._on_near_span())
                    ui.Button("1 Camera", clicked_fn=self._on_camera)
                    ui.Button("2 Align", clicked_fn=self._on_align)
                    ui.Button("3 Collide", clicked_fn=self._on_collide)

                with ui.HStack(height=26, spacing=4):
                    self._nudge_label = ui.Label(NUDGE_HOME)

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
        result = EbsSimulateService.auto_init()
        self._apply_views()
        self._reset_nudge()
        self._render(result)
        return result

    def _view_row(self, label: str, value: str = "", on: bool = True):
        """경로 한 줄 뒤에 보임 체크박스를 붙인다"""
        with ui.HStack(height=20, spacing=4):
            ui.Label(label, width=90)
            field = ui.StringField()
            if value:
                field.model.set_value(value)
            box = ui.CheckBox(width=20)
            box.model.set_value(on)
            box.model.add_value_changed_fn(
                lambda model, f=field: self._on_view_changed(f, model))
        self._views.append((field, box))
        return field

    def _apply_views(self) -> int:
        """줄마다 적힌 경로를 지금 체크 상태대로 맞춘다. init 이 한 번 부른다"""
        done = 0
        for field, box in self._views:
            path = field.model.get_value_as_string().strip()
            if path:
                done += self._sim.set_visible(
                    path, box.model.get_value_as_bool())
        return done

    def _on_view_changed(self, field, model):
        """체크가 바뀐 그 자리에서 그 경로를 켜거나 끈다"""
        path = field.model.get_value_as_string().strip()
        if not path:
            self._set_status("Path is empty")
            return
        on = model.get_value_as_bool()
        touched = self._sim.set_visible(path, on)
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
        path = self._sim.get_selected_equipment()
        if not path:
            self._set_status("No equipment found in selection")
            return
        name = str(path).rstrip("/").rsplit("/", 1)[-1]
        if name.upper().startswith(EQP_PREFIX):
            name = name[len(EQP_PREFIX):]
        self._eqp_field.model.set_value(name)
        self._reset_nudge()
        self._set_status(f"Selected: {name}")

    def _on_simulate(self):
        """사용자 동작. 서비스 API 한 줄이 절차를 다 들고 있다"""
        self._apply_settings()
        self._spawn(EbsSimulateService.simulate(
            self._eqp_field.model.get_value_as_string()))

    def _overlay(self, work):
        """오버레이 세우는 시간을 재서 같은 줄에 얹는다"""
        started = time.perf_counter()
        try:
            work()
        finally:
            self._sim.add_phase("overlay", time.perf_counter() - started)

    def _on_align(self):
        """2단계. EBS 를 제자리에 놓아 보인다. 밀어 둔 것이 있으면 되돌린다"""
        self._apply_settings()
        self._reset_nudge()
        self._start(self._aligning, WORK_ALIGN)

    async def _aligning(self):
        """align 을 돌리고 화면이 잦아들 때까지 기다린 뒤 한 줄 찍는다"""
        self._render(await self._sim.align_async(
            self._eqp_field.model.get_value_as_string()))
        self._overlay(EbsSimulateOverlay.hide)
        self._sim.say_phases()

    def _on_camera(self):
        """1단계. EBS 가 설 자리에 카메라를 맞춘다. 민 거리는 그대로 둔다"""
        self._apply_settings()
        self._start(self._focusing, WORK_CAMERA)

    async def _focusing(self):
        """카메라를 잡고 한 줄 찍는다. 도는 동안은 다른 것을 안 받는다"""
        self._render(self._sim.focus(
            self._eqp_field.model.get_value_as_string()))
        self._overlay(EbsSimulateOverlay.hide)
        self._sim.say_phases()

    def _on_collide(self):
        """3단계. 충돌을 재고 오버레이를 띄운다"""
        self._apply_settings()
        self._start(self._collide_task, WORK_COLLIDE)

    async def _collide_task(self):
        """도는 동안 진행률을 적고, 끝나면 오버레이를 띄운다"""
        self._mark_nudge(busy=True)
        self._render(await self._watched(self._sim.collide_async()))
        self._overlay(EbsSimulateOverlay.show)
        await self._sim.settle()
        self._sim.say_phases()
        self._mark_nudge()

    def _on_near_span(self):
        """슬라이더를 끄는 그 자리에서 카메라에 반영한다"""
        span = self._sim.set_near_span(self._near_span())
        self._set_status(f"Near plane at {span:.2f} x half the EBS width")

    def _near_span(self) -> float:
        """슬라이더가 가리키는 근평면 배수"""
        try:
            return float(self._near_slider.model.get_value_as_float())
        except (AttributeError, TypeError, ValueError):
            return NEAR_SPAN

    def _reset_nudge(self):
        """민 거리를 0 으로. 지금 장비 이름을 기억해 둔다"""
        self._sim.set_nudge(0.0)
        self._nudge_for = self._eqp_field.model.get_value_as_string().strip()
        self._mark_nudge()

    def _mark_nudge(self, busy: bool = False):
        """어느 쪽으로 얼마나 밀어 뒀나. 미는 것은 뷰포트 손잡이가 한다"""
        if self._nudge_label is None:
            return
        metres = self._sim.get_nudge()
        if not metres:
            text = NUDGE_HOME
        else:
            way = NUDGE_RIGHT if metres > 0 else NUDGE_LEFT
            text = f"{way} {abs(metres):.3f} m"
        self._nudge_label.text = text + (NUDGE_BUSY if busy else "")

    def _on_refresh(self):
        """카메라만 원래 자리로 되돌린다"""
        self._start(self._refreshing, WORK_REFRESH)

    async def _refreshing(self):
        """되돌리고 한 줄 찍는다"""
        self._render(self._sim.refresh_camera())

    def _on_clear_markers(self):
        """사용자 동작. 서비스 API 한 줄이 절차를 다 들고 있다"""
        self._spawn(self._sim.run_clear())

    def _spawn(self, work):
        """서비스 한 줄을 띄우고 결과만 상태 줄에 적는다

        바쁨도 오버레이도 정착도 서비스 안에서 한다. 여기 남는 것은 도는
        동안 상태 줄에 진행률을 적는 것뿐이고, 그건 더미 위젯 일이다
        """
        self._task = asyncio.ensure_future(self._spun(work))

    async def _spun(self, work):
        """띄운 일을 지켜보다 끝나면 결과를 적는다"""
        self._render(await self._watched(work))

    def _start(self, make, label: str):
        """일 하나를 띄운다. 이미 도는 것이 있으면 아예 안 만든다

        make  코루틴이 아니라 코루틴을 만드는 함수다. 코루틴을 먼저 만들어
                 넘기면, 바빠서 버릴 때 안 기다린 코루틴이 남아 경고가 뜬다
        begin_work  띄우기 전에 그 자리에서 세운다. 한 프레임 안에 두 번
                 눌러도 뒤엣것이 막힌다. 손잡이도 같은 것을 본다
        """
        busy = self._sim.busy()
        if busy:
            self._set_status(f"Busy: {busy}")
            return
        self._overlay(EbsSimulateOverlay.wake)
        self._sim.begin_work(label)
        self._task = asyncio.ensure_future(self._working(make))

    async def _working(self, make):
        """일을 돌리고, 어떻게 끝나든 바쁨을 내린다"""
        try:
            await make()
        finally:
            self._sim.end_work()

    async def _watched(self, work):
        """일이 도는 동안 이름과 진행률과 흐른 시간을 상태 줄에 적는다"""
        import omni.kit.app
        started = time.monotonic()
        task = asyncio.ensure_future(work)
        while not task.done():
            self._set_status(
                f"{self._sim.busy()}"
                f" {self._sim.get_progress():3.0f}%"
                f"   {time.monotonic() - started:.1f}s")
            await omni.kit.app.get_app().next_update_async()
        return task.result()

    def _apply_settings(self):
        """입력칸과 콤보의 값을 서비스 설정으로 넘긴다"""
        self._sim.set_usd_path(self._usd_field.model.get_value_as_string())
        self._sim.set_xml_path(self._xml_field.model.get_value_as_string())
        self._sim.set_ebs_paths(
            self._ebs2_field.model.get_value_as_string(),
            self._ebs3_field.model.get_value_as_string(),
        )
        self._sim.set_search_root(self._root_field.model.get_value_as_string())
        self._sim.set_rail_root(self._rail_field.model.get_value_as_string())
        self._sim.set_show_lasers(self._lasers.model.get_value_as_bool())
        self._sim.set_near_span(self._near_span())
        self._sim.set_min_gaps(self._number(self._side_field, MIN_SIDE),
                                        self._number(self._ceiling_field, MIN_CEILING))
        self._sim.set_skin(
            self._skin_field.model.get_value_as_string().strip())

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
