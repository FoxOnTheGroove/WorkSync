from collections import OrderedDict
import os
import struct
import tempfile
import zlib
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
LABEL_BG_W       = 140    # pixels
LABEL_BG_H       = 26     # pixels
LINE_THICKNESS   = 2
LINE_COLOR       = 0xFFFFFFFF
MAX_OVERLAYS     = 5


def _make_solid_png(r: int, g: int, b: int) -> bytes:
    """1×1 solid-color RGB PNG (no external deps)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b'IDAT', zlib.compress(bytes([0, r, g, b])))
    iend = chunk(b'IEND', b'')
    return b'\x89PNG\r\n\x1a\n' + ihdr + idat + iend


class ColorpickOverlay:
    _instances: dict  = {}   # vpname  -> ColorpickOverlay
    _key_to_vp: dict  = {}   # key     -> vpname
    _next_key: int    = 0
    _bg_image_path: str = None  # 1×1 sky-blue PNG, shared across all instances

    @classmethod
    def _gen_key(cls) -> int:
        k = cls._next_key
        cls._next_key += 1
        return k

    @classmethod
    def _ensure_bg_image(cls):
        if cls._bg_image_path is None:
            png = _make_solid_png(0x87, 0xCE, 0xEB)  # sky blue
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            tmp.write(png)
            tmp.close()
            cls._bg_image_path = tmp.name

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
        if not cls._instances:
            if cls._bg_image_path:
                try:
                    os.unlink(cls._bg_image_path)
                except OSError:
                    pass
                cls._bg_image_path = None

    @classmethod
    def _get_or_create(cls, vpname: str) -> "ColorpickOverlay":
        if vpname not in cls._instances:
            cls._instances[vpname] = cls(vpname)
        return cls._instances[vpname]

    # ------------------------------------------------------------------
    # 인스턴스  (뷰포트 1개당 1인스턴스 / MAX_OVERLAYS개 슬롯 관리)
    # ------------------------------------------------------------------

    def __init__(self, vpname: str):
        self._vpname     = vpname
        self._scene_view = None
        # slots[i] = {"root": sc.Transform, "label": sc.Label, "marker_path": str|None}
        self._slots: list[dict] = []
        # key -> slot_idx  (OrderedDict → 삽입순 = FIFO)
        self._active: OrderedDict[int, int] = OrderedDict()
        self._setup(vpname)

    def _setup(self, vpname: str):
        try:
            ColorpickOverlay._ensure_bg_image()
            vph = hytwin_vp_wg.ViewportWidgetHost().get_instance_by_viewport_name(vpname)
            self._scene_view = vph.scene_view
            self._create_slots()
        except Exception as e:
            print(f"[ColorpickOverlay] setup failed for '{vpname}': {e}")

    def _create_slots(self):
        """MAX_OVERLAYS 개의 씬 아이템 세트를 미리 생성 (모두 숨김)."""
        for _ in range(MAX_OVERLAYS):
            slot = {"root": None, "label": None, "marker_path": None}
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
                        sc.Image(
                            ColorpickOverlay._bg_image_path,
                            width=LABEL_BG_W,
                            height=LABEL_BG_H,
                        )
                        slot["label"] = sc.Label(
                            "",
                            size=LABEL_SIZE,
                            alignment=ui.Alignment.CENTER,
                        )
            self._slots.append(slot)

    # ------------------------------------------------------------------

    def _add(self, prim_path: str, display_text: str, pos3d: tuple) -> int:
        """슬롯에 오버레이 추가. 풀이 꽉 차면 가장 오래된 것을 FIFO 방출."""
        if len(self._active) >= MAX_OVERLAYS:
            oldest_key = next(iter(self._active))
            self._deactivate(oldest_key)

        # 비어 있는 슬롯 인덱스 선택
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
        self._scene_view = None
        self._slots.clear()
