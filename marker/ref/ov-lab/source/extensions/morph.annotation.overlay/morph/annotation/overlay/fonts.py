"""Fonts / Hangul display.

Hangul is enabled by the **app** settings ``/app/font/customFontPath`` + ``/app/font/customRegionFiles``
(read once at app start by Kit's glyph manager). The editor window and the viewport labels follow
the app font.

- Do NOT put a ``"font"`` style on the editor by default: omni.ui bakes a styled font separately with
  the default (Latin) glyph ranges only, so Hangul turns into "??" even when the app font has it.
- ``font_setting()``: the ``fontPath`` setting only when the user set it explicitly and the file
  is a TrueType / OpenType font (``is_font_file``), else ``""`` (= app font, no style).
  Auto-detected fonts are never used as a style. A style font omni.ui cannot load crashes Kit
  (``setBytesData called with nullptr``), so invalid files are never put on a style.
- ``editor_style(path)``: omni.ui style dict putting ``path`` on the editor's text widgets
  (``{}`` for ``""``).
- ``warn_app_font_once()``: one warning when neither ``/app/font/customFontPath`` nor ``fontPath``
  is set (Hangul may show as "??").
- ``find_font()`` / ``CANDIDATES``: Korean font candidates on this machine, only used for the hint
  in that warning.
- ``KOREAN_REGION_FILE``: reference glyph list (Hangul syllables + jamo + CJK punctuation). The apps
  use their own copy ``source/apps/fonts/korean_region.txt``.
"""

__all__ = [
    "S_FONT_PATH",
    "S_APP_FONT_PATH",
    "S_APP_REGION_FILES",
    "CANDIDATES",
    "KOREAN_REGION_FILE",
    "find_font",
    "is_font_file",
    "font_setting",
    "editor_style",
    "app_font_warning",
    "warn_app_font_once",
]

import os
from typing import Any, Callable, Iterable, Optional

import carb
import carb.settings

_EXT_ID = "morph.annotation.overlay"
S_FONT_PATH = f"/exts/{_EXT_ID}/fontPath"
S_APP_FONT_PATH = "/app/font/customFontPath"
S_APP_REGION_FILES = "/app/font/customRegionFiles"

# Reference glyph list for /app/font/customRegionFiles (apps use source/apps/fonts/korean_region.txt).
KOREAN_REGION_FILE = "data/fonts/korean_region.txt"


def _windows_fonts_dir() -> str:
    return os.path.join(os.environ.get("WINDIR") or "C:/Windows", "Fonts")


def _candidates() -> tuple:
    win = _windows_fonts_dir()
    return (
        # Windows
        os.path.join(win, "malgun.ttf"),  # Malgun Gothic
        os.path.join(win, "NotoSansKR-VF.ttf"),
        os.path.join(win, "gulim.ttc"),
        os.path.join(win, "batang.ttc"),
        # Linux
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        # macOS
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    )


CANDIDATES = _candidates()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def find_font(
    configured: Optional[str] = "",
    candidates: Optional[Iterable[str]] = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str:
    """Font file found on this machine, or ``""``.

    ``configured`` non-empty: that path if it exists, else ``""`` (warning). Empty / not a string:
    the first existing path of ``candidates`` (default ``CANDIDATES``), else ``""``.
    Only used for hints: the result is not applied as a style unless it was configured.
    """
    path = _text(configured)
    if path:
        if exists(path):
            return path
        carb.log_warn(f"[{_EXT_ID}] fontPath {path!r} not found: the editor uses the app font")
        return ""
    for path in CANDIDATES if candidates is None else candidates:
        try:
            if path and exists(path):
                return path
        except Exception:  # noqa: BLE001
            continue
    return ""


# sfnt / TrueType collection signatures (TrueType, OpenType CFF, Apple 'true', .ttc, Type 1 in sfnt).
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"typ1")
_MIN_FONT_BYTES = 12  # sfnt offset table


def is_font_file(path: str) -> bool:
    """True when ``path`` is a readable file starting with a TrueType / OpenType / TTC signature.

    omni.ui crashes the process (``setBytesData called with nullptr``) when a style ``"font"``
    points at a file it cannot load, so only files passing this check are put on a style.
    """
    try:
        if os.path.getsize(path) < _MIN_FONT_BYTES:
            return False
        with open(path, "rb") as f:
            return f.read(4) in _FONT_MAGIC
    except (OSError, TypeError, ValueError):
        return False


def font_setting(
    exists: Callable[[str], bool] = os.path.isfile,
    valid: Callable[[str], bool] = is_font_file,
) -> str:
    """Editor style font: the ``fontPath`` setting if set explicitly and it is a font file, else ``""``.

    ``""`` (the default) = no ``"font"`` style, the editor follows the app font. No auto-detection.
    A missing file or a file that is not a TrueType / OpenType font logs a warning and gives ``""``.
    """
    configured = _text(carb.settings.get_settings().get(S_FONT_PATH))
    if not configured:
        return ""
    path = find_font(configured, candidates=(), exists=exists)
    if path and not valid(path):
        carb.log_warn(f"[{_EXT_ID}] fontPath {path!r} is not a TrueType/OpenType font: the editor uses the app font")
        return ""
    return path


def editor_style(path: str) -> dict:
    """omni.ui style putting ``path`` on the editor's text widgets (``{}`` when ``path`` is empty)."""
    if not path:
        return {}
    font = {"font": path}
    return {
        "font": path,
        "Label": font,
        "Button.Label": font,
        "Field": font,
        "StringField": font,
        "FloatField": font,
        "ComboBox": font,
        "Tooltip": font,
    }


def app_font_warning(app_font: Any, configured: Any, hint_font: str = "") -> Optional[str]:
    """Warning text when neither the app font (``/app/font/customFontPath``) nor ``fontPath`` is set.

    ``None`` when one of them is set. ``hint_font`` (e.g. a detected Malgun Gothic path) is shown
    in the example setting.
    """
    if _text(app_font) or _text(configured):
        return None
    example = (hint_font or "C:/Windows/Fonts/malgun.ttf").replace("\\", "/")
    return (
        f"[{_EXT_ID}] /app/font/customFontPath is empty: Korean text (한글) may show as '??' in the "
        "editor window and viewport labels. Set in the app .kit [settings]: "
        f'app.font.customFontPath = "{example}" and '
        'app.font.customRegionFiles = ["<korean_region.txt>"] '
        "(한글이 ??로 보일 수 있음, 앱 .kit에 font.customFontPath·customRegionFiles 설정)"
    )


_warned = False


def warn_app_font_once(
    warn: Callable[[str], None] = carb.log_warn, reset: bool = False
) -> Optional[str]:
    """Log ``app_font_warning`` once per process (``reset=True`` = forget an earlier warning, for tests).

    Returns the message when it was logged by this call, else ``None``.
    """
    global _warned
    if reset:
        _warned = False
    if _warned:
        return None
    settings = carb.settings.get_settings()
    msg = app_font_warning(settings.get(S_APP_FONT_PATH), settings.get(S_FONT_PATH), find_font(""))
    if msg is None:
        return None
    _warned = True
    warn(msg)
    return msg
