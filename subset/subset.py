from pxr import Usd, UsdGeom, Vt
import omni.usd


class Subset:

    _initialized: bool = False

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
        )
        return subset.GetPrim()

    # ------------------------------------------------------------------ 내부 메서드

    @classmethod
    def _get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()
