__all__ = ["TestAnnotationNoPrims", "TestAnnotationExtensionLifecycle"]

import json
import os
import tempfile
from unittest import mock

import omni.kit.app
import omni.kit.test
import omni.usd
from omni.ui import scene as sc
from pxr import Gf, Usd

from .. import AnnotationOverlay, get_overlay
from .. import drawing as drawing_mod
from .. import extension as extension_mod
from .. import overlay as overlay_mod
from .. import web as web_mod
from ..drawing import OverlayDrawing
from ..overlay import EXT_ID, MSG_STATE
from .test_pick import FakeQueryApi
from .test_web import ALL_TYPES

USDA = """#usda 1.0
(
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Body"
    {
    }
    def Xform "Pipes"
    {
        def Cylinder "Inlet"
        {
        }
    }
    def Mesh "Plate"
    {
        int[] faceVertexCounts = [4, 4]
        int[] faceVertexIndices = [0, 1, 4, 3, 1, 2, 5, 4]
        point3f[] points = [(-1, -1, 3), (0, -1, 3), (1, -1, 3), (-1, 1, 3), (0, 1, 3), (1, 1, 3)]
    }
}
"""

ANNS = [
    {"id": "in1", "kind": "inlet", "label": "Inlet", "description": "냉각수 입구 DN50",
     "position": [-3.0, 0.0, 1.0], "direction": [1, 0, 0], "size": 0.8},
    {"kind": "outlet", "label": "Outlet A", "position": [3.0, 0.0, 1.0], "direction": [1, 0, 0]},
    {"kind": "plane", "label": "Section A-A", "origin": [0.0, 0.0, 1.0], "normal": [1, 0, 0], "size": [1.5, 1.5]},
    {"kind": "label", "label": "Valve V-101", "description": "수동 볼밸브", "position": [1.2, 0.0, 1.4]},
]


async def _updates(n=3):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


class TestAnnotationNoPrims(omni.kit.test.AsyncTestCase):
    """Requirement: annotations never create prims and never touch the model layer."""

    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.scene = os.path.join(self.dir, "model.usda")
        with open(self.scene, "w", encoding="utf-8") as f:
            f.write(USDA)
        with open(os.path.join(self.dir, "model.annotations.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "annotations": ANNS[:2]}, f, ensure_ascii=False)

    async def tearDown(self):
        await omni.usd.get_context().new_stage_async()
        await _updates(2)
        self._tmp.cleanup()

    async def test_operations_leave_stage_unchanged(self):
        ctx = omni.usd.get_context()
        await ctx.open_stage_async(self.scene)
        await _updates()
        stage = ctx.get_stage()
        root = stage.GetRootLayer()

        def snapshot():
            return (
                len(list(stage.Traverse())),
                len(list(stage.TraverseAll())),
                root.dirty,
                root.ExportToString(),
            )

        before = snapshot()
        self.assertGreaterEqual(before[0], 4)
        self.assertFalse(before[2])

        ov = get_overlay()
        self.assertEqual(len(ov.list()), 2)  # autoload read the sidecar
        ov.add({"kind": "label", "label": "Extra", "position": [0, 0, 2]})
        ov.update("in1", description="DN65")
        ov.set_all(ANNS)
        ov.set_visible(False)
        ov.set_visible(True)
        await _updates()  # singleton drawing flush (rebuild on every viewport)
        self.assertEqual(snapshot(), before)

        self.assertEqual(ov.load(), 2)
        ov.save()
        await _updates()
        self.assertEqual(snapshot(), before)

        # explicit rebuild into a standalone scene (what every viewport layer gets)
        drawing = OverlayDrawing(ov)
        scene_view = sc.SceneView()
        try:
            with scene_view.scene:
                item = drawing.create_scene({})
            ov.set_all(ANNS)
            self.assertTrue(drawing.flush())
            self.assertEqual(item.built_count, len(ANNS))
            await _updates()
        finally:
            drawing.stop()
            scene_view.destroy()
        self.assertEqual(snapshot(), before)

        # select, pick and the editor paths (0.3)
        from ..editor import EditorModel

        ov.select("in1")
        await _updates()  # highlighted rebuild on every viewport
        hit = await ov._pick_api(FakeQueryApi(), 0.5, 0.5)
        self.assertIsNotNone(hit)
        drawing = OverlayDrawing(ov)
        scene_view = sc.SceneView()
        model = None
        try:
            with scene_view.scene:
                item = drawing.create_scene({"viewport_api": FakeQueryApi()})
            model = EditorModel(ov, arm_click=drawing.arm_click, disarm_click=drawing.disarm_click)
            model.new("outlet")
            self.assertTrue(model.begin_pick())
            self.assertTrue(item.armed)
            self.assertTrue(item._on_pick_click((0.0, 0.0)))  # click in the middle of the view
            for _ in range(10):
                if not model.picking:
                    break
                await _updates(1)
            self.assertFalse(model.picking, model.status)
            self.assertAlmostEqual(model.values["direction"][2], 1.0)  # outward = +normal
            self.assertTrue(model.apply(), model.error)
            model.set_label("edited")
            self.assertTrue(model.apply(), model.error)
            self.assertTrue(model.delete())
            ov.select("in1")
            ov.select(None)
            await _updates()
        finally:
            if model is not None:
                model.destroy()
            drawing.stop()
            scene_view.destroy()
        self.assertEqual(snapshot(), before)

        # pick_face / select_face and the editor face buttons (0.4): computed from the stage, nothing written
        from .test_editor import FakeArm

        def face_api(path, point):
            api = FakeQueryApi(surface=lambda pixel: (path, point))
            api.view = Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0, 0, 10)).GetInverse()  # camera above
            api.stage = stage
            api.time = Usd.TimeCode.Default()
            return api

        res = await ov._pick_face_api(face_api("/World/Body", (0.2, 0.1, 1.0)), 0.5, 0.5)  # Cube
        self.assertEqual(res["face"]["kind"], "planar")
        self.assertAlmostEqual(res["face"]["area"], 4.0, places=4)
        plate_api = face_api("/World/Plate", (0.5, 0.0, 3.0))  # Mesh: 2 quads, one plane
        res = await ov._pick_face_api(plate_api, 0.5, 0.5)
        self.assertEqual(res["face"]["kind"], "planar")
        self.assertAlmostEqual(res["face"]["area"], 4.0, places=4)
        ov.select_face(res["face"])
        await _updates()  # drawn on every viewport
        arm = FakeArm()
        model = EditorModel(ov, arm_click=arm.arm, disarm_click=arm.disarm)
        try:
            model.new("outlet")
            self.assertTrue(model.begin_face_pick())
            arm.click(plate_api, 0.5, 0.5)
            for _ in range(10):
                if not model.picking:
                    break
                await _updates(1)
            self.assertFalse(model.picking, model.status)
            self.assertTrue(model.apply_face(), model.error)
            self.assertTrue(model.apply(), model.error)
            self.assertTrue(model.delete())
            ov.select_face(None)
            await _updates()
        finally:
            model.destroy()
        self.assertEqual(snapshot(), before)


class _Sub:
    def __init__(self, active: set, type_: str):
        self._active = active
        self._type = type_

    def unsubscribe(self):
        self._active.discard(self._type)


class RecordingMessaging:
    """Stands in for morph.stream.messaging so a second extension instance does not clash."""

    def __init__(self):
        self.is_connected = False
        self.inbound = set()
        self.outbound = set()

    def register_inbound(self, type_, handler, schema=None, *, owner, version=1):
        assert owner == EXT_ID and callable(handler)
        self.inbound.add(type_)
        return _Sub(self.inbound, type_)

    def register_outbound(self, type_, schema=None, *, owner, version=1):
        assert owner == EXT_ID
        self.outbound.add(type_)
        return _Sub(self.outbound, type_)

    def send(self, type_, payload, reply_to=None):
        pass


class TestAnnotationExtensionLifecycle(omni.kit.test.AsyncTestCase):
    """Run the real extension class startup / shutdown on a second instance.

    Web messaging is replaced by a recorder and the scene factory gets a test id, so the
    running extension (singleton, registered types) is not disturbed; it is restored afterwards.
    """

    async def test_shutdown_releases_registrations(self):
        real = get_overlay()
        fake = RecordingMessaging()
        notified = []
        try:
            from omni.kit.viewport.registry import RegisterScene
        except ImportError:
            RegisterScene = None

        def notifier(factory, loading):
            notified.append((getattr(factory, "factory_id", None), loading))

        if RegisterScene is not None:
            RegisterScene.add_notifier(notifier)
        ext = extension_mod.AnnotationOverlayExtension()
        try:
            with mock.patch.object(web_mod, "_default_messaging", lambda: fake), \
                    mock.patch.object(drawing_mod, "FACTORY_ID", f"{EXT_ID}.scene_test"):
                ext.on_startup(EXT_ID)
                inst = get_overlay()
                self.assertIsInstance(inst, AnnotationOverlay)
                self.assertIsNot(inst, real)
                self.assertEqual(fake.inbound, set(ALL_TYPES))
                self.assertEqual(fake.outbound, {MSG_STATE})
                if RegisterScene is not None:
                    self.assertIn((f"{EXT_ID}.scene_test", True), notified)
                await _updates(2)  # update subscription runs without errors

                ext.on_shutdown()
                self.assertIsNone(get_overlay())
                self.assertEqual(fake.inbound, set())
                self.assertEqual(fake.outbound, set())
                self.assertTrue(inst._closed)
                if RegisterScene is not None:
                    self.assertIn((f"{EXT_ID}.scene_test", False), notified)
                await _updates(2)  # nothing left subscribed that touches the closed instance
        finally:
            if getattr(ext, "_overlay", None) is not None:  # an assertion failed mid-way
                ext.on_shutdown()
            if RegisterScene is not None:
                RegisterScene.remove_notifier(notifier)
            overlay_mod._set_instance(real)
        self.assertIs(get_overlay(), real)
