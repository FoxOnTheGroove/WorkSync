import math
import time

import omni.ui as ui
from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf

from .ebs_simulate_camera import viewport_window
from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateOverlay", "EbsSimulateMarks"]

STATE_CLEAR = "clear"
STATE_TIGHT = "tight"
STATE_CLASH = "clash"

FRAME_ID = "ebs_simulate_overlay"

CANNOT = "이 위치에 EBS 장비를 세울 수 없습니다."
INNER  = "내부 장비와 충돌"

CLASH = "충돌"
GAP   = "여유"
TIGHT = "간섭"
SPAN  = "{0:.2f}M"
LEAST = "(최소간격 : {0:.2f}M)"

ABOVE, BELOW, LEFT, RIGHT, MIDDLE = "above", "below", "left", "right", "middle"
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
PAD_X, PAD_Y = 1, 1

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

    def _build(self, window) -> bool:
        """뷰포트에 프레임을 걸고 투영에 쓸 viewport api 를 잡는다"""
        try:
            self._frame = window.get_frame(FRAME_ID)
            with self._frame:
                self._stack = ui.ZStack()
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
        return True

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
        except Exception as e:
            print(f"[ebs] could not build the overlay: {e}")
            self.clear()
            return False
        if not place:
            return True
        self._place()
        return self._start()


    def _floating(self, at, fill, ground, anchor=MIDDLE, step: int = 0,
                  share: int = 1, group=None):
        """월드 좌표에 매달 판 하나. 같은 group 끼리 나란히 세운다"""
        if at is None:
            return
        placer = ui.Placer(draggable=False, offset_x=0, offset_y=0)
        with placer:
            panel = ui.ZStack(width=0, height=0)
            with panel:
                ui.Rectangle(style={"background_color": ground,
                                    "border_radius": 4})
                fill()
        panel.visible = False
        self._marks.append((placer, panel, tuple(at), anchor, step,
                            share, group))

    def _verdict_panel(self, said: dict) -> None:
        """못 세울 때만 한 줄. 내부 간섭이면 그 아래에 한 줄 더. 세울 수 있으면 없다"""
        if said.get("placeable"):
            return

        def one(text):
            """_floating 에 넘길 그리기 함수"""
            def fill():
                """판 속 글줄을 채운다"""
                with ui.VStack(spacing=0, style={"margin_width": PAD_X,
                                                 "margin_height": PAD_Y}):
                    ui.Label(text, height=0, alignment=ui.Alignment.CENTER,
                             style={"font_size": TEXT_SIZE, "color": COLOR_TEXT})
            return fill

        self._floating(said.get("centre"), one(CANNOT), COLOR_CANNOT)
        if said.get("inside"):
            self._floating(said.get("inside_at"), one(INNER), COLOR_CANNOT)

    def _face_panel(self, mark: dict) -> None:
        """한쪽에 상태, 다른 쪽에 거리와 최소 여유. 막힌 면도 똑같이 붙인다"""
        state = mark.get("state")
        ground = COLOR_CAN if state == STATE_CLEAR else COLOR_CANNOT
        ink = COLOR_INK if state == STATE_CLEAR else COLOR_TEXT
        gap = mark.get("distance")
        least = mark.get("min_gap")

        def block(lines):
            """_floating 에 넘길 그리기 함수"""
            def fill():
                """판 속 글줄을 채운다"""
                with ui.VStack(spacing=0, style={"margin_width": PAD_X,
                                                 "margin_height": PAD_Y}):
                    for text in lines:
                        ui.Label(text, height=0, alignment=ui.Alignment.CENTER,
                                 style={"font_size": FACE_SIZE, "color": ink})
            return fill

        at = mark.get("at")
        first, second = ((LEFT, RIGHT) if mark.get("face") in SIDE_BY_SIDE
                         else (ABOVE, BELOW))
        word = (CLASH if state == STATE_CLASH else
                TIGHT if state == STATE_TIGHT else GAP)
        face = mark.get("face")
        self._floating(at, block([word]), ground, first, group=(face, first))
        if gap is None:
            return
        share = 2 if least else 1
        self._floating(at, block([SPAN.format(gap)]), ground, second, 0, share,
                       (face, second))
        if least:
            self._floating(at, block([LEAST.format(least)]), ground, second,
                           1, share, (face, second))

    def _start(self) -> bool:
        """매 프레임 _place 를 부르도록 Kit 업데이트에 붙는다"""
        try:
            import omni.kit.app
            self._follow = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(lambda e: self._place(),
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
            for _, panel, _, _, _, _, group in self._marks:
                if group is not None:
                    widest[group] = max(widest.get(group, 0.0),
                                        panel.computed_width)
            for placer, panel, at, anchor, step, share, group in self._marks:
                spot = self._to_screen(at)
                if spot is None:
                    panel.visible = False
                    continue
                panel_w, panel_h = panel.computed_width, panel.computed_height
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


    def clear(self) -> None:
        """그린 판과 카메라 추적을 놓는다"""
        self._follow = None
        self._marks = []
        if self._stack is not None:
            try:
                self._stack.clear()
            except Exception:
                pass

    def _destroy(self) -> None:
        """프레임과 api 참조까지 전부 놓는다"""
        self.clear()
        self._stack = None
        self._frame = None
        self._api = None
        self._window = None


class EbsSimulateMarks:
    """collide 가 씬에 그리는 것 전부. 3면 판, 여유 선, 화살촉, 안내선, 충돌 상자"""

    def __init__(self, stage_of, root: str):
        """스테이지를 주는 함수와, 그린 것을 담을 뿌리 경로"""
        self._stage_of = stage_of
        self._root = root
        self._pulse = None
        self._pulse_inputs: tuple = ()
        self._pulse_from: float = 0.0

    def draw(self, sheets: list, marks: list = None, boxes: list = None) -> int:
        """판정 한 벌을 씬에 그린다. 그리기 전에 먼저 지운다"""
        stage = self._stage_of()
        if stage is None:
            return 0
        self.clear()
        drawn = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, self._root)
            drawn += self._sheets(stage, sheets)
            drawn += self._gap_lines(stage, marks)
            drawn += self._clash_boxes(stage, boxes)
        return drawn

    def clear(self) -> None:
        """뿌리를 통째로 지우고 깜박임도 놓는다"""
        self._stop_pulse()
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
        """걸린 조각마다 빨간 반투명 상자 하나. 다 그리면 깜박이기 시작"""
        if not boxes:
            return 0
        material = self._material(stage, "clash", COLOR_CLASH,
                                  CLASH_OPACITY, BLOCKED_EMISSION)
        pad = self._clash_pad(stage)
        drawn = 0
        for at, (lo, hi) in enumerate(boxes):
            middle = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
            half = [(hi[i] - lo[i]) * 0.5 + pad for i in range(3)]
            block = UsdGeom.Cube.Define(stage, f"{self._root}/clash_{at}")
            block.CreateSizeAttr(2.0)
            block.CreateExtentAttr([Gf.Vec3f(-1.0, -1.0, -1.0),
                                    Gf.Vec3f(1.0, 1.0, 1.0)])
            block.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*COLOR_CLASH)]))
            block.CreateDisplayOpacityAttr(Vt.FloatArray([CLASH_OPACITY]))
            shape = UsdGeom.Xformable(block)
            shape.AddTranslateOp().Set(Gf.Vec3d(*middle))
            shape.AddScaleOp().Set(Gf.Vec3f(*half))
            UsdShade.MaterialBindingAPI(block.GetPrim()).Bind(material)
            drawn += 1
        if drawn:
            self._start_pulse(stage)
        return drawn

    @staticmethod
    def _clash_pad(stage) -> float:
        """CLASH_PAD 는 m 다. 씬 단위로 바꿔 준다"""
        try:
            per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        except Exception:
            per_unit = 1.0
        return CLASH_PAD / (per_unit or 1.0)


    def _start_pulse(self, stage) -> bool:
        """내부 충돌 상자를 CLASH_PULSE 주기로 깜박인다. clear 가 멈춘다"""
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
    def _gap_shaft(start, end):
        """선은 원뿔 중점에서 시작한다. 뭉툭한 끝이 뾰족한 끝을 먹지 않게"""
        along = Gf.Vec3d(*[end[i] - start[i] for i in range(3)])
        span = along.GetLength()
        if span <= GAP_HEAD_HIGH:
            return start, end
        step = along.GetNormalized() * (GAP_HEAD_HIGH * 0.5)
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
    def _gap_head(stage, path: str, tip, back, material, colour) -> bool:
        """tip 을 향해 뾰족한 원뿔 하나. tip 에서 back 쪽으로 뒤가 눕는다"""
        along = Gf.Vec3d(*[tip[i] - back[i] for i in range(3)])
        if along.GetLength() <= 1e-9:
            return False
        along = along.GetNormalized()
        cone = UsdGeom.Cone.Define(stage, path)
        cone.CreateAxisAttr(UsdGeom.Tokens.z)
        cone.CreateHeightAttr(GAP_HEAD_HIGH)
        cone.CreateRadiusAttr(GAP_HEAD_WIDE)
        cone.CreateExtentAttr(Vt.Vec3fArray([
            Gf.Vec3f(-GAP_HEAD_WIDE, -GAP_HEAD_WIDE, -GAP_HEAD_HIGH / 2.0),
            Gf.Vec3f(GAP_HEAD_WIDE, GAP_HEAD_WIDE, GAP_HEAD_HIGH / 2.0)]))
        cone.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*colour)]))
        matrix = Gf.Matrix4d(1.0)
        matrix.SetRotate(Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0), along))
        matrix.SetTranslateOnly(
            Gf.Vec3d(*[tip[i] - along[i] * GAP_HEAD_HIGH * 0.5
                       for i in range(3)]))
        UsdGeom.Xformable(cone).AddTransformOp().Set(matrix)
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
        UsdGeom.Xformable(rod).AddTransformOp().Set(matrix)
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
                  emission: float = MARKER_EMISSION):
        """마커용 머티리얼. preview 와 MDL 두 셰이더를 단다"""
        path = f"{self._root}/Looks/{name}"
        material = UsdShade.Material.Define(stage, path)
        self._preview_shader(stage, material, path, color, opacity)
        self._mdl_shader(stage, material, path, color, opacity, emission)
        return material

    @staticmethod
    def _preview_shader(stage, material, path: str, color, opacity: float) -> None:
        """UsdPreviewSurface 쪽. 색은 발광으로 낸다"""
        shader = UsdShade.Shader.Define(stage, path + "/shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(0.0, 0.0, 0.0))
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*color))
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
                    emission: float) -> None:
        """OmniPBR 쪽. RTX 가 이걸 쓴다"""
        shader = UsdShade.Shader.Define(stage, path + "/mdl")
        shader.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
        shader.SetSourceAssetSubIdentifier("OmniPBR", "mdl")

        def put(name, type_name, value):
            """셰이더 입력 하나를 만든다"""
            shader.CreateInput(name, type_name).Set(value)

        put("diffuse_color_constant", Sdf.ValueTypeNames.Color3f,
            Gf.Vec3f(0.0, 0.0, 0.0))
        put("emissive_color", Sdf.ValueTypeNames.Color3f, Gf.Vec3f(*color))
        put("emissive_intensity", Sdf.ValueTypeNames.Float, emission)
        put("enable_emission", Sdf.ValueTypeNames.Bool, True)
        put("enable_opacity", Sdf.ValueTypeNames.Bool, True)
        put("opacity_constant", Sdf.ValueTypeNames.Float, opacity)
        put("reflection_roughness_constant", Sdf.ValueTypeNames.Float, 1.0)
        put("metallic_constant", Sdf.ValueTypeNames.Float, 0.0)
        put("specular_level", Sdf.ValueTypeNames.Float, 0.0)
        material.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out")
