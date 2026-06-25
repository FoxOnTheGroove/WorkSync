import omni.ui as ui


# ── Load types ────────────────────────────────────────────────────────────────
# Types 1, 2, 3 consume the "Sim Path".  Type 4 consumes the "Shape Path".
LOAD_TYPE_SIM_1 = 1
LOAD_TYPE_SIM_2 = 2
LOAD_TYPE_SIM_3 = 3
LOAD_TYPE_SHAPE = 4

LOAD_TYPES = (LOAD_TYPE_SIM_1, LOAD_TYPE_SIM_2, LOAD_TYPE_SIM_3, LOAD_TYPE_SHAPE)

# Partition layouts offered by the three "New Tab" buttons.
PARTITION_LAYOUTS = (1, 2, 4)


class Tab:
    """State for a single tab.  Holds `partition_count` partition slots.

    Fill `partitions[i]` with whatever you need to track per partition
    (stage path, prim, viewport handle, ...).
    """

    def __init__(self, name, partition_count):
        self.name            = name
        self.partition_count = partition_count
        self.partitions      = [None] * partition_count


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

                ui.Separator()

                with ui.HStack(height=32, spacing=4):
                    ui.Button("New Tab (1)", clicked_fn=lambda: self._create_tab(1))
                    ui.Button("New Tab (2)", clicked_fn=lambda: self._create_tab(2))
                    ui.Button("New Tab (4)", clicked_fn=lambda: self._create_tab(4))

                ui.Separator()

                # Tab bar: one selectable button per tab.
                self._tab_bar = ui.HStack(height=28, spacing=2)

                ui.Separator()

                # Body: partitions + load buttons for the active tab.
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
                    tab.name + (" *" if active else ""),
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

            ui.Label(
                "{}  ({} partition(s))".format(tab.name, tab.partition_count),
                height=24,
            )

            for p in range(tab.partition_count):
                with ui.CollapsableFrame("Partition {}".format(p + 1)):
                    with ui.HStack(height=28, spacing=4):
                        for load_type in LOAD_TYPES:
                            ui.Button(
                                "Load {}".format(load_type),
                                clicked_fn=lambda pi=p, lt=load_type: self._on_load(pi, lt),
                            )

    # ── Tab logic ───────────────────────────────────────────────────────────────
    def _create_tab(self, partition_count):
        self._tab_counter += 1
        name = "tab_{}".format(self._tab_counter)
        self._tabs.append(Tab(name, partition_count))
        self._active_index = len(self._tabs) - 1
        self._refresh_tab_bar()
        self._refresh_content()
        print("[tester_ui] created {} with {} partition(s)".format(name, partition_count))

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

    # ── Helpers ─────────────────────────────────────────────────────────────────
    def sim_path(self):
        return self._sim_path_m.get_value_as_string() if self._sim_path_m else ""

    def shape_path(self):
        return self._shape_path_m.get_value_as_string() if self._shape_path_m else ""

    def _on_load(self, partition_index, load_type):
        """Load `load_type` into `partition_index` of the active tab.

        Types 1/2/3 use the Sim Path; type 4 uses the Shape Path.
        TODO: implement the actual load here.
        """
        tab = self.active_tab()
        if tab is None:
            return

        path = self.shape_path() if load_type == LOAD_TYPE_SHAPE else self.sim_path()
        print(
            "[tester_ui] load type={} into {} partition {} (path='{}')".format(
                load_type, tab.name, partition_index + 1, path
            )
        )
        # tab.partitions[partition_index] = ...  # store result here

    def destroy(self):
        self._tabs.clear()
        super().destroy()
