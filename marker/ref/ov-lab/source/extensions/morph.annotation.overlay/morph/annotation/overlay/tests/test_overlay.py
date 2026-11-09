__all__ = ["TestAnnotationOverlayApi"]

import carb.settings
import omni.kit.app
import omni.kit.test
from carb.eventdispatcher import get_eventdispatcher

from .. import EVENT_CHANGED, EVENT_FACE_SELECTED, FACE_KINDS, KINDS, MSG_STATE, AnnotationOverlay, get_overlay
from ..overlay import S_MAX

LABEL = {"kind": "label", "position": [1, 2, 3], "label": "L"}
INLET = {"kind": "inlet", "position": [0, 0, 0], "direction": [1, 0, 0]}
PLANE = {"kind": "plane", "origin": [0, 0, 1], "normal": [1, 0, 0]}


class TestAnnotationOverlayApi(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None)
        self.events = []
        self.sub = get_eventdispatcher().observe_event(
            observer_name="morph.annotation.overlay.tests",
            event_name=EVENT_CHANGED,
            on_event=lambda e: self.events.append((e["reason"], list(e["ids"]), e["count"])),
        )
        self.settings = carb.settings.get_settings()
        self.old_max = self.settings.get(S_MAX)

    async def tearDown(self):
        self.sub = None
        self.settings.set_int(S_MAX, self.old_max if self.old_max is not None else 512)

    async def test_public_symbols(self):
        self.assertEqual(EVENT_CHANGED, "morph.annotation.changed")
        self.assertEqual(MSG_STATE, "morph.annotation.overlay.state")
        self.assertIn("plane", KINDS)
        self.assertIsNotNone(get_overlay())
        import morph.annotation.overlay as pkg

        self.assertEqual(
            set(pkg.__all__),
            {"get_overlay", "AnnotationOverlay", "KINDS", "EVENT_CHANGED", "EVENT_SELECTION_CHANGED",
             "EVENT_FACE_SELECTED", "FACE_KINDS", "MSG_STATE"},
        )
        self.assertEqual(pkg.EVENT_SELECTION_CHANGED, "morph.annotation.selection_changed")
        self.assertEqual(EVENT_FACE_SELECTED, "morph.annotation.face_selected")
        self.assertEqual(FACE_KINDS, ("planar", "cylinder_side", "sphere", "polygon", "point"))
        self.assertFalse(hasattr(AnnotationOverlay, "closed"))

    async def test_add_generates_ids(self):
        a = self.ov.add(LABEL)
        b = self.ov.add(LABEL)
        c = self.ov.add({**INLET, "id": "in1"})
        self.assertEqual((a, b, c), ("label_1", "label_2", "in1"))
        self.assertEqual([x["id"] for x in self.ov.list()], ["label_1", "label_2", "in1"])
        self.assertEqual(self.events, [("add", ["label_1"], 1), ("add", ["label_2"], 2), ("add", ["in1"], 3)])

    async def test_add_duplicate_and_invalid(self):
        self.ov.add({**LABEL, "id": "x"})
        with self.assertRaises(ValueError):
            self.ov.add({**LABEL, "id": "x"})
        with self.assertRaises(ValueError):
            self.ov.add({"kind": "label"})
        self.assertEqual(len(self.ov.list()), 1)
        self.assertEqual(len(self.events), 1)

    async def test_max(self):
        self.settings.set_int(S_MAX, 2)
        self.ov.add(LABEL)
        self.ov.add(LABEL)
        with self.assertRaises(ValueError):
            self.ov.add(LABEL)
        with self.assertRaises(ValueError):
            self.ov.set_all([LABEL, LABEL, LABEL])
        self.assertEqual(len(self.ov.list()), 2)

    async def test_list_and_get_are_copies(self):
        self.ov.add({**LABEL, "id": "a", "meta": {"k": 1}})
        got = self.ov.get("a")
        got["meta"]["k"] = 2
        got["position"][0] = 99
        self.ov.list()[0]["label"] = "changed"
        self.assertEqual(self.ov.get("a")["meta"], {"k": 1})
        self.assertEqual(self.ov.get("a")["position"][0], 1.0)
        self.assertEqual(self.ov.get("a")["label"], "L")
        self.assertIsNone(self.ov.get("nope"))

    async def test_update(self):
        self.ov.add({**INLET, "id": "in1", "extra": 5})
        new = self.ov.update("in1", description="DN65", size=0.5)
        self.assertEqual(new["description"], "DN65")
        self.assertEqual(new["size"], 0.5)
        self.assertEqual(new["extra"], 5)
        self.assertEqual(self.events[-1], ("update", ["in1"], 1))
        # None resets an optional field to its default
        self.assertEqual(self.ov.update("in1", size=None)["size"], 1.0)

    async def test_update_failures_leave_state(self):
        self.ov.add({**INLET, "id": "in1"})
        before = self.ov.get("in1")
        n = len(self.events)
        with self.assertRaises(KeyError):
            self.ov.update("nope", label="x")
        with self.assertRaises(ValueError):
            self.ov.update("in1", direction=[0, 0, 0])
        with self.assertRaises(ValueError):
            self.ov.update("in1", kind="outlet")
        with self.assertRaises(ValueError):
            self.ov.update("in1", id="other")
        with self.assertRaises(ValueError):
            self.ov.update("in1", position=None)  # required
        self.assertEqual(self.ov.get("in1"), before)
        self.assertEqual(len(self.events), n)

    async def test_remove_and_clear(self):
        self.ov.add({**LABEL, "id": "a"})
        self.ov.add({**LABEL, "id": "b"})
        self.ov.remove("a")
        self.assertEqual(self.events[-1], ("remove", ["a"], 1))
        with self.assertRaises(KeyError):
            self.ov.remove("a")
        self.ov.clear()
        self.assertEqual(self.events[-1], ("clear", [], 0))
        self.assertEqual(self.ov.list(), [])

    async def test_set_all(self):
        self.ov.add({**LABEL, "id": "old"})
        self.ov.set_all([{**LABEL, "id": "label_1"}, LABEL, PLANE])
        self.assertEqual([a["id"] for a in self.ov.list()], ["label_1", "label_2", "plane_1"])
        self.assertEqual(self.events[-1], ("set_all", [], 3))
        before = self.ov.list()
        with self.assertRaises(ValueError):
            self.ov.set_all([LABEL, {"kind": "plane"}])
        with self.assertRaises(ValueError):
            self.ov.set_all([{**LABEL, "id": "d"}, {**LABEL, "id": "d"}])
        self.assertEqual(self.ov.list(), before)

    async def test_visible(self):
        self.assertTrue(self.ov.visible)
        self.ov.set_visible(False)
        self.assertFalse(self.ov.visible)
        self.assertEqual(self.events[-1], ("visible", [], 0))
        n = len(self.events)
        self.ov.set_visible(False)  # no change, no event
        self.assertEqual(len(self.events), n)

    async def test_singleton_visible_follows_setting(self):
        ov = get_overlay()
        settings = carb.settings.get_settings()
        path = "/exts/morph.annotation.overlay/visible"
        try:
            settings.set_bool(path, False)
            await omni.kit.app.get_app().next_update_async()
            self.assertFalse(ov.visible)
            ov.set_visible(True)
            self.assertTrue(settings.get(path))
        finally:
            settings.set_bool(path, True)
            ov.set_visible(True)

    async def test_no_file(self):
        self.assertIsNone(self.ov.current_file)
        with self.assertRaises(RuntimeError):
            self.ov.save()
        with self.assertRaises(RuntimeError):
            self.ov.load()
