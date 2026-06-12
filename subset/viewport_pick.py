import traceback
import asyncio

import omni.ui.scene as sc
import omni.kit.app
import omni.usd
from pxr import Gf
from omni.kit.viewport.utility import disable_selection, get_active_viewport, get_active_viewport_window

from .subset import Subset


def _log(msg: str) -> None:
    print(f"[Subset][Pick] {msg}")


class ViewportPicker:
    """체크박스 ON 시 뷰포트에서 subset을 선택.

    - 클릭: 클릭 위치의 면이 속한 subset 하나를 선택.
    - 드래그: 사각형 안에 면 중심이 들어오는 subset들을 모두 선택.

    뷰포트 위에 투명한 omni.ui.scene Screen을 덮고 ClickGesture/DragGesture로
    입력을 받는다 (carb.input의 device-level 마우스 구독은 omni.ui 위젯 위에서는
    호출되지 않음). 레이 교차는 omni.kit.raycast.query(RTX 기반, 콜라이더 불필요)로
    수행하고, 히트 위치를 face index로 역산해 subset을 찾는다.
    클릭한 면이 어떤 subset에도 속하지 않으면, 클릭된 prim을 그대로 선택해
    기존 뷰포트 클릭 동작을 흉내낸다.
    """

    def __init__(self, get_mesh_prim, on_pick=None, on_pick_multi=None):
        self._get_mesh_prim = get_mesh_prim
        self._on_pick = on_pick
        self._on_pick_multi = on_pick_multi
        self._raycast = None
        self._frame = None
        self._scene_view = None
        self._rect_transform = None
        self._drag_start = None
        self._pending_paths: list = []
        self._disable_selection = None
        self._face_subset_map: "dict | None" = None

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._subscribe()
        else:
            self._unsubscribe()

    def destroy(self) -> None:
        self._unsubscribe()

    def invalidate_face_subset_cache(self) -> None:
        """subset 목록/이름이 바뀌었을 때(생성/삭제/머지/이름변경) 캐시를 비운다."""
        self._face_subset_map = None

    # ------------------------------------------------------------------ 내부

    def _subscribe(self) -> None:
        if self._scene_view:
            return
        try:
            self._raycast = self._acquire_raycast_interface()

            viewport_window = get_active_viewport_window()
            viewport_api = get_active_viewport()
            if not viewport_window or not viewport_api:
                _log("active viewport window 없음 -> 구독 실패")
                return

            # 기본 클릭-선택을 꺼서 우리가 고른 subset이 곧바로 덮어써지지 않게 한다.
            self._disable_selection = disable_selection(viewport_api, disable_click=True)

            self._frame = viewport_window.get_frame("subset_pick_overlay")
            with self._frame:
                self._scene_view = sc.SceneView()
                with self._scene_view.scene:
                    sc.Screen(gestures=[
                        sc.ClickGesture(self._on_click),
                        sc.DragGesture(
                            on_began_fn=self._on_drag_began,
                            on_changed_fn=self._on_drag_changed,
                            on_ended_fn=self._on_drag_ended,
                        ),
                    ])
                    self._rect_transform = sc.Transform()
            _log("구독 시작 (Click + Drag Gesture)")
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
        self._rect_transform = None
        self._drag_start = None
        self._disable_selection = None  # 핸들 해제 -> 기본 클릭-선택 복원

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

    # ------------------------------------------------------------------ 클릭 (단일 선택)

    def _on_click(self, sender) -> None:
        try:
            self._handle_click(sender)
        except Exception:
            _log("_on_click 예외:")
            traceback.print_exc()

    def _handle_click(self, sender) -> None:
        payload = sender.gesture_payload

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
        hit_position = getattr(result, "hit_position", None)
        _log(f"히트: path={hit_path} pos={hit_position}")

        if not hit_path.startswith(str(mesh_prim.GetPath())):
            _log(f"대상 메시({mesh_prim.GetPath()})와 불일치 -> 클릭된 prim 선택: {hit_path}")
            self._select(hit_path, None)
            return

        # primitive_id는 신뢰할 수 없어(0/1만 관측됨) 히트 위치 기반 최근접 면으로 역산.
        face_index = None
        if hit_position is not None:
            hit_point = Gf.Vec3d(*hit_position)
            face_index = Subset.face_at_point(mesh_prim, hit_point)
            _log(f"face_at_point({hit_point}) -> {face_index}")

        subset_path = None
        if face_index is not None:
            face_map = self._get_face_subset_map(mesh_prim)
            subset_path = face_map.get(face_index)
            _log(f"face {face_index} -> subset {subset_path}")

        target_path = subset_path or hit_path
        self._select(target_path, face_index)

    def _get_face_subset_map(self, mesh_prim) -> dict:
        if self._face_subset_map is None:
            self._face_subset_map = Subset.build_face_subset_map(mesh_prim)
        return self._face_subset_map

    # ------------------------------------------------------------------ 드래그 (다중 선택)

    def _on_drag_began(self, sender) -> None:
        try:
            self._drag_start = tuple(sender.gesture_payload.mouse)
        except Exception:
            _log("_on_drag_began 예외:")
            traceback.print_exc()

    def _on_drag_changed(self, sender) -> None:
        try:
            if self._drag_start is None:
                return
            self._update_rect(self._drag_start, tuple(sender.gesture_payload.mouse))
        except Exception:
            _log("_on_drag_changed 예외:")
            traceback.print_exc()

    def _on_drag_ended(self, sender) -> None:
        try:
            start = self._drag_start
            self._drag_start = None
            if self._rect_transform:
                self._rect_transform.clear()
            if start is None:
                return
            asyncio.ensure_future(
                self._handle_drag_select_async(start, tuple(sender.gesture_payload.mouse))
            )
        except Exception:
            _log("_on_drag_ended 예외:")
            traceback.print_exc()

    def _update_rect(self, start, current) -> None:
        """드래그 중인 선택 사각형을 NDC 평면에 그린다."""
        if not self._rect_transform:
            return
        self._rect_transform.clear()
        x0, y0 = start
        x1, y1 = current
        corners = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)]
        with self._rect_transform:
            for a, b in zip(corners, corners[1:] + corners[:1]):
                sc.Line(a, b, color=0xFF4040FF, thickness=1)

    async def _handle_drag_select_async(self, start, end) -> None:
        try:
            mesh_prim = self._get_mesh_prim()
            if not mesh_prim or not mesh_prim.IsValid():
                _log("mesh_prim 없음 -> 드래그 무시")
                return

            viewport_api = get_active_viewport()
            if not viewport_api:
                _log("active viewport 없음 -> 드래그 무시")
                return

            x0, x1 = sorted((start[0], end[0]))
            y0, y1 = sorted((start[1], end[1]))
            _log(f"드래그 rect NDC=({x0:.3f},{y0:.3f})~({x1:.3f},{y1:.3f})")

            world_to_cam = viewport_api.transform.GetInverse()
            proj = viewport_api.projection
            camera_pos = Gf.Vec3d(viewport_api.transform.ExtractTranslation())

            centers = Subset.face_centers_world(mesh_prim)
            normals = Subset.face_normals_world(mesh_prim)

            faces_in_rect = []
            for fi, center in enumerate(centers):
                cam_pt = world_to_cam.Transform(center)
                if cam_pt[2] >= 0:  # 카메라 뒤
                    continue
                ndc = proj.Transform(cam_pt)
                if not (x0 <= ndc[0] <= x1 and y0 <= ndc[1] <= y1):
                    continue
                # 뒷면(카메라 반대쪽을 향한 면)은 화면에서 가려져 있으므로 제외
                view_dir = (center - camera_pos).GetNormalized()
                if Gf.Dot(normals[fi], view_dir) >= 0:
                    continue
                faces_in_rect.append(fi)

            if not faces_in_rect:
                _log("rect 안에 면 없음")
                return

            face_map = self._get_face_subset_map(mesh_prim)
            faces_by_path: dict = {}
            for fi in faces_in_rect:
                p = face_map.get(fi)
                if p:
                    faces_by_path.setdefault(p, []).append(fi)

            if not faces_by_path:
                _log("rect 안의 면이 속한 subset 없음")
                return

            # subset당 최대 SAMPLE_COUNT개 면만 골라 다른 오브젝트에 가려졌는지
            # 레이캐스트로 확인한다 (subset의 face 수가 2개든 10000개든 비용 고정).
            paths = await self._filter_visible_paths(mesh_prim, camera_pos, centers, faces_by_path)

            if not paths:
                _log("rect 안의 면이 모두 다른 오브젝트에 가려짐")
                return

            _log(f"드래그 선택: 후보 subset {len(faces_by_path)}개 -> 보이는 subset {len(paths)}개")
            self._select_paths(paths)
            if self._on_pick_multi:
                self._on_pick_multi(paths)
        except Exception:
            _log("_handle_drag_select_async 예외:")
            traceback.print_exc()

    _OCCLUSION_SAMPLE_COUNT = 5

    async def _filter_visible_paths(self, mesh_prim, camera_pos, centers, faces_by_path) -> list:
        """각 subset에서 일부 면을 샘플링해, 다른 오브젝트에 가려지지 않고
        보이는 면이 하나라도 있는 subset만 남긴다."""
        paths = list(faces_by_path.keys())
        sample_lists = []
        for path in paths:
            faces = faces_by_path[path]
            if len(faces) <= self._OCCLUSION_SAMPLE_COUNT:
                samples = faces
            else:
                step = len(faces) / self._OCCLUSION_SAMPLE_COUNT
                samples = [faces[int(i * step)] for i in range(self._OCCLUSION_SAMPLE_COUNT)]
            sample_lists.append(samples)

        visibility = await asyncio.gather(*[
            asyncio.gather(*[
                self._is_face_visible(mesh_prim, camera_pos, centers[fi]) for fi in samples
            ])
            for samples in sample_lists
        ])

        return [path for path, results in zip(paths, visibility) if any(results)]

    async def _is_face_visible(self, mesh_prim, camera_pos, face_center) -> bool:
        """카메라 -> 면 중심으로 레이캐스트해, 첫 히트가 이 메시 자신이고 거리가
        면 중심까지의 거리와 거의 같으면(다른 오브젝트에 가려지지 않음) True."""
        offset = face_center - camera_pos
        distance = offset.GetLength()
        if distance < 1e-9:
            return True
        direction = offset / distance

        import omni.kit.raycast.query as rq
        ray = rq.Ray(tuple(camera_pos), tuple(direction))

        loop = asyncio.get_event_loop()
        future = loop.create_future()

        def _on_hit(ray, result) -> None:
            if not future.done():
                future.set_result(result)

        self._raycast.submit_raycast_query(ray, _on_hit)
        result = await future

        if not result.valid:
            return False
        hit_path = str(result.get_target_usd_path())
        if not hit_path.startswith(str(mesh_prim.GetPath())):
            return False

        hit_position = getattr(result, "hit_position", None)
        if hit_position is None:
            return True
        hit_distance = (Gf.Vec3d(*hit_position) - camera_pos).GetLength()
        return abs(hit_distance - distance) <= max(1e-3 * distance, 1e-4)

    # ------------------------------------------------------------------ 선택 적용

    def note_external_selection(self, paths: list) -> None:
        """다중 선택 토글 등 UI 쪽에서 선택을 직접 바꿨을 때, 진행 중인
        재적용 루프가 이를 덮어쓰지 않도록 기준값을 갱신한다."""
        self._pending_paths = list(paths)

    def _select(self, path: str, face_index: "int | None") -> None:
        self._select_paths([path])
        if self._on_pick:
            self._on_pick(path, face_index)

    def _select_paths(self, paths: list) -> None:
        self._pending_paths = list(paths)
        omni.usd.get_context().get_selection().set_selected_prim_paths(list(paths), True)
        _log(f"선택 변경 -> {paths}")
        asyncio.ensure_future(self._reassert_selection(list(paths)))

    async def _reassert_selection(self, paths: list) -> None:
        """뷰포트 자체의 클릭-선택이 비동기로 한 박자 늦게 끼어들어 우리가 고른
        prim을 덮어쓰는 경우가 있어, 몇 프레임 동안 선택을 다시 강제한다."""
        app = omni.kit.app.get_app()
        selection = omni.usd.get_context().get_selection()
        for _ in range(2):
            await app.next_update_async()
            if self._pending_paths != paths:
                return
            if sorted(selection.get_selected_prim_paths()) != sorted(paths):
                _log(f"선택이 덮어써짐 -> 재적용: {paths}")
                selection.set_selected_prim_paths(list(paths), True)

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
