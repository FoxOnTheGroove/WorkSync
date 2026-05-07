from collections import OrderedDict
import omni.kit.app
import omni.usd
import omni.ui as ui
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from pxr import UsdGeom, UsdShade, Sdf, Gf, Usd
from .colorpick import Colorpick

MARKER_PRIM_NAME = "colorpick_marker"
MARKER_RADIUS    = 0.35
PANEL_W          = 160
PANEL_H          = 80
PANEL_BG         = 0xFFFFFFFF
SWATCH_COL_W     = PANEL_H
DOT_SIZE         = 13
LABEL_SIZE       = 13
PANEL_PAD        = 6
ITEM_GAP         = 5
PANEL_OFFSET_X   = 12          # 빨간 점 → 패널 우하단 오프셋 (픽셀)
PANEL_OFFSET_Y   = 12
RING_SIZE        = 10          # 동심원 지름 (픽셀)
RING_THICK       = 2           # 동심원 테두리 두께
MAX_OVERLAYS     = 5

_WIN_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR             |
    ui.WINDOW_FLAGS_NO_SCROLLBAR             |
    ui.WINDOW_FLAGS_NO_RESIZE                |
    ui.WINDOW_FLAGS_NO_CLOSE                 |
    ui.WINDOW_FLAGS_NO_COLLAPSE              |
    ui.WINDOW_FLAGS_NO_MOVE                  |
    ui.WINDOW_FLAGS_NO_DOCKING            |
    ui.WINDOW_FLAGS_NO_BACKGROUND         |
    ui.WINDOW_FLAGS_NO_FOCUS_ON_APPEARING
)

_RING_FLAGS = _WIN_FLAGS


def _to_temp(rgb) -> str:
    return "111"


def _to_pressure(rgb) -> str:
    return "111"


class ColorpickOverlay:
    _instances: dict  = {}
    _key_to_vp: dict  = {}
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
        info = Colorpick.get_result_by_name(vp_name)
        if not info["hit"]:
            return None
        c        = info["texel_color"]
        hex_str  = f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"
        temp_str = f"온도 {_to_temp(c)}"
        pres_str = f"압력 {_to_pressure(c)}"
        ui_color = (0xFF << 24) | (c[2] << 16) | (c[1] << 8) | c[0]
        inst = cls._get_or_create(vp_name)
        return inst._add(info["prim_path"], hex_str, temp_str, pres_str, ui_color, pos3d)

    @classmethod
    def off(cls, identifier):
        if isinstance(identifier, int):
            vpname = cls._key_to_vp.get(identifier)
            if vpname and vpname in cls._instances:
                cls._instances[vpname]._deactivate(identifier)
        elif isinstance(identifier, str):
            if identifier in cls._instances:
                cls._instances[identifier]._deactivate_all()

    @classmethod
    def set_visible(cls, vp_name: str, visible: bool):
        if vp_name in cls._instances:
            cls._instances[vp_name]._set_visible_all(visible)

    @classmethod
    def set_visible_all(cls, visible: bool):
        for inst in cls._instances.values():
            inst._set_visible_all(visible)

    # ------------------------------------------------------------------
    # convenience API
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
    # 인스턴스 (뷰포트 1개당 1인스턴스 / MAX_OVERLAYS개 슬롯 관리)
    # ------------------------------------------------------------------

    def __init__(self, vpname: str):
        self._vpname       = vpname
        self._viewport_api = None
        self._frame        = None
        self._slots: list[dict] = []
        self._active: OrderedDict[int, int] = OrderedDict()
        self._update_sub   = None
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._viewport_api = vph.viewport.viewport_api
            self._frame        = vph.frame
            self._create_slots()
            self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._on_update, name=f"colorpick_overlay_{vpname}"
            )
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for '{vpname}': {e}")

    def _create_slots(self):
        _lbl_style = {"color": 0xFF202020, "font_size": LABEL_SIZE}

        for i in range(MAX_OVERLAYS):
            # ── 패널 윈도우 ───────────────────────────────────────
            win = ui.Window(
                f"_cpoverlay_{self._vpname}_{i}",
                flags=_WIN_FLAGS,
                width=PANEL_W, height=PANEL_H,
                visible=False,
            )
            win.frame.style = {"background_color": 0x00000000}
            win.frame.opaque_for_mouse_events = False
            win.padding_x = 0
            win.padding_y = 0
            with win.frame:
                with ui.ZStack():
                    ui.Rectangle(style={
                        "background_color": PANEL_BG,
                        "border_radius": 4,
                    })
                    with ui.HStack():
                        with ui.VStack(width=SWATCH_COL_W):
                            ui.Spacer(height=PANEL_PAD)
                            with ui.HStack():
                                ui.Spacer(width=PANEL_PAD)
                                swatch = ui.Rectangle(
                                    style={
                                        "background_color": 0xFF808080,
                                        "border_radius": 4,
                                    },
                                )
                                ui.Spacer(width=PANEL_PAD)
                            ui.Spacer(height=PANEL_PAD)
                        with ui.VStack(spacing=ITEM_GAP):
                            ui.Spacer(height=PANEL_PAD)
                            with ui.HStack(height=DOT_SIZE, spacing=4):
                                dot = ui.Rectangle(
                                    width=DOT_SIZE,
                                    style={
                                        "background_color": 0xFF808080,
                                        "border_radius": 2,
                                    },
                                )
                                hex_lbl = ui.Label(
                                    "#000000",
                                    style=_lbl_style,
                                )
                            temp_lbl = ui.Label(
                                "온도 -",
                                style=_lbl_style,
                            )
                            pres_lbl = ui.Label(
                                "압력 -",
                                style=_lbl_style,
                            )
                            ui.Spacer(height=PANEL_PAD)
                        ui.Spacer(width=PANEL_PAD)

            # ── 동심원 윈도우 ─────────────────────────────────────
            ring_win = ui.Window(
                f"_cpring_{self._vpname}_{i}",
                flags=_RING_FLAGS,
                width=RING_SIZE, height=RING_SIZE,
                visible=False,
            )
            ring_win.frame.style = {"background_color": 0x00000000}
            ring_win.frame.opaque_for_mouse_events = False
            ring_win.padding_x = 0
            ring_win.padding_y = 0
            with ring_win.frame:
                ui.Rectangle(style={
                    "background_color": 0x00000000,
                    "border_color":     0xFF000000,
                    "border_width":     RING_THICK,
                    "border_radius":    999,
                })

            self._slots.append({
                "window":      win,
                "ring_win":    ring_win,
                "swatch":      swatch,
                "color_dot":   dot,
                "hex_label":   hex_lbl,
                "temp_label":  temp_lbl,
                "press_label": pres_lbl,
                "world_pos":   None,
                "marker_path": None,
            })

    # ------------------------------------------------------------------

    def _viewport_offset(self) -> tuple:
        try:
            return self._frame.screen_position_x, self._frame.screen_position_y
        except Exception:
            return 0.0, 0.0

    def _on_update(self, event):
        if not self._active:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        ox, oy = self._viewport_offset()
        vp_w   = self._frame.computed_width
        vp_h   = self._frame.computed_height
        for slot_idx in self._active.values():
            slot = self._slots[slot_idx]
            if slot["world_pos"] is None:
                continue
            sp = self._world_to_screen(slot["world_pos"], stage)
            if sp:
                raw_x = ox + sp[0] + PANEL_OFFSET_X
                raw_y = oy + sp[1] + PANEL_OFFSET_Y
                slot["window"].position_x = max(ox, min(ox + vp_w - PANEL_W, raw_x))
                slot["window"].position_y = max(oy, min(oy + vp_h - PANEL_H, raw_y))
                slot["ring_win"].position_x = ox + sp[0] - RING_SIZE / 2
                slot["ring_win"].position_y = oy + sp[1] - RING_SIZE / 2

    def _world_to_screen(self, world_pos: tuple, stage) -> "tuple | None":
        try:
            cam_path  = self._viewport_api.get_active_camera()
            cam_prim  = stage.GetPrimAtPath(str(cam_path))
            if not cam_prim.IsValid():
                return None
            cam_gf  = UsdGeom.Camera(cam_prim).GetCamera(Usd.TimeCode.Default())
            frustum = cam_gf.frustum
            view    = frustum.ComputeViewMatrix()
            proj    = frustum.ComputeProjectionMatrix()
            cam_space = view.Transform(Gf.Vec3d(*world_pos))
            if cam_space[2] >= 0:
                return None
            ndc = proj.Transform(cam_space)
            w = self._frame.computed_width
            h = self._frame.computed_height
            return (ndc[0] + 1) / 2 * w, (1 - ndc[1]) / 2 * h
        except Exception:
            return None

    # ------------------------------------------------------------------

    def _add(self, prim_path: str, hex_str: str, temp_str: str,
             pres_str: str, ui_color: int, pos3d: tuple) -> int:
        if len(self._active) >= MAX_OVERLAYS:
            oldest_key = next(iter(self._active))
            self._deactivate(oldest_key)

        used     = set(self._active.values())
        slot_idx = next(i for i in range(MAX_OVERLAYS) if i not in used)
        slot     = self._slots[slot_idx]

        slot["world_pos"]      = pos3d
        slot["window"].visible = True
        slot["ring_win"].visible = True

        slot["swatch"].style     = {"background_color": ui_color}
        slot["color_dot"].style  = {"background_color": ui_color}
        slot["hex_label"].text   = hex_str
        slot["temp_label"].text  = temp_str
        slot["press_label"].text = pres_str

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
            slot["window"].visible   = False
            slot["ring_win"].visible = False
            slot["world_pos"]        = None
            self._remove_slot_marker(slot)
        ColorpickOverlay._key_to_vp.pop(key, None)

    def _deactivate_all(self):
        for key in list(self._active.keys()):
            self._deactivate(key)

    def _set_visible(self, key: int, visible: bool):
        slot_idx = self._active.get(key)
        if slot_idx is not None:
            slot = self._slots[slot_idx]
            slot["window"].visible   = visible
            slot["ring_win"].visible = visible

    def _set_visible_all(self, visible: bool):
        for slot_idx in self._active.values():
            slot = self._slots[slot_idx]
            slot["window"].visible   = visible
            slot["ring_win"].visible = visible

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
        local_pos   = w2l.Transform(Gf.Vec3d(*pos3d))
        slot_idx    = self._slots.index(slot)
        marker_path = f"{prim_path}/{MARKER_PRIM_NAME}_{slot_idx}"
        sphere      = UsdGeom.Sphere.Define(stage, marker_path)
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
        for slot in self._slots:
            win = slot.get("window")
            if win:
                win.destroy()
            ring = slot.get("ring_win")
            if ring:
                ring.destroy()
        self._update_sub = None
        self._slots.clear()
