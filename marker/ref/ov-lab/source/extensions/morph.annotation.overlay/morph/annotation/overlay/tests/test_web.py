__all__ = ["TestAnnotationWeb"]

import json
import os
import tempfile
from unittest import mock

import carb.settings
import omni.kit.test

from .. import MSG_STATE, AnnotationOverlay
from .. import sidecar as sidecar_mod
from ..overlay import EXT_ID, S_MAX, S_SHOW_DESC, S_TEXT_SCALE
from ..web import (
    MSG_ADD,
    MSG_LIST,
    MSG_RELOAD,
    MSG_REMOVE,
    MSG_SAVE,
    MSG_SET_OPTIONS,
    MSG_SET_VISIBLE,
    MSG_PICK,
    MSG_SELECT,
    MSG_UPDATE,
    MSG_PICK_FACE,
    MSG_CLEAR_FACE,
    WebBridge,
)

ALL_TYPES = (MSG_LIST, MSG_ADD, MSG_UPDATE, MSG_REMOVE, MSG_SET_VISIBLE, MSG_SET_OPTIONS, MSG_SAVE, MSG_RELOAD,
             MSG_PICK, MSG_SELECT, MSG_PICK_FACE, MSG_CLEAR_FACE)

LABEL = {"kind": "label", "position": [1, 2, 3], "label": "L"}


class FakeCtx:
    def __init__(self):
        self.replies = []

    def reply_ok(self, **fields):
        self.replies.append({"ok": True, **fields})

    def reply_error(self, code, message, **fields):
        self.replies.append({"ok": False, "error": code, "message": message, **fields})

    @property
    def one(self):
        assert len(self.replies) == 1, self.replies
        return self.replies[0]


class FakeMessaging:
    def __init__(self, connected=True):
        self.is_connected = connected
        self.sent = []

    def send(self, type_, payload, reply_to=None):
        json.dumps(payload, allow_nan=False)  # same constraint as stream
        self.sent.append((type_, payload))


class TestAnnotationWeb(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.scene = os.path.join(self._tmp.name, "scene.usda")
        self.file_scene = True
        self.ov = AnnotationOverlay(scene_file_provider=lambda: self.scene if self.file_scene else None)
        self.msg = FakeMessaging()
        self.web = WebBridge(self.ov, messaging_getter=lambda: self.msg)
        self.settings = carb.settings.get_settings()
        self.old_max = self.settings.get(S_MAX)
        self.old_scale = self.settings.get(S_TEXT_SCALE)
        self.old_show = self.settings.get(S_SHOW_DESC)

    async def tearDown(self):
        self.web.stop()
        self.settings.set_int(S_MAX, self.old_max if self.old_max is not None else 512)
        self.settings.set_float(S_TEXT_SCALE, float(self.old_scale) if self.old_scale is not None else 1.0)
        self.settings.set_string(S_SHOW_DESC, self.old_show if isinstance(self.old_show, str) else "always")
        self._tmp.cleanup()

    def assertNoDirectory(self, message):
        """Web messages carry file names only: no folder of the temp dir may appear."""
        for folder in (self._tmp.name, os.path.realpath(self._tmp.name)):
            self.assertNotIn(os.path.normcase(folder), os.path.normcase(message))
        self.assertNotIn(os.sep + "scene.annotations.json", message)

    def call(self, handler, payload):
        ctx = FakeCtx()
        handler(payload, ctx)
        return ctx.one

    async def test_types_registered_by_extension(self):
        from morph.stream.messaging import get_messaging

        msg = get_messaging()
        for t in ALL_TYPES:
            self.assertTrue(t.startswith(EXT_ID + "."))
            with self.assertRaises(ValueError, msg=t):  # already registered by the running extension
                sub = msg.register_inbound(t, lambda p, c: None, owner=EXT_ID)
                sub.unsubscribe()

    async def test_list(self):
        self.ov.add({**LABEL, "id": "a"})
        r = self.call(self.web._on_list, {})
        self.assertTrue(r["ok"])
        self.assertEqual([a["id"] for a in r["annotations"]], ["a"])
        self.assertTrue(r["visible"])
        self.assertEqual(r["file"], "scene.annotations.json")  # name only
        self.file_scene = False
        self.assertIsNone(self.call(self.web._on_list, {})["file"])

    async def test_add(self):
        r = self.call(self.web._on_add, {"annotation": LABEL})
        self.assertEqual(r, {"ok": True, "id": "label_1"})
        r = self.call(self.web._on_add, {"annotation": {"kind": "label"}})
        self.assertEqual(r["error"], "invalid_argument")
        self.assertIn("position", r["message"])
        self.settings.set_int(S_MAX, 1)
        r = self.call(self.web._on_add, {"annotation": LABEL})
        self.assertEqual(r["error"], "resource_exhausted")

    async def test_update(self):
        self.ov.add({**LABEL, "id": "a"})
        r = self.call(self.web._on_update, {"id": "a", "fields": {"label": "New"}})
        self.assertTrue(r["ok"])
        self.assertEqual(r["annotation"]["label"], "New")
        self.assertEqual(self.call(self.web._on_update, {"id": "zz", "fields": {}})["error"], "not_found")
        r = self.call(self.web._on_update, {"id": "a", "fields": {"position": [0, 0]}})
        self.assertEqual(r["error"], "invalid_argument")
        r = self.call(self.web._on_update, {"id": "a", "fields": {"kind": "plane"}})
        self.assertEqual(r["error"], "invalid_argument")

    async def test_update_null_resets_to_default(self):
        self.ov.add({"id": "in1", "kind": "inlet", "position": [0, 0, 0], "direction": [1, 0, 0], "size": 0.5,
                     "description": "DN50", "color": [0, 1, 0], "visible": False, "extra": 1})
        r = self.call(self.web._on_update, {"id": "in1", "fields": {
            "size": None, "description": None, "color": None, "visible": None, "extra": None}})
        self.assertTrue(r["ok"], r)
        ann = r["annotation"]
        self.assertEqual(ann["size"], 1.0)
        self.assertEqual(ann["description"], "")
        self.assertEqual(ann["color"], [0.2, 0.55, 1.0])
        self.assertTrue(ann["visible"])
        self.assertNotIn("extra", ann)
        self.assertEqual(self.ov.get("in1"), ann)
        # a required field cannot be reset
        r = self.call(self.web._on_update, {"id": "in1", "fields": {"position": None}})
        self.assertEqual(r["error"], "invalid_argument")
        self.assertEqual(self.ov.get("in1"), ann)

    async def test_remove(self):
        self.ov.add({**LABEL, "id": "a"})
        self.assertEqual(self.call(self.web._on_remove, {"id": "a"}), {"ok": True, "id": "a"})
        self.assertEqual(self.call(self.web._on_remove, {"id": "a"})["error"], "not_found")

    async def test_set_visible(self):
        self.ov.add({**LABEL, "id": "a"})
        self.assertEqual(self.call(self.web._on_set_visible, {"visible": False}), {"ok": True})
        self.assertFalse(self.ov.visible)
        self.assertEqual(self.call(self.web._on_set_visible, {"visible": False, "id": "a"}), {"ok": True})
        self.assertFalse(self.ov.get("a")["visible"])
        self.assertEqual(self.call(self.web._on_set_visible, {"visible": True, "id": "zz"})["error"], "not_found")
        self.assertEqual(self.call(self.web._on_set_visible, {"visible": "yes"})["error"], "invalid_argument")

    async def test_save_and_reload(self):
        self.ov.add({**LABEL, "id": "a"})
        r = self.call(self.web._on_save, {})
        self.assertEqual(r, {"ok": True, "file": "scene.annotations.json"})
        self.ov.clear()
        self.assertEqual(self.call(self.web._on_reload, {}), {"ok": True, "count": 1})
        with open(self.ov.current_file, "w", encoding="utf-8") as f:
            f.write("{broken")
        r = self.call(self.web._on_reload, {})
        self.assertEqual(r["error"], "failed_precondition")
        self.assertIn("scene.annotations.json", r["message"])
        self.assertNoDirectory(r["message"])
        self.assertEqual(len(self.ov.list()), 1)

    async def test_reload_bad_version_message_has_no_path(self):
        with open(self.ov.current_file, "w", encoding="utf-8") as f:
            json.dump({"version": 7, "annotations": []}, f)
        r = self.call(self.web._on_reload, {})
        self.assertEqual(r["error"], "failed_precondition")
        self.assertNoDirectory(r["message"])

    async def test_reload_io_error(self):
        os.mkdir(self.ov.current_file)  # a folder where the file should be: open() fails, not "missing"
        self.ov.add({**LABEL, "id": "a"})
        r = self.call(self.web._on_reload, {})
        self.assertEqual(r["error"], "io_error")
        self.assertIn("scene.annotations.json", r["message"])
        self.assertNoDirectory(r["message"])
        self.assertEqual(len(self.ov.list()), 1)  # list kept

    async def test_save_io_error_missing_folder(self):
        self.scene = os.path.join(self._tmp.name, "no_such_folder", "scene.usda")
        self.ov.add({**LABEL, "id": "a"})
        r = self.call(self.web._on_save, {})
        self.assertEqual(r["error"], "io_error")
        self.assertIn("scene.annotations.json", r["message"])
        self.assertNoDirectory(r["message"])
        self.assertNotIn("no_such_folder", r["message"])

    async def test_save_io_error_replace_denied(self):
        self.ov.add({**LABEL, "id": "a"})
        self.assertTrue(self.call(self.web._on_save, {})["ok"])
        with open(self.ov.current_file, encoding="utf-8") as f:
            before = f.read()
        self.ov.add({**LABEL, "id": "b"})

        def denied(src, dst):
            raise PermissionError(13, "Permission denied", src)  # filename = full temp path

        with mock.patch.object(sidecar_mod.os, "replace", side_effect=denied):
            r = self.call(self.web._on_save, {})
        self.assertEqual(r["error"], "io_error")
        self.assertIn("Permission denied", r["message"])
        self.assertNoDirectory(r["message"])
        self.assertNotIn(".tmp", r["message"])
        with open(self.ov.current_file, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)  # old file intact
        self.assertEqual([n for n in os.listdir(self._tmp.name) if n.endswith(".tmp")], [])

    async def test_set_options(self):
        r = self.call(self.web._on_set_options, {})
        self.assertEqual(set(r), {"ok", "show_description", "text_scale"})
        self.assertTrue(r["ok"])
        self.assertFalse(self.web.push_pending)  # nothing changed

        r = self.call(self.web._on_set_options, {"show_description": False, "text_scale": 0.8})
        self.assertEqual(r, {"ok": True, "show_description": False, "text_scale": 0.8})
        self.assertEqual(self.settings.get(S_SHOW_DESC), "never")
        self.assertAlmostEqual(self.settings.get(S_TEXT_SCALE), 0.8)
        self.assertTrue(self.web.push_pending)
        self.assertTrue(self.web.flush())
        _t, payload = self.msg.sent[-1]
        self.assertFalse(payload["show_description"])
        self.assertAlmostEqual(payload["text_scale"], 0.8)

        r = self.call(self.web._on_set_options, {"show_description": True})
        self.assertEqual(r["show_description"], True)
        self.assertAlmostEqual(r["text_scale"], 0.8)  # untouched
        self.assertEqual(self.settings.get(S_SHOW_DESC), "always")

        for bad in ({"show_description": "no"}, {"text_scale": True}, {"text_scale": 0.1},
                    {"text_scale": 5}, {"text_scale": "1"}, {"text_scale": float("nan")}):
            r = self.call(self.web._on_set_options, bad)
            self.assertEqual(r["error"], "invalid_argument", bad)
        self.assertAlmostEqual(self.settings.get(S_TEXT_SCALE), 0.8)  # failures change nothing

    async def test_list_has_options(self):
        r = self.call(self.web._on_list, {})
        self.assertIn("show_description", r)
        self.assertIn("text_scale", r)
        self.assertIn("selected_id", r)
        self.assertIsNone(r["selected_id"])
        self.assertIn("face", r)
        self.assertIsNone(r["face"])

    async def test_no_file_scene(self):
        self.file_scene = False
        self.assertEqual(self.call(self.web._on_save, {})["error"], "failed_precondition")
        self.assertEqual(self.call(self.web._on_reload, {})["error"], "failed_precondition")

    async def test_unavailable_after_stop(self):
        self.web.stop()
        self.assertEqual(self.call(self.web._on_list, {})["error"], "unavailable")
        self.assertEqual(self.call(self.web._on_add, {"annotation": LABEL})["error"], "unavailable")
        self.assertEqual(self.call(self.web._on_set_options, {"text_scale": 2})["error"], "unavailable")

    async def test_state_push_coalesced(self):
        self.ov.add(LABEL)
        self.ov.add(LABEL)
        self.ov.set_visible(False)
        self.assertTrue(self.web.flush())
        self.assertFalse(self.web.flush())  # nothing new
        self.assertEqual(len(self.msg.sent), 1)
        t, payload = self.msg.sent[0]
        self.assertEqual(t, MSG_STATE)
        self.assertEqual(len(payload["annotations"]), 2)
        self.assertFalse(payload["visible"])
        self.assertEqual(payload["file"], "scene.annotations.json")
        self.assertIsInstance(payload["show_description"], bool)
        self.assertIsInstance(payload["text_scale"], float)
        self.assertIsNone(payload["selected_id"])
        self.assertIsNone(payload["face"])

    async def test_state_push_on_connect_only_when_connected(self):
        self.msg.is_connected = False
        self.ov.add(LABEL)
        self.assertFalse(self.web.flush())
        self.assertEqual(self.msg.sent, [])
        self.msg.is_connected = True
        self.web.request_push()  # what client_connected does
        self.assertTrue(self.web.flush())
        self.assertEqual(len(self.msg.sent), 1)
