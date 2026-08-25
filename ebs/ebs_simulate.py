import re
import xml.etree.ElementTree as ET

from pxr import Usd, UsdGeom, Gf
import omni.usd

__all__ = ["EbsSimulate"]

EQP_PREFIX = "EQP_"

# 시뮬레이션 판정 면. 전/후/바닥은 보지 않는다.
FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)

GRID = 3                 # 면당 3x3 분할
ANCHOR_DEPTH = 6         # 장비 프림에서 첫 자식을 타고 내려갈 단계 수


class EbsSimulate:
    """EBS 시뮬레이션 구현부.

    모든 계산과 USD 접근은 이 클래스가 담당한다.
    외부에는 EbsSimulateService만 노출된다.
    """

    def __init__(self):
        self._xml_path: str = ""
        self._ebs_path_2port: str = ""
        self._ebs_path_3port: str = ""
        self._clearance: float = 1.0        # 각 면 바깥으로 검사할 두께
        self._eqp_index: dict = {}          # "EQP_########" -> prim path
        self._port_map: dict = {}           # "########" -> port count
        self._result: dict = {}

    # ── 설정 ─────────────────────────────────────────────────────────────────

    def set_xml_path(self, path: str) -> None:
        self._xml_path = (path or "").strip()
        self._port_map = {}

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def set_clearance(self, value: float) -> None:
        self._clearance = max(0.0, float(value))

    def get_result(self) -> dict:
        return dict(self._result)

    def teardown(self) -> None:
        self._eqp_index = {}
        self._port_map = {}
        self._result = {}

    # ── 시뮬레이션 진입점 ────────────────────────────────────────────────────

    def simulate(self, equipment: str = "") -> dict:
        """장비명(또는 경로)으로, 비어 있으면 현재 선택으로 시뮬레이션한다."""
        stage = self._get_stage()
        if stage is None:
            return self._fail("No stage open")

        eqp_prim = (self._resolve_by_name(stage, equipment) if equipment.strip()
                    else self._resolve_by_selection(stage))
        if eqp_prim is None:
            return self._fail("Equipment prim not found: "
                              f"{equipment.strip() or '(no selection)'}")

        eqp_id = self._equipment_id(eqp_prim)
        self.focus(str(eqp_prim.GetPath()))

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

        anchor = self._descend_first_child(eqp_prim, ANCHOR_DEPTH)
        if not self.align(ebs_prim, anchor):
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
        }
        return dict(self._result)

    # ── 장비 프림 조회 ───────────────────────────────────────────────────────

    def build_index(self) -> int:
        """EQP_ 프림 인덱스를 다시 만든다. 하부로는 내려가지 않아 대용량에서도 가볍다."""
        stage = self._get_stage()
        self._eqp_index = {}
        if stage is None:
            return 0
        stack = list(stage.GetPseudoRoot().GetChildren())
        while stack:
            prim = stack.pop()
            name = prim.GetName().upper()
            if name.startswith(EQP_PREFIX):
                self._eqp_index[name] = str(prim.GetPath())
                continue          # 장비 내부 메시까지 순회하지 않는다
            stack.extend(prim.GetChildren())
        return len(self._eqp_index)

    def get_selected_equipment(self) -> str:
        """현재 선택(메시)에서 조상을 거슬러 올라가 장비 경로를 반환한다."""
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
        """'########', 'EQP_########', 전체 경로 모두 허용."""
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
        """첫 자식을 depth 단계 따라 내려간다. 도중에 끊기면 마지막 프림."""
        current = prim
        for _ in range(depth):
            children = current.GetChildren()
            if not children:
                break
            current = children[0]
        return current

    # ── 포트 개수 (XML) ──────────────────────────────────────────────────────

    def load_ports(self) -> int:
        """XML을 읽어 '########' -> 포트 개수 맵을 만든다. 항목 수를 반환."""
        self._port_map = {}
        if not self._xml_path:
            return 0
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
        """태그명·속성 키/값·텍스트 어디에 있든 포트 키를 주워 담는다."""
        out = [elem.tag.rsplit("}", 1)[-1]]
        for k, v in elem.attrib.items():
            out.append(k.rsplit("}", 1)[-1])
            out.append(v)
        if elem.text:
            out.append(elem.text)
        return [t.strip() for t in out if t and t.strip()]

    def get_port_count(self, eqp_id: str) -> "int | None":
        """장비 ID의 포트 개수. 최대 인덱스가 곧 포트 수."""
        if not self._port_map:
            self.load_ports()
        return self._port_map.get(eqp_id.upper())

    # ── 정렬 ─────────────────────────────────────────────────────────────────

    def align(self, ebs_prim: Usd.Prim, anchor_prim: Usd.Prim) -> bool:
        """EBS의 위치와 회전을 anchor에 맞춘다."""
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

        # 행벡터 규약: M_local * M_parent = M_world
        target_local = anchor_world * parent_world.GetInverse()

        # 원본 레이어를 더럽히지 않도록 세션 레이어에 기록한다.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            try:
                # 회전까지 한 번에 담기 위해 transform op 하나로 덮어쓴다.
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(target_local)
                return True
            except Exception as e:
                print(f"[ebs] transform op failed, translate only: {e}")
                api = UsdGeom.XformCommonAPI(ebs_prim)
                return bool(api and api.SetTranslate(
                    Gf.Vec3d(target_local.ExtractTranslation())))

    # ── 충돌 판정 ────────────────────────────────────────────────────────────

    def check_collision(self, ebs_prim: Usd.Prim, exclude: list = None) -> dict:
        """좌·우·천장 3면을 3x3으로 나눠 각 칸의 충돌 여부를 반환한다.

        면과 셀은 EBS 프림의 로컬 축을 따른다 (+X 정면, ±Y 좌우, +Z 천장).
        """
        empty = {face: [False] * (GRID * GRID) for face in FACES}
        stage = self._get_stage()
        if stage is None:
            return empty

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,          # 대용량 스테이지에서 지오메트리 순회 회피
        )
        # GfBBox3d: GetRange()는 프림 로컬 박스, GetMatrix()는 로컬→월드.
        ebs_bbox = cache.ComputeWorldBound(ebs_prim)
        local_box = ebs_bbox.GetRange()
        to_world = ebs_bbox.GetMatrix()
        if local_box.IsEmpty():
            return empty

        # 로컬 축을 따라 자른 셀을, 각각 월드 AABB로 변환해 검사한다.
        cells = {
            face: [Gf.BBox3d(cell, to_world).ComputeAlignedRange() for cell in boxes]
            for face, boxes in self._build_cells(local_box).items()
        }

        # broad phase: EBS를 여유만큼 부풀린 범위에 걸치는 장비만 후보로 남긴다.
        world_box = ebs_bbox.ComputeAlignedRange()
        margin = Gf.Vec3d(self._clearance, self._clearance, self._clearance)
        search = Gf.Range3d(world_box.GetMin() - margin, world_box.GetMax() + margin)
        skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]

        if not self._eqp_index:
            self.build_index()

        candidates = []
        for path in self._eqp_index.values():
            if any(path == s or path.startswith(s + "/") for s in skip):
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                continue
            box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not box.IsEmpty() and not Gf.Range3d.GetIntersection(box, search).IsEmpty():
                candidates.append(box)

        result = {face: [False] * (GRID * GRID) for face in FACES}
        for face, boxes in cells.items():
            for i, cell in enumerate(boxes):
                for box in candidates:
                    if not Gf.Range3d.GetIntersection(box, cell).IsEmpty():
                        result[face][i] = True
                        break
        return result

    def _build_cells(self, box: Gf.Range3d) -> dict:
        """프림 로컬 축 기준으로 면별 3x3 셀을 만든다. 순서는 좌→우, 위→아래.

        +X가 정면(판정 제외), ±Y가 좌우, +up이 천장.
        카메라가 로컬 +X에서 -X를 보므로 화면 오른쪽이 +Y, 화면 위가 +up이 된다.
        """
        up_axis = 1 if UsdGeom.GetStageUpAxis(self._get_stage()) == UsdGeom.Tokens.y else 2
        front_axis = 0                                  # 전후 (판정 제외 방향)
        side_axis = 3 - up_axis - front_axis            # 좌우 (Z-up이면 Y)
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
                    # 위→아래 순서가 되도록 행을 뒤집는다
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

    # ── 카메라 ───────────────────────────────────────────────────────────────

    def focus(self, prim_path: str) -> bool:
        """대상 프림의 정면(로컬 +X쪽)에 카메라를 두고 F키처럼 화면에 채운다."""
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
            frame_viewport_prims(viewport, prims=[prim_path])   # 방향은 두고 크기만 맞춘다
        except Exception as e:
            print(f"[ebs] camera framing failed: {e}")
            return False
        return True

    def _place_front_camera(self, stage, viewport, prim, center, distance) -> bool:
        """프림 로컬 +X에서 -X를 바라보도록 카메라를 배치한다."""
        cam_prim = stage.GetPrimAtPath(str(viewport.camera_path))
        if not cam_prim.IsValid():
            return False

        tc = Usd.TimeCode.Default()
        local_to_world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(tc)
        rot = local_to_world.ExtractRotationMatrix()
        up_row = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
        front = Gf.Vec3d(rot[0][0], rot[0][1], rot[0][2]).GetNormalized()          # 로컬 +X
        up    = Gf.Vec3d(rot[up_row][0], rot[up_row][1], rot[up_row][2]).GetNormalized()

        # 카메라는 로컬 -Z를 바라본다 → Z_cam = 시선의 반대 = front
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

        # 뷰포트 카메라는 세션 레이어 프림이므로 세션 레이어에 기록해야 반영된다.
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
                coi.Set(Gf.Vec3d(0.0, 0.0, -distance))   # 궤도 회전 중심을 대상에 맞춘다
        return True

    # ── 내부 ─────────────────────────────────────────────────────────────────

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
        }
        return dict(self._result)
