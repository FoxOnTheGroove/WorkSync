from collections import OrderedDict
import omni.kit.app
import omni.usd
import omni.ui.scene as sc
import omni.ui as ui
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from pxr import UsdGeom, UsdShade, Sdf, Gf, Usd
from .colorpick import Colorpick

MARKER_PRIM_NAME = "colorpick_marker"
MARKER_RADIUS    = 0.35
LABEL_OFFSET_Y   = 5.0
LABEL_SIZE       = 18
LABEL_BG_W       = 180
LABEL_BG_H       = 36
LABEL_BG_COLOR   = 0xFFEBCE87  # sky blue ABGR — adjust if omni.ui 2D uses different format
LINE_THICKNESS   = 2
LINE_COLOR       = 0xFFFFFFFF
MAX_OVERLAYS     = 5


class ColorpickOverlay:
    _instances: dict  = {}   # vpname  -> ColorpickOverlay
    _key_to_vp: dict  = {}   # key     -> vpname
    _next_key: int    = 0

    @classmethod
    def _gen_key(cls) -> int:
        k = cls._next_key
        cls._next_key += 1
        return k

    # ------------------------------------------------------------------
    # classmethod API
    # ------------------------------------------------------------------

    @classmethod
    def on(cls, vp_name: str, pos3d: tuple, **kwargs) -> int | None:
        """히트가 있으면 오버레이를 추가하고 key 반환. 히트 없으면 None."""
        info = Colorpick.get_result_by_name(vp_name)
        if not info["hit"]:
            return None
        c = info["texel_color"]
        display_text = f"{c[0]}, {c[1]}, {c[2]}"
        inst = cls._get_or_create(vp_name)
        return inst._add(info["prim_path"], display_text, pos3d)

    @classmethod
    def off(cls, identifier):
        """key (int) → 해당 오버레이만 끔.  vpname (str) → 해당 뷰포트 전체 끔."""
        if isinstance(identifier, int):
            vpname = cls._key_to_vp.get(identifier)
            if vpname and vpname in cls._instances:
                cls._instances[vpname]._deactivate(identifier)
        elif isinstance(identifier, str):
            if identifier in cls._instances:
                cls._instances[identifier]._deactivate_all()

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

    @classmethod
    def _get_or_create(cls, vpname: str) -> "ColorpickOverlay":
        if vpname not in cls._instances:
            cls._instances[vpname] = cls(vpname)
        return cls._instances[vpname]

    # ------------------------------------------------------------------
    # 인스턴스  (뷰포트 1개당 1인스턴스 / MAX_OVERLAYS개 슬롯 관리)
    # ------------------------------------------------------------------

    def __init__(self, vpname: str):
        self._vpname       = vpname
        self._scene_view   = None
        self._viewport_api = None
        self._frame        = None
        self._slots: list[dict] = []
        self._active: OrderedDict[int, int] = OrderedDict()
        self._update_sub   = None
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._scene_view   = vph.scene_view
            self._viewport_api = vph.viewport_api
            self._frame        = vph.frame
            self._create_slots()
            self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._on_update, name=f"colorpick_overlay_{vpname}"
            )
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for '{vpname}': {e}")

    def _create_slots(self):
        """MAX_OVERLAYS 개의 슬롯을 미리 생성 (모두 숨김).
        - 씬뷰: sc.Line
        - vph.frame: ui.Rectangle + ui.Label (2D 패널)
        """
        with self._frame:
            overlay = ui.ZStack()  # 뷰포트 전체를 덮는 투명 컨테이너

        for _ in range(MAX_OVERLAYS):
            slot = {
                "line_root":  None,
                "bg_placer":  None,
                "panel":      None,
                "text_label": None,
                "world_pos":  None,
                "marker_path": None,
            }

            # ── 3D 씬뷰: 선만 ──────────────────────────────────────
            with self._scene_view.scene:
                with sc.Transform(
                    transform=sc.Matrix44.get_translation_matrix(0, 0, 0),
                    visible=False,
                ) as line_root:
                    slot["line_root"] = line_root
                    sc.Line(
                        [0, 0, 0],
                        [0, LABEL_OFFSET_Y, 0],
                        color=LINE_COLOR,
                        thickness=LINE_THICKNESS,
                    )

            # ── 2D 패널: vph.frame ────────────────────────────────
            with overlay:
                with ui.Placer(offset_x=0, offset_y=0) as placer:
                    slot["bg_placer"] = placer
                    with ui.ZStack(
                        width=LABEL_BG_W,
                        height=LABEL_BG_H,
                        visible=False,
                    ) as panel:
                        slot["panel"] = panel
                        ui.Rectangle(
                            style={"background_color": LABEL_BG_COLOR}
                        )
                        slot["text_label"] = ui.Label(
                            "",
                            alignment=ui.Alignment.CENTER,
                            style={"color": 0xFFFFFFFF, "font_size": LABEL_SIZE},
                        )

            self._slots.append(slot)

    # ------------------------------------------------------------------

    def _on_update(self, event):
        if not self._active:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        for slot_idx in self._active.values():
            slot = self._slots[slot_idx]
            sp = self._world_to_screen(slot["world_pos"], stage)
            if sp:
                slot["bg_placer"].offset_x = sp[0] - LABEL_BG_W / 2
                slot["bg_placer"].offset_y = sp[1] - LABEL_BG_H / 2

    def _world_to_screen(self, world_pos: tuple, stage) -> "tuple | None":
        """world 좌표 → 화면 픽셀 (x, y). 카메라 뒤면 None."""
        try:
            cam_path = self._viewport_api.get_active_camera()
            cam_prim = stage.GetPrimAtPath(str(cam_path))
            if not cam_prim.IsValid():
                return None
            cam_gf   = UsdGeom.Camera(cam_prim).GetCamera(Usd.TimeCode.Default())
            frustum  = cam_gf.frustum
            view     = frustum.ComputeViewMatrix()
            proj     = frustum.ComputeProjectionMatrix()
            cam_space = view.Transform(Gf.Vec3d(*world_pos))
            if cam_space[2] >= 0:   # 카메라 뒤
                return None
            ndc = proj.Transform(cam_space)
            w, h = self._viewport_api.resolution
            return (ndc[0] + 1) / 2 * w, (1 - ndc[1]) / 2 * h
        except Exception:
            return None

    # ------------------------------------------------------------------

    def _add(self, prim_path: str, display_text: str, pos3d: tuple) -> int:
        if len(self._active) >= MAX_OVERLAYS:
            oldest_key = next(iter(self._active))
            self._deactivate(oldest_key)

        used = set(self._active.values())
        slot_idx = next(i for i in range(MAX_OVERLAYS) if i not in used)

        slot = self._slots[slot_idx]
        x, y, z = pos3d
        slot["world_pos"] = (x, y + LABEL_OFFSET_Y, z)
        slot["text_label"].text = display_text
        slot["line_root"].transform = sc.Matrix44.get_translation_matrix(x, y, z)
        slot["line_root"].visible = True
        slot["panel"].visible = True

        self._remove_slot_marker(slot)
        self._create_slot_marker(slot, prim_path, pos3d)

        key = ColorpickOverlay._gen_key()
        self._active[key] = slot_idx
        ColorpickOverlay._key_to_vp[key] = self._vpname
        return key

    def _deactivate(self, key: int):
        slot_idx = self._active.pop(key, None)
        if slot_idx is not None:
            slot = self._slots[slot_idx]
            slot["line_root"].visible = False
            slot["panel"].visible = False
            slot["world_pos"] = None
            self._remove_slot_marker(slot)
        ColorpickOverlay._key_to_vp.pop(key, None)

    def _deactivate_all(self):
        for key in list(self._active.keys()):
            self._deactivate(key)

    # ------------------------------------------------------------------

    def _create_slot_marker(self, slot: dict, prim_path: str, pos3d: tuple):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        target = stage.GetPrimAtPath(prim_path)
        if not target.IsValid():
            return

        w2l = UsdGeom.Xformable(target).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        ).GetInverse()
        local_pos = w2l.Transform(Gf.Vec3d(*pos3d))

        slot_idx    = self._slots.index(slot)
        marker_path = f"{prim_path}/{MARKER_PRIM_NAME}_{slot_idx}"
        sphere = UsdGeom.Sphere.Define(stage, marker_path)
        UsdGeom.XformCommonAPI(sphere).SetTranslate(local_pos)
        sphere.GetRadiusAttr().Set(MARKER_RADIUS)
        self._apply_red_material(stage, sphere.GetPrim())
        slot["marker_path"] = marker_path

    def _remove_slot_marker(self, slot: dict):
        path = slot.get("marker_path")
        if not path:
            return
        stage = omni.usd.get_context().get_stage()
        if stage and stage.GetPrimAtPath(path).IsValid():
            stage.RemovePrim(path)
        slot["marker_path"] = None

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

    # ------------------------------------------------------------------

    def _destroy(self):
        self._deactivate_all()
        self._update_sub = None
        self._scene_view = None
        self._slots.clear()
