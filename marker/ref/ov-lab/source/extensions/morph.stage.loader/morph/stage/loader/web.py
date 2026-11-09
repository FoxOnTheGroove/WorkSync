"""웹 메시지 (morph.stream.messaging 1.x 경유). 내부 모듈.

핸들러는 모듈 함수로 두어 테스트에서 가짜 ctx로 직접 호출할 수 있게 한다.
"""

from __future__ import annotations

import asyncio
import time

import carb
from carb.eventdispatcher import get_eventdispatcher
from morph.stream.messaging import get_messaging

from .loader import EXT_ID, StageLoadError, get_loader

MSG_LIST_SCENES = f"{EXT_ID}.list_scenes"
MSG_OPEN_SCENE = f"{EXT_ID}.open_scene"
MSG_RELOAD = f"{EXT_ID}.reload"
MSG_NEW_SCENE = f"{EXT_ID}.new_scene"
MSG_GET_STATE = f"{EXT_ID}.get_state"
MSG_STATE = f"{EXT_ID}.state"

EVENT_CLIENT_CONNECTED = "morph.stream.client_connected"

SCHEMA_LIST = {"type": "object", "properties": {"refresh": {"type": "boolean"}}}
SCHEMA_OPEN = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
SCHEMA_EMPTY = {"type": "object"}
SCHEMA_STATE = {
    "type": "object",
    "required": ["scene_id", "name", "loading"],
    "properties": {
        "scene_id": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "loading": {"type": "boolean"},
    },
}

# 웹 list_scenes {refresh: true}의 다시 스캔 최소 간격. 그 안의 요청은 캐시를 돌려준다.
_WEB_REFRESH_MIN_S = 1.0
_last_web_refresh = 0.0

_tasks: set = set()


def _unavailable(ctx) -> None:
    ctx.reply_error("unavailable", f"{EXT_ID} is not running")


def _live_loader(ctx):
    """실행 중인 로더. 없거나 shutdown됐으면 `unavailable`을 회신하고 None."""
    loader = get_loader()
    if loader is None or not loader._alive:
        _unavailable(ctx)
        return None
    return loader


def _reply_precondition(loader, ctx, e: RuntimeError) -> None:
    # shutdown 뒤의 RuntimeError(_check_idle)는 failed_precondition이 아니라 unavailable
    if not loader._alive:
        _unavailable(ctx)
    else:
        ctx.reply_error("failed_precondition", str(e))


def _spawn(coro) -> None:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def push_state() -> None:
    """웹이 연결돼 있으면 현재 state를 푸시한다."""
    loader = get_loader()
    msg = get_messaging()
    if loader is None or msg is None or not msg.is_connected:
        return
    try:
        msg.send(MSG_STATE, loader._state_payload())
    except Exception as e:  # noqa: BLE001
        carb.log_warn(f"[{EXT_ID}] state 송신 실패: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------- 핸들러
def handle_list_scenes(payload: dict, ctx) -> None:
    global _last_web_refresh
    loader = _live_loader(ctx)
    if loader is None:
        return
    refresh = payload.get("refresh") is True
    if refresh:
        now = time.monotonic()
        if now - _last_web_refresh < _WEB_REFRESH_MIN_S:
            refresh = False  # 1초에 1회 제한: 캐시(설정이 바뀌었으면 그 반영은 함)
        else:
            _last_web_refresh = now
    scenes = loader.list_scenes(refresh=refresh)
    ctx.reply_ok(scenes=[s.to_dict() for s in scenes], current_scene_id=loader.current()["scene_id"])


def handle_get_state(payload: dict, ctx) -> None:
    loader = _live_loader(ctx)
    if loader is None:
        return
    ctx.reply_ok(**loader._state_payload())


async def _finish(loader, op, ctx, on_ok) -> None:
    try:
        await loader._run(op)
    except StageLoadError as e:
        ctx.reply_error(e.code, str(e))
        return
    except asyncio.CancelledError:
        # 취소(shutdown 등): 아직 회신하지 않았으면 unavailable을 회신하고 그대로 전파
        if not ctx.replied:
            try:
                _unavailable(ctx)
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] 취소 회신 실패: {type(e).__name__}: {e}")
        raise
    except Exception as e:  # noqa: BLE001
        if not loader._alive:
            _unavailable(ctx)
        else:
            ctx.reply_error("open_failed", f"{type(e).__name__}: {e}")
        return
    on_ok()


def handle_open_scene(payload: dict, ctx) -> None:
    loader = _live_loader(ctx)
    if loader is None:
        return
    scene_id = payload.get("id")
    if not isinstance(scene_id, str) or not scene_id:
        ctx.reply_error("invalid_argument", "id must be a non-empty string")
        return
    try:
        op, info = loader._reserve_open_scene(scene_id)
    except KeyError:
        ctx.reply_error("not_found", f"scene not found: {scene_id}")
        return
    except RuntimeError as e:
        _reply_precondition(loader, ctx, e)
        return
    _spawn(_finish(loader, op, ctx, lambda: ctx.reply_ok(scene=info.to_dict())))


def handle_reload(payload: dict, ctx) -> None:
    loader = _live_loader(ctx)
    if loader is None:
        return
    try:
        op = loader._reserve_reload()
    except RuntimeError as e:
        _reply_precondition(loader, ctx, e)
        return
    _spawn(_finish(loader, op, ctx, lambda: ctx.reply_ok(scene_id=loader.current()["scene_id"])))


def handle_new_scene(payload: dict, ctx) -> None:
    loader = _live_loader(ctx)
    if loader is None:
        return
    try:
        op = loader._reserve_new()
    except RuntimeError as e:
        _reply_precondition(loader, ctx, e)
        return
    _spawn(_finish(loader, op, ctx, lambda: ctx.reply_ok()))


# ---------------------------------------------------------------------- 등록
class WebBridge:
    def __init__(self):
        self._subs = []
        self._conn_sub = None
        msg = get_messaging()
        if msg is None:
            carb.log_warn(f"[{EXT_ID}] morph.stream.messaging 없음: 웹 메시지 비활성")
            return
        self._subs = [
            msg.register_outbound(MSG_STATE, schema=SCHEMA_STATE, owner=EXT_ID),
            msg.register_inbound(MSG_LIST_SCENES, handle_list_scenes, schema=SCHEMA_LIST, owner=EXT_ID),
            msg.register_inbound(MSG_OPEN_SCENE, handle_open_scene, schema=SCHEMA_OPEN, owner=EXT_ID),
            msg.register_inbound(MSG_RELOAD, handle_reload, schema=SCHEMA_EMPTY, owner=EXT_ID),
            msg.register_inbound(MSG_NEW_SCENE, handle_new_scene, schema=SCHEMA_EMPTY, owner=EXT_ID),
            msg.register_inbound(MSG_GET_STATE, handle_get_state, schema=SCHEMA_EMPTY, owner=EXT_ID),
        ]
        self._conn_sub = get_eventdispatcher().observe_event(
            observer_name=f"{EXT_ID}:client_connected",
            event_name=EVENT_CLIENT_CONNECTED,
            on_event=lambda _e: push_state(),
        )

    def destroy(self) -> None:
        self._conn_sub = None
        for sub in self._subs:
            try:
                sub.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        self._subs = []
