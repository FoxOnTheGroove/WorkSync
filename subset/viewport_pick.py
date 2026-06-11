import carb.input
import omni.appwindow
import omni.usd
from pxr import Gf
from omni.kit.viewport.utility import get_active_viewport, get_active_viewport_window

from .subset import Subset


class ViewportPicker:
    """체크박스 ON 시, 뷰포트 클릭 위치의 면이 속한 subset을 우선 선택.

    클릭한 면이 어떤 subset에도 속하지 않으면 아무것도 하지 않고
    기존 뷰포트 클릭 동작(메시 전체 선택)을 그대로 둔다.
    """

    def __init__(self, get_mesh_prim):
        self._get_mesh_prim = get_mesh_prim
        self._input_iface = None
        self._mouse = None
        self._sub = None

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._subscribe()
        else:
            self._unsubscribe()

    def destroy(self) -> None:
        self._unsubscribe()

    # ------------------------------------------------------------------ 내부

    def _subscribe(self) -> None:
        if self._sub:
            return
        try:
            app_window = omni.appwindow.get_default_app_window()
            self._mouse = app_window.get_mouse()
            self._input_iface = carb.input.acquire_input_interface()
            self._sub = self._input_iface.subscribe_to_input_events(self._on_mouse_event)
        except Exception as e:
            print(f"[Subset] 뷰포트 피킹 구독 실패: {e}")
            self._sub = None

    def _unsubscribe(self) -> None:
        if self._sub and self._input_iface:
            self._input_iface.unsubscribe_to_input_events(self._sub)
        self._sub = None

    def _on_mouse_event(self, event, *args) -> None:
        if event.event_type != carb.input.MouseEventType.LEFT_BUTTON_UP:
            return

        mesh_prim = self._get_mesh_prim()
        if not mesh_prim or not mesh_prim.IsValid():
            return

        viewport_window = get_active_viewport_window()
        viewport_api = get_active_viewport()
        if not viewport_window or not viewport_api:
            return

        coords = self._input_iface.get_mouse_coords_pixel(self._mouse)
        frame = viewport_window.frame
        local_x = coords[0] - frame.screen_position_x
        local_y = coords[1] - frame.screen_position_y
        if not (0 <= local_x <= frame.computed_width and 0 <= local_y <= frame.computed_height):
            return  # 뷰포트 밖 클릭

        ndc_x = (local_x / frame.computed_width) * 2.0 - 1.0
        ndc_y = 1.0 - (local_y / frame.computed_height) * 2.0

        origin, direction = self._compute_ray(viewport_api, ndc_x, ndc_y)
        face_index = Subset.raycast_face(mesh_prim, origin, direction)
        if face_index is None:
            return

        face_map = Subset.build_face_subset_map(mesh_prim)
        subset_path = face_map.get(face_index)
        if subset_path:
            omni.usd.get_context().get_selection().set_selected_prim_paths([subset_path], True)

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
