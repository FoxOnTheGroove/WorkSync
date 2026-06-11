import omni.ui as ui
import omni.usd
from pxr import UsdGeom
from .subset import Subset
from .viewport_pick import ViewportPicker

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
        self._result_prims: list = []
        self._result_groups: list = []
        self._picker = ViewportPicker(lambda: self._mesh_prim, self._on_pick)

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

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Subset Pick (viewport)", width=160)
                    pick_checkbox = ui.CheckBox(width=20)
                    pick_checkbox.model.set_value(False)
                    pick_checkbox.model.add_value_changed_fn(
                        lambda m: self._picker.set_enabled(m.get_value_as_bool())
                    )

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

    def _on_pick(self, prim_path: str, face_index: "int | None"):
        name = prim_path.rstrip("/").rsplit("/", 1)[-1]
        if face_index is not None and self._result_groups:
            for gi, group in enumerate(self._result_groups):
                if face_index in group:
                    Subset.highlight_group(self._mesh_prim, self._result_groups, gi)
                    if gi < len(self._result_prims):
                        name = self._result_prims[gi].GetName()
                    break
        self._set_status(f"[Pick] {name} 선택됨")

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
        self._result_prims = []
        self._result_groups = []
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
        self._result_prims = list(prims)
        self._result_groups = list(groups)
        colors = Subset.group_colors(len(groups))

        with self._result_stack:
            for i, (prim, group) in enumerate(zip(self._result_prims, self._result_groups)):
                with ui.VStack(spacing=2, height=46):
                    with ui.HStack(spacing=4, height=22):
                        ui.Rectangle(
                            width=14, height=14,
                            style={"background_color": _to_ui_color(colors[i]),
                                   "border_radius": 2},
                        )
                        select_btn = ui.Button(
                            f"{prim.GetName()}  ({len(group)} faces)",
                            clicked_fn=self._make_select_cb(i),
                            width=ui.Fraction(1),
                            style={"Button": {"alignment": ui.Alignment.LEFT}},
                        )
                        hide_btn = ui.Button(
                            "Show" if Subset.is_hidden(prim) else "Hide",
                            width=50,
                        )
                        hide_btn.set_clicked_fn(self._make_hide_cb(i, hide_btn))

                    with ui.HStack(spacing=4, height=20):
                        name_field = ui.StringField(width=ui.Fraction(1))
                        name_field.model.set_value(prim.GetName())
                        rename_btn = ui.Button("Rename", width=55)
                        rename_btn.set_clicked_fn(
                            self._make_rename_cb(i, name_field, select_btn)
                        )

    # ------------------------------------------------------------------ 행 콜백

    def _make_select_cb(self, i):
        def _cb():
            prim = self._result_prims[i]
            if prim and prim.IsValid():
                omni.usd.get_context().get_selection().set_selected_prim_paths(
                    [str(prim.GetPath())], True
                )
        return _cb

    def _make_hide_cb(self, i, hide_btn):
        def _cb():
            prim = self._result_prims[i]
            if not prim or not prim.IsValid():
                return
            hidden = Subset.toggle_hidden(prim)
            hide_btn.text = "Show" if hidden else "Hide"
        return _cb

    def _make_rename_cb(self, i, name_field, select_btn):
        def _cb():
            prim = self._result_prims[i]
            if not prim or not prim.IsValid():
                return
            new_name = name_field.model.get_value_as_string()
            new_prim = Subset.rename_subset(prim, new_name)
            if new_prim:
                self._result_prims[i] = new_prim
                select_btn.text = f"{new_prim.GetName()}  ({len(self._result_groups[i])} faces)"
                name_field.model.set_value(new_prim.GetName())
                self._set_status(f"[OK] renamed -> {new_prim.GetName()}")
            else:
                self._set_status("[FAIL] rename 실패 (이름 중복/유효하지 않음)")
        return _cb

    def destroy(self):
        self._picker.destroy()
        if self._window:
            self._window.destroy()
            self._window = None
