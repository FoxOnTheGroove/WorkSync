"""스키마 부분집합 검증기 테스트."""
import omni.kit.test

from ..schema import check_schema, validate


class TestSchema(omni.kit.test.AsyncTestCase):
    async def test_types(self):
        self.assertIsNone(validate(1, {"type": "number"}))
        self.assertIsNone(validate(1.5, {"type": "number"}))
        self.assertIsNotNone(validate(True, {"type": "number"}))
        self.assertIsNone(validate(2, {"type": "integer"}))
        self.assertIsNone(validate(2.0, {"type": "integer"}))
        self.assertIsNotNone(validate(2.5, {"type": "integer"}))
        self.assertIsNone(validate("a", {"type": "string"}))
        self.assertIsNone(validate(None, {"type": ["string", "null"]}))
        self.assertIsNone(validate(False, {"type": "boolean"}))
        self.assertIsNone(validate([1], {"type": "array"}))
        self.assertIsNotNone(validate({}, {"type": "array"}))

    async def test_required_properties_items_enum(self):
        schema = {
            "type": "object",
            "required": ["mode", "points"],
            "properties": {
                "mode": {"type": "string", "enum": ["orbit", "pan"]},
                "points": {"type": "array", "items": {"type": "number"}},
            },
        }
        self.assertIsNone(validate({"mode": "orbit", "points": [1, 2]}, schema))
        self.assertIn("missing required 'points'", validate({"mode": "orbit"}, schema))
        self.assertIn("payload.mode", validate({"mode": "zoom", "points": []}, schema))
        self.assertIn("payload.points[1]", validate({"mode": "pan", "points": [1, "x"]}, schema))

    async def test_additional_properties(self):
        schema = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
        self.assertIsNone(validate({"a": 1}, schema))
        self.assertIn("unexpected property 'b'", validate({"a": 1, "b": 2}, schema))

    async def test_object_keywords_ignored_for_non_object(self):
        # required/properties/additionalProperties는 object 값에만 적용 (CONTRACT 1.0.1)
        schema = {"required": ["x"], "properties": {"x": {"type": "string"}}, "additionalProperties": False}
        for value in ([], [1, 2], "s", 3, 1.5, True, None):
            self.assertIsNone(validate(value, schema), repr(value))
        self.assertIn("missing required 'x'", validate({}, schema))
        self.assertIn("payload.x", validate({"x": 1}, schema))
        self.assertIn("unexpected property 'y'", validate({"x": "a", "y": 1}, schema))
        # 중첩: type 없는 하위 스키마는 object가 아닌 값을 통과, type: object를 함께 두면 거부
        loose = {"type": "object", "properties": {"p": {"required": ["x"]}}}
        self.assertIsNone(validate({"p": [1]}, loose))
        strict = {"type": "object", "properties": {"p": {"type": "object", "required": ["x"]}}}
        self.assertIn("payload.p: expected object", validate({"p": [1]}, strict))

    async def test_items_ignored_for_non_array(self):
        schema = {"items": {"type": "number"}}
        for value in ({"a": "x"}, "abc", 1, None):
            self.assertIsNone(validate(value, schema), repr(value))
        self.assertIn("payload[0]", validate(["x"], schema))

    async def test_check_schema(self):
        check_schema({"type": "object", "properties": {"a": {"type": "string"}}})
        for bad in ([], {"type": "float"}, {"required": "a"}, {"properties": []}, {"additionalProperties": {}}):
            with self.assertRaises(ValueError):
                check_schema(bad)
