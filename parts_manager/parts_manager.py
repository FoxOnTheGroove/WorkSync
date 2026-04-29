from pxr import Usd, UsdGeom
import omni.usd
import omni.kit.app
import carb.events
from dataclasses import dataclass
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from morph.hytwin_usd_loader_extension import get_instance as get_loader_instance

__all__ = ["PartsManager", "PrimNode", "PrimNodeInfo"]


@dataclass
class PrimNodeInfo:
    """외부 공개용 노드 정보. USD 내부 객체 미포함."""
    name: str
    index_key: str
    depth: int
    is_visible: bool
    is_leaf: bool
    children: list  # list[PrimNodeInfo]


@dataclass
class PrimNode:
    prim: object  # Usd.Prim
    path: str
    name: str
    depth: int
    is_part: bool
    children: list
    is_leaf: bool
    index_key: str  # 구조 기반 위치 키 (예: "vid", "vid_2", "vid_0_1")
    is_visible: bool = True


class PartsManager:

    _sync_enabled: bool = False
    _trees: dict = {}              # viewport_id(str) -> list[PrimNode]
    _node_map: dict = {}           # index_key -> PrimNode
    _viewport_key_map: dict = {}   # viewport_id(str) -> index_key(str)
    _active_viewport_id = None
    _on_orbit_event_click = None
    _on_orbit_event_drag_start = None

    # ── 공개 API ─────────────────────────────────────────────────────────────

    @classmethod
    def initialize(cls) -> None:
        bus = omni.kit.app.get_app().get_message_bus_event_stream()
        cls._on_orbit_event_click = bus.create_subscription_to_pop_by_type(
            carb.events.type_from_string("hytwin_orbit_extension:gesture:click"),
            cls.set_active_viewport,
        )
        cls._on_orbit_event_drag_start = bus.create_subscription_to_pop_by_type(
            carb.events.type_from_string("hytwin_orbit_extension:gesture:drag:start"),
            cls.set_active_viewport,
        )
        cls.make_tree()

    @classmethod
    def make_tree(cls) -> None:
        """모든 뷰포트 프림 트리를 빌드해 _node_map, _viewport_key_map 초기화."""
        stage = cls._get_stage()
        if stage is None:
            return
        prim_config = get_loader_instance()._loaded_prim_config
        cls._trees = {}
        cls._viewport_key_map = {}
        all_roots = []
        for vph in hytwin_vp_wg.ViewportWidgetHost().get_instances():
            camera_path = vph.viewport.viewport_api.camera_path
            cam_prim = stage.GetPrimAtPath(camera_path)
            if not cam_prim.IsValid():
                continue
            cam_name = cam_prim.GetName()
            try:
                prim_name = prim_config[cam_name]
            except (KeyError, TypeError):
                continue
            if not prim_name:
                continue
            prim = stage.GetPrimAtPath(prim_name)
            if not prim.IsValid():
                continue
            vid = str(vph.viewport.viewport_api.id)
            node = cls._build_subtree(prim, depth=0, sibling_index=vid, parent_key="")
            cls._trees[vid] = [node]
            cls._viewport_key_map[vid] = node.index_key
            all_roots.append(node)
        cls._node_map = {}
        cls._build_node_map(all_roots)

    @classmethod
    def get_prim_tree(cls) -> "list[PrimNodeInfo]":
        """_active_viewport_id 기준 해당 뷰포트의 PrimNodeInfo 리스트 반환."""
        return cls.get_prim_tree_by_id(cls._active_viewport_id)

    @classmethod
    def get_prim_tree_by_id(cls, vp_id) -> "list[PrimNodeInfo]":
        """viewport_id에 대응하는 PrimNodeInfo 리스트 반환. 없으면 []."""
        if vp_id is None:
            return []
        nodes = cls._trees.get(str(vp_id), [])
        return [cls._to_info(n) for n in nodes]

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
                node.is_visible = visible

    @classmethod
    def set_sync(cls, enabled: bool) -> None:
        """sync 활성화 여부 설정. True 시 _active_viewport_id 기준으로 즉시 동기화."""
        cls._sync_enabled = enabled
        if enabled:
            cls._immediate_sync()

    @classmethod
    def set_active_viewport(cls, event: carb.events.IEvent) -> None:
        cls._active_viewport_id = event.payload["viewport_api_id"]

    # ── 보조 API ─────────────────────────────────────────────────────────────

    @classmethod
    def get_part_by_viewport(cls, viewport_id) -> "PrimNode | None":
        """뷰포트 ID에 대응하는 최상위 파츠 PrimNode를 반환."""
        key = cls._resolve_key_from_viewport(viewport_id)
        return cls._node_map.get(key)

    @classmethod
    def get_load_prim_names(cls) -> list[str]:
        """로드된 최상위 파츠 이름 목록을 반환."""
        return [n.name for n in cls._node_map.values() if n.depth == 0]

    @classmethod
    def get_load_prims(cls) -> list:
        """로드된 최상위 파츠 프림 객체 목록을 반환."""
        return [n.prim for n in cls._node_map.values() if n.depth == 0]

    @classmethod
    def get_load_prim_paths(cls) -> list[str]:
        """로드된 최상위 파츠 SdfPath(문자열) 목록을 반환."""
        return [n.path for n in cls._node_map.values() if n.depth == 0]

    # ── 내부 ─────────────────────────────────────────────────────────────────

    @classmethod
    def _to_info(cls, node: "PrimNode") -> PrimNodeInfo:
        return PrimNodeInfo(
            name=node.name,
            index_key=node.index_key,
            depth=node.depth,
            is_visible=node.is_visible,
            is_leaf=node.is_leaf,
            children=[cls._to_info(c) for c in node.children],
        )

    @classmethod
    def _get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    _EXCLUDED_TYPES = {"Material", "Shader", "NodeGraph", "GeomSubset"}

    @classmethod
    def _is_excluded(cls, prim: Usd.Prim) -> bool:
        type_name = prim.GetTypeName()
        return type_name.endswith("Light") or type_name in cls._EXCLUDED_TYPES

    @classmethod
    def _build_subtree(cls, prim: Usd.Prim, depth: int, sibling_index, parent_key: str = "") -> PrimNode:
        key = f"{parent_key}_{sibling_index}" if parent_key else str(sibling_index)
        path = str(prim.GetPath())
        eligible = [c for c in prim.GetChildren() if not cls._is_excluded(c)]
        children = [
            cls._build_subtree(child, depth + 1, i, key)
            for i, child in enumerate(eligible)
        ]
        return PrimNode(
            prim=prim,
            path=path,
            name=prim.GetName(),
            depth=depth,
            is_part=(depth == 0),
            children=children,
            is_leaf=(len(children) == 0),
            index_key=key,
            is_visible=cls._compute_visibility(path),
        )

    @classmethod
    def _immediate_sync(cls) -> None:
        """_active_viewport_id 기준 타겟 프림을 찾아 파츠 전체를 즉시 동기화."""
        reference_key = cls._resolve_key_from_viewport(cls._active_viewport_id)
        if reference_key is None:
            return
        ref_node = cls._node_map.get(reference_key)
        if ref_node is None:
            return
        prefix = reference_key + "_"
        subtree_keys = [reference_key] + [k for k in cls._node_map if k.startswith(prefix)]
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
    def _resolve_key_from_viewport(cls, viewport_id) -> "str | None":
        return cls._viewport_key_map.get(str(viewport_id)) if viewport_id is not None else None

    @classmethod
    def _build_node_map(cls, nodes: list) -> None:
        for node in nodes:
            cls._node_map[node.index_key] = node
            if not node.is_leaf:
                cls._build_node_map(node.children)

    @classmethod
    def _resolve_targets(cls, index_key: str) -> list[str]:
        """sync 대상 index_key 목록 반환. 파츠 레벨이면 전체 파츠, 하위면 상대 위치로 매핑."""
        part_keys = list(cls._viewport_key_map.values())
        if index_key in part_keys:
            return part_keys
        for pk in part_keys:
            if index_key.startswith(pk + "_"):
                rel_key = index_key[len(pk) + 1:]
                return [f"{p}_{rel_key}" for p in part_keys if f"{p}_{rel_key}" in cls._node_map]
        return [index_key]

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
