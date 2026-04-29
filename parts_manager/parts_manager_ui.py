import omni.ui as ui
from .parts_manager import PartsManager, PrimNode

_SCROLL_STYLE = {
    "background_color": 0xFF1E1E1E,
    "border_color":     0xFF555555,
    "border_width":     1,
    "border_radius":    4,
    "padding":          4,
}

_PART_FRAME_STYLE = {
    "border_color":  0xFF555555,
    "border_width":  1,
    "border_radius": 4,
}


class PartsManagerUI:

    def __init__(self):
        self._window = None
        self._tree: list = []
        self._collapsed: dict[str, bool] = {}        # index_key -> bool
        self._expand_buttons: dict[str, ui.Button] = {}  # index_key -> Button
        self._vis_buttons: dict[str, ui.Button] = {}     # index_key -> Button
        self._children_stacks: dict[str, ui.VStack] = {} # index_key -> VStack

    def build_ui(self):
        self._window = ui.Window("Parts Manager", width=300, height=400)

        with self._window.frame:
            with ui.VStack(spacing=2):
                with ui.HStack(height=24):
                    ui.Label("Parts Manager", style={"font_size": 15})
                    ui.Spacer()
                    ui.Button("find", width=40, height=22, clicked_fn=self._on_refresh)
                    ui.Label("sync", width=32, style={"font_size": 12, "color": 0xFFAAAAAA})
                    cb = ui.CheckBox(width=20)
                    cb.model.add_value_changed_fn(
                        lambda m: PartsManager.set_sync(m.get_value_as_bool())
                    )

                with ui.ScrollingFrame(height=ui.Fraction(1), style=_SCROLL_STYLE):
                    self._list_stack = ui.VStack(spacing=4)
                self._refresh_list()

    def _on_refresh(self):
        self._refresh_list()

    def _refresh_list(self):
        self._list_stack.clear()
        self._expand_buttons = {}
        self._vis_buttons = {}
        self._children_stacks = {}
        self._tree = PartsManager.get_prim_tree()

        with self._list_stack:
            if not self._tree:
                ui.Label("(no prims found)", style={"color": 0xFF888888})
            else:
                for node in self._tree:
                    self._render_node(node)

    def _render_node(self, node: PrimNode):
        if node.is_part:
            with ui.Frame(style=_PART_FRAME_STYLE):
                with ui.VStack(spacing=0):
                    self._render_node_content(node)
        else:
            self._render_node_content(node)

    def _render_node_content(self, node: PrimNode):
        key = node.index_key
        is_visible = PartsManager.get_visibility(key)
        is_expanded = not self._collapsed.get(key, True)

        row_height = 26 if node.is_part else 22
        row_style = {"background_color": 0xFF383838} if node.is_part else {}

        with ui.HStack(height=row_height, style=row_style):
            if node.depth > 0:
                ui.Spacer(width=node.depth * 16)

            if not node.is_leaf:
                btn_expand = ui.Button(
                    "v" if is_expanded else ">",
                    width=20,
                    clicked_fn=lambda k=key: self._on_expand_toggle(k),
                )
                self._expand_buttons[key] = btn_expand
            else:
                ui.Spacer(width=20)

            vis_style = {} if is_visible else {"color": 0xFF666666}
            btn_vis = ui.Button(
                "O" if is_visible else "-",
                width=24,
                style=vis_style,
                clicked_fn=lambda k=key: self._on_vis_toggle(k),
            )
            self._vis_buttons[key] = btn_vis

            if node.is_part:
                label_style = {"font_size": 14, "color": 0xFFDDDDDD}
            else:
                label_style = {"font_size": 13, "color": 0xFFAAAAAA}
            ui.Label(node.name, style=label_style)

        if not node.is_leaf:
            children_stack = ui.VStack(spacing=0)
            children_stack.visible = is_expanded
            self._children_stacks[key] = children_stack
            with children_stack:
                for child in node.children:
                    self._render_node(child)

    def _on_expand_toggle(self, key: str):
        self._collapsed[key] = not self._collapsed.get(key, True)
        now_expanded = not self._collapsed[key]

        stack = self._children_stacks.get(key)
        if stack:
            stack.visible = now_expanded

        btn = self._expand_buttons.get(key)
        if btn:
            btn.text = "v" if now_expanded else ">"

    def _on_vis_toggle(self, key: str):
        new_vis = not PartsManager.get_visibility(key)
        PartsManager.set_visibility(key, new_vis)

        for k, btn in self._vis_buttons.items():
            vis = PartsManager.get_visibility(k)
            btn.text = "O" if vis else "-"
            btn.style = {} if vis else {"color": 0xFF666666}

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
