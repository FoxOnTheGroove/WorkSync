"""
CAD Converter UI

- Source 경로 입력 (STEP 등)
- Dest 경로 입력 (출력 USD)
- (Prim 경로 입력은 지금은 제외)
- Convert Options 선택창 (Up Axis / Tess LOD / Instancing / Materials)
- Convert 버튼
- Load 버튼 (+ Auto Load 체크박스)
"""

import omni.ui as ui
import omni.kit.async_engine

from . import std_convert


class CadConverterUI:

    def __init__(self):
        self._window: ui.Window | None = None
        self._src_field: ui.StringField | None = None
        self._dest_field: ui.StringField | None = None
        self._status_label: ui.Label | None = None

        # 옵션 위젯
        self._up_axis_combo: ui.ComboBox | None = None
        self._lod_combo: ui.ComboBox | None = None
        self._instancing_cb: ui.CheckBox | None = None
        self._materials_cb: ui.CheckBox | None = None
        self._autoload_cb: ui.CheckBox | None = None

        self._up_axis_labels = list(std_convert.UP_AXIS_CHOICES.keys())
        self._lod_labels = list(std_convert.TESS_LOD_CHOICES.keys())

    # ---------------- build ----------------

    def build_ui(self):
        self._window = ui.Window("CAD Converter", width=480, height=360)
        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):

                # --- 경로 입력 ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Source:", width=70)
                    self._src_field = ui.StringField()
                    self._src_field.model.set_value("C:/data/model.stp")

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Dest:", width=70)
                    self._dest_field = ui.StringField()
                    self._dest_field.model.set_value("C:/data/out/model.usd")

                ui.Spacer(height=4)
                ui.Label("Convert Options", style={"font_size": 14})
                ui.Separator(height=2)

                # --- Up Axis ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Up Axis:", width=120)
                    self._up_axis_combo = ui.ComboBox(0, *self._up_axis_labels)

                # --- Tessellation LOD ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Tessellation LOD:", width=120)
                    # 기본값 Medium(index 2)
                    self._lod_combo = ui.ComboBox(2, *self._lod_labels)

                # --- Instancing ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Instancing:", width=120)
                    self._instancing_cb = ui.CheckBox(width=20)
                    self._instancing_cb.model.set_value(False)

                # --- Materials ---
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Use Materials:", width=120)
                    self._materials_cb = ui.CheckBox(width=20)
                    self._materials_cb.model.set_value(False)

                ui.Spacer(height=6)

                # --- 버튼 ---
                with ui.HStack(height=28, spacing=8):
                    ui.Button("Convert", clicked_fn=self._on_convert)
                    ui.Button("Load", clicked_fn=self._on_load)
                    ui.Label("Auto Load", width=70)
                    self._autoload_cb = ui.CheckBox(width=20)
                    self._autoload_cb.model.set_value(True)

                self._status_label = ui.Label("Status: ready", height=20)

    # ---------------- options ----------------

    def _gather_options(self) -> dict:
        up_idx = self._up_axis_combo.model.get_item_value_model().get_value_as_int()
        lod_idx = self._lod_combo.model.get_item_value_model().get_value_as_int()
        up_label = self._up_axis_labels[up_idx]
        lod_label = self._lod_labels[lod_idx]

        return std_convert.build_options(
            up_axis=std_convert.UP_AXIS_CHOICES[up_label],
            tess_lod=std_convert.TESS_LOD_CHOICES[lod_label],
            instancing=self._instancing_cb.model.get_value_as_bool(),
            use_materials=self._materials_cb.model.get_value_as_bool(),
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
            result = await std_convert.convert_async(src, dest, options)
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
        self._set_status(f"loaded -> {prim_path}")

    # ---------------- util ----------------

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
