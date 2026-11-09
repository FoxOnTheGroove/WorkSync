"""morph.camera.control 익스텐션 진입점. 배선만 한다 (로직은 controller.py)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import carb
import carb.eventdispatcher
import carb.settings
import omni.ext
import omni.kit.app
import omni.usd
from morph.stream.messaging import get_messaging
from omni.kit.viewport.utility import get_active_viewport, get_viewport_from_window_name

try:  # 선택 API (창 이름까지 얻기)
    from omni.kit.viewport.utility import get_active_viewport_and_window
except ImportError:  # pragma: no cover
    get_active_viewport_and_window = None

from .controller import EXT_ID, CameraController
from .web import TYPE_STATE, register_web

EVENT_FOCUS_CHANGED = "morph.viewport.focus_changed"  # 소유: morph.viewport.split (의존 선언 없이 이벤트만)
EVENT_CLIENT_CONNECTED = "morph.stream.client_connected"

_controller: Optional[CameraController] = None


def get_camera_controller() -> Optional[CameraController]:
    """싱글턴. startup 이후 유효, shutdown 이후 None."""
    return _controller


# ---------------------------------------------------------------------- 주입용 기본 구현

def _dispatch(name: str, payload: Dict[str, Any]) -> None:
    carb.eventdispatcher.get_eventdispatcher().dispatch_event(name, payload=payload)


def _send_state(payload: Dict[str, Any], force: bool) -> bool:
    msg = get_messaging()  # 캐시하지 않는다
    if msg is None or not (force or msg.is_connected):
        return False
    try:
        msg.send(TYPE_STATE, payload)
    except ValueError as e:  # 스키마 위반·직렬화 불가 등: 송신만 포기하고 업데이트는 계속
        carb.log_warn(f"[{EXT_ID}] cannot send {TYPE_STATE}: {e}")
        return False
    return True


def _get_setting(path: str) -> Any:
    return carb.settings.get_settings().get(path)


def _resolve_viewport(name: str) -> Any:
    return get_viewport_from_window_name(name)


def _active_viewport() -> Tuple[Any, str]:
    """(viewport_api, 창 이름). 뷰포트가 없으면 (None, "")."""
    viewport_api, window = None, None
    if get_active_viewport_and_window is not None:
        try:
            viewport_api, window = get_active_viewport_and_window()
        except Exception:  # noqa: BLE001 -- 뷰포트 창 없음
            viewport_api, window = None, None
    if viewport_api is None:
        try:
            viewport_api = get_active_viewport()
        except Exception:  # noqa: BLE001
            viewport_api = None
    name = ""
    if window is not None:
        name = getattr(window, "title", "") or getattr(window, "name", "") or ""
    return viewport_api, str(name)


# ---------------------------------------------------------------------- IExt

class CameraControlExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        global _controller
        self._web_subs: List[Any] = []
        self._event_subs: List[Any] = []
        self._update_sub = None
        self._update_failed = False

        controller = CameraController(
            dispatch=_dispatch,
            send_state=_send_state,
            viewport_resolver=_resolve_viewport,
            active_viewport_getter=_active_viewport,
            settings_getter=_get_setting,
        )
        self._controller = controller

        # 초기 대상 = Kit 활성 뷰포트. 없으면 update에서 주기적으로 다시 찾는다.
        viewport_api, name = _active_viewport()
        if viewport_api is not None:
            controller._set_target_viewport(viewport_api, name)

        dispatcher = carb.eventdispatcher.get_eventdispatcher()
        self._event_subs = [
            dispatcher.observe_event(
                observer_name=f"{EXT_ID}:focus_changed",
                event_name=EVENT_FOCUS_CHANGED,
                on_event=self._on_focus_changed,
            ),
            dispatcher.observe_event(
                observer_name=f"{EXT_ID}:client_connected",
                event_name=EVENT_CLIENT_CONNECTED,
                on_event=self._on_client_connected,
            ),
        ]
        try:
            self._event_subs.extend(self._subscribe_stage_events(dispatcher))
        except Exception as e:  # noqa: BLE001 -- 카메라 제어 자체는 계속 동작해야 함
            carb.log_warn(f"[{EXT_ID}] stage event subscription failed: {type(e).__name__}: {e}")

        update_event = getattr(omni.kit.app, "GLOBAL_EVENT_UPDATE", None)
        if update_event:
            self._update_sub = dispatcher.observe_event(
                observer_name=f"{EXT_ID}:update", event_name=update_event, on_event=self._on_update
            )
        else:
            self._update_sub = (
                omni.kit.app.get_app()
                .get_update_event_stream()
                .create_subscription_to_pop(self._on_update, name=f"{EXT_ID}:update")
            )

        messaging = get_messaging()
        if messaging is None:
            carb.log_warn(f"[{EXT_ID}] morph.stream.messaging not available; web messages disabled")
        else:
            try:
                self._web_subs = register_web(messaging, controller)
            except (ValueError, RuntimeError) as e:
                carb.log_error(f"[{EXT_ID}] web message registration failed: {e}")
                self._web_subs = []

        _controller = controller

    def on_shutdown(self):
        global _controller
        _controller = None
        for sub in self._web_subs:
            sub.unsubscribe()
        self._web_subs = []
        self._event_subs = []
        self._update_sub = None
        if self._controller is not None:
            self._controller._shutdown()
            self._controller = None

    # 콜백: 예외가 업데이트 루프로 새지 않게 경고로만 남긴다
    def _on_update(self, _event):
        try:
            self._controller._update()
            self._update_failed = False
        except Exception as e:  # noqa: BLE001
            if not self._update_failed:  # 연속 실패는 한 번만 기록
                carb.log_warn(f"[{EXT_ID}] update failed: {type(e).__name__}: {e}")
            self._update_failed = True

    def _subscribe_stage_events(self, dispatcher) -> List[Any]:
        """기본 USD 컨텍스트의 스테이지 열림/닫힘 구독 (보간·대기 명령 취소용)."""
        ctx = omni.usd.get_context()
        if ctx is None:
            return []
        types = [omni.usd.StageEventType.OPENED, omni.usd.StageEventType.CLOSED]
        if hasattr(ctx, "stage_event_name"):
            return [
                dispatcher.observe_event(
                    observer_name=f"{EXT_ID}:stage_{int(t)}",
                    event_name=ctx.stage_event_name(t),
                    on_event=self._on_stage_event,
                )
                for t in types
            ]
        wanted = {int(t) for t in types}
        return [
            ctx.get_stage_event_stream().create_subscription_to_pop(
                lambda e: self._on_stage_event(e) if e.type in wanted else None, name=f"{EXT_ID}:stage"
            )
        ]

    def _on_stage_event(self, event):
        if self._controller is not None:
            self._controller._on_stage_opened(event)

    def _on_focus_changed(self, event):
        if self._controller is not None:
            self._controller._on_focus_changed(event)

    def _on_client_connected(self, event):
        if self._controller is None:
            return
        try:
            self._controller._on_client_connected(event)
        except ValueError as e:  # 방어: 송신 검증 실패가 이벤트 디스패치로 새지 않게
            carb.log_warn(f"[{EXT_ID}] cannot send state on client_connected: {e}")
