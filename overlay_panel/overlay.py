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
PANEL_COLOR      = 0xCC303030
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
        c = info["texel_color"]
        display_text = f"{c[0]}, {c[1]}, {c[2]}"
        cls.get(vp_name)._show(info["prim_path"], display_text, pos3d)

    @classmethod
    def off(cls, vp_name: str = None):
        targets = [cls._instances[vp_name]] if (vp_name and vp_name in cls._instances) else list(cls._instances.values())
        for inst in targets:
            inst._hide()

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
        self._vpname      = vpname
        self._scene_view  = None
        self._marker_path = None
        self._root        = None   # sc.Transform — 전체 오버레이 루트
        self._label       = None   # sc.Label 참조 (text 갱신용)
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._scene_view = vph.scene_view
            self._create_scene_items()
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for '{vpname}': {e}")

    def _create_scene_items(self):
        """씬 아이템을 딱 한 번 생성. 이후 visible/transform 속성만 조작."""
        with self._scene_view.scene:
            with sc.Transform(
                transform=sc.Matrix44.get_translation_matrix(0, 0, 0),
                visible=False,
            ) as root:
                self._root = root
                # 충돌점(로컬 원점) → 레이블(Y 오프셋) 연결선
                sc.Line(
                    [0, 0, 0],
                    [0, LABEL_OFFSET_Y, 0],
                    color=LINE_COLOR,
                    thickness=LINE_THICKNESS,
                )
                with sc.Transform(
                    transform=sc.Matrix44.get_translation_matrix(0, LABEL_OFFSET_Y, 0)
                ):
                    self._label = sc.Label(
                        "",
                        size=LABEL_SIZE,
                        fill_color=PANEL_COLOR,
                        alignment=ui.Alignment.CENTER,
                    )

    # ------------------------------------------------------------------

    def _show(self, target_prim_path: str, display_text: str, pos3d: tuple):
        if self._root is None:
            print(f"[ColorpickOverlay] scene items not ready for '{self._vpname}'")
            return

        self._remove_marker()
        self._create_marker(target_prim_path, pos3d)

        # 루트 Transform 위치만 갱신 — scene.clear() 없음
        x, y, z = pos3d
        self._root.transform = sc.Matrix44.get_translation_matrix(x, y, z)
        self._label.text     = display_text
        self._root.visible   = True

    def _hide(self):
        self._remove_marker()
        if self._root is not None:
            self._root.visible = False

    # ------------------------------------------------------------------

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
        mat    = UsdShade.Material.Define(stage, mat_path)
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

    def _destroy(self):
        self._hide()
        self._scene_view = None
        self._root       = None
        self._label      = None
