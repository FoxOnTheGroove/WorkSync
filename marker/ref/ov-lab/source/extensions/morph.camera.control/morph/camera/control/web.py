"""웹 메시지 (morph.stream.messaging 1.x 경유). 핸들러는 컨트롤러 큐에 넣기만 하고 요청마다 정확히 한 번 회신한다.

회신 순서: 입력 오류(invalid_argument) → 없는 프리셋(not_found) / 프리셋 개수 상한(resource_exhausted)
→ 조작 대상 없음(failed_precondition) → 성공(ok).
`id: null` 요청의 회신은 stream이 송신을 생략한다 (형식 검사만).
"""
from __future__ import annotations

from typing import Any, List

from .controller import EXT_ID, CameraController, _duration, _finite, _zoom_factor

TYPE_ORBIT = f"{EXT_ID}.orbit"
TYPE_PAN = f"{EXT_ID}.pan"
TYPE_ZOOM = f"{EXT_ID}.zoom"
TYPE_APPLY_PRESET = f"{EXT_ID}.apply_preset"
TYPE_SAVE_PRESET = f"{EXT_ID}.save_preset"
TYPE_LIST_PRESETS = f"{EXT_ID}.list_presets"
TYPE_DELETE_PRESET = f"{EXT_ID}.delete_preset"
TYPE_STATE = f"{EXT_ID}.state"

ERR_INVALID_ARGUMENT = "invalid_argument"
ERR_NOT_FOUND = "not_found"
ERR_FAILED_PRECONDITION = "failed_precondition"
ERR_RESOURCE_EXHAUSTED = "resource_exhausted"

# 웹 남용 방지 상한 (웹 save_preset에만 적용. Python API는 제한 없음). CONTRACT "웹 메시지"에 고정.
MAX_PRESET_NAME_LEN = 64  # 유니코드 코드 포인트 수
MAX_PRESETS = 64  # 저장된 프리셋 전체 개수 (Python으로 저장한 것 포함)

_NUM = {"type": "number"}
_VEC3 = {"type": "array", "items": {"type": "number"}}

SCHEMA_ORBIT = {"type": "object", "required": ["d_yaw", "d_pitch"], "properties": {"d_yaw": _NUM, "d_pitch": _NUM}}
SCHEMA_PAN = {"type": "object", "required": ["dx", "dy"], "properties": {"dx": _NUM, "dy": _NUM}}
SCHEMA_ZOOM = {"type": "object", "required": ["factor"], "properties": {"factor": _NUM}}
SCHEMA_APPLY_PRESET = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}, "duration_s": _NUM},
}
SCHEMA_SAVE_PRESET = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
SCHEMA_LIST_PRESETS = {"type": "object"}
SCHEMA_DELETE_PRESET = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
SCHEMA_STATE = {
    "type": "object",
    "required": ["camera_path", "eye", "target", "fov"],
    "properties": {"camera_path": {"type": "string"}, "eye": _VEC3, "target": _VEC3, "fov": _NUM},
}


class WebHandlers:
    """웹→Kit 핸들러 (메인 업데이트 루프에서 호출됨). 모든 요청에 reply_ok / reply_error 중 하나로 한 번 회신."""

    def __init__(self, controller: CameraController):
        self._controller = controller

    def _precondition_failed(self, ctx: Any, **fields) -> bool:
        reason = self._controller._unavailable_reason()
        if reason is None:
            return False
        ctx.reply_error(ERR_FAILED_PRECONDITION, reason, **fields)
        return True

    def on_orbit(self, payload: dict, ctx: Any) -> None:
        try:
            d_yaw, d_pitch = _finite(payload["d_yaw"], "d_yaw"), _finite(payload["d_pitch"], "d_pitch")
        except ValueError as e:
            ctx.reply_error(ERR_INVALID_ARGUMENT, str(e))
            return
        if self._precondition_failed(ctx):
            return
        self._controller.orbit(d_yaw, d_pitch)
        ctx.reply_ok()

    def on_pan(self, payload: dict, ctx: Any) -> None:
        try:
            dx, dy = _finite(payload["dx"], "dx"), _finite(payload["dy"], "dy")
        except ValueError as e:
            ctx.reply_error(ERR_INVALID_ARGUMENT, str(e))
            return
        if self._precondition_failed(ctx):
            return
        self._controller.pan(dx, dy)
        ctx.reply_ok()

    def on_zoom(self, payload: dict, ctx: Any) -> None:
        try:
            factor = _zoom_factor(payload["factor"])
        except ValueError as e:
            ctx.reply_error(ERR_INVALID_ARGUMENT, str(e))
            return
        if self._precondition_failed(ctx):
            return
        self._controller.zoom(factor)
        ctx.reply_ok()

    def on_apply_preset(self, payload: dict, ctx: Any) -> None:
        name = payload["name"]
        try:
            duration = _duration(payload.get("duration_s", 0.0))
        except ValueError as e:
            ctx.reply_error(ERR_INVALID_ARGUMENT, str(e), name=name)
            return
        if not self._controller._has_preset(name):
            ctx.reply_error(ERR_NOT_FOUND, "unknown preset", name=name)
            return
        if self._precondition_failed(ctx, name=name):
            return
        try:
            self._controller.apply_preset(name, duration)
        except KeyError:  # 방어 (다른 스레드에서 삭제된 경우)
            ctx.reply_error(ERR_NOT_FOUND, "unknown preset", name=name)
            return
        ctx.reply_ok(name=name)

    def on_save_preset(self, payload: dict, ctx: Any) -> None:
        name = payload.get("name")
        if not isinstance(name, str) or not name:  # 스키마 검증이 꺼져 있어도 안전하게
            ctx.reply_error(ERR_INVALID_ARGUMENT, "preset name must be a non-empty string", name="")
            return
        if len(name) > MAX_PRESET_NAME_LEN:
            ctx.reply_error(
                ERR_INVALID_ARGUMENT,
                f"preset name must be at most {MAX_PRESET_NAME_LEN} characters",
                name=name[:MAX_PRESET_NAME_LEN],
                limit=MAX_PRESET_NAME_LEN,
            )
            return
        if not self._controller._has_preset(name) and self._controller._preset_count() >= MAX_PRESETS:
            ctx.reply_error(
                ERR_RESOURCE_EXHAUSTED, f"too many presets (max {MAX_PRESETS})", name=name, limit=MAX_PRESETS
            )
            return
        if self._precondition_failed(ctx, name=name):
            return
        try:
            self._controller.save_preset(name)
        except RuntimeError as e:  # 방어 (위 검사와 같은 조건)
            ctx.reply_error(ERR_FAILED_PRECONDITION, str(e), name=name)
            return
        ctx.reply_ok(name=name)

    def on_list_presets(self, payload: dict, ctx: Any) -> None:
        ctx.reply_ok(presets=self._controller._preset_payloads())

    def on_delete_preset(self, payload: dict, ctx: Any) -> None:
        name = payload.get("name")
        try:
            self._controller.delete_preset(name)
        except KeyError:
            ctx.reply_error(ERR_NOT_FOUND, "unknown preset", name=name if isinstance(name, str) else "")
            return
        ctx.reply_ok(name=name)


def register_web(messaging: Any, controller: CameraController) -> List[Any]:
    """메시지 타입 등록. 반환한 Subscription 목록은 on_shutdown에서 unsubscribe 한다."""
    handlers = WebHandlers(controller)
    subs: List[Any] = []
    try:
        subs.append(messaging.register_outbound(TYPE_STATE, SCHEMA_STATE, owner=EXT_ID))
        subs.append(messaging.register_inbound(TYPE_ORBIT, handlers.on_orbit, SCHEMA_ORBIT, owner=EXT_ID))
        subs.append(messaging.register_inbound(TYPE_PAN, handlers.on_pan, SCHEMA_PAN, owner=EXT_ID))
        subs.append(messaging.register_inbound(TYPE_ZOOM, handlers.on_zoom, SCHEMA_ZOOM, owner=EXT_ID))
        subs.append(
            messaging.register_inbound(TYPE_APPLY_PRESET, handlers.on_apply_preset, SCHEMA_APPLY_PRESET, owner=EXT_ID)
        )
        subs.append(
            messaging.register_inbound(TYPE_SAVE_PRESET, handlers.on_save_preset, SCHEMA_SAVE_PRESET, owner=EXT_ID)
        )
        subs.append(
            messaging.register_inbound(TYPE_LIST_PRESETS, handlers.on_list_presets, SCHEMA_LIST_PRESETS, owner=EXT_ID)
        )
        subs.append(
            messaging.register_inbound(
                TYPE_DELETE_PRESET, handlers.on_delete_preset, SCHEMA_DELETE_PRESET, owner=EXT_ID
            )
        )
    except Exception:
        for sub in subs:
            sub.unsubscribe()
        raise
    return subs
