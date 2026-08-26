import math
import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager

from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf
import omni.usd

__all__ = ["EbsSimulate"]

EQP_PREFIX = "EQP_"
PORT_ID_KEY = "port-id"       # value identifying a port: '<equipment>_<n>'
OFFSET_KEY  = "offset"        # port distance from its addr, along the rail direction
CADX_KEY    = "cad-x"         # rail start point along X, on the addr group
CADY_KEY    = "cad-y"         # rail start point along Y, on the addr group
CAD_PER_UNIT    = 100.0 / 3.0     # cad-x units per stage unit
OFFSET_PER_UNIT = 100000.0        # offset units per stage unit
RAIL_PREFIX = "rail_"

GEOMETRY_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves",
    "Capsule", "Cone", "Cube", "Cylinder", "Sphere", "Plane",
})

CAMERA_PATH    = "/EbsCamera"             # session-layer camera owned by this extension
CAMERA_FILL    = 0.9                      # how much of the view the target fills
CAMERA_SLAB    = 0.2                      # clip this much past the target, front and back

MARKER_ROOT    = "/EbsCollisionMarkers"   # session-layer scope holding the cell quads
MARKER_OPACITY = 0.35
COLOR_BLOCKED  = (0.9, 0.1, 0.1)
COLOR_CLEAR    = (0.35, 0.75, 0.4)
CHECKER_SHADE  = 0.72     # every other cell is shaded, so the grid reads as a grid

# Faces evaluated by the simulation. Front, back and floor are ignored.
FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)

GRID = 5                 # divisions given to the longest edge; the others get
                         # an integer count that keeps the cells near square
OVERLAP_EPS = 1e-6       # boxes merely touching a face do not count as blocking
PRECISION_BBOX = "bbox"      # one box per equipment
PRECISION_MESH = "mesh"      # one box per mesh
PRECISION_TRI  = "triangle"  # the mesh triangles themselves

# Prim types that can never contain equipment. Descending into them is what made
# the first scan expensive: geometry subtrees are where nearly all prims live.
PRUNE_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves", "Capsule", "Cone", "Cube",
    "Cylinder", "Sphere", "Plane", "GeomSubset",
    "Material", "Shader", "NodeGraph", "Camera",
})
ANCHOR_DEPTH = 6         # how many times to follow child(0) down from the equipment prim


class EbsSimulate:
    """EBS simulation implementation.

    All computation and USD access lives here.
    Only EbsSimulateService is exposed to the outside.
    """

    def __init__(self):
        self._xml_path: str = ""
        self._ebs_path_2port: str = ""
        self._ebs_path_3port: str = ""
        self._clearance: float = 1.0        # thickness probed outward from each face
        self._search_root: str = ""         # limit the scan to this subtree when set
        self._eqp_index: dict = {}          # "EQP_########" -> prim path
        self._port_map: dict = {}           # "########" -> sorted port indices
        self._port_elements: dict = {}      # "########" -> port elements in port order
        self._port_offsets: dict = {}       # "########" -> {index: offset}
        self._port_addr: dict = {}          # "########" -> base addr number
        self._port_addr_of: dict = {}       # "########" -> {index: addr number}
        self._addr_cad: dict = {}           # addr number -> (cad-x, cad-y)
        self._rail_root: str = ""           # parent path holding the rail prims
        self._bounds_cache: dict = {}       # prim path -> world aligned Gf.Range3d
        self._mesh_bounds: dict = {}        # equipment path -> [(mesh path, box)]
        self._triangles: dict = {}          # mesh path -> world-space triangles
        self._visible: dict = {}            # prim path -> visibility, for one run
        self._grid_shape: dict = {}         # face -> (rows, cols) of the last run
        self._previous_camera = None        # viewport camera to restore on release
        self._precision: str = PRECISION_TRI
        self._timings: list = []            # [label, elapsed_ms] for the last run
        self._notes: list = []              # diagnostics for the last run, shown in the UI
        self._started: float = 0.0
        self._ready: bool = False           # set by init(), required before prepare()
        self._target: dict = None           # prepared equipment / EBS for the step buttons
        self._aligned: bool = False
        self._result: dict = {}

    # -- settings ------------------------------------------------------------

    def set_xml_path(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._xml_path:
            self._port_map = {}
            self._port_elements = {}
            self._ready = False          # the port table has to be read again
        self._xml_path = path

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def set_clearance(self, value: float) -> None:
        self._clearance = max(0.0, float(value))

    def set_precision(self, mode: str) -> None:
        """How closely collisions are tested: 'bbox', 'mesh' or 'triangle'."""
        if mode in (PRECISION_BBOX, PRECISION_MESH, PRECISION_TRI):
            self._precision = mode
        else:
            print(f"[ebs] unknown precision '{mode}', keeping {self._precision}")

    def set_rail_root(self, path: str) -> None:
        """Parent prim holding the rail_<a>_<b> prims."""
        self._rail_root = (path or "").strip()

    def set_search_root(self, path: str) -> None:
        """Limit the equipment scan to one subtree, e.g. '/World/Factory'."""
        path = (path or "").strip()
        if path != self._search_root:
            self._eqp_index = {}
            self._bounds_cache = {}
            self._ready = False          # the stage has to be scanned again
        self._search_root = path

    def get_result(self) -> dict:
        return dict(self._result)

    def get_timings(self) -> list:
        """[label, elapsed_ms] recorded during the last run."""
        return [list(t) for t in self._timings]

    def teardown(self) -> None:
        self.release_camera()
        self.clear_markers()
        self._eqp_index = {}
        self._port_map = {}
        self._port_elements = {}
        self._bounds_cache = {}
        self._mesh_bounds = {}
        self._triangles = {}
        self._visible = {}
        self._timings = []
        self._ready = False
        self._target = None
        self._aligned = False
        self._result = {}

    # -- timing --------------------------------------------------------------

    def _begin(self) -> None:
        """Start a fresh timing and diagnostics record for one button press."""
        self._timings = []
        self._notes = []
        self._started = time.perf_counter()

    def _note(self, text: str) -> None:
        """Record a diagnostic line: printed, and carried back to the UI."""
        self._notes.append(text)
        print(f"[ebs] {text}")

    def get_notes(self) -> list:
        return list(self._notes)

    @contextmanager
    def _stage_timer(self, label: str):
        """Record how long one step of the run took."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self._timings.append([label, (time.perf_counter() - started) * 1000.0])

    # -- steps ---------------------------------------------------------------

    def init(self) -> dict:
        """Step 0: scan the stage and the XML once, so the steps can be cheap.

        Everything cached here - the equipment index, their bounds, the port
        table - survives until this runs again, and nothing else builds it.
        """
        self._begin()
        self._ready = False
        self._target = None
        self._aligned = False
        self._triangles = {}
        self.make_camera()

        equipment = self.build_index()
        ports = self.load_ports()
        self._ready = equipment > 0 and ports > 0
        self._note(f"indexed {equipment} equipment, {ports} port entries")
        if not equipment:
            return self._payload(False, "No EQP_ prims found - check the search root")
        if not ports:
            return self._payload(False, "No ports read - check the XML path")
        return self._payload(True, f"Ready: {equipment} equipment, {ports} port entries")

    def prepare(self, equipment: str = "") -> dict:
        """Step 1: resolve the equipment, its port count and the matching EBS prim."""
        self._begin()
        if not self._ready:
            return self._payload(False, "Run Init first")
        return self._do_prepare(equipment)

    def focus(self) -> dict:
        """Step 1: move the camera in front of the prepared equipment."""
        self._begin()
        return self._do_focus()

    def align(self) -> dict:
        """Step 2: move the EBS onto the prepared equipment."""
        self._begin()
        return self._do_align()

    def collide(self) -> dict:
        """Step 3: run the collision check for the aligned EBS."""
        self._begin()
        return self._do_collide()

    def simulate(self, equipment: str = "") -> dict:
        """Run every step in order. Init has to have run first."""
        self._begin()
        if not self._ready:
            return self._payload(False, "Run Init first")
        result = self._do_prepare(equipment)
        if not result["ok"]:
            return result
        result = self._do_focus()
        if not result["ok"]:
            return result
        result = self._do_align()
        if not result["ok"]:
            return result
        return self._do_collide()

    # -- step bodies (shared by the single-step buttons and simulate) ---------

    def _do_prepare(self, equipment: str) -> dict:
        self._target = None
        self._aligned = False

        stage = self._get_stage()
        if stage is None:
            return self._payload(False, "No stage open")

        with self._stage_timer("resolve equipment"):
            eqp_prim = (self._resolve_by_name(stage, equipment) if equipment.strip()
                        else self._resolve_by_selection(stage))
        if eqp_prim is None:
            return self._payload(False, "Equipment prim not found: "
                                 f"{equipment.strip() or '(no selection)'}")

        eqp_id = self._equipment_id(eqp_prim)

        with self._stage_timer("port lookup"):
            port_count = self.get_port_count(eqp_id)
        if port_count is None:
            return self._payload(False, f"No port info for '{eqp_id}' in XML",
                                 equipment=eqp_prim, eqp_id=eqp_id)
        if port_count not in (2, 3):
            return self._payload(False, f"{port_count}-port equipment: no matching EBS",
                                 equipment=eqp_prim, eqp_id=eqp_id, port_count=port_count)

        ebs_path = self._ebs_path_2port if port_count == 2 else self._ebs_path_3port
        ebs_prim = stage.GetPrimAtPath(ebs_path) if ebs_path else None
        if ebs_prim is None or not ebs_prim.IsValid():
            return self._payload(False, f"Invalid {port_count}-port EBS prim path: {ebs_path}",
                                 equipment=eqp_prim, eqp_id=eqp_id, port_count=port_count)

        anchor = self._descend_first_child(eqp_prim, ANCHOR_DEPTH)
        self._target = {
            "equipment": eqp_prim,
            "eqp_id": eqp_id,
            "port_count": port_count,
            "ebs": ebs_prim,
            "anchor": anchor,
        }
        return self._payload(True, f"Prepared: {eqp_id} ({port_count} port)")

    def _do_focus(self) -> dict:
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        with self._stage_timer("camera focus"):
            # Frame the whole equipment, but take the facing from the anchor -
            # the same prim that supplies the rotation and Z of the placement.
            moved = self._move_camera(str(self._target["equipment"].GetPath()),
                                      self._target["anchor"])
        return self._payload(moved, "Camera moved" if moved else "Camera focus failed")

    def _do_align(self) -> dict:
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        stage = self._get_stage()
        anchor = self._target["anchor"]

        with self._stage_timer("align EBS"):
            target = self.compute_target(stage, self._target["eqp_id"], anchor)
            if target is not None:
                self._aligned = self._place_ebs(self._target["ebs"], target, anchor)
                note = ("EBS placed at port 0, world "
                        f"({target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f})")
            else:
                # No usable rail/port data: keep working off the anchor prim.
                print("[ebs] port geometry unavailable, falling back to the anchor prim")
                self._aligned = self._align_prims(self._target["ebs"], anchor)
                note = "EBS aligned to the anchor prim"
        return self._payload(self._aligned, note if self._aligned else "EBS alignment failed")

    def _do_collide(self) -> dict:
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        if not self._aligned:
            return self._payload(False, "Run Align first")

        cells = self.check_collision(
            self._target["ebs"],
            exclude=[self._target["equipment"], self._target["ebs"]],
            split=self._target["equipment"],
        )
        hit_count = sum(sum(1 for c in v if c) for v in cells.values())
        with self._stage_timer("draw markers"):
            self.show_markers(self._target["ebs"], cells)
        return self._payload(
            True,
            "No collision" if hit_count == 0 else f"{hit_count} cell(s) blocked",
            cells=cells, hit_count=hit_count,
        )

    # -- equipment lookup ----------------------------------------------------

    def build_index(self) -> int:
        """Rebuild the EQP_ prim index.

        Descent stops at each equipment and at any geometry or material prim, so
        the scan never enters the subtrees that hold nearly all of the prims.
        """
        stage = self._get_stage()
        self._eqp_index = {}
        self._bounds_cache = {}
        self._mesh_bounds = {}
        self._triangles = {}
        if stage is None:
            return 0
        visited = 0
        started = time.perf_counter()
        for prim, name in self._walk(stage):
            visited += 1
            if name.startswith(EQP_PREFIX):
                self._eqp_index[name] = str(prim.GetPath())
        self._timings.append([f"build index (visited {visited})",
                              (time.perf_counter() - started) * 1000.0])
        return len(self._eqp_index)

    def _walk(self, stage: Usd.Stage):
        """Yield (prim, upper-case name) for every prim that could be equipment.

        Equipment prims are yielded without descending into them; geometry and
        material subtrees are skipped entirely.
        """
        root = None
        if self._search_root:
            root = stage.GetPrimAtPath(self._search_root)
            if not root.IsValid():
                print(f"[ebs] search root not found, scanning the whole stage: "
                      f"{self._search_root}")
                root = None
        stack = list((root or stage.GetPseudoRoot()).GetChildren())
        while stack:
            prim = stack.pop()
            name = prim.GetName().upper()
            yield prim, name
            if name.startswith(EQP_PREFIX):
                continue              # do not descend into equipment internals
            type_name = prim.GetTypeName()
            if type_name in PRUNE_TYPES or type_name.endswith("Light"):
                continue              # geometry and shading never hold equipment
            stack.extend(prim.GetChildren())

    def get_selected_equipment(self) -> str:
        """Walk up from the selected mesh to the owning equipment prim path."""
        stage = self._get_stage()
        prim = self._resolve_by_selection(stage) if stage else None
        return str(prim.GetPath()) if prim else ""

    def _resolve_by_selection(self, stage: Usd.Stage) -> "Usd.Prim | None":
        paths = omni.usd.get_context().get_selection().get_selected_prim_paths()
        for path in paths:
            prim = stage.GetPrimAtPath(path)
            while prim and prim.IsValid() and prim != stage.GetPseudoRoot():
                if prim.GetName().upper().startswith(EQP_PREFIX):
                    return prim
                prim = prim.GetParent()
        return None

    def _resolve_by_name(self, stage: Usd.Stage, text: str) -> "Usd.Prim | None":
        """Accepts '########', 'EQP_########' or a full prim path."""
        text = text.strip()
        if text.startswith("/"):
            prim = stage.GetPrimAtPath(text)
            return prim if prim.IsValid() else None
        key = text.upper()
        if not key.startswith(EQP_PREFIX):
            key = EQP_PREFIX + key

        path = self._eqp_index.get(key)
        if path:
            prim = stage.GetPrimAtPath(path)
            return prim if prim.IsValid() else None

        # Try the obvious path first: equipment usually sits directly under the
        # search root, and one GetPrimAtPath beats any amount of walking.
        started = time.perf_counter()
        direct = f"{self._search_root.rstrip('/')}/{key}" if self._search_root else f"/{key}"
        prim = stage.GetPrimAtPath(direct)
        if prim.IsValid():
            self._eqp_index[key] = direct
            self._timings.append(["find equipment (direct path)",
                                  (time.perf_counter() - started) * 1000.0])
            return prim

        # Not there: fall back to scanning, stopping at the first match.
        visited = 0
        started = time.perf_counter()
        found = None
        for prim, name in self._walk(stage):
            visited += 1
            if name == key:
                self._eqp_index[key] = str(prim.GetPath())
                found = prim
                break
        self._timings.append([f"find equipment (visited {visited})",
                              (time.perf_counter() - started) * 1000.0])
        return found

    @staticmethod
    def _equipment_id(prim: Usd.Prim) -> str:
        name = prim.GetName()
        return name[len(EQP_PREFIX):] if name.upper().startswith(EQP_PREFIX) else name

    @staticmethod
    def _descend_first_child(prim: Usd.Prim, depth: int) -> Usd.Prim:
        """Follow child(0) down `depth` levels; stop early at the deepest prim."""
        current = prim
        for _ in range(depth):
            children = current.GetChildren()
            if not children:
                break
            current = children[0]
        return current

    # -- port count (XML) ----------------------------------------------------

    def load_ports(self) -> int:
        """Read the XML into port indices, port offsets, addr numbers and cad-x.

        The file is a tree of <group name="..."> blocks holding key/value entries:
        an Addr group carries 'cad-x' and contains one Station group per port,
        each carrying 'port-id' ('<equipment>_<n>') and 'offset'. Document order
        is not port order and unrelated stations sit in between, so ports are
        found by their port-id and sorted by n.
        """
        self._port_map = {}
        self._port_elements = {}
        self._port_offsets = {}
        self._port_addr = {}
        self._port_addr_of = {}
        self._addr_cad = {}
        if not self._xml_path:
            return 0

        with self._stage_timer("parse XML"):
            try:
                root = ET.parse(self._xml_path).getroot()
            except Exception as e:
                print(f"[ebs] XML parse failed: {e}")
                return 0

            parents = {child: parent for parent in root.iter() for child in parent}
            port_pattern = re.compile(r"^([A-Za-z0-9]+)_(\d+)$")
            addr_pattern = re.compile(r"^addr0*(\d+)$", re.IGNORECASE)

            # cad of every addr, including the neighbours that hold no ports
            for elem in root.iter():
                m = addr_pattern.match((elem.get("name", "")
                                        or elem.tag.rsplit("}", 1)[-1]).strip())
                if not m:
                    continue
                cadx = self._as_float(self._key_value(elem, CADX_KEY))
                cady = self._as_float(self._key_value(elem, CADY_KEY))
                if cadx is not None or cady is not None:
                    self._addr_cad[int(m.group(1))] = (cadx or 0.0, cady or 0.0)

            found = {}                       # equipment -> {index: (station, offset, addr)}
            for elem in root.iter():
                station, port_id = self._provider_of(elem, PORT_ID_KEY, parents)
                if not port_id:
                    continue
                m = port_pattern.match(port_id.strip())
                if not m:
                    continue
                equipment, index = m.group(1).upper(), int(m.group(2))
                offset = self._as_float(self._key_value(station, OFFSET_KEY))
                _, addr_number = self._owning_addr(station, parents, addr_pattern)
                found.setdefault(equipment, {})[index] = (station, offset, addr_number)

            for key, by_index in found.items():
                indices = sorted(by_index)
                self._port_map[key] = indices
                self._port_elements[key] = [by_index[i][0] for i in indices]
                self._port_offsets[key] = {i: by_index[i][1] for i in indices}
                by_port = {i: by_index[i][2] for i in indices
                           if by_index[i][2] is not None}
                self._port_addr_of[key] = by_port
                # The port with the highest number sits next to the addr, so its
                # block is the one this equipment belongs to. Lower-numbered ports
                # reach further and can fall into a neighbouring addr's block.
                self._port_addr[key] = by_port[max(by_port)] if by_port else None
                if len(set(by_port.values())) > 1:
                    print(f"[ebs] {key}: ports span several addr blocks {by_port}, "
                          f"base addr {self._port_addr[key]}")
                if indices != list(range(1, len(indices) + 1)):
                    print(f"[ebs] {key}: port indices are not 1..N: {indices}")
        return len(self._port_map)

    # -- XML helpers ---------------------------------------------------------

    def _provider_of(self, elem, key: str, parents: dict):
        """Return (owning group, value) for a key carried by this element.

        A key is either an attribute on a group, or a child entry written as
        key="..." value="...". Both shapes appear in these files.
        """
        value = self._attr(elem, key)
        if value:
            return elem, value
        if (self._attr(elem, "key") or "").lower() == key.lower():
            own = self._attr(elem, "value") or (elem.text or "").strip()
            if own:
                return parents.get(elem, elem), own
        return None, ""

    def _key_value(self, elem, key: str) -> str:
        """Value of a key on a group: its own attribute, or a direct child entry."""
        if elem is None:
            return ""
        value = self._attr(elem, key)
        if value:
            return value
        for child in list(elem):
            if (self._attr(child, "key") or "").lower() == key.lower():
                return self._attr(child, "value") or (child.text or "").strip()
        return ""

    @staticmethod
    def _owning_addr(elem, parents: dict, pattern):
        """Nearest ancestor group whose name looks like 'Addr#####'."""
        current = elem
        while current is not None:
            for candidate in (current.get("name", ""), current.tag.rsplit("}", 1)[-1]):
                m = pattern.match((candidate or "").strip())
                if m:
                    return current, int(m.group(1))
            current = parents.get(current)
        return None, None

    @staticmethod
    def _as_float(text) -> "float | None":
        try:
            return float(str(text).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _attr(elem, name: str) -> str:
        """Attribute lookup that ignores XML namespaces on the attribute name."""
        if elem is None:
            return ""
        value = elem.attrib.get(name)
        if value is not None:
            return value
        for k, v in elem.attrib.items():
            if k.rsplit("}", 1)[-1] == name:
                return v
        return ""

    def get_port_count(self, eqp_id: str) -> "int | None":
        """Number of ports of an equipment, i.e. how many port-ids it has."""
        indices = self.get_port_indices(eqp_id)
        return len(indices) if indices else None

    def get_port_indices(self, eqp_id: str) -> list:
        """Port indices of an equipment, sorted ascending."""
        return list(self._port_map.get(eqp_id.upper(), []))

    # -- rail and port geometry ----------------------------------------------

    def find_rail(self, stage: Usd.Stage, addr_number: int, prefer=()):
        """Pick the rail leaving this addr. Returns (prim, neighbour, axis).

        Several rails can start at the same addr, so the candidates are filtered
        down: only a straight one counts, meaning exactly one of cad-x / cad-y
        differs between the two addrs. When ports spilled into neighbouring
        blocks, the rail running towards those is the one to take.
        """
        prefix = f"{RAIL_PREFIX}{addr_number}_"

        def neighbour_of(prim):
            name = prim.GetName().lower()
            if not name.startswith(prefix):
                return None
            parts = name.split("_")
            return int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None

        found = []
        root = stage.GetPrimAtPath(self._rail_root) if self._rail_root else None
        if root is not None and root.IsValid():
            found = [(p, n) for p in root.GetChildren()
                     if (n := neighbour_of(p)) is not None]
            if not found:
                print(f"[ebs] no {prefix}* under {self._rail_root}, scanning the stage")
        if not found:
            found = [(p, n) for p, _ in self._walk(stage)
                     if (n := neighbour_of(p)) is not None]
        if not found:
            return None, None, None

        straight = []
        for prim, neighbour in found:
            axis = self._rail_axis(addr_number, neighbour)
            if axis is None:
                print(f"[ebs]   skipping {prim.GetName()}: not a straight rail "
                      f"along one cad axis")
                continue
            straight.append((prim, neighbour, axis))
        if not straight:
            print(f"[ebs] {prefix}*: no straight rail among "
                  f"{[p.GetName() for p, _ in found]}")
            return None, None, None

        if len(straight) > 1 and prefer:
            # Ports reached into these addrs, so the rail heading there is ours.
            for prim, neighbour, axis in straight:
                if neighbour in prefer:
                    print(f"[ebs]   {prim.GetName()} chosen: it ends at addr "
                          f"{neighbour}, where a port sits")
                    return prim, neighbour, axis
            base_cad = self._addr_cad.get(addr_number)
            for prim, neighbour, axis in straight:
                if any(abs(self._addr_cad[a][axis] - base_cad[axis]) > 1e-6
                       for a in prefer if a in self._addr_cad):
                    print(f"[ebs]   {prim.GetName()} chosen: it runs along the axis "
                          f"the spilled ports differ on")
                    return prim, neighbour, axis

        if len(straight) > 1:
            print(f"[ebs] {prefix}*: several straight rails "
                  f"{[(p.GetName(), n) for p, n, _ in straight]}, taking the first")
        return straight[0]

    def _rail_axis(self, addr_a: int, addr_b: int) -> "int | None":
        """Axis a rail runs along, or None when it is not straight.

        Only one of cad-x / cad-y may differ between the two addrs; a rail that
        moves on both is a corner, and not one of the rails we place along.
        """
        cad_a, cad_b = self._addr_cad.get(addr_a), self._addr_cad.get(addr_b)
        if cad_a is None or cad_b is None:
            return None
        span = (cad_b[0] - cad_a[0], cad_b[1] - cad_a[1])
        moves = [i for i in (0, 1) if abs(span[i]) > 1e-6]
        return moves[0] if len(moves) == 1 else None

    def compute_rail_point(self, stage: Usd.Stage, eqp_id: str):
        """Where the virtual port 0 sits, in the rail's own space.

        Returns (point, axis, rail) with the point as a Gf.Vec3d and axis 0 or
        1 for the axis the rail runs along. The addr sits at the rail's start point
        and the rail prim at its midpoint, so the start is half a rail length
        back from it. Ports run from the addr along the rail at a constant
        spacing - on the line the order is 1, 2, (3), addr - and port 0 is one
        more step past port 1.

        A rail runs along X or Y: only one of cad-x / cad-y differs between the
        two addrs, and that difference gives both the axis and the direction.
        """
        key = eqp_id.upper()
        addr_a = self._port_addr.get(key)
        if addr_a is None:
            print(f"[ebs] {key}: no addr block found for its ports")
            return None

        # Ports that spilled into other blocks tell us which rail to follow.
        spilled = {a for i, a in self._port_addr_of.get(key, {}).items()
                   if a != addr_a}
        rail, addr_b, axis = self.find_rail(stage, addr_a, prefer=spilled)
        if rail is None:
            print(f"[ebs] {key}: no straight {RAIL_PREFIX}{addr_a}_* rail found")
            return None

        cad_a, cad_b = self._addr_cad[addr_a], self._addr_cad[addr_b]
        span = (cad_b[0] - cad_a[0], cad_b[1] - cad_a[1])

        length = span[axis] / CAD_PER_UNIT                   # signed: carries direction
        direction = 1.0 if length >= 0 else -1.0

        offsets = self._rebase_offsets(key, addr_a, axis, direction)
        spacing = self._port_spacing(key, offsets)
        if spacing is None:
            return None
        rail_local = self._local_translation(rail)
        start = rail_local[axis] - length / 2.0              # rail start = addr position
        offset_zero = offsets[1] + spacing                   # one step past port 1
        shift = direction * offset_zero / OFFSET_PER_UNIT

        coords = [rail_local[0], rail_local[1], rail_local[2]]
        coords[axis] = start + shift                         # the other axes stay as-is
        point = Gf.Vec3d(*coords)

        name = "XY"[axis]
        gaps = [f"{offsets[i] - offsets[i + 1]:.1f}"
                for i in sorted(offsets) if i + 1 in offsets]
        print(f"[ebs] {key}: addr {addr_a} -> rail {rail.GetName()} (neighbour {addr_b})")
        print(f"[ebs]   cad {cad_a} -> {cad_b}, span ({span[0]:+.3f}, {span[1]:+.3f})"
              f" -> runs along {name}, direction {direction:+.0f}")
        print(f"[ebs]   length {span[axis]:+.3f} / {CAD_PER_UNIT:.4f} = {length:+.4f} units")
        print(f"[ebs]   rail.{name.lower()} {rail_local[axis]:.4f} - {length:+.4f}/2 "
              f"= start {start:.4f}")
        print(f"[ebs]   offsets " +
              ", ".join(f"{i}:{offsets[i]:.1f}" for i in sorted(offsets)) +
              f" | gaps [{', '.join(gaps)}] -> spacing {spacing:.1f}")
        print(f"[ebs]   offset0 = {offsets[1]:.1f} + {spacing:.1f} = {offset_zero:.1f}"
              f" / {OFFSET_PER_UNIT:.0f} = {offset_zero / OFFSET_PER_UNIT:.4f} units")
        print(f"[ebs]   {name.lower()} = {start:.4f} {shift:+.4f} = {point[axis]:.4f}"
              f" (rail's other axes kept)")
        return point, axis, rail

    def _rebase_offsets(self, key: str, base_addr: int, axis: int,
                        direction: float) -> dict:
        """Offsets of every port measured from the same addr.

        A port's offset is measured from the addr block it is written in, and
        the lower-numbered ports reach far enough to fall into a neighbouring
        block. Such an offset is shifted by the distance between that addr and
        the base one, so all of them share an origin again.
        """
        offsets = dict(self._port_offsets.get(key, {}))
        addr_of = self._port_addr_of.get(key, {})
        base_cad = self._addr_cad.get(base_addr)
        if base_cad is None:
            return offsets

        for index, offset in list(offsets.items()):
            addr = addr_of.get(index)
            if addr is None or addr == base_addr:
                continue
            cad = self._addr_cad.get(addr)
            if cad is None:
                print(f"[ebs] {key}: port {index} sits in addr {addr}, which has no cad")
                continue
            gap = (cad[axis] - base_cad[axis]) / CAD_PER_UNIT      # signed, in units
            shift = direction * gap * OFFSET_PER_UNIT
            offsets[index] = offset + shift
            print(f"[ebs]   port {index} is in addr {addr}, not {base_addr}: "
                  f"{offset:.1f} {shift:+.1f} = {offsets[index]:.1f} "
                  f"(addr gap {gap:+.4f} units)")
        return offsets

    def _port_spacing(self, key: str, offsets: dict) -> "float | None":
        """Distance between neighbouring ports, averaged over the pairs present.

        Two ports give one gap; a third gives a second one and the two are
        averaged. Ports sit at a constant spacing with the lower numbers
        further from the addr, so each gap is offset(n) - offset(n + 1).
        """
        gaps = [offsets[i] - offsets[i + 1]
                for i in sorted(offsets) if i + 1 in offsets]
        if 1 not in offsets or not gaps:
            print(f"[ebs] {key}: needs the offsets of ports 1 and 2, got {offsets}")
            return None

        spacing = sum(gaps) / len(gaps)
        if spacing <= 0:
            print(f"[ebs] {key}: ports should get closer to the addr as the number "
                  f"rises, got {offsets}")
        if len(gaps) > 1 and max(gaps) - min(gaps) > 1e-6:
            print(f"[ebs] {key}: port spacing is uneven {gaps}, using {spacing}")
        return spacing

    def compute_target(self, stage: Usd.Stage, eqp_id: str, anchor: Usd.Prim):
        """World point the EBS has to sit on.

        X and Y come from the rail: the axis it runs along carries the virtual
        port 0, the other axis keeps the rail's own value. That point is lifted
        into world space. Z stays the anchor prim's world Z. The rail, the
        anchor and the EBS live under different parents, so everything meets in
        world space.
        """
        found = self.compute_rail_point(stage, eqp_id)
        if found is None:
            return None
        in_rail_space, _, rail = found

        world = self._parent_world(rail).Transform(in_rail_space)
        anchor_world = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()).ExtractTranslation()
        target = Gf.Vec3d(world[0], world[1], anchor_world[2])
        print(f"[ebs]   rail space ({in_rail_space[0]:.4f}, {in_rail_space[1]:.4f}, "
              f"{in_rail_space[2]:.4f}) -> world ({world[0]:.4f}, {world[1]:.4f}, "
              f"{world[2]:.4f})")
        print(f"[ebs]   target = ({target[0]:.4f}, {target[1]:.4f}, {target[2]:.4f})"
              f"  [rail xy, anchor z from {anchor.GetName()}]")
        return target

    @staticmethod
    def _local_translation(prim: Usd.Prim) -> Gf.Vec3d:
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return Gf.Vec3d(0.0, 0.0, 0.0)
        return xformable.GetLocalTransformation(
            Usd.TimeCode.Default()).ExtractTranslation()

    @staticmethod
    def _parent_world(prim: Usd.Prim) -> Gf.Matrix4d:
        """Local-to-world transform of a prim's parent, i.e. the space its
        local translation is expressed in."""
        parent = prim.GetParent() if prim else None
        if parent and parent.IsValid() and UsdGeom.Xformable(parent):
            return UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default())
        return Gf.Matrix4d(1.0)

    # -- alignment -----------------------------------------------------------

    def _place_ebs(self, ebs_prim: Usd.Prim, world_position: Gf.Vec3d,
                   anchor: Usd.Prim) -> bool:
        """Put the EBS on a world point, rotated like the anchor prim.

        The point and the anchor's orientation are both brought into the EBS
        parent's space before they are written, so the EBS lands on that exact
        world point whatever its own parent does.
        """
        stage = self._get_stage()
        xformable = UsdGeom.Xformable(ebs_prim)
        if stage is None or not xformable:
            return False

        tc = Usd.TimeCode.Default()
        to_ebs_space = self._parent_world(ebs_prim).GetInverse()
        anchor_world = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(tc)
        rotation = self._normalized_rows(anchor_world * to_ebs_space)
        scale = self._extract_scale(xformable.GetLocalTransformation(tc))
        local = to_ebs_space.Transform(world_position)
        print(f"[ebs]   EBS local translate ({local[0]:.4f}, {local[1]:.4f}, "
              f"{local[2]:.4f}), rotation from {anchor.GetName()}")
        return self._write_transform(stage, xformable, rotation, scale, local)

    def _align_prims(self, ebs_prim: Usd.Prim, anchor_prim: Usd.Prim) -> bool:
        """Match the EBS position and rotation to the anchor, keeping its own scale."""
        stage = self._get_stage()
        if stage is None or not anchor_prim.IsValid():
            return False
        xformable = UsdGeom.Xformable(ebs_prim)
        if not xformable:
            return False

        tc = Usd.TimeCode.Default()
        anchor_world = UsdGeom.Xformable(anchor_prim).ComputeLocalToWorldTransform(tc)

        # Row-vector convention: M_local * M_parent = M_world
        target_local = anchor_world * self._parent_world(ebs_prim).GetInverse()

        # The anchor sits deep inside the equipment and carries its own scale.
        # Take only its orientation and position; the EBS keeps the scale it had.
        rotation = self._normalized_rows(target_local)
        scale = self._extract_scale(xformable.GetLocalTransformation(tc))
        return self._write_transform(stage, xformable, rotation, scale,
                                     target_local.ExtractTranslation())

    def _write_transform(self, stage, xformable, rotation, scale, translation) -> bool:
        """Write position and orientation into the prim's existing xform ops.

        Prims here usually carry translate / orient / scale ops, so those are
        set in place: the scale op is left alone (the EBS keeps its own size)
        and nothing about the op stack changes. Only when there is no usable
        op does this fall back to authoring a single transform op.
        """
        ops = {op.GetOpName(): op for op in xformable.GetOrderedXformOps()}

        # Author into the session layer so the source layers stay untouched.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if "xformOp:transform" in ops:
                ops["xformOp:transform"].Set(
                    self._compose(rotation, scale, translation))
                return True

            if "xformOp:translate" in ops and self._set_rotation(ops, rotation):
                ops["xformOp:translate"].Set(Gf.Vec3d(translation))
                return True

            print("[ebs] no usable xform ops, authoring a single transform op")
            try:
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(
                    self._compose(rotation, scale, translation))
                return True
            except Exception as e:
                print(f"[ebs] transform op failed, translate only: {e}")
                api = UsdGeom.XformCommonAPI(xformable.GetPrim())
                return bool(api and api.SetTranslate(Gf.Vec3d(translation)))

    def _set_rotation(self, ops: dict, rotation) -> bool:
        """Set whichever rotation op the prim already has. False if it has none."""
        matrix = self._compose(rotation, Gf.Vec3d(1.0, 1.0, 1.0),
                               Gf.Vec3d(0.0, 0.0, 0.0))
        orient = ops.get("xformOp:orient")
        if orient is not None:
            quat = matrix.ExtractRotationQuat()
            precision = orient.GetPrecision()
            if precision == UsdGeom.XformOp.PrecisionFloat:
                quat = Gf.Quatf(quat)
            elif precision == UsdGeom.XformOp.PrecisionHalf:
                quat = Gf.Quath(Gf.Quatf(quat))
            orient.Set(quat)
            return True

        for order in ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"):
            op = ops.get(f"xformOp:rotate{order}")
            if op is not None:
                angles = self._euler(rotation, order)
                op.Set(Gf.Vec3f(*angles) if op.GetPrecision() ==
                       UsdGeom.XformOp.PrecisionFloat else Gf.Vec3d(*angles))
                return True
        return False

    @staticmethod
    def _compose(rotation, scale, translation) -> Gf.Matrix4d:
        """Build a matrix from rotation rows, per-axis scale and a translation."""
        return Gf.Matrix4d(
            rotation[0][0] * scale[0], rotation[0][1] * scale[0], rotation[0][2] * scale[0], 0.0,
            rotation[1][0] * scale[1], rotation[1][1] * scale[1], rotation[1][2] * scale[1], 0.0,
            rotation[2][0] * scale[2], rotation[2][1] * scale[2], rotation[2][2] * scale[2], 0.0,
            translation[0], translation[1], translation[2], 1.0,
        )

    @staticmethod
    def _euler(rotation, order: str = "XYZ") -> tuple:
        """Euler angles in degrees for a rotation given as three unit rows.

        USD applies rotate<ABC> as Ra * Rb * Rc on row vectors. The angles are
        recovered for XYZ and the other orders follow by permuting the axes.
        """
        axes = {"X": 0, "Y": 1, "Z": 2}
        a, b, c = (axes[ch] for ch in order)          # first, second, third axis
        m = [[rotation[i][j] for j in range(3)] for i in range(3)]

        # Reorder rows and columns so the maths below is always the XYZ case.
        p = [a, b, c]
        sign = 1.0 if order in ("XYZ", "YZX", "ZXY") else -1.0
        r = [[m[p[i]][p[j]] for j in range(3)] for i in range(3)]

        beta = math.asin(max(-1.0, min(1.0, -sign * r[0][2])))
        if abs(math.cos(beta)) < 1e-9:                 # gimbal lock
            alpha = 0.0
            gamma = math.atan2(-sign * r[1][0], r[1][1])
        else:
            alpha = math.atan2(sign * r[1][2], r[2][2])
            gamma = math.atan2(sign * r[0][1], r[0][0])

        values = [0.0, 0.0, 0.0]
        values[a], values[b], values[c] = (math.degrees(alpha), math.degrees(beta),
                                           math.degrees(gamma))
        return tuple(values)

    @staticmethod
    def _normalized_rows(matrix: Gf.Matrix4d) -> list:
        """Rotation rows of a matrix with any scale divided out."""
        rows = matrix.ExtractRotationMatrix()
        return [Gf.Vec3d(rows[i][0], rows[i][1], rows[i][2]).GetNormalized()
                for i in range(3)]

    @staticmethod
    def _extract_scale(matrix: Gf.Matrix4d) -> Gf.Vec3d:
        """Per-axis scale of a matrix, i.e. the length of each rotation row."""
        rows = matrix.ExtractRotationMatrix()
        scale = [Gf.Vec3d(rows[i][0], rows[i][1], rows[i][2]).GetLength() for i in range(3)]
        return Gf.Vec3d(*[v if v > 1e-12 else 1.0 for v in scale])

    # -- collision -----------------------------------------------------------

    def check_collision(self, ebs_prim: Usd.Prim, exclude: list = None,
                        split: Usd.Prim = None) -> dict:
        """Test the left, right and ceiling faces as 3x3 grids.

        `split` is an equipment whose meshes are tested one by one instead of
        as a single box: the EBS is mounted on it, so its overall bounds always
        overlap, but individual parts of it can still catch on the faces.

        Faces and cells follow the EBS prim's local axes, named as the front
        camera sees them: right is +X, left is -X, ceiling is +up.
        """
        stage = self._get_stage()
        if stage is None:
            return {face: [] for face in FACES}
        self._visible = {}          # visibility can change between runs

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,          # read extentsHint instead of walking geometry
        )

        with self._stage_timer("EBS bounds"):
            # GfBBox3d: GetRange() is the local box, GetMatrix() maps it to world.
            ebs_bbox = self._ebs_bound(ebs_prim)
            local_box = ebs_bbox.GetRange()
            to_world = ebs_bbox.GetMatrix()
            world_box = ebs_bbox.ComputeAlignedRange()
        if local_box.IsEmpty():
            return {face: [] for face in FACES}

        with self._stage_timer("build cells"):
            # Cells are cut along the local axes, then converted to world AABBs.
            cells = {
                face: [Gf.BBox3d(rng, to_world).ComputeAlignedRange()
                       for rng, _ in boxes]
                for face, boxes in self._build_cells(local_box).items()
            }

        with self._stage_timer(f"equipment bounds ({len(self._eqp_index)})"):
            missing = self._cache_bounds(stage, cache)

        with self._stage_timer("broad phase"):
            # Keep only equipment overlapping the EBS box grown by the clearance.
            margin = Gf.Vec3d(self._clearance, self._clearance, self._clearance)
            search = Gf.Range3d(world_box.GetMin() - margin, world_box.GetMax() + margin)
            skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
            overlapping = [(path, box) for path, box in self._bounds_cache.items()
                           if not any(path == s or path.startswith(s + "/") for s in skip)
                           and self._overlaps(box, search)]
            near = [(path, box) for path, box in overlapping
                    if self._is_visible(stage, path)]
            hidden = len(overlapping) - len(near)

        # Narrow down: one box per mesh, then the mesh triangles themselves.
        # Each pass only sees what the previous one let through.
        candidates = list(near)
        if self._precision in (PRECISION_MESH, PRECISION_TRI):
            with self._stage_timer("mesh bounds"):
                refined = []
                for path, _ in near:
                    prim = stage.GetPrimAtPath(path)
                    for mesh_path, box in self._geometry_bounds(prim, cache):
                        if not self._overlaps(box, search):
                            continue
                        if not self._is_visible(stage, mesh_path):
                            hidden += 1
                            continue
                        refined.append((mesh_path, box))
                candidates = refined
        coarse = len(candidates)
        if split is not None and split.IsValid():
            candidates += [(path, box)
                           for path, box in self._geometry_bounds(split, cache)
                           if self._overlaps(box, search)
                           and self._is_visible(stage, path)]

        size = local_box.GetMax() - local_box.GetMin()
        self._note(f"precision {self._precision}, clearance {self._clearance}, "
                   f"EBS size ({size[0]:.3f}, {size[1]:.3f}, {size[2]:.3f})")
        self._note(f"EBS local box {tuple(round(v, 3) for v in local_box.GetMin())} .. "
                   f"{tuple(round(v, 3) for v in local_box.GetMax())} "
                   f"(the cells tile exactly this)")
        self._note(f"EBS world box {tuple(round(v, 2) for v in world_box.GetMin())} .. "
                   f"{tuple(round(v, 2) for v in world_box.GetMax())}")
        self._note(f"{len(self._bounds_cache)} equipment -> {len(near)} near -> "
                   f"{coarse} candidates + {len(candidates) - coarse} own meshes"
                   + (f", {hidden} hidden skipped" if hidden else ""))

        with self._stage_timer(f"cell test ({len(candidates)} candidates)"):
            result = {face: [False] * len(boxes) for face, boxes in cells.items()}
            hits = {}
            triangle_tests = 0
            boxed_only = set()
            flat = [(face, i, cell)
                    for face, boxes in cells.items() for i, cell in enumerate(boxes)]

            # Walk the candidates, not the cells: a mesh's triangles are then
            # read and tested once each, against only the cells its box reaches.
            for path, box in candidates:
                targets = [entry for entry in flat
                           if not result[entry[0]][entry[1]]
                           and self._overlaps(box, entry[2])]
                if not targets:
                    continue

                triangles = (self._mesh_triangles(stage, path)
                             if self._precision == PRECISION_TRI else None)
                if triangles:
                    triangle_tests += len(triangles)
                    for triangle in triangles:
                        remaining = [e for e in targets if not result[e[0]][e[1]]]
                        if not remaining:
                            break
                        lo = [min(v[i] for v in triangle) for i in range(3)]
                        hi = [max(v[i] for v in triangle) for i in range(3)]
                        tri_box = Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))
                        for face, i, cell in remaining:
                            # A flat triangle has a flat box, so this pre-filter
                            # only asks for contact; the exact test decides.
                            if (not Gf.Range3d.GetIntersection(tri_box, cell).IsEmpty()
                                    and self._triangle_hits_box(triangle, cell)):
                                result[face][i] = True
                                hits.setdefault(path.rsplit("/", 1)[-1], []).append(
                                    f"{face}[{i}]")
                    continue

                if self._precision == PRECISION_TRI:
                    # Nothing to test: keep the box result, but say so rather
                    # than let it pass as a triangle hit.
                    boxed_only.add(path)
                for face, i, _ in targets:
                    result[face][i] = True
                    hits.setdefault(path.rsplit("/", 1)[-1], []).append(f"{face}[{i}]")

            if triangle_tests:
                self._note(f"{triangle_tests} triangle tests")
            elif self._precision == PRECISION_TRI and candidates:
                self._note("no candidate reached a cell, so no triangle was tested")
            if boxed_only:
                self._note(f"{len(boxed_only)} of the blocking prims had no triangles, "
                           f"judged by box: "
                           f"{', '.join(sorted(p.rsplit('/', 1)[-1] for p in boxed_only))}")

        if hits:
            for name, where in sorted(hits.items()):
                self._note(f"hit by {name}: {', '.join(where)}")
        elif candidates:
            self._note("candidates were near but none reached a cell")
        else:
            self._note("nothing within clearance - raise it if that looks wrong")

        if missing:
            print(f"[ebs] {missing} equipment prim(s) had no usable bound")
        return result

    def _mesh_triangles(self, stage, path: str) -> list:
        """World-space triangles of one mesh, cached by prim path.

        Only meshes that survived the box passes ever get here, so the point
        arrays of the whole stage are never read. Returns an empty list when the
        prim carries no triangles to test - a Cube or a Sphere, or a mesh whose
        points cannot be read - and the caller has to decide what that means.
        """
        if path in self._triangles:
            return self._triangles[path]

        triangles = []
        prim = stage.GetPrimAtPath(path)
        mesh = UsdGeom.Mesh(prim) if prim and prim.IsValid() else None
        if mesh:
            tc = Usd.TimeCode.Default()
            points = self._attr_value(mesh.GetPointsAttr(), tc)
            counts = self._attr_value(mesh.GetFaceVertexCountsAttr(), tc)
            indices = self._attr_value(mesh.GetFaceVertexIndicesAttr(), tc)
            if points is None or counts is None or indices is None:
                self._note(f"{path.rsplit('/', 1)[-1]}: no point data, "
                           f"falling back to its box")
            else:
                to_world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(tc)
                world = [to_world.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in points]
                cursor = 0
                for count in counts:
                    if count >= 3 and cursor + count <= len(indices):
                        fan = [world[indices[cursor + k]] for k in range(count)]
                        # Triangulate the face as a fan around its first vertex.
                        for k in range(1, count - 1):
                            triangles.append((fan[0], fan[k], fan[k + 1]))
                    cursor += count
        elif prim and prim.IsValid():
            self._note(f"{path.rsplit('/', 1)[-1]} is a "
                       f"{prim.GetTypeName()}, tested as its box")
        self._triangles[path] = triangles
        return triangles

    @staticmethod
    def _attr_value(attr, tc):
        """Attribute value at the default time, or at its first time sample."""
        if not attr or not attr.IsValid():
            return None
        value = attr.Get(tc)
        if value is None or len(value) == 0:
            samples = attr.GetTimeSamples()
            if samples:
                value = attr.Get(samples[0])
        return value if value is not None and len(value) else None

    @staticmethod
    def _triangle_hits_box(triangle, box: Gf.Range3d) -> bool:
        """Exact triangle / axis-aligned box overlap (separating axis test)."""
        lo, hi = box.GetMin(), box.GetMax()
        centre = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        half = [(hi[i] - lo[i]) * 0.5 for i in range(3)]
        v = [[triangle[j][i] - centre[i] for i in range(3)] for j in range(3)]

        # 1. the box's own three axes
        for i in range(3):
            if min(v[0][i], v[1][i], v[2][i]) > half[i] or \
               max(v[0][i], v[1][i], v[2][i]) < -half[i]:
                return False

        edges = [[v[1][i] - v[0][i] for i in range(3)],
                 [v[2][i] - v[1][i] for i in range(3)],
                 [v[0][i] - v[2][i] for i in range(3)]]

        # 2. the triangle's plane
        normal = [edges[0][1] * edges[1][2] - edges[0][2] * edges[1][1],
                  edges[0][2] * edges[1][0] - edges[0][0] * edges[1][2],
                  edges[0][0] * edges[1][1] - edges[0][1] * edges[1][0]]
        reach = sum(half[i] * abs(normal[i]) for i in range(3))
        distance = sum(normal[i] * v[0][i] for i in range(3))
        if abs(distance) > reach:
            return False

        # 3. the nine edge / box-axis cross products
        for edge in edges:
            for i in range(3):
                j, k = (i + 1) % 3, (i + 2) % 3
                axis = [0.0, 0.0, 0.0]
                axis[j], axis[k] = -edge[k], edge[j]
                if abs(axis[j]) < 1e-12 and abs(axis[k]) < 1e-12:
                    continue
                projected = [sum(axis[m] * v[n][m] for m in range(3)) for n in range(3)]
                reach = sum(half[m] * abs(axis[m]) for m in range(3))
                if min(projected) > reach or max(projected) < -reach:
                    return False
        return True

    def _is_visible(self, stage, path: str) -> bool:
        """Whether a prim renders, inherited visibility included.

        Answers are memoised for the run: ComputeVisibility walks the ancestors
        every time, and the same branches come up again and again.
        """
        if path in self._visible:
            return self._visible[path]
        prim = stage.GetPrimAtPath(path)
        visible = True
        if prim and prim.IsValid():
            imageable = UsdGeom.Imageable(prim)
            if imageable:
                visible = imageable.ComputeVisibility(
                    Usd.TimeCode.Default()) != UsdGeom.Tokens.invisible
        self._visible[path] = visible
        return visible

    @staticmethod
    def _overlaps(a: Gf.Range3d, b: Gf.Range3d) -> bool:
        """True when two boxes share volume, not just a face.

        The EBS is mounted on its equipment, so surfaces line up exactly all the
        time; requiring a real overlap keeps those from reading as blocked.
        """
        overlap = Gf.Range3d.GetIntersection(a, b)
        if overlap.IsEmpty():
            return False
        extent = overlap.GetMax() - overlap.GetMin()
        return all(extent[i] > OVERLAP_EPS for i in range(3))

    def _ebs_bound(self, prim: Usd.Prim):
        """Bound of the EBS itself, computed from its geometry.

        The scene scan reads extentsHint because it has hundreds of prims to
        get through, but a hint can be stale or padded and this one box decides
        where every cell and marker goes, so it is measured directly. When the
        two disagree the run says so.
        """
        exact = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        ).ComputeWorldBound(prim)

        hinted = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,
        ).ComputeWorldBound(prim).ComputeAlignedRange()
        measured = exact.ComputeAlignedRange()
        if not hinted.IsEmpty() and not measured.IsEmpty():
            slack = max(abs(hinted.GetMin()[i] - measured.GetMin()[i]) for i in range(3))
            slack = max(slack, max(abs(hinted.GetMax()[i] - measured.GetMax()[i])
                                   for i in range(3)))
            span = max(measured.GetMax()[i] - measured.GetMin()[i] for i in range(3))
            if span > 0 and slack > span * 0.01:
                self._note(f"the EBS extentsHint is off by {slack:.3f}, "
                           f"using the measured bound")
        return exact

    def _geometry_bounds(self, prim: Usd.Prim, cache) -> list:
        """World bounds of each geometry prim under `prim`, cached per equipment.

        The equipment does not move, so this is paid once. Descent stops at each
        geometry prim, so nested meshes are counted once and materials are not
        walked at all.
        """
        key = str(prim.GetPath())
        if key in self._mesh_bounds:
            return self._mesh_bounds[key]

        bounds = []
        stack = [prim]
        while stack:
            current = stack.pop()
            type_name = current.GetTypeName()
            if type_name in GEOMETRY_TYPES:
                box = cache.ComputeWorldBound(current).ComputeAlignedRange()
                if not box.IsEmpty():
                    bounds.append((str(current.GetPath()), box))
                continue
            if type_name in PRUNE_TYPES or type_name.endswith("Light"):
                continue
            stack.extend(current.GetChildren())
        self._mesh_bounds[key] = bounds
        return bounds

    def _cache_bounds(self, stage, cache) -> int:
        """Compute and cache the world bound of every equipment prim.

        Equipment does not move, so this is paid once and reused by later runs.
        """
        missing = 0
        for path in self._eqp_index.values():
            if path in self._bounds_cache:
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                missing += 1
                continue
            box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if box.IsEmpty():
                missing += 1
                continue
            self._bounds_cache[path] = box
        return missing

    def _build_cells(self, box: Gf.Range3d) -> dict:
        """Cells per face in the prim's local axes, left to right, top to bottom.

        +/-Y is the front/back (not evaluated), +/-X the sides, +up the ceiling.
        The camera stands on local -Y and looks toward +Y, so screen-right is
        +X and the grids read the same way round as the view does.

        The longest edge of the EBS is cut into GRID divisions and every other
        edge takes the whole number of those that fits it best, so the cells come
        out as square as the box allows: 5x5 on a cube, 5x3 on a flatter one.
        """
        up_axis = 1 if UsdGeom.GetStageUpAxis(self._get_stage()) == UsdGeom.Tokens.y else 2
        front_axis = 3 - up_axis                        # front/back, not evaluated
        side_axis = 3 - up_axis - front_axis            # sides (X when Z-up)
        t = self._clearance

        lo, hi = box.GetMin(), box.GetMax()
        extent = [hi[i] - lo[i] for i in range(3)]
        unit = max(extent) / GRID if max(extent) > 0 else 1.0
        divisions = [max(1, int(round(extent[i] / unit))) if unit > 0 else 1
                     for i in range(3)]

        cells = {}
        shapes = {}

        def make(fixed_axis, outward, row_axis, col_axis):
            """Cells as (probe range, surface quad) in the box's own space."""
            rows, cols = divisions[row_axis], divisions[col_axis]
            out = []
            row_lo, row_hi = lo[row_axis], hi[row_axis]
            col_lo, col_hi = lo[col_axis], hi[col_axis]
            row_step = (row_hi - row_lo) / rows
            col_step = (col_hi - col_lo) / cols
            for r in range(rows):
                for c in range(cols):
                    cmin, cmax = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
                    # rows are flipped so index 0 is the top row
                    cmin[row_axis] = row_hi - (r + 1) * row_step
                    cmax[row_axis] = row_hi - r * row_step
                    cmin[col_axis] = col_lo + c * col_step
                    cmax[col_axis] = col_lo + (c + 1) * col_step
                    if outward > 0:
                        surface = hi[fixed_axis]
                        cmin[fixed_axis], cmax[fixed_axis] = surface, surface + t
                    else:
                        surface = lo[fixed_axis]
                        cmin[fixed_axis], cmax[fixed_axis] = surface - t, surface

                    # The quad sits flat on the EBS surface, spanning the cell.
                    quad = []
                    for r_end, c_end in ((0, 0), (0, 1), (1, 1), (1, 0)):
                        corner = [0.0, 0.0, 0.0]
                        corner[fixed_axis] = surface
                        corner[row_axis] = cmax[row_axis] if r_end else cmin[row_axis]
                        corner[col_axis] = cmax[col_axis] if c_end else cmin[col_axis]
                        quad.append(tuple(corner))
                    out.append((Gf.Range3d(Gf.Vec3d(*cmin), Gf.Vec3d(*cmax)), quad))
            return out, (rows, cols)

        # Looking along +Y with the up axis up puts +X on the right of the screen.
        for face, args in ((FACE_RIGHT,   (side_axis, +1, up_axis, front_axis)),
                           (FACE_LEFT,    (side_axis, -1, up_axis, front_axis)),
                           (FACE_CEILING, (up_axis,   +1, front_axis, side_axis))):
            cells[face], shapes[face] = make(*args)
        self._grid_shape = shapes
        return cells

    def get_grid_shape(self) -> dict:
        """Rows and columns of each face's grid, as of the last run."""
        return dict(self._grid_shape)

    # -- collision markers ---------------------------------------------------

    def show_markers(self, ebs_prim: Usd.Prim, cells: dict) -> int:
        """Draw the 3x3 grids as translucent quads on the EBS surfaces.

        Everything is rebuilt from scratch on each run and lives in the session
        layer, so the markers never reach the source layers.
        """
        stage = self._get_stage()
        if stage is None:
            return 0
        self.clear_markers()

        bbox = self._ebs_bound(ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty():
            return 0

        drawn = 0
        built = self._build_cells(local_box)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, MARKER_ROOT)
            # Four materials rather than two: neighbouring cells alternate
            # between the plain colour and a shaded one, so the grid lines read
            # without drawing any.
            materials = {}
            for blocked, base in ((True, COLOR_BLOCKED), (False, COLOR_CLEAR)):
                for dark in (False, True):
                    colour = self.shade(base, dark)
                    name = f"{'blocked' if blocked else 'clear'}{'_dark' if dark else ''}"
                    materials[(blocked, dark)] = (
                        self._marker_material(stage, name, colour), colour)

            for face, boxes in built.items():
                flags = cells.get(face, [])
                cols = self._grid_shape.get(face, (1, 1))[1]
                for i, (_, quad) in enumerate(boxes):
                    blocked = bool(i < len(flags) and flags[i])
                    dark = ((i // cols) + (i % cols)) % 2 == 1
                    material, colour = materials[(blocked, dark)]
                    points = [to_world.Transform(Gf.Vec3d(*corner)) for corner in quad]
                    self._marker_quad(stage, f"{MARKER_ROOT}/{face}_{i}", points,
                                      material, colour)
                    drawn += 1
        print(f"[ebs] drew {drawn} collision markers under {MARKER_ROOT}")
        return drawn

    def clear_markers(self) -> None:
        """Remove the markers drawn by the previous run."""
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(MARKER_ROOT).IsValid():
                stage.RemovePrim(MARKER_ROOT)

    @staticmethod
    def shade(colour, dark: bool):
        """The same colour, dimmed a little, for the checkerboard's other square."""
        return tuple(c * CHECKER_SHADE for c in colour) if dark else tuple(colour)

    @staticmethod
    def _marker_quad(stage, path: str, points: list, material, color) -> None:
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4]))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([0, 1, 2, 3]))
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
        mesh.CreateDisplayOpacityAttr(Vt.FloatArray([MARKER_OPACITY]))
        if material:
            UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(material)

    @staticmethod
    def _marker_material(stage, name: str, color):
        """Translucent preview surface, one per state, reused by every quad."""
        path = f"{MARKER_ROOT}/Looks/{name}"
        material = UsdShade.Material.Define(stage, path)
        shader = UsdShade.Shader.Define(stage, path + "/shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(color[0] * 0.6, color[1] * 0.6, color[2] * 0.6))
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(MARKER_OPACITY)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return material

    # -- camera --------------------------------------------------------------

    def make_camera(self) -> bool:
        """Create the camera this extension drives, replacing any earlier one.

        It lives in the session layer, so it never reaches the saved scene, and
        the viewport is switched to it - which is what keeps the user's own
        perspective camera untouched.
        """
        stage = self._get_stage()
        if stage is None:
            return False
        self.release_camera()

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera = UsdGeom.Camera.Define(stage, CAMERA_PATH)
            camera.CreateFocalLengthAttr(50.0)
            camera.CreateHorizontalApertureAttr(20.955)
            camera.CreateVerticalApertureAttr(15.2908)
            camera.CreateClippingRangeAttr(Gf.Vec2f(0.1, 1.0e6))
            prim = camera.GetPrim()
            prim.CreateAttribute("omni:kit:centerOfInterest",
                                 Sdf.ValueTypeNames.Vector3d).Set(
                                     Gf.Vec3d(0.0, 0.0, -100.0))

        viewport = self._viewport()
        if viewport is not None:
            self._previous_camera = str(viewport.camera_path)
            try:
                viewport.camera_path = CAMERA_PATH
            except Exception as e:
                print(f"[ebs] could not switch the viewport camera: {e}")
        self._note(f"camera {CAMERA_PATH} created")
        return True

    def release_camera(self) -> None:
        """Give the viewport back its camera and delete ours."""
        stage = self._get_stage()
        if stage is None:
            return
        viewport = self._viewport()
        if viewport is not None and self._previous_camera:
            try:
                viewport.camera_path = self._previous_camera
            except Exception as e:
                print(f"[ebs] could not restore the viewport camera: {e}")
        self._previous_camera = None
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(CAMERA_PATH).IsValid():
                stage.RemovePrim(CAMERA_PATH)

    @staticmethod
    def _viewport():
        try:
            from omni.kit.viewport.utility import get_active_viewport
            return get_active_viewport()
        except Exception as e:
            print(f"[ebs] viewport utility unavailable: {e}")
            return None

    def _move_camera(self, prim_path: str, facing: Usd.Prim = None) -> bool:
        """Fill the view with the prim, seen from the facing prim's local -Y.

        The camera stands on the facing prim's local -Y side and looks toward
        +Y, centred on it, at the distance that just fits the framed prim. Its
        clipping range is then closed in around the target, so whatever sits in
        front of or behind it drops out of the view while the sides stay.
        """
        stage = self._get_stage()
        viewport = self._viewport()
        if stage is None or viewport is None:
            return False
        prim = stage.GetPrimAtPath(prim_path)
        cam_prim = stage.GetPrimAtPath(CAMERA_PATH)
        if not prim.IsValid():
            return False
        if not cam_prim.IsValid():
            print("[ebs] no camera - run Init first")
            return False
        if str(viewport.camera_path) != CAMERA_PATH:
            try:
                viewport.camera_path = CAMERA_PATH
            except Exception as e:
                print(f"[ebs] could not switch the viewport camera: {e}")

        facing = facing if (facing is not None and facing.IsValid()) else prim
        tc = Usd.TimeCode.Default()
        rot = UsdGeom.Xformable(facing).ComputeLocalToWorldTransform(
            tc).ExtractRotationMatrix()
        up_row = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
        # Row 1 is the prim's local +Y in world terms; the view runs along it,
        # so the camera stands on the -Y side.
        view = Gf.Vec3d(rot[1][0], rot[1][1], rot[1][2]).GetNormalized()
        up = Gf.Vec3d(rot[up_row][0], rot[up_row][1], rot[up_row][2]).GetNormalized()

        # A camera looks down its local -Z, so Z_cam is the opposite of the view.
        z_cam = -view
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()

        centre = UsdGeom.Xformable(facing).ComputeLocalToWorldTransform(
            tc).ExtractTranslation()
        centre = Gf.Vec3d(centre[0], centre[1], centre[2])
        corners = self._box_corners(self._world_range(prim))
        if not corners:
            return False

        distance = self._fit_distance(cam_prim, viewport, corners, centre,
                                      x_cam, y_cam, z_cam)
        eye = centre + z_cam * distance
        matrix = Gf.Matrix4d(
            x_cam[0], x_cam[1], x_cam[2], 0.0,
            y_cam[0], y_cam[1], y_cam[2], 0.0,
            z_cam[0], z_cam[1], z_cam[2], 0.0,
            eye[0],   eye[1],   eye[2],   1.0,
        )

        # Depth of the target along the view, measured from the eye.
        slab = corners + self._box_corners(
            self._world_range(self._target["ebs"]) if self._target else None)
        depths = [Gf.Dot(Gf.Vec3d(*c) - eye, -z_cam) for c in slab]
        near, far = min(depths), max(depths)
        margin = max((far - near) * CAMERA_SLAB, 1e-3)
        near, far = max(near - margin, distance * 1e-4), far + margin

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            xformable = UsdGeom.Xformable(cam_prim)
            transform_op = next(
                (op for op in xformable.GetOrderedXformOps()
                 if op.GetOpName() == "xformOp:transform"), None)
            if transform_op is not None:
                transform_op.Set(matrix)
            else:
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(matrix)
            UsdGeom.Camera(cam_prim).GetClippingRangeAttr().Set(
                Gf.Vec2f(float(near), float(far)))
            coi = cam_prim.GetAttribute("omni:kit:centerOfInterest")
            if coi and coi.IsValid():
                coi.Set(Gf.Vec3d(0.0, 0.0, -distance))   # orbit around the target
        self._note(f"camera at {distance:.2f} from the target, "
                   f"clipping {near:.2f} .. {far:.2f} (anything nearer or "
                   f"further is culled)")
        return True

    def _world_range(self, prim) -> "Gf.Range3d | None":
        if prim is None or not prim.IsValid():
            return None
        box = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,
        ).ComputeWorldBound(prim).ComputeAlignedRange()
        return None if box.IsEmpty() else box

    @staticmethod
    def _box_corners(box) -> list:
        if box is None:
            return []
        lo, hi = box.GetMin(), box.GetMax()
        return [(x, y, z) for x in (lo[0], hi[0])
                for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]

    @staticmethod
    def _fit_distance(cam_prim, viewport, corners, centre, x_cam, y_cam, z_cam) -> float:
        """How far back the camera has to sit for every corner to be in shot."""
        camera = UsdGeom.Camera(cam_prim)
        focal = camera.GetFocalLengthAttr().Get() or 50.0
        aperture = camera.GetHorizontalApertureAttr().Get() or 20.955
        tan_h = (aperture * 0.5) / focal
        try:
            width, height = viewport.resolution
            aspect = (width / height) if height else 1.0
        except Exception:
            aspect = 1.0
        tan_v = tan_h / aspect if aspect else tan_h

        needed = 0.0
        for corner in corners:
            offset = Gf.Vec3d(*corner) - centre
            along = Gf.Dot(offset, z_cam)
            wide = abs(Gf.Dot(offset, x_cam)) / tan_h if tan_h else 0.0
            tall = abs(Gf.Dot(offset, y_cam)) / tan_v if tan_v else 0.0
            needed = max(needed, along + max(wide, tall))
        return max(needed / CAMERA_FILL, 1e-3)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _get_stage() -> "Usd.Stage | None":
        return omni.usd.get_context().get_stage()

    def _payload(self, ok: bool, reason: str, cells: dict = None, hit_count: int = 0,
                 equipment=None, eqp_id: str = "", port_count=None) -> dict:
        """Build the result dict. Target fields come from the prepared target
        unless explicitly passed (prepare reports them before the target is set)."""
        target = self._target or {}
        equipment = equipment or target.get("equipment")
        ebs = target.get("ebs")
        anchor = target.get("anchor")
        self._result = {
            "ok": ok,
            "reason": reason,
            "equipment": str(equipment.GetPath()) if equipment else "",
            "equipment_id": eqp_id or target.get("eqp_id", ""),
            "port_count": port_count if port_count is not None else target.get("port_count"),
            "ebs": str(ebs.GetPath()) if ebs else "",
            "anchor": str(anchor.GetPath()) if anchor else "",
            "cells": cells,
            "hit_count": hit_count,
            "grid": dict(self._grid_shape),
            "timings": list(self._timings),
            "notes": list(self._notes),
            "total_ms": (time.perf_counter() - self._started) * 1000.0,
        }
        return dict(self._result)
