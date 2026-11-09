"""카메라 목록 (1.4.0): 순서 규칙, 스테이지 순회, SplitView.list_cameras, 웹 list_cameras.

스테이지는 메모리 스테이지를 `stage_provider`로 주입한다 (테스트 kit의 UsdContext·뷰포트 상태에 기대지 않음).
"""

from unittest import mock

import omni.kit.test
from pxr import Sdf, Usd, UsdGeom

from .. import CameraInfo, SplitView
from .. import web as web_mod
from ..adapters import HeadlessAdapter, validate_camera_prim
from ..cameras import BUILTIN_CAMERAS, list_camera_prims, sort_cameras
from ..web import (
    ERR_FAILED_PRECONDITION,
    ERR_UNAVAILABLE,
    MSG_LIST_CAMERAS,
    SCHEMA_LIST_CAMERAS_REPLY,
    WebBridge,
)
from .fakes import FakeCtx, FakeWindowAdapter

PERSP, TOP, FRONT, RIGHT = "/OmniverseKit_Persp", "/OmniverseKit_Top", "/OmniverseKit_Front", "/OmniverseKit_Right"


def _stage_with_cameras():
    """사용자 카메라 + 세션 레이어 기본 카메라 + 목록에서 빠져야 하는 prim들."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Camera.Define(stage, "/World/B")
    UsdGeom.Camera.Define(stage, "/World/A/Cam")  # 부모가 Camera가 아니어도 됨
    UsdGeom.Camera.Define(stage, "/World/B/Child")  # Camera 아래 Camera도 찾는다
    UsdGeom.Camera.Define(stage, "/Zed")
    UsdGeom.Xform.Define(stage, "/World/NotCam")
    inactive = UsdGeom.Camera.Define(stage, "/World/Inactive").GetPrim()
    inactive.SetActive(False)
    # 추상(class) prim 아래 카메라 → 빠짐. 이를 참조하는 instanceable prim 안의 카메라는 instance proxy → 빠짐
    stage.CreateClassPrim("/_Proto")
    UsdGeom.Camera.Define(stage, "/_Proto/Cam")
    inst = UsdGeom.Xform.Define(stage, "/World/Inst").GetPrim()
    inst.GetReferences().AddInternalReference("/_Proto")
    inst.SetInstanceable(True)
    # Kit 기본 카메라는 세션 레이어에 있다 (Kit이 스테이지를 열 때 만드는 것과 같은 위치)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for path in (RIGHT, PERSP, TOP, FRONT):
            UsdGeom.Camera.Define(stage, path)
    return stage


def _check_reply_schema(test, reply):
    """`SCHEMA_LIST_CAMERAS_REPLY`(CONTRACT 표)와 같은 모양인지 직접 확인 (stream은 회신을 검증하지 않음)."""
    schema = SCHEMA_LIST_CAMERAS_REPLY
    test.assertEqual(set(schema["required"]), {"ok", "cameras"})
    test.assertIs(reply["ok"], True)
    test.assertIsInstance(reply["cameras"], list)
    item = schema["properties"]["cameras"]["items"]
    types = {"string": str, "boolean": bool}
    for cam in reply["cameras"]:
        test.assertEqual(set(cam), set(item["required"]))
        for key, spec in item["properties"].items():
            test.assertIsInstance(cam[key], types[spec["type"]], key)


class TestSortCameras(omni.kit.test.AsyncTestCase):
    async def test_user_by_path_then_builtin_in_kit_order(self):
        cams = [
            CameraInfo(RIGHT, "OmniverseKit_Right", True),
            CameraInfo("/b", "b", False),
            CameraInfo(PERSP, "OmniverseKit_Persp", True),
            CameraInfo("/World/Cam2", "Cam2", False),
            CameraInfo("/World/Cam10", "Cam10", False),
            CameraInfo("/World/Cam2", "Cam2", False),  # 중복 제거
            CameraInfo(TOP, "OmniverseKit_Top", True),
        ]
        self.assertEqual(
            [c.path for c in sort_cameras(cams)],
            # 경로 문자열 오름차순 (자연 정렬 아님: "Cam10" < "Cam2"), 기본 카메라는 Persp, Top, Front, Right 순
            ["/World/Cam10", "/World/Cam2", "/b", PERSP, TOP, RIGHT],
        )

    async def test_builtin_set(self):
        self.assertEqual(BUILTIN_CAMERAS, (PERSP, TOP, FRONT, RIGHT))

    async def test_camera_info_dict(self):
        self.assertEqual(
            CameraInfo("/World/Cam", "Cam", False).to_dict(), {"path": "/World/Cam", "name": "Cam", "builtin": False}
        )


class TestListCameraPrims(omni.kit.test.AsyncTestCase):
    async def test_no_stage_is_empty(self):
        self.assertEqual(list_camera_prims(None), [])

    async def test_empty_stage(self):
        self.assertEqual(list_camera_prims(Usd.Stage.CreateInMemory()), [])

    async def test_cameras_in_contract_order(self):
        stage = _stage_with_cameras()
        cams = list_camera_prims(stage)
        self.assertEqual(
            [c.path for c in cams],
            ["/World/A/Cam", "/World/B", "/World/B/Child", "/Zed", PERSP, TOP, FRONT, RIGHT],
        )
        self.assertEqual([c.name for c in cams][:4], ["Cam", "B", "Child", "Zed"])
        self.assertEqual(cams[4].name, "OmniverseKit_Persp")
        self.assertEqual([c.builtin for c in cams], [False] * 4 + [True] * 4)
        self.assertTrue(all(isinstance(c, CameraInfo) for c in cams))

    async def test_excluded_prims(self):
        stage = _stage_with_cameras()
        paths = {c.path for c in list_camera_prims(stage)}
        for excluded in ("/World/NotCam", "/World/Inactive", "/_Proto/Cam", "/World/Inst/Cam"):
            self.assertNotIn(excluded, paths)
        # instance proxy는 스테이지에 있지만 목록에는 넣지 않는다 (CONTRACT 결정)
        proxy = stage.GetPrimAtPath("/World/Inst/Cam")
        self.assertTrue(proxy and proxy.IsInstanceProxy())

    async def test_builtin_only_by_path(self):
        # builtin은 경로로만 판단한다 (어느 레이어에 정의됐는지는 보지 않음). 이름만 같은 다른 경로는 사용자 카메라
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.Camera.Define(stage, "/OmniverseKit_Persp")  # 루트 레이어여도 Kit 기본 경로면 builtin
        UsdGeom.Camera.Define(stage, "/World/OmniverseKit_Persp")
        cams = list_camera_prims(stage)
        self.assertEqual(
            [(c.path, c.builtin) for c in cams], [("/World/OmniverseKit_Persp", False), (PERSP, True)]
        )

    async def test_listed_prims_pass_validation(self):
        # set_view_camera 검증과 목록 기준이 같다: 목록의 모든 경로가 validate_camera_prim을 통과한다
        stage = _stage_with_cameras()
        for cam in list_camera_prims(stage):
            validate_camera_prim(stage, cam.path)  # 예외 없음
            self.assertTrue(Sdf.Path(cam.path).IsAbsolutePath())


class TestSplitViewListCameras(omni.kit.test.AsyncTestCase):
    async def test_returns_list_and_empty_without_stage(self):
        stage = _stage_with_cameras()
        current = {"stage": stage}
        names = []

        def provider(name):
            names.append(name)
            return current["stage"]

        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None, stage_provider=provider)
        self.addCleanup(view._shutdown)
        cams = view.list_cameras()
        self.assertEqual([c.path for c in cams], [c.path for c in list_camera_prims(stage)])
        self.assertEqual(names, [""])  # 논리 전용: 기본 UsdContext
        # 요청 때마다 다시 순회한다 (캐시 없음)
        UsdGeom.Camera.Define(stage, "/New")
        self.assertIn("/New", [c.path for c in view.list_cameras()])
        current["stage"] = None
        self.assertEqual(view.list_cameras(), [])

    async def test_uses_adapter_usd_context(self):
        adapter = FakeWindowAdapter()
        adapter.usd_context_name = "ctx2"
        names = []
        view = SplitView(adapter, emit=lambda n, p: None, stage_provider=lambda n: names.append(n))
        self.addCleanup(view._shutdown)
        self.assertEqual(view.list_cameras(), [])
        self.assertEqual(names, ["ctx2"])

    async def test_no_events_or_state(self):
        events, states = [], []
        stage = _stage_with_cameras()
        view = SplitView(
            HeadlessAdapter(), emit=lambda n, p: events.append(n), state_sink=states.append,
            stage_provider=lambda _n: stage,
        )
        self.addCleanup(view._shutdown)
        view.list_cameras()
        view._tick()
        self.assertEqual((events, states), ([], []))

    async def test_every_listed_camera_accepted_by_set_view_camera(self):
        # 창 모드 + 실제 prim 검증: 목록에 있는 것은 set_view_camera가 성공한다
        stage = _stage_with_cameras()
        adapter = FakeWindowAdapter(validate=lambda p: validate_camera_prim(stage, p))
        view = SplitView(adapter, emit=lambda n, p: None, stage_provider=lambda _n: stage)
        self.addCleanup(view._shutdown)
        cams = view.list_cameras()
        self.assertTrue(cams)
        for cam in cams:
            view.set_view_camera(0, cam.path)
            self.assertEqual(view.list_views()[0].camera_path, cam.path)


class TestWebListCameras(omni.kit.test.AsyncTestCase):
    def _bridge(self, stage):
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None, stage_provider=lambda _n: stage)
        self.addCleanup(view._shutdown)
        return view, WebBridge(lambda: view)

    async def test_reply_ok_with_cameras(self):
        stage = _stage_with_cameras()
        _view, bridge = self._bridge(stage)
        ctx = FakeCtx(MSG_LIST_CAMERAS)
        bridge.on_list_cameras({}, ctx)
        self.assertEqual(len(ctx.replies), 1)
        reply = ctx.replies[0]
        _check_reply_schema(self, reply)
        self.assertEqual(reply["cameras"], [c.to_dict() for c in list_camera_prims(stage)])
        self.assertEqual(reply["cameras"][0], {"path": "/World/A/Cam", "name": "Cam", "builtin": False})
        self.assertEqual(reply["cameras"][-1], {"path": RIGHT, "name": "OmniverseKit_Right", "builtin": True})

    async def test_empty_stage_is_ok_empty_list(self):
        _view, bridge = self._bridge(Usd.Stage.CreateInMemory())
        ctx = FakeCtx(MSG_LIST_CAMERAS)
        bridge.on_list_cameras({}, ctx)
        self.assertEqual(ctx.replies, [{"ok": True, "cameras": []}])

    async def test_no_stage_is_failed_precondition(self):
        _view, bridge = self._bridge(None)
        ctx = FakeCtx(MSG_LIST_CAMERAS)
        bridge.on_list_cameras({}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_FAILED_PRECONDITION])
        self.assertTrue(ctx.replies[0]["message"])

    async def test_not_running_is_unavailable(self):
        ctx = FakeCtx(MSG_LIST_CAMERAS)
        WebBridge(lambda: None).on_list_cameras({}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])
        view = SplitView(HeadlessAdapter(), emit=lambda n, p: None, stage_provider=lambda _n: Usd.Stage.CreateInMemory())
        view._shutdown()
        ctx = FakeCtx(MSG_LIST_CAMERAS)
        WebBridge(lambda: view).on_list_cameras({}, ctx)
        self.assertEqual([r["error"] for r in ctx.replies], [ERR_UNAVAILABLE])

    async def test_type_name_and_request_schema(self):
        self.assertEqual(MSG_LIST_CAMERAS, "morph.viewport.split.list_cameras")
        self.assertEqual(web_mod.SCHEMA_LIST_CAMERAS, {"type": "object"})

    async def test_registered_as_inbound(self):
        class _Msg:
            def __init__(self):
                self.inbound = []
                self.outbound = []

            def register_outbound(self, msg_type, schema=None, *, owner, version=1):
                self.outbound.append(msg_type)
                return mock.Mock()

            def register_inbound(self, msg_type, handler, schema=None, *, owner, version=1):
                self.inbound.append((msg_type, handler, schema))
                return mock.Mock()

        msg = _Msg()
        bridge = WebBridge(lambda: None)
        bridge.register(msg)
        entry = [e for e in msg.inbound if e[0] == MSG_LIST_CAMERAS]
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0][1], bridge.on_list_cameras)
        self.assertEqual(entry[0][2], {"type": "object"})
        self.assertNotIn(MSG_LIST_CAMERAS, msg.outbound)  # 요청 type은 outbound로 선언하지 않는다
