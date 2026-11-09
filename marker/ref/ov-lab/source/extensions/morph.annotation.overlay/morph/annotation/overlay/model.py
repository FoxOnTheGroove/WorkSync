"""Annotation model: validation, defaults, extra-field preservation.

Pure Python (no Kit imports) so it can be unit-tested anywhere.
"""

__all__ = [
    "KINDS",
    "DEFAULT_COLORS",
    "ID_RE",
    "MAX_LABEL",
    "MAX_DESCRIPTION",
    "AnnotationLimitError",
    "normalize",
    "next_id",
    "is_valid_id",
]

import copy
import json
import math
import re
from typing import Iterable

KINDS = ("label", "inlet", "outlet", "plane")

DEFAULT_COLORS = {
    "label": [0.95, 0.95, 0.95],
    "inlet": [0.2, 0.55, 1.0],
    "outlet": [1.0, 0.45, 0.1],
    "plane": [1.0, 0.85, 0.2],
}

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_LABEL = 128
MAX_DESCRIPTION = 1000

DEFAULT_ARROW_SIZE = 1.0
DEFAULT_PLANE_SIZE = [2.0, 2.0]

_COMMON = ("id", "kind", "label", "description", "color", "visible")
# Fields owned by each kind, in output order.
_KIND_FIELDS = {
    "label": ("position",),
    "inlet": ("position", "direction", "size"),
    "outlet": ("position", "direction", "size"),
    "plane": ("origin", "normal", "size", "up"),
}


class AnnotationLimitError(ValueError):
    """maxAnnotations exceeded (internal: lets the web layer answer resource_exhausted)."""


def is_valid_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(name: str, v) -> float:
    if not _is_number(v):
        raise ValueError(f"'{name}' must be a number, got {type(v).__name__}")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"'{name}' must be finite, got {v}")
    return f


def _vec(name: str, v, n: int = 3) -> list:
    if isinstance(v, (str, bytes, dict)) or not hasattr(v, "__len__") or not hasattr(v, "__iter__"):
        raise ValueError(f"'{name}' must be a list of {n} numbers")
    items = list(v)
    if len(items) != n:
        raise ValueError(f"'{name}' must have {n} components, got {len(items)}")
    return [_finite(f"{name}[{i}]", x) for i, x in enumerate(items)]


def _nonzero_vec(name: str, v) -> list:
    out = _vec(name, v)
    if math.sqrt(sum(x * x for x in out)) < 1e-12:
        raise ValueError(f"'{name}' must not be a zero vector")
    return out


def _positive(name: str, v) -> float:
    f = _finite(name, v)
    if f <= 0.0:
        raise ValueError(f"'{name}' must be > 0, got {v}")
    return f


def _string(name: str, v, max_len: int) -> str:
    if not isinstance(v, str):
        raise ValueError(f"'{name}' must be a string, got {type(v).__name__}")
    if len(v) > max_len:
        raise ValueError(f"'{name}' is too long ({len(v)} > {max_len})")
    return v


def normalize(ann, *, require_id: bool = False) -> dict:
    """Validate one annotation and return a new dict with defaults filled.

    - ``id`` may be missing (the caller generates one) unless ``require_id``.
    - Unknown fields are kept (deep copy) after the known ones.
    - Raises ``ValueError`` with the field name and reason.
    """
    if not isinstance(ann, dict):
        raise ValueError(f"annotation must be an object, got {type(ann).__name__}")
    kind = ann.get("kind")
    if kind is None:
        raise ValueError("'kind' is required")
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r} (expected one of {', '.join(KINDS)})")

    out = {}
    if "id" in ann and ann["id"] is not None:
        if not is_valid_id(ann["id"]):
            raise ValueError(f"'id' must match [A-Za-z0-9_-]{{1,64}}, got {ann['id']!r}")
        out["id"] = ann["id"]
    elif require_id:
        raise ValueError("'id' is required")
    out["kind"] = kind
    out["label"] = _string("label", ann.get("label", ""), MAX_LABEL)
    out["description"] = _string("description", ann.get("description", ""), MAX_DESCRIPTION)

    color = ann.get("color")
    if color is None:
        out["color"] = list(DEFAULT_COLORS[kind])
    else:
        c = _vec("color", color)
        for i, x in enumerate(c):
            if x < 0.0 or x > 1.0:
                raise ValueError(f"'color[{i}]' must be in 0..1, got {x}")
        out["color"] = c

    visible = ann.get("visible", True)
    if not isinstance(visible, bool):
        raise ValueError(f"'visible' must be a boolean, got {type(visible).__name__}")
    out["visible"] = visible

    def required(name):
        if name not in ann or ann[name] is None:
            raise ValueError(f"'{name}' is required for kind '{kind}'")
        return ann[name]

    if kind == "label":
        out["position"] = _vec("position", required("position"))
    elif kind in ("inlet", "outlet"):
        out["position"] = _vec("position", required("position"))
        out["direction"] = _nonzero_vec("direction", required("direction"))
        size = ann.get("size")
        out["size"] = DEFAULT_ARROW_SIZE if size is None else _positive("size", size)
    else:  # plane
        out["origin"] = _vec("origin", required("origin"))
        out["normal"] = _nonzero_vec("normal", required("normal"))
        size = ann.get("size")
        if size is None:
            out["size"] = list(DEFAULT_PLANE_SIZE)
        else:
            wh = _vec("size", size, 2)
            out["size"] = [_positive("size[0]", wh[0]), _positive("size[1]", wh[1])]
        up = ann.get("up")
        if up is not None:
            out["up"] = _nonzero_vec("up", up)

    known = set(_COMMON) | set(_KIND_FIELDS[kind])
    for key, value in ann.items():
        if key in known:
            continue
        if not isinstance(key, str):
            raise ValueError(f"field names must be strings, got {key!r}")
        out[key] = copy.deepcopy(value)

    try:
        json.dumps(out, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"annotation is not JSON-serializable: {e}") from None
    return out


def next_id(kind: str, taken: Iterable[str]) -> str:
    """Smallest unused ``<kind>_<n>`` (n >= 1)."""
    taken = set(taken)
    n = 1
    while f"{kind}_{n}" in taken:
        n += 1
    return f"{kind}_{n}"
