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
    "padding":          3,
}

_HEAD_STYLE   = {"font_size": 12, "color": 0xFF888888}
# omni.ui 는 style 인자를 순회하므로 None 을 넘길 수 없다 — 기본값도 dict 여야 한다
_NAME_STYLE   = {"font_size": 12, "color": 0xFFDDDDDD}
_DIM_STYLE    = {"font_size": 12, "color": 0xFFAAAAAA}
_MODE_STYLE   = {"font_size": 12, "color": 0xFF7FD4FF}   # 필드를 만드는 모드 계수
_STATUS_STYLE = {"font_size": 12, "color": 0xFFAAAAAA}
_ERROR_STYLE  = {"font_size": 12, "color": 0xFF6666EE}

_FULL_DOMAIN = "(full domain)"
_ROW_H = 20

# 기여도가 이 값 미만인 입력은 한 줄에 다 넣으면 읽기 힘들어 툴팁으로만 보여준다
_SHARE_CUTOFF = 0.05


class TwinViewerUI:

    def __init__(self):
        self._window        = None
        self._path_field    = None
        self._ns_combo      = None
        self._width_model   = None
        self._step_model    = None
        self._time_label    = None
        self._inputs_stack  = None
        self._outputs_stack = None
        self._status_label  = None

        self._ns_names: list[str] = []
        self._input_models: dict = {}               # 입력 이름 → ui 모델

    def build_ui(self):
        self._window = ui.Window("Twin Viewer", width=460, height=620)

        with self._window.frame:
            # 창 전체를 하나의 스크롤로 묶는다 — 입력/출력 칸이 각자 잘리지 않게
            with ui.ScrollingFrame():
                with ui.VStack(spacing=5, style={"margin": 8}):
                    with ui.HStack(height=22, spacing=4):
                        ui.Label(".twin", width=48)
                        self._path_field = ui.StringField()
                        self._path_field.model.set_value(
                            r"C:\Users\OPTI\Documents\HXVelVectorTBROM_23R2.twin")
                        ui.Button("Load", width=48, clicked_fn=self._on_load)

                    with ui.HStack(height=22, spacing=4):
                        ui.Label("region", width=48)
                        self._ns_combo = ui.ComboBox(0, _FULL_DOMAIN)
                        self._ns_combo.model.add_item_changed_fn(self._on_region_changed)

                    with ui.HStack(height=22, spacing=4):
                        ui.Label("width", width=48,
                                 tooltip="0 이면 바운딩박스에서 자동 산출")
                        self._width_model = ui.FloatDrag(min=0.0, max=1000.0,
                                                         step=0.05).model
                        self._width_model.set_value(twin.get_point_width())

                    ui.Label("inputs", height=16, style=_HEAD_STYLE)
                    with ui.ZStack(style=_PANEL_STYLE):
                        ui.Rectangle()
                        self._inputs_stack = ui.VStack(spacing=1)

                    with ui.HStack(height=24, spacing=4):
                        ui.Button("Evaluate", height=22, clicked_fn=self._on_evaluate)
                        ui.Button("Clear", width=56, height=22, clicked_fn=self._on_clear)

                    with ui.HStack(height=24, spacing=4):
                        ui.Label("step (s)", width=48,
                                 tooltip="한 프레임에 전진할 트윈 시각(초). "
                                         "0 이면 트윈의 기본값을 쓴다")
                        self._step_model = ui.FloatDrag(min=0.0, max=1e6, step=0.0005,
                                                        width=80).model
                        self._step_model.set_value(0.0)
                        ui.Button("▶ Play", width=64, height=22, clicked_fn=self._on_play)
                        ui.Button("❚❚ Pause", width=64, height=22,
                                  clicked_fn=self._on_pause)
                        ui.Button("■ Stop", width=64, height=22, clicked_fn=self._on_stop)
                        self._time_label = ui.Label("t = 0", width=ui.Fraction(1),
                                                    style=_DIM_STYLE)

                    ui.Label("outputs", height=16, style=_HEAD_STYLE)
                    with ui.ZStack(style=_PANEL_STYLE):
                        ui.Rectangle()
                        self._outputs_stack = ui.VStack(spacing=1)

                    self._status_label = ui.Label("", height=32, word_wrap=True,
                                                  style=_STATUS_STYLE)

        twin.set_on_evaluated(self._on_evaluated_event)

        self._rebuild_inputs()
        self._rebuild_outputs()
        self._set_status("Not loaded")

    def destroy(self):
        twin.pause()                    # UI 가 사라진 뒤에도 루프가 도는 걸 막는다
        twin.set_on_evaluated(None)
        self._input_models = {}
        if self._window:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------------ 콜백

    def _on_load(self):
        path = self._path_field.model.get_value_as_string().strip().strip('"')
        self._apply_display_settings()

        if not twin.load_twin(path):
            self._refresh_all()
            self._set_status("Load failed — 콘솔 로그를 확인하세요.", error=True)
            return

        self._refresh_all()
        if not twin.is_output_field_connected():
            self._set_status("경고: TBROM 출력 필드가 트윈 출력에 연결돼 있지 않습니다. "
                             "Twin Builder에서 다시 export해야 합니다.", error=True)
            return
        self._set_status(f"{twin.get_rom_name()} / {twin.get_field_name()} — "
                         f"{twin.get_point_count()} points (지오메트리만)  |  "
                         f"기본 step {twin.get_default_step_size():g}s, "
                         f"end {twin.get_default_end_time():g}s")

    def _on_region_changed(self, model, item):
        if twin.is_loaded():
            self._apply_display_settings()
            twin.show_geometry(self._selected_named_selection())
            self._set_status(f"region: {self._selected_named_selection() or _FULL_DOMAIN} — "
                             f"{twin.get_point_count()} points (지오메트리만)")

    def _on_evaluate(self):
        self._apply_display_settings()
        inputs = {name: m.get_value_as_float() for name, m in self._input_models.items()}
        if not twin.evaluate(inputs, self._selected_named_selection()):
            self._set_status("Evaluate failed — 콘솔 로그를 확인하세요.", error=True)
            return

        self._rebuild_outputs()
        lo, hi = twin.get_value_range()
        self._set_status(f"{twin.get_point_count()} points, "
                         f"{twin.get_field_name()} {lo:.4g} ~ {hi:.4g}")

    def _on_clear(self):
        twin.clear()
        self._update_time_label()
        self._set_status("Cleared")

    def _on_play(self):
        if not twin.play(self._step_model.get_value_as_float(),
                         self._selected_named_selection()):
            self._set_status("Play failed — 콘솔 로그를 확인하세요.", error=True)
            return
        step = self._step_model.get_value_as_float() or twin.get_default_step_size()
        self._set_status(f"Playing — step {step:g}s, "
                         f"end {twin.get_default_end_time():g}s")

    def _on_pause(self):
        twin.pause()
        self._update_time_label()
        self._set_status(f"Paused at t = {twin.get_sim_time():g}s")

    def _on_stop(self):
        twin.stop(self._selected_named_selection())
        self._rebuild_outputs()
        self._update_time_label()
        self._set_status("Stopped — t = 0")

    def _on_evaluated_event(self, point_count: int, value_range):
        # 재생 중에는 매 프레임 불린다. 콘솔 출력은 넣지 않는다.
        self._update_time_label()

    def _update_time_label(self):
        if self._time_label:
            self._time_label.text = f"t = {twin.get_sim_time():g}"

    # ------------------------------------------------------------------ 내부

    def _apply_display_settings(self):
        twin.set_point_width(self._width_model.get_value_as_float())

    def _selected_named_selection(self) -> "str | None":
        # 인덱스 0은 항상 전체 도메인
        index = self._ns_combo.model.get_item_value_model().get_value_as_int()
        if index <= 0 or index > len(self._ns_names):
            return None
        return self._ns_names[index - 1]

    def _refresh_all(self):
        self._rebuild_named_selections()
        self._rebuild_inputs()
        self._rebuild_outputs()

    def _rebuild_named_selections(self):
        self._ns_names = twin.get_named_selections()
        # ComboBox 항목은 교체가 안 되므로 지우고 다시 채운다
        model = self._ns_combo.model
        for item in model.get_item_children():
            model.remove_item(item)
        for label in [_FULL_DOMAIN] + self._ns_names:
            model.append_child_item(None, ui.SimpleStringModel(label))
        model.get_item_value_model().set_value(0)

    def _rebuild_inputs(self):
        self._inputs_stack.clear()
        self._input_models = {}

        inputs = twin.get_inputs()
        # 스크롤 대신 항목 수만큼 높이를 잡아 전부 보이게 한다
        self._inputs_stack.height = ui.Pixel(_ROW_H * max(1, len(inputs)) + 6)
        if not inputs:
            with self._inputs_stack:
                ui.Label("(no inputs)", height=_ROW_H, style=_DIM_STYLE)
            return

        with self._inputs_stack:
            for name, value in inputs.items():
                with ui.HStack(height=_ROW_H, spacing=4):
                    ui.Label(name, width=ui.Fraction(1), tooltip=name)
                    field = ui.FloatDrag(width=100, step=0.1)
                    try:
                        field.model.set_value(float(value))
                    except (TypeError, ValueError):
                        field.model.set_value(0.0)
                    self._input_models[name] = field.model

    def _rebuild_outputs(self):
        self._outputs_stack.clear()

        outputs = twin.get_outputs()
        sources = twin.get_output_sources()
        self._outputs_stack.height = ui.Pixel(_ROW_H * max(1, len(outputs) + 1) + 6)
        if not outputs:
            with self._outputs_stack:
                ui.Label("(no outputs)", height=_ROW_H, style=_DIM_STYLE)
            return

        with self._outputs_stack:
            with ui.HStack(height=_ROW_H, spacing=4):
                ui.Label("name", width=ui.Fraction(1), style=_HEAD_STYLE)
                ui.Label("value", width=90, style=_HEAD_STYLE)
                ui.Label("driven by (기여도)", width=ui.Fraction(1.4), style=_HEAD_STYLE)

            for name, value in outputs.items():
                # outField_mode_* 가 TBROM 모드 계수 — 이 값들이 필드를 만든다
                is_mode = name.startswith("outField_mode_")
                src = sources.get(name)
                if src is None:
                    src_text = full_text = "?"          # 아직 분석 전
                elif not src:
                    src_text = full_text = "(none)"     # 어떤 입력에도 반응하지 않음
                else:
                    # 이 모델은 모든 출력이 모든 입력에 반응한다. 나열은 의미가 없고
                    # 지배적인 입력이 무엇인지가 정보라서 기여도 순으로 보여준다.
                    full_text = "  ".join(f"{n} {s:.2f}" for n, s in src)
                    src_text = "  ".join(f"{n} {s:.2f}" for n, s in src
                                         if s >= _SHARE_CUTOFF)

                with ui.HStack(height=_ROW_H, spacing=4):
                    ui.Label(name, width=ui.Fraction(1), tooltip=name,
                             style=_MODE_STYLE if is_mode else _NAME_STYLE)
                    try:
                        text = f"{float(value):.6g}"
                    except (TypeError, ValueError):
                        text = str(value)
                    ui.Label(text, width=90, style=_DIM_STYLE)
                    ui.Label(src_text, width=ui.Fraction(1.4), tooltip=full_text,
                             style=_DIM_STYLE)

    def _set_status(self, text: str, error: bool = False):
        if self._status_label:
            self._status_label.text = f"Status: {text}"
            self._status_label.set_style(_ERROR_STYLE if error else _STATUS_STYLE)
