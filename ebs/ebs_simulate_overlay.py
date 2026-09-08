import omni.ui as ui

from .ebs_simulate_camera import viewport_window
from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateOverlay"]

FRAME_ID = "ebs_simulate_overlay"

CAN    = "EBS INSTALL AVAILABLE"
CANNOT = "EBS INSTALL BLOCKED"
INNER  = "internal clash"

CLASH = "clash"
GAP   = "clearance"
TIGHT = "interference"
SPAN  = "{0:.2f}M"
LEAST = "(min gap : {0:.2f}M)"

ABOVE, BELOW, LEFT, RIGHT, MIDDLE = "above", "below", "left", "right", "middle"
LINE_ROOM = 6
ROOM_HEADS = 1.5
PANEL_GAP = 0.1
DEEP_WIDE = 2

SIDE_BY_SIDE = ("ceiling",)

COLOR_CAN    = 0xFF9AE7FF
COLOR_CANNOT = 0xFF1B39FC
COLOR_TEXT   = 0xFFFFFFFF
COLOR_INK    = 0xFF000000
TEXT_SIZE    = 19
FACE_SIZE    = 17
PAD_X, PAD_Y = 1, 1


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
        """뷰포트 하나에 붙는 오버레이 한 벌"""
        self._vp_name = vp_name
        self._window = None
        self._api = None
        self._frame = None
        self._stack = None
        self._marks = []
        self._threads = []
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
                    self._deep_line(mark)
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
        """세울 수 있나 없나 한 줄. 내부 간섭이면 그 아래에 한 줄 더"""
        ok = bool(said.get("placeable"))
        ink = COLOR_INK if ok else COLOR_TEXT

        def one(text, colour, size):
            """한 줄짜리 판을 그리는 함수를 만든다"""
            def fill():
                """판 속 글줄을 채운다"""
                with ui.VStack(spacing=0, style={"margin_width": PAD_X,
                                                 "margin_height": PAD_Y}):
                    ui.Label(text, height=0, alignment=ui.Alignment.CENTER,
                             style={"font_size": size, "color": colour})
            return fill

        self._floating(said.get("centre"), one(CAN if ok else CANNOT, ink,
                                               TEXT_SIZE),
                       COLOR_CAN if ok else COLOR_CANNOT)
        if said.get("inside"):
            self._floating(said.get("inside_at"),
                           one(INNER, COLOR_TEXT, TEXT_SIZE), COLOR_CANNOT)

    def _face_panel(self, mark: dict) -> None:
        """한쪽에 상태, 다른 쪽에 거리와 최소 여유. 막힌 면도 똑같이 붙인다"""
        state = mark.get("state")
        ground = COLOR_CAN if state == "clear" else COLOR_CANNOT
        ink = COLOR_INK if state == "clear" else COLOR_TEXT
        gap = mark.get("distance")
        least = mark.get("min_gap")

        def block(lines):
            """글줄 목록을 그리는 함수를 만든다"""
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
        word = (CLASH if state == "clash" else
                TIGHT if state == "tight" else GAP)
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

    def _deep_line(self, mark: dict) -> None:
        """파고든 면의 선은 메시에 묻히니 화면 위에 곧장 다시 긋는다"""
        gap = mark.get("distance")
        if gap is None or gap >= 0.0:
            return
        start, end = mark.get("from"), mark.get("to")
        if not start or not end:
            return
        try:
            ends = []
            for at in (start, end):
                placer = ui.Placer(draggable=False, offset_x=0, offset_y=0)
                with placer:
                    dot = ui.Rectangle(width=1, height=1,
                                       style={"background_color": 0x00000000})
                ends.append((placer, dot, tuple(at)))
            look = {"color": COLOR_CANNOT, "border_width": DEEP_WIDE}
            try:
                thread = ui.FreeBezierCurve(
                    ends[0][1], ends[1][1], style=look,
                    start_tangent_width=ui.Pixel(0),
                    end_tangent_width=ui.Pixel(0))
            except Exception:
                thread = ui.FreeBezierCurve(ends[0][1], ends[1][1], style=look)
        except Exception as e:
            print(f"[ebs] could not draw the buried gap line on screen: {e}")
            return
        thread.visible = False
        self._threads.append((thread, ends))

    def _draw_threads(self) -> None:
        """화면 위에 그은 선의 두 끝을 이번 프레임 자리로 옮긴다"""
        for thread, ends in self._threads:
            spots = [self._to_screen(at) for _, _, at in ends]
            if any(spot is None for spot in spots):
                thread.visible = False
                continue
            for (placer, _, _), spot in zip(ends, spots):
                placer.offset_x = spot[0]
                placer.offset_y = spot[1]
            thread.visible = True

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
        if not self._marks and not self._threads:
            return
        try:
            self._draw_threads()
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
            from pxr import Gf
            from .ebs_simulate import GAP_HEAD_WIDE
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
        from pxr import Gf
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
        self._threads = []
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
