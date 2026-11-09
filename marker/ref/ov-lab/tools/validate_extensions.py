#!/usr/bin/env python3
"""MORPH 익스텐션 구조/계약 정합성 검사.

사용:
  python tools/validate_extensions.py                      # 전체
  python tools/validate_extensions.py --ext morph.camera.control
  python tools/validate_extensions.py --changed-by morph.camera.control --base main

종료 코드: ERROR가 있으면 1, 아니면 0.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "source" / "extensions"
AGENT_DIR = ROOT / ".claude" / "agents"
REGISTRY = ROOT / "docs" / "EXTENSIONS.md"

EXT_ID_RE = re.compile(r"^morph\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
NAME_RE = re.compile(r"^morph\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_.]*$")
BACKTICK_RE = re.compile(r"`([^`]+)`")
REQUIRED_SECTIONS = ["목적", "언제 쓰나", "공개 Python API", "이벤트", "사용 예", "변경 이력"]
REVIEWER_AGENTS = {"ext-reviewer"}
SHARED_PATHS = ("repo.toml", "premake5.lua", "source/apps/", "docs/EXTENSIONS.md", ".claude/", "tools/", "CLAUDE.md")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"[{where}] {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warns.append(f"[{where}] {msg}")


# ---------- 파서 ----------

def parse_registry(rep: Report) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not REGISTRY.exists():
        rep.err("registry", f"{REGISTRY.relative_to(ROOT)} 없음")
        return rows
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("morph."):
            continue
        deps = [] if cells[3] in ("-", "") else [d.strip() for d in cells[3].split(",") if d.strip()]
        rows[cells[0]] = {"agent": cells[1], "status": cells[2], "deps": deps}
    return rows


def split_sections(text: str) -> dict[str, str]:
    """'## 제목' / '### 제목' 단위로 본문을 나눈다. 키는 제목 원문."""
    sections: dict[str, str] = {}
    current = "__head__"
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(#{2,3})\s+(.*)$", line)
        if m:
            sections[current] = "\n".join(buf)
            current, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf)
    return sections


def find_section(sections: dict[str, str], prefix: str) -> str | None:
    for title, body in sections.items():
        if title.startswith(prefix):
            return body
    return None


def table_rows(body: str | None) -> list[list[str]]:
    if not body:
        return []
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|") or set(s.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        out.append(cells)
    return out[1:] if out else []  # 헤더 제외


def first_name(cell: str) -> str | None:
    m = BACKTICK_RE.search(cell)
    return m.group(1) if m else None


@dataclass
class Contract:
    ext_id: str
    version: str | None = None
    status: str | None = None
    agent: str | None = None
    deps: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    received: list[tuple[str, str]] = field(default_factory=list)  # (event, owner)
    web_types: list[str] = field(default_factory=list)


def parse_contract(ext_id: str, path: Path, rep: Report) -> Contract | None:
    if not path.exists():
        rep.err(ext_id, "CONTRACT.md 없음")
        return None
    text = path.read_text(encoding="utf-8")
    c = Contract(ext_id)
    sections = split_sections(text)

    def header(key: str) -> str | None:
        m = re.search(rf"^-\s*{key}\s*:\s*(.+)$", text, re.M)
        return m.group(1).strip() if m else None

    c.version, c.status, c.agent = header("버전"), header("상태"), header("담당 에이전트")
    dep_raw = header("의존") or ""
    c.deps = [d.strip() for d in dep_raw.split(",") if d.strip().startswith("morph.")]
    for key, val in (("버전", c.version), ("상태", c.status), ("담당 에이전트", c.agent)):
        if not val:
            rep.err(ext_id, f"CONTRACT 헤더에 '- {key}:' 없음")
    if c.status and c.status not in ("draft", "stable", "deprecated"):
        rep.warn(ext_id, f"알 수 없는 상태 '{c.status}' (draft|stable|deprecated)")

    for sec in REQUIRED_SECTIONS:
        if find_section(sections, sec) is None:
            rep.err(ext_id, f"CONTRACT 필수 섹션 '## {sec}' 없음")

    usage = find_section(sections, "사용 예") or ""
    if "```" not in usage:
        rep.err(ext_id, "'사용 예'에 코드 블록 없음")

    for row in table_rows(find_section(sections, "발행")):
        n = first_name(row[0])
        if n:
            c.published.append(n)
    for row in table_rows(find_section(sections, "수신")):
        n = first_name(row[0])
        if n:
            c.received.append((n, row[1].strip() if len(row) > 1 else ""))
    for row in table_rows(find_section(sections, "웹 메시지")):
        if len(row) > 1:
            n = first_name(row[1])
            if n:
                c.web_types.append(n)
    return c


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, m.group(2)


def read_toml(path: Path, rep: Report, ext_id: str) -> dict | None:
    if tomllib is None:
        rep.warn(ext_id, "tomllib/tomli 없음 — extension.toml 검사 생략 (Python 3.11+ 권장)")
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        rep.err(ext_id, f"extension.toml 파싱 실패: {e}")
        return None


# ---------- 검사 ----------

def domain_of(ext_id: str) -> str:
    return ext_id.split(".")[1] if ext_id.count(".") >= 2 else ""


def check_extension(ext_id: str, registry: dict, rep: Report) -> Contract | None:
    d = EXT_DIR / ext_id
    if not EXT_ID_RE.match(ext_id):
        rep.err(ext_id, "익스텐션 ID 형식 위반 (morph.<domain>.<name>)")

    c = parse_contract(ext_id, d / "CONTRACT.md", rep)

    # extension.toml
    toml_path = d / "config" / "extension.toml"
    toml_deps: list[str] | None = None
    if not toml_path.exists():
        rep.warn(ext_id, "config/extension.toml 없음 — 골격 생성 전 (repo template new 필요)")
    else:
        data = read_toml(toml_path, rep, ext_id)
        if data is not None:
            ver = (data.get("package") or {}).get("version")
            if c and c.version and ver and ver != c.version:
                rep.err(ext_id, f"버전 불일치: CONTRACT {c.version} ≠ extension.toml {ver}")
            toml_deps = sorted(k for k in (data.get("dependencies") or {}) if k.startswith("morph."))

    # registry
    reg = registry.get(ext_id)
    if reg is None:
        rep.err(ext_id, "docs/EXTENSIONS.md 레지스트리에 없음")
    if c and reg:
        if c.agent and reg["agent"] != c.agent:
            rep.err(ext_id, f"담당 에이전트 불일치: CONTRACT {c.agent} ≠ 레지스트리 {reg['agent']}")
        if c.status and reg["status"] != c.status:
            rep.warn(ext_id, f"상태 불일치: CONTRACT {c.status} ≠ 레지스트리 {reg['status']}")
        if sorted(c.deps) != sorted(reg["deps"]):
            rep.err(ext_id, f"의존 불일치: CONTRACT {sorted(c.deps)} ≠ 레지스트리 {sorted(reg['deps'])}")
    if c and toml_deps is not None and sorted(c.deps) != toml_deps:
        rep.err(ext_id, f"의존 불일치: CONTRACT {sorted(c.deps)} ≠ extension.toml {toml_deps}")

    # 담당 에이전트
    if c and c.agent:
        ap = AGENT_DIR / f"{c.agent}.md"
        if not ap.exists():
            rep.err(ext_id, f"담당 에이전트 파일 없음: .claude/agents/{c.agent}.md")
        else:
            fm, body = parse_frontmatter(ap)
            if fm.get("name") != c.agent:
                rep.err(c.agent, f"frontmatter name '{fm.get('name')}' ≠ 파일명")
            desc = fm.get("description", "")
            if len(desc) < 40:
                rep.warn(c.agent, "description이 짧음 — 위임 트리거 키워드를 구체적으로")
            if ext_id not in desc:
                rep.warn(c.agent, "description에 익스텐션 ID가 없음")
            if f"source/extensions/{ext_id}" not in body:
                rep.err(c.agent, f"본문에 소유 범위 source/extensions/{ext_id} 명시 없음")

    # 이름 규칙: 이벤트는 자기 domain 접두어, 웹 메시지는 자기 ext-id 접두어 (stream 1.0)
    if c:
        prefix = f"morph.{domain_of(ext_id)}."
        for n in c.published:
            if not NAME_RE.match(n):
                rep.err(ext_id, f"이름 형식 위반: {n}")
            elif not n.startswith(prefix):
                rep.err(ext_id, f"'{n}'은 자기 domain 접두어 '{prefix}'로 시작해야 함")
        for n in c.web_types:
            if not NAME_RE.match(n):
                rep.err(ext_id, f"이름 형식 위반: {n}")
            elif not n.startswith(f"{ext_id}."):
                rep.err(ext_id, f"웹 메시지 '{n}'은 자기 ext-id 접두어 '{ext_id}.'로 시작해야 함")
    return c


def check_cross(contracts: dict[str, Contract], registry: dict, rep: Report) -> None:
    owners: dict[str, str] = {}
    for c in contracts.values():
        for e in c.published:
            if e in owners and owners[e] != c.ext_id:
                rep.err(c.ext_id, f"이벤트 '{e}'를 {owners[e]}도 발행 — 소유자는 하나여야 함")
            owners.setdefault(e, c.ext_id)

    web_owner: dict[str, str] = {}
    for c in contracts.values():
        for t in c.web_types:
            if t in web_owner and web_owner[t] != c.ext_id:
                rep.err(c.ext_id, f"웹 메시지 타입 '{t}'를 {web_owner[t]}도 선언")
            web_owner.setdefault(t, c.ext_id)

    for c in contracts.values():
        for e, owner in c.received:
            real = owners.get(e)
            if real is None:
                rep.warn(c.ext_id, f"수신 이벤트 '{e}'를 발행하는 익스텐션이 없음")
            elif owner and owner != real:
                rep.err(c.ext_id, f"수신 이벤트 '{e}' 소유자 표기 {owner} ≠ 실제 {real}")
            if real and real != c.ext_id and real not in c.deps:
                rep.warn(c.ext_id, f"'{e}'를 수신하지만 {real}이 의존에 없음 (이벤트만 쓰면 무시 가능)")
        for dep in c.deps:
            if dep not in contracts and dep not in registry:
                rep.err(c.ext_id, f"의존 '{dep}'이 존재하지 않음")

    # 순환 의존
    graph = {k: v["deps"] for k, v in registry.items()}
    state: dict[str, int] = {}

    def dfs(n: str, path: list[str]) -> None:
        state[n] = 1
        for m in graph.get(n, []):
            if state.get(m) == 1:
                rep.err("registry", "순환 의존: " + " → ".join(path + [n, m]))
            elif state.get(m) is None:
                dfs(m, path + [n])
        state[n] = 2

    for n in graph:
        if n not in state:
            dfs(n, [])


VERSION_REQ_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
APP_DIR = ROOT / "source" / "apps"


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("+")[0].split("-")[0])[:3])


def check_versions(rep: Report) -> None:
    """morph.* 의존마다 버전 범위를 요구한다.

    Kit은 version = "1.2"를 ^1.2 (1.2 이상, 2.0 미만)로 푼다. 범위의 major가 제공 측과 다르거나
    범위가 제공 측 현재 버전보다 높으면 앱이 의존을 풀지 못한다. 앱(.kit)은 Kit이 만든
    버전 고정 블록(tools/kit.py lock)도 있어야 한다.
    """
    provided: dict[str, str] = {}
    for p in sorted(EXT_DIR.glob("*/config/extension.toml")):
        data = read_toml(p, Report(), p.parent.parent.name)
        if data and (data.get("package") or {}).get("version"):
            provided[p.parent.parent.name] = data["package"]["version"]

    sources = [(p.parent.parent.name, p) for p in sorted(EXT_DIR.glob("*/config/extension.toml"))]
    sources += [(f"app {p.name}", p) for p in sorted(APP_DIR.glob("*.kit"))]
    for where, path in sources:
        if path.suffix == ".kit":
            # Kit이 붙인 고정 블록은 [settings.app.exts]를 다시 선언해 tomllib가 거부한다 (Kit은 병합) -> 앞부분만 읽는다
            text = path.read_text(encoding="utf-8").split("# BEGIN GENERATED PART")[0]
            try:
                data = tomllib.loads(text)
            except Exception as e:  # noqa: BLE001
                rep.err(where, f"파싱 실패: {e}")
                continue
        else:
            data = read_toml(path, rep, where)
        if data is None:
            continue
        for dep, spec in (data.get("dependencies") or {}).items():
            if not dep.startswith("morph."):
                continue
            req = spec.get("version") if isinstance(spec, dict) else None
            have = provided.get(dep)
            if not isinstance(req, str) or not VERSION_REQ_RE.match(req):
                rep.err(where, f"의존 '{dep}'에 버전 범위 없음 — '{dep}' = {{ version = \"<major>.<minor>\" }} (^ 범위)")
                continue
            if isinstance(spec, dict) and spec.get("exact"):
                rep.warn(where, f"의존 '{dep}'이 exact — 저장소 익스텐션은 ^ 범위로 둔다")
            if have is None:
                continue  # 존재 여부는 다른 검사가 본다
            r, h = _ver_tuple(req), _ver_tuple(have)
            if r[0] != h[0]:
                rep.err(where, f"의존 '{dep}' 범위 ^{req}의 major ≠ 제공 버전 {have}")
            elif r[0] == 0 and len(r) > 1 and r[1] != h[1]:
                # 0.x는 minor가 major 역할: Kit은 ^0.1을 0.1.x로만 푼다 (0.2.0 불가, 2026-11-04 확인)
                rep.err(where, f"의존 '{dep}' 범위 ^{req}는 0.x라 minor가 같아야 함 ≠ 제공 버전 {have}")
            elif r > h[:len(r)]:
                rep.err(where, f"의존 '{dep}' 범위 ^{req}가 제공 버전 {have}보다 높음")

    for p in sorted(APP_DIR.glob("*.kit")):
        text = p.read_text(encoding="utf-8")
        if "# BEGIN GENERATED PART" not in text:
            rep.warn(f"app {p.name}", "버전 고정 블록 없음 — py -3.12 tools/kit.py lock " + p.name)
            continue
        locked = re.findall(r'"(morph\.[^"]+)"', text.split("# BEGIN GENERATED PART", 1)[1])
        if locked:
            rep.err(f"app {p.name}", f"버전 고정 블록에 저장소 익스텐션 {locked} — generateVersionLockSkipLocalExts 확인")


def check_agents(registry: dict, rep: Report) -> None:
    if not AGENT_DIR.exists():
        rep.err("agents", ".claude/agents 없음")
        return
    reg_agents = {v["agent"] for v in registry.values()}
    for ap in sorted(AGENT_DIR.glob("ext-*.md")):
        fm, _ = parse_frontmatter(ap)
        name = ap.stem
        if not fm:
            rep.err(name, "frontmatter 없음")
            continue
        for key in ("name", "description"):
            if not fm.get(key):
                rep.err(name, f"frontmatter '{key}' 없음")
        tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
        if name in REVIEWER_AGENTS:
            if tools & {"Edit", "Write", "NotebookEdit"}:
                rep.err(name, "리뷰어는 읽기 전용이어야 함 (Edit/Write 제거)")
        elif name not in reg_agents:
            rep.warn(name, "레지스트리에 연결된 익스텐션이 없는 에이전트")
        if not fm.get("tools"):
            rep.warn(name, "tools 미지정 — 모든 도구 상속됨")


def check_changed_by(ext_id: str, base: str, rep: Report) -> None:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", base, "--"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
        out += subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        rep.warn("boundary", f"git diff 실패 ({e}) — 경계 검사 생략")
        return
    own = f"source/extensions/{ext_id}/"
    for f in sorted(set(filter(None, out.splitlines()))):
        if f.startswith(own):
            continue
        kind = "공유 파일" if f.startswith(SHARED_PATHS) else "다른 영역"
        rep.err("boundary", f"{ext_id} 담당 범위 밖 변경 ({kind}): {f}")


# ---------- main ----------

def main() -> int:
    # 파이프로 읽힐 때(에이전트/CI) 로캘 인코딩(cp949) 대신 UTF-8로 출력
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ext", help="이 익스텐션만 상세 검사 (교차 검사는 항상 전체)")
    ap.add_argument("--changed-by", help="이 익스텐션 담당 에이전트의 변경 범위 검사")
    ap.add_argument("--base", default="HEAD", help="--changed-by 비교 기준 git ref (기본 HEAD)")
    args = ap.parse_args()

    rep = Report()
    registry = parse_registry(rep)
    on_disk = sorted(p.name for p in EXT_DIR.iterdir() if p.is_dir()) if EXT_DIR.exists() else []
    for rid in registry:
        if rid not in on_disk:
            rep.err(rid, "레지스트리에 있으나 source/extensions/에 폴더 없음")

    contracts: dict[str, Contract] = {}
    scoped = Report()
    for ext_id in on_disk:
        target = rep if (args.ext is None or args.ext == ext_id) else scoped
        c = check_extension(ext_id, registry, target)
        if c:
            contracts[ext_id] = c
    if args.ext and args.ext not in on_disk:
        rep.err(args.ext, "source/extensions/에 없음")

    cross = Report()
    check_cross(contracts, registry, cross)
    check_agents(registry, cross)
    check_versions(cross)
    for bucket in ("errors", "warns"):
        for line in getattr(cross, bucket):
            if args.ext is None or args.ext in line or "registry" in line or "[app " in line:
                getattr(rep, bucket).append(line)

    if args.changed_by:
        check_changed_by(args.changed_by, args.base, rep)

    for w in rep.warns:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    scope = args.ext or "전체"
    print(f"\n{scope}: 익스텐션 {len(contracts)}개 검사 — ERROR {len(rep.errors)}, WARN {len(rep.warns)}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
