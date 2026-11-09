__all__ = ["TestAnnotationDrawing"]

import omni.kit.app
import omni.kit.test
import omni.ui as ui
from omni.ui import scene as sc
from pxr import Gf

import carb.settings

from .. import AnnotationOverlay
from ..drawing import (
    DESC_LINE_CHARS,
    DESC_MAX_LINES,
    FACTORY_ID,
    M_REBUILD,
    SMALL_VIEW_FACTOR,
    OverlayDrawing,
    build_annotation,
    plane_basis,
    tag_text_widths,
    text_width,
    view_text_factor,
    wrap_text,
)
from ..overlay import S_SHOW_DESC, S_TEXT_SCALE

ANNS = [
    {"id": "in1", "kind": "inlet", "label": "Inlet", "description": "냉각수 입구 DN50",
     "position": [-3.0, 0.0, -5.0], "direction": [1, 0, 0], "size": 0.8},
    {"kind": "outlet", "label": "Outlet A", "position": [3.0, 0.0, -5.0], "direction": [1, 0, 0]},
    {"kind": "plane", "label": "Section A-A", "origin": [0.0, 0.0, -5.0], "normal": [1, 0, 0], "size": [1.5, 1.5]},
    {"kind": "label", "label": "Valve V-101", "description": "x" * 300, "position": [1.2, 0.0, -5.0]},
    {"kind": "label", "position": [0.0, 0.0, -6.0]},  # no text: dot only
]

# Perspective projection (Gf row-vector convention): [3][3] == 0
PERSPECTIVE = Gf.Matrix4d(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -1, 0, 0, -0.2, 0)


class _Sub:
    def __init__(self, api):
        self._api = api

    def destroy(self):
        self._api.callbacks = []


class FakeViewportApi:
    def __init__(self, resolution=(1280, 720)):
        self.view = Gf.Matrix4d(1.0)  # camera at origin looking down -Z
        self.projection = PERSPECTIVE
        self.resolution = resolution
        self.callbacks = []

    def subscribe_to_view_change(self, cb):
        self.callbacks.append(cb)
        return _Sub(self)

    def move_camera_z(self, z):
        self.view = Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0, 0, z)).GetInverse()
        self._notify()

    def resize(self, w, h):
        self.resolution = (w, h)
        self._notify()

    def _notify(self):
        for cb in list(self.callbacks):
            cb(self)


class TestAnnotationDrawing(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.drawing = OverlayDrawing(self.ov)
        # Standalone SceneView (tests run with --no-window; the viewport layer does the same inside a frame)
        self.scene_view = sc.SceneView()
        self.settings = carb.settings.get_settings()
        self.old_scale = self.settings.get(S_TEXT_SCALE)
        self.old_show = self.settings.get(S_SHOW_DESC)

    async def tearDown(self):
        self.drawing.stop()
        self.scene_view.destroy()
        self.scene_view = None
        self.settings.set_float(S_TEXT_SCALE, float(self.old_scale) if self.old_scale is not None else 1.0)
        self.settings.set_string(S_SHOW_DESC, self.old_show if isinstance(self.old_show, str) else "always")

    def _make_scene(self, api=None):
        with self.scene_view.scene:
            return self.drawing.create_scene({"viewport_api": api} if api else {})

    async def test_plane_basis(self):
        n, h, v = plane_basis([2, 0, 0], [0, 0, 1])
        self.assertAlmostEqual(n[0], 1.0)
        self.assertAlmostEqual(abs(v[2]), 1.0)
        for a, b in ((n, h), (n, v), (h, v)):
            self.assertAlmostEqual(sum(x * y for x, y in zip(a, b)), 0.0)
        # up parallel to normal: falls back to another axis
        n, h, v = plane_basis([0, 0, 1], [0, 0, 1])
        self.assertAlmostEqual(sum(x * y for x, y in zip(n, v)), 0.0)
        self.assertAlmostEqual(sum(x * x for x in v), 1.0)

    async def test_build_without_viewport(self):
        self.ov.set_all(ANNS)
        scene = self._make_scene()  # no viewport_api: no culling, no crash
        self.assertEqual(scene.built_count, len(ANNS))
        await omni.kit.app.get_app().next_update_async()

    async def test_build_each_kind_standalone(self):
        from ..model import normalize

        with self.scene_view.scene:
            for ann in ANNS:
                self.assertIsNotNone(build_annotation(sc, ui, normalize(ann), 2.0, [0, 1, 0]))

    async def test_rebuild_only_when_dirty(self):
        scene = self._make_scene()
        self.assertEqual(scene.built_count, 0)
        self.drawing.flush()
        self.assertFalse(self.drawing.flush())  # nothing changed
        self.ov.add(ANNS[0])
        self.ov.add(ANNS[1])
        self.assertTrue(self.drawing.dirty)
        self.assertTrue(self.drawing.flush())  # one rebuild for both changes
        self.assertEqual(scene.built_count, 2)
        self.assertFalse(self.drawing.flush())

    async def test_visibility(self):
        self.ov.set_all(ANNS)
        scene = self._make_scene()
        self.ov.update("in1", visible=False)
        self.drawing.flush()
        self.assertEqual(scene.built_count, len(ANNS) - 1)
        self.ov.set_visible(False)
        self.drawing.flush()
        self.assertEqual(scene.built_count, 0)
        self.ov.set_visible(True)
        self.drawing.flush()
        self.assertEqual(scene.built_count, len(ANNS) - 1)
        scene.visible = False  # per-viewport toggle (viewport menu)
        self.assertFalse(scene.visible)

    async def test_behind_camera_hidden(self):
        api = FakeViewportApi()
        self.ov.add({"kind": "label", "label": "front", "position": [0, 0, -5]})
        self.ov.add({"kind": "label", "label": "behind", "position": [0, 0, 5]})
        scene = self._make_scene(api)
        self.assertEqual(scene.shown_count, 1)
        api.move_camera_z(10)  # both in front now
        self.assertEqual(scene.shown_count, 2)
        scene.destroy()
        self.assertEqual(api.callbacks, [])
        self.assertEqual(self.drawing.scenes, [])

    async def test_perf_scope_recorded(self):
        from morph.perf.monitor import get_monitor

        self.ov.add(ANNS[3])
        self._make_scene()
        mon = get_monitor()
        if mon is None or not mon.enabled:
            self.skipTest("morph.perf.monitor not running or disabled")
        self.assertIn(M_REBUILD, mon.flush())

    async def test_wrap_description(self):
        self.assertEqual(wrap_text(""), [])
        self.assertEqual(wrap_text("  a   b  "), ["a b"])
        short = "냉각수 입구 DN50"
        self.assertEqual(wrap_text(short), [short])
        lines = wrap_text("x" * 300)  # one long word: hard cut, 2 lines, ellipsis
        self.assertEqual(len(lines), DESC_MAX_LINES)
        self.assertTrue(all(len(line) <= DESC_LINE_CHARS for line in lines))
        self.assertTrue(lines[-1].endswith("…"))
        words = " ".join(["word"] * 30)  # 149 chars
        lines = wrap_text(words)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("word word"))
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(len(line) <= DESC_LINE_CHARS for line in lines))
        exact = " ".join(["abcd"] * 16)  # 79 chars: fits in 2 lines, no ellipsis
        self.assertEqual(" ".join(wrap_text(exact)), exact)

    async def test_box_covers_text(self):
        fs = 14.0
        # wide glyphs (Hangul) are estimated at ~1 em, other glyphs above 0.6 em
        self.assertGreaterEqual(text_width("냉각수", fs), 3 * fs)
        self.assertGreater(text_width("Valve", fs), 5 * 0.6 * fs)
        mixed = "냉각수 입구 DN50 (Coolant inlet)"
        self.assertGreater(text_width(mixed, fs), len(mixed) * 0.6 * fs)
        desc_lines = wrap_text("수동 볼밸브 " * 12)
        widths = tag_text_widths("Valve V-101", desc_lines, fs, fs * 0.8)
        self.assertEqual(len(widths), 1 + len(desc_lines))
        self.assertEqual(tag_text_widths("", [], fs, fs), [0.0])

    async def test_show_description_setting(self):
        self.ov.set_all(ANNS)
        scene = self._make_scene()
        self.settings.set_string(S_SHOW_DESC, "never")
        self.assertFalse(self.drawing._params()[4])
        self.drawing.mark_dirty()
        self.assertTrue(self.drawing.flush())
        self.assertEqual(scene.built_count, len(ANNS))  # shapes stay, only descriptions go
        self.settings.set_string(S_SHOW_DESC, "always")
        self.assertTrue(self.drawing._params()[4])
        with self.scene_view.scene:  # description-only label with descriptions hidden: dot only
            self.assertIsNotNone(build_annotation(
                sc, ui, {"kind": "label", "label": "", "description": "d", "color": [1, 1, 1],
                         "position": [0, 0, 0]}, 1.0, [0, 1, 0], False))

    async def test_small_view_shrinks_text(self):
        self.settings.set_float(S_TEXT_SCALE, 1.5)
        self.assertEqual(view_text_factor(None), 1.0)
        self.assertEqual(view_text_factor(object()), 1.0)  # no resolution: unchanged
        api = FakeViewportApi(resolution=(480, 259))  # one view of a 2x2 split
        self.ov.add(ANNS[3])
        scene = self._make_scene(api)
        self.assertAlmostEqual(scene.text_scale_used, 1.5 * SMALL_VIEW_FACTOR)
        self.drawing.flush()
        self.assertFalse(self.drawing.dirty)
        api.resize(1920, 1080)  # back to a big view: factor flips, rebuild requested
        self.assertTrue(self.drawing.dirty)
        self.assertTrue(self.drawing.flush())
        self.assertAlmostEqual(scene.text_scale_used, 1.5)
        api.move_camera_z(1)  # camera move alone does not rebuild
        self.assertFalse(self.drawing.dirty)

    async def test_registry_factory(self):
        try:
            from omni.kit.viewport.registry import RegisterScene
        except ImportError:
            self.skipTest("omni.kit.viewport.registry not loaded")
        events = []

        def notifier(factory, loading):
            events.append((factory, loading))

        RegisterScene.add_notifier(notifier)
        try:
            before = {f for f, _ in events}
            self.assertTrue(self.drawing.start())
            self.assertIsNotNone(self.drawing._enable_hook)  # follows later disable / enable too
            ours = [f for f, loading in events if loading and f not in before]
            self.assertEqual(len(ours), 1)
            self.assertEqual(ours[0].factory_id, FACTORY_ID)
            # what ViewportSceneLayer does for every viewport window
            self.ov.add(ANNS[2])
            with self.scene_view.scene:
                with sc.Transform():
                    item = ours[0]({"viewport_api": FakeViewportApi()})
            self.assertEqual(item.built_count, 1)
            self.assertEqual(item.name, "Annotations")
            self.assertTrue(item.visible)
            self.drawing.stop()
            self.assertIn((ours[0], False), events)
            item.destroy()  # layer would do this on unload; must be safe twice
        finally:
            RegisterScene.remove_notifier(notifier)
