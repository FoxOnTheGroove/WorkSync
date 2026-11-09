__all__ = ["TestAnnotationEditorModel", "TestAnnotationEditorWindow", "TestAnnotationViewPick"]

import asyncio
import os
import tempfile

import carb.settings
import omni.kit.app
import omni.kit.test

from .. import AnnotationOverlay
from ..editor import EditorController, EditorModel, EditorWindow
from ..overlay import S_SHOW_DESC, S_TEXT_SCALE

HIT = {"position": [1.0, 2.0, 3.0], "normal": [0.0, 0.0, 1.0]}


async def _updates(n=3):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


class FakeArm:
    """Stands in for OverlayDrawing.arm_click / disarm_click."""

    def __init__(self):
        self.callback = None
        self.disarmed = 0

    def arm(self, cb):
        self.callback = cb

    def disarm(self):
        self.callback = None
        self.disarmed += 1

    def click(self, api="api", x=0.5, y=0.5):
        cb, self.callback = self.callback, None  # the drawing disarms after one click
        cb(api, x, y)


class TestAnnotationEditorModel(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.scene = os.path.join(self._tmp.name, "scene.usda")
        self.ov = AnnotationOverlay(scene_file_provider=lambda: self.scene)
        self.arm = FakeArm()
        self.pick_calls = []
        self.pick_result = HIT
        self.m = EditorModel(self.ov, arm_click=self.arm.arm, disarm_click=self.arm.disarm, pick_at=self._pick_at)
        self.refreshes = 0
        self.m.subscribe(self._refresh)

    async def tearDown(self):
        self.m.destroy()
        self._tmp.cleanup()

    def _refresh(self):
        self.refreshes += 1

    def _pick_at(self, api, x, y):
        self.pick_calls.append((api, x, y))
        fut = asyncio.get_event_loop().create_future()
        if isinstance(self.pick_result, BaseException):
            fut.set_exception(self.pick_result)
        else:
            fut.set_result(self.pick_result)
        return fut

    async def test_new_apply_adds_and_selects(self):
        self.m.new("inlet")
        self.assertTrue(self.m.is_new)
        self.m.set_new_id("in1")
        self.m.set_label("Inlet")
        self.m.set_description("냉각수")
        self.m.set_component("position", 0, -3.0)
        self.m.set_component("direction", 1, 0.0)
        self.m.set_component("size", 0, 0.8)
        self.assertTrue(self.m.apply(), self.m.error)
        ann = self.ov.get("in1")
        self.assertEqual(ann["kind"], "inlet")
        self.assertEqual(ann["label"], "Inlet")
        self.assertEqual(ann["position"], [-3.0, 0.0, 0.0])
        self.assertEqual(ann["size"], 0.8)
        self.assertEqual(self.ov.selected_id, "in1")
        self.assertFalse(self.m.is_new)
        self.assertEqual(self.m.editing_id, "in1")
        self.assertEqual(self.m.error, "")
        self.assertGreater(self.refreshes, 0)

    async def test_auto_id(self):
        self.m.new("label")
        self.assertTrue(self.m.apply())
        self.assertEqual(self.ov.selected_id, "label_1")

    async def test_edit_apply_updates_and_keeps_extra(self):
        self.ov.add({"id": "p", "kind": "plane", "origin": [0, 0, 0], "normal": [1, 0, 0], "up": [0, 0, 1],
                     "extra": {"k": 1}})
        self.m.select("p")
        self.assertEqual(self.m.editing_id, "p")
        self.assertTrue(self.m.use_up)
        self.m.set_label("Section")
        self.m.set_component("size", 0, 3.0)
        self.m.set_use_up(False)
        self.assertTrue(self.m.apply(), self.m.error)
        ann = self.ov.get("p")
        self.assertEqual(ann["label"], "Section")
        self.assertEqual(ann["size"], [3.0, 2.0])
        self.assertNotIn("up", ann)  # unchecked = stage up axis
        self.assertEqual(ann["extra"], {"k": 1})

    async def test_validation_error_shown_and_nothing_changed(self):
        self.ov.add({"id": "o", "kind": "outlet", "position": [0, 0, 0], "direction": [1, 0, 0]})
        self.m.select("o")
        before = self.ov.get("o")
        for i in range(3):
            self.m.set_component("direction", i, 0.0)  # zero vector
        self.assertFalse(self.m.apply())
        self.assertIn("direction", self.m.error)
        self.assertEqual(self.ov.get("o"), before)
        self.m.new("label")
        self.m.set_new_id("bad id!")
        self.assertFalse(self.m.apply())
        self.assertIn("id", self.m.error)
        self.assertEqual(len(self.ov.list()), 1)

    async def test_delete(self):
        self.ov.add({"id": "a", "kind": "label", "position": [0, 0, 0]})
        self.m.new("label")
        self.assertFalse(self.m.delete())  # nothing selected
        self.m.select("a")
        self.assertTrue(self.m.delete())
        self.assertIsNone(self.ov.get("a"))
        self.assertTrue(self.m.is_new)

    async def test_follows_external_selection_and_updates(self):
        self.ov.add({"id": "a", "kind": "label", "label": "A", "position": [0, 0, 0]})
        self.ov.select("a")  # e.g. from the web
        self.assertEqual(self.m.editing_id, "a")
        self.ov.update("a", label="from web")
        self.assertEqual(self.m.label, "from web")  # not dirty: reloaded
        self.m.set_label("typing")
        self.ov.update("a", description="d")
        self.assertEqual(self.m.label, "typing")  # unsaved edit kept
        self.ov.remove("a")
        self.assertTrue(self.m.is_new)

    async def test_set_kind_keeps_point(self):
        self.m.new("label")
        self.m.set_component("position", 2, 5.0)
        self.m.set_kind("plane")
        self.assertEqual(self.m.values["origin"], [0.0, 0.0, 5.0])
        self.assertEqual([f[0] for f in self.m.fields], ["origin", "normal", "size", "up"])
        self.m.set_kind("outlet")
        self.assertEqual(self.m.values["position"], [0.0, 0.0, 5.0])
        self.assertEqual(self.m.to_annotation()["size"], 1.0)

    async def _pick(self, kind, result=HIT):
        self.pick_result = result
        self.m.new(kind)
        self.assertTrue(self.m.begin_pick())
        self.assertTrue(self.m.picking)
        self.arm.click("vp", 0.25, 0.75)
        await _updates(2)
        self.assertFalse(self.m.picking)
        return self.m

    async def test_pick_fills_label(self):
        m = await self._pick("label")
        self.assertEqual(self.pick_calls, [("vp", 0.25, 0.75)])
        self.assertEqual(m.values["position"], [1.0, 2.0, 3.0])
        self.assertEqual(self.ov.list(), [])  # nothing applied yet
        self.assertTrue(m.apply())
        self.assertEqual(self.ov.get("label_1")["position"], [1.0, 2.0, 3.0])

    async def test_pick_inlet_flows_inward_outlet_outward(self):
        m = await self._pick("inlet")
        self.assertEqual(m.values["direction"], [-0.0, -0.0, -1.0])
        m = await self._pick("outlet")
        self.assertEqual(m.values["direction"], [0.0, 0.0, 1.0])
        m = await self._pick("outlet", {"position": [4.0, 0.0, 0.0], "normal": None})
        self.assertEqual(m.values["position"], [4.0, 0.0, 0.0])
        self.assertEqual(m.values["direction"], [1.0, 0.0, 0.0])  # unchanged without a normal

    async def test_pick_plane_sets_origin_only(self):
        self.ov.add({"id": "p", "kind": "plane", "origin": [0, 0, 0], "normal": [0, 1, 0]})
        self.m.select("p")
        self.m.begin_pick()
        self.arm.click()
        await _updates(2)
        self.assertEqual(self.m.values["origin"], [1.0, 2.0, 3.0])
        self.assertEqual(self.m.values["normal"], [0.0, 1.0, 0.0])
        self.assertTrue(self.m.apply())
        self.assertEqual(self.ov.get("p")["origin"], [1.0, 2.0, 3.0])

    async def test_pick_miss_and_failure(self):
        m = await self._pick("label", None)
        self.assertIn("빈 곳", m.error)
        m = await self._pick("label", TimeoutError("slow"))
        self.assertIn("slow", m.error)

    async def test_cancel_pick(self):
        self.m.begin_pick()
        self.m.cancel_pick()
        self.assertFalse(self.m.picking)
        self.assertEqual(self.arm.disarmed, 1)
        self.assertIsNone(self.arm.callback)
        no_arm = EditorModel(self.ov)
        self.assertFalse(no_arm.begin_pick())  # no drawing: error, no crash
        self.assertTrue(no_arm.error)
        no_arm.destroy()

    async def test_reload_and_save(self):
        self.ov.add({"id": "a", "kind": "label", "position": [0, 0, 0]})
        self.assertTrue(self.m.save(), self.m.error)
        self.assertIn("scene.annotations.json", self.m.status)
        self.ov.clear()
        self.assertTrue(self.m.reload())
        self.assertEqual(len(self.ov.list()), 1)
        self.scene = None
        self.assertFalse(self.m.save())
        self.assertTrue(self.m.error)


class TestAnnotationEditorWindow(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.settings = carb.settings.get_settings()
        self.old_scale = self.settings.get(S_TEXT_SCALE)
        self.old_show = self.settings.get(S_SHOW_DESC)

    async def tearDown(self):
        self.settings.set_float(S_TEXT_SCALE, float(self.old_scale) if self.old_scale is not None else 1.0)
        self.settings.set_string(S_SHOW_DESC, self.old_show if isinstance(self.old_show, str) else "always")

    async def test_window_build_and_close(self):
        self.ov.add({"id": "a", "kind": "plane", "label": "A", "origin": [0, 0, 0], "normal": [1, 0, 0]})
        model = EditorModel(self.ov, arm_click=lambda cb: None, disarm_click=lambda: None)
        win = EditorWindow(model)
        try:
            # build every form variant explicitly (no frame drawing needed)
            for step in (lambda: None, lambda: model.select("a"), lambda: model.set_use_up(True),
                         lambda: model.new("inlet"), model.begin_pick, lambda: model.new("outlet")):
                step()
                model.error = "error text"
                win.window.frame.clear()
                with win.window.frame:
                    win._build()
            self.assertGreaterEqual(win.build_count, 6)
            model.set_text_scale(9.0)  # clamped
            self.assertAlmostEqual(self.settings.get(S_TEXT_SCALE), 4.0)
            model.set_show_description(False)
            self.assertEqual(self.settings.get(S_SHOW_DESC), "never")
            await _updates(3)
            model.begin_pick()
            win._on_visibility(False)  # what closing / hiding the window calls
            self.assertFalse(model.picking)  # hiding cancels a pending pick
            win.visible = False
            await _updates(1)
            self.assertFalse(win.visible)
        finally:
            win.destroy()
            model.destroy()
        self.assertTrue(win.visibility_fn_cleared)  # our visibility callback was replaced on destroy
        self.assertIsNone(win.window)
        await _updates(2)

    async def test_dock_into_panel(self):
        import omni.ui as ui

        target = ui.Window("MORPH Annotations Test Dock Target", width=200, height=200, visible=True)
        model = EditorModel(self.ov)
        win = EditorWindow(model)
        try:
            await _updates(2)
            self.assertIsNone(win.dock_to_panel(("No Such Window",)))  # no target: floating, no error
            self.assertIsNone(win.docked_to)
            title = win.dock_to_panel(("No Such Window", "MORPH Annotations Test Dock Target"))
            self.assertEqual(title, "MORPH Annotations Test Dock Target")  # first existing target wins
            self.assertEqual(win.docked_to, title)
            await _updates(3)
            self.assertTrue(win.visible)
        finally:
            win.destroy()
            model.destroy()
            target.destroy()
        await _updates(2)

    async def test_dock_later_and_destroy_before(self):
        model = EditorModel(self.ov)
        win = EditorWindow(model)
        win.dock_later(("No Such Window",), frames=1)
        await _updates(3)
        self.assertIsNone(win.docked_to)  # nothing to dock into: stays floating
        win.dock_later(("No Such Window",), frames=5)
        win.destroy()  # pending docking is cancelled
        model.destroy()
        await _updates(6)
        self.assertIsNone(win.docked_to)

    async def test_controller_docks_on_first_show(self):
        import omni.ui as ui

        target = ui.Window("MORPH Annotations Test Panel", width=200, height=200, visible=True)
        ctl = EditorController(self.ov, drawing=None, font_path="", dock_targets=("MORPH Annotations Test Panel",))
        try:
            ctl.show()
            await _updates(5)
            self.assertEqual(ctl.window.docked_to, "MORPH Annotations Test Panel")
            self.assertEqual(ctl.window.font_path, "")  # "": no font style, app font
        finally:
            ctl.destroy()
            target.destroy()
        await _updates(2)


class FakeSelectionToken:
    """Stands in for omni.kit.viewport.utility.disable_selection(...): restores on deletion."""

    alive = 0

    def __init__(self, api):
        self.api = api
        FakeSelectionToken.alive += 1

    def __del__(self):
        FakeSelectionToken.alive -= 1


class TestAnnotationViewPick(omni.kit.test.AsyncTestCase):
    """AnnotationOverlay.is_picking / cancel_pick with the editor + drawing (Kit click selection restore)."""

    async def setUp(self):
        from omni.ui import scene as sc

        from .. import drawing as drawing_mod
        from ..drawing import OverlayDrawing
        from .test_pick import FakeQueryApi

        self._drawing_mod = drawing_mod
        self._orig_block = drawing_mod._block_selection
        FakeSelectionToken.alive = 0
        drawing_mod._block_selection = lambda api: FakeSelectionToken(api) if api is not None else None
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.drawing = OverlayDrawing(self.ov)
        self.scene_view = sc.SceneView()
        with self.scene_view.scene:
            self.scenes = [self.drawing.create_scene({"viewport_api": FakeQueryApi()}) for _ in range(2)]
        self.ctl = EditorController(self.ov, self.drawing, font_path="", dock_targets=())
        self.ov._set_view_pick(self.ctl)

    async def tearDown(self):
        self.ctl.destroy()
        self.drawing.stop()
        self.scene_view.destroy()
        self._drawing_mod._block_selection = self._orig_block
        self.ov._shutdown()

    async def test_no_editor_yet(self):
        self.assertFalse(self.ov.is_picking)
        self.ov.cancel_pick()  # no window / nothing pending: no-op
        bare = AnnotationOverlay(scene_file_provider=lambda: None)
        self.assertFalse(bare.is_picking)  # no editor (e.g. streaming app / tests)
        bare.cancel_pick()

    async def test_cancel_restores_selection_on_every_viewport(self):
        self.ctl.show()
        self.assertTrue(self.ctl.model.begin_pick())
        self.assertTrue(self.ov.is_picking)
        self.assertTrue(all(s.armed for s in self.scenes))
        self.assertEqual(FakeSelectionToken.alive, 2)  # Kit click selection off on both viewports
        self.ov.cancel_pick()
        self.assertFalse(self.ov.is_picking)
        self.assertFalse(self.drawing.click_armed)
        self.assertFalse(any(s.armed for s in self.scenes))
        self.assertEqual(FakeSelectionToken.alive, 0)  # tokens released = selection layers restored
        self.ov.cancel_pick()  # again: no-op

    async def test_hide_window_restores_selection(self):
        self.ctl.show()
        self.ctl.model.begin_pick()
        self.assertEqual(FakeSelectionToken.alive, 2)
        self.ctl.window._on_visibility(False)  # what closing / hiding the window calls
        self.assertFalse(self.ov.is_picking)
        self.assertEqual(FakeSelectionToken.alive, 0)

    async def test_click_restores_selection(self):
        self.ctl.show()
        self.ctl.model.begin_pick()
        self.assertTrue(self.scenes[1]._on_pick_click((0.0, 0.0)))
        await _updates(3)  # click handled, pick resolved (fake viewport answers)
        self.assertFalse(self.ov.is_picking)
        self.assertEqual(FakeSelectionToken.alive, 0)
        self.assertEqual(self.ctl.model.values["position"][2], -5.0)  # filled from the hit

    async def test_controller_show_hide_destroy(self):
        listeners = len(self.ov._listeners)
        ctl = EditorController(self.ov, drawing=None)
        self.assertFalse(ctl.visible)
        ctl.show()
        self.assertTrue(ctl.visible)
        self.assertIsNotNone(ctl.model)
        await _updates(2)
        ctl.toggle()
        self.assertFalse(ctl.visible)
        ctl.toggle()
        self.assertTrue(ctl.visible)
        try:
            import omni.kit.menu.utils  # noqa: F401

            has_menu = True
        except ImportError:
            has_menu = False
        added = ctl.add_menu()
        self.assertIs(added, has_menu)  # True only when omni.kit.menu.utils is loaded, never raises
        ctl.destroy()
        self.assertIsNone(ctl.window)
        self.assertIsNone(ctl.model)
        self.assertEqual(len(self.ov._listeners), listeners)  # the model unsubscribed
        await _updates(2)
