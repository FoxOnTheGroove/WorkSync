"""morph.annotation.overlay public API. See CONTRACT.md."""

__all__ = [
    "get_overlay",
    "AnnotationOverlay",
    "KINDS",
    "EVENT_CHANGED",
    "EVENT_SELECTION_CHANGED",
    "EVENT_FACE_SELECTED",
    "FACE_KINDS",
    "MSG_STATE",
]

from .face import FACE_KINDS
from .model import KINDS
from .overlay import (
    EVENT_CHANGED,
    EVENT_FACE_SELECTED,
    EVENT_SELECTION_CHANGED,
    MSG_STATE,
    AnnotationOverlay,
    get_overlay,
)

# Kit finds the IExt subclass by scanning this module; not part of the public API.
from .extension import AnnotationOverlayExtension  # noqa: F401,E402
