from pxr import Usd, Sdf, UsdGeom
import omni.usd
from dataclasses import dataclass

__all__ = ["PartsManager", "PrimNode", "LOAD_PRIMS_PATH"]

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
    index_key: str  # 구조 기반 위치 키 (예: "0", "0_2", "1_0_1")


class PartsManager:

    @classmethod
    def initialize(cls) -> None:
        """확장 시작 시 호출. 추후 _instance 등 클래스 레벨 상태 초기화에 사용."""
        pass

    @classmethod
    def get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    @classmethod
    def get_load_prim_names(cls) -> list[str]:
        """load_prims 아래 직계 자식 프림의 이름 목록을 반환."""
        stage = cls.get_stage()
        if stage is None:
            return []
        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []
        return [child.GetName() for child in load_prims_prim.GetChildren()]

    @classmethod
    def get_load_prims(cls) -> list:
        """load_prims 아래 직계 자식 프림 객체 목록을 반환."""
        stage = cls.get_stage()
        if stage is None:
            return []
        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []
        return list(load_prims_prim.GetChildren())

    @classmethod
    def get_load_prim_paths(cls) -> list[str]:
        """load_prims 아래 직계 자식 프림의 전체 SdfPath(문자열) 목록을 반환."""
        stage = cls.get_stage()
        if stage is None:
            return []
        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []
        return [str(child.GetPath()) for child in load_prims_prim.GetChildren()]

    @classmethod
    def get_prim_tree(cls) -> list:
        """load_prims 아래 전체 계층을 PrimNode 트리로 반환."""
        stage = cls.get_stage()
        if stage is None:
            return []
        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []
        return [
            cls._build_subtree(child, depth=0, sibling_index=i, parent_key="")
            for i, child in enumerate(load_prims_prim.GetChildren())
        ]

    @classmethod
    def _build_subtree(cls, prim: Usd.Prim, depth: int, sibling_index: int, parent_key: str = "") -> PrimNode:
        key = f"{parent_key}_{sibling_index}" if parent_key else str(sibling_index)
        children = [
            cls._build_subtree(child, depth + 1, i, key)
            for i, child in enumerate(prim.GetChildren())
        ]
        return PrimNode(
            prim=prim,
            path=str(prim.GetPath()),
            name=prim.GetName(),
            depth=depth,
            is_part=(depth == 0),
            children=children,
            is_leaf=(len(children) == 0),
            index_key=key,
        )

    @classmethod
    def get_visibility(cls, path: str) -> bool:
        """ComputeVisibility()로 상속을 반영한 실제 가시성을 반환."""
        stage = cls.get_stage()
        if stage is None:
            return True
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            return True
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            return True
        return imageable.ComputeVisibility() != UsdGeom.Tokens.invisible

    @classmethod
    def set_visibility(cls, path: str, visible: bool) -> None:
        """대상 프림의 가시성을 설정."""
        stage = cls.get_stage()
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
