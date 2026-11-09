"""Sidecar JSON file (``<scene>.annotations.json``): path, read, atomic write.

Pure Python (no Kit imports).
"""

__all__ = ["FORMAT_VERSION", "DEFAULT_SUFFIX", "sidecar_path", "read_sidecar", "write_sidecar"]

import json
import os
import shutil
import tempfile
from typing import List, Optional, Tuple

from .model import is_valid_id, next_id, normalize

FORMAT_VERSION = 1
DEFAULT_SUFFIX = ".annotations.json"


def sidecar_path(scene_file: Optional[str], suffix: str = DEFAULT_SUFFIX) -> Optional[str]:
    """``C:/x/pipe.usda`` -> ``C:/x/pipe.annotations.json``. None if no scene file."""
    if not scene_file:
        return None
    if not isinstance(suffix, str) or not suffix or "/" in suffix or "\\" in suffix:
        suffix = DEFAULT_SUFFIX
    folder, name = os.path.split(scene_file)
    stem = os.path.splitext(name)[0]
    return os.path.join(folder, stem + suffix)


def read_sidecar(path: str, max_count: Optional[int] = None) -> Tuple[Optional[List[dict]], List[str]]:
    """Read a sidecar file.

    Returns ``(annotations, warnings)``. ``annotations`` is ``None`` when the file does not exist.
    Invalid items are skipped with a warning (reason included).
    Raises ``ValueError`` for broken JSON / wrong top-level shape / version > FORMAT_VERSION.
    Raises ``OSError`` for read failures other than "not found".
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return None, []
    name = os.path.basename(path)  # messages carry the file name only (they reach the web)
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError(f"broken JSON in {name}: {e}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{name}: top level must be an object")
    version = data.get("version", FORMAT_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"{name}: 'version' must be a positive integer, got {version!r}")
    if version > FORMAT_VERSION:
        raise ValueError(f"{name}: version {version} is newer than supported {FORMAT_VERSION}")
    items = data.get("annotations", [])
    if not isinstance(items, list):
        raise ValueError(f"{name}: 'annotations' must be a list")

    warnings: List[str] = []
    out: List[dict] = []
    ids = set()
    # Reserve explicit ids first so generated ids never steal a later explicit one.
    explicit = {it.get("id") for it in items if isinstance(it, dict) and is_valid_id(it.get("id"))}
    for i, item in enumerate(items):
        try:
            ann = normalize(item)
        except ValueError as e:
            warnings.append(f"annotations[{i}] skipped: {e}")
            continue
        if "id" in ann:
            if ann["id"] in ids:
                warnings.append(f"annotations[{i}] skipped: duplicate id {ann['id']!r}")
                continue
        else:
            ann = {"id": next_id(ann["kind"], ids | explicit), **ann}
        if max_count is not None and len(out) >= max_count:
            warnings.append(f"annotations[{i}:] skipped: maxAnnotations {max_count} reached")
            break
        ids.add(ann["id"])
        out.append(ann)
    return out, warnings


NEW_FILE_MODE = 0o644  # mkstemp creates 0600; a new sidecar gets ordinary file permissions


def _copy_mode(src: str, dst: str) -> None:
    """Give the temp file the permissions of the file it replaces (best effort, POSIX bits)."""
    try:
        if os.path.exists(src):
            shutil.copymode(src, dst)
        else:
            os.chmod(dst, NEW_FILE_MODE)
    except OSError:
        pass  # permissions are best effort; the write itself decides success


def write_sidecar(path: str, annotations: List[dict]) -> str:
    """Write atomically (temp file in the same folder, then replace). Returns the absolute path.

    The existing file's permissions are copied to the temp file before the replace.
    On failure the existing file is left untouched and the temp file is removed.
    """
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    data = {"version": FORMAT_VERSION, "annotations": annotations}
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".annotations-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _copy_mode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
