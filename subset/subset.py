import math
import colorsys
from collections import defaultdict, deque

from pxr import Usd, UsdGeom, Vt, Gf, Sdf
import omni.usd


class Subset:

    _initialized: bool = False

    FAMILY_NAME = "surfaces"   # 자동 생성 subset들의 familyName

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
