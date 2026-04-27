import omni.ui.scene as sc
import omni.ui as ui

LABEL_OFFSET_Y = 0.5
MARKER_SIZE    = 10
LINE_THICKNESS = 2
LINE_COLOR     = 0xFFFFFFFF


def _find_viewport_window(name: str):
    """뷰포트 이름으로 ViewportWindow 반환. 없으면 None."""
    try:
        # Kit 106+ : get_viewport_window_instances() 사용
        from omni.kit.viewport.utility import get_viewport_window_instances
        for w in get_viewport_window_instances():
            if getattr(w, "name", None) == name or getattr(w, "title", None) == name:
                return w
    except Exception:
        pass

    # 폴백: omni.ui Workspace로 이름 검색
    win = ui.Workspace.get_window(name)
    if win is not None:
        return win

    return None


class ColorpickOverlay:
    # viewport_name -> ColorpickOverlay 인스턴스
    _instances: dict = {}

    # ------------------------------------------------------------------
    # classmethod API
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, viewport_name: str) -> "ColorpickOverlay":
        if viewport_name not in cls._instances:
            cls._instances[viewport_name] = cls(viewport_name)
        return cls._instances[viewport_name]

    @classmethod
    def on(cls, viewport_name: str, prim_name: str, pos3d: tuple, **kwargs):
        cls.get(viewport_name)._update(prim_name, pos3d)

    @classmethod
    def clear(cls, viewport_name: str = None):
        targets = [cls._instances[viewport_name]] if viewport_name else list(cls._instances.values())
        for inst in targets:
            inst._clear()

    @classmethod
    def destroy(cls, viewport_name: str = None):
        if viewport_name:
            inst = cls._instances.pop(viewport_name, None)
            if inst:
                inst._destroy()
        else:
            for inst in list(cls._instances.values()):
                inst._destroy()
            cls._instances.clear()

    # ------------------------------------------------------------------
    # 인스턴스
    # ------------------------------------------------------------------

    def __init__(self, viewport_name: str):
        self._viewport_name = viewport_name
        self._scene_view    = None
        self._vp_frame      = None
        self._hit_pos       = None
        self._prim_name     = None
        self._setup(viewport_name)

    def _setup(self, viewport_name: str):
        vp_window = _find_viewport_window(viewport_name)
        if vp_window is None:
            print(f"[ColorpickOverlay] viewport '{viewport_name}' not found")
            return

        frame_key = f"worksync_colorpick_{viewport_name}"

        # ViewportWindow 는 get_frame(), 일반 ui.Window 는 .frame
        if hasattr(vp_window, "get_frame"):
            self._vp_frame = vp_window.get_frame(frame_key)
        else:
            self._vp_frame = vp_window.frame

        with self._vp_frame:
            self._scene_view = sc.SceneView()

    def _update(self, prim_name: str, pos3d: tuple):
        if self._scene_view is None:
            print(f"[ColorpickOverlay] SceneView not ready for '{self._viewport_name}'")
            return
        self._prim_name = prim_name
        self._hit_pos   = pos3d
        self._rebuild_scene()

    def _clear(self):
        self._hit_pos   = None
        self._prim_name = None
        self._rebuild_scene()

    def _rebuild_scene(self):
        if self._scene_view is None:
            return
        self._scene_view.scene.clear()
        if self._hit_pos is None:
            return

        x, y, z    = self._hit_pos
        lx, ly, lz = x, y + LABEL_OFFSET_Y, z

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
        self._scene_view = None
        self._vp_frame   = None
