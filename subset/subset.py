import math
import colorsys
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
    ) -> list[list[int]]:
        """이웃 면 간 법선 각도 차이 기반 region growing으로 면 그룹 분류.

        직육면체 → 6그룹, 꼭짓점 잘린 박스 → 7그룹, 원기둥 → 3그룹.
        min_faces 미만인 그룹은 가장 많이 맞닿은 이웃 그룹에 병합.
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

        groups.sort(key=len, reverse=True)
        return groups

    @classmethod
    def generate_subsets(
        cls,
        mesh_prim: Usd.Prim,
        threshold_deg: float = 30.0,
        min_faces: int = 1,
    ) -> "tuple[list[Usd.Prim], list[list[int]]]":
        """면 분류 후 그룹별 GeomSubset 생성. 기존 자동 생성분은 제거."""
        groups = cls.classify_faces(mesh_prim, threshold_deg, min_faces)
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

        success, _ = omni.kit.commands.execute(
            "MovePrim", path_from=str(old_path), path_to=str(new_path)
        )
        if not success:
            print(f"[Subset] '{old_path}' -> '{new_path}' 이름 변경 실패.")
            return None
        return stage.GetPrimAtPath(new_path)

    # ------------------------------------------------------------------ 뷰포트 피킹

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

    # ------------------------------------------------------------------ 디버그 색상

    @staticmethod
    def group_colors(count: int) -> list[tuple[float, float, float]]:
        # 황금비 간격 hue → 그룹 수와 무관하게 서로 구분되는 색
        colors = []
        for i in range(count):
            hue = (i * 0.61803398875) % 1.0
            colors.append(colorsys.hsv_to_rgb(hue, 0.75, 0.95))
        return colors

    @classmethod
    def apply_group_colors(cls, mesh_prim: Usd.Prim, groups: list[list[int]]) -> None:
        """그룹별 색을 per-face displayColor primvar로 기록 (시각 확인용)."""
        data = cls._get_mesh_data(mesh_prim)
        if data is None or not groups:
            return
        num_faces = len(data[1])

        colors = cls.group_colors(len(groups))
        face_colors = [Gf.Vec3f(0.5, 0.5, 0.5)] * num_faces
        for gi, group in enumerate(groups):
            c = Gf.Vec3f(*colors[gi])
            for f in group:
                face_colors[f] = c

        primvars = UsdGeom.PrimvarsAPI(mesh_prim)
        pv = primvars.CreatePrimvar(
            "displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.uniform
        )
        pv.Set(Vt.Vec3fArray(face_colors))

    @classmethod
    def clear_group_colors(cls, mesh_prim: Usd.Prim) -> None:
        if not mesh_prim or not mesh_prim.IsValid():
            return
        mesh_prim.RemoveProperty("primvars:displayColor")

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
