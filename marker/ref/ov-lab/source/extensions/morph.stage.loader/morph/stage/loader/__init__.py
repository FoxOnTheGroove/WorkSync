"""morph.stage.loader 공개 API. 사용법은 CONTRACT.md."""

from .extension import MorphStageLoaderExtension
from .loader import EVENT_OPEN_FAILED, EVENT_OPENED, StageLoader, StageLoadError, get_loader
from .scanner import SceneInfo
from .web import MSG_STATE

__all__ = [
    "get_loader",
    "StageLoader",
    "SceneInfo",
    "StageLoadError",
    "EVENT_OPENED",
    "EVENT_OPEN_FAILED",
    "MSG_STATE",
    "MorphStageLoaderExtension",
]
