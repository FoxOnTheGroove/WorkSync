from collections import OrderedDict

import omni.kit.app
import omni.usd
import omni.ui as ui
from pxr import Usd, UsdGeom, Gf
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg

from .ebs_simulate_service import EbsSimulateService

PANEL_W        = 150
PANEL_H        = 54
PANEL_BG       = 0xFFFFFFFF
PANEL_PAD      = 6
ITEM_GAP       = 4
LABEL_SIZE     = 13
PANEL_OFFSET_X = 12
PANEL_OFFSET_Y = 12
MAX_OVERLAYS   = 8

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


class EbsSimulateOverlay:
    """EBS 시뮬레이션 결과를 뷰포트 위에 띄우는 오버레이.

    값은 EbsSimulateService(공개 API)로만 조회한다.
    """

    _instances: dict = {}     # vp_name -> EbsSimulateOverlay
    _key_to_vp: dict = {}     # key(int) -> vp_name
    _next_key: int   = 0

    @classmethod
    def _gen_key(cls) -> int:
        k = cls._next_key
        cls._next_key += 1
        return k

    # ── 공개 API ─────────────────────────────────────────────────────────────

    @classmethod
    def on(cls, vp_name: str, prim_path: str, pos3d: tuple = None) -> "int | None":
        """대상 프림에 오버레이 패널을 띄우고 key를 반환한다."""
        if pos3d is None:
            pos3d = EbsSimulateService.get_anchor(prim_path)
        if pos3d is None:
            return None
        inst = cls._get_or_create(vp_name)
        return inst._add(prim_path, pos3d)

    @classmethod
    def off(cls, identifier) -> None:
        """key(int)면 해당 패널, vp_name(str)이면 뷰포트 전체 패널을 끈다."""
        if isinstance(identifier, int):
            vp_name = cls._key_to_vp.get(identifier)
            if vp_name and vp_name in cls._instances:
                cls._instances[vp_name]._deactivate(identifier)
        elif isinstance(identifier, str):
            if identifier in cls._instances:
                cls._instances[identifier]._deactivate_all()

    @classmethod
    def set_visible(cls, vp_name: str, visible: bool) -> None:
        if vp_name in cls._instances:
            cls._instances[vp_name]._set_visible_all(visible)

    @classmethod
    def set_visible_all(cls, visible: bool) -> None:
        for inst in cls._instances.values():
            inst._set_visible_all(visible)

    @classmethod
    def refresh(cls) -> None:
        """공개 API에서 최신 값을 다시 읽어 모든 패널 라벨을 갱신한다."""
        for inst in cls._instances.values():
            inst._refresh_labels()

    @classmethod
    def destroy(cls, vp_name: str = None) -> None:
        if vp_name:
            inst = cls._instances.pop(vp_name, None)
            if inst:
                inst._destroy()
        else:
            for inst in list(cls._instances.values()):
                inst._destroy()
            cls._instances.clear()

    @classmethod
    def _get_or_create(cls, vp_name: str) -> "EbsSimulateOverlay":
        if vp_name not in cls._instances:
            cls._instances[vp_name] = cls(vp_name)
        return cls._instances[vp_name]

    # ── 인스턴스 (뷰포트 1개당 1인스턴스 / MAX_OVERLAYS개 슬롯) ───────────────

    def __init__(self, vp_name: str):
        self._vp_name      = vp_name
        self._viewport_api = None
        self._frame        = None
        self._slots: list[dict] = []
        self._active: "OrderedDict[int, int]" = OrderedDict()
        self._update_sub = None
        self._setup(vp_name)

    def _setup(self, vp_name: str) -> None:
        try:
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vp_name)
            self._viewport_api = vph.viewport.viewport_api
            self._frame        = vph.frame
            self._create_slots()
            self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._on_update, name=f"ebs_overlay_{vp_name}"
            )
        except Exception as e:
            print(f"[ebs] overlay setup failed for '{vp_name}': {e}")

    def _create_slots(self) -> None:
        _lbl_style = {"color": 0xFF202020, "font_size": LABEL_SIZE}

        for i in range(MAX_OVERLAYS):
            win = ui.Window(
                f"_ebsoverlay_{self._vp_name}_{i}",
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
                        ui.Spacer(width=PANEL_PAD)
                        with ui.VStack(spacing=ITEM_GAP):
                            ui.Spacer(height=PANEL_PAD)
                            name_lbl  = ui.Label("-", style=_lbl_style)
                            value_lbl = ui.Label("value -", style=_lbl_style)
                            ui.Spacer(height=PANEL_PAD)
                        ui.Spacer(width=PANEL_PAD)

            self._slots.append({
                "window":      win,
                "name_label":  name_lbl,
                "value_label": value_lbl,
                "world_pos":   None,
                "prim_path":   None,
            })

    # ── 갱신 ─────────────────────────────────────────────────────────────────

    def _on_update(self, event) -> None:
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
                slot["window"].position_x = max(ox, min(ox + dw - PANEL_W, px + PANEL_OFFSET_X))
                slot["window"].position_y = max(oy, min(oy + dh - PANEL_H, py + PANEL_OFFSET_Y))
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        for slot_idx in self._active.values():
            slot = self._slots[slot_idx]
            path = slot["prim_path"]
            if not path:
                continue
            value = EbsSimulateService.get_value(path)
            slot["value_label"].text = "value -" if value is None else f"value {value:.3f}"

    def _viewport_offset(self) -> tuple:
        try:
            return self._frame.screen_position_x, self._frame.screen_position_y
        except Exception:
            return 0.0, 0.0

    def _world_to_screen(self, world_pos: tuple, stage) -> "tuple | None":
        try:
            w, h     = self._viewport_api.resolution
            cam_path = self._viewport_api.get_active_camera()
            cam_prim = stage.GetPrimAtPath(str(cam_path))
            if not cam_prim.IsValid():
                return None
            cam_schema = UsdGeom.Camera(cam_prim)
            focal = cam_schema.GetFocalLengthAttr().Get()
            ap_h  = cam_schema.GetHorizontalApertureAttr().Get()
            if not focal or focal == 0:
                return None
            tan_hx = ap_h / (2.0 * focal)
            tan_hy = tan_hx * h / w
            try:
                view = self._viewport_api.view_matrix
            except AttributeError:
                view = cam_schema.GetCamera(Usd.TimeCode.Default()).frustum.ComputeViewMatrix()
            cam_space = view.Transform(Gf.Vec3d(*world_pos))
            if cam_space[2] >= 0:
                return None
            d = -cam_space[2]
            x_ndc = cam_space[0] / (d * tan_hx)
            y_ndc = cam_space[1] / (d * tan_hy)
            return (x_ndc + 1) / 2 * w, (1 - y_ndc) / 2 * h
        except Exception:
            return None

    # ── 슬롯 ─────────────────────────────────────────────────────────────────

    def _add(self, prim_path: str, pos3d: tuple) -> int:
        if len(self._active) >= MAX_OVERLAYS:
            self._deactivate(next(iter(self._active)))

        used     = set(self._active.values())
        slot_idx = next(i for i in range(MAX_OVERLAYS) if i not in used)
        slot     = self._slots[slot_idx]

        slot["world_pos"]       = pos3d
        slot["prim_path"]       = prim_path
        slot["name_label"].text = prim_path.rsplit("/", 1)[-1]
        slot["window"].visible  = True

        key = EbsSimulateOverlay._gen_key()
        self._active[key] = slot_idx
        EbsSimulateOverlay._key_to_vp[key] = self._vp_name
        self._refresh_labels()
        return key

    def _deactivate(self, key: int) -> None:
        slot_idx = self._active.pop(key, None)
        if slot_idx is not None:
            slot = self._slots[slot_idx]
            slot["window"].visible = False
            slot["world_pos"]      = None
            slot["prim_path"]      = None
        EbsSimulateOverlay._key_to_vp.pop(key, None)

    def _deactivate_all(self) -> None:
        for key in list(self._active.keys()):
            self._deactivate(key)

    def _set_visible_all(self, visible: bool) -> None:
        for slot_idx in self._active.values():
            self._slots[slot_idx]["window"].visible = visible

    def _destroy(self) -> None:
        self._deactivate_all()
        for slot in self._slots:
            win = slot.get("window")
            if win:
                win.destroy()
        self._update_sub = None
        self._slots.clear()
