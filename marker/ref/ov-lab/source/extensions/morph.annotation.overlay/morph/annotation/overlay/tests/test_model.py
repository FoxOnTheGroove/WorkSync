__all__ = ["TestAnnotationModel"]

import math

import omni.kit.test

from ..model import DEFAULT_COLORS, KINDS, next_id, normalize

LABEL = {"kind": "label", "position": [1, 2, 3]}
INLET = {"kind": "inlet", "position": [0, 0, 0], "direction": [2, 0, 0]}
OUTLET = {"kind": "outlet", "position": [0, 0, 0], "direction": [0, 0, 1]}
PLANE = {"kind": "plane", "origin": [0, 0, 1], "normal": [1, 0, 0]}


class TestAnnotationModel(omni.kit.test.AsyncTestCase):
    def assertInvalid(self, ann, needle=None):
        with self.assertRaises(ValueError) as cm:
            normalize(ann)
        if needle:
            self.assertIn(needle, str(cm.exception))

    async def test_kinds_constant(self):
        self.assertEqual(KINDS, ("label", "inlet", "outlet", "plane"))

    async def test_defaults(self):
        a = normalize(LABEL)
        self.assertEqual(a["label"], "")
        self.assertEqual(a["description"], "")
        self.assertEqual(a["color"], DEFAULT_COLORS["label"])
        self.assertTrue(a["visible"])
        self.assertEqual(a["position"], [1.0, 2.0, 3.0])
        self.assertNotIn("id", a)
        self.assertEqual(normalize(INLET)["size"], 1.0)
        self.assertEqual(normalize(OUTLET)["color"], DEFAULT_COLORS["outlet"])
        p = normalize(PLANE)
        self.assertEqual(p["size"], [2.0, 2.0])
        self.assertNotIn("up", p)  # default = stage up axis at draw time

    async def test_direction_kept_unnormalized(self):
        self.assertEqual(normalize(INLET)["direction"], [2.0, 0.0, 0.0])

    async def test_required_fields_per_kind(self):
        self.assertInvalid({"kind": "label"}, "position")
        self.assertInvalid({"kind": "inlet", "position": [0, 0, 0]}, "direction")
        self.assertInvalid({"kind": "outlet", "direction": [1, 0, 0]}, "position")
        self.assertInvalid({"kind": "plane", "origin": [0, 0, 0]}, "normal")
        self.assertInvalid({"kind": "plane", "normal": [0, 0, 1]}, "origin")
        self.assertInvalid({"position": [0, 0, 0]}, "kind")
        self.assertInvalid({"kind": "arrow", "position": [0, 0, 0]}, "unknown kind")
        self.assertInvalid("not a dict")

    async def test_numbers(self):
        self.assertInvalid({**LABEL, "position": [0, math.nan, 0]}, "finite")
        self.assertInvalid({**LABEL, "position": [0, math.inf, 0]}, "finite")
        self.assertInvalid({**LABEL, "position": [0, "1", 0]}, "number")
        self.assertInvalid({**LABEL, "position": [True, 0, 0]}, "number")
        self.assertInvalid({**LABEL, "position": [0, 0]}, "3 components")
        self.assertInvalid({**INLET, "size": 0}, "> 0")
        self.assertInvalid({**INLET, "size": -1}, "> 0")
        self.assertInvalid({**PLANE, "size": [1, 0]}, "> 0")
        self.assertInvalid({**PLANE, "size": 2.0})
        self.assertInvalid({**LABEL, "color": [1.5, 0, 0]}, "0..1")
        self.assertInvalid({**LABEL, "visible": 1}, "visible")

    async def test_zero_vectors(self):
        self.assertInvalid({**INLET, "direction": [0, 0, 0]}, "zero")
        self.assertInvalid({**PLANE, "normal": [0, 0, 0]}, "zero")
        self.assertInvalid({**PLANE, "up": [0, 0, 0]}, "zero")

    async def test_strings(self):
        self.assertInvalid({**LABEL, "label": 3}, "label")
        self.assertInvalid({**LABEL, "label": "x" * 129}, "too long")
        self.assertInvalid({**LABEL, "description": "x" * 1001}, "too long")
        normalize({**LABEL, "label": "x" * 128, "description": "y" * 1000})

    async def test_id_format(self):
        for good in ("a", "A-1_b", "x" * 64):
            self.assertEqual(normalize({**LABEL, "id": good})["id"], good)
        for bad in ("", "a b", "a.b", "x" * 65, 12, "한글"):
            self.assertInvalid({**LABEL, "id": bad}, "id")
        with self.assertRaises(ValueError):
            normalize(LABEL, require_id=True)

    async def test_extra_fields_preserved(self):
        src = {**LABEL, "tag": {"pipe": "P-1", "n": [1, 2]}, "zz": None}
        a = normalize(src)
        self.assertEqual(a["tag"], {"pipe": "P-1", "n": [1, 2]})
        self.assertIn("zz", a)
        src["tag"]["pipe"] = "changed"
        self.assertEqual(a["tag"]["pipe"], "P-1")  # deep copy
        # field order: known first, extras after
        self.assertEqual(list(a)[:2], ["kind", "label"])
        self.assertEqual(list(a)[-2:], ["tag", "zz"])

    async def test_extra_fields_must_be_json(self):
        self.assertInvalid({**LABEL, "x": math.nan}, "JSON")
        self.assertInvalid({**LABEL, "x": object()}, "JSON")

    async def test_next_id(self):
        self.assertEqual(next_id("label", []), "label_1")
        self.assertEqual(next_id("label", ["label_1", "label_3"]), "label_2")
