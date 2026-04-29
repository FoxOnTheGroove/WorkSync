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

    _sync_enabled: bool = False
    _node_map: dict = {}  # index_key -> PrimNode (get_prim_tree 호출 시 갱신)

    # ── 공개 API ─────────────────────────────────────────────────────────────

    @classmethod
    def get_prim_tree(cls) -> list:
        """load_prims 아래 전체 계층을 PrimNode 트리로 반환하고 내부 캐시를 갱신."""
        stage = cls._get_stage()
        if stage is None:
            return []
        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []
        tree = [
            cls._build_subtree(child, depth=0, sibling_index=i, parent_key="")
            for i, child in enumerate(load_prims_prim.GetChildren())
        ]
        cls._node_map = {}
        cls._build_node_map(tree)
        return tree

    @classmethod
    def get_visibility(cls, index_key: str) -> bool:
        """index_key 위치 프림의 가시성을 반환. 상속 반영."""
        node = cls._node_map.get(index_key)
        if node is None:
            return True
        return cls._compute_visibility(node.path)

    @classmethod
    def set_visibility(cls, index_key: str, visible: bool) -> None:
        """index_key 위치 프림의 가시성을 설정. sync ON 시 동일 구조 위치 전체에 적용."""
        targets = cls._resolve_targets(index_key) if cls._sync_enabled else [index_key]
        for key in targets:
            node = cls._node_map.get(key)
            if node:
                cls._apply_visibility(node.path, visible)

    @classmethod
    def set_sync(cls, enabled: bool, reference_key: str = None) -> None:
        """sync 활성화 여부 설정. enabled=True이고 reference_key 제공 시 즉시 동기화."""
        cls._sync_enabled = enabled
        if enabled and reference_key is not None:
            cls._immediate_sync(reference_key)

    # ── 보조 API ─────────────────────────────────────────────────────────────

    @classmethod
    def get_load_prim_names(cls) -> list[str]:
        """load_prims 아래 직계 자식 프림의 이름 목록을 반환."""
        stage = cls._get_stage()
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
        stage = cls._get_stage()
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
        stage = cls._get_stage()
        if stage is None:
            return []
        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []
        return [str(child.GetPath()) for child in load_prims_prim.GetChildren()]

    # ── 내부 ─────────────────────────────────────────────────────────────────

    @classmethod
    def _get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

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
    def _immediate_sync(cls, reference_key: str) -> None:
        """reference_key 기준으로 동일 구조 위치 전체에 즉시 가시성 동기화.
        파츠 레벨(depth=0)이면 해당 파츠 전체 노드를 기준으로 적용."""
        ref_node = cls._node_map.get(reference_key)
        if ref_node is None:
            return

        if ref_node.depth == 0:
            prefix = reference_key + "_"
            subtree_keys = [reference_key] + [k for k in cls._node_map if k.startswith(prefix)]
        else:
            subtree_keys = [reference_key]

        for key in subtree_keys:
            node = cls._node_map.get(key)
            if node is None:
                continue
            vis = cls._compute_visibility(node.path)
            for target_key in cls._resolve_targets(key):
                if target_key == key:
                    continue
                target_node = cls._node_map.get(target_key)
                if target_node:
                    cls._apply_visibility(target_node.path, vis)

    @classmethod
    def _build_node_map(cls, nodes: list) -> None:
        for node in nodes:
            cls._node_map[node.index_key] = node
            if not node.is_leaf:
                cls._build_node_map(node.children)

    @classmethod
    def _resolve_targets(cls, index_key: str) -> list[str]:
        """sync 대상 index_key 목록 반환. 파츠 레벨이면 전체 파츠, 하위면 상대 위치로 매핑."""
        segments = index_key.split("_")
        part_keys = [k for k in cls._node_map if "_" not in k]
        if len(segments) == 1:
            return part_keys
        rel_key = "_".join(segments[1:])
        return [f"{p}_{rel_key}" for p in part_keys if f"{p}_{rel_key}" in cls._node_map]

    @classmethod
    def _compute_visibility(cls, path: str) -> bool:
        stage = cls._get_stage()
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
    def _apply_visibility(cls, path: str, visible: bool) -> None:
        stage = cls._get_stage()
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
