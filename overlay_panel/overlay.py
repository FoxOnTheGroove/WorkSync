from collections import OrderedDict
import os
import omni.kit.app
import omni.usd
import omni.ui as ui
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from pxr import UsdGeom, UsdShade, Sdf, Gf, Usd
from .colorpick import Colorpick

MARKER_PRIM_NAME = "colorpick_marker"
MARKER_RADIUS    = 0.35
PANEL_W          = 190
PANEL_H          = 80
PANEL_BG         = 0xFFFFFFFF
SWATCH_COL_W     = 80
DOT_SIZE         = 13
LABEL_SIZE       = 13
PANEL_PAD        = 6
ITEM_GAP         = 5
PANEL_OFFSET_X   = 12
PANEL_OFFSET_Y   = 12
MAX_OVERLAYS     = 5

_WIN_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR          |
    ui.WINDOW_FLAGS_NO_SCROLLBAR          |
    ui.WINDOW_FLAGS_NO_RESIZE             |
    ui.WINDOW_FLAGS_NO_CLOSE              |
    ui.WINDOW_FLAGS_NO_COLLAPSE           |
    ui.WINDOW_FLAGS_NO_MOVE               |
    ui.WINDOW_FLAGS_NO_DOCKING            |
    ui.WINDOW_FLAGS_NO_BACKGROUND         |
    ui.WINDOW_FLAGS_NO_FOCUS_ON_APPEARING
)


def _load_pressure_map():
    path = os.path.join(os.path.dirname(__file__), "data", "plot_velocity")
    try:
        with open(path) as f:
            return [float(l.strip()) for l in f if l.strip()]
    except Exception:
        return []

_PRESSURE_MAP = _load_pressure_map()


def _pressure_data(uv_val: float):
    vals = _PRESSURE_MAP
    n = len(vals)
    if n < 2:
        print(f"[Pressure] PRESSURE_MAP empty, uv={uv_val:.6f}")
        return None, None
    v_min, v_max = vals[0], vals[-1]
    if v_max == v_min:
        return 0.0, vals[0]
    norm = [(v - v_min) / (v_max - v_min) for v in vals]
    if uv_val <= norm[0]:
        print(f"[Pressure] uv={uv_val:.6f} <= norm[0]={norm[0]:.6f} (val={vals[0]}) → clamp 0")
        return 0.0, vals[0]
    if uv_val >= norm[-1]:
        print(f"[Pressure] uv={uv_val:.6f} >= norm[{n-1}]={norm[-1]:.6f} (val={vals[-1]}) → clamp {n-1}")
        return float(n - 1), vals[-1]
    for i in range(n - 1):
        if norm[i] <= uv_val <= norm[i + 1]:
            span = norm[i + 1] - norm[i]
            t = (uv_val - norm[i]) / span if span > 0 else 0.0
            idx = i + t
            interp_val = vals[i] + t * (vals[i + 1] - vals[i])
            print(
                f"[Pressure] uv={uv_val:.6f} | "
                f"n1=[{i}] val={vals[i]:.6f} norm={norm[i]:.6f} | "
                f"n2=[{i+1}] val={vals[i+1]:.6f} norm={norm[i+1]:.6f} | "
                f"→ idx={idx:.2f} val={interp_val:.6f}"
            )
            return idx, interp_val
    return float(n - 1), vals[-1]


class ColorpickOverlay:
    _instances: dict  = {}
    _key_to_vp: dict  = {}
    _next_key: int    = 0
    _vis_suppress: bool = True

    @classmethod
    def _gen_key(cls) -> int:
        k = cls._next_key
        cls._next_key += 1
        return k

    # ------------------------------------------------------------------
    # classmethod API
    # ------------------------------------------------------------------

    @classmethod
    def on(cls, gesture_id: str, vp_api_id: str, pos3d: tuple, **kwargs) -> int | None:
        info = Colorpick.get_result_by_id(vp_api_id)
        if not info["hit"]:
            return None
        c   = info["texel_color"]
        uv  = info.get("uv_value", 0.0) or 0.0
        idx, val = _pressure_data(uv)
        hex_str   = f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"
        pres_str  = f"압력(v_idx) {idx:.2f}"   if idx is not None else "압력(v_idx) -"
        plotv_str = f"plot_v value : {val:.6f}" if val is not None else "plot_v value : -"
        ui_color  = (0xFF << 24) | (c[2] << 16) | (c[1] << 8) | c[0]
        inst = cls._get_or_create(vp_api_id)
        return inst._add(info["prim_path"], hex_str, pres_str, plotv_str, ui_color, pos3d, gesture_id)

    @classmethod
    def off(cls, identifier):
        if isinstance(identifier, int):
            vp_api_id = cls._key_to_vp.get(identifier)
            if vp_api_id and vp_api_id in cls._instances:
                cls._instances[vp_api_id]._deactivate(identifier)
        elif isinstance(identifier, str):
            if identifier in cls._instances:
                cls._instances[identifier]._deactivate_all()

    @classmethod
    def set_visible(cls, vp_api_id: str, visible: bool):
        if vp_api_id in cls._instances:
            inst = cls._instances[vp_api_id]
            inst._vis_vp = visible
            inst._refresh_visible()

    @classmethod
    def visible_all(cls, visible: bool):
        for inst in cls._instances.values():
            inst._vis_vp = visible
            inst._refresh_visible()

    @classmethod
    def suppress_all(cls, visible: bool):
        cls._vis_suppress = visible
        for inst in cls._instances.values():
            inst._refresh_visible()

    # ------------------------------------------------------------------
    # convenience API
    # ------------------------------------------------------------------

    @classmethod
    def panel_on(cls, gesture_id: str, vp_api_id: str, pos3d: tuple, **kwargs) -> int | None:
        return cls.on(gesture_id, vp_api_id, pos3d, **kwargs)

    @classmethod
    def panel_off(cls, key: int):
        cls.off(key)

    @classmethod
    def panel_off_all(cls, vp_api_id: str):
        cls.off(vp_api_id)

    @classmethod
    def destroy(cls, vp_api_id: str = None):
        if vp_api_id:
            inst = cls._instances.pop(vp_api_id, None)
            if inst:
                inst._destroy()
        else:
            for inst in list(cls._instances.values()):
                inst._destroy()
            cls._instances.clear()

    @classmethod
    def _get_or_create(cls, vp_api_id: str) -> "ColorpickOverlay":
        if vp_api_id not in cls._instances:
            cls._instances[vp_api_id] = cls(vp_api_id)
        return cls._instances[vp_api_id]

    # ------------------------------------------------------------------
    # 인스턴스 (뷰포트 1개당 1인스턴스 / MAX_OVERLAYS개 슬롯 관리)
    # ------------------------------------------------------------------

    def __init__(self, vp_api_id: str):
        self._vp_api_id    = vp_api_id
        self._vis_vp       = True
        self._viewport_api = None
        self._frame        = None
        self._slots: list[dict] = []
        self._active: OrderedDict[int, int] = OrderedDict()
        self._update_sub   = None
        self._setup(vp_api_id)

    def _setup(self, vp_api_id: str):
        try:
            vphs = hytwin_vp_wg.ViewportWidgetHost().get_instances()
            vph  = next((v for v in vphs if v.viewport_api.id == vp_api_id), None)
            if vph is None:
                print(f"[ColorpickOverlay] viewport id '{vp_api_id}' not found")
                return
            self._viewport_api = vph.viewport.viewport_api
            self._frame        = vph.frame
            self._create_slots()
            self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._on_update, name=f"colorpick_overlay_{vp_api_id}"
            )
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for id '{vp_api_id}': {e}")

    def _create_slots(self):
        _lbl_style = {"color": 0xFF202020, "font_size": LABEL_SIZE}

        for i in range(MAX_OVERLAYS):
            win = ui.Window(
                f"_cpoverlay_{self._vp_api_id}_{i}",
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
                        "background_color": 0xFFCCCCCC,
                        "border_radius": 5,
                    })
                    with ui.VStack():
                        ui.Spacer(height=1)
                        with ui.HStack():
                            ui.Spacer(width=1)
                            ui.Rectangle(style={
                                "background_color": PANEL_BG,
                                "border_radius": 4,
                            })
                            ui.Spacer(width=1)
                        ui.Spacer(height=1)
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
                            pres_lbl  = ui.Label(
                                "압력(v_idx) -",
                                style=_lbl_style,
                            )
                            plotv_lbl = ui.Label(
                                "plot_v value : -",
                                style=_lbl_style,
                            )
                            ui.Spacer(height=PANEL_PAD)
                        ui.Spacer(width=PANEL_PAD)

            self._slots.append({
                "window":      win,
                "swatch":      swatch,
                "color_dot":   dot,
                "hex_label":   hex_lbl,
                "press_label": pres_lbl,
                "plotv_label": plotv_lbl,
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
        rw, rh = self._viewport_api.resolution
        dw = self._frame.computed_width  or rw
        dh = self._frame.computed_height or rh
        sx = dw / rw if rw > 0 else 1.0
        sy = dh / rh if rh > 0 else 1.0
        for slot_idx in self._active.values():
            slot = self._slots[slot_idx]
            if slot["world_pos"] is None:
                continue
            sp = self._world_to_screen(slot["world_pos"], stage)
            if sp:
                px = ox + sp[0] * sx
                py = oy + sp[1] * sy
                raw_x = px + PANEL_OFFSET_X
                raw_y = py + PANEL_OFFSET_Y
                slot["window"].position_x = max(ox, min(ox + dw - PANEL_W, raw_x))
                slot["window"].position_y = max(oy, min(oy + dh - PANEL_H, raw_y))

    def _world_to_screen(self, world_pos: tuple, stage) -> "tuple | None":
        try:
            w, h      = self._viewport_api.resolution
            cam_path  = self._viewport_api.get_active_camera()
            cam_prim  = stage.GetPrimAtPath(str(cam_path))
            if not cam_prim.IsValid():
                return None
            cam_schema = UsdGeom.Camera(cam_prim)
            focal = cam_schema.GetFocalLengthAttr().Get()
            ap_h  = cam_schema.GetHorizontalApertureAttr().Get()
            if not focal or focal == 0:
                return None
            tan_hx = ap_h / (2.0 * focal)
            tan_hy = tan_hx * h / w
            view      = self._viewport_api.view_matrix  # AttributeError → fallback
            cam_space = view.Transform(Gf.Vec3d(*world_pos))
            if cam_space[2] >= 0:
                return None
            d = -cam_space[2]
            x_ndc = cam_space[0] / (d * tan_hx)
            y_ndc = cam_space[1] / (d * tan_hy)
            return (x_ndc + 1) / 2 * w, (1 - y_ndc) / 2 * h
        except AttributeError:
            return self._world_to_screen_fallback(world_pos, stage)
        except Exception:
            return None

    def _world_to_screen_fallback(self, world_pos: tuple, stage) -> "tuple | None":
        try:
            w, h      = self._viewport_api.resolution
            cam_path  = self._viewport_api.get_active_camera()
            cam_prim  = stage.GetPrimAtPath(str(cam_path))
            if not cam_prim.IsValid():
                return None
            cam_schema = UsdGeom.Camera(cam_prim)
            focal = cam_schema.GetFocalLengthAttr().Get()
            ap_h  = cam_schema.GetHorizontalApertureAttr().Get()
            if not focal or focal == 0:
                return None
            tan_hx    = ap_h / (2.0 * focal)
            tan_hy    = tan_hx * h / w
            frustum   = cam_schema.GetCamera(Usd.TimeCode.Default()).frustum
            cam_space = frustum.ComputeViewMatrix().Transform(Gf.Vec3d(*world_pos))
            if cam_space[2] >= 0:
                return None
            d = -cam_space[2]
            x_ndc = cam_space[0] / (d * tan_hx)
            y_ndc = cam_space[1] / (d * tan_hy)
            return (x_ndc + 1) / 2 * w, (1 - y_ndc) / 2 * h
        except Exception:
            return None

    # ------------------------------------------------------------------

    def _add(self, prim_path: str, hex_str: str, pres_str: str,
             plotv_str: str, ui_color: int, pos3d: tuple, gesture_id: str) -> int:
        if len(self._active) >= MAX_OVERLAYS:
            oldest_key = next(iter(self._active))
            self._deactivate(oldest_key)

        used     = set(self._active.values())
        slot_idx = next(i for i in range(MAX_OVERLAYS) if i not in used)
        key      = ColorpickOverlay._gen_key()
        slot     = self._slots[slot_idx]

        slot["world_pos"] = pos3d

        slot["swatch"].style     = {"background_color": ui_color}
        slot["color_dot"].style  = {"background_color": ui_color}
        slot["hex_label"].text   = hex_str
        slot["press_label"].text = pres_str
        slot["plotv_label"].text = plotv_str

        self._remove_slot_marker(slot)
        self._create_slot_marker(slot, prim_path, pos3d)

        self._active[key] = slot_idx
        ColorpickOverlay._key_to_vp[key] = self._vp_api_id
        self._refresh_visible()
        return key

    def _deactivate(self, key: int):
        slot_idx = self._active.pop(key, None)
        if slot_idx is not None:
            slot = self._slots[slot_idx]
            slot["window"].visible = False
            slot["world_pos"]      = None
            self._remove_slot_marker(slot)
        ColorpickOverlay._key_to_vp.pop(key, None)

    def _deactivate_all(self):
        for key in list(self._active.keys()):
            self._deactivate(key)

    def _refresh_visible(self):
        show = ColorpickOverlay._vis_suppress and self._vis_vp
        for slot_idx in self._active.values():
            self._slots[slot_idx]["window"].visible = show

    def _get_slots(self):
        return list(self._slots)

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
        self._update_sub = None
        self._slots.clear()
