import omni.ui as ui
from . import setbgcolor


class GradientBGWindow(ui.Window):

    def __init__(self):
        super().__init__("Gradient Background", width=320, height=240)
        self._subs        = []
        self._cw_start    = None
        self._cw_end      = None
        self._angle_m     = None
        self._intensity_m = None
        self._build_ui()

    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=6):

                ui.Button("Init", height=32, clicked_fn=self._on_init)
                ui.Separator()

                with ui.HStack(height=24):
                    ui.Label("Start Color", width=90)
                    self._cw_start = ui.ColorWidget(0.15, 0.15, 0.15)

                with ui.HStack(height=24):
                    ui.Label("End Color", width=90)
                    self._cw_end = ui.ColorWidget(0.60, 0.60, 0.60)

                with ui.HStack(height=24):
                    ui.Label("Angle", width=90)
                    self._angle_m = ui.FloatDrag(min=0.0, max=360.0, step=1.0).model
                    self._angle_m.set_value(90.0)

                with ui.HStack(height=24):
                    ui.Label("Intensity", width=90)
                    self._intensity_m = ui.FloatDrag(min=100.0, max=50000.0, step=100.0).model
                    self._intensity_m.set_value(400.0)

        self._subs += self._subscribe_color(self._cw_start)
        self._subs += self._subscribe_color(self._cw_end)
        self._subs.append(self._angle_m.add_value_changed_fn(lambda _: self._on_params_changed()))
        self._subs.append(self._intensity_m.add_value_changed_fn(lambda _: self._on_params_changed()))

    def _subscribe_color(self, cw):
        subs  = []
        model = cw.model
        for child in model.get_item_children():
            item_m = model.get_item_value_model(child)
            subs.append(item_m.add_value_changed_fn(lambda _: self._on_params_changed()))
        return subs

    def _get_color(self, cw):
        model    = cw.model
        children = model.get_item_children()
        return tuple(model.get_item_value_model(c).get_value_as_float() for c in children[:3])

    def _on_init(self):
        setbgcolor.init_scene()

    def _on_params_changed(self):
        if self._cw_start is None:
            return
        setbgcolor.update_gradient(
            color_start=self._get_color(self._cw_start),
            color_end=self._get_color(self._cw_end),
            angle_deg=self._angle_m.get_value_as_float(),
            intensity_scale=self._intensity_m.get_value_as_float(),
        )

    def destroy(self):
        self._subs.clear()
        super().destroy()
