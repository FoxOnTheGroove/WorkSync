"""장면 폴더 스캔 (내부 모듈). 순수 파일시스템 코드라 Kit 없이도 동작한다."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable

SAMPLES_ROOT = "samples"
DEFAULT_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")

_NAME_BAD = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class SceneInfo:
    """장면 한 개. `path`는 절대 경로 (웹에는 보내지 않는다)."""

    id: str
    name: str
    root: str
    path: str
    size_bytes: int

    def to_dict(self) -> dict:
        """웹용 dict (`path` 제외)."""
        return {"id": self.id, "name": self.name, "root": self.root, "size_bytes": self.size_bytes}


def sanitize_name(name: str) -> str:
    """루트 이름을 `[A-Za-z0-9_-]+`로 정리한다. 남는 글자가 없으면 `root`."""
    cleaned = _NAME_BAD.sub("_", name or "").strip("_")
    return cleaned or "root"


def _split_entry(entry: str) -> tuple[str | None, str]:
    """`"name=path"` 또는 `"path"`. `=` 앞이 경로처럼 보이면(구분자·드라이브 콜론) 전체를 경로로 본다."""
    if "=" in entry:
        head, tail = entry.split("=", 1)
        head = head.strip()
        if head and not any(c in head for c in "/\\:"):
            return head, tail.strip()
    return None, entry.strip()


def parse_roots(
    entries: Iterable[str] | None,
    samples_dir: str | None = None,
    warn: Callable[[str], None] | None = None,
) -> list[tuple[str, str]]:
    """설정 `roots` 항목과 샘플 폴더를 `(이름, 절대 경로)` 목록으로 만든다 (설정 순서, samples 맨 뒤).

    이름이 겹치면 뒤 것에 `_2`, `_3`…을 붙인다. 형식이 잘못된 항목(상대 경로·빈 값)은 warn 후 건너뛴다.
    폴더 존재 여부는 여기서 보지 않는다 (scan에서 확인).
    """
    raw: list[tuple[str, str]] = []
    for entry in entries or []:
        if not isinstance(entry, str) or not entry.strip():
            if warn:
                warn(f"roots: 빈 항목/문자열 아님 건너뜀: {entry!r}")
            continue
        name, path = _split_entry(entry)
        if not path or not os.path.isabs(path):
            if warn:
                warn(f"roots: 절대 경로가 아님, 건너뜀: {entry!r}")
            continue
        path = os.path.normpath(path)
        if name is None:
            name = os.path.basename(path.rstrip("\\/")) or path
        raw.append((sanitize_name(name), path))
    if samples_dir:
        raw.append((SAMPLES_ROOT, os.path.normpath(samples_dir)))

    used: set[str] = set()
    result: list[tuple[str, str]] = []
    for name, path in raw:
        unique = name
        n = 2
        while unique.lower() in used:
            unique = f"{name}_{n}"
            n += 1
        used.add(unique.lower())
        result.append((unique, path))
    return result


def normalize_extensions(exts: Iterable[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    for e in exts if exts is not None else DEFAULT_EXTENSIONS:
        if not isinstance(e, str) or not e.strip():
            continue
        e = e.strip().lower()
        if not e.startswith("."):
            e = "." + e
        if e not in out:
            out.append(e)
    return tuple(out)


def _walk(root: str, max_depth: int, exts: tuple[str, ...]) -> list[tuple[str, str, int]]:
    """루트 아래 `(상대 경로 /구분, 절대 경로, 크기)` 목록. 숨김 폴더 건너뜀, 링크 순환 방지."""
    found: list[tuple[str, str, int]] = []
    visited: set[str] = set()

    def visit(folder: str, rel: str, depth: int) -> None:
        try:
            real = os.path.normcase(os.path.realpath(folder))
        except OSError:
            real = os.path.normcase(folder)
        if real in visited:
            return
        visited.add(real)
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            if depth == 0:
                raise
            return  # 하위 폴더 읽기 실패는 조용히 건너뛴다
        for de in entries:
            try:
                if de.is_dir():
                    if de.name.startswith(".") or depth >= max_depth:
                        continue
                    visit(de.path, f"{rel}{de.name}/", depth + 1)
                elif de.is_file():
                    if de.name.lower().endswith(exts):
                        found.append((f"{rel}{de.name}", os.path.normpath(os.path.abspath(de.path)), de.stat().st_size))
            except OSError:
                continue

    visit(root, "", 0)
    return found


def scan(
    roots: list[tuple[str, str]],
    exts: tuple[str, ...],
    max_depth: int,
    max_scenes: int,
    warn: Callable[[str], None] | None = None,
) -> list[SceneInfo]:
    """CONTRACT "장면 목록 모델" 순서대로 장면 목록을 만든다."""
    max_depth = max(0, int(max_depth))
    max_scenes = max(0, int(max_scenes))
    scenes: list[SceneInfo] = []
    truncated = False
    for name, root in roots:
        if not os.path.isdir(root):
            if warn:
                warn(f"장면 루트 폴더 없음, 건너뜀: {name}={root}")
            continue
        try:
            files = _walk(root, max_depth, exts)
        except OSError as e:
            if warn:
                warn(f"장면 루트 폴더 읽기 실패, 건너뜀: {name}={root} ({e})")
            continue
        files.sort(key=lambda f: (f[0].casefold(), f[0]))
        for rel, path, size in files:
            if len(scenes) >= max_scenes:
                truncated = True
                break
            scenes.append(SceneInfo(id=f"{name}/{rel}", name=rel.rsplit("/", 1)[-1], root=name, path=path, size_bytes=int(size)))
        if truncated:
            break
    if truncated and warn:
        warn(f"장면 수가 maxScenes({max_scenes})를 넘어 나머지는 목록에서 뺐습니다")
    return scenes


def norm_path(url: str | None) -> str | None:
    """stage URL/경로를 비교·표시용으로 정리. 로컬 경로는 절대·정규화, URL은 그대로. 빈 값은 None."""
    if not url:
        return None
    s = url
    if s.lower().startswith("file:"):
        s = s[5:]
        if s.startswith("//"):
            s = s[2:]  # file:///C:/x -> /C:/x
        if len(s) > 2 and s[0] == "/" and s[2] == ":":
            s = s[1:]  # /C:/x -> C:/x
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]+://", s):
        return s  # omniverse://, http:// 등
    if os.path.isabs(s):
        return os.path.normpath(s)
    return s


def path_key(path: str | None) -> str | None:
    """같은 파일인지 비교하는 키 (Windows 대소문자 무시)."""
    p = norm_path(path)
    if p is None:
        return None
    return os.path.normcase(p) if os.path.isabs(p) else p


def display_name(path: str | None) -> str | None:
    if not path:
        return None
    tail = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return tail or None
