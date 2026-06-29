"""
CAD Converter UI

- Source 경로 입력 (STEP 등)
- Dest 경로 입력 (출력 USD)
- (Prim 경로 입력은 지금은 제외)
- Convert Options 선택창 (Up Axis / Tess LOD / Meters Per Unit / Instancing / Materials)
- Convert 버튼
- Load 버튼 (+ Auto Load 체크박스)
"""

import omni.ui as ui
import omni.kit.async_engine

from . import std_convert


# ---------------- styles ----------------

LABEL_W = 130          # 옵션 라벨 고정 폭
ROW_H = 26

_SECTION_TITLE = {"font_size": 15, "color": 0xFFCCCCCC}
_HINT = {"font_size": 12, "color": 0xFF888888}

_GROUP_FRAME = {
    "Frame": {
        "background_color": 0xFF2A2A2A,
        "border_color":     0xFF4A4A4A,
        "border_width":     1,
        "border_radius":    6,
        "padding":          8,
        "margin_height":    2,
    }
}

_CONVERT_BTN = {"Button": {"background_color": 0xFF3B7A3B, "border_radius": 4}}
_LOAD_BTN    = {"Button": {"background_color": 0xFF3B5A7A, "border_radius": 4}}
_CLEAR_BTN   = {"Button": {"background_color": 0xFF7A3B3B, "border_radius": 4}}

_STATUS = {"font_size": 12, "color": 0xFFAACCAA}


class CadConverterUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._src_field: ui.StringField | None = None
        self._dest_field: ui.StringField | None = None
        self._status_label: ui.Label | None = None

        # 옵션 위젯
        self._up_axis_combo: ui.ComboBox | None = None
        self._lod_combo: ui.ComboBox | None = None
        self._mpu_combo: ui.ComboBox | None = None
        self._instancing_cb: ui.CheckBox | None = None
        self._materials_cb: ui.CheckBox | None = None
        self._autoload_cb: ui.CheckBox | None = None

        self._up_axis_labels = list(std_convert.UP_AXIS_CHOICES.keys())
        self._lod_labels = list(std_convert.TESS_LOD_CHOICES.keys())
        self._mpu_labels = list(std_convert.METERS_PER_UNIT_CHOICES.keys())

        self._loaded_prims: list[str] = []   # 로드로 생성된 prim 경로 추적

    # ---------------- build ----------------

    def build_ui(self):
        self._window = ui.Window("CAD Converter", width=460, height=400)
        with self._window.frame:
            with ui.VStack(spacing=10, height=0, style={"margin": 10}):
                self._build_paths()
                self._build_options()
                self._build_actions()
                self._build_status()

    def _build_paths(self):
        ui.Label("Files", style=_SECTION_TITLE)
        with ui.Frame(style=_GROUP_FRAME):
            with ui.VStack(spacing=6, height=0):
                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Source", width=70)
                    self._src_field = ui.StringField()
                    self._src_field.model.set_value("C:/data/model.stp")
                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Dest", width=70)
                    self._dest_field = ui.StringField()
                    self._dest_field.model.set_value("C:/data/out/model.usd")

    def _build_options(self):
        ui.Label("Convert Options", style=_SECTION_TITLE)
        with ui.Frame(style=_GROUP_FRAME):
            with ui.VStack(spacing=6, height=0):
                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Up Axis", width=LABEL_W)
                    self._up_axis_combo = ui.ComboBox(0, *self._up_axis_labels)

                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Tessellation LOD", width=LABEL_W)
                    self._lod_combo = ui.ComboBox(2, *self._lod_labels)  # Medium

                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Meters Per Unit", width=LABEL_W)
                    self._mpu_combo = ui.ComboBox(0, *self._mpu_labels)  # Meter

                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Instancing", width=LABEL_W)
                    self._instancing_cb = ui.CheckBox(width=20)
                    self._instancing_cb.model.set_value(False)
                    ui.Spacer()

                with ui.HStack(height=ROW_H, spacing=6):
                    ui.Label("Use Materials", width=LABEL_W)
                    self._materials_cb = ui.CheckBox(width=20)
                    self._materials_cb.model.set_value(False)
                    ui.Spacer()

    def _build_actions(self):
        with ui.HStack(height=32, spacing=8):
            ui.Button("Convert", clicked_fn=self._on_convert, style=_CONVERT_BTN)
            ui.Button("Load", clicked_fn=self._on_load, style=_LOAD_BTN)
            ui.Button("Clear", clicked_fn=self._on_clear, style=_CLEAR_BTN)
            with ui.HStack(width=110, spacing=6):
                self._autoload_cb = ui.CheckBox(width=20)
                self._autoload_cb.model.set_value(True)
                ui.Label("Auto Load")

    def _build_status(self):
        ui.Separator(height=2)
        self._status_label = ui.Label("Status: ready", height=20, style=_STATUS)

    # ---------------- options ----------------

    def _combo_label(self, combo: ui.ComboBox, labels: list) -> str:
        idx = combo.model.get_item_value_model().get_value_as_int()
        return labels[idx]

    def _gather_options(self) -> dict:
        up_label = self._combo_label(self._up_axis_combo, self._up_axis_labels)
        lod_label = self._combo_label(self._lod_combo, self._lod_labels)
        mpu_label = self._combo_label(self._mpu_combo, self._mpu_labels)

        return std_convert.build_options(
            up_axis=std_convert.UP_AXIS_CHOICES[up_label],
            tess_lod=std_convert.TESS_LOD_CHOICES[lod_label],
            instancing=self._instancing_cb.model.get_value_as_bool(),
            use_materials=self._materials_cb.model.get_value_as_bool(),
            meters_per_unit=std_convert.METERS_PER_UNIT_CHOICES[mpu_label],
        )

    # ---------------- callbacks ----------------

    def _on_convert(self):
        src = self._src_field.model.get_value_as_string().strip()
        dest = self._dest_field.model.get_value_as_string().strip()
        if not src or not dest:
            self._set_status("ERROR: source/dest 경로를 입력하세요")
            return
        options = self._gather_options()
        self._set_status("converting...")
        omni.kit.async_engine.run_coroutine(self._run_convert(src, dest, options))

    async def _run_convert(self, src: str, dest: str, options: dict):
        try:
            await std_convert.convert_async(src, dest, options)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: convert 실패 - {e}")
            return

        self._set_status(f"converted -> {dest}")

        if self._autoload_cb.model.get_value_as_bool():
            self._load(dest)

    def _on_load(self):
        dest = self._dest_field.model.get_value_as_string().strip()
        if not dest:
            self._set_status("ERROR: dest 경로를 입력하세요")
            return
        self._load(dest)

    def _load(self, dest: str):
        try:
            prim_path = std_convert.load_into_stage(dest)  # prim 경로 지정은 지금은 제외
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: load 실패 - {e}")
            return
        if prim_path:
            self._loaded_prims.append(prim_path)
        self._set_status(f"loaded -> {prim_path}")

    def _on_clear(self):
        if not self._loaded_prims:
            self._set_status("지울 로드 항목이 없습니다")
            return
        try:
            std_convert.remove_prims(self._loaded_prims)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"ERROR: clear 실패 - {e}")
            return
        count = len(self._loaded_prims)
        self._loaded_prims.clear()
        self._set_status(f"cleared {count} prim(s)")

    # ---------------- util ----------------

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
