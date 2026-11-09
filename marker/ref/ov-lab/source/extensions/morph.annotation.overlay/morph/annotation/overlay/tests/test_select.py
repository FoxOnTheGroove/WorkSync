__all__ = ["TestAnnotationSelection", "TestAnnotationWebSelectPick", "TestAnnotationClickCapture"]

import json
import os
import tempfile

import omni.kit.app
import omni.kit.test
from carb.eventdispatcher import get_eventdispatcher
from omni.ui import scene as sc

from .. import EVENT_CHANGED, EVENT_SELECTION_CHANGED, MSG_STATE, AnnotationOverlay
from ..drawing import SELECTED_STYLE, OverlayDrawing, highlight_color, ndc_to_image
from ..web import WebBridge
from .test_pick import FakeQueryApi
from .test_web import FakeCtx, FakeMessaging

LABEL = {"kind": "label", "position": [1, 2, 3], "label": "L"}
INLET = {"kind": "inlet", "position": [0, 0, -5], "direction": [1, 0, 0]}


async def _updates(n=3):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


class TestAnnotationSelection(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.scene = os.path.join(self._tmp.name, "scene.usda")
        self.ov = AnnotationOverlay(scene_file_provider=lambda: self.scene)
        self.log = []
        disp = get_eventdispatcher()
        self.subs = [
            disp.observe_event(
                observer_name="morph.annotation.overlay.tests.sel",
                event_name=EVENT_SELECTION_CHANGED,
                on_event=lambda e: self.log.append(("sel", e["id"], self.ov.selected_id)),
            ),
            disp.observe_event(
                observer_name="morph.annotation.overlay.tests.chg",
                event_name=EVENT_CHANGED,
                on_event=lambda e: self.log.append(("chg", e["reason"])),
            ),
        ]

    async def tearDown(self):
        self.subs = []
        self._tmp.cleanup()

    async def test_select_and_event(self):
        self.ov.add({**LABEL, "id": "a"})
        self.ov.add({**LABEL, "id": "b"})
        self.log.clear()
        self.assertIsNone(self.ov.selected_id)
        self.ov.select("a")
        self.assertEqual(self.ov.selected_id, "a")
        self.assertEqual(self.log, [("sel", "a", "a")])  # selection alone does not emit "changed"
        self.ov.select("a")  # same value: no event
        self.assertEqual(len(self.log), 1)
        with self.assertRaises(KeyError):
            self.ov.select("zz")
        self.assertEqual(self.ov.selected_id, "a")
        self.ov.select(None)
        self.assertEqual(self.log[-1], ("sel", None, None))

    async def test_cleared_on_remove_after_changed(self):
        self.ov.add({**LABEL, "id": "a"})
        self.ov.add({**LABEL, "id": "b"})
        self.ov.select("a")
        self.log.clear()
        self.ov.remove("b")  # another one: kept
        self.assertEqual(self.ov.selected_id, "a")
        self.assertEqual(self.log, [("chg", "remove")])
        self.log.clear()
        self.ov.remove("a")
        self.assertIsNone(self.ov.selected_id)
        self.assertEqual(self.log, [("chg", "remove"), ("sel", None, None)])  # changed first

    async def test_cleared_on_clear_and_load(self):
        self.ov.add({**LABEL, "id": "a"})
        self.ov.select("a")
        self.ov.clear()
        self.assertIsNone(self.ov.selected_id)

        self.ov.add({**LABEL, "id": "a"})
        self.ov.save()
        self.ov.select("a")
        self.assertEqual(self.ov.load(), 1)  # same id reloaded: still dropped
        self.assertIsNone(self.ov.selected_id)

        self.ov.select("a")
        self.ov._on_stage_opened()
        self.assertIsNone(self.ov.selected_id)
        self.ov.select("a")
        self.ov._on_stage_closed()
        self.assertIsNone(self.ov.selected_id)

    async def test_set_all_keeps_if_present(self):
        self.ov.add({**LABEL, "id": "a"})
        self.ov.select("a")
        self.ov.set_all([{**LABEL, "id": "a"}, {**LABEL, "id": "c"}])
        self.assertEqual(self.ov.selected_id, "a")
        self.ov.set_all([{**LABEL, "id": "c"}])
        self.assertIsNone(self.ov.selected_id)

    async def test_failed_operations_keep_selection(self):
        self.ov.add({**LABEL, "id": "a"})
        self.ov.select("a")
        with self.assertRaises(ValueError):
            self.ov.set_all([{"kind": "label"}])
        with self.assertRaises(KeyError):
            self.ov.remove("zz")
        with open(self.ov.current_file, "w", encoding="utf-8") as f:
            f.write("{broken")
        with self.assertRaises(ValueError):
            self.ov.load()
        self.assertEqual(self.ov.selected_id, "a")


class TestAnnotationWebSelectPick(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.api = FakeQueryApi()
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

    async def test_select(self):
        self.ov.add({**LABEL, "id": "a"})
        self.web.flush()
        self.msg.sent.clear()
        self.assertEqual(self.call(self.web._on_select, {"id": "a"}).one, {"ok": True, "selected_id": "a"})
        self.assertEqual(self.ov.selected_id, "a")
        self.assertTrue(self.web.flush())  # selection is pushed in state
        self.assertEqual(self.msg.sent[-1][0], MSG_STATE)
        self.assertEqual(self.msg.sent[-1][1]["selected_id"], "a")
        self.assertEqual(self.call(self.web._on_list, {}).one["selected_id"], "a")
        r = self.call(self.web._on_select, {"id": "zz"}).one
        self.assertEqual(r["error"], "not_found")
        self.assertEqual(self.ov.selected_id, "a")
        self.assertEqual(self.call(self.web._on_select, {"id": None}).one, {"ok": True, "selected_id": None})
        self.assertEqual(self.call(self.web._on_select, {"id": 3}).one["error"], "invalid_argument")

    async def _deferred(self, payload, frames=10):
        ctx = self.call(self.web._on_pick, payload)
        self.assertEqual(ctx.replies, [])  # not answered synchronously
        await _updates(frames)
        return ctx.one  # exactly one reply

    async def test_pick_hit(self):
        r = await self._deferred({"viewport_name": "Viewport", "x": 0.5, "y": 0.5})
        self.assertEqual(set(r), {"ok", "hit", "position", "normal"})
        self.assertTrue(r["ok"] and r["hit"])
        self.assertAlmostEqual(r["position"][2], -5.0)
        self.assertAlmostEqual(r["normal"][2], 1.0)
        json.dumps(r, allow_nan=False)
        self.assertEqual(self.ov.list(), [])  # pick creates nothing

    async def test_pick_miss(self):
        self.api.surface = lambda pixel: None
        r = await self._deferred({"viewport_name": "Viewport", "x": 0.1, "y": 0.9})
        self.assertEqual(r, {"ok": True, "hit": False})

    async def test_pick_errors(self):
        r = self.call(self.web._on_pick, {"viewport_name": "Nope", "x": 0.5, "y": 0.5}).one
        self.assertEqual(r["error"], "not_found")
        for bad in ({"x": 1.5, "y": 0.5}, {"x": 0.5, "y": -1}, {"x": "a", "y": 0.5}):
            r = self.call(self.web._on_pick, {"viewport_name": "Viewport", **bad}).one
            self.assertEqual(r["error"], "invalid_argument", bad)
        # rejected query / no hydra texture: answered once (deferred), "unavailable", no 5 s wait
        for result in (False, None):
            self.api.result = result
            r = await self._deferred({"viewport_name": "Viewport", "x": 0.5, "y": 0.5}, frames=3)
            self.assertEqual(r["error"], "unavailable", result)
            self.assertIn("rejected", r["message"])

    async def test_pick_timeout_unavailable(self):
        from ..pick import Picker

        self.ov._picker = Picker({"Viewport": self.api}.get, timeout=0.05)
        self.api.respond = False
        ctx = self.call(self.web._on_pick, {"viewport_name": "Viewport", "x": 0.5, "y": 0.5})
        for _ in range(200):
            if ctx.replies:
                break
            await omni.kit.app.get_app().next_update_async()
        self.assertEqual(ctx.one["error"], "unavailable")

    async def test_pick_cancelled_by_shutdown(self):
        self.api.respond = False
        ctx = self.call(self.web._on_pick, {"viewport_name": "Viewport", "x": 0.5, "y": 0.5})
        self.ov._shutdown()
        await _updates(3)
        self.assertEqual(ctx.one["error"], "unavailable")


class TestAnnotationClickCapture(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.drawing = OverlayDrawing(self.ov)
        self.scene_view = sc.SceneView()
        self.clicks = []

    async def tearDown(self):
        self.drawing.stop()
        self.scene_view.destroy()

    def _scene(self, api):
        with self.scene_view.scene:
            return self.drawing.create_scene({"viewport_api": api})

    async def test_selected_is_highlighted(self):
        self.ov.add({**INLET, "id": "in1"})
        self.ov.add({**LABEL, "id": "a"})
        scene = self._scene(FakeQueryApi())
        self.assertIsNone(scene.selected_built)
        self.drawing.flush()
        self.ov.select("in1")
        self.assertTrue(self.drawing.dirty)  # selection change rebuilds
        self.assertTrue(self.drawing.flush())
        self.assertEqual(scene.selected_built, "in1")
        self.assertEqual(scene.built_count, 2)
        self.ov.select(None)
        self.drawing.flush()
        self.assertIsNone(scene.selected_built)
        c = highlight_color([0.2, 0.55, 1.0])
        self.assertTrue(all(b >= a for a, b in zip([0.2, 0.55, 1.0], c)))
        self.assertLessEqual(max(c), 1.0)
        self.assertGreater(SELECTED_STYLE.line, 2.0)

    async def test_ndc_to_image(self):
        api = FakeQueryApi()
        self.assertEqual(ndc_to_image(api, (-1.0, 1.0)), (0.0, 0.0))  # top-left
        self.assertEqual(ndc_to_image(api, (0.0, 0.0)), (0.5, 0.5))
        self.assertEqual(ndc_to_image(api, (1.0, -1.0)), (1.0, 1.0))
        self.assertIsNone(ndc_to_image(api, (1.5, 0.0)))  # outside the rendered image

    async def test_one_click_on_any_viewport(self):
        a, b = FakeQueryApi(), FakeQueryApi()
        sa, sb = self._scene(a), self._scene(b)
        self.drawing.arm_click(lambda api, x, y: self.clicks.append((api, x, y)))
        self.assertTrue(self.drawing.click_armed)
        self.assertTrue(sa.armed and sb.armed)
        sc_ = self._scene(FakeQueryApi())  # a viewport opened while armed is armed too
        self.assertTrue(sc_.armed)
        self.assertFalse(sb._on_pick_click((3.0, 0.0)))  # outside the image: ignored, still armed
        self.assertTrue(self.drawing.click_armed)
        self.assertTrue(sb._on_pick_click((0.0, 0.5)))
        self.assertFalse(self.drawing.click_armed)  # a second click is ignored right away
        sa._on_pick_click((0.0, 0.0))  # second click before the first is handled: dropped
        await _updates(1)  # screens are removed and the callback runs on the next loop iteration
        self.assertEqual(len(self.clicks), 1)
        api, x, y = self.clicks[0]
        self.assertIs(api, b)
        self.assertAlmostEqual(x, 0.5)
        self.assertAlmostEqual(y, 0.25)  # NDC y up -> image y down
        self.assertFalse(self.drawing.click_armed)  # one click only
        self.assertFalse(sa.armed or sb.armed or sc_.armed)
        sa._on_pick_click((0.0, 0.0))
        await _updates(1)
        self.assertEqual(len(self.clicks), 1)

    async def test_disarm_and_destroy(self):
        s = self._scene(FakeQueryApi())
        self.drawing.arm_click(lambda *a: self.clicks.append(a))
        self.drawing.disarm_click()
        self.assertFalse(s.armed)
        self.drawing.arm_click(lambda *a: self.clicks.append(a))
        s.destroy()  # destroying an armed scene is safe
        await _updates(2)
        self.assertEqual(self.clicks, [])
