import array
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

from .ebs_simulate_camera import EbsSimulateCamera, CAMERA_PATH

__all__ = ["EbsSimulate"]

EQP_PREFIX = "EQP_"
PORT_ID_KEY = "port-id"
OFFSET_KEY  = "offset"
CADX_KEY    = "cad-x"
CADY_KEY    = "cad-y"
NEXT_KEY    = "next-address"
PULS_KEY    = "distance-puls"
ADDR_PATTERN = re.compile(r"^addr0*(\d+)$", re.IGNORECASE)
PORT_PATTERN = re.compile(r"^([A-Za-z0-9]+)_(\d+)$")
CACHE_SUFFIX  = ".ebscache.json"
CACHE_VERSION = 1
READ_BLOCK    = 8 << 20
CAD_PER_UNIT    = 100.0 / 3.0
CAD_SLACK       = 0.1             # 비유효축 허용 유격 (100/3이 안 나눠떨어짐)
OFFSET_PER_UNIT = 100000.0
RAIL_PREFIX = "rail_"

SCALE_FIXED = "fixed"
SCALE_PULS  = "puls"
SCALE_SNAP  = "snap"
SCALE_MODES = (SCALE_FIXED, SCALE_PULS, SCALE_SNAP)


def _remote(path: str) -> bool:
    head = path.split("://", 1)
    return len(head) == 2 and head[0].isalpha()


def _client():
    import omni.client
    return omni.client


def _stamp_of(path: str) -> list:
    if _remote(path):
        client = _client()
        result, entry = client.stat(path)
        if result != client.Result.OK:
            raise OSError(f"{result} on {path}")
        when = getattr(entry, "modified_time", None)
        moment = int(when.timestamp() * 1e9) if when is not None else 0
        return [CACHE_VERSION, int(entry.size), moment]
    stat = os.stat(path)
    return [CACHE_VERSION, stat.st_size, stat.st_mtime_ns]


def _read_bytes(path: str) -> bytes:
    if _remote(path):
        client = _client()
        result, _, content = client.read_file(path)
        if result != client.Result.OK:
            raise OSError(f"{result} on {path}")
        return memoryview(content).tobytes()
    with open(path, "rb") as handle:
        return handle.read()


def _write_text(path: str, text: str) -> None:
    if _remote(path):
        client = _client()
        result = client.write_file(path, text.encode("utf-8"))
        if result != client.Result.OK:
            raise OSError(f"{result} on {path}")
        return
    spare = path + ".part"
    try:
        with open(spare, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(spare, path)
    except Exception:
        try:
            os.remove(spare)
        except OSError:
            pass
        raise


def _plain(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _as_float(text) -> "float | None":
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


class _PortScan:
    def __init__(self):
        self.addr_cad = {}
        self.addr_next = {}
        self.found = {}
        self._groups = []
        self._addrs = []
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
            if written:
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


try:
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

VERDICT_HEIGHT = 0.8      # 판정 패널 높이. EBS 바닥 0, 천장 1
NEIGHBOUR_REACH = 1.5
GROUP_NAMES = ("AMH", "Construction")

STATE_CLASH = "clash"     # 면이 막혔다. 거리는 없다
STATE_TIGHT = "tight"     # 닿지는 않았는데 최소 여유보다 가깝다. 간섭
STATE_CLEAR = "clear"     # 비었다. distance None 이면 reach 안에 아무것도 없음

# 최소 여유 기본값, m. set_min_gaps 로 바꾼다
MIN_GAP_CEILING = 0.1
MIN_GAP_SIDE = 0.6


MARKER_ROOT    = "/EbsCollisionMarkers"
MARKER_OPACITY = 0.075
COLOR_BLOCKED  = (0.9, 0.2, 0.2)
BLOCKED_OPACITY = 0.6
BLOCKED_EMISSION = 1000.0
COLOR_CLEAR    = (1.0, 1.0, 1.0)
MARKER_EMISSION = 10000.0  # 마커 발광 세기
COLOR_GAP      = (0.9, 0.7, 0.0)   # 여유를 재는 선. 패널과 같은 짙은 황색
COLOR_TIGHT    = (0.9, 0.15, 0.15)  # 최소 여유보다 가까울 때. 간섭
GAP_OPACITY    = 1.0
GAP_EMISSION   = 3000.0
SHEET_GAP      = 0.001    # 뒷면이 앞면에서 떨어지는 거리 (대각선 대비)

LASER_ROOT     = "/EbsPortLasers"
LASER_COLOR    = (1.0, 0.05, 0.05)
LASER_COLOR_0  = (1.0, 0.75, 0.0)
LASER_RADIUS   = 0.0013

SWEEP_ROOT     = "/EbsPortSweep"
SWEEP_COLOR_PORT = LASER_COLOR
SWEEP_COLOR_EQP  = (0.15, 0.8, 0.3)

OURS = (MARKER_ROOT, LASER_ROOT, SWEEP_ROOT, CAMERA_PATH)
OURS_UNDER = tuple(p + "/" for p in OURS)
NOW = Usd.TimeCode.Default()

FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)

GRID = 1                 # 면당 셀 분할 수. 1이면 면 하나가 셀 하나
# 카메라 단계에서 양옆 빼고 나머지 장비를 투명하게. 느려서 기본은 꺼 둔다 --
# 켜려면 여기를 True 로. 되돌리기는 release_camera / teardown 이 한다
FADE_OTHERS = False
LOOKS = "Looks"
SHADER_TYPE = "Shader"
GONE_THRESHOLD = 0.5     # 0 이면 blend 라 안 사라진다. 컷아웃 문턱값
GONE_LAYER = "ebs_hidden.usda"
GONE = (("inputs:opacity", "Float", 0.0),
        ("inputs:opacityThreshold", "Float", GONE_THRESHOLD),
        ("inputs:enable_opacity", "Bool", True),
        ("inputs:opacity_constant", "Float", 0.0),
        ("inputs:opacity_threshold", "Float", GONE_THRESHOLD))

GRID_CELLS = 24
OVERLAP_EPS = 1e-6
PROBE_RATIO = 0.01
REACH_RATIO = 1.5        # 거리를 재는 범위 (EBS 최장변 대비). 넘으면 거리 없음
PRECISION_BBOX = "bbox"
PRECISION_MESH = "mesh"
PRECISION_TRI  = "triangle"

PRUNE_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves", "Capsule", "Cone", "Cube",
    "Cylinder", "Sphere", "Plane", "GeomSubset",
    "Material", "Shader", "NodeGraph", "Camera",
})
ANCHOR_DEPTH = 6
PASS_TYPES  = ("Scope",)
MIN_PORTS = 2
MAX_PORTS = 3

PIVOT_TOLERANCE = 1.0    # 포트 1에서 이만큼 넘게 떨어지면 피봇이 아님
PIVOT_ACROSS = 0.5


class EbsSimulate:
    def __init__(self):
        self._xml_path: str = ""
        self._usd_path: str = ""
        self._ebs_path_2port: str = ""
        self._ebs_path_3port: str = ""
        self._search_root: str = ""
        self._eqp_index: dict = {}
        self._port_map: dict = {}
        self._port_offsets: dict = {}
        self._port_addr: dict = {}
        self._port_addr_of: dict = {}
        self._addr_cad: dict = {}
        self._addr_next: dict = {}
        self._offset_scale: str = SCALE_SNAP
        self._rail_root: str = ""
        self._rail_index: dict = None
        self._rail_frame = None
        self._triangles: dict = {}
        self._eqp_boxes: dict = None
        self._bounds = None
        self._stage_index = None
        self._ebs_box = None
        self._lasers: bool = False
        self._verdict: dict = {}
        self._min_gap = {FACE_CEILING: MIN_GAP_CEILING,
                         FACE_LEFT: MIN_GAP_SIDE,
                         FACE_RIGHT: MIN_GAP_SIDE}   # 면 -> 최소 여유, m
        self._blockers: dict = {}
        self._local: dict = {}
        self._faces: dict = {}    # 메시별 면 상자 + 로컬 격자
        self._hidden: list = []
        self._eqp_looks: dict = {}
        self._eqp_shared: set = set()
        self._gone = None
        self._boxed: dict = {}
        self._visible: dict = {}
        self._grid_shape: dict = {}
        self._port_world: dict = {}
        self._port_rail_z: float = 0.0
        self._face_planes: dict = {}
        self._camera = EbsSimulateCamera()
        self._precision: str = PRECISION_TRI
        self._timings: list = []
        self._notes: list = []
        self._blocked: str = ""
        self._why: str = ""
        self._started: float = 0.0
        self._ready: bool = False
        self._target: dict = None
        self._aligned: bool = False
        self._result: dict = {}


    def set_usd_path(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._usd_path:
            self._ready = False
        self._usd_path = path

    def open_stage(self) -> bool:
        if not self._usd_path:
            return True
        context = omni.usd.get_context()
        now = ""
        try:
            now = str(context.get_stage_url() or "")
        except Exception:
            pass
        if now and now.rstrip("/") == self._usd_path.rstrip("/"):
            self._note(f"stage already open: {now}")
            return True
        with self._stage_timer("USD: open"):
            try:
                told = context.open_stage(self._usd_path)
            except Exception as e:
                self._note(f"could not open {self._usd_path}: {e}")
                return False
        ok = told[0] if isinstance(told, tuple) else told
        if ok is False:
            self._note(f"could not open {self._usd_path}: "
                       + (str(told[1]) if isinstance(told, tuple) and len(told) > 1
                          else "the stage did not open"))
            return False
        self._note(f"stage opened: {self._usd_path}")
        return True

    def set_xml_path(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._xml_path:
            self._port_map = {}
            self._ready = False
        self._xml_path = path

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def hide_ebs(self) -> int:
        return self._show_ebs([self._ebs_path_2port, self._ebs_path_3port], False)

    def show_ebs(self, prim) -> int:
        return self._show_ebs([prim], True)

    def _show_ebs(self, wanted: list, visible: bool) -> int:
        stage = self._get_stage()
        if stage is None:
            return 0
        shown = UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible
        done, touched = 0, []
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for one in wanted:
                    if isinstance(one, str):
                        if not one:
                            continue
                        one = stage.GetPrimAtPath(one)
                    prim = one
                    if prim is None or not prim.IsValid():
                        continue
                    imageable = UsdGeom.Imageable(prim)
                    if imageable:
                        imageable.CreateVisibilityAttr().Set(shown)
                        touched.append(str(prim.GetPath()))
                        done += 1
        except Exception as e:
            self._note(f"could not set the EBS visibility ({e})")
        self._forget_ebs(touched)
        return done

    def set_min_gaps(self, side: float, ceiling: float) -> None:
        self._min_gap = {FACE_CEILING: max(0.0, float(ceiling)),
                         FACE_LEFT: max(0.0, float(side)),
                         FACE_RIGHT: max(0.0, float(side))}

    @staticmethod
    def _probe_depth(box: Gf.Range3d) -> float:
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
        self._lasers = bool(on)

    def set_rail_root(self, path: str) -> None:
        self._rail_index = None
        self._rail_root = (path or "").strip()

    def set_search_root(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._search_root:
            self._eqp_index = {}
            self._ready = False
        self._search_root = path

    def get_result(self) -> dict:
        return dict(self._result)

    def get_timings(self) -> list:
        return [list(t) for t in self._timings]

    def teardown(self) -> None:
        self.show_equipment()
        self._camera.remove(self._get_stage())
        self.clear_markers()
        self.clear_port_lasers()
        self.clear_sweep()
        self.hide_ebs()
        self._eqp_index = {}
        self._port_map = {}
        self._triangles = {}
        self._local = {}
        self._faces = {}
        self._visible = {}
        self._timings = []
        self._ready = False
        self._target = None
        self._aligned = False
        self._result = {}


    def _begin(self) -> None:
        self._boxed = {}
        self._timings = []
        self._notes = []
        self._blocked = ""
        self._started = time.perf_counter()

    def _report_stages(self, title: str, mark: int = 0) -> None:
        stages = {}
        for label, spent in self._timings[mark:]:
            stage, _, kind = label.partition(":")
            stage, kind = stage.strip(), kind.strip() or "took"
            stages.setdefault(stage, {})
            stages[stage][kind] = stages[stage].get(kind, 0.0) + spent
        if not stages:
            return
        total = sum(sum(k.values()) for k in stages.values())
        print(f"[ebs] {title}: {total:.1f} ms")
        for stage, kinds in stages.items():
            parts = "  ".join(f"{kind} {spent:8.1f} ms"
                              for kind, spent in kinds.items())
            print(f"[ebs]   {stage:<10} {parts}")

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


    def init(self) -> dict:
        self._begin()
        self._eqp_boxes = None
        self._eqp_looks = {}
        self._eqp_shared = set()
        if self._gone is not None:
            self._gone.Clear()
        self._hidden = []
        self._bounds = None
        self._stage_index = None
        self._ebs_box = None
        self._ready = False
        self._target = None
        self._aligned = False
        self._verdict = {}
        self._triangles = {}
        self._local = {}
        self._faces = {}
        if not self.open_stage():
            return self._payload(False, f"Could not open {self._usd_path}")
        if self._get_stage() is None:
            return self._payload(False, "No stage open - give a USD path")
        if self._camera.make(self._get_stage()):
            self._note(f"camera {CAMERA_PATH} created (the viewport switches "
                       f"to it when the camera step runs)")

        self.hide_ebs()
        equipment = self.build_index()
        self._stage_boxes()
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

    def align(self, equipment: str = "") -> dict:
        self._begin()
        if not self._ready:
            return self._payload(False, "Run Init first")
        made = self._do_prepare(equipment)
        if not made["ok"]:
            return made
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
        parents = {}
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

        self._blocked = ""
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
        across = (-along[1], along[0])

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
        result = self._do_collide()
        if not result["ok"]:
            return result
        told = self._do_focus()
        if not told["ok"]:
            return told
        result["timings"] = list(self._timings)
        result["notes"] = list(self._notes)
        result["total_ms"] = (time.perf_counter() - self._started) * 1000.0
        return result


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
        if FADE_OTHERS:
            with self._stage_timer("hide the other equipment"):
                found = self.side_band(stage, self._target["ebs"],
                                       self._target["equipment"])
                beside = found.get("beside", [])
                hidden = self.hide_other_equipment(
                    [str(self._target["equipment"].GetPath())] + beside)
            self._note(f"kept {1 + len(beside)}, {hidden} made see-through")
        ebs = self._target["ebs"]
        anchor = self._target["anchor"]
        facing = anchor if (anchor is not None and anchor.IsValid()) else ebs
        with self._stage_timer("camera focus"):
            told = self._camera.place(stage, self._world_range(ebs), facing)
        if told:
            self._note(told)
        return self._payload(bool(told), "Camera on the EBS" if told
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

        self._forget_triangles(self._target["ebs"])
        self._ebs_box = None

        self.show_ebs(self._target["ebs"])

        if self._lasers:
            with self._stage_timer("draw port lasers"):
                drawn = self.show_port_lasers()
            if drawn:
                self._note(f"{drawn} port laser(s) drawn under {LASER_ROOT}")
        else:
            self.clear_port_lasers()
        return self._payload(self._aligned, note if self._aligned else "EBS alignment failed")

    def _side_roots(self) -> list:
        stage = self._get_stage()
        if stage is None:
            return []
        found = self.side_band(stage, self._target["ebs"],
                               self._target["equipment"])
        beside = found.get("beside", []) if found else []
        roots = []
        for path in beside:
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                roots.append(prim)
        self._note("left and right judged against "
                   + (", ".join(str(p).rsplit("/", 1)[-1] for p in beside)
                      if beside else "nothing -- no machine beside this one")
                   + "; the ceiling still walks the stage")
        return roots

    def _do_collide(self) -> dict:
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        if not self._aligned:
            return self._payload(False, "Run Align first")

        mark = len(self._timings)
        apart = [self._target["ebs"], self._target["equipment"]]
        bounds = self._bounds_cache()
        roots = self._side_roots()
        cells = self.check_collision(self._target["ebs"], exclude=apart,
                                     cache=bounds, roots=roots)
        hit_count = sum(sum(1 for c in v if c) for v in cells.values())
        distances = self.measure_faces(self._target["ebs"], cells,
                                       exclude=apart, cache=bounds, roots=roots)
        for face, found in distances.items():
            if found.get("distance") is None:
                self._note(f"{face}: clear, nothing within "
                           f"{found.get('reach', 0):.3f}")
            else:
                self._note(f"{face}: clear, nearest {found['distance']:.4f} away "
                           f"({found['prim'].rsplit('/', 1)[-1]})")

        try:
            meeting = self.check_equipment(self._target["ebs"],
                                           self._target["equipment"],
                                           cache=bounds)
        except Exception as e:
            meeting = {"hit": False, "pairs": [], "tests": 0}
            self._note(f"interference check failed: {type(e).__name__}: {e}")
        if meeting["hit"]:
            a, b = meeting["pairs"][0]
            self._note(f"the EBS runs through the equipment: "
                       f"{a.rsplit('/', 1)[-1]} x {b.rsplit('/', 1)[-1]} "
                       f"(stopped there, {meeting['tests']} pairs tested)")
        else:
            self._note(f"clear of the equipment itself "
                       f"({meeting['tests']} triangle pairs tested)")

        for why, paths in sorted(self._boxed.items()):
            self._note(f"{len(paths)} judged by box, {why}: "
                       + ", ".join(sorted(p.rsplit("/", 1)[-1] for p in paths)[:4])
                       + (" ..." if len(paths) > 4 else ""))

        with self._stage_timer("verdict: build"):
            try:
                verdict = self.build_verdict(self._target["ebs"], cells,
                                             distances, meeting["hit"])
            except Exception as e:
                verdict = {}
                self._note(f"no overlay verdict: {type(e).__name__}: {e}")

        with self._stage_timer("markers: draw"):
            self.show_markers(self._target["ebs"], cells,
                              verdict.get("marks"))
        self._verdict = verdict

        self._report_stages("collide", mark)

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
        bbox = self._ebs_bound(ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty():
            return {}
        lo, hi = local_box.GetMin(), local_box.GetMax()
        spot = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        up_axis = (self._face_planes.get(FACE_CEILING) or (2,))[0]
        spot[up_axis] = lo[up_axis] + (hi[up_axis] - lo[up_axis]) * VERDICT_HEIGHT
        middle = to_world.Transform(Gf.Vec3d(*spot))
        marks = self._face_marks(local_box, to_world, cells, distances)
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
            if at is None:
                blank["state"] = STATE_CLEAR
                marks.append(blank)
                continue
            reach = found.get("distance", 0.0)
            start, end = list(at), list(at)
            start[axis] = coord
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


    def build_index(self) -> int:
        stage = self._get_stage()
        self._eqp_index = {}
        self._rail_index = None
        self._triangles = {}
        self._local = {}
        self._faces = {}
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
                continue
            type_name = prim.GetTypeName()
            if type_name in PRUNE_TYPES or type_name.endswith("Light"):
                continue
            stack.extend(_children(prim))

    def equipment_boxes(self, stage) -> dict:
        if self._eqp_boxes is not None:
            return self._eqp_boxes
        with self._stage_timer(f"measure {len(self._eqp_index)} equipment"):
            by_path = {path: box
                       for path, _, _, box, _, _ in self._stage_boxes()}
            boxes = {name: by_path[path]
                     for name, path in self._eqp_index.items() if path in by_path}
        self._eqp_boxes = boxes
        return boxes

    def hide_other_equipment(self, keep: list) -> int:
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
        if not self._hidden:
            return
        stage = self._get_stage()
        if stage is not None:
            self._author_opacity(stage, self._hidden, False)
        self._hidden = []

    def _looks_shaders(self, stage, path: str) -> list:
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
                    stack.extend(_children(prim))
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
        session = stage.GetSessionLayer()
        if self._gone is None:
            self._gone = Sdf.Layer.CreateAnonymous(GONE_LAYER)
        if self._gone.identifier not in session.subLayerPaths:
            session.subLayerPaths.insert(0, self._gone.identifier)
        return self._gone

    def _author_opacity(self, stage, paths: list, hide: bool) -> bool:
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

    @staticmethod
    def _cast(box, way) -> tuple:
        lo, hi = box.GetMin(), box.GetMax()
        centre = (lo[0] + hi[0]) * 0.5 * way[0] + (lo[1] + hi[1]) * 0.5 * way[1]
        half = (abs(way[0]) * (hi[0] - lo[0]) + abs(way[1]) * (hi[1] - lo[1])) * 0.5
        return (centre - half, centre + half)

    def side_neighbours(self, stage, ebs_prim, eqp_prim) -> list:
        found = self.side_band(stage, ebs_prim, eqp_prim)
        return found["beside"] if found else []

    def side_band(self, stage, ebs_prim, eqp_prim) -> dict:
        boxes = self.equipment_boxes(stage)
        mine = self._equipment_id(eqp_prim)
        key = next((name for name in self._eqp_index
                    if name[len(EQP_PREFIX):] == mine or name == mine), None)
        here = boxes.get(key) or self._world_range(eqp_prim)
        if here is None or here.IsEmpty():
            return {}

        sideways = self._sideways(ebs_prim)
        inward = (-sideways[1], sideways[0])
        my_side = self._cast(here, sideways)
        my_deep = self._cast(here, inward)
        reach = (my_side[1] - my_side[0]) * NEIGHBOUR_REACH
        if reach <= 0:
            return {}

        left = right = None
        for name, box in boxes.items():
            if name == key:
                continue
            deep = self._cast(box, inward)
            if deep[0] >= my_deep[1] or deep[1] <= my_deep[0]:
                continue
            side = self._cast(box, sideways)
            if side[0] >= my_side[1]:
                gap, hand = side[0] - my_side[1], "right"
            elif side[1] <= my_side[0]:
                gap, hand = my_side[0] - side[1], "left"
            else:
                continue
            if gap > reach:
                continue
            if hand == "right":
                if right is None or gap < right[0]:
                    right = (gap, name)
            elif left is None or gap < left[0]:
                left = (gap, name)

        return {
            "beside": [self._eqp_index[one[1]] for one in (left, right) if one],
            "sideways": sideways, "inward": inward,
            "side": (my_side[0] - reach, my_side[1] + reach),
            "deep": my_deep,
        }

    def _sideways(self, ebs_prim) -> tuple:
        try:
            row = self._ebs_bound(ebs_prim).GetMatrix().GetRow(0)
            length = math.sqrt(row[0] ** 2 + row[1] ** 2)
            if length > 1e-9:
                return (row[0] / length, row[1] / length)
        except Exception:
            pass
        return (1.0, 0.0)

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
                current = first
                continue
            current, level = first, level + 1
        return current, True


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
        parser = expat.ParserCreate()
        parser.buffer_text = True
        parser.StartElementHandler = scan.start
        parser.EndElementHandler = scan.end
        parser.CharacterDataHandler = scan.data

        reads = 0
        if _remote(self._xml_path):
            whole = _read_bytes(self._xml_path)
            for at in range(0, len(whole) or 1, READ_BLOCK):
                parser.Parse(whole[at:at + READ_BLOCK], False)
                reads += 1
        else:
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


    def _cache_path(self) -> str:
        return self._xml_path + CACHE_SUFFIX

    def _source_stamp(self) -> list:
        return _stamp_of(self._xml_path)

    def _load_cache(self) -> bool:
        path = self._cache_path()
        try:
            want = self._source_stamp()
        except OSError as e:
            self._note(f"xml not readable: {e}")
            return False
        with self._stage_timer("XML: cache read"):
            try:
                blob = json.loads(_read_bytes(path).decode("utf-8"))
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
                text = json.dumps(blob)
                _write_text(path, text)
            except Exception as e:
                self._note(f"xml cache not written: {e}")
                return
        self._note(f"xml cache written: {path} "
                   f"({len(text.encode('utf-8')) / 1048576:.2f} MB)")

    def get_port_count(self, eqp_id: str) -> "int | None":
        indices = self.get_port_indices(eqp_id)
        return len(indices) if indices else None

    def get_port_indices(self, eqp_id: str) -> list:
        return list(self._port_map.get(eqp_id.upper(), []))


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

        length = span[axis] / CAD_PER_UNIT
        direction = 1.0 if length >= 0 else -1.0

        rail_local = self._local_translation(rail)
        start = rail_local[axis] - length / 2.0
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
        offset_zero = offsets[1] + spacing

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
            gap = (cad[axis] - base_cad[axis]) / CAD_PER_UNIT
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
            self._port_rail_z = spot[2]
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
        a, b, c = (axes[ch] for ch in order)
        m = [[rotation[i][j] for j in range(3)] for i in range(3)]

        p = [a, b, c]
        sign = 1.0 if order in ("XYZ", "YZX", "ZXY") else -1.0
        r = [[m[p[i]][p[j]] for j in range(3)] for i in range(3)]

        beta = math.asin(max(-1.0, min(1.0, -sign * r[0][2])))
        if abs(math.cos(beta)) < 1e-9:
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


    def _bounds_cache(self):
        if self._bounds is None:
            self._bounds = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
                useExtentsHint=True,
            )
        return self._bounds

    def check_collision(self, ebs_prim: Usd.Prim, exclude: list = None,
                        cache=None, roots: list = None) -> dict:
        stage = self._get_stage()
        if stage is None:
            return {face: [] for face in FACES}
        self._visible = {}
        cache = cache if cache is not None else self._bounds_cache()

        with self._stage_timer("faces: search"):
            ebs_bbox = self._ebs_bound(ebs_prim)
            local_box = ebs_bbox.GetRange()
            to_world = ebs_bbox.GetMatrix()
            world_box = ebs_bbox.ComputeAlignedRange()
        if local_box.IsEmpty():
            return {face: [] for face in FACES}

        with self._stage_timer("faces: search"):
            cells = {
                face: [Gf.BBox3d(rng, to_world).ComputeAlignedRange()
                       for rng, _ in boxes]
                for face, boxes in self._build_cells(local_box).items()
            }

        with self._stage_timer("faces: search"):
            depth = self._probe_depth(local_box)
            margin = Gf.Vec3d(depth, depth, depth)
            search = Gf.Range3d(world_box.GetMin() - margin, world_box.GetMax() + margin)
            skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
            candidates, visited = self._by_face(stage, cache, search, skip,
                                                roots, cells, margin)
        coarse = len(candidates)

        size = local_box.GetMax() - local_box.GetMin()
        self._note(f"precision {self._precision}, probe depth {depth:.4f}, "
                   f"EBS size ({size[0]:.3f}, {size[1]:.3f}, {size[2]:.3f})")
        self._note(f"EBS local box {tuple(round(v, 3) for v in local_box.GetMin())} .. "
                   f"{tuple(round(v, 3) for v in local_box.GetMax())} "
                   f"(the cells tile exactly this)")
        self._note(f"EBS world box {tuple(round(v, 2) for v in world_box.GetMin())} .. "
                   f"{tuple(round(v, 2) for v in world_box.GetMax())}")
        self._note(f"visited {visited} prims, {coarse} meshes within the probe, "
                   f"skipping {skip}")

        with self._stage_timer("faces: detect"):
            result = {face: [False] * len(boxes) for face, boxes in cells.items()}
            hits = {}
            self._blockers = {}
            triangle_tests = 0
            boxed_only = set()
            flat = [(face, i, cell,
                     tuple(cell.GetMin()), tuple(cell.GetMax()))
                    for face, boxes in cells.items() for i, cell in enumerate(boxes)]

            for path, box, mine in candidates:
                targets = [entry for entry in flat
                           if entry[0] in mine
                           and not result[entry[0]][entry[1]]
                           and self._overlaps(box, entry[2])]
                if not targets:
                    continue

                triangles = (self._mesh_triangles(stage, path)
                             if self._precision == PRECISION_TRI else None)
                if triangles:
                    triangle_tests += len(triangles)
                    for triangle, lo, hi in triangles:
                        remaining = [e for e in targets if not result[e[0]][e[1]]]
                        if not remaining:
                            break
                        for face, i, cell, edge, far in remaining:
                            if (lo[0] <= far[0] and hi[0] >= edge[0]
                                    and lo[1] <= far[1] and hi[1] >= edge[1]
                                    and lo[2] <= far[2] and hi[2] >= edge[2]
                                    and self._triangle_hits_box(triangle, cell)):
                                result[face][i] = True
                                self._blockers.setdefault(face, path)
                                hits.setdefault(path.rsplit("/", 1)[-1], []).append(
                                    f"{face}[{i}]")
                    continue

                if self._precision == PRECISION_TRI:
                    boxed_only.add(path)
                for face, i, _, _, _ in targets:
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
        if prim is None or not prim.IsValid():
            return
        root = str(prim.GetPath())
        for path in [p for p in self._triangles
                     if p == root or p.startswith(root + "/")]:
            del self._triangles[path]

    def _mesh_local(self, stage, path: str):
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
                data = (points, counts, indices)
        elif prim and prim.IsValid():
            self._boxed.setdefault(f"a {prim.GetTypeName()}", []).append(path)
        self._local[path] = data
        return data

    @staticmethod
    def _to_world(stage, path: str):
        prim = stage.GetPrimAtPath(path) if stage else None
        if prim is None or not prim.IsValid():
            return None
        try:
            return UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default())
        except Exception:
            return None

    def _mesh_triangles(self, stage, path: str) -> list:
        if path in self._triangles:
            return self._triangles[path]
        triangles = []
        data = self._mesh_local(stage, path)
        to_world = self._to_world(stage, path)
        if data and to_world is not None:
            points, counts, indices = data
            world = [to_world.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in points]
            cursor = 0
            for count in counts:
                if count >= 3 and cursor + count <= len(indices):
                    fan = [world[indices[cursor + k]] for k in range(count)]
                    for k in range(1, count - 1):
                        triangles.append(self._with_box(
                            (fan[0], fan[k], fan[k + 1])))
                cursor += count
        self._triangles[path] = triangles
        return triangles

    @staticmethod
    def _with_box(triangle):
        a, b, c = triangle
        return (triangle,
                (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]),
                 min(a[2], b[2], c[2])),
                (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]),
                 max(a[2], b[2], c[2])))

    def _triangles_reaching(self, stage, path: str, box: Gf.Range3d) -> list:
        lo_box, hi_box = box.GetMin(), box.GetMax()
        x0, y0, z0 = lo_box[0], lo_box[1], lo_box[2]
        x1, y1, z1 = hi_box[0], hi_box[1], hi_box[2]

        cached = self._triangles.get(path)
        if cached is not None:
            return [(path, tri, lo, hi) for tri, lo, hi in cached
                    if lo[0] <= x1 and hi[0] >= x0 and lo[1] <= y1
                    and hi[1] >= y0 and lo[2] <= z1 and hi[2] >= z0]

        data = self._mesh_local(stage, path)
        to_world = self._to_world(stage, path)
        if not data or to_world is None:
            return []
        near = self._pulled_back(box, to_world)
        if near is None:
            return []
        wanted = self._faces_near(path, data, near)
        if not wanted:
            return []

        points, counts, indices = data
        start, size = self._faces[path][0], self._faces[path][1]
        move = self._mover(to_world, points, path)

        kept = []
        for at in wanted:
            first, count = start[at], size[at]
            fan = [move(points[indices[first + k]]) for k in range(count)]
            for k in range(1, count - 1):
                a, b, c = fan[0], fan[k], fan[k + 1]
                low = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]),
                       min(a[2], b[2], c[2]))
                high = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]),
                        max(a[2], b[2], c[2]))
                if (low[0] <= x1 and high[0] >= x0 and low[1] <= y1
                        and high[1] >= y0 and low[2] <= z1 and high[2] >= z0):
                    kept.append((path, (a, b, c), low, high))
        return kept

    def _mover(self, to_world, points, path: str):
        """로컬 점을 월드로. 행렬을 펼 수 있으면 파이썬 산술로 돈다."""
        try:
            r0, r1, r2, r3 = (to_world.GetRow(0), to_world.GetRow(1),
                              to_world.GetRow(2), to_world.GetRow(3))
            a00, a01, a02 = r0[0], r0[1], r0[2]
            a10, a11, a12 = r1[0], r1[1], r1[2]
            a20, a21, a22 = r2[0], r2[1], r2[2]
            a30, a31, a32 = r3[0], r3[1], r3[2]
            if points:
                x, y, z = points[0][0], points[0][1], points[0][2]
                mine = (x * a00 + y * a10 + z * a20 + a30,
                        x * a01 + y * a11 + z * a21 + a31,
                        x * a02 + y * a12 + z * a22 + a32)
                theirs = to_world.Transform(Gf.Vec3d(x, y, z))
                span = max(abs(theirs[i]) for i in range(3)) or 1.0
                if all(abs(mine[i] - theirs[i]) <= span * 1e-9 for i in range(3)):
                    return lambda p: (p[0] * a00 + p[1] * a10 + p[2] * a20 + a30,
                                      p[0] * a01 + p[1] * a11 + p[2] * a21 + a31,
                                      p[0] * a02 + p[1] * a12 + p[2] * a22 + a32)
                self._boxed.setdefault("an unexpected transform", []).append(path)
        except Exception:
            pass
        return lambda p: to_world.Transform(Gf.Vec3d(p[0], p[1], p[2]))

    def _face_grid(self, path: str, data):
        """면마다 로컬 상자를 한 번 재고 격자에 담는다. 메시가 안 변하면 그대로.

        매번 점을 다시 훑던 자리다 -- 면 20만이면 그것만 150 ms 다.
        로컬 공간이라 EBS 를 옮겨도 다시 안 만든다 (행렬만 바뀐다).
        """
        made = self._faces.get(path)
        if made is not None:
            return made
        points, counts, indices = data
        start, size = array.array("i"), array.array("i")
        lows = [array.array("d") for _ in range(3)]
        highs = [array.array("d") for _ in range(3)]
        cursor, total = 0, len(indices)
        for count in counts:
            end = cursor + count
            if count < 3 or end > total:
                cursor = end
                continue
            corner = points[indices[cursor]]
            lo = [corner[0], corner[1], corner[2]]
            hi = [corner[0], corner[1], corner[2]]
            for k in range(cursor + 1, end):
                corner = points[indices[k]]
                for i in range(3):
                    v = corner[i]
                    if v < lo[i]:
                        lo[i] = v
                    elif v > hi[i]:
                        hi[i] = v
            start.append(cursor)
            size.append(count)
            for i in range(3):
                lows[i].append(lo[i])
                highs[i].append(hi[i])
            cursor = end

        faces = len(start)
        if not faces:
            made = (start, size, lows, highs, (0.0, 0.0, 0.0),
                    (1.0, 1.0, 1.0), 1, {})
            self._faces[path] = made
            return made
        origin = tuple(min(lows[i]) for i in range(3))
        far = tuple(max(highs[i]) for i in range(3))
        spread = max(1, min(GRID_CELLS, int(round(faces ** (1.0 / 3.0)))))
        step = tuple(max((far[i] - origin[i]) / spread, 1e-9) for i in range(3))
        grid = {}
        for at in range(faces):
            for key in self._cells_of([lows[i][at] for i in range(3)],
                                      [highs[i][at] for i in range(3)],
                                      origin, step, spread):
                grid.setdefault(key, []).append(at)
        made = (start, size, lows, highs, origin, step, spread, grid)
        self._faces[path] = made
        return made

    def _faces_near(self, path: str, data, near) -> list:
        start, size, lows, highs, origin, step, spread, grid = \
            self._face_grid(path, data)
        if not grid:
            return []
        (lx, ly, lz), (hx, hy, hz) = near
        seen = set()
        for key in self._cells_of((lx, ly, lz), (hx, hy, hz),
                                  origin, step, spread):
            seen.update(grid.get(key, ()))
        lo0, lo1, lo2 = lows
        hi0, hi1, hi2 = highs
        return [at for at in seen
                if lo0[at] <= hx and hi0[at] >= lx and lo1[at] <= hy
                and hi1[at] >= ly and lo2[at] <= hz and hi2[at] >= lz]

    @staticmethod
    def _pulled_back(box: Gf.Range3d, to_world):
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
        known = self._visible.get(path)
        if known is not None:
            return known
        visible = True
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
        path = self._path_of(prim)
        if self._ebs_box is not None and self._ebs_box[0] == path:
            return self._ebs_box[1]
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
        if path:
            self._ebs_box = (path, exact)
        return exact

    @staticmethod
    def _path_of(prim) -> str:
        try:
            return str(prim.GetPath()) if prim.IsValid() else ""
        except AttributeError:
            return ""

    def _forget_ebs(self, paths=()) -> None:
        self._ebs_box = None
        for path in paths:
            self._visible.pop(path, None)

    def _stage_boxes(self, cache=None) -> list:
        if self._stage_index is not None:
            return self._stage_index
        stage = self._get_stage()
        if stage is None:
            return []
        cache = cache if cache is not None else self._bounds_cache()
        index = []
        with self._stage_timer("stage: index"):
            stack = [(prim, ()) for prim in _children(stage.GetPseudoRoot())]
            while stack:
                prim, chain = stack.pop()
                path = str(prim.GetPath())
                if path in OURS or path.startswith(OURS_UNDER):
                    continue
                type_name = prim.GetTypeName()
                if type_name in SKIP_TYPES or type_name.endswith("Light"):
                    continue
                box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
                if box.IsEmpty():
                    continue
                if (type_name in GEOMETRY_TYPES
                        or prim.GetName().upper().startswith(EQP_PREFIX)):
                    lo, hi = box.GetMin(), box.GetMax()
                    index.append((path,
                                  (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]),
                                  box, prim, chain))
                    continue
                stack.extend((kid, chain + ((prim, path),))
                             for kid in _children(prim))
        self._stage_index = index
        self._note(f"stage index: {len(index)} boxes")
        return index

    def _from_index(self, stage, cache, search: Gf.Range3d, skip: list) -> tuple:
        skip_exact = frozenset(skip)
        skip_under = tuple(s + "/" for s in skip)
        low, high = search.GetMin(), search.GetMax()
        lo0, lo1, lo2 = low[0], low[1], low[2]
        hi0, hi1, hi2 = high[0], high[1], high[2]
        eps = OVERLAP_EPS
        found, visited, inside = [], 0, []
        for path, lo, hi, box, prim, chain in self._stage_boxes(cache):
            if path in skip_exact or (skip_under and path.startswith(skip_under)):
                continue
            visited += 1
            if (min(hi[0], hi0) - max(lo[0], lo0) <= eps
                    or min(hi[1], hi1) - max(lo[1], lo1) <= eps
                    or min(hi[2], hi2) - max(lo[2], lo2) <= eps):
                continue
            if any(not self._is_visible(one, where) for one, where in chain):
                continue
            inside.append(prim)
        for prim in inside:
            got, seen = self._gather_nearby(stage, cache, search, skip, [prim])
            found.extend(got)
            visited += seen
        return found, visited

    def _by_face(self, stage, cache, search, skip, roots, cells, margin):
        if roots is None:
            found, visited = self._gather_nearby(stage, cache, search, skip)
            return [(path, box, FACES) for path, box in found], visited

        sides = tuple(face for face in cells if face != FACE_CEILING)
        beside, visited = self._gather_nearby(stage, cache, search, skip, roots)
        candidates = [(path, box, sides) for path, box in beside]

        top = cells.get(FACE_CEILING) or []
        if top:
            whole = self._union(top)
            above, seen = self._gather_nearby(
                stage, cache,
                Gf.Range3d(whole.GetMin() - margin, whole.GetMax() + margin),
                skip)
            visited += seen
            candidates += [(path, box, (FACE_CEILING,)) for path, box in above]
        return candidates, visited

    def _gather_nearby(self, stage, cache, search: Gf.Range3d, skip: list,
                       roots: list = None) -> tuple:
        if roots is None:
            return self._from_index(stage, cache, search, skip)
        found, visited = [], 0
        ours_exact = frozenset(OURS)
        skip_exact = frozenset(skip)
        skip_under = tuple(s + "/" for s in skip)
        stack = list(roots)
        while stack:
            prim = stack.pop()
            path = str(prim.GetPath())
            if path in ours_exact or path.startswith(OURS_UNDER):
                continue
            if path in skip_exact or (skip_under and path.startswith(skip_under)):
                continue
            type_name = prim.GetTypeName()
            if type_name in SKIP_TYPES or type_name.endswith("Light"):
                continue

            visited += 1
            box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if box.IsEmpty() or not self._overlaps(box, search):
                continue
            if not self._is_visible(prim, path):
                continue
            if type_name in GEOMETRY_TYPES:
                found.append((path, box))
                continue
            stack.extend(_children(prim))
        return found, visited

    def check_equipment(self, ebs_prim: Usd.Prim, eqp_prim: Usd.Prim,
                        cache=None) -> dict:
        stage = self._get_stage()
        blank = {"hit": False, "pairs": [], "tests": 0}
        if stage is None or eqp_prim is None or not eqp_prim.IsValid():
            return blank

        cache = cache if cache is not None else self._bounds_cache()
        world_box = self._ebs_bound(ebs_prim).ComputeAlignedRange()
        if world_box.IsEmpty():
            return blank

        with self._stage_timer("equipment: search"):
            ours, _ = self._gather_nearby(stage, cache, world_box, [],
                                          roots=[ebs_prim])
            theirs, _ = self._gather_nearby(stage, cache, world_box, [],
                                            roots=[eqp_prim])
        if not ours or not theirs:
            self._note(f"no interference test: {len(ours)} EBS meshes against "
                       f"{len(theirs)} on the equipment")
            return blank

        shared = Gf.Range3d.GetIntersection(self._union([b for _, b in ours]),
                                            self._union([b for _, b in theirs]))
        if shared.IsEmpty():
            self._note(f"clear of the equipment: {len(ours)} EBS meshes and "
                       f"{len(theirs)} on it never share a box")
            return blank

        with self._stage_timer("equipment: read"):
            mine, ebs_read = self._triangles_near(stage, ours, shared)
            yours, eqp_read = self._triangles_near(stage, theirs, shared)
        self._note("read: " + "; ".join(
            f"{side} {t['meshes']} mesh, {t['faces']} faces, "
            f"{t['built']} grid built, {t['world']} from the world cache"
            for side, t in (("EBS", ebs_read), ("equipment", eqp_read))))
        if not mine or not yours:
            self._note(f"clear of the equipment: nothing reaches the shared box "
                       f"({len(mine)} against {len(yours)} triangles)")
            return blank

        with self._stage_timer("equipment: detect"):
            pairs, tests = self._meetings(mine, yours, shared)
        self._note(f"interference: {len(mine)} EBS triangles against {len(yours)} "
                   f"on the equipment, {tests} pairs tested")
        return {"hit": bool(pairs), "pairs": pairs, "tests": tests}

    @staticmethod
    def _union(boxes: list) -> Gf.Range3d:
        lo = [min(b.GetMin()[i] for b in boxes) for i in range(3)]
        hi = [max(b.GetMax()[i] for b in boxes) for i in range(3)]
        return Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))

    def _triangles_near(self, stage, meshes: list, box: Gf.Range3d) -> tuple:
        kept, tally = [], {"meshes": 0, "built": 0, "world": 0, "faces": 0}
        for path, mesh_box in meshes:
            if Gf.Range3d.GetIntersection(mesh_box, box).IsEmpty():
                continue
            tally["meshes"] += 1
            if path in self._triangles:
                tally["world"] += 1
            elif path not in self._faces:
                tally["built"] += 1
            kept.extend(self._triangles_reaching(stage, path, box))
            made = self._faces.get(path)
            if made:
                tally["faces"] += len(made[0])
        return kept, tally

    def _meetings(self, mine: list, yours: list, box: Gf.Range3d) -> tuple:
        grid, origin, step, spread = self._grid_of(yours, box)
        pairs, tests = [], 0
        for ebs_path, triangle, lo, hi in mine:
            seen = set()
            for key in self._cells_of(lo, hi, origin, step, spread):
                seen.update(grid.get(key, ()))
            for index in seen:
                eqp_path, other, other_lo, other_hi = yours[index]
                if (lo[0] > other_hi[0] or hi[0] < other_lo[0]
                        or lo[1] > other_hi[1] or hi[1] < other_lo[1]
                        or lo[2] > other_hi[2] or hi[2] < other_lo[2]):
                    continue
                tests += 1
                if self._triangles_meet(triangle, other):
                    pairs.append((ebs_path, eqp_path))
                    return pairs, tests
        return pairs, tests

    @classmethod
    def _grid_of(cls, items: list, box: Gf.Range3d) -> tuple:
        low, high = box.GetMin(), box.GetMax()
        origin = (low[0], low[1], low[2])
        size = [max(high[i] - origin[i], 1e-9) for i in range(3)]
        spread = max(1, min(GRID_CELLS, int(round(len(items) ** (1.0 / 3.0)))))
        step = [size[i] / spread for i in range(3)]
        grid = {}
        for index, (_, _, lo, hi) in enumerate(items):
            for key in cls._cells_of(lo, hi, origin, step, spread):
                grid.setdefault(key, []).append(index)
        return grid, origin, step, spread

    @staticmethod
    def _cells_of(lo, hi, origin, step, spread):
        spans = []
        for i in range(3):
            first = int((lo[i] - origin[i]) / step[i])
            last = int((hi[i] - origin[i]) / step[i])
            spans.append(range(max(0, min(first, spread - 1)),
                               max(0, min(last, spread - 1)) + 1))
        return [(x, y, z) for x in spans[0] for y in spans[1] for z in spans[2]]

    @classmethod
    def _triangles_meet(cls, a, b) -> bool:
        for edge in ((a[0], a[1]), (a[1], a[2]), (a[2], a[0])):
            if cls._segment_hits_triangle(edge[0], edge[1], b):
                return True
        for edge in ((b[0], b[1]), (b[1], b[2]), (b[2], b[0])):
            if cls._segment_hits_triangle(edge[0], edge[1], a):
                return True
        return False

    @staticmethod
    def _segment_hits_triangle(start, end, triangle) -> bool:
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
            return False
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
        front_axis = 3 - up_axis
        side_axis = 3 - up_axis - front_axis
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
                      exclude: list = None, cache=None, roots: list = None) -> dict:
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

        with self._stage_timer("clearance: search"):
            wanted = {}
            for face, (axis, outward, coord, _, _) in self._face_planes.items():
                if any(cells.get(face, [])):
                    continue
                prism = self._face_prism(local_box, axis, outward, coord, reach)
                wanted[face] = (prism,
                                Gf.BBox3d(prism, to_world).ComputeAlignedRange(),
                                axis, outward, coord)
            candidates = self._reach_by_face(stage, cache, skip, roots, wanted)
        if not wanted:
            return {}

        with self._stage_timer("clearance: detect"):
            results = {}
            for face, (prism, world_prism, axis, outward, coord) in wanted.items():
                near = [(path, box) for path, box in candidates.get(face, ())
                        if self._overlaps(box, world_prism)]
                found = self._nearest_in_prism(stage, near, prism, to_world,
                                               axis, outward, coord)
                results[face] = found or {"distance": None, "prim": "",
                                          "reach": reach}
        return results

    def _reach_by_face(self, stage, cache, skip, roots, wanted) -> dict:
        if not wanted:
            return {}
        if roots is None:
            whole = self._union([box for _, box, _, _, _ in wanted.values()])
            found, _ = self._gather_nearby(stage, cache, whole, skip)
            return {face: found for face in wanted}

        by_face = {}
        sides = {face: one for face, one in wanted.items() if face != FACE_CEILING}
        if sides:
            whole = self._union([box for _, box, _, _, _ in sides.values()])
            found, _ = self._gather_nearby(stage, cache, whole, skip, roots)
            by_face.update({face: found for face in sides})
        top = wanted.get(FACE_CEILING)
        if top is not None:
            found, _ = self._gather_nearby(stage, cache, top[1], skip)
            by_face[FACE_CEILING] = found
        return by_face

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
                best, best_path = gap, path
                best_at = self._box_point(local, prism, axis, outward, coord, gap)
                continue
            for triangle, _, _ in triangles:
                local_tri = [inverse.Transform(Gf.Vec3d(*v)) for v in triangle]
                found = self._triangle_gap(local_tri, prism, axis, outward, coord)
                if found is not None and (best is None or found[0] < best):
                    best, best_path, best_at = found[0], path, found[1]
        if best is None:
            return None
        return {"distance": max(best, 0.0), "prim": best_path, "at": best_at}

    @staticmethod
    def _box_point(local, prism, axis: int, outward: int, coord: float, gap: float):
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
            tight = {mark["face"] for mark in marks or ()
                     if mark.get("state") == STATE_TIGHT}
            for face, boxes in built.items():
                flags = ([True] * len(boxes) if face in tight
                         else cells.get(face, []))
                for i, (_, quad) in enumerate(boxes):
                    material, colour, alpha = materials[
                        bool(i < len(flags) and flags[i])]
                    points = [to_world.Transform(Gf.Vec3d(*corner)) for corner in quad]
                    self._marker_sheet(stage, f"{MARKER_ROOT}/{face}_{i}", points,
                                       material, colour, alpha)
                    drawn += 1

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
                bottom = spot[2]
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
                top, bottom = port[2], here[2]
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
        self._verdict = {}
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


    def release_camera(self) -> None:
        self.show_equipment()
        self._camera.release(self._get_stage())

    def refresh_camera(self) -> dict:
        told = self._camera.reset(self._get_stage())
        if told:
            self._note(told)
        return self._payload(bool(told), told or "Run Camera first")

    def _world_range(self, prim, cache=None) -> "Gf.Range3d | None":
        if prim is None or not prim.IsValid():
            return None
        if cache is None:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
                useExtentsHint=True,
            )
        box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        return None if box.IsEmpty() else box

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
            "rows": rows or [],
            "equipment_hit": equipment_hit or {"hit": False, "pairs": [], "tests": 0},
            "timings": list(self._timings),
            "notes": list(self._notes),
            "total_ms": (time.perf_counter() - self._started) * 1000.0,
        }
        return dict(self._result)
