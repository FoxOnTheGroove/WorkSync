"""Viewport overlay: the one line that says whether the EBS can stand here.

Everything it draws comes from the public API, and everything about how it
looks is decided here -- the simulation side hands over a point and a yes or
no and does not know what a viewport is.

Text in a viewport is not scene description: a USD prim cannot hold a line of
letters. It is omni.ui.scene that draws it, over the render, always facing the
camera, and the viewport keeps it lined up with whatever the camera does.
"""

from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateOverlay"]

FRAME_ID = "ebs_simulate_overlay"

CAN    = "이 위치에 EBS 세울 수 있음"
CANNOT = "이 위치에 EBS 세울 수 없음"

COLOR_CAN    = 0xFF00B4E6      # 짙은 황색 (ABGR, omni.ui 순서)
COLOR_CANNOT = 0xFF2626E6      # 빨강
TEXT_SIZE    = 22


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
        """Take the drawing away, keep the frame."""
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
            from omni.kit.viewport.utility import (get_active_viewport_window,
                                                   get_viewport_window_by_name)
        except ImportError:
            print("[ebs] no viewport to draw the overlay on (omni.kit not here)")
            return None
        try:
            return (get_viewport_window_by_name(vp_name) if vp_name
                    else get_active_viewport_window())
        except Exception as e:
            print(f"[ebs] could not find the viewport window: {e}")
            return None

    # -- one viewport ---------------------------------------------------------

    def __init__(self, vp_name):
        self._vp_name = vp_name
        self._window = None
        self._view = None

    def _build(self, window) -> bool:
        try:
            from omni.ui import scene as sc
        except ImportError:
            print("[ebs] omni.ui.scene is not available, no overlay")
            return False
        try:
            with window.get_frame(FRAME_ID):
                self._view = sc.SceneView()
            # The viewport drives it from here: the camera moves, the scene
            # view follows, and we never touch a projection matrix ourselves.
            window.viewport_api.add_scene_view(self._view)
        except Exception as e:
            print(f"[ebs] could not put the overlay on the viewport: {e}")
            self._view = None
            return False
        self._window = window
        return True

    def refresh(self) -> bool:
        said = EbsSimulateService.get_verdict()
        self.clear()
        if not said or self._view is None:
            return False
        return self._draw(said)

    def _draw(self, said: dict) -> bool:
        from omni.ui import scene as sc
        import omni.ui as ui
        ok = bool(said.get("placeable"))
        try:
            with self._view.scene:
                with sc.Transform(transform=sc.Matrix44.get_translation_matrix(
                        *said["centre"])):
                    sc.Label(CAN if ok else CANNOT,
                             alignment=ui.Alignment.CENTER,
                             color=COLOR_CAN if ok else COLOR_CANNOT,
                             size=TEXT_SIZE)
        except Exception as e:
            print(f"[ebs] could not draw the overlay: {e}")
            return False
        return True

    def clear(self) -> None:
        if self._view is not None:
            try:
                self._view.scene.clear()
            except Exception:
                pass

    def _destroy(self) -> None:
        self.clear()
        if self._view is not None and self._window is not None:
            try:
                self._window.viewport_api.remove_scene_view(self._view)
            except Exception:
                pass
        self._view = None
        self._window = None
