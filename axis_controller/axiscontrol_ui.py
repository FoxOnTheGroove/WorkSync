import omni.ui as ui
from .axiscontrol import AxisControl


class AxisControlUI:

    def __init__(self):
        self._window = None
        self._camera_stack = None
        self._status_label = None

    def build_ui(self):
        self._window = ui.Window("Axis Controller", width=340, height=500)

        with self._window.frame:
            with ui.VStack(spacing=4):

                with ui.HStack(spacing=4):
                    ui.Button("Initialize", clicked_fn=self._on_initialize, width=100)
                    ui.Button("Refresh",    clicked_fn=self._on_refresh,    width=80)
                    self._status_label = ui.Label("", style={"color": 0xFF888888})

                with ui.ScrollingFrame(height=ui.Fraction(1)):
                    self._camera_stack = ui.VStack(spacing=6)
                    self._refresh_cameras()

    def _on_initialize(self):
        ok = AxisControl.initialize()
        if self._status_label:
            self._status_label.text = "[OK] Initialized" if ok else "[FAIL] Check Orbit"
        self._refresh_cameras()

    def _on_refresh(self):
        self._refresh_cameras()

    def _refresh_cameras(self):
        if self._camera_stack is None:
            return
        self._camera_stack.clear()

        cameras = AxisControl.get_cameras()

        with self._camera_stack:
            if not cameras:
                ui.Label("(no cameras)", style={"color": 0xFF888888})
                return

            for cam in cameras:
                target      = AxisControl._get_target_for_camera(cam)
                cam_name    = cam.GetName()
                target_name = target.GetName() if target else "(no target)"
                has_target  = target is not None

                with ui.VStack(spacing=2):
                    with ui.HStack(spacing=6):
                        ui.Label(cam_name, style={"font_size": 14})
                        ui.Label(
                            target_name,
                            style={"color": 0xFF888888 if not has_target else 0xFFFFFFFF},
                        )

                    with ui.HStack(spacing=2):
                        for label, axis in [
                            ("+X", "x"),  ("+Y", "y"),  ("+Z", "z"),
                            ("-X", "-x"), ("-Y", "-y"), ("-Z", "-z"),
                        ]:
                            def make_cb(c=cam, a=axis):
                                def _cb():
                                    AxisControl.set_camera_axis(c, a)
                                return _cb
                            ui.Button(label, clicked_fn=make_cb(), enabled=has_target, width=44)

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
