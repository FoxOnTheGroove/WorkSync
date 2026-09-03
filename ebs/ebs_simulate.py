import io
import json
import math
import os
import re
import time
import xml.parsers.expat as expat
from contextlib import contextmanager, nullcontext, redirect_stdout

from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf
import omni.usd

__all__ = ["EbsSimulate"]

EQP_PREFIX = "EQP_"
PORT_ID_KEY = "port-id"       # value identifying a port: '<equipment>_<n>'
OFFSET_KEY  = "offset"        # port distance from its addr, along the rail direction
CADX_KEY    = "cad-x"         # rail start point along X, on the addr group
CADY_KEY    = "cad-y"         # rail start point along Y, on the addr group
NEXT_KEY    = "next-address"  # addr a NextAddr block leads to
PULS_KEY    = "distance-puls"  # length of that segment, in offset units
ADDR_PATTERN = re.compile(r"^addr0*(\d+)$", re.IGNORECASE)
PORT_PATTERN = re.compile(r"^([A-Za-z0-9]+)_(\d+)$")
CACHE_SUFFIX  = ".ebscache.json"  # the parsed maps, written beside the source xml
CACHE_VERSION = 1                 # bump when the stored shape changes
READ_BLOCK    = 8 << 20           # xml is read this much at a time
CAD_PER_UNIT    = 100.0 / 3.0     # cad-x units per stage unit
CAD_SLACK       = 0.1             # 비유효축 허용 유격 (100/3이 안 나눠떨어짐)
OFFSET_PER_UNIT = 100000.0        # offset units per stage unit
RAIL_PREFIX = "rail_"

SCALE_FIXED = "fixed"   # offset / OFFSET_PER_UNIT, the same everywhere
SCALE_PULS  = "puls"    # offset x (segment length / segment distance-puls)
SCALE_SNAP  = "snap"    # puls, then slid so port 1 sits on the pivot. align only
SCALE_MODES = (SCALE_FIXED, SCALE_PULS, SCALE_SNAP)

def _plain(name: str) -> str:
    """A tag or attribute name without its namespace, however it is written."""
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _as_float(text) -> "float | None":
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


class _PortScan:
    """One streaming pass over the port xml. No tree is built.

    A group's keys are its own attributes plus its <value key=.. value=..>
    children, and the addr in force is whatever addr group encloses it.
    """

    def __init__(self):
        self.addr_cad = {}
        self.addr_next = {}
        self.found = {}
        self._groups = []            # keys of every group still open
        self._addrs = []             # the addr each of those sits in
        self._text = []

    def start(self, tag, attrib):
        entries = {}
        for raw, value in attrib.items():
            entries[_plain(raw)] = value
        key = entries.get("key")
        if key is not None and self._groups:
            self._groups[-1][key.strip().lower()] = entries.get("value", "")
        self._groups.append(entries)
        found = ADDR_PATTERN.match((entries.get("name") or _plain(tag)).strip())
        self._addrs.append(int(found.group(1)) if found
                           else (self._addrs[-1] if self._addrs else None))
        del self._text[:]

    def data(self, text):
        self._text.append(text)

    def end(self, tag):
        entries = self._groups.pop()
        addr = self._addrs.pop()
        key = entries.get("key")
        if key is not None and not entries.get("value") and self._groups:
            written = "".join(self._text).strip()
            if written:                      # <value key="offset">100</value>
                self._groups[-1][key.strip().lower()] = written
        del self._text[:]

        found = ADDR_PATTERN.match((entries.get("name") or _plain(tag)).strip())
        if found:
            cadx = _as_float(entries.get(CADX_KEY))
            cady = _as_float(entries.get(CADY_KEY))
            if cadx is not None or cady is not None:
                self.addr_cad[int(found.group(1))] = (cadx or 0.0, cady or 0.0)
        target = _as_float(entries.get(NEXT_KEY))
        puls = _as_float(entries.get(PULS_KEY))
        if addr is not None and target is not None and puls is not None:
            self.addr_next.setdefault(addr, []).append((int(target), puls))
        port_id = entries.get(PORT_ID_KEY)
        if port_id:
            port = PORT_PATTERN.match(port_id.strip())
            if port:
                self.found.setdefault(port.group(1).upper(), {})[
                    int(port.group(2))] = (_as_float(entries.get(OFFSET_KEY)), addr)

    def close(self):
        return self.found


try:                                  # built once: it is asked for per prim
    _EVERY_CHILD = Usd.TraverseInstanceProxies()
except Exception:
    _EVERY_CHILD = None


def _children(prim):
    if _EVERY_CHILD is not None:
        try:
            return prim.GetFilteredChildren(_EVERY_CHILD)
        except Exception:
            pass
    return prim.GetChildren()

SKIP_TYPES = frozenset({"Material", "Shader", "NodeGraph", "GeomSubset", "Camera"})

GEOMETRY_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves",
    "Capsule", "Cone", "Cube", "Cylinder", "Sphere", "Plane",
})

CAMERA_PATH    = "/EbsCamera"             # session-layer camera owned by this extension
CAMERA_FILL    = 0.9                      # how much of the view the target fills
CAMERA_NEAR    = 0.01                     # fixed, right against the lens: no
                                          # culling, nothing in front is cut
CAMERA_FAR     = 1.0e6                    # the far plane stays open
NEIGHBOUR_REACH = 1.5    # circle to look for the machines either side, as a share
                         # of the target's own width
# Walking up from a blocking mesh, these names answer for whatever is under
# them. Anything under the search root answers as the machine instead -- the
# prim just inside the root. Whichever is met first going up wins.
GROUP_NAMES = ("AMH", "Construction")

STATE_CLASH = "clash"     # 면이 막혔다. 거리는 없다
STATE_TIGHT = "tight"     # 닿지는 않았는데 최소 여유보다 가깝다. 간섭
STATE_CLEAR = "clear"     # 비었다. distance 가 있으면 그 거리, None 이면
                          # reach 안에 아무것도 없었다는 뜻

# 지켜야 하는 최소 여유, m. 면마다 다르다 — 천장은 붙어도 되지만 옆은 사람이
# 지나가야 한다. set_min_gaps 로 바꾼다.
MIN_GAP_CEILING = 0.1
MIN_GAP_SIDE = 0.6

NEIGHBOUR_BAND = 0.5     # and how far out of the target's own row it may sit,
                         # same share: beside it, not across the aisle from it

MARKER_ROOT    = "/EbsCollisionMarkers"   # session-layer scope holding the cell quads
MARKER_OPACITY = 0.075    # faint, and carried by the emission rather than the alpha
COLOR_BLOCKED  = (0.9, 0.2, 0.2)
BLOCKED_OPACITY = 0.6      # the blocked face is the answer, so it reads solid
BLOCKED_EMISSION = 1000.0
COLOR_CLEAR    = (1.0, 1.0, 1.0)
MARKER_EMISSION = 10000.0  # 마커 발광 세기
COLOR_GAP      = (0.9, 0.7, 0.0)   # 여유를 재는 선. 패널과 같은 짙은 황색
COLOR_TIGHT    = (0.9, 0.15, 0.15)  # 최소 여유보다 가까울 때. 간섭
GAP_OPACITY    = 1.0
GAP_EMISSION   = 3000.0
SHEET_GAP      = 0.001    # 뒷면이 앞면에서 떨어지는 거리 (대각선 대비)

LASER_ROOT     = "/EbsPortLasers"   # session-layer scope holding the port test lasers
LASER_COLOR    = (1.0, 0.05, 0.05)  # the real ports read from the XML
LASER_COLOR_0  = (1.0, 0.75, 0.0)   # the virtual port 0 the EBS is placed on
LASER_RADIUS   = 0.0013   # laser radius, as a share of the equipment's bbox diagonal

SWEEP_ROOT     = "/EbsPortSweep"    # port-1 lasers for every equipment at once
SWEEP_COLOR_PORT = LASER_COLOR      # where port 1 is worked out to be
SWEEP_COLOR_EQP  = (0.15, 0.8, 0.3)  # where the equipment itself is

OURS = (MARKER_ROOT, LASER_ROOT, SWEEP_ROOT, CAMERA_PATH)
OURS_UNDER = tuple(p + "/" for p in OURS)   # startswith takes a tuple
NOW = Usd.TimeCode.Default()      # asked for per prim; make it once
LOOKS = "Looks"                   # the material folder right under a machine
SHADER_TYPE = "Shader"
GONE_THRESHOLD = 0.5     # anything under this is cut, and nothing is over it
GONE_LAYER = "ebs_hidden.usda"   # our own layer, holding only those opinions
# What a shader is told to make its machine disappear. UsdPreviewSurface reads
# the camelCase names, an MDL surface the underscored ones, and each ignores
# the spelling it does not know. The threshold is what makes this a cutout
# rather than a blend: at zero the surface is still drawn, just fully
# see-through, which sorts against everything behind it and reads wrong. Above
# zero the fragment is thrown away instead. Turning the prim off would be the
# same picture and a far dearer one: visibility is resolved down the whole
# tree beneath it.
GONE = (("inputs:opacity", "Float", 0.0),
        ("inputs:opacityThreshold", "Float", GONE_THRESHOLD),
        ("inputs:enable_opacity", "Bool", True),
        ("inputs:opacity_constant", "Float", 0.0),
        ("inputs:opacity_threshold", "Float", GONE_THRESHOLD))

FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)

GRID = 1                 # 면당 셀 분할 수. 1이면 면 하나가 셀 하나
GRID_CELLS = 24          # most cells an axis of the interference grid gets
MEET_LIMIT = 4           # mesh pairs named before the answer is settled
OVERLAP_EPS = 1e-6       # boxes merely touching a face do not count as blocking
PROBE_RATIO = 0.01       # contact tolerance, as a share of the EBS's longest edge
REACH_RATIO = 30.0       # 가장 가까운 메시를 찾는 거리 (최장변 대비).
                         # 플랜트를 가로지르는 정도. 넓힌 만큼 훑는 상자가
                         # 커지고 후보가 늘어난다 — 'gather nearby' 참조
PRECISION_BBOX = "bbox"
PRECISION_MESH = "mesh"
PRECISION_TRI  = "triangle"  # the mesh triangles themselves

PRUNE_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves", "Capsule", "Cone", "Cube",
    "Cylinder", "Sphere", "Plane", "GeomSubset",
    "Material", "Shader", "NodeGraph", "Camera",
})
ANCHOR_DEPTH = 6         # how many transform levels down from the equipment the anchor is
PASS_TYPES  = ("Scope",)      # prim types descended through without counting
MIN_PORTS = 2            # ports a placement needs: one gap to step by, at least
MAX_PORTS = 3            # ports the EBS spans; an equipment with more is another shape

PIVOT_TOLERANCE = 1.0    # 포트 1에서 이만큼 넘게 떨어지면 피봇이 아님
PIVOT_ACROSS = 0.5       # the same, times this, across the rail rather than along it

class EbsSimulate:

    def __init__(self):
        self._xml_path: str = ""
        self._ebs_path_2port: str = ""
        self._ebs_path_3port: str = ""
        self._clearance: float = 0.0        # 0 = derive the probe depth from the EBS
        self._search_root: str = ""         # limit the scan to this subtree when set
        self._eqp_index: dict = {}          # "EQP_########" -> prim path
        self._port_map: dict = {}           # "########" -> sorted port indices
        self._port_offsets: dict = {}       # "########" -> {index: offset}
        self._port_addr: dict = {}          # "########" -> base addr number
        self._port_addr_of: dict = {}       # "########" -> {index: addr number}
        self._addr_cad: dict = {}           # addr number -> (cad-x, cad-y)
        self._addr_next: dict = {}          # addr number -> [(next addr, distance-puls)]
        self._offset_scale: str = SCALE_SNAP    # how an offset becomes a distance
        self._rail_root: str = ""           # parent path holding the rail prims
        self._rail_index: dict = None       # addr -> [(rail prim, neighbour addr)]
        self._rail_frame = None             # (addr point, one length on, axis) in rail space
        self._triangles: dict = {}          # mesh path -> world-space triangles
        self._eqp_spots: dict = None        # "EQP_########" -> where its pivot stands
        self._hidden: list = []             # equipment we turned off, to turn back on
        self._eqp_looks: dict = {}          # equipment path -> its shader paths
        self._eqp_shared: set = set()       # equipment whose Looks we cannot write
        self._gone = None                   # the layer holding the see-through opinions
        self._lasers: bool = False          # draw the port lasers on align
        self._verdict: dict = {}            # what the overlay says, from the last run
        self._min_gap = {FACE_CEILING: MIN_GAP_CEILING,
                         FACE_LEFT: MIN_GAP_SIDE,
                         FACE_RIGHT: MIN_GAP_SIDE}   # 면 -> 최소 여유, m
        self._blockers: dict = {}           # face -> the prim that blocked it first
        self._local: dict = {}              # mesh path -> its own points and faces
        self._boxed: dict = {}              # why a prim was judged by its box -> paths
        self._visible: dict = {}            # prim path -> visibility, for one run
        self._grid_shape: dict = {}         # face -> (rows, cols) of the last run
        self._port_world: dict = {}         # port index -> world point, from the last align
        self._port_rail_z: float = 0.0      # world Z of the rail the ports sit on
        self._face_planes: dict = {}        # face -> (axis, outward, coord, rows, cols)
        self._previous_camera = None        # viewport camera to restore on release
        self._precision: str = PRECISION_TRI
        self._timings: list = []            # [label, elapsed_ms] for the last run
        self._notes: list = []              # diagnostics for the last run, shown in the UI
        self._blocked: str = ""             # why the run cannot go on, if it cannot
        self._why: str = ""                 # why the last placement could not be worked out
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
            self._ready = False          # the port table has to be read again
        self._xml_path = path

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def hide_ebs(self) -> int:
        """Put both EBS prims out of sight.

        Wherever they were left standing is not an answer to anything, and a
        reader takes them for one. Init and Clear both put them away; Align is
        what brings the one it used back.
        """
        return self._show_ebs([self._ebs_path_2port, self._ebs_path_3port], False)

    def show_ebs(self, prim) -> int:
        """Bring back the one Align just placed."""
        return self._show_ebs([prim], True)

    def _show_ebs(self, wanted: list, visible: bool) -> int:
        stage = self._get_stage()
        if stage is None:
            return 0
        # Two prims, so nothing here is batched: the whole reason the machines
        # are made see-through rather than hidden is fifteen hundred of them.
        shown = UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible
        done = 0
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for one in wanted:
                    if isinstance(one, str):
                        if not one:              # the path was never filled in
                            continue
                        one = stage.GetPrimAtPath(one)
                    prim = one
                    if prim is None or not prim.IsValid():
                        continue
                    imageable = UsdGeom.Imageable(prim)
                    if imageable:
                        imageable.CreateVisibilityAttr().Set(shown)
                        done += 1
        except Exception as e:
            self._note(f"could not set the EBS visibility ({e})")
        return done

    def set_min_gaps(self, side: float, ceiling: float) -> None:
        """The clearance each face has to keep, in metres.

        Under it the face is reported as interference: nothing is touching,
        but nothing is meant to be that close either.
        """
        self._min_gap = {FACE_CEILING: max(0.0, float(ceiling)),
                         FACE_LEFT: max(0.0, float(side)),
                         FACE_RIGHT: max(0.0, float(side))}

    def set_clearance(self, value: float) -> None:
        self._clearance = max(0.0, float(value))

    def _probe_depth(self, box: Gf.Range3d) -> float:
        if self._clearance > 0.0:
            return self._clearance
        longest = max(box.GetMax()[i] - box.GetMin()[i] for i in range(3))
        return max(longest * PROBE_RATIO, 1e-6)

    def set_precision(self, mode: str) -> None:
        if mode in (PRECISION_BBOX, PRECISION_MESH, PRECISION_TRI):
            self._precision = mode
        else:
            print(f"[ebs] unknown precision '{mode}', keeping {self._precision}")

    def set_offset_scale(self, mode: str) -> None:
        mode = (mode or "").strip().lower()
        self._offset_scale = mode if mode in SCALE_MODES else SCALE_FIXED

    def set_show_lasers(self, on: bool) -> None:
        """Whether Align draws the port lasers. Off is the ordinary run; they
        are there to check the port maths against the drawing."""
        self._lasers = bool(on)

    def set_rail_root(self, path: str) -> None:
        self._rail_index = None
        self._rail_root = (path or "").strip()

    def set_search_root(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._search_root:
            self._eqp_index = {}
            self._ready = False          # the stage has to be scanned again
        self._search_root = path

    def get_result(self) -> dict:
        return dict(self._result)

    def get_timings(self) -> list:
        return [list(t) for t in self._timings]

    def teardown(self) -> None:
        self.release_camera()
        self.clear_markers()
        self.clear_port_lasers()
        self.clear_sweep()
        self.hide_ebs()
        self._eqp_index = {}
        self._port_map = {}
        self._triangles = {}
        self._local = {}
        self._visible = {}
        self._timings = []
        self._ready = False
        self._target = None
        self._aligned = False
        self._result = {}

    # -- timing --------------------------------------------------------------

    def _begin(self) -> None:
        self._boxed = {}
        self._timings = []
        self._notes = []
        self._blocked = ""
        self._started = time.perf_counter()

    def _fail(self, key: str, reason: str, short: str = ""):
        self._why = short or reason
        print(f"[ebs] {key}: {reason}")
        return None

    def _note(self, text: str) -> None:
        self._notes.append(text)
        print(f"[ebs] {text}")

    def get_notes(self) -> list:
        return list(self._notes)

    def _hush(self, loud: bool):
        return nullcontext() if loud else redirect_stdout(io.StringIO())

    @contextmanager
    def _stage_timer(self, label: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self._timings.append([label, (time.perf_counter() - started) * 1000.0])

    # -- steps ---------------------------------------------------------------

    def init(self) -> dict:
        self._begin()
        self._eqp_spots = None       # the plant may not be the one we last looked at
        self._eqp_looks = {}
        self._eqp_shared = set()
        if self._gone is not None:
            self._gone.Clear()               # nothing stays hidden across an init
        self._hidden = []
        self._ready = False
        self._target = None
        self._aligned = False
        self._verdict = {}
        self._triangles = {}
        self._local = {}
        self.make_camera()

        self.hide_ebs()
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
        self._begin()
        if not self._ready:
            return self._payload(False, "Run Init first")
        return self._do_prepare(equipment)

    def align(self) -> dict:
        self._begin()
        return self._do_align()

    def focus(self) -> dict:
        self._begin()
        return self._do_focus()

    def collide(self) -> dict:
        self._begin()
        return self._do_collide()

    def sweep_ports(self) -> dict:
        self._begin()
        if not self._ready:
            return self._payload(False, "Run Init first")
        stage = self._get_stage()
        if stage is None:
            return self._payload(False, "No stage open")

        names = sorted(self._eqp_index)
        spots, rows, failed = {}, [], []
        parents = {}                         # rail parent path -> its world matrix
        tc = Usd.TimeCode.Default()
        with self._stage_timer(f"port 1 of {len(names)} equipment"):
            for position, name in enumerate(names):
                eqp_id = name[len(EQP_PREFIX):] if name.startswith(EQP_PREFIX) else name
                row = {"equipment": eqp_id, "prim": self._eqp_index[name]}
                rows.append(row)
                try:
                    prim = stage.GetPrimAtPath(self._eqp_index[name])
                    anchor, reached = self.resolve_anchor(prim)
                    row["pivot_ok"] = "TRUE" if reached else "FALSE"
                    if not reached:
                        row["why"] = (f"nothing {ANCHOR_DEPTH} levels down to "
                                      f"measure against")
                        failed.append((eqp_id, row["why"]))
                        continue
                    here = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(
                        tc).ExtractTranslation()

                    self._rail_frame = None
                    with self._hush(position == 0):
                        found = self.compute_port_points(stage, eqp_id)
                    if found is None:
                        listed = self._port_map.get(eqp_id.upper())
                        if listed is None:
                            row["pivot_ok"] = "no-xml"
                        elif len(listed) < MIN_PORTS:
                            row["pivot_ok"] = f"port{len(listed)}"
                        else:
                            row["pivot_ok"] = "xml-invalid"
                        row["why"] = self._why or "배치 계산 실패"
                        failed.append((eqp_id, row["why"]))
                        continue
                    points, axis, rail = found
                    if 1 not in points or self._rail_frame is None:
                        row["pivot_ok"] = "xml-invalid"
                        row["why"] = "포트 1 위치 없음"
                        failed.append((eqp_id, row["why"]))
                        continue

                    parent = rail.GetParent()
                    key = str(parent.GetPath()) if parent else ""
                    if key not in parents:
                        parents[key] = self._parent_world(rail)
                    to_world = parents[key]
                    port = to_world.Transform(points[1])
                    row.update(self._measure(to_world, axis, rail, port, here,
                                             self._port_addr.get(eqp_id.upper())))
                    row["_port"], row["_here"] = port, here

                    row["pivot_ok"] = self._pivot_state(row, here, eqp_id)
                except Exception as e:
                    row["pivot_ok"] = "error"
                    row["why"] = f"{type(e).__name__}: {e}"
                    failed.append((eqp_id, row["why"]))

        self._mark_shared(rows)

        for row in rows:
            if "_draw" not in row:
                continue
            doubted = str(row.get("pivot_ok", "")).startswith("invalid")
            spots[row["equipment"]] = (row["_port"] if doubted else row["_draw"],
                                       row["_here"])

        self._blocked = ""                   # a bad equipment does not stop the sweep
        with self._stage_timer("draw sweep"):
            try:
                drawn = self.show_sweep(spots)
            except Exception as e:
                return self._payload(False, f"Could not draw the sweep: {e}")
        self._note(f"port 1 and equipment drawn for {drawn} of "
                   f"{len(names)} equipment")
        self._report_spread(rows)

        grouped = {}
        for name, why in failed:
            grouped.setdefault(why, []).append(name)
        for why, skipped in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            self._note(f"{len(skipped)} skipped, {why}: " + ", ".join(skipped[:6])
                       + (" ..." if len(skipped) > 6 else ""))
        return self._payload(drawn > 0, f"{drawn} equipment swept"
                             if drawn else "Nothing drawn",
                             rows=[{k: v for k, v in row.items()
                                    if not k.startswith("_")} for row in rows])

    def _measure(self, to_world, axis: int, rail, port, here, addr: int) -> dict:
        origin_local, onward_local, _ = self._rail_frame
        origin = to_world.Transform(origin_local)
        onward = to_world.Transform(onward_local)
        run = (onward[0] - origin[0], onward[1] - origin[1])
        length = math.sqrt(run[0] ** 2 + run[1] ** 2) or 1.0
        along = (run[0] / length, run[1] / length)
        across = (-along[1], along[0])                   # the axis we do not care about

        def project(point, unit):
            return ((point[0] - origin[0]) * unit[0]
                    + (point[1] - origin[1]) * unit[1])

        pivot_run = project(here, along)
        port_run = project(port, along)
        here_across = project(here, across)

        drawn = Gf.Vec3d(origin[0] + along[0] * port_run + across[0] * here_across,
                         origin[1] + along[1] * port_run + across[1] * here_across,
                         port[2])

        step = self._addr_step(addr, axis)
        per_unit = (step[1] / step[0]) if step and step[0] else None
        row = {
            "axis": "XY"[axis],
            "rail": rail.GetName(),
            "pivot_coord": here[axis],
            "pivot_offset": pivot_run * OFFSET_PER_UNIT,
            "port_coord": port[axis],
            "port_offset": port_run * OFFSET_PER_UNIT,
            "coord_diff": port_run - pivot_run,
            "off_axis_diff": project(port, across) - here_across,
            "offset_diff": (port_run - pivot_run) * OFFSET_PER_UNIT,
            "_draw": drawn,
            "_along": along,
        }
        if per_unit:
            row["puls_per_unit"] = per_unit
            row["pivot_offset_puls"] = pivot_run * per_unit
            row["port_offset_puls"] = port_run * per_unit
        return row

    def _pivot_state(self, row: dict, here, eqp_id: str) -> str:
        """What a measured row says about its pivot. The sweep's verdict.

        A pivot carrying no transform of its own first, then how far off it
        sits, and last of all -- only when nothing else was the matter -- a
        machine with more ports than the EBS spans. Sharing a pivot is not
        decided here: _mark_shared only ever adds to a row already doubted, so
        a TRUE from this is a TRUE in the report too.
        """
        off = []
        if abs(row["coord_diff"]) > PIVOT_TOLERANCE:
            off.append("axis")
        if abs(row["off_axis_diff"]) > PIVOT_TOLERANCE * PIVOT_ACROSS:
            off.append("across")
        count = len(self._port_map.get(eqp_id.upper(), ()))
        if abs(here[0]) < 1e-6 and abs(here[1]) < 1e-6:
            return "invalid:origin"
        if off:
            return "invalid:" + "+".join(off)
        if count > MAX_PORTS:
            return f"port{count}"
        return "TRUE"

    def _mark_shared(self, rows: list) -> None:
        seen = {}
        for row in rows:
            if "pivot_coord" not in row:
                continue
            spot = (round(row["pivot_coord"], 4), round(row.get("off_axis_diff", 0.0), 4))
            seen.setdefault(spot, []).append(row)
        for spot, sharing in seen.items():
            if len(sharing) < 2:
                continue
            for row in sharing:
                state = str(row.get("pivot_ok", "TRUE"))
                if state.startswith("invalid"):
                    row["pivot_ok"] = state + "+shared"

    def _report_spread(self, rows: list) -> None:
        gaps = [r["offset_diff"] for r in rows
                if "offset_diff" in r and r.get("pivot_ok") == "TRUE"]
        doubted = sum(1 for r in rows
                      if str(r.get("pivot_ok", "")).startswith("invalid"))
        if not gaps:
            return
        middle = sorted(gaps)[len(gaps) // 2]
        self._note(f"offset_diff over {len(gaps)}: min {min(gaps):.0f}, "
                   f"median {middle:.0f}, max {max(gaps):.0f}, "
                   f"mean {sum(gaps) / len(gaps):.0f}")
        if doubted:
            self._note(f"{doubted} left out: a pivot that cannot be one, or more "
                       f"ports than the EBS spans")

    def simulate(self, equipment: str = "") -> dict:
        self._begin()
        if not self._ready:
            return self._payload(False, "Run Init first")
        result = self._do_prepare(equipment)
        if not result["ok"]:
            return result
        result = self._do_align()
        if not result["ok"]:
            return result
        result = self._do_focus()
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

        with self._stage_timer("resolve anchor"):
            anchor, reached = self.resolve_anchor(eqp_prim)
        if not reached:
            print(f"[ebs] {eqp_id}: nothing {ANCHOR_DEPTH} transform levels down, "
                  f"working off the equipment prim")
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
        if not self._aligned:
            return self._payload(False, "Run Align first")
        stage = self._get_stage()
        with self._stage_timer("hide the other equipment"):
            beside = self.side_neighbours(stage, self._target["ebs"],
                                          self._target["equipment"])
            hidden = self.hide_other_equipment(
                [str(self._target["equipment"].GetPath())] + beside)
        # One machine a side at most, so this is three names at most. Anything
        # else still standing is a machine whose Looks could not be written --
        # _author_opacity counts those in a note of its own.
        kept = [self._target["equipment"].GetPath()] + beside
        self._note(f"kept {len(kept)} ({', '.join(str(p).rsplit('/', 1)[-1] for p in kept)}), "
                   f"{hidden} made see-through")

        with self._stage_timer("camera focus"):
            moved = self._move_camera(str(self._target["ebs"].GetPath()),
                                      self._target["anchor"])
        return self._payload(moved, "Camera on the EBS" if moved
                             else "Camera focus failed")

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
            elif self._blocked:
                self._port_world = {}
                self.clear_port_lasers()
                return self._payload(False, self._blocked)
            else:
                print("[ebs] port geometry unavailable, falling back to the anchor prim")
                self._port_world = {}
                self._aligned = self._align_prims(self._target["ebs"], anchor)
                note = "EBS aligned to the anchor prim"

        # The cache holds world-space triangles, and the EBS has just moved.
        self._forget_triangles(self._target["ebs"])

        # The EBS was out of sight until now: where it sat before Align is not
        # an answer to anything, and seeing it there reads as one.
        self.show_ebs(self._target["ebs"])

        if self._lasers:
            with self._stage_timer("draw port lasers"):
                drawn = self.show_port_lasers()
            if drawn:
                self._note(f"{drawn} port laser(s) drawn under {LASER_ROOT}")
        else:
            self.clear_port_lasers()
        return self._payload(self._aligned, note if self._aligned else "EBS alignment failed")

    def _do_collide(self) -> dict:
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        if not self._aligned:
            return self._payload(False, "Run Align first")

        # The equipment is out of the face check: the EBS is placed on it, so it
        # is always right there, and it would read as blocked on every run. What
        # it is actually in the way of is asked separately, and exactly.
        apart = [self._target["ebs"], self._target["equipment"]]
        bounds = self._bounds_cache()          # shared: it remembers what it read
        cells = self.check_collision(self._target["ebs"], exclude=apart, cache=bounds)
        hit_count = sum(sum(1 for c in v if c) for v in cells.values())
        with self._stage_timer("measure clear faces"):
            distances = self.measure_faces(self._target["ebs"], cells,
                                           exclude=apart, cache=bounds)
        for face, found in distances.items():
            if found.get("distance") is None:
                self._note(f"{face}: clear, nothing within "
                           f"{found.get('reach', 0):.3f}")
            else:
                self._note(f"{face}: clear, nearest {found['distance']:.4f} away "
                           f"({found['prim'].rsplit('/', 1)[-1]})")

        # A secondary check must not take the face result down with it.
        with self._stage_timer("equipment interference"):
            try:
                meeting = self.check_equipment(self._target["ebs"],
                                               self._target["equipment"],
                                               cache=bounds)
            except Exception as e:
                meeting = {"hit": False, "pairs": [], "tests": 0}
                self._note(f"interference check failed: {type(e).__name__}: {e}")
        if meeting["hit"]:
            self._note(f"the EBS runs through the equipment at "
                       f"{len(meeting['pairs'])} place(s): "
                       + ", ".join(f"{a.rsplit('/', 1)[-1]} x {b.rsplit('/', 1)[-1]}"
                                   for a, b in meeting["pairs"][:4])
                       + (" ..." if len(meeting["pairs"]) > 4 else ""))
        else:
            self._note(f"clear of the equipment itself "
                       f"({meeting['tests']} triangle pairs tested)")

        for why, paths in sorted(self._boxed.items()):
            self._note(f"{len(paths)} judged by box, {why}: "
                       + ", ".join(sorted(p.rsplit("/", 1)[-1] for p in paths)[:4])
                       + (" ..." if len(paths) > 4 else ""))

        # The verdict is worked out before the drawing, because the gap lines
        # are part of it, and held aside until after: show_markers clears
        # first, and clearing the markers clears the verdict with them. It is
        # a few labels, so it never takes the answer down with it either.
        try:
            verdict = self.build_verdict(self._target["ebs"], cells,
                                         distances, meeting["hit"])
        except Exception as e:
            verdict = {}
            self._note(f"no overlay verdict: {type(e).__name__}: {e}")

        with self._stage_timer("draw markers"):
            self.show_markers(self._target["ebs"], cells,
                              verdict.get("marks"))
        self._verdict = verdict

        # ok says the step ran, not that the answer is good -- blocked cells do
        # not clear it either, and the UI greys the grids out when it is false.
        told = ("No collision" if hit_count == 0
                else f"{hit_count} cell(s) blocked")
        if meeting["hit"]:
            told += ", and through the equipment"
        return self._payload(
            True, told,
            cells=cells, hit_count=hit_count, distances=distances,
            equipment_hit=meeting,
        )

    def owner_name(self, path: str) -> str:
        """What to call whatever is at this path, read from above it.

        A mesh's own name says nothing to anyone -- it is the machine it
        belongs to that is in the way. So the path is walked upwards: one of
        the group names answers for its whole subtree, and the prim just
        inside the search root answers as the machine. Whichever is met first
        going up wins, and the mesh's own name is what is left if neither is.
        """
        parts = [part for part in str(path or "").split("/") if part]
        if not parts:
            return ""
        root = [part for part in (self._search_root or "").split("/") if part]
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] in GROUP_NAMES:
                return parts[i]
            if root and parts[:i] == root:
                return parts[i]
        return parts[-1]

    def build_verdict(self, ebs_prim, cells: dict, distances: dict,
                      inside: bool) -> dict:
        """Can the EBS stand here, and where to say so.

        A point and a yes or no. What it is coloured and how big the letters
        are is the overlay's to decide -- this side does not know what a
        viewport is.
        """
        bbox = self._ebs_bound(ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty():
            return {}
        lo, hi = local_box.GetMin(), local_box.GetMax()
        middle = to_world.Transform(
            Gf.Vec3d(*[(lo[i] + hi[i]) * 0.5 for i in range(3)]))
        marks = self._face_marks(local_box, to_world, cells, distances)
        # Too close counts against it as much as touching does: it is the
        # clearance that has to hold, not just the surface.
        blocked = [{"face": mark["face"], "name": mark["name"],
                    "state": mark["state"]}
                   for mark in marks if mark["state"] != STATE_CLEAR]
        return {
            "marks": marks,
            "centre": (middle[0], middle[1], middle[2]),
            "span": max(hi[i] - lo[i] for i in range(3)),
            "inside": bool(inside),
            "faces": blocked,
            "blocked": sum(sum(1 for c in cells.get(face, []) if c)
                           for face in FACES),
            "placeable": not inside and not blocked,
        }

    def _face_marks(self, local_box, to_world, cells: dict,
                    distances: dict) -> list:
        """One panel a face: where it hangs, and the gap it is reporting.

        The gap is measured between the two world points rather than taken
        from the number the prism worked in, because that number is in the
        EBS's own space and the EBS may be scaled.
        """
        stage = self._get_stage()
        try:
            per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        except Exception:
            per_unit = 1.0
        lo, hi = local_box.GetMin(), local_box.GetMax()
        middle = [(lo[i] + hi[i]) * 0.5 for i in range(3)]

        def world(point):
            got = to_world.Transform(Gf.Vec3d(*point))
            return (got[0], got[1], got[2])

        marks = []
        for face in FACES:
            plane = self._face_planes.get(face)
            if plane is None:
                continue
            axis, outward, coord, _, _ = plane
            surface = list(middle)
            surface[axis] = coord

            least = self._min_gap.get(face, 0.0)
            blank = {"face": face, "distance": None, "name": "",
                     "min_gap": least, "at": world(surface),
                     "from": None, "to": None}
            if any(cells.get(face, [])):
                blank["state"] = STATE_CLASH
                blank["name"] = self.owner_name(self._blockers.get(face, ""))
                marks.append(blank)
                continue

            found = distances.get(face) or {}
            at = found.get("at")
            if at is None:                     # nothing came within the reach
                blank["state"] = STATE_CLEAR
                marks.append(blank)
                continue
            # Anchored where the answer came from, but exactly as long as
            # the answer: the number is the nearest corner's, and the point is
            # the middle of the face that corner belongs to.
            reach = found.get("distance", 0.0)
            start, end = list(at), list(at)
            start[axis] = coord                # straight back onto the face
            end[axis] = coord + (reach if outward > 0 else -reach)
            near, far = world(start), world(end)
            gap = (sum((far[i] - near[i]) ** 2 for i in range(3)) ** 0.5) * per_unit
            marks.append({
                "face": face,
                "state": STATE_TIGHT if gap < least else STATE_CLEAR,
                "distance": gap, "min_gap": least,
                "name": self.owner_name(found.get("prim", "")),
                "at": tuple((near[i] + far[i]) * 0.5 for i in range(3)),
                "from": near, "to": far,
            })
        return marks

    def get_verdict(self) -> dict:
        return dict(self._verdict)

    # -- equipment lookup ----------------------------------------------------

    def build_index(self) -> int:
        stage = self._get_stage()
        self._eqp_index = {}
        self._rail_index = None
        self._triangles = {}
        self._local = {}
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
        root = None
        if self._search_root:
            root = stage.GetPrimAtPath(self._search_root)
            if not root.IsValid():
                print(f"[ebs] search root not found, scanning the whole stage: "
                      f"{self._search_root}")
                root = None
        stack = list(_children(root or stage.GetPseudoRoot()))
        while stack:
            prim = stack.pop()
            name = prim.GetName().upper()
            yield prim, name
            if name.startswith(EQP_PREFIX):
                continue              # do not descend into equipment internals
            type_name = prim.GetTypeName()
            if type_name in PRUNE_TYPES or type_name.endswith("Light"):
                continue              # geometry and shading never hold equipment
            stack.extend(_children(prim))

    def equipment_spots(self, stage) -> dict:
        """Where each machine's pivot stands. Worked out once and kept.

        Not part of init: it is only the camera that wants it, and init is what
        everything else waits on. Machines do not move, so once is enough --
        until init runs again and the plant may be a different one.
        """
        if self._eqp_spots is not None:
            return self._eqp_spots
        spots = {}
        with self._stage_timer(f"locate {len(self._eqp_index)} equipment"):
            for name, path in self._eqp_index.items():
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                anchor, _ = self.resolve_anchor(prim)
                if anchor is None or not anchor.IsValid():
                    continue
                spot = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(
                    NOW).ExtractTranslation()
                spots[name] = (spot[0], spot[1])
        self._eqp_spots = spots
        return spots

    def side_neighbours(self, stage, ebs_prim, eqp_prim) -> list:
        """The machine on each side of the target, within its own width.

        Sideways is the EBS's own left and right -- the axis its two checked
        faces look along -- so this asks the same question the collision does,
        about the machines rather than the geometry. At most one each way, and
        nothing at all where the target stands alone.
        """
        spots = self.equipment_spots(stage)
        mine = self._equipment_id(eqp_prim)
        key = next((name for name in self._eqp_index
                    if name[len(EQP_PREFIX):] == mine or name == mine), None)
        here = spots.get(key)
        box = self._world_range(eqp_prim)
        if here is None or box is None or box.IsEmpty():
            return []
        width = max(box.GetMax()[i] - box.GetMin()[i] for i in range(2))
        reach = width * NEIGHBOUR_REACH
        if reach <= 0:
            return []

        sideways = self._sideways(ebs_prim)
        band = width * NEIGHBOUR_BAND
        left = right = None
        for name, spot in spots.items():
            if name == key:
                continue
            across = spot[0] - here[0]
            along = spot[1] - here[1]
            if across * across + along * along > reach * reach:
                continue                       # outside the circle
            side = across * sideways[0] + along * sideways[1]
            # How far out of the row it stands. Without this the machine on the
            # diagonal wins, because the circle only asks how far away it is
            # and it still leans to one side or the other.
            depth = along * sideways[0] - across * sideways[1]
            if abs(depth) > band:
                continue
            if side >= 0:
                if right is None or side < right[0]:
                    right = (side, name)
            elif left is None or -side < left[0]:
                left = (-side, name)
        # Either may be missing: a machine at the end of a row has one side.
        return [self._eqp_index[found[1]] for found in (left, right) if found]

    def _sideways(self, ebs_prim) -> tuple:
        """The EBS's own left-right, flattened onto the ground."""
        try:
            row = self._ebs_bound(ebs_prim).GetMatrix().GetRow(0)
            length = math.sqrt(row[0] ** 2 + row[1] ** 2)
            if length > 1e-9:
                return (row[0] / length, row[1] / length)
        except Exception:
            pass
        return (1.0, 0.0)

    def hide_other_equipment(self, keep: list) -> int:
        """Turn every machine off but these, so the view is of the target.

        Only equipment: pillars, walls and the ceiling stay, because they are
        what the clear faces are measured against. The machines are not -- the
        only ones that can be beside this EBS are the ones either side of it.
        """
        stage = self._get_stage()
        if stage is None:
            return 0
        self.show_equipment()
        spared = set(keep)
        turn_off = [path for path in self._eqp_index.values() if path not in spared]
        if self._author_opacity(stage, turn_off, True):
            self._hidden = turn_off
        return len(self._hidden)

    def show_equipment(self) -> None:
        """Put back what we turned off. The opinion is dropped, not set back to
        solid: a machine the user had made see-through themselves stays so."""
        if not self._hidden:
            return
        stage = self._get_stage()
        if stage is not None:
            self._author_opacity(stage, self._hidden, False)
        self._hidden = []

    def _looks_shaders(self, stage, path: str) -> list:
        """The shaders under one machine's Looks folder, the ones we can write.

        Materials do not move any more than the machines do, so the walk is
        paid on the first camera and not again until init. A machine that is
        an instance carries its Looks inside the prototype, where an opinion
        of ours would land on a path that composes to nothing; those are left
        out here and counted, rather than written and silently ignored.
        """
        found = self._eqp_looks.get(path)
        if found is not None:
            return found
        found = []
        looks = stage.GetPrimAtPath(path + "/" + LOOKS)
        if looks and looks.IsValid():
            stack = list(_children(looks))
            while stack:
                prim = stack.pop()
                if prim.GetTypeName() != SHADER_TYPE:
                    stack.extend(_children(prim))    # network under a NodeGraph
                    continue
                try:
                    shared = prim.IsInstanceProxy()
                except Exception:
                    shared = False
                if shared:
                    self._eqp_shared.add(path)
                else:
                    found.append(str(prim.GetPath()))
        self._eqp_looks[path] = found
        return found

    def _gone_layer(self, stage):
        """Our own layer over the session, holding the see-through opinions and
        nothing else.

        Putting the machines back is then one call on the layer instead of
        fifteen hundred spec removals -- and removing specs is the one thing a
        change block is not safe for, so the removals were going in one notice
        at a time, or not at all.
        """
        session = stage.GetSessionLayer()
        if self._gone is None:
            self._gone = Sdf.Layer.CreateAnonymous(GONE_LAYER)
        if self._gone.identifier not in session.subLayerPaths:
            session.subLayerPaths.insert(0, self._gone.identifier)
        return self._gone

    def _author_opacity(self, stage, paths: list, hide: bool) -> bool:
        """Make a whole list see-through, or put it back, in one go.

        One prim at a time is one change notice each, and Kit answers every one
        of them -- fifteen hundred of those is a panel that stops responding.
        The layer is written through Sdf inside a change block, which is the
        shape of the API that is safe to batch; the schema helpers read the
        stage back as they go and are not. The shaders are gathered first for
        the same reason: nothing reads the stage once the block is open.
        """
        if not paths:
            return True
        if not hide:
            if self._gone is not None:
                with self._stage_timer(f"show {len(paths)} equipment"):
                    self._gone.Clear()
            return True

        shaders, bare = [], 0
        with self._stage_timer(f"hide {len(paths)} equipment"):
            for path in paths:
                found = self._looks_shaders(stage, path)
                if found:
                    shaders.extend(found)
                else:
                    bare += 1
            if bare:
                self._note(f"{bare} of {len(paths)} machines have no shader we "
                           f"can write under {LOOKS}; those are left alone"
                           + (f" ({len(self._eqp_shared)} of them are instances)"
                              if self._eqp_shared else ""))
            if not shaders:
                return False
            layer = self._gone_layer(stage)
            try:
                with Sdf.ChangeBlock():
                    for shader in shaders:
                        spec = Sdf.CreatePrimInLayer(layer, Sdf.Path(shader))
                        for name, kind, value in GONE:
                            attribute = spec.attributes.get(name)
                            if attribute is None:
                                attribute = Sdf.AttributeSpec(
                                    spec, name, getattr(Sdf.ValueTypeNames, kind))
                            attribute.default = value
            except Exception as e:
                self._note(f"could not set opacity on {len(shaders)} shaders ({e})")
                return False
        self._check_gone(stage, shaders[0])
        return True

    def _check_gone(self, stage, shader: str) -> None:
        """Read one of them back off the stage.

        Whether the input we wrote is the one the material actually reads is
        not something the name can tell us -- a surface knows one spelling of
        opacity or the other, and an MDL module that was never given the
        parameter knows neither. One line saying what it composed to answers
        that from the log instead of from guesswork.
        """
        try:
            prim = stage.GetPrimAtPath(shader)
            if not prim or not prim.IsValid():
                self._note(f"wrote the opinion but {shader} is not on the "
                           f"stage -- nothing will look any different")
                return
            missed = []
            for name, _, want in GONE:
                attribute = prim.GetAttribute(name)
                got = attribute.Get() if attribute else None
                if got != want:
                    missed.append(f"{name}={got}")
            if missed:
                self._note(f"{shader} did not take " + ", ".join(missed))
            else:
                self._note(f"see-through reads back on {shader}")
        except Exception as e:
            self._note(f"could not read {shader} back ({e})")

    def get_selected_equipment(self) -> str:
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

        started = time.perf_counter()
        direct = f"{self._search_root.rstrip('/')}/{key}" if self._search_root else f"/{key}"
        prim = stage.GetPrimAtPath(direct)
        if prim.IsValid():
            self._eqp_index[key] = direct
            self._timings.append(["find equipment (direct path)",
                                  (time.perf_counter() - started) * 1000.0])
            return prim

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
    def resolve_anchor(prim: Usd.Prim, depth: int = ANCHOR_DEPTH):
        current, level = prim, 0
        while level < depth:
            children = _children(current) if current and current.IsValid() else []
            if not children:
                return current, False
            first = children[0]
            if first.GetTypeName() in PASS_TYPES:
                current = first               # grouping, not a level
                continue
            current, level = first, level + 1
        return current, True

    # -- port count (XML) ----------------------------------------------------

    def load_ports(self) -> int:
        self._port_map = {}
        self._port_offsets = {}
        self._port_addr = {}
        self._port_addr_of = {}
        self._addr_cad = {}
        self._addr_next = {}
        if not self._xml_path:
            return 0
        if self._load_cache():
            return len(self._port_map)

        found = self._scan_xml()
        if found is None:
            return 0
        self._collect_ports(found)
        self._save_cache()
        return len(self._port_map)

    def _scan_xml(self) -> "dict | None":
        scan = _PortScan()
        try:
            with self._stage_timer("XML: parse"):
                reads = self._feed_parser(scan)
        except Exception as e:
            self._note(f"xml parse failed: {e}")
            return None
        self._note(f"xml parsed, {reads} reads of {READ_BLOCK >> 20} MB")
        self._addr_cad = scan.addr_cad
        self._addr_next = scan.addr_next
        return scan.found

    def _feed_parser(self, scan: "_PortScan") -> int:
        """Read the file in big blocks and push them at the parser.

        Left to itself expat pulls the file 2 kB at a time, which is a round
        trip each over a share. The blocks are ours so that cannot happen.
        """
        parser = expat.ParserCreate()
        parser.buffer_text = True
        parser.StartElementHandler = scan.start
        parser.EndElementHandler = scan.end
        parser.CharacterDataHandler = scan.data

        reads = 0
        with open(self._xml_path, "rb") as handle:
            while True:
                block = handle.read(READ_BLOCK)
                reads += 1
                if not block:
                    break
                parser.Parse(block, False)
        parser.Parse(b"", True)
        return reads

    def _collect_ports(self, found: dict) -> None:
        spanning, gapped = [], []
        for key, by_index in found.items():
            indices = sorted(by_index)
            self._port_map[key] = indices
            self._port_offsets[key] = {i: by_index[i][0] for i in indices}
            by_port = {i: by_index[i][1] for i in indices
                       if by_index[i][1] is not None}
            self._port_addr_of[key] = by_port
            self._port_addr[key] = by_port[max(by_port)] if by_port else None
            if len(set(by_port.values())) > 1:
                spanning.append(key)
            if indices != list(range(1, len(indices) + 1)):
                gapped.append(key)
        for what, names in (("span several addr blocks", spanning),
                            ("have port indices that are not 1..N", gapped)):
            if names:
                self._note(f"{len(names)} equipment {what}: "
                           + ", ".join(sorted(names)[:6])
                           + (" ..." if len(names) > 6 else ""))

    # -- XML cache -----------------------------------------------------------

    def _cache_path(self) -> str:
        return self._xml_path + CACHE_SUFFIX

    def _source_stamp(self) -> list:
        stat = os.stat(self._xml_path)
        return [CACHE_VERSION, stat.st_size, stat.st_mtime_ns]

    def _load_cache(self) -> bool:
        path = self._cache_path()
        try:
            want = self._source_stamp()
        except OSError as e:
            self._note(f"xml not readable: {e}")
            return False
        if not os.path.exists(path):
            return False
        with self._stage_timer("XML: cache read"):
            try:
                with open(path, encoding="utf-8") as handle:
                    blob = json.load(handle)
                if blob.get("stamp") != want:
                    self._note(f"xml cache stale, parsing again "
                               f"(cache {blob.get('stamp')}, source {want})")
                    return False
                port_map = {k: [int(i) for i in v]
                            for k, v in blob["port_map"].items()}
                offsets = {k: {int(i): v for i, v in d.items()}
                           for k, d in blob["port_offsets"].items()}
                addr_of = {k: {int(i): v for i, v in d.items()}
                           for k, d in blob["port_addr_of"].items()}
                addr = dict(blob["port_addr"])
                cad = {int(k): tuple(v) for k, v in blob["addr_cad"].items()}
                nxt = {int(k): [(int(t), float(pu)) for t, pu in v]
                       for k, v in blob["addr_next"].items()}
            except Exception as e:
                self._note(f"xml cache unusable, parsing again: {e}")
                return False
        self._port_map, self._port_offsets = port_map, offsets
        self._port_addr, self._port_addr_of = addr, addr_of
        self._addr_cad, self._addr_next = cad, nxt
        self._note(f"xml cache hit: {path}")
        return True

    def _save_cache(self) -> None:
        path = self._cache_path()
        with self._stage_timer("XML: cache write"):
            try:
                blob = {"stamp": self._source_stamp(),
                        "port_map": self._port_map,
                        "port_offsets": self._port_offsets,
                        "port_addr": self._port_addr,
                        "port_addr_of": self._port_addr_of,
                        "addr_cad": self._addr_cad,
                        "addr_next": self._addr_next}
                # One write, not the thousands json.dump would make: on a
                # share every one of those is a round trip. Written aside and
                # renamed, so a run that dies half way leaves the old cache.
                text = json.dumps(blob)
                spare = path + ".part"
                with open(spare, "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.replace(spare, path)
            except Exception as e:
                self._note(f"xml cache not written: {e}")
                try:
                    os.remove(path + ".part")
                except OSError:
                    pass
                return
        self._note(f"xml cache written: {path} "
                   f"({os.path.getsize(path) / 1048576:.2f} MB)")

    def get_port_count(self, eqp_id: str) -> "int | None":
        indices = self.get_port_indices(eqp_id)
        return len(indices) if indices else None

    def get_port_indices(self, eqp_id: str) -> list:
        return list(self._port_map.get(eqp_id.upper(), []))

    # -- rail and port geometry ----------------------------------------------

    def find_rail(self, stage: Usd.Stage, addr_number: int, prefer=()):
        prefix = f"{RAIL_PREFIX}{addr_number}_"
        found = self._rails_from(stage, addr_number)
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

    def _rails_from(self, stage: Usd.Stage, addr_number: int) -> list:
        if self._rail_index is None:
            self._rail_index = {}
            root = stage.GetPrimAtPath(self._rail_root) if self._rail_root else None
            if root is not None and root.IsValid():
                source = _children(root)
            else:
                if self._rail_root:
                    print(f"[ebs] no rails under {self._rail_root}, scanning the stage")
                source = (p for p, _ in self._walk(stage))
            for prim in source:
                parts = prim.GetName().lower().split("_")
                if len(parts) < 3 or parts[0] != RAIL_PREFIX[:-1]:
                    continue
                if not (parts[1].isdigit() and parts[2].isdigit()):
                    continue
                self._rail_index.setdefault(int(parts[1]), []).append(
                    (prim, int(parts[2])))
            print(f"[ebs] indexed rails leaving {len(self._rail_index)} addrs")
        return self._rail_index.get(addr_number, [])

    def _rail_axis(self, addr_a: int, addr_b: int) -> "int | None":
        cad_a, cad_b = self._addr_cad.get(addr_a), self._addr_cad.get(addr_b)
        if cad_a is None or cad_b is None:
            return None
        span = (cad_b[0] - cad_a[0], cad_b[1] - cad_a[1])
        moves = [i for i in (0, 1) if abs(span[i]) > CAD_SLACK]
        if len(moves) != 1:
            return None
        held = 1 - moves[0]
        if abs(span[held]) > 1e-6:
            print(f"[ebs]   addr {addr_a} -> {addr_b} wanders {span[held]:+.4f} cad "
                  f"on {'XY'[held]}, within {CAD_SLACK:g}: held at addr {addr_a}")
        return moves[0]

    def _addr_step(self, addr: int, axis: int):
        cad = self._addr_cad.get(addr)
        if cad is None:
            return None
        for target, puls in self._addr_next.get(addr, []):
            if not puls or puls <= 0 or self._rail_axis(addr, target) != axis:
                continue
            length = (self._addr_cad[target][axis] - cad[axis]) / CAD_PER_UNIT
            if abs(length) > 1e-9:
                return abs(length), puls
        return None

    def compute_port_points(self, stage: Usd.Stage, eqp_id: str):
        key = eqp_id.upper()
        self._why = ""
        addr_a = self._port_addr.get(key)
        if addr_a is None:
            return self._fail(key, "no addr block found for its ports",
                              "XML에 포트 없음")

        spilled = {a for i, a in self._port_addr_of.get(key, {}).items()
                   if a != addr_a}
        rail, addr_b, axis = self.find_rail(stage, addr_a, prefer=spilled)
        if rail is None:
            return self._fail(key, f"no straight {RAIL_PREFIX}<addr>_* rail "
                              f"leaving addr {addr_a}",
                              f"직선 레일 없음 (addr {addr_a})")

        cad_a, cad_b = self._addr_cad[addr_a], self._addr_cad[addr_b]
        span = (cad_b[0] - cad_a[0], cad_b[1] - cad_a[1])

        length = span[axis] / CAD_PER_UNIT                   # signed: carries direction
        direction = 1.0 if length >= 0 else -1.0

        rail_local = self._local_translation(rail)
        start = rail_local[axis] - length / 2.0     # 레일 시작 = addr 위치
        origin = [rail_local[0], rail_local[1], rail_local[2]]
        origin[axis] = start
        onward = list(origin)
        onward[axis] = start + length
        self._rail_frame = (Gf.Vec3d(*origin), Gf.Vec3d(*onward), axis)

        name = "XY"[axis]
        print(f"[ebs] {key}: addr {addr_a} -> rail {rail.GetName()} (neighbour {addr_b})")
        print(f"[ebs]   cad {cad_a} -> {cad_b}, span ({span[0]:+.3f}, {span[1]:+.3f})"
              f" -> runs along {name}, direction {direction:+.0f}")
        print(f"[ebs]   length {span[axis]:+.3f} / {CAD_PER_UNIT:.4f} = {length:+.4f} units")
        print(f"[ebs]   rail.{name.lower()} {rail_local[axis]:.4f} - {length:+.4f}/2 "
              f"= start {start:.4f}")
        print(f"[ebs]   offset scale: {self._offset_scale}")

        if self._offset_scale in (SCALE_PULS, SCALE_SNAP):
            along = self._coords_by_puls(key, axis, direction, start, addr_a)
        else:
            along = self._coords_by_offset(key, axis, direction, start, addr_a)
        if along is None:
            return None

        print(f"[ebs]   a constant shift would match: half a rail "
              f"{abs(length) / 2:.4f}, a whole rail {abs(length):.4f}"
              f"  (port 1 is {abs(along[1] - start):.4f} from the base addr)")

        points = {}
        for index, coord in along.items():
            coords = [rail_local[0], rail_local[1], rail_local[2]]
            coords[axis] = coord
            points[index] = Gf.Vec3d(*coords)
        print(f"[ebs]   {name.lower()} = " +
              ", ".join(f"{i}:{along[i]:.4f}" for i in sorted(along)) +
              " (rail's other axes kept)")
        return points, axis, rail

    def _coords_by_offset(self, key: str, axis: int, direction: float,
                          start: float, addr_a: int) -> "dict | None":
        offsets = self._rebase_offsets(key, addr_a, axis, direction)
        spacing = self._port_spacing(key, offsets)
        if spacing is None:
            return None
        offset_zero = offsets[1] + spacing                   # one step past port 1

        gaps = [f"{offsets[i] - offsets[i + 1]:.1f}"
                for i in sorted(offsets) if i + 1 in offsets]
        print(f"[ebs]   offsets " +
              ", ".join(f"{i}:{offsets[i]:.1f}" for i in sorted(offsets)) +
              f" | gaps [{', '.join(gaps)}] -> spacing {spacing:.1f}")
        print(f"[ebs]   offset0 = {offsets[1]:.1f} + {spacing:.1f} = {offset_zero:.1f}"
              f" / {OFFSET_PER_UNIT:.0f} = {offset_zero / OFFSET_PER_UNIT:.4f} units")

        all_offsets = dict(offsets)
        all_offsets[0] = offset_zero
        return {index: start + direction * offset / OFFSET_PER_UNIT
                for index, offset in all_offsets.items()}

    def _coords_by_puls(self, key: str, axis: int, direction: float,
                        start: float, addr_a: int) -> "dict | None":
        offsets = self._port_offsets.get(key, {})
        addr_of = self._port_addr_of.get(key, {})
        base_cad = self._addr_cad.get(addr_a)
        if 1 not in offsets or base_cad is None:
            return self._fail(key, "no offset for port 1",
                              f"포트 1 offset 없음 (있는 포트 {sorted(offsets)})")

        along = {}
        for index in sorted(offsets):
            offset = offsets[index]
            addr = addr_of.get(index, addr_a)
            cad = self._addr_cad.get(addr)
            if offset is None or cad is None:
                return self._fail(key, "a port has no offset, or its addr no cad",
                                  f"포트 {index} offset 또는 addr {addr} cad 없음")
            step = self._addr_step(addr, axis)
            if step is None:
                self._blocked = (f"{key}: addr {addr} has no straight {PULS_KEY} "
                                 f"run for port {index}")
                print(f"[ebs] {self._blocked}")
                self._why = f"직선 {PULS_KEY} 구간 없음 (addr {addr})"
                return None
            seg_length, puls = step
            addr_start = start + (cad[axis] - base_cad[axis]) / CAD_PER_UNIT
            walk = offset * seg_length / puls
            along[index] = addr_start + direction * walk
            print(f"[ebs]   port {index} @ addr {addr}: {offset:.1f} x "
                  f"{seg_length:.4f}/{puls:.0f} = {walk:+.4f} units "
                  f"from {addr_start:.4f}")

        steps = [along[i] - along[i + 1] for i in sorted(along) if i + 1 in along]
        if not steps:
            return self._fail(key, "fewer than two ports, nothing to step by",
                              f"포트 {len(along)}개, 최소 2개 필요")
        pitch = sum(steps) / len(steps)
        along[0] = along[1] + pitch
        print(f"[ebs]   pitch " + ", ".join(f"{s:.4f}" for s in steps) +
              f" -> {pitch:.4f} units, port 0 at {along[0]:.4f}")

        return along

    def _rebase_offsets(self, key: str, base_addr: int, axis: int,
                        direction: float) -> dict:
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
        gaps = [offsets[i] - offsets[i + 1]
                for i in sorted(offsets) if i + 1 in offsets]
        if 1 not in offsets or not gaps:
            print(f"[ebs] {key}: no offsets for ports 1 and 2, got {offsets}")
            self._why = f"포트 1·2 offset 없음 (있는 포트 {sorted(offsets)})"
            return None

        spacing = sum(gaps) / len(gaps)
        if spacing <= 0:
            print(f"[ebs] {key}: ports should get closer to the addr as the number "
                  f"rises, got {offsets}")
        if len(gaps) > 1 and max(gaps) - min(gaps) > 1e-6:
            print(f"[ebs] {key}: port spacing is uneven {gaps}, using {spacing}")
        return spacing

    def compute_target(self, stage: Usd.Stage, eqp_id: str, anchor: Usd.Prim):
        self._port_world = {}
        found = self.compute_port_points(stage, eqp_id)
        if found is None:
            return None
        points, axis, rail = found

        to_world = self._parent_world(rail)
        anchor_world = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()).ExtractTranslation()
        spots = {index: to_world.Transform(spot) for index, spot in points.items()}
        slide = self._snap_shift(to_world, axis, rail, spots, anchor_world, eqp_id)

        for index, spot in spots.items():
            self._port_rail_z = spot[2]      # the rail's own height, shared by all ports
            self._port_world[index] = Gf.Vec3d(spot[0] + slide[0], spot[1] + slide[1],
                                               anchor_world[2])

        in_rail_space = points[0]
        world = spots[0]
        target = self._port_world[0]
        print(f"[ebs]   rail space ({in_rail_space[0]:.4f}, {in_rail_space[1]:.4f}, "
              f"{in_rail_space[2]:.4f}) -> world ({world[0]:.4f}, {world[1]:.4f}, "
              f"{world[2]:.4f})")
        for index in sorted(self._port_world):
            spot = self._port_world[index]
            self._note(f"port {index} world ({spot[0]:.4f}, {spot[1]:.4f}, "
                       f"{spot[2]:.4f})")
        print(f"[ebs]   target = ({target[0]:.4f}, {target[1]:.4f}, {target[2]:.4f})"
              f"  [rail xy, anchor z from {anchor.GetName()}]")
        return target

    def _snap_shift(self, to_world, axis: int, rail, spots: dict, here,
                    eqp_id: str) -> tuple:
        """How far to slide every port so port 1 lands on the pivot.

        Only in snap mode, and only when the placement is otherwise sound. The
        amount is the residual the report calls coord_diff, taken off every
        port at once, so the spacing between them is untouched -- it is the
        origin that moves, not the pitch. Off the rail and in z nothing moves.

        This is align's alone: the sweep works off compute_port_points, so its
        coord_diff still says what the residual was.
        """
        if self._offset_scale != SCALE_SNAP:
            return (0.0, 0.0)
        if 1 not in spots or self._rail_frame is None:
            self._note("no snap: port 1 has no place to be")
            return (0.0, 0.0)

        row = self._measure(to_world, axis, rail, spots[1], here,
                            self._port_addr.get(eqp_id.upper()))
        state = self._pivot_state(row, here, eqp_id)
        if state != "TRUE":
            self._note(f"no snap: pivot reads {state}")
            return (0.0, 0.0)

        along, gap = row["_along"], row["coord_diff"]
        self._note(f"snapped every port {-gap:+.4f} along the rail, "
                   f"port 1 onto the pivot")
        return (-along[0] * gap, -along[1] * gap)

    @staticmethod
    def _local_translation(prim: Usd.Prim) -> Gf.Vec3d:
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return Gf.Vec3d(0.0, 0.0, 0.0)
        return xformable.GetLocalTransformation(
            Usd.TimeCode.Default()).ExtractTranslation()

    @staticmethod
    def _parent_world(prim: Usd.Prim) -> Gf.Matrix4d:
        parent = prim.GetParent() if prim else None
        if parent and parent.IsValid() and UsdGeom.Xformable(parent):
            return UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default())
        return Gf.Matrix4d(1.0)

    # -- alignment -----------------------------------------------------------

    def _place_ebs(self, ebs_prim: Usd.Prim, world_position: Gf.Vec3d,
                   anchor: Usd.Prim) -> bool:
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
        stage = self._get_stage()
        if stage is None or not anchor_prim.IsValid():
            return False
        xformable = UsdGeom.Xformable(ebs_prim)
        if not xformable:
            return False

        tc = Usd.TimeCode.Default()
        anchor_world = UsdGeom.Xformable(anchor_prim).ComputeLocalToWorldTransform(tc)

        target_local = anchor_world * self._parent_world(ebs_prim).GetInverse()

        rotation = self._normalized_rows(target_local)
        scale = self._extract_scale(xformable.GetLocalTransformation(tc))
        return self._write_transform(stage, xformable, rotation, scale,
                                     target_local.ExtractTranslation())

    def _write_transform(self, stage, xformable, rotation, scale, translation) -> bool:
        ops = {op.GetOpName(): op for op in xformable.GetOrderedXformOps()}

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
        return Gf.Matrix4d(
            rotation[0][0] * scale[0], rotation[0][1] * scale[0], rotation[0][2] * scale[0], 0.0,
            rotation[1][0] * scale[1], rotation[1][1] * scale[1], rotation[1][2] * scale[1], 0.0,
            rotation[2][0] * scale[2], rotation[2][1] * scale[2], rotation[2][2] * scale[2], 0.0,
            translation[0], translation[1], translation[2], 1.0,
        )

    @staticmethod
    def _euler(rotation, order: str = "XYZ") -> tuple:
        axes = {"X": 0, "Y": 1, "Z": 2}
        a, b, c = (axes[ch] for ch in order)          # first, second, third axis
        m = [[rotation[i][j] for j in range(3)] for i in range(3)]

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
        rows = matrix.ExtractRotationMatrix()
        return [Gf.Vec3d(rows[i][0], rows[i][1], rows[i][2]).GetNormalized()
                for i in range(3)]

    @staticmethod
    def _extract_scale(matrix: Gf.Matrix4d) -> Gf.Vec3d:
        rows = matrix.ExtractRotationMatrix()
        scale = [Gf.Vec3d(rows[i][0], rows[i][1], rows[i][2]).GetLength() for i in range(3)]
        return Gf.Vec3d(*[v if v > 1e-12 else 1.0 for v in scale])

    # -- collision -----------------------------------------------------------

    @staticmethod
    def _bounds_cache():
        """One of these a step. It remembers, and computing a world bound on a
        plant this size is the whole cost of looking anything up."""
        return UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,          # read extentsHint instead of walking geometry
        )

    def check_collision(self, ebs_prim: Usd.Prim, exclude: list = None,
                        cache=None) -> dict:
        stage = self._get_stage()
        if stage is None:
            return {face: [] for face in FACES}
        self._visible = {}          # visibility can change between runs
        cache = cache if cache is not None else self._bounds_cache()

        with self._stage_timer("EBS bounds"):
            ebs_bbox = self._ebs_bound(ebs_prim)
            local_box = ebs_bbox.GetRange()
            to_world = ebs_bbox.GetMatrix()
            world_box = ebs_bbox.ComputeAlignedRange()
        if local_box.IsEmpty():
            return {face: [] for face in FACES}

        with self._stage_timer("build cells"):
            cells = {
                face: [Gf.BBox3d(rng, to_world).ComputeAlignedRange()
                       for rng, _ in boxes]
                for face, boxes in self._build_cells(local_box).items()
            }

        with self._stage_timer("gather nearby"):
            depth = self._probe_depth(local_box)
            margin = Gf.Vec3d(depth, depth, depth)
            search = Gf.Range3d(world_box.GetMin() - margin, world_box.GetMax() + margin)
            skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
            candidates, visited = self._gather_nearby(stage, cache, search, skip)
        coarse = len(candidates)

        size = local_box.GetMax() - local_box.GetMin()
        self._note(f"precision {self._precision}, probe depth {depth:.4f}"
                   f"{' (set by hand)' if self._clearance > 0 else ' (from the EBS size)'}"
                   f", EBS size ({size[0]:.3f}, {size[1]:.3f}, {size[2]:.3f})")
        self._note(f"EBS local box {tuple(round(v, 3) for v in local_box.GetMin())} .. "
                   f"{tuple(round(v, 3) for v in local_box.GetMax())} "
                   f"(the cells tile exactly this)")
        self._note(f"EBS world box {tuple(round(v, 2) for v in world_box.GetMin())} .. "
                   f"{tuple(round(v, 2) for v in world_box.GetMax())}")
        self._note(f"visited {visited} prims, {coarse} meshes within the probe, "
                   f"skipping {skip}")

        with self._stage_timer(f"cell test ({len(candidates)} candidates)"):
            result = {face: [False] * len(boxes) for face, boxes in cells.items()}
            hits = {}
            self._blockers = {}
            triangle_tests = 0
            boxed_only = set()
            flat = [(face, i, cell)
                    for face, boxes in cells.items() for i, cell in enumerate(boxes)]

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
                            if (not Gf.Range3d.GetIntersection(tri_box, cell).IsEmpty()
                                    and self._triangle_hits_box(triangle, cell)):
                                result[face][i] = True
                                self._blockers.setdefault(face, path)
                                hits.setdefault(path.rsplit("/", 1)[-1], []).append(
                                    f"{face}[{i}]")
                    continue

                if self._precision == PRECISION_TRI:
                    boxed_only.add(path)
                for face, i, _ in targets:
                    result[face][i] = True
                    self._blockers.setdefault(face, path)
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
            self._note(f"blocked by {len(hits)}: "
                       + "; ".join(f"{name} {', '.join(where)}"
                                   for name, where in sorted(hits.items())[:4])
                       + (" ..." if len(hits) > 4 else ""))
        elif candidates:
            self._note("candidates were near but none reached a cell")
        else:
            self._note("nothing within clearance - raise it if that looks wrong")

        return result

    def _forget_triangles(self, prim: Usd.Prim) -> None:
        """Drop a prim's cached triangles. They are in world space, so moving it
        makes them a lie, and only the EBS ever moves."""
        if prim is None or not prim.IsValid():
            return
        root = str(prim.GetPath())
        for cache in (self._triangles, self._local):
            for path in [p for p in cache
                         if p == root or p.startswith(root + "/")]:
                del cache[path]

    def _mesh_local(self, stage, path: str):
        """A mesh's own points and faces, and where it stands. Read once.

        Kept unconverted on purpose: converting every point to world space is
        the expensive part, and whoever asks may only want a corner of it.
        """
        if path in self._local:
            return self._local[path]
        prim = stage.GetPrimAtPath(path) if stage else None
        mesh = UsdGeom.Mesh(prim) if prim and prim.IsValid() else None
        data = None
        if mesh:
            tc = Usd.TimeCode.Default()
            points = self._attr_value(mesh.GetPointsAttr(), tc)
            counts = self._attr_value(mesh.GetFaceVertexCountsAttr(), tc)
            indices = self._attr_value(mesh.GetFaceVertexIndicesAttr(), tc)
            if points is None or counts is None or indices is None:
                self._boxed.setdefault("no point data", []).append(path)
            else:
                data = (points, counts, indices,
                        UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(tc))
        elif prim and prim.IsValid():
            self._boxed.setdefault(f"a {prim.GetTypeName()}", []).append(path)
        self._local[path] = data
        return data

    def _mesh_triangles(self, stage, path: str) -> list:
        if path in self._triangles:
            return self._triangles[path]
        triangles = []
        data = self._mesh_local(stage, path)
        if data:
            points, counts, indices, to_world = data
            world = [to_world.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in points]
            cursor = 0
            for count in counts:
                if count >= 3 and cursor + count <= len(indices):
                    fan = [world[indices[cursor + k]] for k in range(count)]
                    for k in range(1, count - 1):
                        triangles.append((fan[0], fan[k], fan[k + 1]))
                cursor += count
        self._triangles[path] = triangles
        return triangles

    def _triangles_reaching(self, stage, path: str, box: Gf.Range3d) -> list:
        """(path, triangle, low corner, high corner) for what reaches into `box`.

        The box is pulled back through the mesh's transform once, and the faces
        are sifted where they live. Only what survives is worth converting, and
        where the EBS meets a corner of a machine that is nearly none of it.
        """
        lo_box, hi_box = box.GetMin(), box.GetMax()
        cached = self._triangles.get(path)
        if cached is not None:
            # The face check has already converted this one. Re-reading it to
            # save a filter would be the more expensive way round.
            kept = []
            for a, b, c in cached:
                lo = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]),
                      min(a[2], b[2], c[2]))
                hi = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]),
                      max(a[2], b[2], c[2]))
                if all(lo[i] <= hi_box[i] and hi[i] >= lo_box[i] for i in range(3)):
                    kept.append((path, (a, b, c), lo, hi))
            return kept

        data = self._mesh_local(stage, path)
        if not data:
            return []
        points, counts, indices, to_world = data
        near = self._pulled_back(box, to_world)
        if near is None:
            return []
        (lx, ly, lz), (hx, hy, hz) = near
        # Converting a point through Gf allocates one and crosses the binding,
        # and so does reading each of its three numbers back. Sixty thousand
        # triangles of that is most of what this costs, so the matrix is taken
        # apart once and the arithmetic done here, in plain floats. Whether
        # that is the same arithmetic is not assumed: the first point is done
        # both ways, and the fast path is only used where they agree.
        a00 = a01 = a02 = a10 = a11 = a12 = a20 = a21 = a22 = 0.0
        a30 = a31 = a32 = 0.0
        plain = False
        try:
            r0, r1, r2, r3 = (to_world.GetRow(0), to_world.GetRow(1),
                              to_world.GetRow(2), to_world.GetRow(3))
            a00, a01, a02 = r0[0], r0[1], r0[2]
            a10, a11, a12 = r1[0], r1[1], r1[2]
            a20, a21, a22 = r2[0], r2[1], r2[2]
            a30, a31, a32 = r3[0], r3[1], r3[2]
            if points:
                first = points[0]
                x, y, z = first[0], first[1], first[2]
                mine = (x * a00 + y * a10 + z * a20 + a30,
                        x * a01 + y * a11 + z * a21 + a31,
                        x * a02 + y * a12 + z * a22 + a32)
                theirs = to_world.Transform(Gf.Vec3d(x, y, z))
                span = max(abs(theirs[i]) for i in range(3)) or 1.0
                plain = all(abs(mine[i] - theirs[i]) <= span * 1e-9
                            for i in range(3))
                if not plain:
                    self._boxed.setdefault("an unexpected transform", []).append(path)
        except Exception:
            plain = False

        # A million faces go through this and most are nowhere near the box,
        # so what it costs per face is what it costs. One pass, each point read
        # once, nothing built that gets thrown away.
        kept, cursor, total = [], 0, len(indices)
        for count in counts:
            end = cursor + count
            if count < 3 or end > total:
                cursor = end
                continue
            corner = points[indices[cursor]]
            x0 = x1 = corner[0]
            y0 = y1 = corner[1]
            z0 = z1 = corner[2]
            for k in range(cursor + 1, end):
                corner = points[indices[k]]
                v = corner[0]
                if v < x0:
                    x0 = v
                elif v > x1:
                    x1 = v
                v = corner[1]
                if v < y0:
                    y0 = v
                elif v > y1:
                    y1 = v
                v = corner[2]
                if v < z0:
                    z0 = v
                elif v > z1:
                    z1 = v
            if (x0 > hx or x1 < lx or y0 > hy or y1 < ly
                    or z0 > hz or z1 < lz):
                cursor = end
                continue

            fan = []
            for k in range(cursor, end):
                corner = points[indices[k]]
                x, y, z = corner[0], corner[1], corner[2]
                fan.append((x * a00 + y * a10 + z * a20 + a30,
                            x * a01 + y * a11 + z * a21 + a31,
                            x * a02 + y * a12 + z * a22 + a32)
                           if plain else
                           to_world.Transform(Gf.Vec3d(x, y, z)))
            for k in range(1, count - 1):
                a, b, c = fan[0], fan[k], fan[k + 1]
                low = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]),
                       min(a[2], b[2], c[2]))
                high = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]),
                        max(a[2], b[2], c[2]))
                # The face got through on its own corners; a triangle of it
                # still need not reach the box. Same rule as the cached path.
                if (low[0] <= hi_box[0] and high[0] >= lo_box[0]
                        and low[1] <= hi_box[1] and high[1] >= lo_box[1]
                        and low[2] <= hi_box[2] and high[2] >= lo_box[2]):
                    kept.append((path, (a, b, c), low, high))
            cursor = end
        return kept

    @staticmethod
    def _pulled_back(box: Gf.Range3d, to_world):
        """`box` in the mesh's own space, as the box that surely covers it."""
        try:
            inverse = to_world.GetInverse()
        except Exception:
            return None
        lo, hi = box.GetMin(), box.GetMax()
        corners = [inverse.Transform(Gf.Vec3d(x, y, z))
                   for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                   for z in (lo[2], hi[2])]
        return (tuple(min(c[i] for c in corners) for i in range(3)),
                tuple(max(c[i] for c in corners) for i in range(3)))

    @staticmethod
    def _attr_value(attr, tc):
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
        lo, hi = box.GetMin(), box.GetMax()
        centre = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        half = [(hi[i] - lo[i]) * 0.5 for i in range(3)]
        v = [[triangle[j][i] - centre[i] for i in range(3)] for j in range(3)]

        for i in range(3):
            if min(v[0][i], v[1][i], v[2][i]) > half[i] or \
               max(v[0][i], v[1][i], v[2][i]) < -half[i]:
                return False

        edges = [[v[1][i] - v[0][i] for i in range(3)],
                 [v[2][i] - v[1][i] for i in range(3)],
                 [v[0][i] - v[2][i] for i in range(3)]]

        normal = [edges[0][1] * edges[1][2] - edges[0][2] * edges[1][1],
                  edges[0][2] * edges[1][0] - edges[0][0] * edges[1][2],
                  edges[0][0] * edges[1][1] - edges[0][1] * edges[1][0]]
        reach = sum(half[i] * abs(normal[i]) for i in range(3))
        distance = sum(normal[i] * v[0][i] for i in range(3))
        if abs(distance) > reach:
            return False

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

    def _is_visible(self, prim, path: str) -> bool:
        """Is this prim itself shown?

        Its own attribute, not the one computed down the chain of its parents:
        the only caller walks from the top and stops at anything hidden, so an
        ancestor's answer has already been given by the time we are here.
        Asking for the computed one walks those ancestors again, per prim, and
        on this plant that was most of what looking around cost.
        """
        known = self._visible.get(path)
        if known is not None:
            return known
        visible = True
        # Asked of every prim, so nothing here may raise on the ordinary case:
        # a prim that is not imageable at all is common, and letting that come
        # back as an exception costs more than the question does.
        imageable = UsdGeom.Imageable(prim)
        if imageable:
            attribute = imageable.GetVisibilityAttr()
            if attribute:
                visible = attribute.Get(NOW) != UsdGeom.Tokens.invisible
        self._visible[path] = visible
        return visible

    @staticmethod
    def _overlaps(a: Gf.Range3d, b: Gf.Range3d) -> bool:
        overlap = Gf.Range3d.GetIntersection(a, b)
        if overlap.IsEmpty():
            return False
        extent = overlap.GetMax() - overlap.GetMin()
        return all(extent[i] > OVERLAP_EPS for i in range(3))

    def _ebs_bound(self, prim: Usd.Prim):
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

    def _gather_nearby(self, stage, cache, search: Gf.Range3d, skip: list,
                       root: Usd.Prim = None) -> tuple:
        found, visited = [], 0
        ours_exact = frozenset(OURS)
        skip_exact = frozenset(skip)
        skip_under = tuple(s + "/" for s in skip)
        stack = [root] if root is not None else list(_children(stage.GetPseudoRoot()))
        while stack:
            prim = stack.pop()
            path = str(prim.GetPath())
            if path in ours_exact or path.startswith(OURS_UNDER):
                continue                       # what we drew is not an obstacle
            if path in skip_exact or (skip_under and path.startswith(skip_under)):
                continue
            type_name = prim.GetTypeName()
            if type_name in SKIP_TYPES or type_name.endswith("Light"):
                continue
            if not self._is_visible(prim, path):
                continue                       # hides the whole subtree with it

            visited += 1
            box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if box.IsEmpty() or not self._overlaps(box, search):
                continue
            if type_name in GEOMETRY_TYPES:
                found.append((path, box))
                continue
            stack.extend(_children(prim))
        return found, visited

    def check_equipment(self, ebs_prim: Usd.Prim, eqp_prim: Usd.Prim,
                        cache=None) -> dict:
        """Does the EBS pass through the equipment it was placed on?

        The three-face check asks what is around the EBS; this asks whether the
        two occupy the same space. They are meant to touch, so this is the
        strict test -- triangles that actually cross -- rather than boxes, which
        would call every mounting a collision. Equipment that sits wholly inside
        the EBS crosses nothing and is not interference either; that is a thing
        fitting, which is what the run is hoping for.
        """
        stage = self._get_stage()
        blank = {"hit": False, "pairs": [], "tests": 0}
        if stage is None or eqp_prim is None or not eqp_prim.IsValid():
            return blank

        cache = cache if cache is not None else self._bounds_cache()
        world_box = self._ebs_bound(ebs_prim).ComputeAlignedRange()
        if world_box.IsEmpty():
            return blank

        with self._stage_timer("interference: gather"):
            ours, _ = self._gather_nearby(stage, cache, world_box, [], root=ebs_prim)
            theirs, _ = self._gather_nearby(stage, cache, world_box, [], root=eqp_prim)
        if not ours or not theirs:
            self._note(f"no interference test: {len(ours)} EBS meshes against "
                       f"{len(theirs)} on the equipment")
            return blank

        # One region for the whole question, so each mesh is read and filtered
        # once instead of once per pairing with the other side.
        shared = Gf.Range3d.GetIntersection(self._union([b for _, b in ours]),
                                            self._union([b for _, b in theirs]))
        if shared.IsEmpty():
            self._note(f"clear of the equipment: {len(ours)} EBS meshes and "
                       f"{len(theirs)} on it never share a box")
            return blank

        with self._stage_timer("interference: read triangles"):
            mine = self._triangles_near(stage, ours, shared)
            yours = self._triangles_near(stage, theirs, shared)
        if not mine or not yours:
            self._note(f"clear of the equipment: nothing reaches the shared box "
                       f"({len(mine)} against {len(yours)} triangles)")
            return blank

        with self._stage_timer("interference: test"):
            pairs, tests = self._meetings(mine, yours, shared)
        self._note(f"interference: {len(mine)} EBS triangles against {len(yours)} "
                   f"on the equipment, {tests} pairs tested")
        return {"hit": bool(pairs), "pairs": pairs, "tests": tests}

    @staticmethod
    def _union(boxes: list) -> Gf.Range3d:
        lo = [min(b.GetMin()[i] for b in boxes) for i in range(3)]
        hi = [max(b.GetMax()[i] for b in boxes) for i in range(3)]
        return Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))

    def _triangles_near(self, stage, meshes: list, box: Gf.Range3d) -> list:
        """(path, triangle, its low corner, its high corner) that reach into `box`.

        Read and filtered once per mesh. Two meshes that overlap at all usually
        overlap in a corner, so this is what keeps the exact test off the other
        several thousand triangles -- and the corners are kept because the test
        needs them again and recomputing them is most of its cost.
        """
        kept = []
        for path, mesh_box in meshes:
            if Gf.Range3d.GetIntersection(mesh_box, box).IsEmpty():
                continue
            kept.extend(self._triangles_reaching(stage, path, box))
        return kept

    def _meetings(self, mine: list, yours: list, box: Gf.Range3d) -> tuple:
        """Which meshes of the two actually cross, and how many pairs it took.

        The exact test is expensive enough that it must not be reached often, so
        the equipment's triangles go in a grid over the shared box and each of
        the EBS's only meets the ones sharing a cell with it. Boxes are compared
        before triangles; only what survives both is worth the real test.
        """
        grid, origin, step, spread = self._grid_of(yours, box)
        pairs, tests = [], 0
        for ebs_path, triangle, lo, hi in mine:
            seen = set()
            for key in self._cells_of(lo, hi, origin, step, spread):
                seen.update(grid.get(key, ()))
            for index in seen:
                eqp_path, other, other_lo, other_hi = yours[index]
                if any(lo[i] > other_hi[i] or hi[i] < other_lo[i] for i in range(3)):
                    continue
                tests += 1
                if self._triangles_meet(triangle, other):
                    if (ebs_path, eqp_path) not in pairs:
                        pairs.append((ebs_path, eqp_path))
                    if len(pairs) >= MEET_LIMIT:
                        return pairs, tests
        return pairs, tests

    @classmethod
    def _grid_of(cls, items: list, box: Gf.Range3d) -> tuple:
        """Bucket triangles by the cells of a uniform grid over `box`."""
        origin = box.GetMin()
        size = [max(box.GetMax()[i] - origin[i], 1e-9) for i in range(3)]
        spread = max(1, min(GRID_CELLS, int(round(len(items) ** (1.0 / 3.0)))))
        step = [size[i] / spread for i in range(3)]
        grid = {}
        for index, (_, _, lo, hi) in enumerate(items):
            for key in cls._cells_of(lo, hi, origin, step, spread):
                grid.setdefault(key, []).append(index)
        return grid, origin, step, spread

    @staticmethod
    def _cells_of(lo, hi, origin, step, spread):
        """Every cell a box touches, clamped to the grid."""
        spans = []
        for i in range(3):
            first = int((lo[i] - origin[i]) / step[i])
            last = int((hi[i] - origin[i]) / step[i])
            spans.append(range(max(0, min(first, spread - 1)),
                               max(0, min(last, spread - 1)) + 1))
        return [(x, y, z) for x in spans[0] for y in spans[1] for z in spans[2]]

    @classmethod
    def _triangles_meet(cls, a, b) -> bool:
        """Do two triangles actually cross?

        Where two triangles that are not in the same plane meet, the meeting is
        a segment, and its ends are edges of one piercing the other. So the six
        edges against the two faces is the whole test. Triangles lying in one
        plane are not caught, and are not what interference means here: that is
        two surfaces flush against each other, which is how a thing gets mounted.
        """
        for edge in ((a[0], a[1]), (a[1], a[2]), (a[2], a[0])):
            if cls._segment_hits_triangle(edge[0], edge[1], b):
                return True
        for edge in ((b[0], b[1]), (b[1], b[2]), (b[2], b[0])):
            if cls._segment_hits_triangle(edge[0], edge[1], a):
                return True
        return False

    @staticmethod
    def _segment_hits_triangle(start, end, triangle) -> bool:
        """Moller-Trumbore, with the ray cut to the segment."""
        v0, v1, v2 = triangle
        direction = [end[i] - start[i] for i in range(3)]
        edge1 = [v1[i] - v0[i] for i in range(3)]
        edge2 = [v2[i] - v0[i] for i in range(3)]

        def cross(p, q):
            return [p[1] * q[2] - p[2] * q[1],
                    p[2] * q[0] - p[0] * q[2],
                    p[0] * q[1] - p[1] * q[0]]

        def dot(p, q):
            return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]

        pitch = cross(direction, edge2)
        slope = dot(edge1, pitch)
        if abs(slope) < 1e-12:
            return False                     # parallel: the coplanar case
        scale = 1.0 / slope
        offset = [start[i] - v0[i] for i in range(3)]
        u = scale * dot(offset, pitch)
        if u < 0.0 or u > 1.0:
            return False
        turn = cross(offset, edge1)
        v = scale * dot(direction, turn)
        if v < 0.0 or u + v > 1.0:
            return False
        along = scale * dot(edge2, turn)
        return 0.0 <= along <= 1.0

    def _build_cells(self, box: Gf.Range3d) -> dict:
        up_axis = 1 if UsdGeom.GetStageUpAxis(self._get_stage()) == UsdGeom.Tokens.y else 2
        front_axis = 3 - up_axis                        # front/back, not evaluated
        side_axis = 3 - up_axis - front_axis            # sides (X when Z-up)
        t = self._probe_depth(box)
        lo, hi = box.GetMin(), box.GetMax()
        extent = [hi[i] - lo[i] for i in range(3)]
        unit = max(extent) / GRID if max(extent) > 0 else 1.0
        divisions = [max(1, int(round(extent[i] / unit))) if unit > 0 else 1
                     for i in range(3)]

        cells = {}
        shapes = {}
        faces = {}

        def make(fixed_axis, outward, row_axis, col_axis):
            rows, cols = divisions[row_axis], divisions[col_axis]
            out = []
            row_lo, row_hi = lo[row_axis], hi[row_axis]
            col_lo, col_hi = lo[col_axis], hi[col_axis]
            row_step = (row_hi - row_lo) / rows
            col_step = (col_hi - col_lo) / cols
            for r in range(rows):
                for c in range(cols):
                    cmin, cmax = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
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

                    quad = []
                    for r_end, c_end in ((0, 0), (0, 1), (1, 1), (1, 0)):
                        corner = [0.0, 0.0, 0.0]
                        corner[fixed_axis] = surface
                        corner[row_axis] = cmax[row_axis] if r_end else cmin[row_axis]
                        corner[col_axis] = cmax[col_axis] if c_end else cmin[col_axis]
                        quad.append(tuple(corner))
                    out.append((Gf.Range3d(Gf.Vec3d(*cmin), Gf.Vec3d(*cmax)), quad))
            return out, (rows, cols), (fixed_axis, outward,
                                       hi[fixed_axis] if outward > 0 else lo[fixed_axis],
                                       row_axis, col_axis)

        for face, args in ((FACE_RIGHT,   (side_axis, +1, up_axis, front_axis)),
                           (FACE_LEFT,    (side_axis, -1, up_axis, front_axis)),
                           (FACE_CEILING, (up_axis,   +1, front_axis, side_axis))):
            cells[face], shapes[face], faces[face] = make(*args)
        self._grid_shape = shapes
        self._face_planes = faces
        return cells

    def get_grid_shape(self) -> dict:
        return dict(self._grid_shape)

    def measure_faces(self, ebs_prim: Usd.Prim, cells: dict,
                      exclude: list = None, cache=None) -> dict:
        stage = self._get_stage()
        if stage is None:
            return {}
        bbox = self._ebs_bound(ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty() or not self._face_planes:
            return {}

        reach = max(local_box.GetMax()[i] - local_box.GetMin()[i]
                    for i in range(3)) * REACH_RATIO
        skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
        cache = cache if cache is not None else self._bounds_cache()

        # Every face's reach, worked out first, so the stage is walked once for
        # all of them rather than once each.
        wanted = {}
        for face, (axis, outward, coord, _, _) in self._face_planes.items():
            if any(cells.get(face, [])):
                continue                       # something is touching it already
            prism = self._face_prism(local_box, axis, outward, coord, reach)
            wanted[face] = (prism, Gf.BBox3d(prism, to_world).ComputeAlignedRange(),
                            axis, outward, coord)
        if not wanted:
            return {}

        whole = self._union([box for _, box, _, _, _ in wanted.values()])
        candidates, _ = self._gather_nearby(stage, cache, whole, skip)

        results = {}
        for face, (prism, world_prism, axis, outward, coord) in wanted.items():
            near = [(path, box) for path, box in candidates
                    if self._overlaps(box, world_prism)]
            found = self._nearest_in_prism(stage, near, prism, to_world,
                                           axis, outward, coord)
            results[face] = found or {"distance": None, "prim": "", "reach": reach}
        return results

    @staticmethod
    def _face_prism(box: Gf.Range3d, axis: int, outward: int, coord: float,
                    reach: float) -> Gf.Range3d:
        lo = [box.GetMin()[i] for i in range(3)]
        hi = [box.GetMax()[i] for i in range(3)]
        if outward > 0:
            lo[axis], hi[axis] = coord, coord + reach
        else:
            lo[axis], hi[axis] = coord - reach, coord
        return Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))

    def _nearest_in_prism(self, stage, candidates, prism, to_world,
                          axis, outward, coord):
        if not candidates:
            return None

        inverse = to_world.GetInverse()
        bounded = []
        for path, box in candidates:
            local = Gf.BBox3d(box, inverse).ComputeAlignedRange()
            gap = self._gap_along(local, axis, outward, coord)
            if gap is not None:
                bounded.append((gap, path, local))
        bounded.sort(key=lambda item: item[0])

        best, best_path, best_at = None, "", None
        for gap, path, local in bounded:
            if best is not None and gap >= best:
                break
            if self._precision != PRECISION_TRI:
                best, best_path = gap, path
                best_at = self._box_point(local, prism, axis, outward, coord, gap)
                continue
            triangles = self._mesh_triangles(stage, path)
            if not triangles:
                best, best_path = gap, path          # nothing to refine with
                best_at = self._box_point(local, prism, axis, outward, coord, gap)
                continue
            for triangle in triangles:
                local_tri = [inverse.Transform(Gf.Vec3d(*v)) for v in triangle]
                found = self._triangle_gap(local_tri, prism, axis, outward, coord)
                if found is not None and (best is None or found[0] < best):
                    best, best_path, best_at = found[0], path, found[1]
        if best is None:
            return None
        return {"distance": max(best, 0.0), "prim": best_path, "at": best_at}

    @staticmethod
    def _box_point(local, prism, axis: int, outward: int, coord: float, gap: float):
        """Where a prim judged by its box is taken to be: its near side along
        the axis, its middle across, pulled inside the prism so the line the
        overlay draws starts on the face it belongs to."""
        lo, hi = prism.GetMin(), prism.GetMax()
        point = [0.0, 0.0, 0.0]
        point[axis] = coord + (gap if outward > 0 else -gap)
        for i in range(3):
            if i == axis:
                continue
            middle = (local.GetMin()[i] + local.GetMax()[i]) * 0.5
            point[i] = min(max(middle, lo[i]), hi[i])
        return tuple(point)

    @staticmethod
    def _gap_along(box, axis: int, outward: int, coord: float) -> "float | None":
        if outward > 0:
            gap = box.GetMin()[axis] - coord
        else:
            gap = coord - box.GetMax()[axis]
        return None if gap < 0 else gap

    @staticmethod
    def _triangle_gap(triangle, prism, axis: int, outward: int, coord: float):
        """How far the nearest vertex sits off the face, and where to draw to.

        The number is the nearest vertex's, because the clearance is the
        smallest gap and a corner is where a triangle comes closest. Where the
        line is drawn is another question: hanging off a corner reads as
        arbitrary, so it goes to the middle of the triangle instead -- unless
        the middle falls outside the face's own footprint, and then the corner
        is all there is.
        """
        lo, hi = prism.GetMin(), prism.GetMax()
        best, at = None, None
        for vertex in triangle:
            inside = all(lo[i] - OVERLAP_EPS <= vertex[i] <= hi[i] + OVERLAP_EPS
                         for i in range(3) if i != axis)
            if not inside:
                continue
            gap = (vertex[axis] - coord) if outward > 0 else (coord - vertex[axis])
            if gap >= 0 and (best is None or gap < best):
                best, at = gap, vertex
        if best is None:
            return None
        middle = tuple(sum(v[i] for v in triangle) / 3.0 for i in range(3))
        if all(lo[i] - OVERLAP_EPS <= middle[i] <= hi[i] + OVERLAP_EPS
               for i in range(3) if i != axis):
            at = middle
        return best, at

    # -- collision markers ---------------------------------------------------

    def show_markers(self, ebs_prim: Usd.Prim, cells: dict,
                     marks: list = None) -> int:
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
            materials = {
                True: (self._marker_material(stage, "blocked", COLOR_BLOCKED,
                                             BLOCKED_OPACITY, BLOCKED_EMISSION),
                       COLOR_BLOCKED, BLOCKED_OPACITY),
                False: (self._marker_material(stage, "clear", COLOR_CLEAR),
                        COLOR_CLEAR, MARKER_OPACITY),
            }
            for face, boxes in built.items():
                flags = cells.get(face, [])
                for i, (_, quad) in enumerate(boxes):
                    material, colour, alpha = materials[
                        bool(i < len(flags) and flags[i])]
                    points = [to_world.Transform(Gf.Vec3d(*corner)) for corner in quad]
                    self._marker_sheet(stage, f"{MARKER_ROOT}/{face}_{i}", points,
                                       material, colour, alpha)
                    drawn += 1

            # The line from the face out to whatever is nearest. It is what
            # the clearance panel is a label for, so it is drawn here and
            # cleared with the sheets rather than kept on its own.
            radius = self._thread_radius()
            threads = {}
            for mark in marks or ():
                if not mark.get("from") or not mark.get("to"):
                    continue
                tight = mark.get("state") == STATE_TIGHT
                colour = COLOR_TIGHT if tight else COLOR_GAP
                if colour not in threads:
                    threads[colour] = self._marker_material(
                        stage, "tight" if tight else "gap", colour,
                        GAP_OPACITY, GAP_EMISSION)
                if self._gap_line(stage, f"{MARKER_ROOT}/{mark['face']}_gap",
                                  mark["from"], mark["to"], radius,
                                  threads[colour], colour):
                    drawn += 1
        print(f"[ebs] drew {drawn} collision markers under {MARKER_ROOT}")
        return drawn

    def _thread_radius(self) -> float:
        """How thick a drawn line is: the equipment's own diagonal, scaled.

        The port lasers and the clearance lines are the same kind of thing to
        look at, so they are the same thickness and it is worked out once.
        """
        box = self._world_range((self._target or {}).get("equipment"))
        if box is None:
            span = 1.0
        else:
            lo, hi = box.GetMin(), box.GetMax()
            span = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
        return max(span * LASER_RADIUS, 1e-5)

    @staticmethod
    def _gap_line(stage, path: str, start, end, radius: float, material,
                  colour=COLOR_GAP) -> bool:
        """A rod from one point to the other.

        A cylinder is built along Z and then turned onto the direction it has
        to run: the gap is measured perpendicular to a face, and a face can
        point any way the EBS does.
        """
        direction = Gf.Vec3d(*[end[i] - start[i] for i in range(3)])
        height = direction.GetLength()
        if height <= 1e-9:
            return False
        rod = UsdGeom.Cylinder.Define(stage, path)
        rod.CreateAxisAttr(UsdGeom.Tokens.z)
        rod.CreateHeightAttr(height)
        rod.CreateRadiusAttr(radius)
        rod.CreateExtentAttr(Vt.Vec3fArray([
            Gf.Vec3f(-radius, -radius, -height / 2.0),
            Gf.Vec3f(radius, radius, height / 2.0)]))
        rod.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*colour)]))
        matrix = Gf.Matrix4d(1.0)
        # SetRotate clears the translation, so the turn goes on first.
        matrix.SetRotate(Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0),
                                     direction.GetNormalized()))
        matrix.SetTranslateOnly(
            Gf.Vec3d(*[(start[i] + end[i]) * 0.5 for i in range(3)]))
        UsdGeom.Xformable(rod).AddTransformOp().Set(matrix)
        try:
            rod.GetPrim().CreateAttribute(
                "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        except Exception:
            pass
        if material:
            UsdShade.MaterialBindingAPI(rod.GetPrim()).Bind(material)
        return True

    def show_port_lasers(self, points: dict = None) -> int:
        stage = self._get_stage()
        if stage is None:
            return 0
        self.clear_port_lasers()

        points = self._port_world if points is None else points
        if not points:
            return 0

        top = self._port_rail_z
        radius = self._thread_radius()

        drawn = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, LASER_ROOT)
            for index in sorted(points):
                colour = LASER_COLOR_0 if index == 0 else LASER_COLOR
                spot = points[index]
                bottom = spot[2]             # where the EBS sits
                height = max(abs(top - bottom), 1e-3)
                self._laser_cylinder(stage, f"{LASER_ROOT}/port_{index}",
                                     Gf.Vec3d(spot[0], spot[1], (top + bottom) / 2.0),
                                     radius, height, colour)
                drawn += 1
        print(f"[ebs] drew {drawn} port lasers under {LASER_ROOT}, "
              f"radius {radius:.4f}, rail z {top:.4f} down to the EBS z")
        return drawn

    def show_sweep(self, spots: dict) -> int:
        stage = self._get_stage()
        if stage is None:
            return 0
        self.clear_sweep()
        if not spots:
            return 0

        radius = 0.0
        for name in spots:
            box = self._world_range(stage.GetPrimAtPath(self._eqp_index.get(
                EQP_PREFIX + name, "")))
            if box is not None:
                lo, hi = box.GetMin(), box.GetMax()
                radius = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
                radius *= LASER_RADIUS
                break
        radius = max(radius, 1e-5)

        drawn, refused = 0, []
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, SWEEP_ROOT)
            for name, (port, here) in spots.items():
                top, bottom = port[2], here[2]      # rail down to the equipment
                height = max(abs(top - bottom), 1e-3)
                middle = (top + bottom) / 2.0
                stem = self._prim_name(name)
                try:
                    self._laser_cylinder(stage, f"{SWEEP_ROOT}/{stem}_port",
                                         Gf.Vec3d(port[0], port[1], middle),
                                         radius, height, SWEEP_COLOR_PORT)
                    self._laser_cylinder(stage, f"{SWEEP_ROOT}/{stem}_eqp",
                                         Gf.Vec3d(here[0], here[1], middle),
                                         radius, height, SWEEP_COLOR_EQP)
                except Exception as e:
                    refused.append(f"{name}: {e}")
                    continue
                drawn += 1
        print(f"[ebs] drew {drawn} pairs under {SWEEP_ROOT}, radius {radius:.4f}")
        if refused:
            self._note(f"{len(refused)} could not be drawn: "
                       + ", ".join(refused[:4]))
        return drawn

    @staticmethod
    def _prim_name(text: str) -> str:
        cleaned = "".join(c if c.isalnum() or c == "_" else "_" for c in text)
        return cleaned if cleaned[:1].isalpha() or cleaned[:1] == "_" else "_" + cleaned

    def clear_sweep(self) -> None:
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(SWEEP_ROOT).IsValid():
                stage.RemovePrim(SWEEP_ROOT)

    def clear_port_lasers(self) -> None:
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(LASER_ROOT).IsValid():
                stage.RemovePrim(LASER_ROOT)

    @staticmethod
    def _laser_cylinder(stage, path: str, centre, radius: float, height: float,
                        colour) -> None:
        cylinder = UsdGeom.Cylinder.Define(stage, path)
        cylinder.CreateAxisAttr(UsdGeom.Tokens.z)
        cylinder.CreateHeightAttr(height)
        cylinder.CreateRadiusAttr(radius)
        cylinder.CreateExtentAttr(Vt.Vec3fArray([
            Gf.Vec3f(-radius, -radius, -height / 2.0),
            Gf.Vec3f(radius, radius, height / 2.0)]))
        cylinder.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*colour)]))
        cylinder.AddTranslateOp().Set(Gf.Vec3d(centre[0], centre[1], centre[2]))

    def clear_markers(self) -> None:
        self._verdict = {}          # the overlay reads this; it goes with them
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(MARKER_ROOT).IsValid():
                stage.RemovePrim(MARKER_ROOT)

    @staticmethod
    def _face_normal(points: list) -> tuple:
        a, b, c = points[0], points[1], points[2]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(sum(value * value for value in n))
        return tuple(value / length for value in n) if length else (0.0, 0.0, 1.0)

    @classmethod
    def _marker_sheet(cls, stage, path: str, points: list, material, color,
                      opacity: float = MARKER_OPACITY) -> None:
        normal = cls._face_normal(points)
        diagonal = math.sqrt(sum((points[2][i] - points[0][i]) ** 2
                                 for i in range(3)))
        gap = max(diagonal * SHEET_GAP, 1e-9)
        behind = [tuple(corner[i] - normal[i] * gap for i in range(3))
                  for corner in points]
        cls._marker_quad(stage, path, points, material, color, opacity)
        cls._marker_quad(stage, path + "_back", behind, material, color, opacity,
                         flip=True)

    @staticmethod
    def _marker_quad(stage, path: str, points: list, material, color,
                     opacity: float = MARKER_OPACITY, flip: bool = False) -> None:
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4]))
        mesh.CreateFaceVertexIndicesAttr(
            Vt.IntArray([3, 2, 1, 0] if flip else [0, 1, 2, 3]))
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
        mesh.CreateDisplayOpacityAttr(Vt.FloatArray([opacity]))
        try:
            mesh.GetPrim().CreateAttribute(
                "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        except Exception:
            pass
        if material:
            UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(material)

    @classmethod
    def _marker_material(cls, stage, name: str, color, opacity: float = MARKER_OPACITY,
                         emission: float = MARKER_EMISSION):
        path = f"{MARKER_ROOT}/Looks/{name}"
        material = UsdShade.Material.Define(stage, path)
        cls._preview_shader(stage, material, path, color, opacity)
        cls._mdl_shader(stage, material, path, color, opacity, emission)
        return material

    @staticmethod
    def _preview_shader(stage, material, path: str, color, opacity: float) -> None:
        shader = UsdShade.Shader.Define(stage, path + "/shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(0.0, 0.0, 0.0))
        shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*color))
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(0.0, 0.0, 0.0))
        shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),
                                                       "surface")

    @staticmethod
    def _mdl_shader(stage, material, path: str, color, opacity: float,
                    emission: float) -> None:
        shader = UsdShade.Shader.Define(stage, path + "/mdl")
        shader.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
        shader.SetSourceAssetSubIdentifier("OmniPBR", "mdl")

        def put(name, type_name, value):
            shader.CreateInput(name, type_name).Set(value)

        put("diffuse_color_constant", Sdf.ValueTypeNames.Color3f,
            Gf.Vec3f(0.0, 0.0, 0.0))
        put("emissive_color", Sdf.ValueTypeNames.Color3f, Gf.Vec3f(*color))
        put("emissive_intensity", Sdf.ValueTypeNames.Float, emission)
        put("enable_emission", Sdf.ValueTypeNames.Bool, True)
        put("enable_opacity", Sdf.ValueTypeNames.Bool, True)
        put("opacity_constant", Sdf.ValueTypeNames.Float, opacity)
        put("reflection_roughness_constant", Sdf.ValueTypeNames.Float, 1.0)
        put("metallic_constant", Sdf.ValueTypeNames.Float, 0.0)
        put("specular_level", Sdf.ValueTypeNames.Float, 0.0)
        material.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out")

    # -- camera --------------------------------------------------------------

    def make_camera(self) -> bool:
        stage = self._get_stage()
        if stage is None:
            return False
        # Not release_camera: that also puts the hidden machines back, and the
        # camera step turns them off just before asking for the camera.
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

        self._note(f"camera {CAMERA_PATH} created (the viewport switches to it "
                   f"when the camera step runs)")
        return True

    def release_camera(self) -> None:
        self.show_equipment()          # the view goes back with the camera
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
        stage = self._get_stage()
        viewport = self._viewport()
        if stage is None or viewport is None:
            return False
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return False
        # Clear takes the camera away with it, and pressing Camera again is a
        # perfectly ordinary thing to do, so build another one. Asking whether
        # the prim is valid is not the question: Kit writes its own camera
        # state at that path while the viewport is still pointed at it, so what
        # is left behind is a prim with nothing but an over on it. That is
        # valid, and it is not a camera -- which is the empty typename the
        # clipping range came back with. Ask for the camera itself instead.
        cam_prim = stage.GetPrimAtPath(CAMERA_PATH)
        camera = UsdGeom.Camera(cam_prim) if cam_prim.IsValid() else None
        if not camera:
            self.make_camera()      # Define types the leftover over back up
            cam_prim = stage.GetPrimAtPath(CAMERA_PATH)
            camera = UsdGeom.Camera(cam_prim) if cam_prim.IsValid() else None
            if not camera:
                print("[ebs] could not create the camera")
                return False
        if str(viewport.camera_path) != CAMERA_PATH:
            self._previous_camera = str(viewport.camera_path)
            try:
                viewport.camera_path = CAMERA_PATH
            except Exception as e:
                print(f"[ebs] could not switch the viewport camera: {e}")

        facing = facing if (facing is not None and facing.IsValid()) else prim
        tc = Usd.TimeCode.Default()
        rot = UsdGeom.Xformable(facing).ComputeLocalToWorldTransform(
            tc).ExtractRotationMatrix()
        up_row = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
        view = Gf.Vec3d(rot[1][0], rot[1][1], rot[1][2]).GetNormalized()
        up = Gf.Vec3d(rot[up_row][0], rot[up_row][1], rot[up_row][2]).GetNormalized()

        z_cam = -view
        x_cam = Gf.Cross(up, z_cam).GetNormalized()
        y_cam = Gf.Cross(z_cam, x_cam).GetNormalized()

        framed = self._world_range(prim)
        corners = self._box_corners(framed)
        if not corners:
            return False
        centre = Gf.Vec3d(*[(framed.GetMin()[i] + framed.GetMax()[i]) * 0.5
                            for i in range(3)])

        distance = self._fit_distance(cam_prim, viewport, corners, centre,
                                      x_cam, y_cam, z_cam)
        eye = centre + z_cam * distance
        matrix = Gf.Matrix4d(
            x_cam[0], x_cam[1], x_cam[2], 0.0,
            y_cam[0], y_cam[1], y_cam[2], 0.0,
            z_cam[0], z_cam[1], z_cam[2], 0.0,
            eye[0],   eye[1],   eye[2],   1.0,
        )

        # Nothing is cut out of the view. The slab that used to be carved in
        # front of the camera took the plant apart when you orbited, and the
        # machines being turned off is what clears the way now.
        near, far = CAMERA_NEAR, CAMERA_FAR

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
            camera.CreateClippingRangeAttr().Set(
                Gf.Vec2f(float(near), float(far)))
            coi = cam_prim.GetAttribute("omni:kit:centerOfInterest")
            if coi and coi.IsValid():
                coi.Set(Gf.Vec3d(0.0, 0.0, -distance))   # orbit around the target
        self._note(f"camera at {distance:.2f} from the target, "
                   f"near plane {near:.2f}, nothing culled")
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
                 equipment=None, eqp_id: str = "", port_count=None,
                 distances: dict = None, rows: list = None,
                 equipment_hit: dict = None) -> dict:
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
            "distances": distances or {},
            "rows": rows or [],              # a sweep's table, for whoever writes it out
            "equipment_hit": equipment_hit or {"hit": False, "pairs": [], "tests": 0},
            "timings": list(self._timings),
            "notes": list(self._notes),
            "total_ms": (time.perf_counter() - self._started) * 1000.0,
        }
        return dict(self._result)
