import omni.ui as ui
import omni.usd
from pxr import UsdGeom
from .subset import Subset

_SCROLL_STYLE = {
    "background_color": 0xFF1E1E1E,
    "border_color":     0xFF555555,
    "border_width":     1,
    "border_radius":    4,
    "padding":          4,
}


def _to_ui_color(rgb: tuple[float, float, float]) -> int:
    # omni.ui 색상은 0xAABBGGRR
    r, g, b = (int(c * 255) for c in rgb)
    return 0xFF000000 | (b << 16) | (g << 8) | r


class DummyUI:

    def __init__(self):
        self._window = None
        self._status_label = None
        self._mesh_label = None
        self._threshold_model = None
        self._min_faces_model = None
        self._color_checkbox = None
        self._result_stack = None
        self._mesh_prim = None

    def build_ui(self):
        self._window = ui.Window("Subset", width=360, height=520)

        with self._window.frame:
            with ui.VStack(spacing=4):

                with ui.HStack(spacing=4, height=24):
                    ui.Button("Get Selected Mesh", clicked_fn=self._on_pick_mesh, width=130)
                    self._status_label = ui.Label("", style={"color": 0xFF888888})

                self._mesh_label = ui.Label("(no mesh)", style={"color": 0xFF888888}, height=20)

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Angle (deg)", width=80)
                    slider = ui.FloatSlider(min=1.0, max=90.0)
                    slider.model.set_value(30.0)
                    self._threshold_model = slider.model

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Min Faces", width=80)
                    field = ui.IntField(width=60)
                    field.model.set_value(1)
                    self._min_faces_model = field.model
                    ui.Spacer(width=12)
                    ui.Label("Color", width=40)
                    self._color_checkbox = ui.CheckBox(width=20)
                    self._color_checkbox.model.set_value(True)

                with ui.HStack(spacing=4, height=26):
                    ui.Button("Generate Subsets", clicked_fn=self._on_generate)
                    ui.Button("Clear", clicked_fn=self._on_clear, width=70)

                with ui.ScrollingFrame(height=ui.Fraction(1), style=_SCROLL_STYLE):
                    self._result_stack = ui.VStack(spacing=2)

    # ------------------------------------------------------------------ 콜백

    def _on_pick_mesh(self):
        self._mesh_prim = self._find_selected_mesh()
        if self._mesh_prim:
            self._mesh_label.text = str(self._mesh_prim.GetPath())
            self._mesh_label.style = {"color": 0xFFFFFFFF}
            self._set_status("[OK] Mesh")
        else:
            self._mesh_label.text = "(no mesh)"
            self._mesh_label.style = {"color": 0xFF888888}
            self._set_status("[FAIL] Mesh를 선택하세요")

    def _on_generate(self):
        if not self._valid_mesh():
            return
        threshold = self._threshold_model.get_value_as_float()
        min_faces = max(1, self._min_faces_model.get_value_as_int())

        prims, groups = Subset.generate_subsets(self._mesh_prim, threshold, min_faces)
        if not prims:
            self._set_status("[FAIL] 분류 실패")
            return

        if self._color_checkbox.model.get_value_as_bool():
            Subset.apply_group_colors(self._mesh_prim, groups)
        else:
            Subset.clear_group_colors(self._mesh_prim)

        self._set_status(f"[OK] {len(prims)}개 생성")
        self._refresh_results(prims, groups)

    def _on_clear(self):
        if not self._valid_mesh():
            return
        Subset.remove_generated_subsets(self._mesh_prim)
        Subset.clear_group_colors(self._mesh_prim)
        self._result_stack.clear()
        self._set_status("[OK] 제거 완료")

    # ------------------------------------------------------------------ 내부

    def _valid_mesh(self) -> bool:
        if self._mesh_prim is None or not self._mesh_prim.IsValid():
            self._set_status("[FAIL] 먼저 Mesh를 지정하세요")
            return False
        return True

    def _set_status(self, text: str):
        if self._status_label:
            self._status_label.text = text

    def _find_selected_mesh(self):
        """선택된 prim이 Mesh면 그대로, 아니면 하위에서 첫 Mesh 탐색."""
        context = omni.usd.get_context()
        stage = context.get_stage()
        if not stage:
            return None
        for path in context.get_selection().get_selected_prim_paths():
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                continue
            if prim.IsA(UsdGeom.Mesh):
                return prim
            for descendant in prim.GetAllDescendants():
                if descendant.IsA(UsdGeom.Mesh):
                    return descendant
        return None

    def _refresh_results(self, prims, groups):
        self._result_stack.clear()
        colors = Subset.group_colors(len(groups))

        with self._result_stack:
            for i, (prim, group) in enumerate(zip(prims, groups)):
                path = str(prim.GetPath())
                with ui.HStack(spacing=4, height=22):
                    ui.Rectangle(
                        width=14, height=14,
                        style={"background_color": _to_ui_color(colors[i]),
                               "border_radius": 2},
                    )

                    def make_cb(p=path):
                        def _cb():
                            omni.usd.get_context().get_selection().set_selected_prim_paths(
                                [p], True
                            )
                        return _cb

                    ui.Button(
                        f"{prim.GetName()}  ({len(group)} faces)",
                        clicked_fn=make_cb(),
                        style={"Button": {"alignment": ui.Alignment.LEFT}},
                    )

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
