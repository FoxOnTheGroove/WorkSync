import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager

from pxr import Usd, UsdGeom, Gf
import omni.usd

__all__ = ["EbsSimulate"]

EQP_PREFIX = "EQP_"

# Faces evaluated by the simulation. Front, back and floor are ignored.
FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)

GRID = 3                 # 3x3 subdivision per face
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
        self._eqp_index: dict = {}          # "EQP_########" -> prim path
        self._port_map: dict = {}           # "########" -> port count
        self._bounds_cache: dict = {}       # prim path -> world aligned Gf.Range3d
        self._timings: list = []            # [label, elapsed_ms] for the last run
        self._result: dict = {}

    # -- settings ------------------------------------------------------------

    def set_xml_path(self, path: str) -> None:
        path = (path or "").strip()
        if path != self._xml_path:
            self._port_map = {}
        self._xml_path = path

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def set_clearance(self, value: float) -> None:
        self._clearance = max(0.0, float(value))

    def get_result(self) -> dict:
        return dict(self._result)

    def get_timings(self) -> list:
        """[label, elapsed_ms] recorded during the last run."""
        return [list(t) for t in self._timings]

    def teardown(self) -> None:
        self._eqp_index = {}
        self._port_map = {}
        self._bounds_cache = {}
        self._timings = []
        self._result = {}

    # -- timing --------------------------------------------------------------

    @contextmanager
    def _stage_timer(self, label: str):
        """Record how long one step of the run took."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self._timings.append([label, (time.perf_counter() - started) * 1000.0])

    # -- entry point ---------------------------------------------------------

    def simulate(self, equipment: str = "") -> dict:
        """Simulate for the given equipment name/path, or the current selection if empty."""
        self._timings = []
        started = time.perf_counter()

        stage = self._get_stage()
        if stage is None:
            return self._fail("No stage open")

        with self._stage_timer("resolve equipment"):
            eqp_prim = (self._resolve_by_name(stage, equipment) if equipment.strip()
                        else self._resolve_by_selection(stage))
        if eqp_prim is None:
            return self._fail("Equipment prim not found: "
                              f"{equipment.strip() or '(no selection)'}")

        eqp_id = self._equipment_id(eqp_prim)

        with self._stage_timer("camera focus"):
            self.focus(str(eqp_prim.GetPath()))

        with self._stage_timer("port lookup"):
            port_count = self.get_port_count(eqp_id)
        if port_count is None:
            return self._fail(f"No port info for '{eqp_id}' in XML",
                              equipment=eqp_prim, eqp_id=eqp_id)
        if port_count not in (2, 3):
            return self._fail(f"{port_count}-port equipment: no matching EBS",
                              equipment=eqp_prim, eqp_id=eqp_id,
                              port_count=port_count)

        ebs_path = self._ebs_path_2port if port_count == 2 else self._ebs_path_3port
        ebs_prim = stage.GetPrimAtPath(ebs_path) if ebs_path else None
        if ebs_prim is None or not ebs_prim.IsValid():
            return self._fail(f"Invalid {port_count}-port EBS prim path: {ebs_path}",
                              equipment=eqp_prim, eqp_id=eqp_id,
                              port_count=port_count)

        with self._stage_timer("align EBS"):
            anchor = self._descend_first_child(eqp_prim, ANCHOR_DEPTH)
            aligned = self.align(ebs_prim, anchor)
        if not aligned:
            return self._fail("EBS alignment failed",
                              equipment=eqp_prim, eqp_id=eqp_id,
                              port_count=port_count, ebs=ebs_prim)

        cells = self.check_collision(ebs_prim, exclude=[eqp_prim, ebs_prim])
        hit_count = sum(sum(1 for c in v if c) for v in cells.values())

        self._result = {
            "ok": True,
            "reason": "No collision" if hit_count == 0 else f"{hit_count} cell(s) blocked",
            "equipment": str(eqp_prim.GetPath()),
            "equipment_id": eqp_id,
            "port_count": port_count,
            "ebs": str(ebs_prim.GetPath()),
            "anchor": str(anchor.GetPath()),
            "cells": cells,
            "hit_count": hit_count,
            "timings": list(self._timings),
            "total_ms": (time.perf_counter() - started) * 1000.0,
        }
        return dict(self._result)

    # -- equipment lookup ----------------------------------------------------

    def build_index(self) -> int:
        """Rebuild the EQP_ prim index. Descent stops at each equipment, so it
        never walks the meshes inside them - that is what keeps it cheap here."""
        stage = self._get_stage()
        self._eqp_index = {}
        self._bounds_cache = {}
        if stage is None:
            return 0
        with self._stage_timer("build index"):
            stack = list(stage.GetPseudoRoot().GetChildren())
            while stack:
                prim = stack.pop()
                name = prim.GetName().upper()
                if name.startswith(EQP_PREFIX):
                    self._eqp_index[name] = str(prim.GetPath())
                    continue          # do not descend into equipment internals
                stack.extend(prim.GetChildren())
        return len(self._eqp_index)

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
        if key not in self._eqp_index:
            self.build_index()
        path = self._eqp_index.get(key)
        if not path:
            return None
        prim = stage.GetPrimAtPath(path)
        return prim if prim.IsValid() else None

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
        """Parse the XML into '########' -> port count. Returns the entry count."""
        self._port_map = {}
        if not self._xml_path:
            return 0
        with self._stage_timer("parse XML"):
            try:
                root = ET.parse(self._xml_path).getroot()
            except Exception as e:
                print(f"[ebs] XML parse failed: {e}")
                return 0

            pattern = re.compile(r"^([A-Za-z0-9]+)_(\d+)$")
            for elem in root.iter():
                for token in self._tokens(elem):
                    m = pattern.match(token)
                    if not m:
                        continue
                    key, index = m.group(1).upper(), int(m.group(2))
                    if index > self._port_map.get(key, 0):
                        self._port_map[key] = index
        return len(self._port_map)

    @staticmethod
    def _tokens(elem) -> list:
        """Collect port keys wherever they sit: tag name, attribute key/value or text."""
        out = [elem.tag.rsplit("}", 1)[-1]]
        for k, v in elem.attrib.items():
            out.append(k.rsplit("}", 1)[-1])
            out.append(v)
        if elem.text:
            out.append(elem.text)
        return [t.strip() for t in out if t and t.strip()]

    def get_port_count(self, eqp_id: str) -> "int | None":
        """Port count of an equipment: the highest index found is the count."""
        if not self._port_map:
            self.load_ports()
        return self._port_map.get(eqp_id.upper())

    # -- alignment -----------------------------------------------------------

    def align(self, ebs_prim: Usd.Prim, anchor_prim: Usd.Prim) -> bool:
        """Match the EBS position and rotation to the anchor prim."""
        stage = self._get_stage()
        if stage is None or not anchor_prim.IsValid():
            return False
        xformable = UsdGeom.Xformable(ebs_prim)
        if not xformable:
            return False

        tc = Usd.TimeCode.Default()
        anchor_world = UsdGeom.Xformable(anchor_prim).ComputeLocalToWorldTransform(tc)

        parent = ebs_prim.GetParent()
        parent_world = Gf.Matrix4d(1.0)
        if parent and parent.IsValid() and UsdGeom.Xformable(parent):
            parent_world = UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(tc)

        # Row-vector convention: M_local * M_parent = M_world
        target_local = anchor_world * parent_world.GetInverse()

        # Author into the session layer so the source layers stay untouched.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            try:
                # One transform op carries position and rotation together.
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(target_local)
                return True
            except Exception as e:
                print(f"[ebs] transform op failed, translate only: {e}")
                api = UsdGeom.XformCommonAPI(ebs_prim)
                return bool(api and api.SetTranslate(
                    Gf.Vec3d(target_local.ExtractTranslation())))

    # -- collision -----------------------------------------------------------

    def check_collision(self, ebs_prim: Usd.Prim, exclude: list = None) -> dict:
        """Test the left, right and ceiling faces as 3x3 grids.

        Faces and cells follow the EBS prim's local axes
        (+X front, +/-Y sides, +up ceiling).
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

        +X is the front (not evaluated), +/-Y the sides, +up the ceiling.
        The camera looks from local +X toward -X, so on screen
        right is +Y and up is +up.
        """
        up_axis = 1 if UsdGeom.GetStageUpAxis(self._get_stage()) == UsdGeom.Tokens.y else 2
        front_axis = 0                                  # front/back, not evaluated
        side_axis = 3 - up_axis - front_axis            # sides (Y when Z-up)
        t = self._clearance

        lo, hi = box.GetMin(), box.GetMax()
        cells = {}

        def make(fixed_axis, outward, row_axis, col_axis):
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
                    cmin[col_axis] = col_lo + c * col_step
                    cmax[col_axis] = col_lo + (c + 1) * col_step
                    if outward > 0:
                        cmin[fixed_axis], cmax[fixed_axis] = hi[fixed_axis], hi[fixed_axis] + t
                    else:
                        cmin[fixed_axis], cmax[fixed_axis] = lo[fixed_axis] - t, lo[fixed_axis]
                    out.append(Gf.Range3d(Gf.Vec3d(*cmin), Gf.Vec3d(*cmax)))
            return out

        cells[FACE_RIGHT]   = make(side_axis, +1, up_axis, front_axis)
        cells[FACE_LEFT]    = make(side_axis, -1, up_axis, front_axis)
        cells[FACE_CEILING] = make(up_axis,   +1, front_axis, side_axis)
        return cells

    # -- camera --------------------------------------------------------------

    def focus(self, prim_path: str) -> bool:
        """Put the camera in front of the prim (its local +X) and fill the view like F."""
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
        """Aim the camera from the prim's local +X toward -X."""
        cam_prim = stage.GetPrimAtPath(str(viewport.camera_path))
        if not cam_prim.IsValid():
            return False

        tc = Usd.TimeCode.Default()
        local_to_world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(tc)
        rot = local_to_world.ExtractRotationMatrix()
        up_row = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
        front = Gf.Vec3d(rot[0][0], rot[0][1], rot[0][2]).GetNormalized()          # local +X
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

    def _fail(self, reason: str, equipment=None, eqp_id="", port_count=None, ebs=None) -> dict:
        self._result = {
            "ok": False,
            "reason": reason,
            "equipment": str(equipment.GetPath()) if equipment else "",
            "equipment_id": eqp_id,
            "port_count": port_count,
            "ebs": str(ebs.GetPath()) if ebs else "",
            "anchor": "",
            "cells": {face: [False] * (GRID * GRID) for face in FACES},
            "hit_count": 0,
            "timings": list(self._timings),
            "total_ms": sum(t[1] for t in self._timings),
        }
        return dict(self._result)
