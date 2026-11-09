"""StageLoader: 장면 목록·열기·이벤트 (내부 모듈, 공개 심볼은 __init__에서 export)."""

from __future__ import annotations

import os
import time
from typing import Callable

import carb
import carb.settings
import omni.usd
from carb.eventdispatcher import get_eventdispatcher
from morph.perf.monitor import async_scope

from . import scanner
from .scanner import SceneInfo

EXT_ID = "morph.stage.loader"
EVENT_OPENED = "morph.stage.opened"
EVENT_OPEN_FAILED = "morph.stage.open_failed"
M_OPEN_MS = f"{EXT_ID}.open_ms"

S_ROOT = f"/exts/{EXT_ID}"
S_ROOTS = f"{S_ROOT}/roots"
S_INCLUDE_SAMPLES = f"{S_ROOT}/includeSamples"
S_FILE_EXTENSIONS = f"{S_ROOT}/fileExtensions"
S_MAX_DEPTH = f"{S_ROOT}/maxDepth"
S_MAX_SCENES = f"{S_ROOT}/maxScenes"
S_STARTUP_SCENE = f"{S_ROOT}/startupScene"

ERR_OPEN_FAILED = "open_failed"

# 목록에 없는 id로 다시 스캔하는 최소 간격 (웹에서 없는 id를 연타해도 스캔 폭주 방지)
_MISS_RESCAN_MIN_S = 1.0
# 열기 future가 OPENED보다 먼저 끝나 fallback이 이벤트를 낸 뒤, 뒤늦게 오는 같은 OPENED를 무시하는 시간
_LATE_OPENED_WINDOW_S = 5.0


class StageLoadError(Exception):
    """omni.usd 열기 실패. `code`는 웹 실패 code와 같다 (`open_failed`)."""

    def __init__(self, message: str, path: str = "", code: str = ERR_OPEN_FAILED):
        super().__init__(message)
        self.code = code
        self.path = path or ""


class _Op:
    """이 익스텐션이 시작한 열기 하나."""

    __slots__ = ("kind", "url", "scene_id", "finished")

    def __init__(self, kind: str, url: str | None, scene_id: str | None):
        self.kind = kind  # "open" | "reload" | "new"
        self.url = url
        self.scene_id = scene_id
        self.finished = False  # OPENED 처리(또는 실패 처리) 끝


_instance: "StageLoader | None" = None


def get_loader() -> "StageLoader | None":
    """싱글턴. startup 이후 유효, shutdown 이후 None."""
    return _instance


def _set_instance(inst: "StageLoader | None") -> None:
    global _instance
    _instance = inst


class StageLoader:
    def __init__(self, samples_dir: str | None):
        self._samples_dir = samples_dir
        self._settings = carb.settings.get_settings()
        self._scenes: list[SceneInfo] | None = None
        self._by_id: dict[str, SceneInfo] = {}
        self._by_path: dict[str, SceneInfo] = {}
        self._scan_key = None
        self._last_miss_scan = 0.0
        self._warned: set[str] = set()
        self._op: _Op | None = None
        self._cur_path: str | None = None
        self._cur_scene_id: str | None = None
        self._state_listeners: list[Callable[[], None]] = []
        self._alive = True
        # (path_key 또는 "" (새 장면), 만료 시각): fallback으로 처리한 열기의 뒤늦은 OPENED 하나를 무시
        self._suppress_late: tuple[str, float] | None = None
        self._stage_subs = []
        ctx = omni.usd.get_context()
        ed = get_eventdispatcher()
        self._stage_subs = [
            ed.observe_event(
                observer_name=f"{EXT_ID}:stage_opened",
                event_name=ctx.stage_event_name(omni.usd.StageEventType.OPENED),
                on_event=self._on_stage_opened,
            ),
            ed.observe_event(
                observer_name=f"{EXT_ID}:stage_closed",
                event_name=ctx.stage_event_name(omni.usd.StageEventType.CLOSED),
                on_event=self._on_stage_closed,
            ),
        ]
        self._read_current_from_context()

    def _destroy(self) -> None:
        self._alive = False
        self._stage_subs = []
        self._state_listeners = []

    # ------------------------------------------------------------------ 내부 유틸
    def _warn_once(self, msg: str) -> None:
        if msg in self._warned:
            return
        self._warned.add(msg)
        carb.log_warn(f"[{EXT_ID}] {msg}")

    def _add_state_listener(self, fn: Callable[[], None]) -> None:
        """(내부) 상태가 바뀔 때 호출 (웹 state 푸시용)."""
        self._state_listeners.append(fn)

    def _notify_state(self) -> None:
        for fn in list(self._state_listeners):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] state listener 실패: {type(e).__name__}: {e}")

    def _dispatch(self, name: str, payload: dict) -> None:
        # carb 이벤트 페이로드에 None을 넣지 않는다: 키를 빼면 관찰자 e["key"]가 None을 돌려준다.
        clean = {k: v for k, v in payload.items() if v is not None}
        try:
            get_eventdispatcher().dispatch_event(name, payload=clean)
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] {name} 발행 실패: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ 목록
    def _current_scan_key(self):
        s = self._settings
        roots = s.get(S_ROOTS) or []
        if isinstance(roots, str):
            roots = [roots]
        include = s.get(S_INCLUDE_SAMPLES)
        include = True if include is None else bool(include)
        exts = s.get(S_FILE_EXTENSIONS)
        if isinstance(exts, str):
            exts = [exts]
        max_depth = s.get(S_MAX_DEPTH)
        max_scenes = s.get(S_MAX_SCENES)
        return (
            tuple(str(r) for r in roots),
            include,
            scanner.normalize_extensions(exts),
            4 if max_depth is None else int(max_depth),
            1000 if max_scenes is None else int(max_scenes),
        )

    def _rescan(self, key) -> None:
        roots_cfg, include, exts, max_depth, max_scenes = key
        roots = scanner.parse_roots(
            list(roots_cfg), self._samples_dir if include else None, warn=self._warn_once
        )
        scenes = scanner.scan(roots, exts, max_depth, max_scenes, warn=self._warn_once)
        self._scenes = scenes
        self._by_id = {s.id: s for s in scenes}
        self._by_path = {}
        for s in scenes:
            self._by_path.setdefault(scanner.path_key(s.path), s)
        self._scan_key = key
        # 현재 장면 id를 새 목록 기준으로 다시 계산
        self._cur_scene_id = self._scene_for_path(self._cur_path)

    def list_scenes(self, refresh: bool = False) -> list[SceneInfo]:
        key = self._current_scan_key()
        if refresh or self._scenes is None or key != self._scan_key:
            self._rescan(key)
        return list(self._scenes or [])

    def find_scene(self, scene_id: str) -> SceneInfo | None:
        if not isinstance(scene_id, str) or not scene_id:
            return None
        self.list_scenes()
        info = self._by_id.get(scene_id)
        if info is None and time.monotonic() - self._last_miss_scan >= _MISS_RESCAN_MIN_S:
            self._last_miss_scan = time.monotonic()
            self.list_scenes(refresh=True)
            info = self._by_id.get(scene_id)
        return info

    def _scene_for_path(self, path: str | None) -> str | None:
        key = scanner.path_key(path)
        if key is None or self._scenes is None:
            return None
        info = self._by_path.get(key)
        return info.id if info else None

    # ------------------------------------------------------------------ 상태
    @property
    def is_loading(self) -> bool:
        return self._op is not None

    def current(self) -> dict:
        return {"scene_id": self._cur_scene_id, "path": self._cur_path, "loading": self.is_loading}

    def _state_payload(self) -> dict:
        """(내부) 웹 state 페이로드."""
        return {
            "scene_id": self._cur_scene_id,
            "name": scanner.display_name(self._cur_path),
            "loading": self.is_loading,
        }

    def _read_current_from_context(self) -> None:
        ctx = omni.usd.get_context()
        path = None
        try:
            if ctx.get_stage() is not None and not ctx.is_new_stage():
                path = scanner.norm_path(ctx.get_stage_url())
        except Exception:  # noqa: BLE001
            path = None
        self._cur_path = path
        if path is not None and self._scenes is None:
            self.list_scenes()  # 외부 열기 id 판별에 목록이 필요하다 (첫 1회만)
        self._cur_scene_id = self._scene_for_path(path)

    # ------------------------------------------------------------------ stage 이벤트
    def _op_matches_current(self, op: _Op) -> bool:
        """방금 열린 장면(current)이 op가 연 것과 같은 파일인지 (path_key 비교)."""
        if op.kind == "new":
            return self._cur_path is None
        key = scanner.path_key(op.url)
        return key is not None and key == scanner.path_key(self._cur_path)

    def _complete_op(self, op: _Op) -> None:
        """op를 끝내고 current를 확정한다 (호출 전에 _read_current_from_context).

        scene_id는 열린 경로가 op.url과 같은 파일일 때만 op.scene_id를 쓰고, 아니면 목록에서 경로로 조회한 값
        (_read_current_from_context 결과)을 그대로 둔다. scene_id와 path가 어긋난 쌍을 내지 않기 위함.
        """
        op.finished = True
        if self._op is op:
            self._op = None
        if op.scene_id and op.kind != "new" and self._op_matches_current(op):
            # 같은 파일인데 목록 조회가 어긋난 경우(표기 차이) 요청 id를 쓴다
            self._cur_scene_id = op.scene_id

    def _emit_opened(self, source: str) -> None:
        self._dispatch(EVENT_OPENED, {"scene_id": self._cur_scene_id, "path": self._cur_path or "", "source": source})
        self._notify_state()

    def _take_suppressed_late(self) -> bool:
        """fallback으로 이미 처리한 열기의 뒤늦은 OPENED면 True (한 번만)."""
        s, self._suppress_late = self._suppress_late, None
        if s is None:
            return False
        key, deadline = s
        return time.monotonic() <= deadline and (scanner.path_key(self._cur_path) or "") == key

    def _on_stage_opened(self, _e) -> None:
        if not self._alive:
            return
        self._read_current_from_context()
        op = self._op
        if op is not None and not op.finished:
            self._suppress_late = None
            if self._op_matches_current(op):
                self._complete_op(op)
                self._emit_opened("loader")
                return
            # 진행 중 열기와 다른 파일이 열림: 외부 열기로 처리 (op는 계속 진행 중, scene_id는 목록 조회 값)
            self._emit_opened("external")
            return
        if self._take_suppressed_late():
            return
        self._emit_opened("external")

    def _on_stage_closed(self, _e) -> None:
        if not self._alive or self._op is not None:
            return
        self._cur_path = None
        self._cur_scene_id = None

    # ------------------------------------------------------------------ 열기
    def _check_idle(self) -> None:
        if not self._alive:
            raise RuntimeError(f"{EXT_ID} is shut down")
        if self._op is not None:
            raise RuntimeError("another stage open is in progress")

    def _reserve_open_scene(self, scene_id: str) -> tuple[_Op, SceneInfo]:
        """(내부) 동기 검사 + 예약. 웹 핸들러가 지연 회신 전에 busy를 즉시 판단하려고 쓴다."""
        info = self.find_scene(scene_id)
        if info is None:
            raise KeyError(scene_id)
        self._check_idle()
        return self._begin("open", info.path, info.id), info

    def _reserve_open_path(self, path: str) -> _Op:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        self._check_idle()
        url = path.strip()
        if "://" not in url and not url.lower().startswith("file:") and not os.path.isabs(url):
            url = os.path.abspath(url)
        if self._scenes is None:
            self.list_scenes()
        return self._begin("open", url, self._scene_for_path(url))

    def _reserve_reload(self) -> _Op:
        self._check_idle()
        if self._cur_path is None:
            raise RuntimeError("no file stage is open (new or no stage)")
        return self._begin("reload", self._cur_path, self._cur_scene_id)

    def _reserve_new(self) -> _Op:
        self._check_idle()
        return self._begin("new", None, None)

    def _begin(self, kind: str, url: str | None, scene_id: str | None) -> _Op:
        op = _Op(kind, url, scene_id)
        self._op = op
        self._suppress_late = None
        self._notify_state()  # loading: true
        return op

    async def _run(self, op: _Op) -> None:
        """(내부) 예약한 열기를 실행한다. 실패면 StageLoadError."""
        ctx = omni.usd.get_context()
        ok, err = False, ""
        try:
            async with async_scope(M_OPEN_MS):
                if op.kind == "new":
                    result = await ctx.new_stage_async()
                elif op.kind == "reload":
                    result = await ctx.reopen_stage_async()
                else:
                    result = await ctx.open_stage_async(op.url)
            ok, err = bool(result[0]), str(result[1] or "")
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"
        except BaseException:
            # 취소(CancelledError) 등: 예약만 풀고 그대로 전파
            if self._op is op:
                self._op = None
                op.finished = True
                if self._alive:
                    self._notify_state()
            raise

        if op.finished:
            # OPENED 처리에서 이미 마무리됨
            if ok:
                return
            # (드묾) OPENED 뒤 실패 보고: 실패로 처리하되 상태는 이미 갱신됨
        elif ok:
            # OPENED 이벤트보다 future가 먼저 끝난 경우: 여기서 같은 처리를 하고,
            # 뒤늦게 오는 같은 OPENED 하나는 무시한다 (external 이벤트 중복 방지)
            if self._op is op and self._alive:
                self._read_current_from_context()
                self._complete_op(op)
                self._suppress_late = (
                    scanner.path_key(self._cur_path) or "",
                    time.monotonic() + _LATE_OPENED_WINDOW_S,
                )
                self._emit_opened("loader")
            elif self._op is op:
                self._op = None
                op.finished = True
            return

        if self._op is op:
            self._op = None
        op.finished = True
        if self._alive:
            self._read_current_from_context()
        message = err or "stage open failed"
        path = op.url or ""
        carb.log_warn(f"[{EXT_ID}] 장면 열기 실패: {path or '(new stage)'}: {message}")
        if self._alive:
            self._dispatch(
                EVENT_OPEN_FAILED,
                {"scene_id": op.scene_id, "path": path, "error": ERR_OPEN_FAILED, "message": message},
            )
            self._notify_state()
        raise StageLoadError(message, path)

    # ------------------------------------------------------------------ 공개 async API
    async def open_scene(self, scene_id: str) -> SceneInfo:
        op, info = self._reserve_open_scene(scene_id)
        await self._run(op)
        return info

    async def open_path(self, path: str) -> None:
        op = self._reserve_open_path(path)
        await self._run(op)

    async def reload(self) -> SceneInfo | None:
        op = self._reserve_reload()
        await self._run(op)
        return self._by_id.get(self._cur_scene_id) if self._cur_scene_id else None

    async def new_scene(self) -> None:
        op = self._reserve_new()
        await self._run(op)
