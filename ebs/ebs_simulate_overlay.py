"""Viewport overlay: the one line that says whether the EBS can stand here.

Everything it draws comes from the public API, and everything about how it
looks is decided here -- the simulation side hands over a point and a yes or
no and does not know what a viewport is.

Text in a viewport is not scene description: a USD prim cannot hold a line of
letters. So this is an ordinary omni.ui panel sitting in the viewport window's
own frame, and the world point is projected to where it lands on screen every
frame. That is more code than asking omni.ui.scene for a billboard, and it is
plain omni.ui the whole way down, which is the part that can be relied on.
"""

import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateOverlay"]

FRAME_ID = "ebs_simulate_overlay"

# 한글은 뷰포트 폰트에 없어서 빈칸으로 나온다. 영문만 쓴다.
CAN    = "EBS INSTALL AVAILABLE"
CANNOT = "EBS INSTALL BLOCKED"
CLEAR  = "no collision"

# 무엇에 막혔는지. 없으면 위의 CLEAR.
INNER = "INNER: through the equipment"
FACE_WORDS = {
    "left":    "LEFT: blocked",
    "right":   "RIGHT: blocked",
    "ceiling": "CEILING: blocked",
}
FACE_ORDER = ("left", "right", "ceiling")

COLOR_CAN    = 0xFF00B4E6      # 짙은 황색 (ABGR)
COLOR_CANNOT = 0xFF2626E6      # 빨강
COLOR_DETAIL = 0xFFC8C8C8      # 두 번째 줄. 판정이 아니라 사유라 눈에 덜 띈다
COLOR_PANEL  = 0xC0141414      # 뒤에 깔리는 판. 글자가 씬에 묻히지 않게
TEXT_SIZE    = 22
DETAIL_SIZE  = 15
PAD_X, PAD_Y = 10, 5


class EbsSimulateOverlay:
    """One overlay per viewport window, kept by window name."""

    _instances = {}

    # -- what the panel calls -------------------------------------------------

    @classmethod
    def show(cls, vp_name: str = None):
        """Draw the verdict from the last collide. Nothing to say -> nothing."""
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
        """The viewport window, however this build hands one out.

        Each helper is looked up on its own. Importing them together meant one
        name missing took the whole import down, and what came back was
        "omni.kit is not here" about a Kit that was plainly running.
        """
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
            # Older builds only hand out the API, and it is the window that
            # owns a frame. Ask the workspace for it by name.
            try:
                window = ui.Workspace.get_window(vp_name or "Viewport")
            except Exception as e:
                tried.append(f"Workspace.get_window: {e}")
        if window is None:
            print("[ebs] no viewport window to draw the overlay on -- "
                  + "; ".join(tried))
        return window

    # -- one viewport ---------------------------------------------------------

    def __init__(self, vp_name):
        self._vp_name = vp_name
        self._window = None
        self._api = None         # the viewport, for turning world into screen
        self._frame = None
        self._placer = None
        self._panel = None
        self._label = None
        self._detail = None
        self._follow = None      # the update subscription, only while showing
        self._at = None          # the world point being followed

    def _build(self, window) -> bool:
        try:
            self._frame = window.get_frame(FRAME_ID)
            with self._frame:
                # The placer is what moves; everything inside it is laid out
                # once and then just carried about.
                self._placer = ui.Placer(draggable=False, offset_x=0, offset_y=0)
                with self._placer:
                    self._panel = ui.ZStack(width=0, height=0)
                    with self._panel:
                        ui.Rectangle(style={"background_color": COLOR_PANEL,
                                            "border_radius": 4})
                        with ui.VStack(spacing=1,
                                       style={"margin_width": PAD_X,
                                              "margin_height": PAD_Y}):
                            self._label = ui.Label(
                                "", height=0, alignment=ui.Alignment.CENTER,
                                style={"font_size": TEXT_SIZE,
                                       "color": COLOR_CAN})
                            self._detail = ui.Label(
                                "", height=0, alignment=ui.Alignment.CENTER,
                                style={"font_size": DETAIL_SIZE,
                                       "color": COLOR_DETAIL})
            self._panel.visible = False
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
        if not said or self._label is None:
            return False
        ok = bool(said.get("placeable"))
        self._label.text = CAN if ok else CANNOT
        self._label.style = {"font_size": TEXT_SIZE,
                             "color": COLOR_CAN if ok else COLOR_CANNOT}
        self._detail.text = self._why(said)
        self._at = said.get("centre")
        if self._at is None:
            return False
        self._place()                    # so it is there before the next frame
        return self._start()

    @staticmethod
    def _why(said: dict) -> str:
        """What it is caught on, in the order it is read: inside first, then
        the faces left to right and the ceiling last."""
        told = [INNER] if said.get("inside") else []
        blocked = set(said.get("faces") or ())
        told += [FACE_WORDS[face] for face in FACE_ORDER if face in blocked]
        return "   ".join(told) if told else CLEAR

    # -- following the camera -------------------------------------------------

    def _start(self) -> bool:
        """Move with the camera. The app's update stream is asked rather than
        the viewport's, because every Kit has one."""
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
        if self._at is None or self._panel is None:
            return
        try:
            spot = self._to_screen(self._at)
        except Exception as e:
            print(f"[ebs] could not place the overlay: {e}")
            self.clear()
            return
        if spot is None:
            self._panel.visible = False        # behind the camera, or off screen
            return
        x, y = spot
        # The panel is laid out around the point, not from its corner.
        self._placer.offset_x = x - self._panel.computed_width * 0.5
        self._placer.offset_y = y - self._panel.computed_height * 0.5
        self._panel.visible = True

    def _to_screen(self, point):
        """Where a world point lands in the frame, in UI units. None if it is
        behind the camera or outside the view."""
        from pxr import Gf
        api = self._api
        at = Gf.Vec3d(*point)
        view = api.view
        if view.Transform(at)[2] >= 0.0:
            return None                        # behind the lens
        try:
            clip = api.world_to_ndc
        except AttributeError:
            clip = view * api.projection
        ndc = clip.Transform(at)               # Transform divides by w for us
        if not (-1.0 <= ndc[0] <= 1.0 and -1.0 <= ndc[1] <= 1.0):
            return None
        # The frame's own size, so this is in the same units the placer wants
        # and the display's scaling never enters into it.
        width = self._frame.computed_width
        height = self._frame.computed_height
        if not width or not height:
            return None
        return ((ndc[0] * 0.5 + 0.5) * width,
                (0.5 - ndc[1] * 0.5) * height)

    # -- teardown -------------------------------------------------------------

    def clear(self) -> None:
        self._follow = None       # dropping the handle ends the subscription
        self._at = None
        if self._panel is not None:
            self._panel.visible = False

    def _destroy(self) -> None:
        self.clear()
        self._label = None
        self._detail = None
        self._panel = None
        self._placer = None
        self._frame = None
        self._api = None
        self._window = None
