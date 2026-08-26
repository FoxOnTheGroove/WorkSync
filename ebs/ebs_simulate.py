import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager

from pxr import Usd, UsdGeom, Gf
import omni.usd

__all__ = ["EbsSimulate"]

EQP_PREFIX = "EQP_"
PORT_ID_KEY = "port-id"       # value identifying a port: '<equipment>_<n>'
OFFSET_KEY  = "offset"        # distance of a port from its addr, along -Y
CADX_KEY    = "cad-x"         # rail start point, on the addr group
CAD_PER_UNIT    = 100.0 / 3.0     # cad-x units per stage unit
OFFSET_PER_UNIT = 100000.0        # offset units per stage unit
RAIL_PREFIX = "rail_"

# Faces evaluated by the simulation. Front, back and floor are ignored.
FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)

GRID = 3                 # 3x3 subdivision per face

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
        self._port_addr: dict = {}          # "########" -> addr number
        self._addr_cadx: dict = {}          # addr number -> cad-x
        self._rail_root: str = ""           # parent path holding the rail prims
        self._bounds_cache: dict = {}       # prim path -> world aligned Gf.Range3d
        self._timings: list = []            # [label, elapsed_ms] for the last run
        self._started: float = 0.0
        self._target: dict = None           # prepared equipment / EBS for the step buttons
        self._aligned: bool = False
        self._result: dict = {}

    # -- settings ------------------------------------------------------------

    def set_xml_path(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._xml_path:
            self._port_map = {}
            self._port_elements = {}
        self._xml_path = path

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def set_clearance(self, value: float) -> None:
        self._clearance = max(0.0, float(value))

    def set_rail_root(self, path: str) -> None:
        """Parent prim holding the rail_<a>_<b> prims."""
        self._rail_root = (path or "").strip()

    def set_search_root(self, path: str) -> None:
        """Limit the equipment scan to one subtree, e.g. '/World/Factory'."""
        path = (path or "").strip()
        if path != self._search_root:
            self._eqp_index = {}
            self._bounds_cache = {}
        self._search_root = path

    def get_result(self) -> dict:
        return dict(self._result)

    def get_timings(self) -> list:
        """[label, elapsed_ms] recorded during the last run."""
        return [list(t) for t in self._timings]

    def teardown(self) -> None:
        self._eqp_index = {}
        self._port_map = {}
        self._port_elements = {}
        self._bounds_cache = {}
        self._timings = []
        self._target = None
        self._aligned = False
        self._result = {}

    # -- timing --------------------------------------------------------------

    def _begin(self) -> None:
        """Start a fresh timing record for one button press."""
        self._timings = []
        self._started = time.perf_counter()

    @contextmanager
    def _stage_timer(self, label: str):
        """Record how long one step of the run took."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self._timings.append([label, (time.perf_counter() - started) * 1000.0])

    # -- steps ---------------------------------------------------------------

    def prepare(self, equipment: str = "") -> dict:
        """Step 0: resolve the equipment, its port count and the matching EBS prim."""
        self._begin()
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
        """Run every step in order."""
        self._begin()
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
            moved = self._move_camera(str(self._target["equipment"].GetPath()))
        return self._payload(moved, "Camera moved" if moved else "Camera focus failed")

    def _do_align(self) -> dict:
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        stage = self._get_stage()
        equipment = self._target["equipment"]

        with self._stage_timer("align EBS"):
            target = self.compute_target(stage, self._target["eqp_id"], equipment)
            if target is not None:
                self._aligned = self._place_ebs(self._target["ebs"], target,
                                                self._target["anchor"])
                note = ("EBS placed at port 0, world "
                        f"({target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f})")
            else:
                # No usable rail/port data: keep working off the anchor prim.
                print("[ebs] port geometry unavailable, falling back to the anchor prim")
                self._aligned = self._align_prims(self._target["ebs"],
                                                  self._target["anchor"])
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
        )
        hit_count = sum(sum(1 for c in v if c) for v in cells.values())
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
        # The full index is still built later, when collision needs it.
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
        self._addr_cadx = {}
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

            # cad-x of every addr, including the neighbours that hold no ports
            for elem in root.iter():
                m = addr_pattern.match((elem.get("name", "")
                                        or elem.tag.rsplit("}", 1)[-1]).strip())
                if not m:
                    continue
                cadx = self._as_float(self._key_value(elem, CADX_KEY))
                if cadx is not None:
                    self._addr_cadx[int(m.group(1))] = cadx

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
                addrs = {by_index[i][2] for i in indices if by_index[i][2] is not None}
                if len(addrs) > 1:
                    print(f"[ebs] {key}: ports span several addr blocks: {sorted(addrs)}")
                self._port_addr[key] = min(addrs) if addrs else None
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
        if not self._port_map:
            self.load_ports()
        return list(self._port_map.get(eqp_id.upper(), []))

    # -- rail and port geometry ----------------------------------------------

    def find_rail(self, stage: Usd.Stage, addr_number: int):
        """Find the rail_<addr>_<neighbour> prim and return (prim, neighbour)."""
        prefix = f"{RAIL_PREFIX}{addr_number}_"

        def match(prim):
            name = prim.GetName().lower()
            if not name.startswith(prefix):
                return None
            parts = name.split("_")
            if len(parts) < 3 or not parts[2].isdigit():
                return None
            return int(parts[2])

        root = stage.GetPrimAtPath(self._rail_root) if self._rail_root else None
        if root is not None and root.IsValid():
            for child in root.GetChildren():         # rails sit right under the root
                neighbour = match(child)
                if neighbour is not None:
                    return child, neighbour
            print(f"[ebs] no {prefix}* under {self._rail_root}, scanning the stage")

        for prim, _ in self._walk(stage):            # fallback: pruned stage scan
            neighbour = match(prim)
            if neighbour is not None:
                return prim, neighbour
        return None, None

    def compute_port_zero_y(self, stage: Usd.Stage, eqp_id: str) -> "float | None":
        """Y of the virtual port 0, which is where the EBS goes.

        The addr sits at the rail's start point, the rail prim sits at its
        midpoint, and ports run from the addr in -Y at a constant spacing:
        along the line the order is 1, 2, (3), addr. Port 0 is one more step
        past port 1, so its offset extrapolates from the first two ports.
        """
        key = eqp_id.upper()
        addr_a = self._port_addr.get(key)
        if addr_a is None:
            print(f"[ebs] {key}: no addr block found for its ports")
            return None

        rail, addr_b = self.find_rail(stage, addr_a)
        if rail is None:
            print(f"[ebs] {key}: no {RAIL_PREFIX}{addr_a}_* prim found")
            return None

        cadx_a, cadx_b = self._addr_cadx.get(addr_a), self._addr_cadx.get(addr_b)
        if cadx_a is None or cadx_b is None:
            print(f"[ebs] {key}: missing {CADX_KEY} for addr {addr_a} or {addr_b}")
            return None

        offsets = self._port_offsets.get(key, {})
        first, second = offsets.get(1), offsets.get(2)
        if first is None or second is None:
            print(f"[ebs] {key}: needs the offsets of ports 1 and 2, got {offsets}")
            return None

        spacing = first - second
        if spacing <= 0:
            print(f"[ebs] {key}: port 1 should sit further from the addr than port 2 "
                  f"(offsets {first}, {second})")
        third = offsets.get(3)
        if third is not None and abs((second - third) - spacing) > 1e-6:
            print(f"[ebs] {key}: port spacing is uneven: "
                  f"{first - second} vs {second - third}")

        rail_y = self._local_translation(rail)[1]
        length = (cadx_b - cadx_a) / CAD_PER_UNIT
        addr_y = rail_y - length / 2.0                   # rail start = addr position
        offset_zero = 2.0 * first - second               # one step past port 1
        return addr_y - offset_zero / OFFSET_PER_UNIT    # offsets run in -Y

    def compute_target(self, stage: Usd.Stage, eqp_id: str, equipment: Usd.Prim):
        """World point the EBS has to sit on.

        X and Y are computed in the rail's own space - the rail's X and the
        virtual port 0 - and then lifted into world space. Z stays the
        equipment's world Z. The rail, the equipment and the EBS live under
        different parents, so everything meets in world space.
        """
        y = self.compute_port_zero_y(stage, eqp_id)
        if y is None:
            return None
        rail, _ = self.find_rail(stage, self._port_addr.get(eqp_id.upper()))
        if rail is None:
            return None

        rail_local = self._local_translation(rail)
        in_rail_space = Gf.Vec3d(rail_local[0], y, rail_local[2])
        world = self._parent_world(rail).Transform(in_rail_space)
        equipment_world = UsdGeom.Xformable(equipment).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()).ExtractTranslation()
        return Gf.Vec3d(world[0], world[1], equipment_world[2])

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
        return self._write_transform(stage, xformable, rotation, scale,
                                     to_ebs_space.Transform(world_position))

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
        """Author scale, rotation and position as one transform op."""
        matrix = Gf.Matrix4d(
            rotation[0][0] * scale[0], rotation[0][1] * scale[0], rotation[0][2] * scale[0], 0.0,
            rotation[1][0] * scale[1], rotation[1][1] * scale[1], rotation[1][2] * scale[1], 0.0,
            rotation[2][0] * scale[2], rotation[2][1] * scale[2], rotation[2][2] * scale[2], 0.0,
            translation[0], translation[1], translation[2], 1.0,
        )
        # Author into the session layer so the source layers stay untouched.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            try:
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(matrix)
                return True
            except Exception as e:
                print(f"[ebs] transform op failed, translate only: {e}")
                api = UsdGeom.XformCommonAPI(xformable.GetPrim())
                return bool(api and api.SetTranslate(Gf.Vec3d(translation)))

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

    def check_collision(self, ebs_prim: Usd.Prim, exclude: list = None) -> dict:
        """Test the left, right and ceiling faces as 3x3 grids.

        Faces and cells follow the EBS prim's local axes, named as the front
        camera sees them: right is -Y, left is +Y, ceiling is +up.
        """
        empty = {face: [False] * (GRID * GRID) for face in FACES}
        stage = self._get_stage()
        if stage is None:
            return empty

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,          # read extentsHint instead of walking geometry
        )

        with self._stage_timer("EBS bounds"):
            # GfBBox3d: GetRange() is the local box, GetMatrix() maps it to world.
            ebs_bbox = cache.ComputeWorldBound(ebs_prim)
            local_box = ebs_bbox.GetRange()
            to_world = ebs_bbox.GetMatrix()
            world_box = ebs_bbox.ComputeAlignedRange()
        if local_box.IsEmpty():
            return empty

        with self._stage_timer("build cells"):
            # Cells are cut along the local axes, then converted to world AABBs.
            cells = {
                face: [Gf.BBox3d(cell, to_world).ComputeAlignedRange() for cell in boxes]
                for face, boxes in self._build_cells(local_box).items()
            }

        if not self._eqp_index:
            self.build_index()

        with self._stage_timer(f"equipment bounds ({len(self._eqp_index)})"):
            missing = self._cache_bounds(stage, cache)

        with self._stage_timer("broad phase"):
            # Keep only equipment overlapping the EBS box grown by the clearance.
            margin = Gf.Vec3d(self._clearance, self._clearance, self._clearance)
            search = Gf.Range3d(world_box.GetMin() - margin, world_box.GetMax() + margin)
            skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
            candidates = [
                box for path, box in self._bounds_cache.items()
                if not any(path == s or path.startswith(s + "/") for s in skip)
                and not Gf.Range3d.GetIntersection(box, search).IsEmpty()
            ]

        with self._stage_timer(f"cell test ({len(candidates)} candidates)"):
            result = {face: [False] * (GRID * GRID) for face in FACES}
            for face, boxes in cells.items():
                for i, cell in enumerate(boxes):
                    for box in candidates:
                        if not Gf.Range3d.GetIntersection(box, cell).IsEmpty():
                            result[face][i] = True
                            break

        if missing:
            print(f"[ebs] {missing} equipment prim(s) had no usable bound")
        return result

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
        """Build the 3x3 cells per face in the prim's local axes,
        ordered left to right and top to bottom.

        +/-X is the front/back (not evaluated), +/-Y the sides, +up the ceiling.
        The camera looks from local -X toward +X, so screen-right is -Y:
        the face named "right" is the -Y one, and the ceiling columns are
        reversed to read left-to-right the same way.
        """
        up_axis = 1 if UsdGeom.GetStageUpAxis(self._get_stage()) == UsdGeom.Tokens.y else 2
        front_axis = 0                                  # front/back, not evaluated
        side_axis = 3 - up_axis - front_axis            # sides (Y when Z-up)
        t = self._clearance

        lo, hi = box.GetMin(), box.GetMax()
        cells = {}

        def make(fixed_axis, outward, row_axis, col_axis, flip_cols=False):
            out = []
            row_lo, row_hi = lo[row_axis], hi[row_axis]
            col_lo, col_hi = lo[col_axis], hi[col_axis]
            row_step = (row_hi - row_lo) / GRID
            col_step = (col_hi - col_lo) / GRID
            for r in range(GRID):
                for c in range(GRID):
                    cmin, cmax = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
                    # rows are flipped so index 0 is the top row
                    cmin[row_axis] = row_hi - (r + 1) * row_step
                    cmax[row_axis] = row_hi - r * row_step
                    col = (GRID - 1 - c) if flip_cols else c
                    cmin[col_axis] = col_lo + col * col_step
                    cmax[col_axis] = col_lo + (col + 1) * col_step
                    if outward > 0:
                        cmin[fixed_axis], cmax[fixed_axis] = hi[fixed_axis], hi[fixed_axis] + t
                    else:
                        cmin[fixed_axis], cmax[fixed_axis] = lo[fixed_axis] - t, lo[fixed_axis]
                    out.append(Gf.Range3d(Gf.Vec3d(*cmin), Gf.Vec3d(*cmax)))
            return out

        # The camera looks from local -X, so screen-right is -Y.
        cells[FACE_RIGHT]   = make(side_axis, -1, up_axis, front_axis)
        cells[FACE_LEFT]    = make(side_axis, +1, up_axis, front_axis)
        cells[FACE_CEILING] = make(up_axis,   +1, front_axis, side_axis, flip_cols=True)
        return cells

    # -- camera --------------------------------------------------------------

    def _move_camera(self, prim_path: str) -> bool:
        """Put the camera in front of the prim (its local -X) and fill the view like F."""
        try:
            from omni.kit.viewport.utility import get_active_viewport, frame_viewport_prims
        except Exception as e:
            print(f"[ebs] viewport utility unavailable: {e}")
            return False

        stage = self._get_stage()
        viewport = get_active_viewport()
        if stage is None or viewport is None:
            return False
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return False

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,
        )
        box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if box.IsEmpty():
            return False
        center = (box.GetMin() + box.GetMax()) * 0.5
        diagonal = (box.GetMax() - box.GetMin()).GetLength()
        distance = max(diagonal * 1.5, 1.0)

        self._place_front_camera(stage, viewport, prim, center, distance)
        try:
            frame_viewport_prims(viewport, prims=[prim_path])   # keep direction, fit size
        except Exception as e:
            print(f"[ebs] camera framing failed: {e}")
            return False
        return True

    def _place_front_camera(self, stage, viewport, prim, center, distance) -> bool:
        """Aim the camera from the prim's local -X toward +X."""
        cam_prim = stage.GetPrimAtPath(str(viewport.camera_path))
        if not cam_prim.IsValid():
            return False

        tc = Usd.TimeCode.Default()
        local_to_world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(tc)
        rot = local_to_world.ExtractRotationMatrix()
        up_row = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
        front = -Gf.Vec3d(rot[0][0], rot[0][1], rot[0][2]).GetNormalized()         # local -X
        up    = Gf.Vec3d(rot[up_row][0], rot[up_row][1], rot[up_row][2]).GetNormalized()

        # A camera looks down its local -Z, so Z_cam is the opposite of the view direction.
        z_cam = front
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()
        eye = Gf.Vec3d(center) + z_cam * distance

        matrix = Gf.Matrix4d(
            x_cam[0], x_cam[1], x_cam[2], 0.0,
            y_cam[0], y_cam[1], y_cam[2], 0.0,
            z_cam[0], z_cam[1], z_cam[2], 0.0,
            eye[0],   eye[1],   eye[2],   1.0,
        )

        # The viewport camera is a session-layer prim, so author there for it to take effect.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            xformable = UsdGeom.Xformable(cam_prim)
            transform_op = next(
                (op for op in xformable.GetOrderedXformOps()
                 if op.GetOpName() == "xformOp:transform"), None)
            if transform_op is not None:
                transform_op.Set(matrix)
            else:
                try:
                    xformable.ClearXformOpOrder()
                    xformable.AddTransformOp().Set(matrix)
                except Exception as e:
                    print(f"[ebs] camera transform failed: {e}")
                    return False
            coi = cam_prim.GetAttribute("omni:kit:centerOfInterest")
            if coi and coi.IsValid():
                coi.Set(Gf.Vec3d(0.0, 0.0, -distance))   # orbit around the target
        return True

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
            "timings": list(self._timings),
            "total_ms": (time.perf_counter() - self._started) * 1000.0,
        }
        return dict(self._result)
