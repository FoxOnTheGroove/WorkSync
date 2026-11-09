"""morph.viewport.split 익스텐션 진입점과 싱글턴."""

from __future__ import annotations

import carb
import carb.eventdispatcher
import carb.settings
import omni.ext
from morph.stream.messaging import get_messaging

from .adapters import HeadlessAdapter, find_kit_adapter, prim_exists
from .layout import LAYOUTS
from .split_view import SplitView
from .style import read_view_style
from .web import EXT_ID, MSG_STATE, WebBridge

__all__ = ["get_split_view", "SplitViewExtension"]

_SETTINGS_ROOT = "/exts/morph.viewport.split"
_EVENT_CLIENT_CONNECTED = "morph.stream.client_connected"

_instance: SplitView | None = None


def get_split_view() -> SplitView | None:
    """싱글턴. 이 익스텐션 startup 이후 유효, shutdown 이후 None."""
    return _instance


def _send_state(payload: dict) -> None:
    """웹으로 상태 송신. 송신 예외(ValueError 등)는 경고로만 남긴다 (업데이트 루프·이벤트 디스패치로 새지 않게)."""
    try:
        msg = get_messaging()
        if msg is None or not msg.is_connected:
            return
        msg.send(MSG_STATE, payload)
    except Exception as exc:  # noqa: BLE001
        carb.log_warn(f"[{EXT_ID}] sending {MSG_STATE} failed: {exc!r}")


def _read_settings(get=None) -> tuple[str, bool, list[str]]:
    """startup 설정 (defaultLayout, focusOnClick, defaultCameras). `get`은 테스트용 (기본 carb settings).

    sideRatio는 여기서 읽지 않는다 (SplitView가 설정과 양방향으로 맞춘다: `side_ratio_setting`).
    """
    if get is None:
        get = carb.settings.get_settings().get
    layout = get(f"{_SETTINGS_ROOT}/defaultLayout") or "1x1"
    if layout not in LAYOUTS:
        carb.log_warn(f"[{EXT_ID}] defaultLayout {layout!r} is not one of {LAYOUTS}; using 1x1")
        layout = "1x1"
    focus_on_click = get(f"{_SETTINGS_ROOT}/focusOnClick")
    focus_on_click = True if focus_on_click is None else bool(focus_on_click)
    cameras = get(f"{_SETTINGS_ROOT}/defaultCameras") or []
    if isinstance(cameras, str):
        cameras = [cameras]
    cameras = [c for c in cameras if isinstance(c, str)]
    return layout, focus_on_click, cameras


class SplitViewExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        global _instance
        self._subs = []
        self._conn_sub = None

        layout, focus_on_click, cameras = _read_settings()
        settings = carb.settings.get_settings()
        style = read_view_style(settings.get, _SETTINGS_ROOT)
        adapter = find_kit_adapter(style=style)
        view = SplitView(
            adapter if adapter is not None else HeadlessAdapter(),
            default_cameras=cameras,
            focus_on_click=focus_on_click,
            state_sink=_send_state,
            prim_exists=prim_exists,
            adapter_probe=(lambda: find_kit_adapter(style=style)) if adapter is None else None,
            side_ratio_setting=(settings.get, settings.set),
            subscribe=True,
        )
        _instance = view

        try:
            msg = get_messaging()
            if msg is not None:
                self._subs = WebBridge(get_split_view).register(msg)
            else:
                carb.log_warn(f"[{EXT_ID}] morph.stream.messaging is not available; web messages disabled")

            self._conn_sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
                event_name=_EVENT_CLIENT_CONNECTED,
                on_event=self._on_client_connected,
                observer_name=f"{EXT_ID} client_connected",
            )

            if layout != "1x1":
                view.set_layout(layout)
        except Exception:
            # startup 실패: 반쯤 만든 상태를 되돌린다 (싱글턴 None, 구독 해제, 창 정리)
            self._conn_sub = None
            for sub in self._subs:
                sub.unsubscribe()
            self._subs = []
            _instance = None
            view._shutdown()  # noqa: SLF001 - 같은 패키지
            raise

    def _on_client_connected(self, _event) -> None:
        view = get_split_view()
        if view is None:
            return
        try:
            msg = get_messaging()
            if msg is None:
                return
            msg.send(MSG_STATE, view._state_payload())  # noqa: SLF001 - 같은 패키지
        except Exception as exc:  # noqa: BLE001 - 이벤트 디스패치로 새지 않게
            carb.log_warn(f"[{EXT_ID}] sending {MSG_STATE} on client_connected failed: {exc!r}")

    def on_shutdown(self):
        global _instance
        self._conn_sub = None
        for sub in self._subs:
            sub.unsubscribe()
        self._subs = []
        view, _instance = _instance, None
        if view is not None:
            view._shutdown()  # noqa: SLF001 - 같은 패키지
