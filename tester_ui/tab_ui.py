import omni.ui as ui


# ── Load types ────────────────────────────────────────────────────────────────
# Types 1, 2, 3 consume the "Sim Path".  Type 4 consumes the "Shape Path".
LOAD_TYPE_SIM_1 = 1
LOAD_TYPE_SIM_2 = 2
LOAD_TYPE_SIM_3 = 3
LOAD_TYPE_SHAPE = 4

LOAD_TYPES = (LOAD_TYPE_SIM_1, LOAD_TYPE_SIM_2, LOAD_TYPE_SIM_3, LOAD_TYPE_SHAPE)

# Button text per load type.
LOAD_LABELS = {
    LOAD_TYPE_SIM_1: "press",
    LOAD_TYPE_SIM_2: "vel",
    LOAD_TYPE_SIM_3: "line",
    LOAD_TYPE_SHAPE: "eqp",
}

# Viewport layouts offered by the three "New Tab" buttons.
VIEWPORT_LAYOUTS = (1, 2, 4)


class Tab:
    """State for a single tab.  Holds `viewport_count` viewport slots.

    Fill `viewports[i]` with whatever you need to track per viewport
    (stage path, prim, viewport handle, ...).
    """

    def __init__(self, name, viewport_count):
        self.name              = name
        self.viewport_count    = viewport_count
        self.viewports         = [None] * viewport_count
        self.selected_viewport = 0
        self.maximized         = False


class TabManagerWindow(ui.Window):

    def __init__(self):
        super().__init__("Tab Manager", width=420, height=520)
        self._tabs          = []
        self._active_index  = -1
        self._tab_counter   = 0
        self._sim_path_m    = None
        self._shape_path_m  = None
        self._tab_bar       = None
        self._content       = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        with self.frame:
            with ui.VStack(spacing=6):

                with ui.HStack(height=24):
                    ui.Label("Sim Path", width=90)
                    self._sim_path_m = ui.StringField().model

                with ui.HStack(height=24):
                    ui.Label("Shape Path", width=90)
                    self._shape_path_m = ui.StringField().model

                with ui.HStack(height=32, spacing=4):
                    ui.Button("New Tab (1)", clicked_fn=lambda: self._create_tab(1))
                    ui.Button("New Tab (2)", clicked_fn=lambda: self._create_tab(2))
                    ui.Button("New Tab (4)", clicked_fn=lambda: self._create_tab(4))

                # Tab bar: one selectable button per tab.
                self._tab_bar = ui.HStack(height=28, spacing=2)

                # Body: active-tab controls + per-viewport load buttons.
                self._content = ui.VStack(spacing=6)

        self._refresh_tab_bar()
        self._refresh_content()

    def _refresh_tab_bar(self):
        self._tab_bar.clear()
        with self._tab_bar:
            if not self._tabs:
                ui.Label("(no tabs)", height=24)
                return
            for i, tab in enumerate(self._tabs):
                active = (i == self._active_index)
                ui.Button(
                    "{} ({}){}".format(tab.name, tab.viewport_count, " *" if active else ""),
                    height=24,
                    clicked_fn=lambda idx=i: self._activate_tab(idx),
                )

    def _refresh_content(self):
        self._content.clear()
        tab = self.active_tab()
        with self._content:
            if tab is None:
                ui.Label("Create a tab to begin.", height=24)
                return

            # Selected-tab row: name + maximize / minimize.
            with ui.HStack(height=28, spacing=4):
                ui.Button(
                    "{}{}".format(tab.name, "  [MAX]" if tab.maximized else ""),
                    clicked_fn=lambda: self._activate_tab(self._active_index),
                )
                ui.Button("Maximize", width=90, clicked_fn=self._maximize_tab)
                ui.Button("Minimize", width=90, clicked_fn=self._minimize_tab)

            self._build_viewport_grid(tab)

    def _build_viewport_grid(self, tab):
        # Maximized: show only the selected viewport, full size.
        if tab.maximized:
            self._build_viewport(tab, tab.selected_viewport)
            return

        n = tab.viewport_count
        if n == 2:
            # x | x
            with ui.HStack(spacing=4):
                self._build_viewport(tab, 0)
                self._build_viewport(tab, 1)
        elif n == 4:
            # 2 x 2
            with ui.VStack(spacing=4):
                with ui.HStack(spacing=4):
                    self._build_viewport(tab, 0)
                    self._build_viewport(tab, 1)
                with ui.HStack(spacing=4):
                    self._build_viewport(tab, 2)
                    self._build_viewport(tab, 3)
        else:
            # single viewport
            self._build_viewport(tab, 0)

    def _build_viewport(self, tab, v):
        selected = (v == tab.selected_viewport)
        with ui.CollapsableFrame(
            "viewport {}{}".format(v + 1, " *" if selected else "")
        ):
            with ui.VStack(spacing=4):
                ui.Button(
                    "select",
                    height=28,
                    clicked_fn=lambda: self._select_viewport(v),
                )
                with ui.HStack(height=28, spacing=4):
                    for load_type in LOAD_TYPES:
                        ui.Button(
                            LOAD_LABELS[load_type],
                            clicked_fn=lambda lt=load_type: self._on_load(v, lt),
                        )

    # ── Tab logic ───────────────────────────────────────────────────────────────
    def _create_tab(self, viewport_count):
        self._tab_counter += 1
        name = "tab_{}".format(self._tab_counter)
        self._tabs.append(Tab(name, viewport_count))
        self._active_index = len(self._tabs) - 1
        self._refresh_tab_bar()
        self._refresh_content()
        print("[tester_ui] created {} with {} viewport(s)".format(name, viewport_count))

    def _activate_tab(self, index):
        if 0 <= index < len(self._tabs):
            self._active_index = index
            self._refresh_tab_bar()
            self._refresh_content()
            print("[tester_ui] activated {}".format(self._tabs[index].name))

    def active_tab(self):
        if 0 <= self._active_index < len(self._tabs):
            return self._tabs[self._active_index]
        return None

    def _select_viewport(self, index):
        tab = self.active_tab()
        if tab is None or not (0 <= index < tab.viewport_count):
            return
        tab.selected_viewport = index
        self._refresh_content()
        print("[tester_ui] {} selected viewport {}".format(tab.name, index + 1))

    def _maximize_tab(self):
        """Maximize the active tab; the target is its selected viewport.

        TODO: implement the actual maximize behavior.
        """
        tab = self.active_tab()
        if tab is None:
            return
        tab.maximized = True
        self._refresh_content()
        print("[tester_ui] maximize {} -> viewport {}".format(
            tab.name, tab.selected_viewport + 1))

    def _minimize_tab(self):
        """Restore the active tab from maximized state.

        TODO: implement the actual minimize behavior.
        """
        tab = self.active_tab()
        if tab is None:
            return
        tab.maximized = False
        self._refresh_content()
        print("[tester_ui] minimize {}".format(tab.name))

    # ── Helpers ─────────────────────────────────────────────────────────────────
    def sim_path(self):
        return self._sim_path_m.get_value_as_string() if self._sim_path_m else ""

    def shape_path(self):
        return self._shape_path_m.get_value_as_string() if self._shape_path_m else ""

    def _on_load(self, viewport_index, load_type):
        """Load `load_type` into `viewport_index` of the active tab.

        press/vel/line use the Sim Path; eqp uses the Shape Path.
        TODO: implement the actual load here.
        """
        tab = self.active_tab()
        if tab is None:
            return

        path = self.shape_path() if load_type == LOAD_TYPE_SHAPE else self.sim_path()
        print(
            "[tester_ui] load '{}' into {} viewport {} (path='{}')".format(
                LOAD_LABELS[load_type], tab.name, viewport_index + 1, path
            )
        )
        # tab.viewports[viewport_index] = ...  # store result here

    def destroy(self):
        self._tabs.clear()
        super().destroy()
