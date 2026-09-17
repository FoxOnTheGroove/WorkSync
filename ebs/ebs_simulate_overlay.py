import asyncio
import math
import time

import omni.ui as ui
from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf

from .ebs_simulate_camera import viewport_window
from .ebs_simulate_service import EbsSimulateService, WORK_SETTLE

__all__ = ["EbsSimulateOverlay", "EbsSimulateMarks", "EbsSimulateGrip"]

STATE_CLEAR = "clear"
STATE_TIGHT = "tight"
STATE_CLASH = "clash"

FRAME_ID = "ebs_simulate_overlay"

CANNOT = "이 위치에 EBS 장비를 세울 수 없습니다."
INNER  = "내부 장비와 충돌"
HOME   = "원점"
SLID   = "{0:+.0f}mm"
STALE  = "~"

GRIP_HEAD  = 0.3
GRIP_THICK = 0.175 / 3.0 * 0.75
GRIP_FLARE = 0.25 * 0.75
GRIP_PICK  = 14.0

GRIP_IDLE, GRIP_HOLD = "idle", "hold"
GRIP_COLORS = {GRIP_IDLE: (0.85, 0.58, 0.05),
               GRIP_HOLD: (1.0, 0.92, 0.35)}

CLASH = "충돌"
GAP   = "여유"
TIGHT = "간섭"
SPAN  = "{0:.0f}mm"
LEAST = "(최소간격 : {0:.0f}mm)"
MM_PER_M  = 1000.0
GAP_WIDTH = 84

ABOVE, BELOW, LEFT, RIGHT, MIDDLE = "above", "below", "left", "right", "middle"
GRIP_WIDTH = 100
LINE_ROOM = 6
ROOM_HEADS = 1.5
PANEL_GAP = 0.1

SIDE_BY_SIDE = ("ceiling",)

COLOR_CAN    = 0xFF9AE7FF
COLOR_CANNOT = 0xFF1B39FC
COLOR_TEXT   = 0xFFFFFFFF
COLOR_INK    = 0xFF000000
TEXT_SIZE    = 19
FACE_SIZE    = 17
PAD_X, PAD_Y = 3, 1

MARKER_OPACITY  = 0.075
MARKER_EMISSION = 10000.0
COLOR_BLOCKED   = (0.9, 0.2, 0.2)
BLOCKED_OPACITY = 0.6
BLOCKED_EMISSION = 1000.0
COLOR_CLEAR     = (1.0, 1.0, 1.0)
SHEET_GAP       = 0.001

COLOR_GAP      = (1.0, 0.906, 0.604)
COLOR_TIGHT    = (0.988, 0.224, 0.106)
GAP_RADIUS     = 0.002
GAP_HEAD_HIGH  = 0.02
GAP_HEAD_WIDE  = 0.016
GAP_OPACITY    = 1.0
GAP_EMISSION   = 3000.0

LEAD_RADIUS = 0.001
LEAD_OVER   = 0.01
COLOR_LEAD  = (1.0, 1.0, 1.0)

WORK_WIDE     = 260
WORK_LINE     = 22
WORK_BAR      = 8
WORK_PAD      = 8
WORK_GAP      = 4
WORK_ROOM     = WORK_WIDE - WORK_PAD * 2
WORK_HIGH     = WORK_PAD * 2 + WORK_LINE * 2 + WORK_GAP * 2 + WORK_BAR
WORK_PCT      = "{0:.0f}%"
COLOR_WORK    = 0xE6141414
COLOR_TRACK   = 0x33FFFFFF
COLOR_FILL    = 0xFF20C8FF
WORK_SIZE     = 17

CLASH_ROOT    = "{0}/Clash"
CLASH_OPACITY = 0.35
CLASH_PAD     = 0.002
COLOR_CLASH   = (0.95, 0.15, 0.15)
CLASH_PULSE   = 2.0
CLASH_PULSE_LOW  = 0.15
CLASH_PULSE_HIGH = 1.0


class EbsSimulateOverlay:
    _instances = {}


    @classmethod
    def show(cls, vp_name: str = None):
        """뷰포트 오버레이를 띄운다. 판정을 다시 읽어 그린다"""
        overlay = cls._get(vp_name)
        if overlay is not None:
            overlay.refresh()
        return overlay

    @classmethod
    def build(cls, vp_name: str = None):
        """그리기만 하고 화면 배치는 미룬다"""
        overlay = cls._get(vp_name)
        if overlay is not None:
            overlay.refresh(place=False)
        return overlay

    @classmethod
    def reveal(cls, vp_name: str = None):
        """만들어 둔 판을 화면에 앉히고 카메라를 따라가게 한다"""
        for name, overlay in list(cls._instances.items()):
            if vp_name in (None, name):
                overlay._place()
                overlay._start()

    @classmethod
    def wake(cls, vp_name: str = None):
        """프레임만 세워 둔다. 작업중 표가 뜰 자리를 미리 잡는 것"""
        return cls._get(vp_name)

    @classmethod
    def hide(cls, vp_name: str = None):
        """그린 것을 지운다. 프레임은 남긴다"""
        for name, overlay in list(cls._instances.items()):
            if vp_name in (None, name):
                overlay.clear()

    @classmethod
    def destroy(cls, vp_name: str = None):
        """프레임까지 놓고 인스턴스를 버린다"""
        for name, overlay in list(cls._instances.items()):
            if vp_name in (None, name):
                overlay._destroy()
                cls._instances.pop(name, None)

    @classmethod
    def _get(cls, vp_name: str = None):
        """뷰포트별 인스턴스 하나. 없으면 만든다"""
        window = cls._window(vp_name)
        if window is None:
            return None
        name = window.name
        overlay = cls._instances.get(name)
        if overlay is None:
            overlay = cls(name)
            if not overlay._build(window):
                return None
            cls._instances[name] = overlay
        return overlay

    _window = staticmethod(viewport_window)

    def __init__(self, vp_name):
        """뷰포트마다 하나. _get 이 만들어 들고 있는다"""
        self._vp_name = vp_name
        self._window = None
        self._api = None
        self._frame = None
        self._stack = None
        self._marks = []
        self._follow = None
        self._texts = {}
        self._dials = {}
        self._dial_hold = None
        self._grounds = {}
        self._from = None
        self._was = 0.0
        self._work = None
        self._work_hold = None
        self._work_pct_hold = None
        self._work_words = {}
        self._work_pcts = {}
        self._work_fill = None
        self._work_gap = None
        self._work_panel = None

    def _build(self, window) -> bool:
        """뷰포트에 프레임을 걸고 투영에 쓸 viewport api 를 잡는다"""
        try:
            self._frame = window.get_frame(FRAME_ID)
            with self._frame:
                with ui.ZStack():
                    self._stack = ui.ZStack()
                    self._build_work()
        except Exception as e:
            print(f"[ebs] could not put the overlay on the viewport: {e}")
            return False
        self._api = getattr(window, "viewport_api", None)
        if self._api is None:
            try:
                from omni.kit.viewport.utility import get_active_viewport
                self._api = get_active_viewport()
            except Exception as e:
                print(f"[ebs] the overlay has no viewport to project through: {e}")
                return False
        self._window = window
        self._start()
        return True

    def _build_work(self) -> None:
        """작업중 표를 한 번만 지어 둔다. 보이고 감추는 것만 나중에 한다

        매번 다시 지으면 글자가 깜빡인다. 글줄은 모양마다 하나씩 두고
        하나만 켠다. ui.Label 은 처음 글로 잡아 둔 자리를 계속 쓴다
        여백은 Spacer 로 깐다. 스타일의 margin_width 는 자손까지 흘러내려
        막대 하나에도 좌우로 붙는다. 그러면 막대가 찰수록 판이 밀려난다
        속은 전부 판보다 좁게 못 박아서 무엇도 판을 밀 수 없게 한다
        """
        self._work = ui.Placer(draggable=False, offset_x=0, offset_y=0)
        with self._work:
            self._work_panel = ui.ZStack(width=ui.Pixel(WORK_WIDE),
                                         height=ui.Pixel(WORK_HIGH))
            with self._work_panel:
                ui.Rectangle(style={"background_color": COLOR_WORK,
                                    "border_radius": 6})
                with ui.VStack(spacing=0):
                    ui.Spacer(height=ui.Pixel(WORK_PAD))
                    self._work_hold = ui.ZStack(height=ui.Pixel(WORK_LINE))
                    ui.Spacer(height=ui.Pixel(WORK_GAP))
                    with ui.HStack(height=ui.Pixel(WORK_BAR)):
                        ui.Spacer(width=ui.Pixel(WORK_PAD))
                        with ui.ZStack(width=ui.Pixel(WORK_ROOM)):
                            ui.Rectangle(style={"background_color": COLOR_TRACK,
                                                "border_radius": 3})
                            with ui.HStack():
                                self._work_fill = ui.Rectangle(
                                    width=ui.Pixel(0),
                                    style={"background_color": COLOR_FILL,
                                           "border_radius": 3})
                                self._work_gap = ui.Spacer()
                        ui.Spacer(width=ui.Pixel(WORK_PAD))
                    ui.Spacer(height=ui.Pixel(WORK_GAP))
                    self._work_pct_hold = ui.ZStack(height=ui.Pixel(WORK_LINE))
                    ui.Spacer(height=ui.Pixel(WORK_PAD))
        self._work_panel.visible = False

    def _work_word(self, hold, store: dict, text: str) -> None:
        """그 글 모양의 글줄만 켠다. 없으면 그때 하나 만든다

        글줄 폭은 판 폭과 같다. 좁게 잡으면 그만큼 왼쪽으로 치우친다
        """
        if hold is None:
            return
        shape = "".join("0" if one.isdigit() else one for one in text)

        label = store.get(shape)
        if label is None:
            with hold:
                label = ui.Label(text, height=ui.Pixel(WORK_LINE),
                                 width=ui.Pixel(WORK_WIDE),
                                 alignment=ui.Alignment.CENTER,
                                 style={"color": COLOR_TEXT,
                                        "font_size": WORK_SIZE})
            store[shape] = label
        label.text = text
        for key, one in store.items():
            one.visible = key == shape

    def _work_place(self) -> None:
        """작업중이면 가운데에 앉히고 진행도를 고친다. 아니면 감춘다

        글은 동작 이름 하나뿐이다. 여러 구간을 도는 동작이면 막대가 구간마다
        제 몫 안에서 차오른다. 두 구간이면 앞이 0~50, 뒤가 50~100 이다
        막대는 제 자리 안에서만 찬다. 다 차도 WORK_ROOM 이라 판보다 좁다
        판 크기는 프레임마다 도로 못 박는다. 무엇이 밀어도 되돌아온다
        """
        panel = self._work_panel
        if panel is None or self._work is None:
            return
        busy = EbsSimulateService.busy()
        if not busy:
            panel.visible = False
            return
        done = max(0.0, min(EbsSimulateService.get_progress(), 100.0))
        self._work_word(self._work_hold, self._work_words, busy)
        self._work_word(self._work_pct_hold, self._work_pcts,
                        WORK_PCT.format(done))
        if self._work_fill is not None:
            self._work_fill.width = ui.Pixel(WORK_ROOM * done / 100.0)
        panel.width = ui.Pixel(WORK_WIDE)
        panel.height = ui.Pixel(WORK_HIGH)
        try:
            width = self._frame.computed_width
            height = self._frame.computed_height
        except Exception:
            return
        self._work.offset_x = (width - WORK_WIDE) * 0.5
        self._work.offset_y = (height - WORK_HIGH) * 0.5
        panel.visible = True

    def refresh(self, place: bool = True) -> bool:
        """판정을 읽어 판을 새로 그린다"""
        said = EbsSimulateService.get_verdict()
        self.clear()
        if not said or self._stack is None:
            return False
        try:
            with self._stack:
                self._verdict_panel(said)
                for mark in said.get("marks") or ():
                    self._face_panel(mark)
            EbsSimulateGrip.place(said, self._to_window)
        except Exception as e:
            print(f"[ebs] could not build the overlay: {e}")
            self.clear()
            return False
        if not place:
            return True
        self._place()
        return self._start()


    def _floating(self, at, fill, ground, anchor=MIDDLE, step: float = 0.0,
                  share: int = 1, group=None, key=None, on: bool = True,
                  wide: int = 0):
        """월드 좌표에 매달 판 하나. 같은 group 끼리 나란히 세운다

        wide  판 폭을 픽셀로 못 박는다. _place 가 computed_width 대신 이것을
              쓰므로 글이 바뀌어도 판이 제자리에서 안 흔들린다
        """
        if at is None:
            return
        placer = ui.Placer(draggable=False, offset_x=0, offset_y=0)
        with placer:
            panel = ui.ZStack(width=ui.Pixel(wide) if wide else 0, height=0)
            with panel:
                behind = ui.Rectangle(style={"background_color": ground,
                                             "border_radius": 4})
                fill()
        if key is not None:
            self._grounds.setdefault(key, []).append(behind)
        panel.visible = False
        self._marks.append([placer, panel, tuple(at), anchor, step,
                            share, group, key, on, wide])

    def _verdict_panel(self, said: dict) -> None:
        """못 세울 때만 보이는 한 줄. 판은 늘 만들어 둔다

        on  끄는 동안 세울 수 있게 바뀌면 _restate 가 이 표만 내린다. 판을
                  그때 만들면 마우스를 받고 있는 판을 갈아엎게 된다
        끄는 동안에는 둘 다 내린다. 손을 뗀 자리에서 다시 재고 올린다
        """
        held = EbsSimulateGrip.held()
        self._floating(said.get("centre"), self._one(CANNOT), COLOR_CANNOT,
                       key=("verdict", "centre"),
                       on=not held and not said.get("placeable"))
        self._floating(said.get("inside_at"), self._one(INNER), COLOR_CANNOT,
                       key=("verdict", "inside_at"),
                       on=not held and bool(said.get("inside")))
        self._offset_panel(said)

    def _one(self, text, ink=COLOR_TEXT, key=None):
        """_floating 에 넘길 그리기 함수. key 를 주면 글줄을 적어 둔다"""
        def fill():
            """판 속 글줄을 채운다"""
            with ui.VStack(spacing=0, style={"margin_width": PAD_X,
                                             "margin_height": PAD_Y}):
                self._label(text, ink, key)
        return fill

    def _offset_panel(self, said: dict) -> None:
        """손잡이 아래 OFFSET_HEIGHT 높이에 다는 이격 표"""
        def fill():
            """글줄을 담을 빈 칸 하나. 채우는 것은 _dial 이 한다"""
            with ui.VStack(spacing=0, style={"margin_width": PAD_X,
                                             "margin_height": PAD_Y}):
                self._dial_hold = ui.ZStack(
                    height=0, width=ui.Pixel(GRIP_WIDTH - PAD_X * 2))

        self._floating(self._grip_at(said, "under"), fill, COLOR_CAN,
                       key=("verdict", "offset"), wide=GRIP_WIDTH)
        self._dial(said)

    def _dial(self, said: dict) -> None:
        """이격 글줄. 글자 모양마다 제 글줄을 하나씩 두고 하나만 켠다

        ui.Label 은 text 만 갈아 끼우면 처음 글로 잡아 둔 그리기 자리를
        그대로 쓴다. 자릿수가 같은 글끼리만 한 글줄을 쓰면 가운데가 계속
        맞는다. 모양은 숫자를 0 으로 바꾼 꼴로 센다
        """
        hold = self._dial_hold
        if hold is None:
            return
        word = self._offset_word(said)
        shape = "".join("0" if one.isdigit() else one for one in word)
        label = self._dials.get(shape)
        if label is None:
            with hold:
                label = self._label(word, COLOR_INK, None,
                                    GRIP_WIDTH - PAD_X * 2)
            self._dials[shape] = label
        label.text = word
        for key, one in self._dials.items():
            one.visible = key == shape

    @staticmethod
    def _grip_at(said: dict, which: str = "at"):
        """손잡이 쪽 월드 자리. 손잡이가 없으면 None

        자리는 EBS 안 좌표라 뿌리 변환을 태워야 월드가 된다. 미는 동안에는
        그 변환만 바뀌므로 표도 저절로 따라간다
        which  at 은 손잡이가 선 자리, under 는 그 아래 이격 표 자리
        """
        grip = said.get("grip") or {}
        at, matrix = grip.get(which), grip.get("matrix")
        if at is None:
            return None
        if matrix is None:
            return tuple(at)
        got = matrix.Transform(Gf.Vec3d(*at))
        return (got[0], got[1], got[2])

    @staticmethod
    def _offset_word(said: dict) -> str:
        """지금 얼마나 밀려 있나(mm). 눈금 아래면 원점"""
        slid = said.get("offset") or 0.0
        return (SLID.format(EbsSimulateOverlay._mm(slid))
                if abs(slid) >= 5e-4 else HOME)

    @classmethod
    def restate(cls, vp_name: str = None) -> None:
        """판을 다시 만들지 않고 자리와 글자만 고친다. 손잡이가 끌 때 부른다"""
        for name, overlay in list(cls._instances.items()):
            if vp_name in (None, name):
                overlay._restate()

    def _restate(self) -> None:
        """끄는 동안. 판을 다시 만들지 않고 자리와 글자만 고친다"""
        said = EbsSimulateService.get_verdict()
        if not said:
            return
        spots = {("verdict", "centre"): said.get("centre"),
                 ("verdict", "inside_at"): said.get("inside_at"),
                 ("verdict", "offset"): self._grip_at(said, "under")}
        self._dial(said)
        for mark in said.get("marks") or ():
            spots[("face", mark["face"])] = mark.get("at")
            self._say(("face", mark["face"], "word"), self._word_of(mark))
            gap = mark.get("distance")
            if gap is not None:
                self._say(("face", mark["face"], "span"),
                          (STALE if mark.get("stale") else "")
                          + SPAN.format(self._mm(gap)))
        EbsSimulateGrip.place(said, self._to_window)
        for mark in said.get("marks") or ():
            self._repaint(("face", mark["face"]), mark.get("state"))
        held = EbsSimulateGrip.held()
        shown = {("verdict", "centre"): not held and not said.get("placeable"),
                 ("verdict", "inside_at"): not held and bool(said.get("inside"))}
        for entry in self._marks:
            at = spots.get(entry[7])
            if at is not None:
                entry[2] = tuple(at)
            if entry[7] in shown:
                entry[8] = shown[entry[7]]

    def _label(self, text: str, ink: int, key=None, wide: int = 0):
        """판 속 글줄 하나. key 를 주면 나중에 갈아 끼우려고 적어 둔다"""
        label = ui.Label(text, height=0,
                         width=ui.Pixel(wide) if wide else 0,
                         alignment=ui.Alignment.CENTER,
                         style={"font_size": TEXT_SIZE, "color": ink})
        if key is not None:
            self._texts.setdefault(key, label)
        return label

    def _say(self, key, text: str) -> None:
        """적어 둔 글줄 하나를 갈아 끼운다"""
        label = self._texts.get(key)
        if label is not None:
            label.text = text

    def _repaint(self, key, state: str) -> None:
        """그 면의 판 색을 지금 상태에 맞춘다. 끄는 동안 여유가 충돌로 바뀐다"""
        ground = COLOR_CAN if state == STATE_CLEAR else COLOR_CANNOT
        ink = COLOR_INK if state == STATE_CLEAR else COLOR_TEXT
        for behind in self._grounds.get(key, ()):
            behind.style = {"background_color": ground, "border_radius": 4}
        for one in ("word", "span", "least"):
            label = self._texts.get(key + (one,))
            if label is not None:
                label.style = {"font_size": FACE_SIZE, "color": ink}

    @staticmethod
    def _word_of(mark: dict) -> str:
        """그 면의 상태 한 낱말"""
        state = mark.get("state")
        return (CLASH if state == STATE_CLASH else
                TIGHT if state == STATE_TIGHT else GAP)

    def _face_panel(self, mark: dict) -> None:
        """한쪽에 상태, 다른 쪽에 거리와 최소 여유. 막힌 면도 똑같이 붙인다"""
        state = mark.get("state")
        ground = COLOR_CAN if state == STATE_CLEAR else COLOR_CANNOT
        ink = COLOR_INK if state == STATE_CLEAR else COLOR_TEXT
        gap = mark.get("distance")
        least = mark.get("min_gap")

        def block(lines, key=None, wide: int = 0):
            """_floating 에 넘길 그리기 함수. key 를 주면 글줄을 적어 둔다

            wide  글줄 칸을 이만큼으로. 자릿수가 바뀌어도 판이 안 들썩인다
            """
            def fill():
                """판 속 글줄을 채운다"""
                with ui.VStack(spacing=0, style={"margin_width": PAD_X,
                                                 "margin_height": PAD_Y}):
                    for text in lines:
                        label = ui.Label(text, height=0,
                                         width=ui.Pixel(wide) if wide else 0,
                                         alignment=ui.Alignment.CENTER,
                                         style={"font_size": FACE_SIZE,
                                                "color": ink})
                        if key is not None:
                            self._texts.setdefault(key, label)
            return fill

        at = mark.get("at")
        first, second = ((LEFT, RIGHT) if mark.get("face") in SIDE_BY_SIDE
                         else (ABOVE, BELOW))
        face = mark.get("face")
        self._floating(at, block([self._word_of(mark)],
                                 ("face", face, "word")), ground,
                       first, group=(face, first), key=("face", face))
        if gap is None:
            return
        share = 2 if least else 1
        self._floating(at, block([SPAN.format(self._mm(gap))],
                                 ("face", face, "span"), GAP_WIDTH), ground,
                       second, 0, share, (face, second), ("face", face),
                       wide=GAP_WIDTH)
        if least:
            self._floating(at, block([LEAST.format(self._mm(least))],
                                     ("face", face, "least")), ground, second,
                           1, share, (face, second), ("face", face))

    @staticmethod
    def _mm(metres: float) -> float:
        """미터로 잰 값을 밀리미터로. 판에 적는 단위다"""
        return (metres or 0.0) * MM_PER_M

    def _tick(self) -> None:
        """한 프레임 몫. 표를 보고, hover 를 묻고, 판을 카메라에 맞춘다"""
        self._work_place()
        self._place()

    def _start(self) -> bool:
        """매 프레임 _tick 을 부르도록 Kit 업데이트에 붙는다. 한 번만 붙는다"""
        if self._follow is not None:
            return True
        try:
            import omni.kit.app
            self._follow = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._tick(),
                                            name="ebs overlay follow")
        except Exception as e:
            print(f"[ebs] the overlay will not follow the camera: {e}")
            return False
        return True

    def _place(self) -> None:
        """월드 좌표를 화면 좌표로 옮겨 판을 앉힌다. 화면 밖이면 숨긴다"""
        if not self._marks:
            return
        try:
            width = self._frame.computed_width
            height = self._frame.computed_height
            widest = {}
            for _, panel, _, _, _, _, group, _, on, wide in self._marks:
                if not on:
                    continue
                if group is not None:
                    widest[group] = max(widest.get(group, 0.0),
                                        wide or panel.computed_width)
            for placer, panel, at, anchor, step, share, group, _, on, wide in \
                    self._marks:
                spot = self._to_screen(at) if on else None
                if spot is None:
                    panel.visible = False
                    continue
                panel_w = wide or panel.computed_width
                panel_h = panel.computed_height
                room = self._room_at(at, spot)
                stack = panel_h * (1.0 + PANEL_GAP)
                block = widest.get(group, panel_w)
                inset = (block - panel_w) * 0.5
                x, y = spot[0] - panel_w * 0.5, spot[1] - panel_h * 0.5
                if anchor == ABOVE:
                    y = spot[1] - panel_h - room - step * stack
                elif anchor == BELOW:
                    y = spot[1] + room + step * stack
                elif anchor in (LEFT, RIGHT):
                    y += (step - (share - 1) * 0.5) * stack
                    x = (spot[0] - block - room + inset if anchor == LEFT
                         else spot[0] + room + inset)
                if self._outside(x, y, panel_w, panel_h, width, height):
                    panel.visible = False
                    continue
                placer.offset_x = x
                placer.offset_y = y
                panel.visible = True
        except Exception as e:
            print(f"[ebs] could not place the overlay: {e}")
            self.clear()

    def _room_at(self, at, spot) -> float:
        """선과 판 사이 여백. 화살촉 반지름의 ROOM_HEADS 배가 화면에서 몇 픽셀인가"""
        try:
            want = GAP_HEAD_WIDE * ROOM_HEADS
            camera = self._api.view.GetInverse()
            side = Gf.Vec3d(camera[0][0], camera[0][1], camera[0][2])
            side = side.GetNormalized() * want
            other = self._to_screen([at[i] + side[i] for i in range(3)])
        except Exception:
            other = None
        if other is None:
            return LINE_ROOM
        step = ((other[0] - spot[0]) ** 2 + (other[1] - spot[1]) ** 2) ** 0.5
        return step if step > 0.5 else LINE_ROOM

    @staticmethod
    def _outside(x, y, panel_w, panel_h, width, height) -> bool:
        """판이 프레임 밖으로 나갔나"""
        if width <= 0 or height <= 0:
            return False
        return x < 0 or y < 0 or x + panel_w > width or y + panel_h > height

    def _to_screen(self, point):
        """월드 점을 화면 픽셀로. 카메라 뒤나 화면 밖이면 None"""
        api = self._api
        at = Gf.Vec3d(*point)
        view = api.view
        if view.Transform(at)[2] >= 0.0:
            return None
        try:
            clip = api.world_to_ndc
        except AttributeError:
            clip = view * api.projection
        ndc = clip.Transform(at)
        if not (-1.0 <= ndc[0] <= 1.0 and -1.0 <= ndc[1] <= 1.0):
            return None
        width = self._frame.computed_width
        height = self._frame.computed_height
        if not width or not height:
            return None
        return ((ndc[0] * 0.5 + 0.5) * width,
                (0.5 - ndc[1] * 0.5) * height)

    def _to_window(self, point):
        """월드 점을 창 좌표로. 마우스 콜백이 주는 좌표계와 같다

        _to_screen 은 판 안 좌표라 판이 창 안쪽에 있으면 그만큼 어긋난다
        손잡이를 집을 때는 이쪽을 쓴다
        """
        at = self._to_screen(point)
        if at is None:
            return None
        try:
            return (at[0] + self._frame.screen_position_x,
                    at[1] + self._frame.screen_position_y)
        except Exception:
            return at


    def clear(self) -> None:
        """그린 판을 놓는다. 프레임 구독은 그대로 둔다

        _follow  작업중 표도 여기서 돈다. Clear 가 맨 먼저 이걸 부르므로
                 여기서 놓으면 지우는 동안 표가 안 보인다
        """
        EbsSimulateGrip.hide()
        self._marks = []
        self._texts = {}
        self._dials = {}
        self._dial_hold = None
        self._grounds = {}
        if self._stack is not None:
            try:
                self._stack.clear()
            except Exception:
                pass

    def _destroy(self) -> None:
        """프레임과 api 참조까지 전부 놓는다"""
        self.clear()
        EbsSimulateGrip.destroy()
        self._follow = None
        self._work = None
        self._work_hold = None
        self._work_pct_hold = None
        self._work_words = {}
        self._work_pcts = {}
        self._work_fill = None
        self._work_gap = None
        self._work_panel = None
        self._stack = None
        self._frame = None
        self._api = None
        self._window = None


class EbsSimulateGrip:
    """EBS 앞 공중에 뜬 양방향 화살표. 끌면 EBS 가 좌우로 간다

    프림으로 그린다. 마우스는 카메라의 입력 판이 먼저 받아 이리로 넘긴다
    _hit  화면에 비친 몸통에서 GRIP_PICK 픽셀 안이면 잡은 것으로 본다
    EbsSimulateService.slide  끈 만큼을 넘긴다. 다시 재지 않고 산수로 따라간다
    """

    _one = None
    _again = False

    @classmethod
    def place(cls, said: dict, to_screen) -> bool:
        """판정이 준 자리 앞에 손잡이를 세운다. 없으면 만든다"""
        grip = said.get("grip") or {}
        if not grip.get("at"):
            cls.hide()
            return False
        if cls._one is None:
            cls._one = cls()
        return cls._one.stand(grip, to_screen)

    @classmethod
    def held(cls) -> bool:
        """잡고 있거나, 놓고 다시 재는 중인가. 판정 표를 내릴지 여기로 묻는다

        _again  놓은 뒤 다시 재기 전까지는 판정이 민 자리 것이 아니다. 그
                 사이에 표를 올리면 없어질 충돌이 잠깐 떴다 사라진다
        """
        return cls._again or (cls._one is not None and cls._one.holding)

    @classmethod
    def hide(cls) -> None:
        """그린 것을 지운다"""
        if cls._one is not None:
            cls._one.wipe()

    @classmethod
    def destroy(cls) -> None:
        """지우고 손을 뗀다"""
        cls.hide()
        cls._again = False
        cls._one = None

    def __init__(self):
        """자리만. 세우는 것은 stand. 뿌리는 충돌에서 빼는 쪽과 같아야 한다"""
        from .ebs_simulate import GRIP_ROOT
        self._root = GRIP_ROOT
        self._paint = EbsSimulateMarks(self._stage, self._root)
        self._ends = None
        self._matrix = None
        self._reach = 1.0
        self._high = 1.0
        self._unit = 1.0
        self._to_screen = None
        self._state = ""
        self._from = None
        self._was = 0.0

    @staticmethod
    def _stage():
        """지금 열린 스테이지"""
        try:
            import omni.usd
            return omni.usd.get_context().get_stage()
        except Exception:
            return None

    def stand(self, grip: dict, to_screen) -> bool:
        """EBS 안 좌표로 눕힌다. 뿌리에 EBS 변환을 걸어 같이 움직인다

        몸통 중점은 EBS 상자 앞면에 정확히 앉는다. 앞으로 띄우지 않는다
        """
        self._to_screen = to_screen
        self._matrix = grip.get("matrix")
        self._high = max(grip.get("high") or 0.0, 1e-6)
        self._reach = max((grip.get("wide") or 0.0) * 0.5, 1e-6)
        self._unit = grip.get("unit") or 1.0
        spot, side = list(grip["at"]), grip["side"]
        ends = []
        for way in (-1.0, 1.0):
            end = list(spot)
            end[side] += self._reach * way
            ends.append(tuple(end))
        self._ends = tuple(ends)
        EbsSimulateService.watch_grip(self)
        return self._draw(GRIP_HOLD if self._from is not None else GRIP_IDLE)

    def _draw(self, state: str) -> bool:
        """그 상태 색으로 몸통 하나와 화살촉 둘. 뿌리가 EBS 를 따라간다

        머티리얼은 상태마다 두지 않고 하나를 색만 바꿔 쓴다. 새로 세우면
        OmniPBR 인스턴스가 하나 더 생겨 RTX 가 MDL 을 그 자리에서 컴파일한다.
        잡는 순간 그 값을 물면 처음 잡을 때만 몇 초씩 멈춘다
        """
        stage = self._stage()
        if stage is None or self._ends is None:
            return False
        self._state = state
        colour = GRIP_COLORS[state]
        one, two = self._ends
        thick = self._high * GRIP_THICK
        high, wide = self._reach * GRIP_HEAD, self._high * GRIP_FLARE
        body = EbsSimulateMarks._gap_shaft(one, two, high)
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = UsdGeom.Xform.Define(stage, self._root)
                if self._matrix is not None:
                    EbsSimulateMarks._moved(root, self._matrix)
                skin = self._paint._material(stage, "grip", colour,
                                             1.0, 0.0, glow=False)
                self._paint._gap_line(stage, f"{self._root}/shaft", body[0],
                                      body[1], thick, skin, colour)
                for name, tip, back in (("a", one, two), ("b", two, one)):
                    self._paint._gap_head(stage, f"{self._root}/head_{name}",
                                          tip, back, skin, colour, high, wide)
        except Exception as e:
            print(f"[ebs] could not draw the grip: {e}")
            return False
        return True

    def wipe(self) -> None:
        """그린 것을 지우고 잡은 것도 놓는다"""
        self._from = None
        self._ends = None
        self._matrix = None
        self._paint.clear()

    def press(self, x: float, y: float) -> bool:
        """여기서 눌렸나. 눌렸으면 끌기를 시작한다

        도는 일이 있으면 안 받는다. 재는 중에 밀면 판정 한 벌을 바깥에서
        갈아엎게 된다
        """
        if self._ends is None or EbsSimulateService.busy():
            return False
        if not self._hit(x, y):
            return False
        self._from = x
        self._was = EbsSimulateService.get_nudge()
        EbsSimulateService.hold_clash(False)
        self._draw(GRIP_HOLD)
        return True

    def drag(self, x: float, y: float) -> bool:
        """끄는 중이면 그만큼 민다"""
        if self._from is None or EbsSimulateService.busy():
            return False
        per = self._unit_pixels()
        if per:
            metres = (x - self._from) / per * self._unit
            EbsSimulateService.slide(self._was + metres)
            EbsSimulateOverlay.restate()
        return True

    def release(self) -> None:
        """놓는다. 다시 재는 것은 다음 프레임부터 따로 돈다

        마우스 이벤트 안에서 재면 그동안 킷이 멈춘다. 띄워 놓고 바로
        돌려주면 그 사이에 화면이 한 번 그려진다
        _again  다시 재기 전에 세운다. 그래야 그 사이 restate 가 낡은 판정
                 으로 표를 올리지 않는다
        """
        if self._from is None:
            return
        self._from = None
        self._draw(GRIP_IDLE)
        if EbsSimulateService.busy():
            EbsSimulateOverlay.restate()
            return
        EbsSimulateGrip._again = True
        EbsSimulateOverlay.restate()
        EbsSimulateOverlay.wake()
        EbsSimulateService.begin_work(WORK_SETTLE)
        asyncio.ensure_future(self._settle())

    async def _settle(self) -> None:
        """손을 뗀 자리에서 내부 충돌을 다시 재고 연출을 되켠다

        다시 켠 상자가 실제로 빛나기 시작할 때까지 표를 세워 둔다. 저작이
        끝난 자리에서 내리면 표가 사라지고도 한참 아무것도 안 보인다
        판정 표는 다 재고 나서 한 번에 올린다. 터져도 finally 가 올린다
        """
        try:
            import omni.kit.app
            await omni.kit.app.get_app().next_update_async()
            EbsSimulateService.hold_clash(True)
            await EbsSimulateService.settle()
        except Exception as e:
            print(f"[ebs] could not retest after the grip: {e}")
        finally:
            EbsSimulateGrip._again = False
            EbsSimulateOverlay.restate()
            EbsSimulateService.end_work()

    @property
    def holding(self) -> bool:
        """지금 잡고 있나"""
        return self._from is not None

    def _hit(self, x: float, y: float) -> bool:
        """화면에 비친 몸통에서 몇 픽셀 안인가"""
        spots = self._screen_ends()
        if spots is None:
            return False
        (ax, ay), (bx, by) = spots
        dx, dy = bx - ax, by - ay
        size = dx * dx + dy * dy
        if size <= 1e-9:
            return False
        along = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / size))
        near_x, near_y = ax + dx * along, ay + dy * along
        pick = self._pick_pixels(size ** 0.5)
        return (x - near_x) ** 2 + (y - near_y) ** 2 <= pick * pick

    def _pick_pixels(self, pixels: float) -> float:
        """집을 수 있는 반지름. 가까이 가서 굵게 그려지면 그만큼 넓게 잡는다

        굵기와 길이는 둘 다 EBS 안 길이라 비로 두면 크기가 얼마든 맞는다
        """
        fat = max(GRIP_THICK, GRIP_FLARE) * self._high
        drawn = pixels * fat / (self._reach * 2.0) if self._reach else 0.0
        return max(GRIP_PICK, drawn)

    def _unit_pixels(self) -> float:
        """스테이지 한 단위가 화면에서 몇 픽셀인가. 못 재면 0

        몸통을 화면에서 재고 월드에서 재서 나눈다. EBS 크기가 얼마든 맞는다
        """
        spots, ends = self._screen_ends(), self._world_ends()
        if spots is None or ends is None:
            return 0.0
        (ax, ay), (bx, by) = spots
        pixels = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        reach = sum((ends[1][i] - ends[0][i]) ** 2 for i in range(3)) ** 0.5
        return pixels / reach if pixels and reach else 0.0

    def _world_ends(self):
        """몸통 양 끝의 월드 좌표. EBS 변환을 태워서 낸다"""
        if self._ends is None:
            return None
        if self._matrix is None:
            return self._ends
        return tuple(tuple(self._matrix.Transform(Gf.Vec3d(*end)))
                     for end in self._ends)

    def _screen_ends(self):
        """몸통 양 끝의 화면 좌표. 화면 밖이면 None"""
        ends = self._world_ends()
        if ends is None or self._to_screen is None:
            return None
        one, two = self._to_screen(ends[0]), self._to_screen(ends[1])
        return None if one is None or two is None else (one, two)


class EbsSimulateMarks:
    """collide 가 씬에 그리는 것 전부. 3면 판, 여유 선, 화살촉, 안내선, 충돌 상자"""

    def __init__(self, stage_of, root: str):
        """스테이지를 주는 함수와, 그린 것을 담을 뿌리 경로"""
        self._stage_of = stage_of
        self._root = root
        self._pulse = None
        self._pulse_inputs: tuple = ()
        self._pulse_from: float = 0.0
        self._clash_at: dict = {}
        self._clash_lit: bool = True

    def draw(self, sheets: list, marks: list = None, boxes: list = None,
             fresh: bool = True) -> int:
        """판정 한 벌을 씬에 그린다. fresh 면 먼저 지우고, 아니면 고쳐 그린다

        fresh=False  프림을 지웠다 다시 만들지 않는다. 이름이 같으니 Define 이
                  있던 것을 돌려주고 속성만 새로 쓴다. 미는 동안 이 길로 온다
        """
        stage = self._stage_of()
        if stage is None:
            return 0
        if fresh:
            self.clear()
        drawn = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, self._root)
            drawn += self._sheets(stage, sheets)
            drawn += self._gap_lines(stage, marks)
            drawn += self._clash_boxes(stage, boxes)
        return drawn

    def hide_clash(self) -> bool:
        """내부충돌연출을 걷는다. 지우지 않고 뿌리 하나만 감춘다

        지우면 상자마다 rprim 이 사라지고, 켤 때 그만큼 다시 지어야 한다.
        손잡이를 잡을 때마다 그 값을 물면 잡는 순간이 걸린다
        뿌리 하나를 invisible 로 두면 Hydra 는 깃발만 뒤집는다. 지어 둔 것도
        _clash_at 도 그대로라 켤 때는 걸린 것만 다시 보이면 된다
        머티리얼은 Looks 아래 그대로 두어 다시 켤 때 만들 일이 없게 한다
        """
        self._stop_pulse()
        return self._light_clash(False)

    def _light_clash(self, on: bool) -> bool:
        """상자 뿌리를 켜고 끈다. 달라질 때만 쓴다"""
        if on == self._clash_lit:
            return False
        stage = self._stage_of()
        if stage is None:
            return False
        where = CLASH_ROOT.format(self._root)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            prim = stage.GetPrimAtPath(where)
            if prim is None or not prim.IsValid():
                return False
            UsdGeom.Imageable(prim).GetVisibilityAttr().Set(
                UsdGeom.Tokens.inherited if on else UsdGeom.Tokens.invisible)
        self._clash_lit = on
        return True

    def clear(self) -> None:
        """뿌리를 통째로 지우고 깜박임도 놓는다"""
        self._stop_pulse()
        self._clash_at = {}
        self._clash_lit = True
        stage = self._stage_of()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(self._root).IsValid():
                stage.RemovePrim(self._root)


    def _sheets(self, stage, sheets: list) -> int:
        """3면을 칸마다 한 장씩. 막힌 칸은 빨강, 아니면 흰색"""
        looks = {
            True: (self._material(stage, "blocked", COLOR_BLOCKED,
                                  BLOCKED_OPACITY, BLOCKED_EMISSION),
                   COLOR_BLOCKED, BLOCKED_OPACITY),
            False: (self._material(stage, "clear", COLOR_CLEAR),
                    COLOR_CLEAR, MARKER_OPACITY),
        }
        drawn = 0
        for name, points, blocked in sheets or ():
            material, colour, alpha = looks[bool(blocked)]
            self._sheet(stage, f"{self._root}/{name}", points, material,
                        colour, alpha)
            drawn += 1
        return drawn

    def _gap_lines(self, stage, marks: list) -> int:
        """면마다 여유 선 하나, 양 끝 화살촉, 그리고 잰 자리로 가는 안내선"""
        threads = {}
        drawn = 0
        for mark in marks or ():
            if not mark.get("from") or not mark.get("to"):
                continue
            warn = mark.get("state") in (STATE_TIGHT, STATE_CLASH)
            colour = COLOR_TIGHT if warn else COLOR_GAP
            if colour not in threads:
                threads[colour] = self._material(
                    stage, "tight" if warn else "gap", colour,
                    GAP_OPACITY, GAP_EMISSION)
            shaft = self._gap_shaft(mark["from"], mark["to"])
            if self._gap_line(stage, f"{self._root}/{mark['face']}_gap",
                              shaft[0], shaft[1], GAP_RADIUS,
                              threads[colour], colour):
                drawn += 1
            drawn += self._gap_heads(stage, mark["face"], mark["from"],
                                     mark["to"], threads[colour], colour)
            drawn += self._lead_line(stage, mark, threads)
        return drawn

    def _lead_line(self, stage, mark: dict, threads: dict) -> int:
        """선 끝에서 멈춘 자리까지 흰 안내선 한 줄. 꺾는 자리는 건너뛴다"""
        lead = mark.get("lead")
        if not lead:
            return 0
        if COLOR_LEAD not in threads:
            threads[COLOR_LEAD] = self._material(
                stage, "lead", COLOR_LEAD, GAP_OPACITY, GAP_EMISSION)
        one, two = self._stretched(mark["to"], lead[-1], LEAD_OVER)
        drawn = int(self._gap_line(stage, f"{self._root}/{mark['face']}_lead_0",
                                   one, two, LEAD_RADIUS, threads[COLOR_LEAD],
                                   COLOR_LEAD))
        # 꺾어 가며 마디마다 긋던 것. 멈출 자리를 고르는 _lead_path 는 그대로라
        # 아래를 되살리면 다시 꺾어 그린다 (마디마다 프림 하나)
        # drawn = 0
        # spot = mark["to"]
        # for at, step in enumerate(lead):
        #     one, two = self._stretched(spot, step, LEAD_OVER)
        #     if self._gap_line(stage,
        #                       f"{self._root}/{mark['face']}_lead_{at}",
        #                       one, two, LEAD_RADIUS, threads[COLOR_LEAD],
        #                       COLOR_LEAD):
        #         drawn += 1
        #     spot = step
        return drawn + self._lead_tick(stage, mark, threads[COLOR_LEAD])

    def _lead_tick(self, stage, mark: dict, material) -> int:
        """멈춘 자리에 짧은 눈금 하나. 안내선과 직각으로, 삐져나온 길이의 두 배"""
        way = mark.get("tick")
        spot = (mark.get("lead") or [None])[-1]
        if not way or not spot:
            return 0
        span = sum(v * v for v in way) ** 0.5
        if span <= 1e-9:
            return 0
        step = [v / span * LEAD_OVER for v in way]
        return int(self._gap_line(
            stage, f"{self._root}/{mark['face']}_tick",
            tuple(spot[i] - step[i] for i in range(3)),
            tuple(spot[i] + step[i] for i in range(3)),
            LEAD_RADIUS, material, COLOR_LEAD))

    @staticmethod
    def _stretched(start, end, over: float):
        """두 점을 잇되 양 끝을 over 만큼 더 뻗는다"""
        along = Gf.Vec3d(*[end[i] - start[i] for i in range(3)])
        if along.GetLength() <= 1e-9:
            return start, end
        step = along.GetNormalized() * over
        return (tuple(start[i] - step[i] for i in range(3)),
                tuple(end[i] + step[i] for i in range(3)))

    def _clash_boxes(self, stage, boxes) -> int:
        """걸린 장비 메쉬마다 빨간 반투명 상자 하나

        상자는 장비 메쉬의 자리라 EBS 를 밀어도 안 움직인다. 그래서 한 번
        세워 두고 걸린 것만 보이게 한다. 다시 지을 일이 없으니 깜박임도
        안 끊긴다
        boxes  None 이면 손대지 않는다. 빈 목록이면 전부 감춘다
        _light_clash  잡는 동안 감춰 둔 뿌리를 도로 켠다
        """
        if boxes is None:
            return 0
        where = CLASH_ROOT.format(self._root)
        UsdGeom.Scope.Define(stage, where)
        self._light_clash(True)
        show = set()
        for path, lo, hi in boxes:
            name = self._clash_at.get(path)
            if name is None:
                name = f"{where}/box_{len(self._clash_at)}"
                self._clash_cube(stage, name, lo, hi)
                self._clash_at[path] = name
            show.add(name)
        drawn = 0
        for name in self._clash_at.values():
            prim = stage.GetPrimAtPath(name)
            if prim is None or not prim.IsValid():
                continue
            on = name in show
            UsdGeom.Imageable(prim).GetVisibilityAttr().Set(
                UsdGeom.Tokens.inherited if on else UsdGeom.Tokens.invisible)
            drawn += int(on)
        if drawn:
            self._start_pulse(stage)
        else:
            self._stop_pulse()
        return drawn

    def _clash_cube(self, stage, path: str, lo, hi) -> None:
        """그 자리에 상자 하나를 세운다. 한 번 세우면 다시 안 건드린다"""
        pad = self._clash_pad(stage)
        middle = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        half = [(hi[i] - lo[i]) * 0.5 + pad for i in range(3)]
        block = UsdGeom.Cube.Define(stage, path)
        block.CreateSizeAttr(2.0)
        block.CreateExtentAttr([Gf.Vec3f(-1.0, -1.0, -1.0),
                                Gf.Vec3f(1.0, 1.0, 1.0)])
        block.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*COLOR_CLASH)]))
        block.CreateDisplayOpacityAttr(Vt.FloatArray([CLASH_OPACITY]))
        matrix = Gf.Matrix4d(1.0)
        matrix.SetScale(Gf.Vec3d(*half))
        matrix.SetTranslateOnly(Gf.Vec3d(*middle))
        self._moved(block, matrix)
        UsdShade.MaterialBindingAPI(block.GetPrim()).Bind(
            self._material(stage, "clash", COLOR_CLASH,
                           CLASH_OPACITY, BLOCKED_EMISSION))

    @staticmethod
    def _moved(shape, matrix) -> None:
        """그 프림의 변환을 쓴다. 있던 것이면 갈아 끼운다

        fresh=False 로 다시 그릴 때 AddTransformOp 를 또 부르면 USD 가 막는다
        """
        xformable = UsdGeom.Xformable(shape)
        op = next((one for one in xformable.GetOrderedXformOps()
                   if one.GetOpName() == "xformOp:transform"), None)
        if op is None:
            xformable.ClearXformOpOrder()
            op = xformable.AddTransformOp()
        op.Set(matrix)

    @staticmethod
    def _clash_pad(stage) -> float:
        """CLASH_PAD 는 m 다. 씬 단위로 바꿔 준다"""
        try:
            per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        except Exception:
            per_unit = 1.0
        return CLASH_PAD / (per_unit or 1.0)


    def _start_pulse(self, stage) -> bool:
        """내부 충돌 상자를 CLASH_PULSE 주기로 깜박인다. clear 가 멈춘다

        이미 돌고 있으면 그대로 둔다. 다시 걸면 깜박임이 처음으로 되돌아간다
        """
        if self._pulse is not None:
            return True
        self._stop_pulse()
        if CLASH_PULSE <= 0.0:
            return False
        inputs = self._pulse_inputs_of(stage, self._root)
        if not inputs:
            return False
        self._pulse_inputs = inputs
        self._pulse_from = time.monotonic()
        try:
            import omni.kit.app
            self._pulse = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._pulse_step(),
                                            name="ebs clash pulse")
        except Exception as e:
            self._pulse_inputs = ()
            print(f"[ebs] the clash boxes will not blink: {e}")
            return False
        return True

    @staticmethod
    def _pulse_inputs_of(stage, root: str) -> tuple:
        """깜박일 때 건드릴 속성과 1.0 일 때의 값. 투명도만 건드린다"""
        looks = f"{root}/Looks/clash"
        wanted = ((f"{looks}/shader", "inputs:opacity", 1.0),
                  (f"{looks}/mdl", "inputs:opacity_constant", 1.0))
        found = []
        try:
            for path, name, full in wanted:
                prim = stage.GetPrimAtPath(path)
                if prim is None or not prim.IsValid():
                    continue
                attribute = prim.GetAttribute(name)
                if attribute:
                    found.append((attribute, full))
        except Exception:
            return ()
        return tuple(found)

    def _pulse_step(self) -> None:
        """한 프레임 몫. 지금 시각으로 투명도를 정해 머티리얼에 쓴다"""
        stage = self._stage_of()
        if stage is None or not self._pulse_inputs:
            self._stop_pulse()
            return
        phase = (time.monotonic() - self._pulse_from) / CLASH_PULSE
        level = CLASH_PULSE_LOW + (CLASH_PULSE_HIGH - CLASH_PULSE_LOW) \
            * (0.5 - 0.5 * math.cos(phase * 2.0 * math.pi))
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for attribute, full in self._pulse_inputs:
                    attribute.Set(level * full)
        except Exception as e:
            print(f"[ebs] the clash boxes stopped blinking: {e}")
            self._stop_pulse()

    def _stop_pulse(self) -> None:
        """깜박임 구독을 놓는다"""
        self._pulse = None
        self._pulse_inputs = ()


    @staticmethod
    def _gap_shaft(start, end, head: float = GAP_HEAD_HIGH):
        """선은 원뿔 중점에서 시작한다. 뭉툭한 끝이 뾰족한 끝을 먹지 않게"""
        along = Gf.Vec3d(*[end[i] - start[i] for i in range(3)])
        span = along.GetLength()
        if span <= head:
            return start, end
        step = along.GetNormalized() * (head * 0.5)
        return (tuple(start[i] + step[i] for i in range(3)),
                tuple(end[i] - step[i] for i in range(3)))

    def _gap_heads(self, stage, face: str, start, end, material, colour) -> int:
        """선 양 끝에 원뿔을 붙여 화살표로 보이게 한다"""
        made = 0
        for name, tip, back in (("a", start, end), ("b", end, start)):
            if self._gap_head(stage, f"{self._root}/{face}_head_{name}",
                              tip, back, material, colour):
                made += 1
        return made

    @staticmethod
    def _gap_head(stage, path: str, tip, back, material, colour,
                  high: float = GAP_HEAD_HIGH,
                  wide: float = GAP_HEAD_WIDE) -> bool:
        """tip 을 향해 뾰족한 원뿔 하나. tip 에서 back 쪽으로 뒤가 눕는다"""
        along = Gf.Vec3d(*[tip[i] - back[i] for i in range(3)])
        if along.GetLength() <= 1e-9:
            return False
        along = along.GetNormalized()
        cone = UsdGeom.Cone.Define(stage, path)
        cone.CreateAxisAttr(UsdGeom.Tokens.z)
        cone.CreateHeightAttr(high)
        cone.CreateRadiusAttr(wide)
        cone.CreateExtentAttr(Vt.Vec3fArray([
            Gf.Vec3f(-wide, -wide, -high / 2.0),
            Gf.Vec3f(wide, wide, high / 2.0)]))
        cone.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*colour)]))
        matrix = Gf.Matrix4d(1.0)
        matrix.SetRotate(Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0), along))
        matrix.SetTranslateOnly(
            Gf.Vec3d(*[tip[i] - along[i] * high * 0.5 for i in range(3)]))
        EbsSimulateMarks._moved(cone, matrix)
        try:
            cone.GetPrim().CreateAttribute(
                "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        except Exception:
            pass
        if material:
            UsdShade.MaterialBindingAPI(cone.GetPrim()).Bind(material)
        return True

    @staticmethod
    def _gap_line(stage, path: str, start, end, radius: float, material,
                  colour=COLOR_GAP) -> bool:
        """두 점 사이에 실린더 하나. 길이가 0 이면 안 그린다"""
        direction = Gf.Vec3d(*[end[i] - start[i] for i in range(3)])
        height = direction.GetLength()
        if height <= 1e-9:
            return False
        rod = UsdGeom.Cylinder.Define(stage, path)
        rod.CreateAxisAttr(UsdGeom.Tokens.z)
        rod.CreateHeightAttr(height)
        rod.CreateRadiusAttr(radius)
        rod.CreateExtentAttr(Vt.Vec3fArray([
            Gf.Vec3f(-radius, -radius, -height / 2.0),
            Gf.Vec3f(radius, radius, height / 2.0)]))
        rod.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*colour)]))
        matrix = Gf.Matrix4d(1.0)
        matrix.SetRotate(Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0),
                                     direction.GetNormalized()))
        matrix.SetTranslateOnly(
            Gf.Vec3d(*[(start[i] + end[i]) * 0.5 for i in range(3)]))
        EbsSimulateMarks._moved(rod, matrix)
        try:
            rod.GetPrim().CreateAttribute(
                "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        except Exception:
            pass
        if material:
            UsdShade.MaterialBindingAPI(rod.GetPrim()).Bind(material)
        return True


    @staticmethod
    def _face_normal(points: list) -> tuple:
        """그 사각형의 법선"""
        a, b, c = points[0], points[1], points[2]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(sum(value * value for value in n))
        return tuple(value / length for value in n) if length else (0.0, 0.0, 1.0)

    @classmethod
    def _sheet(cls, stage, path: str, points: list, material, color,
               opacity: float = MARKER_OPACITY) -> None:
        """면 판 한 장. 발광이 양면이 안 돼서 앞뒤 두 장을 겹친다"""
        normal = cls._face_normal(points)
        diagonal = math.sqrt(sum((points[2][i] - points[0][i]) ** 2
                                 for i in range(3)))
        gap = max(diagonal * SHEET_GAP, 1e-9)
        behind = [tuple(corner[i] - normal[i] * gap for i in range(3))
                  for corner in points]
        cls._quad(stage, path, points, material, color, opacity)
        cls._quad(stage, path + "_back", behind, material, color, opacity,
                  flip=True)

    @staticmethod
    def _quad(stage, path: str, points: list, material, color,
              opacity: float = MARKER_OPACITY, flip: bool = False) -> None:
        """사각형 메시 한 장. 양면이고 그림자는 안 만든다"""
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4]))
        mesh.CreateFaceVertexIndicesAttr(
            Vt.IntArray([3, 2, 1, 0] if flip else [0, 1, 2, 3]))
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
        mesh.CreateDisplayOpacityAttr(Vt.FloatArray([opacity]))
        try:
            mesh.GetPrim().CreateAttribute(
                "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        except Exception:
            pass
        if material:
            UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(material)

    def _material(self, stage, name: str, color, opacity: float = MARKER_OPACITY,
                  emission: float = MARKER_EMISSION, glow: bool = True):
        """마커용 머티리얼. preview 와 MDL 두 셰이더를 단다

        glow  색을 발광으로 낸다. 끄면 빛을 받는 diffuse 로 낸다
        """
        path = f"{self._root}/Looks/{name}"
        material = UsdShade.Material.Define(stage, path)
        self._preview_shader(stage, material, path, color, opacity, glow)
        self._mdl_shader(stage, material, path, color, opacity, emission, glow)
        return material

    @staticmethod
    def _preview_shader(stage, material, path: str, color, opacity: float,
                        glow: bool = True) -> None:
        """UsdPreviewSurface 쪽. 색은 발광으로, glow 를 끄면 diffuse 로 낸다"""
        shader = UsdShade.Shader.Define(stage, path + "/shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        dark = Gf.Vec3f(0.0, 0.0, 0.0)
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            dark if glow else Gf.Vec3f(*color))
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*color) if glow else dark)
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(0.0, 0.0, 0.0))
        shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),
                                                       "surface")

    @staticmethod
    def _mdl_shader(stage, material, path: str, color, opacity: float,
                    emission: float, glow: bool = True) -> None:
        """OmniPBR 쪽. RTX 가 이걸 쓴다"""
        shader = UsdShade.Shader.Define(stage, path + "/mdl")
        shader.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
        shader.SetSourceAssetSubIdentifier("OmniPBR", "mdl")

        def put(name, type_name, value):
            """셰이더 입력 하나를 만든다"""
            shader.CreateInput(name, type_name).Set(value)

        dark = Gf.Vec3f(0.0, 0.0, 0.0)
        put("diffuse_color_constant", Sdf.ValueTypeNames.Color3f,
            dark if glow else Gf.Vec3f(*color))
        put("emissive_color", Sdf.ValueTypeNames.Color3f, Gf.Vec3f(*color))
        put("emissive_intensity", Sdf.ValueTypeNames.Float,
            emission if glow else 0.0)
        put("enable_emission", Sdf.ValueTypeNames.Bool, bool(glow))
        put("enable_opacity", Sdf.ValueTypeNames.Bool, True)
        put("opacity_constant", Sdf.ValueTypeNames.Float, opacity)
        put("reflection_roughness_constant", Sdf.ValueTypeNames.Float, 1.0)
        put("metallic_constant", Sdf.ValueTypeNames.Float, 0.0)
        put("specular_level", Sdf.ValueTypeNames.Float, 0.0)
        material.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out")
