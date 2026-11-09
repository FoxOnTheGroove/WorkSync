"""웹 메시지 (morph.stream.messaging 경유)."""

from __future__ import annotations

from typing import Callable

from .layout import LAYOUTS

__all__ = [
    "WebBridge",
    "EXT_ID",
    "MSG_SET_LAYOUT",
    "MSG_SET_VIEW_CAMERA",
    "MSG_FOCUS_VIEW",
    "MSG_GET_STATE",
    "MSG_LIST_CAMERAS",
    "MSG_SET_SIDE_RATIO",
    "MSG_STATE",
    "ERR_INVALID_ARGUMENT",
    "ERR_NOT_FOUND",
    "ERR_FAILED_PRECONDITION",
    "ERR_UNAVAILABLE",
]

EXT_ID = "morph.viewport.split"

# 웹 type = `<owner ext-id>.<name>` (stream 1.x 규칙). carb 이벤트 이름(`morph.viewport.*`)과는 별개다.
MSG_SET_LAYOUT = f"{EXT_ID}.set_layout"
MSG_SET_VIEW_CAMERA = f"{EXT_ID}.set_view_camera"
MSG_FOCUS_VIEW = f"{EXT_ID}.focus_view"
MSG_GET_STATE = f"{EXT_ID}.get_state"
MSG_LIST_CAMERAS = f"{EXT_ID}.list_cameras"
MSG_SET_SIDE_RATIO = f"{EXT_ID}.set_side_ratio"
MSG_STATE = f"{EXT_ID}.state"

# 핸들러 실패 code (CONTRACT "웹 메시지")
ERR_INVALID_ARGUMENT = "invalid_argument"
ERR_NOT_FOUND = "not_found"
ERR_FAILED_PRECONDITION = "failed_precondition"
ERR_UNAVAILABLE = "unavailable"

SCHEMA_SET_LAYOUT = {
    "type": "object",
    "required": ["layout"],
    "properties": {"layout": {"type": "string", "enum": list(LAYOUTS)}},
}
SCHEMA_SET_VIEW_CAMERA = {
    "type": "object",
    "required": ["view_id", "camera_path"],
    "properties": {"view_id": {"type": "integer"}, "camera_path": {"type": "string"}},
}
SCHEMA_FOCUS_VIEW = {
    "type": "object",
    "required": ["view_id"],
    "properties": {"view_id": {"type": "integer"}},
}
SCHEMA_GET_STATE = {"type": "object"}
# ratio: stream 스키마의 "number"가 bool·NaN·inf를 거른다고 가정하지 않는다 (stream CONTRACT에 규칙 없음,
# 검증을 끌 수도 있음). 핸들러가 SplitView.set_side_ratio로 다시 검사한다 → invalid_argument.
SCHEMA_SET_SIDE_RATIO = {
    "type": "object",
    "required": ["ratio"],
    "properties": {"ratio": {"type": "number"}},
}
SCHEMA_LIST_CAMERAS = {"type": "object"}
# list_cameras 성공 회신 형식 (CONTRACT 표와 같음). stream은 회신을 outbound 스키마로 검증하지 않으므로
# 등록하지 않고 테스트에서 이 정의로 확인한다 (요청 type은 register_outbound로 선언하지 않는다 — stream 규칙).
_CAMERA_SCHEMA = {
    "type": "object",
    "required": ["path", "name", "builtin"],
    "properties": {
        "path": {"type": "string"},
        "name": {"type": "string"},
        "builtin": {"type": "boolean"},
    },
}
SCHEMA_LIST_CAMERAS_REPLY = {
    "type": "object",
    "required": ["ok", "cameras"],
    "properties": {
        "ok": {"type": "boolean", "enum": [True]},
        "cameras": {"type": "array", "items": _CAMERA_SCHEMA},
    },
}
_RECT_SCHEMA = {
    # 앱 메인 창 기준 0..1, 원점 좌상단. 모르면 null (CONTRACT "뷰 화면 영역")
    "type": ["object", "null"],
    "required": ["x", "y", "w", "h"],  # null이면 적용되지 않음 (object일 때만)
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "w": {"type": "number"},
        "h": {"type": "number"},
    },
}
_VIEW_SCHEMA = {
    "type": "object",
    "required": ["view_id", "viewport_name", "camera_path", "rect"],
    "properties": {
        "view_id": {"type": "integer"},
        "viewport_name": {"type": "string"},
        "camera_path": {"type": "string"},
        "rect": _RECT_SCHEMA,
    },
}
SCHEMA_STATE = {
    "type": "object",
    "required": ["layout", "focused_view_id", "views", "side_ratio"],
    "properties": {
        "layout": {"type": "string"},
        "focused_view_id": {"type": "integer"},
        "views": {"type": "array", "items": _VIEW_SCHEMA},
        "side_ratio": {"type": "number"},  # 1.5.0
    },
}


class WebBridge:
    """웹 명령 → SplitView. 요청마다 정확히 한 번 `ctx.reply_ok` / `ctx.reply_error`로 회신한다.

    SplitView의 예상된 예외는 code로 바꿔 회신한다: IndexError → not_found, ValueError → invalid_argument,
    RuntimeError(열린 스테이지 없음) → failed_precondition, 익스텐션이 실행 중이 아님 → unavailable
    (핸들러 진입 뒤 shutdown된 경우의 RuntimeError 포함).
    그 외 예외는 그대로 흘려 stream이 `handler_error`로 회신하게 둔다.
    """

    def __init__(self, get_view: Callable[[], object]):
        self._get_view = get_view

    def register(self, messaging) -> list:
        """inbound 6종 + outbound state 등록. 반환 Subscription은 호출자가 보관·해제한다."""
        subs = []
        try:
            subs.append(messaging.register_outbound(MSG_STATE, schema=SCHEMA_STATE, owner=EXT_ID, version=1))
            for msg_type, handler, schema in (
                (MSG_SET_LAYOUT, self.on_set_layout, SCHEMA_SET_LAYOUT),
                (MSG_SET_VIEW_CAMERA, self.on_set_view_camera, SCHEMA_SET_VIEW_CAMERA),
                (MSG_FOCUS_VIEW, self.on_focus_view, SCHEMA_FOCUS_VIEW),
                (MSG_GET_STATE, self.on_get_state, SCHEMA_GET_STATE),
                (MSG_LIST_CAMERAS, self.on_list_cameras, SCHEMA_LIST_CAMERAS),
                (MSG_SET_SIDE_RATIO, self.on_set_side_ratio, SCHEMA_SET_SIDE_RATIO),
            ):
                subs.append(messaging.register_inbound(msg_type, handler, schema, owner=EXT_ID, version=1))
        except Exception:
            for sub in subs:
                sub.unsubscribe()
            raise
        return subs

    def _view(self, ctx):
        """실행 중인 SplitView. 없으면(시작 전·shutdown 뒤) `unavailable`을 회신하고 None."""
        view = self._get_view()
        if view is None or getattr(view, "_closed", False):
            ctx.reply_error(ERR_UNAVAILABLE, f"{EXT_ID} is not running")
            return None
        return view

    @staticmethod
    def _call(ctx, view, fn, *args, view_id=None) -> bool:
        """SplitView 호출. 예상된 예외는 실패 회신으로 바꾸고 False. 성공이면 True (회신은 호출자가).

        RuntimeError는 그 사이 `view`가 shutdown됐으면 `unavailable`, 아니면 `failed_precondition`.
        """
        try:
            fn(*args)
        except IndexError as exc:
            fields = {} if view_id is None else {"view_id": view_id}
            ctx.reply_error(ERR_NOT_FOUND, str(exc), **fields)
            return False
        except ValueError as exc:
            ctx.reply_error(ERR_INVALID_ARGUMENT, str(exc))
            return False
        except RuntimeError as exc:
            if getattr(view, "_closed", False):
                ctx.reply_error(ERR_UNAVAILABLE, f"{EXT_ID} is not running")
            else:
                ctx.reply_error(ERR_FAILED_PRECONDITION, str(exc))
            return False
        return True

    def on_set_layout(self, payload: dict, ctx) -> None:
        view = self._view(ctx)
        if view is not None and self._call(ctx, view, view.set_layout, payload["layout"]):
            ctx.reply_ok()

    def on_set_view_camera(self, payload: dict, ctx) -> None:
        view = self._view(ctx)
        view_id = payload["view_id"]
        if view is not None and self._call(
            ctx, view, view.set_view_camera, view_id, payload["camera_path"], view_id=view_id
        ):
            ctx.reply_ok()

    def on_focus_view(self, payload: dict, ctx) -> None:
        view = self._view(ctx)
        view_id = payload["view_id"]
        if view is not None and self._call(ctx, view, view.focus_view, view_id, view_id=view_id):
            ctx.reply_ok()

    def on_get_state(self, payload: dict, ctx) -> None:
        view = self._view(ctx)
        if view is not None:
            ctx.reply_ok(**view._state_payload())  # noqa: SLF001 - 같은 패키지

    def on_list_cameras(self, payload: dict, ctx) -> None:
        view = self._view(ctx)
        if view is None:
            return
        cameras = view._list_cameras_or_none()  # noqa: SLF001 - 같은 패키지
        if cameras is None:
            ctx.reply_error(ERR_FAILED_PRECONDITION, "no open stage")
            return
        ctx.reply_ok(cameras=[cam.to_dict() for cam in cameras])

    def on_set_side_ratio(self, payload: dict, ctx) -> None:
        """`{ratio}` → `{ok: true, side_ratio: <적용값>}`. 숫자 아님(bool 포함)·NaN·inf·누락은 invalid_argument."""
        view = self._view(ctx)
        if view is None:
            return
        applied = []
        if self._call(ctx, view, lambda r: applied.append(view.set_side_ratio(r)), payload.get("ratio")):
            ctx.reply_ok(side_ratio=applied[0])
