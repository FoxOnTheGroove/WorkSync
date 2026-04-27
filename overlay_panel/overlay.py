import omni.kit.app
import omni.usd
import omni.ui.scene as sc
import omni.ui as ui
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from pxr import UsdGeom, Gf, Usd

MARKER_PRIM_NAME = "colorpick_marker"
MARKER_RADIUS    = 0.05
LABEL_OFFSET_Y   = 0.5
LINE_THICKNESS   = 2
LINE_COLOR       = 0xFFFFFFFF


class ColorpickOverlay:
    _instances: dict = {}

    # ------------------------------------------------------------------
    # classmethod API
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, vpname: str) -> "ColorpickOverlay":
        if vpname not in cls._instances:
            cls._instances[vpname] = cls(vpname)
        return cls._instances[vpname]

    @classmethod
    def on(cls, vpname: str, target_prim_path: str, prim_name: str, pos3d: tuple, **kwargs):
        """컬러픽 발생 시 호출. 기존 마커/오버레이를 지우고 새 위치에 다시 그림."""
        cls.get(vpname)._update(target_prim_path, prim_name, pos3d)

    @classmethod
    def off(cls, vpname: str = None):
        """프림 미선택 등 오버레이를 숨겨야 할 때 호출 (추후 로직 확장 예정)."""
        targets = [cls._instances[vpname]] if (vpname and vpname in cls._instances) else list(cls._instances.values())
        for inst in targets:
            inst._clear()

    @classmethod
    def destroy(cls, vpname: str = None):
        """익스텐션 종료 시 호출. SceneView 참조 및 USD 마커 전체 해제."""
        if vpname:
            inst = cls._instances.pop(vpname, None)
            if inst:
                inst._destroy()
        else:
            for inst in list(cls._instances.values()):
                inst._destroy()
            cls._instances.clear()

    # ------------------------------------------------------------------
    # 인스턴스
    # ------------------------------------------------------------------

    def __init__(self, vpname: str):
        self._vpname       = vpname
        self._scene_view   = None
        self._marker_path  = None
        self._update_sub   = None
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._scene_view = vph.scene_view
        except Exception as e:
            print(f"[ColorpickOverlay] failed to get scene_view for '{vpname}': {e}")

    # ------------------------------------------------------------------

    def _update(self, target_prim_path: str, prim_name: str, pos3d: tuple):
        if self._scene_view is None:
            print(f"[ColorpickOverlay] scene_view not ready for '{self._vpname}'")
            return

        self._prim_name = prim_name
        self._remove_marker()
        self._create_marker(target_prim_path, pos3d)

        if self._update_sub is None:
            self._update_sub = omni.kit.app.get_app().get_update_event_stream() \
                .create_subscription_to_pop(self._on_update, name=f"colorpick_{self._vpname}")

    def _create_marker(self, target_prim_path: str, pos3d: tuple):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        target = stage.GetPrimAtPath(target_prim_path)
        if not target.IsValid():
            return

        # world → 타겟 로컬 변환
        w2l = UsdGeom.Xformable(target).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        ).GetInverse()
        local_pos = w2l.Transform(Gf.Vec3d(*pos3d))

        marker_path = f"{target_prim_path}/{MARKER_PRIM_NAME}"
        sphere = UsdGeom.Sphere.Define(stage, marker_path)
        UsdGeom.XformCommonAPI(sphere).SetTranslate(local_pos)
        sphere.GetRadiusAttr().Set(MARKER_RADIUS)
        self._marker_path = marker_path

    def _remove_marker(self):
        if self._marker_path is None:
            return
        stage = omni.usd.get_context().get_stage()
        if stage and stage.GetPrimAtPath(self._marker_path).IsValid():
            stage.RemovePrim(self._marker_path)
        self._marker_path = None

    # ------------------------------------------------------------------

    def _on_update(self, _event):
        if self._marker_path is None:
            return

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        marker_prim = stage.GetPrimAtPath(self._marker_path)
        if not marker_prim.IsValid():
            return

        world_xform = UsdGeom.Xformable(marker_prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        world_pos = tuple(world_xform.ExtractTranslation())
        self._rebuild_scene(world_pos)

    def _rebuild_scene(self, world_pos: tuple):
        self._scene_view.scene.clear()

        x, y, z    = world_pos
        lx, ly, lz = x, y + LABEL_OFFSET_Y, z

        with self._scene_view.scene:
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

    # ------------------------------------------------------------------

    def _clear(self):
        self._update_sub = None
        self._remove_marker()
        if self._scene_view:
            self._scene_view.scene.clear()

    def _destroy(self):
        self._clear()
        self._scene_view = None
