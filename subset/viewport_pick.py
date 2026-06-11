import traceback

import omni.ui.scene as sc
import omni.kit.app
import omni.usd
from pxr import Gf
from omni.kit.viewport.utility import get_active_viewport, get_active_viewport_window

from .subset import Subset


def _log(msg: str) -> None:
    print(f"[Subset][Pick] {msg}")


class ViewportPicker:
    """체크박스 ON 시, 뷰포트 클릭 위치의 면이 속한 subset을 우선 선택.

    뷰포트 위에 투명한 omni.ui.scene Screen을 덮고 ClickGesture로 클릭을 받는다
    (carb.input의 device-level 마우스 구독은 omni.ui 위젯 위에서는 호출되지 않음).
    레이 교차는 omni.kit.raycast.query(RTX 기반, 콜라이더 불필요)로 수행하고,
    히트 위치를 face index로 역산해 subset을 찾는다.
    클릭한 면이 어떤 subset에도 속하지 않으면, 클릭된 prim을 그대로 선택해
    기존 뷰포트 클릭 동작을 흉내낸다.
    """

    def __init__(self, get_mesh_prim):
        self._get_mesh_prim = get_mesh_prim
        self._raycast = None
        self._frame = None
        self._scene_view = None

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._subscribe()
        else:
            self._unsubscribe()

    def destroy(self) -> None:
        self._unsubscribe()

    # ------------------------------------------------------------------ 내부

    def _subscribe(self) -> None:
        if self._scene_view:
            return
        try:
            self._raycast = self._acquire_raycast_interface()

            viewport_window = get_active_viewport_window()
            if not viewport_window:
                _log("active viewport window 없음 -> 구독 실패")
                return

            self._frame = viewport_window.get_frame("subset_pick_overlay")
            with self._frame:
                self._scene_view = sc.SceneView()
                with self._scene_view.scene:
                    sc.Screen(gesture=sc.ClickGesture(self._on_click))
            _log("구독 시작 (ClickGesture)")
        except Exception:
            _log("구독 실패:")
            traceback.print_exc()
            self._scene_view = None
            self._frame = None

    def _unsubscribe(self) -> None:
        if self._frame:
            self._frame.clear()
            _log("구독 해제")
        self._frame = None
        self._scene_view = None

    @staticmethod
    def _acquire_raycast_interface():
        try:
            import omni.kit.raycast.query
        except ImportError:
            # 익스텐션이 꺼져 있으면 즉시 활성화 후 재시도
            manager = omni.kit.app.get_app().get_extension_manager()
            manager.set_extension_enabled_immediate("omni.kit.raycast.query", True)
            import omni.kit.raycast.query
        return omni.kit.raycast.query.acquire_raycast_query_interface()

    def _on_click(self, sender) -> None:
        try:
            self._handle_click(sender)
        except Exception:
            _log("_on_click 예외:")
            traceback.print_exc()

    def _handle_click(self, sender) -> None:
        payload = sender.gesture_payload
        _log(f"클릭 감지, payload attrs={[a for a in dir(payload) if not a.startswith('_')]}")

        mesh_prim = self._get_mesh_prim()
        if not mesh_prim or not mesh_prim.IsValid():
            _log("mesh_prim 없음 -> 무시")
            return

        viewport_api = get_active_viewport()
        if not viewport_api:
            _log("active viewport 없음 -> 무시")
            return

        ndc_x, ndc_y = payload.mouse
        _log(f"클릭 NDC=({ndc_x}, {ndc_y})")

        origin, direction = self._compute_ray(viewport_api, ndc_x, ndc_y)
        _log(f"ray origin={tuple(origin)} dir={tuple(direction)}")

        import omni.kit.raycast.query as rq
        ray = rq.Ray(tuple(origin), tuple(direction))
        self._raycast.submit_raycast_query(ray, self._make_on_hit(mesh_prim))
        _log("raycast 쿼리 제출")

    def _make_on_hit(self, mesh_prim):
        def _on_hit(ray, result) -> None:
            try:
                self._handle_hit(mesh_prim, result)
            except Exception:
                _log("_on_hit 예외:")
                traceback.print_exc()
        return _on_hit

    def _handle_hit(self, mesh_prim, result) -> None:
        if not result.valid:
            _log("히트 없음 (result.valid == False)")
            return

        hit_path = str(result.get_target_usd_path())
        primitive_id = getattr(result, "primitive_id", -1)
        hit_position = getattr(result, "hit_position", None)
        _log(f"히트: path={hit_path} primitive_id={primitive_id} pos={hit_position}")

        selection = omni.usd.get_context().get_selection()

        if not hit_path.startswith(str(mesh_prim.GetPath())):
            _log(f"대상 메시({mesh_prim.GetPath()})와 불일치 -> 클릭된 prim 선택: {hit_path}")
            selection.set_selected_prim_paths([hit_path], True)
            return

        face_index = Subset.face_from_primitive_id(mesh_prim, primitive_id)
        _log(f"face_from_primitive_id({primitive_id}) -> {face_index}")
        if face_index is None and hit_position is not None:
            hit_point = Gf.Vec3d(*hit_position)
            face_index = Subset.face_at_point(mesh_prim, hit_point)
            _log(f"face_at_point({hit_point}) -> {face_index}")

        subset_path = None
        if face_index is not None:
            face_map = Subset.build_face_subset_map(mesh_prim)
            subset_path = face_map.get(face_index)
            _log(f"face {face_index} -> subset {subset_path}")

        target_path = subset_path or hit_path
        selection.set_selected_prim_paths([target_path], True)
        _log(f"선택 변경 -> {target_path}")

    @staticmethod
    def _compute_ray(viewport_api, ndc_x: float, ndc_y: float):
        inv_proj = viewport_api.projection.GetInverse()
        cam_to_world = viewport_api.transform

        def unproject(z):
            p_view = inv_proj.Transform(Gf.Vec3d(ndc_x, ndc_y, z))
            return cam_to_world.Transform(p_view)

        near = unproject(-1.0)
        far = unproject(1.0)
        origin = cam_to_world.ExtractTranslation()
        direction = (far - near).GetNormalized()
        return Gf.Vec3d(origin), direction
