"""pick_face / select_face: overlay API, event, web messages, editor buttons, drawing."""

__all__ = ["TestFacePickSelect", "TestFaceWeb", "TestFaceEditor", "TestFaceDrawing"]

import asyncio
import json

import omni.kit.app
import omni.kit.test
from carb.eventdispatcher import get_eventdispatcher
from omni.ui import scene as sc
from pxr import Gf, Usd, UsdGeom

from .. import EVENT_CHANGED, EVENT_FACE_SELECTED, MSG_STATE, AnnotationOverlay, get_overlay
from ..drawing import OverlayDrawing, face_label_lines
from ..editor import EditorModel, EditorWindow
from ..face import point_face
from ..web import MSG_CLEAR_FACE, MSG_PICK_FACE, WebBridge
from .test_drawing import FakeViewportApi
from .test_editor import FakeArm
from .test_pick import FakeQueryApi
from .test_web import FakeCtx, FakeMessaging

FACE = {"kind": "planar", "center": [1.0, 2.0, 3.0], "normal": [0.0, 0.0, 1.0], "area": 4.0,
        "size": [2.0, 1.0], "outline": [[0.0, 1.5, 3.0], [2.0, 1.5, 3.0], [2.0, 2.5, 3.0], [0.0, 2.5, 3.0]]}


async def _updates(n=3):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


def body_stage():
    """A cube whose +Z side is the fake viewport's wall (z = -5, camera at the origin looking down -Z)."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    cube = UsdGeom.Cube.Define(stage, "/World/Body")  # size 2
    UsdGeom.Xformable(cube.GetPrim()).AddTranslateOp().Set(Gf.Vec3d(0, 0, -6))
    return stage


def face_api(stage):
    api = FakeQueryApi()
    api.stage = stage
    api.time = Usd.TimeCode.Default()
    return api


class TestFacePickSelect(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = body_stage()
        self.api = face_api(self.stage)
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None, viewport_resolver={"Viewport": self.api}.get)
        self.log = []
        disp = get_eventdispatcher()
        self.subs = [
            disp.observe_event(observer_name="morph.annotation.overlay.tests.face", event_name=EVENT_FACE_SELECTED,
                               on_event=lambda e: self.log.append(("face", e["face"]))),
            disp.observe_event(observer_name="morph.annotation.overlay.tests.face_chg", event_name=EVENT_CHANGED,
                               on_event=lambda e: self.log.append(("chg", e["reason"]))),
        ]

    async def tearDown(self):
        self.subs = []
        self.ov._shutdown()

    async def test_pick_face_hit(self):
        fut = self.ov.pick_face("Viewport", 0.5, 0.5)
        self.assertIsInstance(fut, asyncio.Future)
        result = await fut
        self.assertEqual(set(result), {"position", "face"})
        self.assertAlmostEqual(result["position"][2], -5.0)
        face = result["face"]
        self.assertEqual(face["kind"], "planar")
        for a, b in zip(face["center"], [0, 0, -5]):
            self.assertAlmostEqual(a, b, places=5)
        self.assertAlmostEqual(face["normal"][2], 1.0)  # toward the camera
        self.assertAlmostEqual(face["area"], 4.0, places=5)
        self.assertNotIn("/World", json.dumps(result, allow_nan=False))  # no prim path anywhere
        self.assertIsNone(self.ov.selected_face)  # pick_face does not select
        self.assertEqual(self.log, [])
        self.assertEqual(self.ov.list(), [])
        self.assertEqual(self.ov._picker.pending, 0)

    async def test_pick_face_miss_and_errors(self):
        self.api.surface = lambda pixel: None
        self.assertIsNone(await self.ov.pick_face("Viewport", 0.5, 0.5))
        with self.assertRaises(KeyError):
            self.ov.pick_face("Nope", 0.5, 0.5)
        with self.assertRaises(ValueError):
            self.ov.pick_face("Viewport", 1.5, 0.5)
        self.api.result = False  # rejected: a done future failing with RuntimeError
        fut = self.ov.pick_face("Viewport", 0.5, 0.5)
        self.assertTrue(fut.done())
        with self.assertRaises(RuntimeError):
            await fut

    async def test_pick_face_mesh_worker_build(self):
        """A mesh at or above the async threshold is built on a worker thread; the reply is the same."""
        from pxr import Vt

        from .test_face import box_mesh_data

        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        pts, counts, idx = box_mesh_data()
        mesh = UsdGeom.Mesh.Define(stage, "/World/Body")
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(x, y, z - 6) for x, y, z in pts]))  # top at z = -5
        mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(counts))
        mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(idx))
        self.api.stage = stage
        self.ov._faces.async_faces = 1
        result = await self.ov.pick_face("Viewport", 0.5, 0.5)
        face = result["face"]
        self.assertEqual(face["kind"], "planar")
        for a, b in zip(face["center"], [0, 0, -5]):
            self.assertAlmostEqual(a, b, places=5)
        self.assertAlmostEqual(face["area"], 4.0, places=5)
        self.assertEqual(self.ov._faces.build_count, 1)
        self.assertEqual(len(self.ov._faces.cached_keys), 1)
        self.assertEqual(self.ov._face_tasks, set())

    async def test_shutdown_while_face_is_computed(self):
        from pxr import Vt

        from .test_face import box_mesh_data

        pts, counts, idx = box_mesh_data()
        mesh = UsdGeom.Mesh.Define(self.stage, "/World/Mesh")
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(x, y, z - 6) for x, y, z in pts]))
        mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(counts))
        mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(idx))
        self.api.path_for = lambda pixel, path: "/World/Mesh"
        self.ov._faces.async_faces = 1
        fut = self.ov.pick_face("Viewport", 0.5, 0.5)  # the query answers right away; the face task runs
        await asyncio.sleep(0)
        self.ov._shutdown()
        await _updates(3)
        self.assertTrue(fut.done())
        if not fut.cancelled():  # the face was already finished before shutdown
            self.assertEqual(fut.result()["face"]["kind"], "planar")
        self.assertEqual(self.ov._face_tasks, set())

    async def test_unknown_prim_or_no_stage_is_point(self):
        self.api.path_for = lambda pixel, path: "/World/Gone"
        result = await self.ov.pick_face("Viewport", 0.5, 0.5)
        self.assertEqual(result["face"]["kind"], "point")
        self.assertEqual(result["face"]["center"], result["position"])
        self.assertAlmostEqual(result["face"]["normal"][2], 1.0)  # pick normal

    async def test_shutdown_cancels(self):
        self.api.respond = False
        fut = self.ov.pick_face("Viewport", 0.5, 0.5)
        self.ov._shutdown()
        await _updates(2)
        self.assertTrue(fut.cancelled())

    async def test_caller_cancel_drops_job(self):
        self.api.respond = False
        fut = self.ov.pick_face("Viewport", 0.5, 0.5)
        self.assertEqual(self.ov._picker.pending, 1)
        fut.cancel()
        await _updates(2)
        self.assertEqual(self.ov._picker.pending, 0)

    async def test_select_face_event_and_copies(self):
        self.ov.select_face(FACE)
        self.assertEqual(self.ov.selected_face, FACE)
        self.assertEqual(self.log, [("face", FACE)])  # no "changed": not an annotation
        got = self.ov.selected_face
        got["center"][0] = 99.0
        self.assertEqual(self.ov.selected_face["center"][0], 1.0)  # property returns a copy
        self.ov.select_face(dict(FACE))  # same value: no event
        self.assertEqual(len(self.log), 1)
        with self.assertRaises(ValueError):
            self.ov.select_face({**FACE, "normal": [0, 0, 0]})
        self.assertEqual(self.ov.selected_face, FACE)  # unchanged after a bad face
        self.ov.select_face(None)
        self.assertEqual(self.log[-1], ("face", None))
        self.ov.select_face(None)
        self.assertEqual(len(self.log), 2)

    async def test_face_selection_independent_of_annotations(self):
        self.ov.select_face(FACE)
        self.ov.add({"id": "a", "kind": "label", "position": [0, 0, 0]})
        self.ov.select("a")
        self.ov.clear()
        self.assertEqual(self.ov.selected_face, FACE)  # annotation changes keep the face
        self.ov._reset_faces()  # what stage open / close does
        self.assertIsNone(self.ov.selected_face)

    async def test_stage_open_clears_face_on_singleton(self):
        import omni.usd

        ov = get_overlay()
        self.assertIsNotNone(ov)
        ov.select_face(FACE)
        await omni.usd.get_context().new_stage_async()
        await _updates(2)
        self.assertIsNone(ov.selected_face)


class TestFaceWeb(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.api = face_api(body_stage())
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None, viewport_resolver={"Viewport": self.api}.get)
        self.msg = FakeMessaging()
        self.web = WebBridge(self.ov, messaging_getter=lambda: self.msg)

    async def tearDown(self):
        self.web.stop()
        self.ov._shutdown()

    def call(self, handler, payload):
        ctx = FakeCtx()
        handler(payload, ctx)
        return ctx

    async def _deferred(self, payload, frames=10):
        ctx = self.call(self.web._on_pick_face, payload)
        self.assertEqual(ctx.replies, [])  # deferred
        await _updates(frames)
        return ctx.one  # exactly one reply

    async def test_types(self):
        self.assertEqual(MSG_PICK_FACE, "morph.annotation.overlay.pick_face")
        self.assertEqual(MSG_CLEAR_FACE, "morph.annotation.overlay.clear_face")

    async def test_pick_face_hit_selects_and_pushes(self):
        self.web.flush()
        self.msg.sent.clear()
        r = await self._deferred({"viewport_name": "Viewport", "x": 0.5, "y": 0.5})
        self.assertEqual(set(r), {"ok", "hit", "position", "face"})
        self.assertTrue(r["ok"] and r["hit"])
        self.assertEqual(r["face"]["kind"], "planar")
        self.assertNotIn("/World", json.dumps(r, allow_nan=False))
        self.assertEqual(self.ov.selected_face, r["face"])  # the web reply selects the face
        self.assertTrue(self.web.flush())
        t, payload = self.msg.sent[-1]
        self.assertEqual(t, MSG_STATE)
        self.assertEqual(payload["face"], r["face"])  # outline included
        self.assertEqual(len(payload["face"]["outline"]), 4)
        self.assertEqual(self.call(self.web._on_list, {}).one["face"], r["face"])

    async def test_pick_face_miss_clears(self):
        self.ov.select_face(FACE)
        self.api.surface = lambda pixel: None
        r = await self._deferred({"viewport_name": "Viewport", "x": 0.2, "y": 0.2})
        self.assertEqual(r, {"ok": True, "hit": False})
        self.assertIsNone(self.ov.selected_face)

    async def test_pick_face_errors(self):
        self.ov.select_face(FACE)
        r = self.call(self.web._on_pick_face, {"viewport_name": "Nope", "x": 0.5, "y": 0.5}).one
        self.assertEqual(r["error"], "not_found")
        for bad in ({"x": 1.5, "y": 0.5}, {"x": 0.5, "y": -1}, {"x": "a", "y": 0.5}):
            r = self.call(self.web._on_pick_face, {"viewport_name": "Viewport", **bad}).one
            self.assertEqual(r["error"], "invalid_argument", bad)
        self.api.result = None  # no hydra texture
        r = await self._deferred({"viewport_name": "Viewport", "x": 0.5, "y": 0.5}, frames=3)
        self.assertEqual(r["error"], "unavailable")
        self.assertEqual(self.ov.selected_face, FACE)  # failures keep the selection

    async def test_pick_face_shutdown_unavailable(self):
        self.api.respond = False
        ctx = self.call(self.web._on_pick_face, {"viewport_name": "Viewport", "x": 0.5, "y": 0.5})
        self.ov._shutdown()
        await _updates(3)
        self.assertEqual(ctx.one["error"], "unavailable")

    async def test_clear_face_and_state(self):
        self.assertIsNone(self.web.state_payload()["face"])
        self.ov.select_face(FACE)
        self.assertEqual(self.web.state_payload()["face"], FACE)
        self.assertTrue(self.web.push_pending)  # face selection is pushed
        self.assertEqual(self.call(self.web._on_clear_face, {}).one, {"ok": True})
        self.assertIsNone(self.ov.selected_face)
        self.assertEqual(self.call(self.web._on_clear_face, {}).one, {"ok": True})  # idempotent
        self.web.stop()
        self.assertEqual(self.call(self.web._on_clear_face, {}).one["error"], "unavailable")
        self.assertEqual(self.call(self.web._on_pick_face, {"viewport_name": "Viewport", "x": 0.5, "y": 0.5}).one["error"],
                         "unavailable")


class TestFaceEditor(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.arm = FakeArm()
        self.result = {"position": [1.0, 2.0, 3.0], "face": FACE}
        self.m = EditorModel(self.ov, arm_click=self.arm.arm, disarm_click=self.arm.disarm,
                             pick_at=self._never, pick_face_at=self._pick_face_at)
        self.calls = []

    async def tearDown(self):
        self.m.destroy()
        self.ov._shutdown()

    def _never(self, *_a):
        raise AssertionError("position pick must not be used for [면 선택]")

    def _pick_face_at(self, api, x, y):
        self.calls.append((api, x, y))
        fut = asyncio.get_event_loop().create_future()
        if isinstance(self.result, BaseException):
            fut.set_exception(self.result)
        else:
            fut.set_result(self.result)
        return fut

    async def _click(self):
        self.assertTrue(self.m.begin_face_pick())
        self.assertTrue(self.m.picking)
        self.assertEqual(self.m.pick_mode, "face")
        self.arm.click("vp", 0.25, 0.75)
        await _updates(2)
        self.assertFalse(self.m.picking)

    async def test_select_face_button(self):
        self.m.new("label")
        await self._click()
        self.assertEqual(self.calls, [("vp", 0.25, 0.75)])
        self.assertEqual(self.ov.selected_face, FACE)
        self.assertEqual(self.m.face, FACE)
        self.assertIn("planar", self.m.status)
        self.assertEqual(self.m.values["position"], [0.0, 0.0, 0.0])  # the form is not touched
        self.assertFalse(self.m.dirty)
        self.result = None
        await self._click()
        self.assertIsNone(self.ov.selected_face)  # empty space clears
        self.assertIn("빈 곳", self.m.error)
        self.result = TimeoutError("slow")
        self.ov.select_face(FACE)
        await self._click()
        self.assertIn("slow", self.m.error)
        self.assertEqual(self.ov.selected_face, FACE)

    async def test_cancel_face_pick(self):
        self.m.begin_face_pick()
        self.m.cancel_pick()
        self.assertFalse(self.m.picking)
        self.assertEqual(self.arm.disarmed, 1)
        self.assertEqual(self.calls, [])

    async def test_face_to_form(self):
        self.assertFalse(self.m.apply_face())  # nothing selected
        self.assertTrue(self.m.error)
        self.ov.select_face(FACE)
        for kind, expect in (
            ("label", {"position": [1.0, 2.0, 3.0]}),
            ("inlet", {"position": [1.0, 2.0, 3.0], "direction": [-0.0, -0.0, -1.0]}),
            ("outlet", {"position": [1.0, 2.0, 3.0], "direction": [0.0, 0.0, 1.0]}),
        ):
            self.m.new(kind)
            self.assertTrue(self.m.apply_face(), kind)
            for name, value in expect.items():
                self.assertEqual(self.m.values[name], value, (kind, name))
            self.assertTrue(self.m.dirty)
            self.assertEqual(self.ov.list(), [])  # nothing applied yet
        self.m.new("plane")
        self.m.set_use_up(True)
        self.assertTrue(self.m.apply_face())
        self.assertEqual(self.m.values["origin"], [1.0, 2.0, 3.0])
        self.assertEqual(self.m.values["normal"], [0.0, 0.0, 1.0])
        self.assertAlmostEqual(self.m.values["size"][0], 2.4)
        self.assertAlmostEqual(self.m.values["size"][1], 1.2)
        self.assertFalse(self.m.use_up)  # stage up axis, like the drawn face size
        self.assertTrue(self.m.apply(), self.m.error)
        ann = self.ov.get(self.ov.selected_id)
        self.assertEqual(ann["origin"], [1.0, 2.0, 3.0])
        self.assertNotIn("up", ann)
        # a point face (size 0) keeps the current plane size
        self.m.new("plane")
        self.ov.select_face(point_face([4, 5, 6], [0, 1, 0]))
        self.assertTrue(self.m.apply_face())
        self.assertEqual(self.m.values["size"], [2.0, 2.0])
        self.assertEqual(self.m.values["normal"], [0.0, 1.0, 0.0])

    async def test_face_change_keeps_form(self):
        self.ov.add({"id": "a", "kind": "label", "position": [0, 0, 0]})
        self.m.select("a")
        self.m.set_label("edited, not applied")
        self.ov.select_face(FACE)
        self.assertEqual(self.m.editing_id, "a")
        self.assertEqual(self.m.label, "edited, not applied")

    async def test_window_builds_face_section(self):
        model = EditorModel(self.ov, arm_click=lambda cb: None, disarm_click=lambda: None)
        win = EditorWindow(model)
        try:
            for step in (lambda: None, lambda: self.ov.select_face(FACE), model.begin_face_pick,
                         lambda: self.ov.select_face(None)):
                step()
                win.window.frame.clear()
                with win.window.frame:
                    win._build()
            self.assertGreaterEqual(win.build_count, 4)
        finally:
            win.destroy()
            model.destroy()
        await _updates(2)


class TestFaceDrawing(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.drawing = OverlayDrawing(self.ov)
        self.scene_view = sc.SceneView()

    async def tearDown(self):
        self.drawing.stop()
        self.scene_view.destroy()

    async def test_selected_face_drawn_and_culled(self):
        api = FakeViewportApi()
        with self.scene_view.scene:
            scene = self.drawing.create_scene({"viewport_api": api})
        self.assertFalse(scene.face_built)
        face = {**FACE, "center": [0.0, 0.0, -5.0]}
        self.ov.select_face(face)
        self.assertTrue(self.drawing.dirty)
        self.assertTrue(self.drawing.flush())
        self.assertTrue(scene.face_built)
        self.assertEqual(scene.built_count, 0)  # not an annotation
        self.assertEqual(scene.shown_count, 1)
        api.move_camera_z(-10)  # the face centre is behind the camera now
        self.assertEqual(scene.shown_count, 0)
        api.move_camera_z(0)
        self.ov.set_visible(False)  # follows the global display switch
        self.drawing.flush()
        self.assertFalse(scene.face_built)
        self.ov.set_visible(True)
        self.ov.select_face(None)
        self.drawing.flush()
        self.assertFalse(scene.face_built)
        with self.scene_view.scene:  # point faces (no outline) draw too
            self.ov.select_face(point_face([0, 0, -5], [0, 0, 1]))
            self.drawing.flush()
        self.assertTrue(scene.face_built)

    async def test_label_lines(self):
        lines = face_label_lines({**FACE, "center": [1.23456, -2.0, 0.0005], "area": 12.3456})
        self.assertEqual(lines, ["(1.235, -2.000, 0.001)", "n (0.000, 0.000, 1.000)", "A = 12.346"])
        self.assertEqual(face_label_lines({**FACE, "area": 0.0})[2], "A = 0.000")
        self.assertEqual(face_label_lines({**FACE, "area": 0.00002})[2], "A = 2.000e-05")
