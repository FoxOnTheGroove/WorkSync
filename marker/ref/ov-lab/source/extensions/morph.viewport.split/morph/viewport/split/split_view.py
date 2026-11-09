"""공개 `SplitView`. 논리 상태(core)와 창 어댑터를 업데이트 루프 하나로 엮는다."""

from __future__ import annotations

from typing import Callable, Sequence

import carb
import carb.eventdispatcher
import omni.kit.app
import omni.usd
from morph.perf.monitor import counter, gauge, scope

from .adapters import read_side_ratio
from .cameras import CameraInfo, list_camera_prims
from .core import ViewInfo, SplitCore
from .layout import LAYOUTS, check_side_ratio
from .rect import rects_differ

__all__ = ["SplitView"]

# 전환 중이 아닐 때 뷰 rect(웹 state)를 이 프레임 간격으로 확인해, 바뀌었으면 state를 다시 보낸다.
RECT_CHECK_FRAMES = 30
# 레이아웃 전환 완료 직후에는 도킹 크기가 아직 자리 잡는 중일 수 있어 이만큼 뒤에 한 번 더 확인한다.
RECT_SETTLE_FRAMES = 5

METRIC_LAYOUT_SWITCHES = "morph.viewport.split.layout_switches"
METRIC_LAYOUT_STEP_MS = "morph.viewport.split.layout_step_ms"
METRIC_VIEW_COUNT = "morph.viewport.split.view_count"
METRIC_LIST_CAMERAS_MS = "morph.viewport.split.list_cameras_ms"

SIDE_RATIO_SETTING = "/exts/morph.viewport.split/sideRatio"
# sideRatio가 배치에 쓰이는 레이아웃
SIDE_LAYOUT = "1+3"

EmitFn = Callable[[str, dict], None]
SettingGet = Callable[[str], object]
SettingSet = Callable[[str, object], None]


def _same_setting(a: object, b: object) -> bool:
    """설정 원시값이 같은지 (타입까지. NaN끼리는 같다고 본다 — 매 프레임 다시 해석하지 않게)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and a != a and b != b:
        return True
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001
        return False


def _dispatch(event_name: str, payload: dict) -> None:
    carb.eventdispatcher.get_eventdispatcher().dispatch_event(event_name, payload=payload)


def _context_stage(usd_context_name: str):
    ctx = omni.usd.get_context(usd_context_name)
    return ctx.get_stage() if ctx else None


def _log_error(message: str) -> None:
    # 테스트가 의도적 실패를 carb error로 남기지 않도록 모듈 함수로 둔다 (patch 대상).
    carb.log_error(message)


class SplitView:
    """분할화면 상태. 모든 메서드는 메인 스레드 전용.

    생성자 인자는 내부용이다 (다른 익스텐션은 `get_split_view()`로 싱글턴을 얻는다).
    """

    def __init__(
        self,
        adapter,
        *,
        default_cameras: Sequence[str] = (),
        focus_on_click: bool = True,
        emit: EmitFn | None = None,
        state_sink: Callable[[dict], None] | None = None,
        prim_exists: Callable[[str], bool | None] | None = None,
        adapter_probe: Callable[[], object] | None = None,
        stage_provider: Callable[[str], object] | None = None,
        side_ratio_setting: tuple[SettingGet, SettingSet] | None = None,
        subscribe: bool = False,
    ):
        self._adapter = adapter
        self._focus_on_click = bool(focus_on_click)
        self._emit = emit or _dispatch
        self._state_sink = state_sink
        self._prim_exists = prim_exists
        self._adapter_probe = adapter_probe
        # UsdContext 이름 -> 열린 스테이지(없으면 None). 카메라 목록용 (테스트가 주입).
        self._stage_provider = stage_provider or _context_stage
        self._probe_countdown = 0
        self._stage_reset_pending = False
        self._closed = False
        self._last_view_count: int | None = None
        self._camera0_set_while_headless = False

        # sideRatio ("1+3" 오른쪽 열 폭). 기준은 이 값이고 설정과 양방향으로 맞춘다:
        # set_side_ratio는 설정에도 쓰고, 설정이 직접 바뀌면 다음 조회·업데이트 때 이 값으로 받는다.
        # side_ratio_setting이 없으면(테스트) 설정 없이 기본값에서 시작한다.
        self._side_get, self._side_set = side_ratio_setting or (None, None)
        self._side_raw: object = None
        self._side_ratio = read_side_ratio(lambda _path: None)
        self._poll_side_setting()
        self._layout_side: float | None = None  # 마지막 "1+3" begin_layout에 넘긴 값 (재도킹 필요 판단)
        self._side_in_state: float = self._side_ratio  # 마지막 state payload에 담은 값 (시작만으로는 푸시 안 함)
        self._relayout_done = False  # sideRatio 재도킹이 이번 프레임에 끝남 → state 푸시

        camera0 = None if adapter.headless else adapter.read_camera(0)
        name0 = adapter.names(1)[0] if not adapter.headless else ""
        self._core = SplitCore(default_cameras, camera0=camera0, name0=name0)
        self._startup_camera0 = camera0
        self._rect_countdown = 0
        self._rect_warned = False
        # 마지막으로 웹 state에 담은 rect (변경 감지 기준). 시작 값은 지금 상태 — 시작만으로는 푸시하지 않는다.
        self._rects: list[dict | None] = self._read_rects()

        self._subs = []
        if subscribe:
            dispatcher = carb.eventdispatcher.get_eventdispatcher()
            self._subs.append(
                dispatcher.observe_event(
                    event_name=omni.kit.app.GLOBAL_EVENT_UPDATE,
                    on_event=lambda _e: self._tick(),
                    observer_name="morph.viewport.split update",
                )
            )
            usd_context = omni.usd.get_context()
            if usd_context is not None:
                self._subs.append(
                    dispatcher.observe_event(
                        event_name=usd_context.stage_event_name(omni.usd.StageEventType.OPENED),
                        on_event=lambda _e: self._on_stage_opened(),
                        observer_name="morph.viewport.split stage opened",
                    )
                )

    # ================================================================ 공개 API
    @property
    def is_headless(self) -> bool:
        """논리 전용 모드 여부 (Kit 기본 뷰포트 창이 없음)."""
        return bool(self._adapter.headless)

    def set_layout(self, layout: str) -> None:
        """레이아웃 전환. 논리 상태는 즉시 바뀌고 창 준비 후 `layout_changed`가 발행된다."""
        if not isinstance(layout, str) or layout not in LAYOUTS:
            raise ValueError(f"unsupported layout {layout!r}; expected one of {LAYOUTS}")
        self._ensure_open()
        if self._core.set_layout(layout):
            self._begin_adapter_layout()

    def get_layout(self) -> str:
        return self._core.layout

    def list_views(self) -> list[ViewInfo]:
        return self._core.list_views()

    def set_view_camera(self, view_id: int, camera_path: str) -> None:
        """뷰에 카메라 prim 연결. 창이 아직 없으면 저장했다가 창이 생길 때 적용한다."""
        self._core.check_view_id(view_id)
        if not isinstance(camera_path, str) or not camera_path:
            raise ValueError(f"invalid camera path {camera_path!r}")
        self._ensure_open()
        if camera_path == self._core.camera_of(view_id):
            return
        self._adapter.validate_camera(camera_path)
        actual = self._adapter.write_camera(view_id, camera_path)
        if actual is not None and actual != camera_path:
            # 검증을 통과했는데 뷰포트가 받지 않은 경우 (드묾). 실제 값이 기준이다.
            raise ValueError(f"viewport rejected camera {camera_path!r}")
        self._core.set_camera(view_id, camera_path)
        if view_id == 0 and self._adapter.headless:
            self._camera0_set_while_headless = True

    def focus_view(self, view_id: int) -> None:
        """포커스 뷰 변경 (Kit 활성 뷰포트도 바꾼다). 창이 아직 없으면 생길 때 포커스를 준다."""
        self._core.check_view_id(view_id)
        self._ensure_open()
        changed = self._core.set_focus(view_id)
        if self._core.transitioning:
            return  # 전환 완료 때 포커스 뷰에 포커스를 준다
        if changed or not self._kit_active_is(view_id):
            # 같은 view_id라도 Kit 활성 뷰포트가 그 창이 아니면(외부 뷰포트 창 활성화 등) 다시 맞춘다.
            # 논리 포커스는 그대로이므로 이 경우 focus_changed는 없다.
            self._apply_focus(view_id)

    def get_focused_view(self) -> ViewInfo:
        return self._core.list_views()[self._core.focused]

    def set_side_ratio(self, ratio: float) -> float:
        """"1+3" 오른쪽 열 폭(창 폭 대비)을 0.1..0.5로 잘라 적용하고 적용값을 돌려준다.

        숫자가 아니거나(bool 포함) 유한하지 않으면 `ValueError`. 설정 `sideRatio`에도 쓴다.
        현재 "1+3"(창 모드)이면 다음 업데이트에 창을 다시 만들지 않고 재도킹한다 (전환 중이면 끝난 뒤).
        """
        applied = check_side_ratio(ratio)
        self._ensure_open()
        if self._side_set is not None:
            self._side_set(SIDE_RATIO_SETTING, applied)
            self._side_raw = applied
        self._side_ratio = applied
        return applied

    def get_side_ratio(self) -> float:
        """현재 유효한 "1+3" 오른쪽 열 폭 (잘린 값). 설정을 직접 바꿨으면 그 값을 해석한 결과."""
        self._poll_side_setting()
        return self._side_ratio

    def list_cameras(self) -> list[CameraInfo]:
        """고를 수 있는 카메라 prim 목록 (사용자 카메라 경로순, 그다음 Kit 기본 카메라). 스테이지가 없으면 []."""
        cameras = self._list_cameras_or_none()
        return [] if cameras is None else cameras

    # ================================================================ 내부
    def _begin_adapter_layout(self) -> None:
        """현재 논리 레이아웃으로 어댑터 전환 시작. sideRatio는 항상 지금 값을 넘긴다."""
        layout = self._core.layout
        self._layout_side = self._side_ratio if layout == SIDE_LAYOUT else None
        self._adapter.begin_layout(layout, self._core.camera_of, side_ratio=self._side_ratio)

    def _poll_side_setting(self) -> None:
        """설정 `sideRatio`가 직접 바뀌었으면 받아들인다 (해석 규칙은 설정 표: 잘못된 값은 기본값 + 경고)."""
        if self._side_get is None:
            return
        try:
            raw = self._side_get(SIDE_RATIO_SETTING)
        except Exception:  # noqa: BLE001 - 설정을 못 읽으면 지금 값 유지
            return
        if _same_setting(raw, self._side_raw):
            return
        self._side_raw = raw
        self._side_ratio = read_side_ratio(lambda _path: raw)

    def _maybe_relayout_side(self) -> None:
        """"1+3"(창 모드)인데 배치에 쓴 sideRatio가 지금 값과 다르면 재도킹 시작 (창·카메라·포커스 유지)."""
        if self._adapter.headless or self._core.layout != SIDE_LAYOUT or self._layout_side == self._side_ratio:
            return
        if self._core.begin_relayout():
            self._begin_adapter_layout()

    def _list_cameras_or_none(self) -> list[CameraInfo] | None:
        """열린 스테이지가 없으면 None (웹 `failed_precondition`), 있으면 목록. 호출할 때마다 순회한다."""
        stage = self._stage_provider(getattr(self._adapter, "usd_context_name", "") or "")
        if stage is None:
            return None
        with scope(METRIC_LIST_CAMERAS_MS):
            return list_camera_prims(stage)

    def _state_payload(self) -> dict:
        """웹 `morph.viewport.split.state` 페이로드 (내부용). 이벤트 페이로드 + 뷰마다 `rect`.

        담은 rect를 변경 감지 기준으로 기억한다.
        """
        payload = self._core.state_payload()
        rects = self._read_rects()
        self._rects = rects
        for view, rect in zip(payload["views"], rects):
            view["rect"] = rect
        self._poll_side_setting()
        payload["side_ratio"] = self._side_ratio
        self._side_in_state = self._side_ratio
        return payload

    def _read_rects(self) -> list[dict | None]:
        """현재 뷰들의 화면 영역. 실패하거나 모양이 이상하면 모두 None (경고 1회)."""
        n = self._core.count
        try:
            rects = list(self._adapter.read_rects(n))
            if len(rects) != n or not all(r is None or isinstance(r, dict) for r in rects):
                raise ValueError(f"adapter returned {len(rects)} rects for {n} views")
            return rects
        except Exception as exc:  # noqa: BLE001 - rect를 몰라도 나머지 상태는 보낸다
            if not self._rect_warned:
                self._rect_warned = True
                carb.log_warn(f"[morph.viewport.split] reading view rects failed: {exc!r}")
            return [None] * n

    def _rects_changed(self) -> bool:
        """전환 중이 아닐 때 RECT_CHECK_FRAMES마다 rect를 다시 읽어 마지막으로 보낸 값과 비교."""
        if self._core.transitioning:
            return False
        if self._rect_countdown > 0:
            self._rect_countdown -= 1
            return False
        self._rect_countdown = RECT_CHECK_FRAMES
        return rects_differ(self._rects, self._read_rects())

    def _kit_active_is(self, view_id: int) -> bool:
        """Kit 활성 뷰포트가 이 뷰의 창인지. 논리 전용 모드는 항상 True (맞출 창이 없음)."""
        if self._adapter.headless:
            return True
        try:
            return self._adapter.read_active_view() == view_id
        except Exception as exc:  # noqa: BLE001 - 판단 불가면 다시 포커스를 준다
            carb.log_warn(f"[morph.viewport.split] reading Kit active viewport failed: {exc!r}")
            return False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("morph.viewport.split is shut down")

    def _on_stage_opened(self) -> None:
        # 뷰포트도 같은 이벤트에서 세션 카메라를 만든다. 순서를 피하려고 다음 업데이트에 적용한다.
        self._stage_reset_pending = True

    def _tick(self) -> None:
        """업데이트 프레임마다 한 번."""
        if self._closed:
            return
        self._maybe_attach_windows()  # 어댑터가 바뀔 수 있으므로 먼저
        core, adapter = self._core, self._adapter
        self._poll_side_setting()

        if self._stage_reset_pending:
            self._stage_reset_pending = False
            for view_id in range(core.count):
                self._reset_camera(view_id)

        if core.transitioning:
            with scope(METRIC_LAYOUT_STEP_MS):
                # 전환 중에도 창이 유지되는 기존 뷰는 외부 카메라 변경을 계속 감지한다.
                self._detect_camera_changes(core.stable_count)
                try:
                    if adapter.step():
                        self._complete_transition()
                except Exception as exc:  # noqa: BLE001 - 정책: error 1회, 추가 창 정리 후 1x1
                    self._fail_transition(exc)
        else:
            self._detect_external_changes()
            if not core.transitioning:
                self._maybe_relayout_side()  # 슬라이더 등으로 여러 번 바뀌었어도 지금 값으로 한 번
            if not core.transitioning:  # 위에서 전환(창 닫힘·재도킹)이 시작됐으면 완료 때 적용
                try:
                    adapter.refresh_style()
                except Exception as exc:  # noqa: BLE001 - 꾸밈 실패가 업데이트 루프를 막지 않게
                    carb.log_warn(f"[morph.viewport.split] refreshing view window style failed: {exc!r}")

        events = core.pop_events()
        for name, payload in events:
            try:
                self._emit(name, payload)
            except Exception as exc:  # noqa: BLE001 - 관찰자 예외가 상태 갱신을 막지 않게
                _log_error(f"[morph.viewport.split] event observer failed for {name}: {exc}")
        # 이벤트가 있었거나, sideRatio가 바뀌었거나(재도킹·레이아웃 전환 중이면 끝난 프레임에 — 전환 중 rect는
        # 모두 null이라 보내지 않는다), 분할선 드래그·앱 창 크기 변경 등으로 뷰 rect가 바뀌었으면 state 1회 (병합).
        push = (
            bool(events)
            or self._relayout_done
            or (self._side_ratio != self._side_in_state and not core.transitioning)
        )
        self._relayout_done = False
        if self._state_sink is not None and (push or self._rects_changed()):
            try:
                self._state_sink(self._state_payload())
            except Exception as exc:  # noqa: BLE001 - 웹 송신 실패가 업데이트 루프를 막지 않게
                carb.log_warn(f"[morph.viewport.split] sending state to web failed: {exc!r}")

        # perf 모니터는 주기 내 마지막 값을 유지하므로 값이 바뀔 때만 기록한다.
        if core.count != self._last_view_count:
            self._last_view_count = core.count
            gauge(METRIC_VIEW_COUNT, float(core.count))

    def _complete_transition(self) -> None:
        core, adapter = self._core, self._adapter
        n = core.count
        stable = core.stable_count
        relayout = core.relayout
        cameras: list[str | None] = []
        for view_id in range(n):
            current = adapter.read_camera(view_id) if view_id < stable else None
            if current is not None:
                # 기존 뷰: 뷰포트의 실제 값이 기준 (전환 중 외부 변경을 덮어쓰지 않는다)
                if current != core.camera_of(view_id):
                    core.set_camera(view_id, current)
                cameras.append(current)
            else:
                # 새 뷰(또는 창이 막 생긴 뷰): 저장된 값을 적용
                cameras.append(adapter.write_camera(view_id, core.camera_of(view_id)))
        core.complete_transition(adapter.names(n), cameras)
        # 정책: 전환 중 Kit 활성 뷰포트 변경(클릭, 창 생성·도킹에 따른 포커스)은 반영하지 않고 포커스 뷰를 다시 준다.
        self._apply_focus(core.focused)
        self._rect_countdown = RECT_SETTLE_FRAMES  # 도킹 크기가 자리 잡은 뒤 rect 재확인
        if relayout:
            self._relayout_done = True  # layout_changed는 없지만 rect·side_ratio가 바뀌었으므로 state 푸시
        else:
            counter(METRIC_LAYOUT_SWITCHES)

    def _apply_focus(self, view_id: int) -> None:
        """Kit 활성 뷰포트를 포커스 뷰로. 실패해도 논리 상태·레이아웃은 유지한다 (error 로그)."""
        try:
            self._adapter.focus(view_id)
        except Exception as exc:  # noqa: BLE001
            _log_error(f"[morph.viewport.split] failed to focus view {view_id}: {exc!r}")

    def _fail_transition(self, exc: BaseException) -> None:
        """창 생성·도킹·완료 처리 실패. error 로그 1회, 추가 창을 정리하고 1x1로 돌아가 layout_changed.

        같은 에러가 매 프레임 반복되지 않도록 재시도하지 않는다. 도킹되지 않은 레이아웃을
        완료된 것처럼 알리지 않는다.
        """
        core, adapter = self._core, self._adapter
        _log_error(
            f"[morph.viewport.split] layout {core.layout!r} failed, falling back to 1x1: {exc!r}"
        )
        try:
            adapter.abort()
        except Exception as abort_exc:  # noqa: BLE001
            carb.log_warn(f"[morph.viewport.split] cleanup after failure raised: {abort_exc!r}")
        core.abort_to_1x1()
        try:
            self._complete_transition()
        except Exception as again:  # noqa: BLE001 - 1x1 완료도 실패하면 논리 상태만 끝낸다
            carb.log_warn(f"[morph.viewport.split] completing 1x1 fallback raised: {again!r}")
            if core.transitioning:
                core.complete_transition([], [])

    def _detect_camera_changes(self, limit: int) -> None:
        """뷰포트 API에서 카메라가 바뀜 (set_active_camera, Kit UI 등). view 0..limit-1."""
        core, adapter = self._core, self._adapter
        for view_id in range(min(limit, core.count)):
            current = adapter.read_camera(view_id)
            if current is not None and current != core.camera_of(view_id):
                core.set_camera(view_id, current)

    def _detect_external_changes(self) -> None:
        core, adapter = self._core, self._adapter

        # 1) 사용자가 추가 창을 닫음 → 1x1
        if adapter.closed_views():
            if core.set_layout("1x1"):
                self._begin_adapter_layout()
            return

        # 2) 외부 카메라 변경
        self._detect_camera_changes(core.count)

        # 3) Kit 활성 뷰포트가 바뀜 (창 클릭 등). 이 익스텐션이 만들지 않은 뷰포트 창이 활성이면 None → 무시.
        active = adapter.read_active_view()
        if active is not None and active != core.focused:
            if self._focus_on_click:
                core.set_focus(active)
            else:
                self._apply_focus(core.focused)  # 포커스 유지: Kit 활성 뷰포트를 되돌린다

        # 4) 연결된 카메라 prim 삭제 → 그 뷰만 기본값
        if self._prim_exists is not None:
            for view_id in core.views_with_deleted_camera(self._prim_exists):
                self._reset_camera(view_id)

    def _reset_camera(self, view_id: int) -> None:
        core = self._core
        desired = core.default_camera(view_id)
        actual = self._adapter.write_camera(view_id, desired)
        core.set_camera(view_id, actual if actual else desired)

    def _maybe_attach_windows(self) -> None:
        """논리 전용으로 시작했는데 나중에 기본 뷰포트 창이 생기면 창 모드로 바꾼다."""
        if self._adapter_probe is None or not self._adapter.headless or self._core.transitioning:
            return
        if self._probe_countdown > 0:
            self._probe_countdown -= 1
            return
        self._probe_countdown = 30
        adapter = self._adapter_probe()
        if adapter is None:
            return
        self._adapter = adapter
        self._adapter_probe = None
        core = self._core
        kit_camera0 = adapter.read_camera(0)
        self._startup_camera0 = kit_camera0
        if self._camera0_set_while_headless:
            # 헤드리스 중 명시적으로 연결한 view 0 카메라는 창에 적용한다.
            actual = adapter.write_camera(0, core.camera_of(0))
            if actual is not None and actual != core.camera_of(0):
                core.set_camera(0, actual, emit=False)
        elif kit_camera0 is not None:
            # 그렇지 않으면 Kit 쪽 카메라를 덮어쓰지 않고 채택한다 (layout_changed의 views에 담긴다).
            core.set_camera(0, kit_camera0, emit=False)
        # view 0 창 이름을 채우고, 현재 레이아웃을 실제 창으로 다시 만든다 (완료 시 layout_changed).
        core.restart_transition(adapter.names(1)[0])
        self._begin_adapter_layout()

    def _shutdown(self) -> None:
        """이벤트 없이 정리: 구독 해제, 추가 창 닫기, view 0 카메라 복원."""
        if self._closed:
            return
        self._closed = True
        self._subs = []
        self._core.clear_events()
        try:
            self._adapter.shutdown(self._startup_camera0)
        finally:
            self._state_sink = None
