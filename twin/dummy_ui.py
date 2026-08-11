"""twin_viewer_service 의 외부 API만 사용하는 더미 UI.

구현부(TwinViewer)를 직접 import 하지 않는다 — service 계층 검증용.
"""

import omni.ui as ui

from . import twin_viewer_service as twin

_PANEL_STYLE = {
    "background_color": 0xFF1E1E1E,
    "border_color":     0xFF555555,
    "border_width":     1,
    "border_radius":    4,
    "padding":          4,
}

_STATUS_STYLE = {"font_size": 12, "color": 0xFFAAAAAA}
_ERROR_STYLE  = {"font_size": 12, "color": 0xFF6666EE}

_FULL_DOMAIN = "(full domain)"


class TwinViewerUI:

    def __init__(self):
        self._window       = None
        self._path_field   = None
        self._prim_field   = None
        self._width_model  = None
        self._rom_combo    = None
        self._ns_combo     = None
        self._inputs_stack = None
        self._status_label = None

        self._rom_names: list[str] = []
        self._ns_names:  list[str] = []
        self._input_models: dict = {}               # 입력 이름 → ui 모델

    def build_ui(self):
        self._window = ui.Window("Twin Viewer (dummy)", width=360, height=480)

        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                with ui.HStack(height=24, spacing=4):
                    ui.Label(".twin", width=52)
                    self._path_field = ui.StringField()
                    self._path_field.model.set_value("/path/to/model.twin")
                    ui.Button("Load", width=50, clicked_fn=self._on_load)
                    ui.Button("Unload", width=60, clicked_fn=self._on_unload)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("rom", width=52)
                    self._rom_combo = ui.ComboBox(0)
                    self._rom_combo.model.add_item_changed_fn(self._on_rom_changed)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("region", width=52)
                    self._ns_combo = ui.ComboBox(0, _FULL_DOMAIN)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("prim", width=52)
                    self._prim_field = ui.StringField()
                    self._prim_field.model.set_value(twin.get_prim_path())
                    ui.Button("Set", width=40, clicked_fn=self._on_set_prim_path)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("width", width=52)
                    self._width_model = ui.FloatDrag(min=0.001, max=1000.0, step=0.05).model
                    self._width_model.set_value(twin.get_point_width())
                    self._width_model.add_value_changed_fn(self._on_width_changed)

                ui.Label("inputs", height=20, style=_STATUS_STYLE)
                with ui.ScrollingFrame(height=ui.Fraction(1), style=_PANEL_STYLE):
                    self._inputs_stack = ui.VStack(spacing=2)

                with ui.HStack(height=26, spacing=4):
                    ui.Button("Evaluate", height=24, clicked_fn=self._on_evaluate)
                    ui.Button("Clear", width=60, height=24, clicked_fn=self._on_clear)
                    ui.Button("Status", width=60, height=24, clicked_fn=self._on_status)

                self._status_label = ui.Label("", height=32, word_wrap=True, style=_STATUS_STYLE)

        twin.set_on_loaded(self._on_loaded_event)
        twin.set_on_evaluated(self._on_evaluated_event)

        self._rebuild_inputs()
        self._set_status("Not loaded")

    def destroy(self):
        twin.set_on_loaded(None)
        twin.set_on_evaluated(None)
        self._input_models = {}
        if self._window:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------------ 콜백

    def _on_load(self):
        path = self._path_field.model.get_value_as_string().strip().strip('"')
        if not twin.load_twin(path):
            self._refresh_all()
            self._set_status("Load failed — 콘솔 로그를 확인하세요.", error=True)
            return
        self._refresh_all()
        self._set_status(f"Loaded: {twin.get_rom_name()} / {twin.get_field_name()}")

    def _on_unload(self):
        twin.unload_twin()
        self._refresh_all()
        self._set_status("Unloaded")

    def _on_rom_changed(self, model, item):
        index = model.get_item_value_model().get_value_as_int()
        if 0 <= index < len(self._rom_names):
            if twin.select_rom(self._rom_names[index]):
                self._rebuild_named_selections()
                self._rebuild_inputs()

    def _on_set_prim_path(self):
        twin.set_prim_path(self._prim_field.model.get_value_as_string().strip())
        self._set_status(f"prim path: {twin.get_prim_path()}")

    def _on_width_changed(self, model):
        twin.set_point_width(model.get_value_as_float())

    def _on_evaluate(self):
        inputs = {name: m.get_value_as_float() for name, m in self._input_models.items()}
        if not twin.evaluate(inputs, self._selected_named_selection()):
            self._set_status("Evaluate failed — 콘솔 로그를 확인하세요.", error=True)
            return
        self._set_status(f"Evaluated: {twin.get_point_count()} points, "
                         f"range {twin.get_value_range()}")

    def _on_clear(self):
        twin.clear()
        self._set_status("Cleared")

    def _on_status(self):
        parts = [f"{k}={v}" for k, v in twin.get_status().items()]
        self._set_status(" | ".join(parts))

    # ------------------------------------------------------------------ 이벤트 훅

    def _on_loaded_event(self, twin_file: str):
        print(f"[twin] on_loaded: {twin_file}")

    def _on_evaluated_event(self, point_count: int, value_range):
        print(f"[twin] on_evaluated: {point_count} points, range={value_range}")

    # ------------------------------------------------------------------ 내부

    def _selected_named_selection(self) -> "str | None":
        # 인덱스 0은 항상 전체 도메인
        index = self._ns_combo.model.get_item_value_model().get_value_as_int()
        if index <= 0 or index > len(self._ns_names):
            return None
        return self._ns_names[index - 1]

    def _refresh_all(self):
        self._rebuild_roms()
        self._rebuild_named_selections()
        self._rebuild_inputs()

    def _rebuild_roms(self):
        self._rom_names = twin.get_rom_names()
        self._fill_combo(self._rom_combo, self._rom_names)

    def _rebuild_named_selections(self):
        self._ns_names = twin.get_named_selections()
        self._fill_combo(self._ns_combo, [_FULL_DOMAIN] + self._ns_names)

    @staticmethod
    def _fill_combo(combo, labels: list):
        # ComboBox 항목은 교체가 안 되므로 지우고 다시 채운다
        model = combo.model
        for item in model.get_item_children():
            model.remove_item(item)
        for label in labels:
            model.append_child_item(None, ui.SimpleStringModel(label))
        model.get_item_value_model().set_value(0)

    def _rebuild_inputs(self):
        self._inputs_stack.clear()
        self._input_models = {}

        defaults = twin.get_input_defaults()
        if not defaults:
            with self._inputs_stack:
                ui.Label("(no inputs)", height=20, style=_STATUS_STYLE)
            return

        with self._inputs_stack:
            for name, value in defaults.items():
                with ui.HStack(height=22, spacing=4):
                    ui.Label(name, width=ui.Fraction(1), tooltip=name)
                    field = ui.FloatDrag(width=100, step=0.1)
                    try:
                        field.model.set_value(float(value))
                    except (TypeError, ValueError):
                        field.model.set_value(0.0)
                    self._input_models[name] = field.model

    def _set_status(self, text: str, error: bool = False):
        if self._status_label:
            self._status_label.text = f"Status: {text}"
            self._status_label.set_style(_ERROR_STYLE if error else _STATUS_STYLE)
