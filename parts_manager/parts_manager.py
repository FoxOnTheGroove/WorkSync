from pxr import Usd, UsdGeom, UsdShade, Tf
import omni.usd
import omni.kit.app
import carb.events
from dataclasses import dataclass
import morph.hytwin_viewportwidget_extension as hytwin_vp_wg
from morph.hytwin_usd_loader_extension import get_instance as get_loader_instance

__all__ = ["PartsManager", "PrimNode"]


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
    material_key: "str | None" = None


class PartsManager:

    _sync_enabled: bool = False
    _MAX_DEPTH: int = 3
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
    def get_prim_tree(cls) -> "dict | None":
        """_active_viewport_id 기준 최상위 프림의 자식 노드 페이로드 반환."""
        return cls.get_prim_tree_by_id(cls._active_viewport_id)

    @classmethod
    def get_prim_tree_by_id(cls, vp_id) -> "dict | None":
        """viewport_id 최상위 프림 기준 페이로드 반환.
        하부에 메시가 있는 자식이 정확히 1개면 그 자식을, 그 외(0개/여러개)면 최상위 프림을 반환."""
        if vp_id is None:
            return None
        nodes = cls._trees.get(str(vp_id), [])
        if not nodes:
            return None
        target = nodes[0]
        while True:
            mesh_children = [
                c for c in target.children
                if any(p.GetTypeName() == "Mesh" for p in Usd.PrimRange(c.prim))
            ]
            if len(mesh_children) != 1:
                break
            target = mesh_children[0]
        if len(mesh_children) >= 10:
            # (임시) 자식이 너무 많으면 트리에서 제거해 target만 노출
            target.children = [c for c in target.children if c not in mesh_children]
            target.is_leaf = not target.children
        return cls._to_payload(target, depth_offset=target.depth)

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
    def get_material(cls, index_key: str) -> "str | None":
        """index_key 노드에 적용된 material_key를 반환."""
        node = cls._node_map.get(index_key)
        return node.material_key if node else None

    @classmethod
    def set_material(cls, index_key: str, key: "str | None") -> None:
        """index_key 노드에 마테리얼을 적용하고 material_key를 기록.
        노드 이하(재귀) 모든 Mesh에 동일 마테리얼을 바인딩한다."""
        node = cls._node_map.get(index_key)
        if node is None:
            return
        stage = cls._get_stage()
        if stage is None:
            return
        # 이전 마테리얼 prim 제거 (reference 소스면 RemovePrim 실패 → SetActive(False) fallback)
        looks = stage.GetPrimAtPath(f"{node.path}/Looks")
        if looks.IsValid():
            for child in looks.GetChildren():
                if not stage.RemovePrim(child.GetPath()):
                    child.SetActive(False)
        node.material_key = key
        meshes = [p for p in Usd.PrimRange(node.prim) if p.GetTypeName() == "Mesh"]
        if not meshes:
            return
        if key is None:
            for prim in meshes:
                UsdShade.MaterialBindingAPI(prim).UnbindAllBindings()
            return
        url = cls._get_material_url(key)
        mtl_path = f"{node.path}/Looks/{Tf.MakeValidIdentifier(key)}"
        mtl_prim = stage.GetPrimAtPath(mtl_path)
        if not mtl_prim.IsValid():
            mtl_prim = stage.DefinePrim(mtl_path, "Material")
            mtl_prim.GetReferences().AddReference(url)
        material = UsdShade.Material(mtl_prim)
        for prim in meshes:
            UsdShade.MaterialBindingAPI(prim).Bind(material)

    @classmethod
    def save_material_eqp(cls) -> None:
        """material_key가 있는 모든 노드를 원본 .usd별로 묶어, 원본을 URL당 한 번만 열어
        전부 적용 후 저장(덮어쓰기)한다. 작업 스테이지와 무관."""
        # 원본 URL별 노드 그루핑
        groups: dict = {}
        for node in cls._node_map.values():
            if node.material_key:
                groups.setdefault(cls._get_origin_url(node), []).append(node)

        for url, nodes in groups.items():
            src_stage = Usd.Stage.Open(url)
            if src_stage is None:
                continue
            default = src_stage.GetDefaultPrim()
            if not default:
                continue
            dst_root = str(default.GetPath())
            for node in nodes:
                # 작업 스테이지 뷰포트 루트 경로 → 원본 defaultPrim 으로 상대 변환
                vid = node.index_key.split("_")[0]
                root_node = cls._trees[vid][0]
                rel = node.path[len(root_node.path):]      # "" 또는 "/Sub/Mesh..."
                dst_node = src_stage.GetPrimAtPath(dst_root + rel)
                if not dst_node.IsValid():
                    continue
                # 원본은 root layer이므로 RemovePrim으로 기존 Looks 자식 전체 제거
                looks_prim = src_stage.GetPrimAtPath(f"{dst_node.GetPath()}/Looks")
                if looks_prim.IsValid():
                    for child in looks_prim.GetChildren():
                        src_stage.RemovePrim(child.GetPath())
                mtl_path = f"{dst_node.GetPath()}/Looks/{Tf.MakeValidIdentifier(node.material_key)}"
                mtl_prim = src_stage.DefinePrim(mtl_path, "Material")
                mtl_prim.GetReferences().AddReference(cls._get_material_url(node.material_key))
                material = UsdShade.Material(mtl_prim)
                for prim in Usd.PrimRange(dst_node):
                    if prim.GetTypeName() == "Mesh":
                        UsdShade.MaterialBindingAPI(prim).Bind(material)
            src_stage.GetRootLayer().Save()

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
    def get_node_by_path(cls, path: str) -> "PrimNode | None":
        """path에 해당하는 노드를 트리에서 찾아 반환. 없으면 가장 가까운 조상 노드를 반환."""
        nodes_by_path = {node.path: node for node in cls._node_map.values()}
        p = path
        while p:
            node = nodes_by_path.get(p)
            if node is not None:
                return node
            p = p.rsplit("/", 1)[0]
        return None

    @classmethod
    def get_load_prim_paths(cls) -> list[str]:
        """로드된 최상위 파츠 SdfPath(문자열) 목록을 반환."""
        return [n.path for n in cls._node_map.values() if n.depth == 0]

    # ── 내부 ─────────────────────────────────────────────────────────────────

    @classmethod
    def _to_payload(cls, node: "PrimNode", depth_offset: int = 0) -> dict:
        return {
            "name": node.name,
            "index_key": node.index_key,
            "depth": node.depth - depth_offset,
            "is_visible": node.is_visible,
            "is_leaf": node.is_leaf,
            "children": [cls._to_payload(c, depth_offset) for c in node.children],
        }

    @classmethod
    def _get_stage(cls) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    @classmethod
    def _get_material_url(cls, key: str) -> str:
        """material_key로부터 Nucleus 상의 .usd URL을 반환. (직접 구현 예정)"""
        raise NotImplementedError

    @classmethod
    def _get_origin_url(cls, node: "PrimNode") -> str:
        """노드의 원본 .usd Nucleus URL을 반환. (직접 구현 예정, 일단 하드코딩)"""
        raise NotImplementedError

    _EXCLUDED_TYPES = {"Material", "Shader", "NodeGraph", "GeomSubset", "LineRenderer"}

    @classmethod
    def _is_excluded(cls, prim: Usd.Prim) -> bool:
        type_name = prim.GetTypeName()
        if type_name.endswith("Light") or type_name in cls._EXCLUDED_TYPES:
            return True
        if type_name == "Xform" and not prim.GetChildren():
            return True
        return False

    @classmethod
    def _build_subtree(cls, prim: Usd.Prim, depth: int, sibling_index, parent_key: str = "") -> PrimNode:
        key = f"{parent_key}_{sibling_index}" if parent_key else str(sibling_index)
        path = str(prim.GetPath())
        eligible = [] if depth >= cls._MAX_DEPTH else [c for c in prim.GetChildren() if not cls._is_excluded(c)]
        children = [
            cls._build_subtree(child, depth + 1, i, key)
            for i, child in enumerate(eligible)
        ]
        if len(children) == 1 and children[0].is_leaf:
            children = []
        # 저장된 마테리얼 복원: Looks 자식이 있으면 그 이름을 material_key로
        looks = prim.GetChild("Looks")
        material_key = looks.GetChildren()[0].GetName() if looks.IsValid() and looks.GetChildren() else None
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
            material_key=material_key,
        )

    @classmethod
    def _immediate_sync(cls) -> None:
        """_active_viewport_id 기준 파츠 전체를 즉시 동기화."""
        reference_key = cls._resolve_key_from_viewport(cls._active_viewport_id)
        if reference_key is None:
            return
        prefix = reference_key + "_"
        subtree_keys = [reference_key] + [k for k in cls._node_map if k.startswith(prefix)]
        for key in subtree_keys:
            node = cls._node_map.get(key)
            if node is None:
                continue
            for target_key in cls._resolve_targets(key):
                if target_key == key:
                    continue
                target_node = cls._node_map.get(target_key)
                if target_node:
                    cls._apply_visibility(target_node.path, node.is_visible)
                    target_node.is_visible = node.is_visible

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
        vis_attr = imageable.GetVisibilityAttr()
        return vis_attr.Get() != UsdGeom.Tokens.invisible if vis_attr else True

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
        # MakeVisible/MakeInvisible은 조상까지 순회 수정하므로 직접 attr 설정
        imageable.GetVisibilityAttr().Set(
            UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible
        )
