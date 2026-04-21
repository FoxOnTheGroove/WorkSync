import omni.ui as ui
from .parts_manager import PartsManager


class PartsManagerUI:

    def __init__(self, manager: PartsManager):
        self._manager = manager
        self._window = None
        self._name_labels: list[ui.Label] = []

    def build_ui(self):
        self._window = ui.Window("Parts Manager", width=300, height=400)

        with self._window.frame:
            with ui.VStack(spacing=4):

                ui.Label("load_prims children", style={"font_size": 16})
                ui.Spacer(height=4)

                # 새로고침 버튼
                ui.Button("Refresh", clicked_fn=self._on_refresh)

                ui.Separator()

                # 이름 목록 출력 영역
                self._list_stack = ui.VStack(spacing=2)
                self._refresh_list()

    def _on_refresh(self):
        self._refresh_list()

    def _refresh_list(self):
        """list_stack을 비우고 현재 이름 목록으로 다시 채움."""
        self._list_stack.clear()
        names = self._manager.get_load_prim_names()

        with self._list_stack:
            if not names:
                ui.Label("(no prims found)", style={"color": 0xFF888888})
            else:
                for name in names:
                    ui.Label(f"  • {name}")

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
