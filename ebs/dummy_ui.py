import omni.ui as ui

from .ebs_simulate_service import EbsSimulateService
from .ebs_simulate_overlay import EbsSimulateOverlay

_LIST_STYLE = {
    "background_color": 0xFF1E1E1E,
    "border_color":     0xFF555555,
    "border_width":     1,
    "border_radius":    4,
    "padding":          4,
}


class EbsDummyUI:
    """공개 API(EbsSimulateService) 동작 확인용 더미 익스텐션 UI."""

    def __init__(self):
        self._window: "ui.Window | None" = None
        self._path_field: "ui.StringField | None" = None
        self._vp_field: "ui.StringField | None" = None
        self._status_label: "ui.Label | None" = None
        self._list_stack: "ui.VStack | None" = None
        self._value_labels: dict = {}      # prim_path -> ui.Label
        self._overlay_keys: dict = {}      # prim_path -> overlay key(int)

    # ── 빌드 ─────────────────────────────────────────────────────────────────

    def build_ui(self) -> None:
        self._window = ui.Window("EBS Simulate (dummy)", width=380, height=420)

        with self._window.frame:
            with ui.VStack(spacing=6, style={"margin": 8}):
                with ui.HStack(height=24, spacing=4):
                    ui.Label("Prim:", width=44)
                    self._path_field = ui.StringField()
                    self._path_field.model.set_value("/World/target")
                    ui.Button("Add", width=50, clicked_fn=self._on_add_target)

                with ui.HStack(height=24, spacing=4):
                    ui.Label("Viewport:", width=64)
                    self._vp_field = ui.StringField()
                    self._vp_field.model.set_value("Viewport")
                    ui.Button("Overlay", width=70, clicked_fn=self._on_toggle_overlay)

                with ui.HStack(height=26, spacing=4):
                    ui.Button("Start", clicked_fn=self._on_start)
                    ui.Button("Stop", clicked_fn=self._on_stop)
                    ui.Button("Step", clicked_fn=self._on_step)
                    ui.Button("Reset", clicked_fn=self._on_reset)

                self._status_label = ui.Label("Status: idle", height=20)

                with ui.ScrollingFrame(height=ui.Fraction(1), style=_LIST_STYLE):
                    self._list_stack = ui.VStack(spacing=2)

        EbsSimulateService.subscribe(self._on_result)
        self._refresh_list()

    # ── 핸들러 ───────────────────────────────────────────────────────────────

    def _on_add_target(self) -> None:
        path = self._path_field.model.get_value_as_string().strip()
        if not path:
            return
        target = EbsSimulateService.add_target(path)
        if target is None:
            self._set_status(f"invalid prim: {path}")
            return
        self._set_status(f"target added: {path}")
        self._refresh_list()

    def _on_remove_target(self, path: str) -> None:
        key = self._overlay_keys.pop(path, None)
        if key is not None:
            EbsSimulateOverlay.off(key)
        EbsSimulateService.remove_target(path)
        self._set_status(f"target removed: {path}")
        self._refresh_list()

    def _on_toggle_overlay(self) -> None:
        vp_name = self._vp_field.model.get_value_as_string().strip()
        paths = EbsSimulateService.get_target_paths()
        if self._overlay_keys:
            for key in self._overlay_keys.values():
                EbsSimulateOverlay.off(key)
            self._overlay_keys.clear()
            self._set_status("overlay off")
            return
        for path in paths:
            key = EbsSimulateOverlay.on(vp_name, path)
            if key is not None:
                self._overlay_keys[path] = key
        self._set_status(f"overlay on ({len(self._overlay_keys)})")

    def _on_start(self) -> None:
        EbsSimulateService.start()
        self._set_status("running")

    def _on_stop(self) -> None:
        EbsSimulateService.stop()
        self._set_status("stopped")

    def _on_step(self) -> None:
        result = EbsSimulateService.step()
        if result is None:
            self._set_status("not initialized")
            return
        self._update_values(result)

    def _on_reset(self) -> None:
        EbsSimulateService.reset()
        self._set_status("reset")
        self._refresh_list()

    def _on_result(self, result: dict) -> None:
        self._update_values(result)

    # ── 목록 ─────────────────────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        if self._list_stack is None:
            return
        self._list_stack.clear()
        self._value_labels = {}

        paths = EbsSimulateService.get_target_paths()
        with self._list_stack:
            if not paths:
                ui.Label("(no targets)", style={"color": 0xFF888888})
                return
            for path in paths:
                with ui.HStack(height=22, spacing=4):
                    ui.Label(path.rsplit("/", 1)[-1], style={"font_size": 13, "color": 0xFFDDDDDD})
                    label = ui.Label("-", width=80, style={"font_size": 13, "color": 0xFFAAAAAA})
                    self._value_labels[path] = label
                    ui.Button("x", width=22, clicked_fn=lambda p=path: self._on_remove_target(p))

    def _update_values(self, result: dict) -> None:
        values = result.get("values", {})
        for path, label in self._value_labels.items():
            value = values.get(path)
            label.text = "-" if value is None else f"{value:.3f}"
        self._set_status(f"step {result.get('step', 0)} | t={result.get('time', 0.0):.3f}")
        EbsSimulateOverlay.refresh()

    def _set_status(self, text: str) -> None:
        if self._status_label:
            self._status_label.text = f"Status: {text}"

    # ── 정리 ─────────────────────────────────────────────────────────────────

    def destroy(self) -> None:
        EbsSimulateService.unsubscribe(self._on_result)
        for key in self._overlay_keys.values():
            EbsSimulateOverlay.off(key)
        self._overlay_keys.clear()
        if self._window:
            self._window.destroy()
            self._window = None
