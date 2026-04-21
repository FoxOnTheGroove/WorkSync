from pxr import Usd, Sdf, UsdGeom
import omni.usd
from dataclasses import dataclass, field


# load_prims 경로: 루트 아래 고정
LOAD_PRIMS_PATH = "/World/load_prims"  # 실제 환경에 따라 /Root/load_prims 로 변경


@dataclass
class PrimNode:
    prim: object  # Usd.Prim
    path: str
    name: str
    depth: int
    is_part: bool
    children: list
    is_leaf: bool


class PartsManager:

    def get_stage(self) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    def get_load_prim_names(self) -> list[str]:
        """
        load_prims 아래 직계 자식 프림의 이름(name, 경로 아님) 목록을 반환.
        load_prims 프림이 없으면 빈 리스트 반환.
        """
        stage = self.get_stage()
        if stage is None:
            return []

        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []

        names = [child.GetName() for child in load_prims_prim.GetChildren()]
        return names

    def get_load_prim_paths(self) -> list[str]:
        """
        load_prims 아래 직계 자식 프림의 전체 SdfPath(문자열) 목록을 반환.
        """
        stage = self.get_stage()
        if stage is None:
            return []

        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []

        paths = [str(child.GetPath()) for child in load_prims_prim.GetChildren()]
        return paths

    def get_prim_tree(self) -> list:
        """load_prims 아래 전체 계층을 PrimNode 트리로 반환."""
        stage = self.get_stage()
        if stage is None:
            return []

        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []

        return [self._build_subtree(child, depth=0) for child in load_prims_prim.GetChildren()]

    def _build_subtree(self, prim: Usd.Prim, depth: int) -> PrimNode:
        children = [self._build_subtree(child, depth + 1) for child in prim.GetChildren()]
        return PrimNode(
            prim=prim,
            path=str(prim.GetPath()),
            name=prim.GetName(),
            depth=depth,
            is_part=(depth == 0),
            children=children,
            is_leaf=(len(children) == 0),
        )

    def get_visibility(self, path: str) -> bool:
        """ComputeVisibility()로 상속을 반영한 실제 가시성을 반환."""
        stage = self.get_stage()
        if stage is None:
            return True
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            return True
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            return True
        return imageable.ComputeVisibility() != UsdGeom.Tokens.invisible

    def set_visibility(self, path: str, visible: bool) -> None:
        """대상 프림의 가시성을 설정."""
        stage = self.get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            return
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            return
        if visible:
            imageable.MakeVisible()
        else:
            imageable.MakeInvisible()
