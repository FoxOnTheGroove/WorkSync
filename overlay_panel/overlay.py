from omni.kit.viewport.utility import get_active_viewport_window
import omni.ui.scene as sc
import omni.ui as ui

LABEL_OFFSET_Y = 0.5
MARKER_SIZE    = 10
LINE_THICKNESS = 2
LINE_COLOR     = 0xFFFFFFFF


class ColorpickOverlay:
    _instance = None

    @classmethod
    def get(cls) -> "ColorpickOverlay":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def on(cls, prim_name: str, pos3d: tuple, **kwargs):
        cls.get()._update(prim_name, pos3d)

    @classmethod
    def clear(cls):
        if cls._instance:
            cls._instance._clear()

    @classmethod
    def destroy(cls):
        if cls._instance:
            cls._instance._destroy()
            cls._instance = None

    # ------------------------------------------------------------------

    def __init__(self):
        vp_window = get_active_viewport_window()
        self._vp_frame = vp_window.get_frame("worksync_colorpick_overlay")
        with self._vp_frame:
            self._scene_view = sc.SceneView()
        self._hit_pos   = None
        self._prim_name = None

    def _update(self, prim_name: str, pos3d: tuple):
        self._prim_name = prim_name
        self._hit_pos   = pos3d
        self._rebuild_scene()

    def _clear(self):
        self._hit_pos   = None
        self._prim_name = None
        self._rebuild_scene()

    def _rebuild_scene(self):
        self._scene_view.scene.clear()
        if self._hit_pos is None:
            return

        x, y, z       = self._hit_pos
        lx, ly, lz    = x, y + LABEL_OFFSET_Y, z

        with self._scene_view.scene:
            sc.Points(
                [[x, y, z]],
                sizes=[MARKER_SIZE],
                colors=[LINE_COLOR],
            )
            sc.Line(
                [x, y, z],
                [lx, ly, lz],
                color=LINE_COLOR,
                thickness=LINE_THICKNESS,
            )
            with sc.Transform(
                transform=sc.Matrix44.get_translation_matrix(lx, ly, lz)
            ):
                sc.Label(
                    self._prim_name,
                    alignment=ui.Alignment.CENTER_BOTTOM,
                )

    def _destroy(self):
        if self._vp_frame:
            self._vp_frame = None
        self._scene_view = None
