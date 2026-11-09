"""로컬 Kit SDK(kit-app-template) 연결 및 빌드/테스트 래퍼.

kit-app-template은 git에서 제외하고 루트의 `kit-app-template/`에 둔다.
`kit-app-template/source`를 이 저장소의 `source/`로 정션(심볼릭 링크) 연결해서
SDK 쪽 `repo build`/`repo test`가 우리 익스텐션을 그대로 사용하게 한다.

사용법:
  python tools/kit.py setup              # SDK 없으면 clone, 버전 확인, source 연결
  python tools/kit.py status             # SDK/연결/빌드 상태
  python tools/kit.py build [args...]    # repo build
  python tools/kit.py test [ext-id...]   # ext-id 지정 시 해당 익스텐션만, 없으면 repo test
  python tools/kit.py launch [app.kit] [args...]  # 앱 실행 (기본 morph.lab.kit)
  python tools/kit.py smoke [app.kit]     # 실제 앱 스모크 (tools/smoke_app.py), 뷰별 스크린샷 _smoke/view<N>.png
  python tools/kit.py repo <args...>     # 그 외 repo 명령 그대로 전달 (예: repo template new)
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK_DIR = ROOT / "kit-app-template"
SDK_REPO = "https://github.com/NVIDIA-Omniverse/kit-app-template.git"
SDK_BRANCH = "110.1.3"
KIT_VERSION = "110.1.3"

IS_WINDOWS = os.name == "nt"
PLATFORM = ("windows" if IS_WINDOWS else "linux") + "-" + ("aarch64" if platform.machine().lower() in ("aarch64", "arm64") else "x86_64")


def die(msg: str) -> None:
    print(f"[kit] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg: str) -> None:
    print(f"[kit] {msg}")


def kit_version() -> str | None:
    xml = SDK_DIR / "tools" / "deps" / "kit-sdk.packman.xml"
    if not xml.is_file():
        return None
    m = re.search(r'name="kit-kernel"\s+version="([0-9.]+)', xml.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def link_target(path: Path) -> Path | None:
    """path가 링크/정션이면 대상 경로, 아니면 None."""
    try:
        return Path(os.readlink(path).removeprefix("\\\\?\\"))
    except (OSError, ValueError):
        return None


def same_path(a: Path, b: Path) -> bool:
    norm = lambda p: os.path.normcase(str(p).rstrip("\\/"))
    return norm(a) == norm(b)


def require_sdk() -> None:
    if not (SDK_DIR / ("repo.bat" if IS_WINDOWS else "repo.sh")).is_file():
        die(f"{SDK_DIR} 에 kit-app-template이 없습니다. `python tools/kit.py setup` 먼저 실행하세요.")
    ver = kit_version()
    if ver != KIT_VERSION:
        info(f"WARNING: Kit SDK 버전 {ver} (기대값 {KIT_VERSION})")


def ensure_link() -> None:
    link = SDK_DIR / "source"
    target = ROOT / "source"
    current = link_target(link)
    if current is not None:
        if same_path(current, target):
            return
        die(f"{link} 가 다른 곳({current})을 가리킵니다. 확인 후 링크를 지우고 다시 실행하세요.")
    if link.exists():
        die(f"{link} 가 실제 폴더로 존재합니다. 내용을 {target} 로 옮긴 뒤 폴더를 지우고 다시 실행하세요.")
    if IS_WINDOWS:
        # 정션은 관리자 권한/개발자 모드 없이 만들 수 있다.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, stdout=subprocess.DEVNULL)
    else:
        link.symlink_to(target, target_is_directory=True)
    info(f"연결: {link} -> {target}")


def repo(*args: str) -> int:
    require_sdk()
    ensure_link()
    cmd = [str(SDK_DIR / "repo.bat")] if IS_WINDOWS else [str(SDK_DIR / "repo.sh")]
    return subprocess.call(cmd + list(args), cwd=SDK_DIR)


def cmd_setup(_: list[str]) -> int:
    if not SDK_DIR.exists():
        info(f"clone {SDK_REPO} ({SDK_BRANCH}) -> {SDK_DIR}")
        subprocess.run(["git", "clone", "--depth", "1", "--branch", SDK_BRANCH, SDK_REPO, str(SDK_DIR)], check=True)
    require_sdk()
    ensure_link()
    info(f"준비 완료 (Kit {kit_version()}). 다음: python tools/kit.py build")
    return 0


def cmd_status(_: list[str]) -> int:
    ver = kit_version()
    info(f"SDK      : {SDK_DIR} ({'Kit ' + ver if ver else '없음'})")
    target = link_target(SDK_DIR / "source")
    info(f"source   : {'-> ' + str(target) if target else '연결 안 됨'}")
    build = SDK_DIR / "_build" / PLATFORM / "release"
    info(f"build    : {build if build.is_dir() else '빌드 전'}")
    exts = sorted(p.name for p in (ROOT / "source" / "extensions").glob("*") if (p / "config" / "extension.toml").is_file())
    info(f"exts     : {', '.join(exts) or '(extension.toml 있는 익스텐션 없음)'}")
    return 0


def cmd_test(args: list[str]) -> int:
    """kit.exe + omni.kit.test로 익스텐션 테스트를 직접 실행한다.

    premake가 만드는 tests-<ext>.bat와 같은 동작이지만 premake 단계가 필요 없다
    (Python 전용 익스텐션. 스마트 앱 컨트롤이 서명 없는 premake5.exe를 막는 환경 대응).
    """
    require_sdk()
    ensure_link()
    ext_ids = [a for a in args if not a.startswith("-")]
    extra = [a for a in args if a.startswith("-")]
    if not ext_ids:
        ext_ids = sorted(p.name for p in (ROOT / "source" / "extensions").glob("*") if (p / "config" / "extension.toml").is_file())
    build = SDK_DIR / "_build" / PLATFORM / "release"
    kit_exe = build / "kit" / ("kit.exe" if IS_WINDOWS else "kit")
    if not kit_exe.is_file():
        die(f"{kit_exe} 없음. `python tools/kit.py build`로 Kit SDK를 먼저 받으세요.")
    cmd = [
        str(kit_exe),
        "--portable-root", str(build),
        "--ext-folder", str(SDK_DIR / "source" / "extensions"),
        "--enable", "omni.kit.test",
        "--/exts/omni.kit.test/runTestsAndQuit=true",
        "--/app/enableStdoutOutput=1",
        "--no-window",
    ]
    cmd += [f"--/exts/omni.kit.test/testExts/{i}={ext_id}" for i, ext_id in enumerate(ext_ids)]
    info(f"test {', '.join(ext_ids)}")
    return subprocess.call(cmd + extra, cwd=build)


DEFAULT_APP = "morph.lab.kit"


def run_app(args: list[str], pre: list[str] | None = None) -> int:
    """_build/.../apps/<app>.kit 을 kit.exe로 직접 실행한다.

    repo launch는 premake가 앱마다 만드는 .bat를 쓰는데, 앱 등록(define_app)은 SDK의
    premake5.lua를 고쳐야 해서 쓰지 않는다. 첫 인자가 *.kit 이면 그 앱, 아니면 DEFAULT_APP.
    """
    require_sdk()
    ensure_link()
    build = SDK_DIR / "_build" / PLATFORM / "release"
    kit_exe = build / "kit" / ("kit.exe" if IS_WINDOWS else "kit")
    app = args.pop(0) if args and args[0].endswith(".kit") else DEFAULT_APP
    app_file = build / "apps" / app
    if not app_file.is_file():
        die(f"{app_file} 없음. `python tools/kit.py build`로 source/apps 를 먼저 반영하세요.")
    cmd = [str(kit_exe), str(app_file), "--ext-folder", str(SDK_DIR / "source" / "extensions")]
    if "streaming" in app and "--no-window" not in args:
        # OS가 창을 스트림 해상도(1920x1080)와 다르게 줄이면 livestream이 프레임을 버린다
        # ("Cannot stream video frame with resolution ..."). 창 없이 띄우면 해상도가 고정된다.
        cmd.append("--no-window")
    if "streaming" in app and "--reset-user" not in args:
        # 이전 실행이 저장한 사용자 설정(뷰포트별 격자 표시 등)이 앱 구성을 덮어쓰지 않게 한다
        cmd.append("--reset-user")
    info(f"launch {app}")
    return subprocess.call(cmd + (pre or []) + args, cwd=build)


def cmd_smoke(args: list[str]) -> int:
    """실제 앱을 띄워 tools/smoke_app.py 시나리오(2x2 분할, 포커스, 카메라, 웹 채널)를 돌리고 종료한다."""
    shot = ROOT / "_smoke"
    shot.mkdir(exist_ok=True)
    os.environ["MORPH_SMOKE_SHOT"] = str(shot)
    return run_app(args, ["--/app/enableStdoutOutput=1", "--exec", str(ROOT / "tools" / "smoke_app.py")])


LOCK_BEGIN = "# BEGIN GENERATED PART"
LOCK_END = "# END GENERATED PART"


def strip_lock(text: str) -> str:
    """앱 .kit에서 Kit이 만든 버전 고정 블록(BEGIN~END GENERATED PART, 앞뒤 # 구분선 포함)을 뺀다."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, v in enumerate(lines) if v.startswith(LOCK_BEGIN)), None)
    if start is None:
        return text
    end = next((i for i, v in enumerate(lines) if i > start and v.startswith(LOCK_END)), None)
    if end is None:
        die(f"고정 블록에 '{LOCK_END}' 줄이 없음 — 파일 끝을 확인하세요")
    # 블록 앞뒤의 '#####' 구분선도 같이 지운다 (Kit이 다시 붙인다)
    if start > 0 and lines[start - 1].startswith("####"):
        start -= 1
    if end + 1 < len(lines) and lines[end + 1].startswith("####"):
        end += 1
    rest = "".join(lines[end + 1:]).strip()
    return "".join(lines[:start]).rstrip() + "\n" + (f"\n{rest}\n" if rest else "")


def cmd_lock(args: list[str]) -> int:
    """앱 .kit의 레지스트리 익스텐션 버전을 고정한다 (repo precache_exts와 같은 Kit 옵션).

    source/apps/<app>.kit 을 _build/.../_lock/ 에 복사해 --ext-precache-mode로 의존성을 풀고,
    Kit이 파일 끝에 쓴 "GENERATED PART" 블록(settings.app.exts.enabled = 정확한 버전 목록)이 생기면
    그 파일을 source/apps 로 옮겨 쓴다 (실패하면 source는 손대지 않는다).
    _build/.../apps 는 source/apps 정션이라 쓰지 않는다. _lock 은 apps 와 같은 깊이라 ${app}/../exts 가 같게 풀린다. 저장소 익스텐션(morph.*)은 고정하지 않는다.
    기본은 기존 고정을 유지한 채 다시 쓰고, -u 이면 고정을 지우고 최신으로 다시 푼다.
    인자 없으면 source/apps/*.kit 전체.
    """
    require_sdk()
    ensure_link()
    update = "-u" in args
    apps = [a for a in args if a.endswith(".kit")] or sorted(p.name for p in (ROOT / "source" / "apps").glob("*.kit"))
    build = SDK_DIR / "_build" / PLATFORM / "release"
    kit_exe = build / "kit" / ("kit.exe" if IS_WINDOWS else "kit")
    if not kit_exe.is_file():
        die(f"{kit_exe} 없음. `python tools/kit.py build`로 Kit SDK를 먼저 받으세요.")
    rc_all = 0
    for app in apps:
        src = ROOT / "source" / "apps" / app
        if not src.is_file():
            die(f"{src} 없음")
        text = src.read_text(encoding="utf-8")
        if update:
            text = strip_lock(text)
        dst = build / "_lock" / app
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        cmd = [
            str(kit_exe), str(dst),
            "--ext-folder", str(SDK_DIR / "source" / "extensions"),
            "--ext-precache-mode",
            "--/app/settings/persistent=0",
            "--/app/settings/loadUserConfig=0",
            "--/app/extensions/generateVersionLock=1",
            "--/app/extensions/generateVersionLockOverwrite=1",
            "--/app/extensions/generateVersionLockSkipLocalExts=1",
            "--/app/extensions/registryEnabled=1",
            "--/app/extensions/detailedSolverExplanation=1",
            "--/app/enableStdoutOutput=1",
            "--/crashreporter/gatherUserStory=0",
        ]
        info(f"lock {app}{' (update)' if update else ''}")
        rc = subprocess.call(cmd, cwd=build)
        out = dst.read_text(encoding="utf-8")
        if rc != 0 or LOCK_BEGIN not in out:
            info(f"lock {app} 실패 (exit={rc}, 고정 블록 {'있음' if LOCK_BEGIN in out else '없음'}). source/apps는 그대로 둔다")
            rc_all = rc_all or rc or 1
            continue
        src.write_text(out.rstrip() + "\n", encoding="utf-8", newline="\n")
        dst.unlink()
        n = sum(1 for line in out[out.index(LOCK_BEGIN):].splitlines() if line.strip().startswith('"'))
        info(f"lock {app}: 익스텐션 {n}개 고정 → {src.relative_to(ROOT)}")
    return rc_all


COMMANDS = {
    "lock": cmd_lock,
    "setup": cmd_setup,
    "status": cmd_status,
    "build": lambda a: repo("build", *a),
    "test": cmd_test,
    "launch": lambda a: run_app(a),
    "smoke": cmd_smoke,
    "repo": lambda a: repo(*a),
}


def main() -> int:
    # 파이프로 읽힐 때(에이전트/CI) 로캘 인코딩(cp949) 대신 UTF-8로 출력
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
