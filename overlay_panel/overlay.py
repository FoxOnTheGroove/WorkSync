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
LABEL_BG_W       = 180          # pixels
LABEL_BG_H       = 36           # pixels
LABEL_BG_COLOR   = 0xFFEBCE87  # sky blue ABGR (0xAABBGGRR)
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
    def set_visible(cls, vp_name: str, visible: bool):
        """선·패널만 visible 토글. 마커 유지. 뷰포트 단위."""
        if vp_name in cls._instances:
            cls._instances[vp_name]._set_visible_all(visible)

    @classmethod
    def set_visible_all(cls, visible: bool):
        """모든 뷰포트의 선·패널 visible 토글."""
        for inst in cls._instances.values():
            inst._set_visible_all(visible)

    # ------------------------------------------------------------------
    # 외부 호출용 convenience API
    # ------------------------------------------------------------------

    @classmethod
    def panel_on(cls, vp_name: str, pos3d: tuple, **kwargs) -> int | None:
        return cls.on(vp_name, pos3d, **kwargs)

    @classmethod
    def panel_off(cls, key: int):
        cls.off(key)

    @classmethod
    def panel_off_all(cls, vp_name: str):
        cls.off(vp_name)

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
        self._slots: list[dict] = []
        self._active: OrderedDict[int, int] = OrderedDict()
        self._update_sub    = None
        self._cam_mat_prev  = None  # N-1 프레임
        self._cam_mat_prev2 = None  # N-2 프레임 (RTX 2프레임 레이턴시 대응)
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._scene_view   = vph.scene_view
            self._viewport_api = vph.viewport_api
            self._create_slots()
            self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._on_update, name=f"colorpick_overlay_{vpname}"
            )
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for '{vpname}': {e}")

    def _create_slots(self):
        """MAX_OVERLAYS 개의 씬 아이템 세트를 미리 생성 (모두 숨김)."""
        for _ in range(MAX_OVERLAYS):
            slot = {"root": None, "label": None, "bg_tf": None, "marker_path": None}
            with self._scene_view.scene:
                with sc.Transform(
                    transform=sc.Matrix44.get_translation_matrix(0, 0, 0),
                    visible=False,
                ) as root:
                    slot["root"] = root
                    sc.Line(
                        [0, 0, 0],
                        [0, LABEL_OFFSET_Y, 0],
                        color=LINE_COLOR,
                        thickness=LINE_THICKNESS,
                    )
                    with sc.Transform(
                        transform=sc.Matrix44.get_translation_matrix(0, LABEL_OFFSET_Y, 0)
                    ):
                        # bg_tf: 매 프레임 카메라 회전역행렬로 갱신 → 빌보드
                        with sc.Transform(
                            transform=sc.Matrix44.get_translation_matrix(0, 0, 0)
                        ) as bg_tf:
                            slot["bg_tf"] = bg_tf
                            with sc.Transform(scale_to=sc.Space.SCREEN):
                                sc.Rectangle(LABEL_BG_W, LABEL_BG_H, color=LABEL_BG_COLOR)
                        slot["label"] = sc.Label(
                            "",
                            size=LABEL_SIZE,
                            alignment=ui.Alignment.CENTER,
                        )
            self._slots.append(slot)

    # ------------------------------------------------------------------

    def _on_update(self, event):
        """프레임마다 bg_tf 회전만 갱신. scene.clear() 없음 → 제스처 안전."""
        if not self._active:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        cur_mat = self._get_billboard_mat(stage)
        if cur_mat is None:
            return
        # 2프레임 전 행렬 적용 → RTX 2프레임 레이턴시와 동기화
        apply_mat = self._cam_mat_prev2 or self._cam_mat_prev or cur_mat
        for slot_idx in self._active.values():
            self._slots[slot_idx]["bg_tf"].transform = apply_mat
        self._cam_mat_prev2 = self._cam_mat_prev
        self._cam_mat_prev  = cur_mat

    def _get_billboard_mat(self, stage) -> "sc.Matrix44 | None":
        """카메라 월드 트랜스폼에서 회전만 추출해 sc.Matrix44로 반환."""
        try:
            cam_path = self._viewport_api.get_active_camera()
            cam_prim = stage.GetPrimAtPath(str(cam_path))
            if not cam_prim.IsValid():
                return None
            xf = UsdGeom.Xformable(cam_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())

            def _norm(row):
                l = (xf[row][0]**2 + xf[row][1]**2 + xf[row][2]**2) ** 0.5
                return [xf[row][c] / l for c in range(3)] if l > 1e-9 else [xf[row][c] for c in range(3)]

            r, u, f = _norm(0), _norm(1), _norm(2)
            flat = [
                r[0], r[1], r[2], 0,
                u[0], u[1], u[2], 0,
                f[0], f[1], f[2], 0,
                0,    0,    0,    1,
            ]
            return sc.Matrix44(*flat)
        except Exception:
            return None

    # ------------------------------------------------------------------

    def _add(self, prim_path: str, display_text: str, pos3d: tuple) -> int:
        """슬롯에 오버레이 추가. 풀이 꽉 차면 가장 오래된 것을 FIFO 방출."""
        if len(self._active) >= MAX_OVERLAYS:
            oldest_key = next(iter(self._active))
            self._deactivate(oldest_key)

        used = set(self._active.values())
        slot_idx = next(i for i in range(MAX_OVERLAYS) if i not in used)

        slot = self._slots[slot_idx]
        x, y, z = pos3d
        slot["root"].transform = sc.Matrix44.get_translation_matrix(x, y, z)
        slot["label"].text     = display_text
        slot["root"].visible   = True

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
            slot["root"].visible = False
            self._remove_slot_marker(slot)
        ColorpickOverlay._key_to_vp.pop(key, None)

    def _deactivate_all(self):
        for key in list(self._active.keys()):
            self._deactivate(key)

    def _set_visible(self, key: int, visible: bool):
        slot_idx = self._active.get(key)
        if slot_idx is not None:
            self._slots[slot_idx]["root"].visible = visible

    def _set_visible_all(self, visible: bool):
        for key in self._active:
            self._set_visible(key, visible)

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
        self._update_sub    = None
        self._cam_mat_prev  = None
        self._cam_mat_prev2 = None
        self._scene_view   = None
        self._slots.clear()
