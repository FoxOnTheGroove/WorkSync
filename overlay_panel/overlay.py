import omni.kit.app
import omni.usd
import omni.ui.scene as sc
import omni.ui as ui
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from pxr import UsdGeom, UsdShade, Sdf, Gf, Usd
from .colorpick import Colorpick

MARKER_PRIM_NAME = "colorpick_marker"
MARKER_RADIUS    = 1.0
LABEL_OFFSET_Y   = 5.0
LABEL_SIZE       = 18
PANEL_WIDTH      = 10.0
PANEL_HEIGHT     = 3.0
PANEL_COLOR      = 0xFF808080
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
    def on(cls, vp_name: str, pos3d: tuple, **kwargs):
        info = Colorpick.get_result_by_name(vp_name)
        if not info["hit"]:
            cls.off(vp_name)
            return
        cls.get(vp_name)._update(info["prim_path"], info["prim_name"], pos3d)

    @classmethod
    def off(cls, vp_name: str = None):
        targets = [cls._instances[vp_name]] if (vp_name and vp_name in cls._instances) else list(cls._instances.values())
        for inst in targets:
            inst._clear()

    @classmethod
    def destroy(cls, vp_name: str = None):
        if vp_name:
            inst = cls._instances.pop(vp_name, None)
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
        self._vpname          = vpname
        self._scene_view      = None
        self._viewport_api    = None
        self._marker_path     = None
        self._update_sub      = None
        self._last_world_pos  = None
        self._last_cam_key    = None
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._scene_view   = vph.scene_view
            self._viewport_api = vph.get_viewport().viewport_api
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for '{vpname}': {e}")

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

        w2l = UsdGeom.Xformable(target).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        ).GetInverse()
        local_pos = w2l.Transform(Gf.Vec3d(*pos3d))

        marker_path = f"{target_prim_path}/{MARKER_PRIM_NAME}"
        sphere = UsdGeom.Sphere.Define(stage, marker_path)
        UsdGeom.XformCommonAPI(sphere).SetTranslate(local_pos)
        sphere.GetRadiusAttr().Set(MARKER_RADIUS)
        self._apply_red_material(stage, sphere.GetPrim())
        self._marker_path = marker_path

    def _apply_red_material(self, stage, prim):
        mat_path = str(prim.GetPath()) + "_mat"
        mat = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path + "/shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor",  Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 0))
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.8, 0, 0))
        shader.CreateInput("roughness",     Sdf.ValueTypeNames.Float).Set(0.5)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI(prim).Bind(mat)

    def _remove_marker(self):
        if self._marker_path is None:
            return
        stage = omni.usd.get_context().get_stage()
        if stage and stage.GetPrimAtPath(self._marker_path).IsValid():
            stage.RemovePrim(self._marker_path)
        self._marker_path = None

    # ------------------------------------------------------------------

    def _get_camera_xform(self, stage) -> "Gf.Matrix4d | None":
        if self._viewport_api is None:
            return None
        try:
            cam_path = self._viewport_api.get_active_camera()
            cam_prim = stage.GetPrimAtPath(cam_path)
            if not cam_prim.IsValid():
                return None
            return UsdGeom.Xformable(cam_prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default()
            )
        except Exception:
            return None

    def _on_update(self, _event):
        if self._marker_path is None:
            return

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        marker_prim = stage.GetPrimAtPath(self._marker_path)
        if not marker_prim.IsValid():
            return

        world_pos = tuple(
            UsdGeom.Xformable(marker_prim)
            .ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            .ExtractTranslation()
        )

        cam_xform = self._get_camera_xform(stage)
        # 회전 변화 감지용 경량 키: 카메라 X축(right)의 월드 방향
        if cam_xform:
            rot = cam_xform.ExtractRotationMatrix()
            cam_key = (rot[0][0], rot[1][0], rot[2][0])
        else:
            cam_key = None

        if world_pos == self._last_world_pos and cam_key == self._last_cam_key:
            return

        self._last_world_pos = world_pos
        self._last_cam_key   = cam_key
        self._rebuild_scene(world_pos, cam_xform)

    def _rebuild_scene(self, world_pos: tuple, cam_xform=None):
        self._scene_view.scene.clear()

        x, y, z    = world_pos
        lx, ly, lz = x, y + LABEL_OFFSET_Y, z

        # 빌보드 매트릭스: 카메라 rotation + 레이블 위치
        if cam_xform:
            rot = cam_xform.ExtractRotationMatrix()   # GfMatrix3d
            gf_mat = Gf.Matrix4d(rot, Gf.Vec3d(lx, ly, lz))
        else:
            gf_mat = Gf.Matrix4d(1.0)
            gf_mat.SetTranslateOnly(Gf.Vec3d(lx, ly, lz))

        # sc.Matrix44 은 flat 16개 float 리스트를 받음
        flat = [gf_mat[r][c] for r in range(4) for c in range(4)]

        with self._scene_view.scene:
            sc.Line(
                [x, y, z],
                [lx, ly, lz],
                color=LINE_COLOR,
                thickness=LINE_THICKNESS,
            )
            with sc.Transform(transform=sc.Matrix44(flat)):
                sc.Rectangle(
                    width=PANEL_WIDTH,
                    height=PANEL_HEIGHT,
                    color=PANEL_COLOR,
                )
                sc.Label(
                    self._prim_name,
                    size=LABEL_SIZE,
                    alignment=ui.Alignment.CENTER,
                )

    # ------------------------------------------------------------------

    def _clear(self):
        self._update_sub     = None
        self._last_world_pos = None
        self._last_cam_key   = None
        self._remove_marker()
        if self._scene_view:
            self._scene_view.scene.clear()

    def _destroy(self):
        self._clear()
        self._scene_view   = None
        self._viewport_api = None
