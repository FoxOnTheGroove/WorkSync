import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateOverlay"]

FRAME_ID = "ebs_simulate_overlay"

# 뷰포트 폰트에 한글이 없어 빈칸으로 나온다. 영문만 쓴다.
CAN    = "EBS INSTALL AVAILABLE"
CANNOT = "EBS INSTALL BLOCKED"
CLEAR  = "no collision"

INNER = "internal clash"          # 무엇에 막혔는지, 한 줄에 하나
FACE_ORDER = ("left", "right", "ceiling")
NAMELESS = "-"

CLASH = "clash"                   # 면 패널: 막혔을 때
GAP   = "clearance"               # 비었을 때, 선 위
TIGHT = "interference"            # 안 닿았는데 최소 여유 미달일 때, 같은 자리
LEAST = "min {0:.3f} m"           # 지켜야 하는 최소 여유, 거리 아래

# 선은 UI 가 아니라 씬에 그린다 (show_markers 의 _gap_line). 그래서 글자는 그
# 선의 중점 양쪽으로 갈라 붙는다 — 한 판으로 두면 뒷판이 선을 가린다.
ABOVE, BELOW, LEFT, RIGHT, MIDDLE = "above", "below", "left", "right", "middle"
LINE_ROOM = 6                     # 선이 지나갈 자리, 판 사이 여백

# 천장은 화면에서 위아래로 벌어지니 글자가 선 양옆으로 간다. 좌우 면은 가로로
# 벌어지니 위아래로 간다.
SIDE_BY_SIDE = ("ceiling",)

# 판 자체가 색을 지고, 글자는 흰색이다. 어두운 판에 색 글자를 쓰면 플랜트를
# 배경으로 읽기 힘들다.
COLOR_CAN    = 0xFF00B4E6      # 짙은 황색 (ABGR)
COLOR_CANNOT = 0xFF2626E6      # 빨강
COLOR_TEXT   = 0xFFFFFFFF
TEXT_SIZE    = 22
DETAIL_SIZE  = 15
FACE_SIZE    = 15
PAD_X, PAD_Y = 10, 5


class EbsSimulateOverlay:
    _instances = {}


    @classmethod
    def show(cls, vp_name: str = None):
        overlay = cls._get(vp_name)
        if overlay is not None:
            overlay.refresh()
        return overlay

    @classmethod
    def hide(cls, vp_name: str = None):
        for name, overlay in list(cls._instances.items()):
            if vp_name in (None, name):
                overlay.clear()

    @classmethod
    def destroy(cls, vp_name: str = None):
        for name, overlay in list(cls._instances.items()):
            if vp_name in (None, name):
                overlay._destroy()
                cls._instances.pop(name, None)

    @classmethod
    def _get(cls, vp_name: str = None):
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

    @staticmethod
    def _window(vp_name: str = None):
        try:
            import omni.kit.viewport.utility as vp_util
        except Exception as e:
            print(f"[ebs] viewport utility unavailable: {e}")
            return None

        tried = []

        def ask(name, call):
            helper = getattr(vp_util, name, None)
            if helper is None:
                tried.append(f"{name}: not in this build")
                return None
            try:
                got = call(helper)
            except Exception as e:
                tried.append(f"{name}: {e}")
                return None
            if got is None:
                tried.append(f"{name}: gave nothing back")
            return got

        window = None
        if vp_name:
            window = ask("get_viewport_window_by_name", lambda f: f(vp_name))
        if window is None:
            pair = ask("get_active_viewport_and_window", lambda f: f())
            window = pair[1] if pair else None
        if window is None:
            window = ask("get_active_viewport_window", lambda f: f())
        if window is None:
            try:
                window = ui.Workspace.get_window(vp_name or "Viewport")
            except Exception as e:
                tried.append(f"Workspace.get_window: {e}")
        if window is None:
            print("[ebs] no viewport window to draw the overlay on -- "
                  + "; ".join(tried))
        return window


    def __init__(self, vp_name):
        self._vp_name = vp_name
        self._window = None
        self._api = None
        self._frame = None
        self._stack = None
        self._marks = []
        self._follow = None

    def _build(self, window) -> bool:
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

    def refresh(self) -> bool:
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
        self._place()
        return self._start()


    def _floating(self, at, fill, ground, anchor=MIDDLE):
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
        self._marks.append((placer, panel, tuple(at), anchor))

    def _verdict_panel(self, said: dict) -> None:
        ok = bool(said.get("placeable"))

        def fill():
            with ui.VStack(spacing=1, style={"margin_width": PAD_X,
                                             "margin_height": PAD_Y}):
                ui.Label(CAN if ok else CANNOT, height=0,
                         alignment=ui.Alignment.CENTER,
                         style={"font_size": TEXT_SIZE, "color": COLOR_TEXT})
                for line in self._why(said):
                    ui.Label(line, height=0, alignment=ui.Alignment.CENTER,
                             style={"font_size": DETAIL_SIZE,
                                    "color": COLOR_TEXT})

        self._floating(said.get("centre"), fill,
                       COLOR_CAN if ok else COLOR_CANNOT)

    def _face_panel(self, mark: dict) -> None:
        state = mark.get("state")
        clash = state == "clash"
        ground = COLOR_CAN if state == "clear" else COLOR_CANNOT
        gap = mark.get("distance")
        least = mark.get("min_gap")
        name = mark.get("name") or ""

        def block(lines):
            def fill():
                with ui.VStack(spacing=1, style={"margin_width": PAD_X,
                                                 "margin_height": PAD_Y}):
                    for text in lines:
                        ui.Label(text, height=0, alignment=ui.Alignment.CENTER,
                                 style={"font_size": FACE_SIZE,
                                        "color": COLOR_TEXT})
            return fill

        at = mark.get("at")
        if clash:
            self._floating(at, block([CLASH]), ground)
            return
        first, second = ((LEFT, RIGHT) if mark.get("face") in SIDE_BY_SIDE
                         else (ABOVE, BELOW))
        self._floating(at, block([TIGHT if state == "tight" else GAP]),
                       ground, first)
        # 잰 것이 없으면 아래 판도 없다. 재지 못했다는 것은 reach 밖이라는 뜻이고,
        # 거기에 대고 쓸 거리도 상대도 없다.
        if gap is None:
            return
        told = [f"{gap:.3f} m"]
        if least:
            told.append(LEAST.format(least))
        if name:
            told.append(name)
        self._floating(at, block(told), ground, second)

    @staticmethod
    def _why(said: dict) -> list:
        told = [INNER] if said.get("inside") else []
        blocked = {found["face"]: found.get("name") or NAMELESS
                   for found in (said.get("faces") or ())}
        told += [f"{face} : {blocked[face]}"
                 for face in FACE_ORDER if face in blocked]
        return told or [CLEAR]


    def _start(self) -> bool:
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
        if not self._marks:
            return
        try:
            width = self._frame.computed_width
            height = self._frame.computed_height
            for placer, panel, at, anchor in self._marks:
                spot = self._to_screen(at)
                if spot is None:
                    panel.visible = False
                    continue
                panel_w, panel_h = panel.computed_width, panel.computed_height
                x, y = spot[0] - panel_w * 0.5, spot[1] - panel_h * 0.5
                if anchor == ABOVE:
                    y = spot[1] - panel_h - LINE_ROOM
                elif anchor == BELOW:
                    y = spot[1] + LINE_ROOM
                elif anchor == LEFT:
                    x = spot[0] - panel_w - LINE_ROOM
                elif anchor == RIGHT:
                    x = spot[0] + LINE_ROOM
                placer.offset_x = min(max(x, 0.0), max(width - panel_w, 0.0))
                placer.offset_y = min(max(y, 0.0), max(height - panel_h, 0.0))
                panel.visible = True
        except Exception as e:
            print(f"[ebs] could not place the overlay: {e}")
            self.clear()

    def _to_screen(self, point):
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
        self._follow = None
        self._marks = []
        if self._stack is not None:
            try:
                self._stack.clear()
            except Exception:
                pass

    def _destroy(self) -> None:
        self.clear()
        self._stack = None
        self._frame = None
        self._api = None
        self._window = None
