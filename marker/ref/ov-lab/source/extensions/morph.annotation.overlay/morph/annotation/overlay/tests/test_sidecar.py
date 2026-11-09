__all__ = ["TestAnnotationSidecar"]

import json
import os
import stat
import sys
import tempfile
from unittest import mock

import carb.settings
import omni.kit.app
import omni.kit.test
import omni.usd

from .. import AnnotationOverlay, get_overlay
from .. import sidecar as sidecar_mod
from ..sidecar import read_sidecar, sidecar_path, write_sidecar

USDA = "#usda 1.0\n(\n    upAxis = \"Z\"\n)\n\ndef Xform \"World\"\n{\n}\n"

ANNS = [
    {"id": "in1", "kind": "inlet", "label": "Inlet", "description": "냉각수 입구 DN50",
     "position": [-3.0, 0.0, 1.0], "direction": [1, 0, 0], "size": 0.8, "vendor": {"tag": "P-100"}},
    {"kind": "outlet", "label": "Outlet A", "position": [3.0, 0.0, 1.0], "direction": [1, 0, 0]},
    {"kind": "plane", "label": "Section A-A", "origin": [0.0, 0.0, 1.0], "normal": [1, 0, 0], "size": [1.5, 1.5]},
    {"kind": "label", "label": "Valve V-101", "description": "수동 볼밸브", "position": [1.2, 0.0, 1.4]},
]


async def _updates(n=5):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class TestAnnotationSidecar(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.scene = os.path.join(self.dir, "pipe_manifold.usda")
        self.ov = AnnotationOverlay(scene_file_provider=lambda: self.scene)

    async def tearDown(self):
        ctx = omni.usd.get_context()
        if ctx.get_stage() is not None and not ctx.get_stage().GetRootLayer().anonymous:
            await ctx.new_stage_async()
            await _updates(2)
        self._tmp.cleanup()

    def _write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    async def test_path(self):
        self.assertTrue(_same_path(self.ov.current_file, os.path.join(self.dir, "pipe_manifold.annotations.json")))
        self.assertIsNone(sidecar_path(None))
        self.assertTrue(sidecar_path("C:/x/a.b.usd", ".notes.json").endswith("a.b.notes.json"))

    async def test_save_load_roundtrip(self):
        self.ov.set_all(ANNS)
        written = self.ov.save()
        self.assertTrue(_same_path(written, self.ov.current_file))
        with open(written, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
        self.assertEqual(data["version"], 1)
        self.assertIn("\n  \"annotations\"", text)  # 2-space indent
        self.assertIn("냉각수", text)  # UTF-8, not escaped
        self.assertEqual([a["id"] for a in data["annotations"]], ["in1", "outlet_1", "plane_1", "label_1"])
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

        other = AnnotationOverlay(scene_file_provider=lambda: self.scene)
        self.assertEqual(other.load(), 4)
        self.assertEqual(other.list(), self.ov.list())
        self.assertEqual(other.get("in1")["vendor"], {"tag": "P-100"})  # extra field kept

    async def test_explicit_path(self):
        self.ov.add(ANNS[3])
        target = os.path.join(self.dir, "elsewhere.json")
        self.assertTrue(_same_path(self.ov.save(target), target))
        other = AnnotationOverlay(scene_file_provider=lambda: None)
        self.assertEqual(other.load(target), 1)

    async def test_missing_file_is_empty(self):
        self.ov.add(ANNS[3])
        self.assertEqual(self.ov.load(), 0)
        self.assertEqual(self.ov.list(), [])

    async def test_broken_json_keeps_list(self):
        self.ov.add(ANNS[3])
        self._write("pipe_manifold.annotations.json", "{ not json")
        with self.assertRaises(ValueError):
            self.ov.load()
        self.assertEqual(len(self.ov.list()), 1)

    async def test_error_messages_have_file_name_only(self):
        for text in ("{ not json", "[]", json.dumps({"version": 0}), json.dumps({"version": 3}),
                     json.dumps({"annotations": {}})):
            path = self._write("pipe_manifold.annotations.json", text)
            with self.assertRaises(ValueError) as cm:
                read_sidecar(path)
            msg = str(cm.exception)
            self.assertIn("pipe_manifold.annotations.json", msg)
            self.assertNotIn(os.path.normcase(self.dir), os.path.normcase(msg))

    async def test_failed_replace_keeps_old_file(self):
        self.ov.set_all(ANNS)
        path = self.ov.save()
        with open(path, "rb") as f:
            before = f.read()
        self.ov.clear()
        with mock.patch.object(sidecar_mod.os, "replace", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(OSError):
                self.ov.save()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])
        self.assertEqual(AnnotationOverlay(scene_file_provider=lambda: self.scene).load(), len(ANNS))

    async def test_failed_write_keeps_old_file(self):
        path = write_sidecar(os.path.join(self.dir, "keep.json"), [])
        with open(path, "rb") as f:
            before = f.read()
        with mock.patch.object(sidecar_mod.os, "fsync", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                write_sidecar(path, [{"id": "a", "kind": "label", "position": [0, 0, 0]}])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])

    async def test_save_keeps_permissions(self):
        if sys.platform == "win32":
            self.skipTest("POSIX permission bits only")
        path = write_sidecar(os.path.join(self.dir, "perm.json"), [])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)  # new file: not mkstemp's 0600
        os.chmod(path, 0o640)
        write_sidecar(path, [])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    async def test_version_too_new(self):
        self.ov.add(ANNS[3])
        self._write("pipe_manifold.annotations.json", json.dumps({"version": 2, "annotations": []}))
        with self.assertRaises(ValueError):
            self.ov.load()
        self.assertEqual(len(self.ov.list()), 1)

    async def test_invalid_items_skipped(self):
        items = [ANNS[3], {"kind": "label"}, {"kind": "inlet", "position": [0, 0, 0], "direction": [0, 0, 0]},
                 {"id": "dup", **ANNS[1]}, {"id": "dup", **ANNS[1]}]
        path = self._write("x.json", json.dumps({"version": 1, "annotations": items}))
        anns, warnings = read_sidecar(path)
        self.assertEqual([a["kind"] for a in anns], ["label", "outlet"])
        self.assertEqual(len(warnings), 3)
        self.assertTrue(any("duplicate" in w for w in warnings))

    async def test_autoload_on_stage_open(self):
        ov = get_overlay()
        settings = carb.settings.get_settings()
        self.assertTrue(settings.get("/exts/morph.annotation.overlay/autoLoad"))
        scene = self._write("pipe_manifold.usda", USDA)
        self._write("pipe_manifold.annotations.json",
                    json.dumps({"version": 1, "annotations": ANNS}, ensure_ascii=False))
        ctx = omni.usd.get_context()
        await ctx.open_stage_async(scene)
        await _updates()
        self.assertTrue(_same_path(ov.current_file, os.path.join(self.dir, "pipe_manifold.annotations.json")))
        self.assertEqual([a["id"] for a in ov.list()], ["in1", "outlet_1", "plane_1", "label_1"])

        # new (anonymous) stage: empty list, no file
        await ctx.new_stage_async()
        await _updates()
        self.assertIsNone(ov.current_file)
        self.assertEqual(ov.list(), [])

    async def test_autoload_bad_sidecar_gives_empty(self):
        ov = get_overlay()
        ov.add({"kind": "label", "position": [0, 0, 0]})
        scene = self._write("bad.usda", USDA)
        self._write("bad.annotations.json", json.dumps({"version": 99, "annotations": ANNS}))
        await omni.usd.get_context().open_stage_async(scene)
        await _updates()
        self.assertEqual(ov.list(), [])
