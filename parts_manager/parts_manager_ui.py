import omni.ui as ui
from .parts_manager import PartsManager, PrimNode

_SCROLL_STYLE = {
    "background_color": 0xFF1E1E1E,
    "border_color":     0xFF555555,
    "border_width":     1,
    "border_radius":    4,
    "padding":          4,
}


class PartsManagerUI:

    def __init__(self, manager: PartsManager):
        self._manager = manager
        self._window = None
        self._tree: list = []
        self._collapsed: dict[str, bool] = {}
        self._expand_buttons: dict[str, ui.Button] = {}
        self._vis_buttons: dict[str, ui.Button] = {}
        self._children_stacks: dict[str, ui.VStack] = {}

    def build_ui(self):
        self._window = ui.Window("Parts Manager", width=300, height=400)

        with self._window.frame:
            with ui.VStack(spacing=4):
                with ui.HStack(height=28):
                    ui.Label("Parts Manager", style={"font_size": 15})
                    ui.Spacer()
                    ui.Button("↻", width=28, height=24, clicked_fn=self._on_refresh)
                ui.Separator()

                with ui.ScrollingFrame(height=ui.Fraction(1), style=_SCROLL_STYLE):
                    self._list_stack = ui.VStack(spacing=1)
                self._refresh_list()

    def _on_refresh(self):
        self._refresh_list()

    def _refresh_list(self):
        self._list_stack.clear()
        self._expand_buttons = {}
        self._vis_buttons = {}
        self._children_stacks = {}
        self._tree = self._manager.get_prim_tree()

        with self._list_stack:
            if not self._tree:
                ui.Label("(no prims found)", style={"color": 0xFF888888})
            else:
                for node in self._tree:
                    self._render_node(node)

    def _render_node(self, node: PrimNode):
        path = node.path
        is_visible = self._manager.get_visibility(path)
        is_expanded = not self._collapsed.get(path, False)

        row_height = 26 if node.is_part else 22
        row_style = {"background_color": 0xFF383838} if node.is_part else {}

        with ui.HStack(height=row_height, style=row_style):
            if node.depth > 0:
                ui.Spacer(width=node.depth * 16)

            if not node.is_leaf:
                btn_expand = ui.Button(
                    "v" if is_expanded else ">",
                    width=20,
                    clicked_fn=lambda p=path: self._on_expand_toggle(p),
                )
                self._expand_buttons[path] = btn_expand
            else:
                ui.Spacer(width=20)

            vis_style = {} if is_visible else {"color": 0xFF666666}
            btn_vis = ui.Button(
                "O" if is_visible else "-",
                width=24,
                style=vis_style,
                clicked_fn=lambda p=path: self._on_vis_toggle(p),
            )
            self._vis_buttons[path] = btn_vis

            if node.is_part:
                label_style = {"font_size": 14, "color": 0xFFDDDDDD}
            else:
                label_style = {"font_size": 13, "color": 0xFFAAAAAA}
            ui.Label(node.name, style=label_style)

        if not node.is_leaf:
            children_stack = ui.VStack(spacing=1)
            children_stack.visible = is_expanded
            self._children_stacks[path] = children_stack
            with children_stack:
                for child in node.children:
                    self._render_node(child)

    def _on_expand_toggle(self, path: str):
        self._collapsed[path] = not self._collapsed.get(path, False)
        now_expanded = not self._collapsed[path]

        stack = self._children_stacks.get(path)
        if stack:
            stack.visible = now_expanded

        btn = self._expand_buttons.get(path)
        if btn:
            btn.text = "v" if now_expanded else ">"

    def _on_vis_toggle(self, path: str):
        current = self._manager.get_visibility(path)
        self._manager.set_visibility(path, not current)
        for p, btn in self._vis_buttons.items():
            vis = self._manager.get_visibility(p)
            btn.text = "O" if vis else "-"
            btn.style = {} if vis else {"color": 0xFF666666}

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
