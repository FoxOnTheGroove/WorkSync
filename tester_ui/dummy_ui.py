import omni.ui as ui


class DummyDebugWindow(ui.Window):

    def __init__(self):
        super().__init__("Tester UI (Debug)", width=320, height=240)
        self._subs    = []
        self._status  = None
        self._count_m = None
        self._counter = 0
        self._build_ui()

    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=6):

                ui.Label("Debug scratch panel", height=24)
                ui.Separator()

                self._status = ui.Label("Ready", height=24)

                with ui.HStack(height=24):
                    ui.Label("Counter", width=90)
                    self._count_m = ui.IntDrag(min=0, max=9999, step=1).model

                ui.Button("Ping", height=32, clicked_fn=self._on_ping)
                ui.Button("Reset", height=32, clicked_fn=self._on_reset)

    def _on_ping(self):
        self._counter += 1
        self._count_m.set_value(self._counter)
        self._status.text = "Ping #{}".format(self._counter)
        print("[tester_ui] ping {}".format(self._counter))

    def _on_reset(self):
        self._counter = 0
        self._count_m.set_value(0)
        self._status.text = "Reset"
        print("[tester_ui] reset")

    def destroy(self):
        self._subs.clear()
        super().destroy()
