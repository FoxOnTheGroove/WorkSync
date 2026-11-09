"""JSON Schema 부분집합 검증기 (내부 구현).

지원 키워드: type, required, properties, items, enum, additionalProperties(bool).
그 외 키워드는 무시한다.
"""
from __future__ import annotations

from typing import Any, Optional

_KNOWN_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and value.is_integer()
    return False


def check_schema(schema: Any, path: str = "schema") -> None:
    """등록 시점에 스키마 형식을 검사한다. 잘못되면 ValueError."""
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: schema must be a dict")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, (list, tuple)) else [t]
        for name in types:
            if name not in _KNOWN_TYPES:
                raise ValueError(f"{path}.type: unknown type {name!r}")
    req = schema.get("required")
    if req is not None and (not isinstance(req, (list, tuple)) or not all(isinstance(r, str) for r in req)):
        raise ValueError(f"{path}.required: must be a list of str")
    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            raise ValueError(f"{path}.properties: must be a dict")
        for key, sub in props.items():
            check_schema(sub, f"{path}.properties.{key}")
    items = schema.get("items")
    if items is not None:
        check_schema(items, f"{path}.items")
    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, (list, tuple)):
        raise ValueError(f"{path}.enum: must be a list")
    addl = schema.get("additionalProperties")
    if addl is not None and not isinstance(addl, bool):
        raise ValueError(f"{path}.additionalProperties: only bool is supported")


def validate(value: Any, schema: Optional[dict], path: str = "payload") -> Optional[str]:
    """value가 schema를 만족하면 None, 아니면 실패 사유 문자열."""
    if not schema:
        return None
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, (list, tuple)) else [t]
        if not any(_matches_type(value, name) for name in types):
            return f"{path}: expected {'|'.join(types)}, got {_type_name(value)}"
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        return f"{path}: value not in enum"
    if isinstance(value, dict):
        for key in schema.get("required") or ():
            if key not in value:
                return f"{path}: missing required '{key}'"
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in value:
                reason = validate(value[key], sub, f"{path}.{key}")
                if reason:
                    return reason
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    return f"{path}: unexpected property '{key}'"
    if isinstance(value, (list, tuple)) and schema.get("items"):
        for i, item in enumerate(value):
            reason = validate(item, schema["items"], f"{path}[{i}]")
            if reason:
                return reason
    return None
