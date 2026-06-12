import asyncio

import omni.ui as ui
import omni.usd
import omni.kit.app
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

_TRANSPARENT_BTN_STYLE = {"Button": {"background_color": 0x00000000, "border_width": 0}}


class DummyUI:

    def __init__(self):
        self._window = None
        self._status_label = None
        self._mesh_label = None
        self._threshold_model = None
        self._min_faces_model = None
        self._merge_by_normal_model = None
        self._multi_select_model = None
        self._result_stack = None
        self._mesh_prim = None
        self._result_prims: list = []
        self._result_groups: list = []
        self._row_frames: list = []
        self._selected_indices: list = []
        self._selected_section_frame = None
        self._picker = ViewportPicker(lambda: self._mesh_prim, self._on_pick, self._on_pick_multi)
        self._selection_sub = omni.usd.get_context().get_stage_event_stream().create_subscription_to_pop(
            self._on_stage_event, name="subset_selection_watch"
        )

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
                    ui.Label("Merge by Normal", width=110)
                    merge_checkbox = ui.CheckBox(width=20)
                    merge_checkbox.model.set_value(False)
                    self._merge_by_normal_model = merge_checkbox.model

                with ui.HStack(spacing=4, height=24):
                    ui.Label("Subset Pick (viewport)", width=130)
                    pick_checkbox = ui.CheckBox(width=20)
                    pick_checkbox.model.set_value(False)
                    pick_checkbox.model.add_value_changed_fn(
                        lambda m: self._picker.set_enabled(m.get_value_as_bool())
                    )
                    ui.Spacer(width=8)
                    ui.Label("Multi Select", width=80)
                    multi_checkbox = ui.CheckBox(width=20)
                    multi_checkbox.model.set_value(False)
                    self._multi_select_model = multi_checkbox.model
                    ui.Spacer(width=8)
                    ui.Button("Merge Selected", clicked_fn=self._on_merge_selected, width=ui.Fraction(1))

                with ui.HStack(spacing=4, height=26):
                    ui.Button("Generate Subsets", clicked_fn=self._on_generate)
                    ui.Button("Clear", clicked_fn=self._on_clear, width=70)

                self._selected_section_frame = ui.Frame()
                self._selected_section_frame.visible = False

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

    def _on_pick(self, prim_path: "str | None", face_index: "int | None"):
        if prim_path is None:
            self._clear_highlight()
            self._set_status("[Pick] 선택 해제")
            return

        name = prim_path.rstrip("/").rsplit("/", 1)[-1]
        matched = False
        if face_index is not None and self._result_groups:
            for gi, group in enumerate(self._result_groups):
                if face_index in group:
                    matched = True
                    if self._multi_select_model.get_value_as_bool():
                        self._toggle_row(gi)
                    else:
                        self._select_rows([gi])
                    if gi < len(self._result_prims):
                        name = self._result_prims[gi].GetName()
                    break
        if not matched:
            self._clear_highlight()
        self._set_status(f"[Pick] {name} 선택됨")

    def _on_pick_multi(self, prim_paths: list):
        indices = []
        for path in prim_paths:
            for i, prim in enumerate(self._result_prims):
                if prim and prim.IsValid() and str(prim.GetPath()) == path:
                    indices.append(i)
                    break
        if not indices:
            return
        if self._multi_select_model.get_value_as_bool():
            merged = list(self._selected_indices)
            for i in indices:
                if i not in merged:
                    merged.append(i)
            indices = merged
        self._select_rows(indices)
        self._set_status(f"[Pick] {len(indices)}개 subset 선택됨")

    def _on_generate(self):
        if not self._valid_mesh():
            return
        threshold = self._threshold_model.get_value_as_float()
        min_faces = max(1, self._min_faces_model.get_value_as_int())
        merge_by_normal = self._merge_by_normal_model.get_value_as_bool()

        prims, groups = Subset.generate_subsets(self._mesh_prim, threshold, min_faces, merge_by_normal)
        if not prims:
            self._set_status("[FAIL] 분류 실패")
            return

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
        self._row_frames = []
        self._selected_indices = []
        self._selected_section_frame.clear()
        self._selected_section_frame.visible = False
        self._set_status("[OK] 제거 완료")

    def _on_merge_selected(self):
        idxs = sorted(set(self._selected_indices))
        if len(idxs) < 2:
            self._set_status("[FAIL] 2개 이상 선택 후 병합하세요")
            return
        prims = [self._result_prims[i] for i in idxs]
        merged_prim = Subset.merge_subsets(self._mesh_prim, prims)
        if not merged_prim:
            self._set_status("[FAIL] 병합 실패")
            return

        merged_group: list = []
        for i in idxs:
            merged_group.extend(self._result_groups[i])

        keep = idxs[0]
        new_prims, new_groups = [], []
        for i, (p, g) in enumerate(zip(self._result_prims, self._result_groups)):
            if i == keep:
                new_prims.append(merged_prim)
                new_groups.append(sorted(merged_group))
            elif i in idxs:
                continue
            else:
                new_prims.append(p)
                new_groups.append(g)

        self._set_status(f"[OK] {len(idxs)}개 병합 -> {merged_prim.GetName()}")

        # Merge 버튼 자신이 들어있는 selected 섹션을 같은 틱에 clear하면 크래시.
        async def _do_refresh():
            await omni.kit.app.get_app().next_update_async()
            self._refresh_results(new_prims, new_groups)
            self._select_rows([new_prims.index(merged_prim)])
        asyncio.ensure_future(_do_refresh())

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
        self._row_frames = []
        self._selected_indices = []
        self._selected_section_frame.clear()
        self._selected_section_frame.visible = False
        self._picker.invalidate_face_subset_cache()

        with self._result_stack:
            for i in range(len(self._result_prims)):
                row_frame = ui.Frame(height=24)
                self._row_frames.append(row_frame)
                self._build_row_content(row_frame, i)

    def _build_row_content(self, frame, i: int, is_selected_slot: bool = False):
        prim = self._result_prims[i]
        group = self._result_groups[i]
        frame.clear()
        with frame:
            with ui.ZStack(height=24):
                if not is_selected_slot:
                    ui.Button("", clicked_fn=self._make_select_cb(i), style=_TRANSPARENT_BTN_STYLE)
                with ui.HStack(spacing=4, height=24):
                    hide_btn = ui.Button("Show" if Subset.is_hidden(prim) else "Hide", width=50)
                    hide_btn.set_clicked_fn(self._make_hide_cb(i))
                    name_field = ui.StringField(width=ui.Fraction(1))
                    name_field.model.set_value(prim.GetName())
                    rename_btn = ui.Button("Rename", width=55)
                    rename_btn.set_clicked_fn(self._make_rename_cb(i, name_field))
                    ui.Label(f"{len(group)} faces", width=70, alignment=ui.Alignment.RIGHT_CENTER)

    def _rebuild_selected_section(self):
        frame = self._selected_section_frame
        frame.clear()
        if not self._selected_indices:
            frame.visible = False
            return
        frame.visible = True

        row_frames = []
        with frame:
            with ui.VStack(spacing=2):
                ui.Label("Selected", style={"color": 0xFF888888}, height=16)
                for i in self._selected_indices:
                    row_frames.append((ui.Frame(height=24), i))
        for row, i in row_frames:
            self._build_row_content(row, i, is_selected_slot=True)

    def _refresh_row(self, i: int):
        # 클릭 이벤트를 처리 중인 프레임 자체를 같은 틱에 clear()하면 크래시하므로
        # 다음 프레임으로 재구성을 미룬다.
        async def _do_refresh():
            await omni.kit.app.get_app().next_update_async()
            if 0 <= i < len(self._row_frames):
                self._build_row_content(self._row_frames[i], i)
            if i in self._selected_indices:
                self._rebuild_selected_section()
        asyncio.ensure_future(_do_refresh())

    def _apply_selection(self, indices: list):
        """선택된 그룹들을 빨강으로 강조하고 USD 선택 및 'Selected' 영역을 갱신."""
        seen: list = []
        for i in indices:
            if 0 <= i < len(self._result_groups) and i not in seen:
                seen.append(i)
        self._selected_indices = seen
        Subset.highlight_selected(self._mesh_prim, self._result_groups, seen)

        paths = [
            str(self._result_prims[i].GetPath())
            for i in seen if self._result_prims[i] and self._result_prims[i].IsValid()
        ]
        omni.usd.get_context().get_selection().set_selected_prim_paths(paths, True)
        self._picker.note_external_selection(paths)

        # 뷰포트 제스처/리스트 클릭 콜백 안에서 섹션을 바로 clear하면 위험하므로 미룬다.
        async def _do_rebuild():
            await omni.kit.app.get_app().next_update_async()
            self._rebuild_selected_section()
        asyncio.ensure_future(_do_rebuild())

    def _select_rows(self, indices: list):
        self._apply_selection(indices)

    def _on_stage_event(self, event) -> None:
        """Subset Pick이 꺼진 상태에서 다른 prim/빈 공간을 선택하면 강조(빨강)를 해제."""
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        if not self._selected_indices:
            return
        if self._picker.is_enabled():
            return

        expected = {
            str(self._result_prims[i].GetPath())
            for i in self._selected_indices
            if 0 <= i < len(self._result_prims) and self._result_prims[i] and self._result_prims[i].IsValid()
        }
        current = set(omni.usd.get_context().get_selection().get_selected_prim_paths())
        if current != expected:
            self._clear_highlight()

    def _clear_highlight(self) -> None:
        self._selected_indices = []
        if self._mesh_prim and self._mesh_prim.IsValid():
            Subset.highlight_selected(self._mesh_prim, self._result_groups, None)

        async def _do_rebuild():
            await omni.kit.app.get_app().next_update_async()
            self._rebuild_selected_section()
        asyncio.ensure_future(_do_rebuild())

    def _toggle_row(self, index: int):
        """ctrl+클릭처럼: 이미 선택된 항목이면 선택 해제, 아니면 선택에 추가."""
        if index in self._selected_indices:
            new_indices = [i for i in self._selected_indices if i != index]
        else:
            new_indices = self._selected_indices + [index]
        self._apply_selection(new_indices)

    # ------------------------------------------------------------------ 행 콜백

    def _make_select_cb(self, i):
        def _cb():
            if self._multi_select_model.get_value_as_bool():
                self._toggle_row(i)
            else:
                self._select_rows([i])
            prim = self._result_prims[i]
            self._set_status(f"[Pick] {prim.GetName()} 선택됨")
        return _cb

    def _make_hide_cb(self, i):
        def _cb():
            prim = self._result_prims[i]
            if not prim or not prim.IsValid():
                return
            Subset.toggle_hidden(prim)
            self._refresh_row(i)
        return _cb

    def _make_rename_cb(self, i, name_field):
        def _cb():
            prim = self._result_prims[i]
            if not prim or not prim.IsValid():
                return
            new_name = name_field.model.get_value_as_string()
            new_prim = Subset.rename_subset(prim, new_name)
            if new_prim:
                self._result_prims[i] = new_prim
                self._picker.invalidate_face_subset_cache()
                self._set_status(f"[OK] renamed -> {new_prim.GetName()}")
                self._refresh_row(i)
            else:
                self._set_status("[FAIL] rename 실패 (이름 중복/유효하지 않음)")
        return _cb

    def destroy(self):
        self._selection_sub = None
        self._picker.destroy()
        if self._window:
            self._window.destroy()
            self._window = None
