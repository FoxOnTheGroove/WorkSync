import math
from collections import defaultdict, deque

from pxr import Usd, UsdGeom, UsdShade, Vt, Gf, Sdf, Tf
import omni.usd
import omni.kit.commands


class Subset:

    _initialized: bool = False

    FAMILY_NAME = "surfaces"   # 자동 생성 subset들의 familyName

    HIDE_MAT_NAME = "_SubsetHideMat"
    SAVED_MATERIAL_ATTR = "subset:savedMaterial"
    NO_BINDING_SENTINEL = "__none__"

    # ------------------------------------------------------------------ 공개 API

    @classmethod
    def initialize(cls) -> bool:
        stage = cls._get_stage()
        if not stage:
            print("[Subset] 스테이지를 가져올 수 없습니다.")
            cls._initialized = False
            return False
        cls._initialized = True
        print("[Subset] 초기화 완료.")
        return True

    @classmethod
    def list_subsets(cls, mesh_prim: Usd.Prim) -> list[Usd.Prim]:
        if not mesh_prim or not mesh_prim.IsValid():
            print("[Subset] 유효하지 않은 mesh prim.")
            return []
        return [
            child for child in mesh_prim.GetChildren()
            if child.IsA(UsdGeom.Subset)
        ]

    @classmethod
    def create_subset(
        cls,
        mesh_prim: Usd.Prim,
        name: str,
        indices: list[int],
        element_type: str = "face",
        family_name: str = "",
    ) -> "Usd.Prim | None":
        if not mesh_prim or not mesh_prim.IsValid():
            print("[Subset] 유효하지 않은 mesh prim.")
            return None

        mesh = UsdGeom.Mesh(mesh_prim)
        if not mesh:
            print("[Subset] mesh prim이 UsdGeomMesh가 아닙니다.")
            return None

        subset = UsdGeom.Subset.CreateGeomSubset(
            mesh,
            name,
            element_type,
            Vt.IntArray(indices),
            family_name,
        )
        return subset.GetPrim()

    @classmethod
    def classify_faces(
        cls,
        mesh_prim: Usd.Prim,
        threshold_deg: float = 30.0,
        min_faces: int = 1,
        merge_by_normal: bool = False,
    ) -> list[list[int]]:
        """이웃 면 간 법선 각도 차이 기반 region growing으로 면 그룹 분류.

        직육면체 → 6그룹, 꼭짓점 잘린 박스 → 7그룹, 원기둥 → 3그룹.
        min_faces 미만인 그룹은 가장 많이 맞닿은 이웃 그룹에 병합.
        merge_by_normal이 True면, 서로 떨어져 있어도 평균 법선이 threshold
        이내로 같은 방향을 보는 그룹들을 하나로 합친다.
        """
        data = cls._get_mesh_data(mesh_prim)
        if data is None:
            return []
        points, counts, indices = data

        normals = cls._compute_face_normals(points, counts, indices)
        adjacency = cls._build_adjacency(counts, indices)
        num_faces = len(counts)

        cos_threshold = math.cos(math.radians(threshold_deg))
        visited = [False] * num_faces
        groups: list[list[int]] = []

        for seed in range(num_faces):
            if visited[seed]:
                continue
            visited[seed] = True
            group = [seed]
            queue = deque([seed])
            while queue:
                face = queue.popleft()
                for nb in adjacency[face]:
                    if visited[nb]:
                        continue
                    # 이웃끼리의 상대 각도만 비교 → 곱면(원기둥 옆면)도 연쇄적으로 합쳌짐
                    if Gf.Dot(normals[face], normals[nb]) >= cos_threshold:
                        visited[nb] = True
                        group.append(nb)
                        queue.append(nb)
            groups.append(group)

        if min_faces > 1:
            groups = cls._merge_small_groups(groups, adjacency, min_faces)

        if merge_by_normal:
            groups = cls._merge_groups_by_normal(groups, normals, cos_threshold)

        groups.sort(key=len, reverse=True)
        return groups

    @classmethod
    def generate_subsets(
        cls,
        mesh_prim: Usd.Prim,
        threshold_deg: float = 30.0,
        min_faces: int = 1,
        merge_by_normal: bool = False,
    ) -> "tuple[list[Usd.Prim], list[list[int]]]":
        """면 분류 후 그룹별 GeomSubset 생성. 기존 자동 생성분은 제거."""
        groups = cls.classify_faces(mesh_prim, threshold_deg, min_faces, merge_by_normal)
        if not groups:
            return [], []

        cls.remove_generated_subsets(mesh_prim)

        prims = []
        for i, group in enumerate(groups):
            prim = cls.create_subset(
                mesh_prim,
                f"surface_{i:02d}",
                sorted(group),
                family_name=cls.FAMILY_NAME,
            )
            if prim:
                prims.append(prim)
        print(f"[Subset] subset {len(prims)}개 생성 (threshold={threshold_deg}°).")
        return prims, groups

    @classmethod
    def remove_generated_subsets(cls, mesh_prim: Usd.Prim) -> None:
        stage = cls._get_stage()
        if not stage:
            return
        for child in cls.list_subsets(mesh_prim):
            family = UsdGeom.Subset(child).GetFamilyNameAttr().Get()
            if family == cls.FAMILY_NAME:
                stage.RemovePrim(child.GetPath())

    # ------------------------------------------------------------------ Hide / Show

    @classmethod
    def _get_or_create_hide_material(cls, mesh_prim: Usd.Prim) -> UsdShade.Material:
        stage = cls._get_stage()
        mat_path = mesh_prim.GetPath().AppendPath(f"Looks/{cls.HIDE_MAT_NAME}")

        existing = stage.GetPrimAtPath(mat_path)
        if existing.IsValid() and existing.IsA(UsdShade.Material):
            return UsdShade.Material(existing)

        material = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path.AppendChild("Shader"))
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.0)
        shader.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(1.0)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return material

    @classmethod
    def is_hidden(cls, subset_prim: Usd.Prim) -> bool:
        if not subset_prim or not subset_prim.IsValid():
            return False
        return subset_prim.HasAttribute(cls.SAVED_MATERIAL_ATTR)

    @classmethod
    def toggle_hidden(cls, subset_prim: Usd.Prim) -> bool:
        """subset의 표시 상태를 반전. 반환값은 변경 후의 hidden 여부.

        실제 visibility 속성 대신, 불투명도 0인 머티리얼을 바인딩해 면을
        숨긴다(Hydra/RTX에서 GeomSubset visibility의 face culling이
        보장되지 않기 때문).
        """
        if not subset_prim or not subset_prim.IsValid() or not subset_prim.IsA(UsdGeom.Subset):
            print("[Subset] 유효하지 않은 subset prim.")
            return False

        binding_api = UsdShade.MaterialBindingAPI.Apply(subset_prim)
        stage = cls._get_stage()

        if cls.is_hidden(subset_prim):
            saved = subset_prim.GetAttribute(cls.SAVED_MATERIAL_ATTR).Get()
            if saved and saved != cls.NO_BINDING_SENTINEL:
                orig_prim = stage.GetPrimAtPath(saved)
                if orig_prim.IsValid() and orig_prim.IsA(UsdShade.Material):
                    binding_api.Bind(UsdShade.Material(orig_prim))
                else:
                    binding_api.UnbindDirectBinding()
            else:
                binding_api.UnbindDirectBinding()
            subset_prim.RemoveProperty(cls.SAVED_MATERIAL_ATTR)
            return False

        rel = binding_api.GetDirectBindingRel()
        targets = rel.GetTargets() if rel else []
        saved_value = str(targets[0]) if targets else cls.NO_BINDING_SENTINEL
        subset_prim.CreateAttribute(
            cls.SAVED_MATERIAL_ATTR, Sdf.ValueTypeNames.String, custom=True
        ).Set(saved_value)

        hide_material = cls._get_or_create_hide_material(subset_prim.GetParent())
        binding_api.Bind(hide_material)
        return True

    # ------------------------------------------------------------------ 이름 변경

    @classmethod
    def rename_subset(cls, subset_prim: Usd.Prim, new_name: str) -> "Usd.Prim | None":
        if not subset_prim or not subset_prim.IsValid() or not subset_prim.IsA(UsdGeom.Subset):
            print("[Subset] 유효하지 않은 subset prim.")
            return None

        new_name = new_name.strip()
        if not new_name:
            print("[Subset] 이름이 비어 있습니다.")
            return None

        safe_name = Tf.MakeValidIdentifier(new_name)
        if not safe_name:
            safe_name = "subset"

        old_path = subset_prim.GetPath()
        new_path = old_path.GetParentPath().AppendChild(safe_name)
        if new_path == old_path:
            return subset_prim

        stage = cls._get_stage()
        if stage.GetPrimAtPath(new_path).IsValid():
            print(f"[Subset] '{safe_name}' 이름이 이미 존재합니다.")
            return None

        # 여러 단계로 나뉜 namespace edit 알림을 하나로 묶어 뷰포트 깜빡임을 줄인다.
        with Sdf.ChangeBlock():
            success, _ = omni.kit.commands.execute(
                "MovePrim", path_from=str(old_path), path_to=str(new_path)
            )
        if not success:
            print(f"[Subset] '{old_path}' -> '{new_path}' 이름 변경 실패.")
            return None
        return stage.GetPrimAtPath(new_path)

    # ------------------------------------------------------------------ 뷰포트 피킹

    # ------------------------------------------------------------------ 병합

    @classmethod
    def merge_subsets(cls, mesh_prim: Usd.Prim, subset_prims: list) -> "Usd.Prim | None":
        """여러 subset의 face index를 첫 번째 subset에 합치고 나머지는 제거."""
        valid = [
            p for p in subset_prims
            if p and p.IsValid() and p.IsA(UsdGeom.Subset)
        ]
        if len(valid) < 2:
            print("[Subset] 합칠 subset이 2개 이상 필요합니다.")
            return None

        merged: set = set()
        for prim in valid:
            indices = UsdGeom.Subset(prim).GetIndicesAttr().Get()
            if indices:
                merged.update(int(i) for i in indices)

        keep = valid[0]
        UsdGeom.Subset(keep).GetIndicesAttr().Set(Vt.IntArray(sorted(merged)))

        stage = cls._get_stage()
        for prim in valid[1:]:
            stage.RemovePrim(prim.GetPath())
        print(f"[Subset] {len(valid)}개 subset 병합 -> {keep.GetName()} ({len(merged)} faces).")
        return keep

    @classmethod
    def build_face_subset_map(cls, mesh_prim: Usd.Prim) -> dict:
        """face index -> subset prim path. 여러 subset에 속하면 먼저 찾은 것 우선."""
        face_map: dict = {}
        for child in cls.list_subsets(mesh_prim):
            indices = UsdGeom.Subset(child).GetIndicesAttr().Get()
            if not indices:
                continue
            path = str(child.GetPath())
            for face in indices:
                face_map.setdefault(face, path)
        return face_map

    @classmethod
    def load_existing_subsets(cls, mesh_prim: Usd.Prim) -> "tuple[list[Usd.Prim], list[list[int]]]":
        """메시에 이미 존재하는 subset들을 (prims, groups) 형태로 읽어온다.

        이전에 Generate/Merge/Rename으로 만들어둔 subset들을 다시 UI 목록에
        불러올 때 사용. indices가 비어있는 subset은 건너뛴다.
        """
        prims: list = []
        groups: list = []
        for child in cls.list_subsets(mesh_prim):
            indices = UsdGeom.Subset(child).GetIndicesAttr().Get()
            if not indices:
                continue
            prims.append(child)
            groups.append([int(i) for i in indices])
        return prims, groups


    @classmethod
    def raycast_face(cls, mesh_prim: Usd.Prim, ray_origin: Gf.Vec3d, ray_dir: Gf.Vec3d) -> "int | None":
        """월드 좌표 레이와 메시의 각 면을 직접 교차 검사해 가장 가까운 face index 반환."""
        data = cls._get_mesh_data(mesh_prim)
        if data is None:
            return None
        points, counts, indices = data

        xform = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world_points = [xform.Transform(Gf.Vec3d(p)) for p in points]

        closest_face = None
        closest_t = None
        offset = 0
        for face, count in enumerate(counts):
            for i in range(1, count - 1):
                v0 = world_points[indices[offset]]
                v1 = world_points[indices[offset + i]]
                v2 = world_points[indices[offset + i + 1]]
                t = cls._ray_triangle_intersect(ray_origin, ray_dir, v0, v1, v2)
                if t is not None and (closest_t is None or t < closest_t):
                    closest_t = t
                    closest_face = face
            offset += count
        return closest_face

    @classmethod
    def face_centers_world(cls, mesh_prim: Usd.Prim) -> list:
        """면별 중심점(월드 좌표) 목록. 드래그 사각형 다중 선택용."""
        data = cls._get_mesh_data(mesh_prim)
        if data is None:
            return []
        points, counts, indices = data

        xform = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        centers = []
        offset = 0
        for count in counts:
            c = Gf.Vec3d(0, 0, 0)
            for i in range(count):
                c += Gf.Vec3d(points[indices[offset + i]])
            centers.append(xform.Transform(c / count))
            offset += count
        return centers

    @classmethod
    def face_normals_world(cls, mesh_prim: Usd.Prim) -> list:
        """면별 법선(월드 좌표, 정규화) 목록. 후면 판별용."""
        data = cls._get_mesh_data(mesh_prim)
        if data is None:
            return []
        points, counts, indices = data
        normals = cls._compute_face_normals(points, counts, indices)

        xform = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        origin = xform.Transform(Gf.Vec3d(0, 0, 0))
        world_normals = []
        for n in normals:
            wn = xform.Transform(Gf.Vec3d(n)) - origin
            length = wn.GetLength()
            world_normals.append(wn / length if length > 1e-9 else Gf.Vec3d(0, 0, 1))
        return world_normals

    @classmethod
    def face_at_point(cls, mesh_prim: Usd.Prim, world_point: Gf.Vec3d, spatial_index: "dict | None" = None) -> "int | None":
        """월드 좌표 점(RTX 레이캐스트 히트 위치)에 가장 가까운 face index 반환.

        spatial_index가 주어지면(build_face_spatial_index) 점 주변 셀의 face만
        검사하고, 정점 변환 결과도 캐시에서 꺼내 쓴다. 클릭 한 번에 모든 정점을
        변환하는 반복을 없애는 것이 핵심이다.
        """
        if spatial_index and "world_points" in spatial_index:
            world_points = spatial_index["world_points"]
            counts       = spatial_index["counts"]
            indices      = spatial_index["indices"]
            offsets      = spatial_index["offsets"]
        else:
            data = cls._get_mesh_data(mesh_prim)
            if data is None:
                return None
            points, counts, indices = data
            xform = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            world_points = [xform.Transform(Gf.Vec3d(p)) for p in points]
            offsets = cls._face_offsets(counts)

        candidate_faces = None
        if spatial_index:
            candidate_faces = cls._nearby_faces(world_point, spatial_index)
        if candidate_faces is None:
            candidate_faces = range(len(counts))

        best_face = None
        best_dist = None
        for face in candidate_faces:
            count = counts[face]
            offset = offsets[face]
            for i in range(1, count - 1):
                v0 = world_points[indices[offset]]
                v1 = world_points[indices[offset + i]]
                v2 = world_points[indices[offset + i + 1]]
                d = cls._point_triangle_distance(world_point, v0, v1, v2)
                if best_dist is None or d < best_dist:
                    best_dist = d
                    best_face = face

        if best_face is None and spatial_index:
            return cls.face_at_point(mesh_prim, world_point, spatial_index=None)
        return best_face

    @staticmethod
    def _face_offsets(counts) -> list:
        offsets = []
        offset = 0
        for c in counts:
            offsets.append(offset)
            offset += c
        return offsets

    @classmethod
    def build_face_spatial_index(cls, mesh_prim: Usd.Prim) -> "dict | None":
        """face 중심점 기반 uniform grid + 변환된 정점 캐시.

        face_at_point 가속용. world_points/counts/indices/offsets를 함께 저장해,
        클릭할 때마다 모든 정점을 변환하는 반복을 없앤다.
        """
        data = cls._get_mesh_data(mesh_prim)
        if data is None:
            return None
        points, counts, indices = data

        xform = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world_points = [xform.Transform(Gf.Vec3d(p)) for p in points]
        offsets = cls._face_offsets(counts)

        # face 중심점 계산 (world_points 재사용)
        centers = []
        for fi, count in enumerate(counts):
            c = Gf.Vec3d(0, 0, 0)
            off = offsets[fi]
            for j in range(count):
                c += world_points[indices[off + j]]
            centers.append(c / count)

        if not centers:
            return None

        min_b = Gf.Vec3d(centers[0])
        max_b = Gf.Vec3d(centers[0])
        for c in centers[1:]:
            for axis in range(3):
                min_b[axis] = min(min_b[axis], c[axis])
                max_b[axis] = max(max_b[axis], c[axis])

        diag = (max_b - min_b).GetLength()
        if diag < 1e-9:
            diag = 1.0
        cell_size = diag / max(1.0, len(centers) ** (1 / 3))
        if cell_size < 1e-9:
            cell_size = diag

        cells: dict = {}
        for fi, c in enumerate(centers):
            key = cls._cell_key(c, cell_size)
            cells.setdefault(key, []).append(fi)

        return {
            "cell_size":   cell_size,
            "cells":       cells,
            "world_points": world_points,
            "counts":      counts,
            "indices":     indices,
            "offsets":     offsets,
        }

    @staticmethod
    def _cell_key(point, cell_size) -> tuple:
        return (
            int(math.floor(point[0] / cell_size)),
            int(math.floor(point[1] / cell_size)),
            int(math.floor(point[2] / cell_size)),
        )

    @staticmethod
    def _nearby_faces(point, spatial_index) -> "list | None":
        cell_size = spatial_index["cell_size"]
        cells = spatial_index["cells"]
        cx, cy, cz = Subset._cell_key(point, cell_size)
        faces: list = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    faces.extend(cells.get((cx + dx, cy + dy, cz + dz), ()))
        return faces or None

    @staticmethod
    def _point_triangle_distance(p, v0, v1, v2) -> float:
        """점-삼각형 최단거리 (Ericson, Real-Time Collision Detection)."""
        ab = v1 - v0
        ac = v2 - v0
        ap = p - v0
        d1 = Gf.Dot(ab, ap)
        d2 = Gf.Dot(ac, ap)
        if d1 <= 0.0 and d2 <= 0.0:
            return (p - v0).GetLength()

        bp = p - v1
        d3 = Gf.Dot(ab, bp)
        d4 = Gf.Dot(ac, bp)
        if d3 >= 0.0 and d4 <= d3:
            return (p - v1).GetLength()

        vc = d1 * d4 - d3 * d2
        if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
            t = d1 / (d1 - d3)
            return (p - (v0 + ab * t)).GetLength()

        cp = p - v2
        d5 = Gf.Dot(ab, cp)
        d6 = Gf.Dot(ac, cp)
        if d6 >= 0.0 and d5 <= d6:
            return (p - v2).GetLength()

        vb = d5 * d2 - d1 * d6
        if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
            t = d2 / (d2 - d6)
            return (p - (v0 + ac * t)).GetLength()

        va = d3 * d6 - d5 * d4
        if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
            t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
            return (p - (v1 + (v2 - v1) * t)).GetLength()

        denom = 1.0 / (va + vb + vc)
        v = vb * denom
        w = vc * denom
        return (p - (v0 + ab * v + ac * w)).GetLength()

    @staticmethod
    def _ray_triangle_intersect(origin, direction, v0, v1, v2) -> "float | None":
        """Möller-Trumbore 알고리즘. 교차하면 origin에서의 거리(t), 아니면 None."""
        eps = 1e-9
        edge1 = v1 - v0
        edge2 = v2 - v0
        h = Gf.Cross(direction, edge2)
        a = Gf.Dot(edge1, h)
        if -eps < a < eps:
            return None
        f = 1.0 / a
        s = origin - v0
        u = f * Gf.Dot(s, h)
        if u < 0.0 or u > 1.0:
            return None
        q = Gf.Cross(s, edge1)
        v = f * Gf.Dot(direction, q)
        if v < 0.0 or u + v > 1.0:
            return None
        t = f * Gf.Dot(edge2, q)
        return t if t > eps else None

    # ------------------------------------------------------------------ 선택 강조 색상

    _BASE_COLOR = (0.18, 0.18, 0.18)  # UsdPreviewSurface 기본 diffuse와 동일
    _HIGHLIGHT_COLOR = (1.0, 0.2, 0.2)

    @classmethod
    def clear_group_colors(cls, mesh_prim: Usd.Prim) -> None:
        if not mesh_prim or not mesh_prim.IsValid():
            return
        mesh_prim.RemoveProperty("primvars:displayColor")

    @classmethod
    def highlight_selected(
        cls,
        mesh_prim: Usd.Prim,
        groups: list[list[int]],
        group_indices: "int | list[int] | None",
    ) -> None:
        """group_indices(단일 또는 목록)에 속한 face만 빨강으로, 나머지는 기본색으로 표시."""
        data = cls._get_mesh_data(mesh_prim)
        if data is None:
            return
        num_faces = len(data[1])

        if group_indices is None:
            group_indices = []
        elif isinstance(group_indices, int):
            group_indices = [group_indices]

        base = Gf.Vec3f(*cls._BASE_COLOR)
        face_colors = [base] * num_faces
        highlight = Gf.Vec3f(*cls._HIGHLIGHT_COLOR)
        for gi in group_indices:
            if groups and 0 <= gi < len(groups):
                for f in groups[gi]:
                    face_colors[f] = highlight

        primvars = UsdGeom.PrimvarsAPI(mesh_prim)
        pv = primvars.CreatePrimvar(
            "displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.uniform
        )
        pv.Set(Vt.Vec3fArray(face_colors))

    # ------------------------------------------------------------------ 내부 메서드

    @classmethod
    def _get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    @classmethod
    def _get_mesh_data(cls, mesh_prim: Usd.Prim):
        if not mesh_prim or not mesh_prim.IsValid():
            print("[Subset] 유효하지 않은 mesh prim.")
            return None
        mesh = UsdGeom.Mesh(mesh_prim)
        if not mesh:
            print("[Subset] mesh prim이 UsdGeomMesh가 아닙니다.")
            return None
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            print("[Subset] mesh 데이터가 비어 있습니다.")
            return None
        return points, counts, indices

    @staticmethod
    def _compute_face_normals(points, counts, indices) -> list[Gf.Vec3d]:
        """Newell's method로 면 법선 계산 (비평면 quad에도 안정적)."""
        normals = []
        offset = 0
        for count in counts:
            n = Gf.Vec3d(0, 0, 0)
            for i in range(count):
                p0 = Gf.Vec3d(points[indices[offset + i]])
                p1 = Gf.Vec3d(points[indices[offset + (i + 1) % count]])
                n[0] += (p0[1] - p1[1]) * (p0[2] + p1[2])
                n[1] += (p0[2] - p1[2]) * (p0[0] + p1[0])
                n[2] += (p0[0] - p1[0]) * (p0[1] + p1[1])
            length = n.GetLength()
            normals.append(n / length if length > 1e-9 else Gf.Vec3d(0, 0, 1))
            offset += count
        return normals

    @staticmethod
    def _build_adjacency(counts, indices) -> list[list[int]]:
        """변(edge)을 공유하는 면끼리 인접 리스트 구성."""
        edge_to_faces: dict = defaultdict(list)
        offset = 0
        for face, count in enumerate(counts):
            for i in range(count):
                v0 = indices[offset + i]
                v1 = indices[offset + (i + 1) % count]
                edge_to_faces[(min(v0, v1), max(v0, v1))].append(face)
            offset += count

        adjacency: list[list[int]] = [[] for _ in counts]
        for faces in edge_to_faces.values():
            for a in faces:
                for b in faces:
                    if a != b and b not in adjacency[a]:
                        adjacency[a].append(b)
        return adjacency

    @staticmethod
    def _merge_small_groups(
        groups: list[list[int]], adjacency: list[list[int]], min_faces: int
    ) -> list[list[int]]:
        """min_faces 미만 그룹을 경계를 가장 많이 공유하는 이웃 그룹에 병합."""
        face_to_group = {}
        for gi, group in enumerate(groups):
            for f in group:
                face_to_group[f] = gi

        # 작은 그룹부터 처리 (병합 결과가 다음 판정에 반영되도록 매번 갱신)
        for gi in sorted(range(len(groups)), key=lambda g: len(groups[g])):
            group = groups[gi]
            if not group or len(group) >= min_faces:
                continue
            boundary_count: dict[int, int] = defaultdict(int)
            for f in group:
                for nb in adjacency[f]:
                    nb_group = face_to_group[nb]
                    if nb_group != gi:
                        boundary_count[nb_group] += 1
            if not boundary_count:
                continue
            target = max(boundary_count, key=boundary_count.get)
            groups[target].extend(group)
            for f in group:
                face_to_group[f] = target
            groups[gi] = []

        return [g for g in groups if g]

    @staticmethod
    def _merge_groups_by_normal(
        groups: list[list[int]], normals: list[Gf.Vec3d], cos_threshold: float
    ) -> list[list[int]]:
        """서로 떨어져 있어도 평균 법선이 cos_threshold 이내로 같은 방향이면 병합."""
        n = len(groups)
        avg_normals = []
        for group in groups:
            total = Gf.Vec3d(0, 0, 0)
            for f in group:
                total += normals[f]
            length = total.GetLength()
            avg_normals.append(total / length if length > 1e-9 else Gf.Vec3d(0, 0, 1))

        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(n):
            for j in range(i + 1, n):
                if Gf.Dot(avg_normals[i], avg_normals[j]) >= cos_threshold:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj

        merged: dict[int, list[int]] = defaultdict(list)
        for i, group in enumerate(groups):
            merged[find(i)].extend(group)
        return list(merged.values())
