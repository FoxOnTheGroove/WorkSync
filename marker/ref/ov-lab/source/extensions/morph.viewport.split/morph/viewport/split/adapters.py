"""창 의존 부분. 논리 상태(`core.SplitCore`)는 여기 의존하지 않는다.

어댑터 인터페이스 (덕 타이핑, 테스트의 가짜 어댑터도 같은 모양):
- `headless: bool`
- `begin_layout(layout, camera_of, side_ratio=None)` : 전환 시작(또는 전환 중 목표 교체). `camera_of(view_id) -> str`.
  `side_ratio`는 "1+3" 오른쪽 열 폭 (SplitView가 항상 넘긴다. None이면 어댑터 기본 출처). 같은 레이아웃으로
  다시 불러도 된다 (sideRatio 변경 재도킹: 창은 그대로, 배치가 바뀐 창만 다시 도킹)
- `step() -> bool` : 업데이트 프레임마다 한 단계. 창 준비가 끝나면 True
- `abort()` : 전환 실패 시 진행 중 단계를 버리고 추가 창을 모두 정리 (view 0은 그대로)
- `names(n) -> list[str]` : view 0..n-1 창 이름 (없으면 "")
- `read_camera(view_id) -> str | None` : 뷰포트 API의 camera_path (창 없으면 None)
- `write_camera(view_id, path) -> str | None` : 쓰고 실제 반영된 값을 돌려준다 (창 없으면 None)
- `read_active_view() -> int | None` : Kit 활성 뷰포트가 우리 뷰면 그 view_id
- `focus(view_id)` : Kit 활성 뷰포트를 해당 뷰로
- `closed_views() -> list[int]` : 사용자가 닫은 추가 뷰
- `validate_camera(path)` : 쓸 수 없는 카메라면 ValueError / RuntimeError
- `refresh_style()` : 전환 중이 아닌 업데이트 프레임마다. 뷰 창 꾸밈(탭바·여백·분할선) 유지. 예외를 던지지 않는다
- `read_rects(n) -> list[dict | None]` : view 0..n-1 화면 영역 (앱 메인 창 기준 0..1, `rect.normalize_rect`).
  모르면 None (논리 전용, 창 없음, 전환 중)
- `shutdown(camera0)` : 추가 창 정리, view 0 카메라·꾸밈 복원
- `usd_context_name: str` (선택, 없으면 "") : 카메라 검증·목록에 쓰는 UsdContext 이름
"""

from __future__ import annotations

import importlib
import time
import weakref
from typing import Callable

import carb
import carb.settings
import omni.kit.viewport.utility as vp_util
import omni.ui as ui
import omni.usd
from pxr import Sdf, UsdGeom

from .layout import dock_plan, side_ratio_from_setting, view_count
from .rect import normalize_rect
from .style import DockSplitter, ViewStyle, apply_window_attrs, window_attrs

__all__ = [
    "HeadlessAdapter",
    "KitWindowAdapter",
    "find_kit_adapter",
    "validate_camera_prim",
    "prim_exists",
    "context_stage",
    "default_window_name",
    "main_window_size",
    "read_side_ratio",
]

_WINDOW_NAME_SETTING = "/exts/omni.kit.viewport.window/startup/windowName"
_DOCK_TAB_INVISIBLE_SETTING = "/exts/omni.kit.viewport.window/startup/dockTabInvisible"
_SIDE_RATIO_SETTING = "/exts/morph.viewport.split/sideRatio"
EXTRA_WINDOW_PREFIX = "MORPH Viewport"


def _style_warn(message: str) -> None:
    # 꾸밈 적용 실패 경고. 테스트가 patch로 가로챈다.
    carb.log_warn(message)


# ---------------------------------------------------------------------- USD 헬퍼
def validate_camera_prim(stage, camera_path: str) -> None:
    """열린 스테이지가 없으면 RuntimeError, prim이 없거나 Camera가 아니면 ValueError."""
    if stage is None:
        raise RuntimeError("no open stage")
    if not isinstance(camera_path, str) or not Sdf.Path.IsValidPathString(camera_path):
        raise ValueError(f"invalid camera path {camera_path!r}")
    path = Sdf.Path(camera_path)
    if not path.IsAbsolutePath() or not path.IsPrimPath():
        raise ValueError(f"invalid camera path {camera_path!r}")
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        raise ValueError(f"no prim at {camera_path!r}")
    if not prim.IsA(UsdGeom.Camera):
        raise ValueError(f"prim {camera_path!r} is not a Camera")


def prim_exists(camera_path: str, usd_context_name: str = "") -> bool | None:
    """기본(또는 지정) UsdContext 스테이지에 prim이 있는지. 스테이지가 없거나 경로가 이상하면 None."""
    ctx = omni.usd.get_context(usd_context_name)
    stage = ctx.get_stage() if ctx else None
    if stage is None or not camera_path or not Sdf.Path.IsValidPathString(camera_path):
        return None
    path = Sdf.Path(camera_path)
    if not path.IsAbsolutePath() or not path.IsPrimPath():
        return None
    prim = stage.GetPrimAtPath(path)
    return bool(prim and prim.IsValid())


def context_stage(usd_context_name: str = ""):
    """기본(또는 지정) UsdContext의 열린 스테이지. 없으면 None."""
    ctx = omni.usd.get_context(usd_context_name)
    return ctx.get_stage() if ctx else None


def default_window_name() -> str:
    return carb.settings.get_settings().get(_WINDOW_NAME_SETTING) or "Viewport"


def main_window_size() -> tuple[float, float] | None:
    """앱 메인 창(omni.appwindow 기본 창) 크기를 omni.ui 창 좌표 단위로. 모르면 None.

    omni.ui 창의 position_x/width 등은 DPI 배율로 나눈 단위(포인트)이고, app window 크기는 픽셀이다.
    둘을 맞추려고 픽셀을 `ui.Workspace.get_dpi_scale()`로 나눈다. app window를 얻지 못하면
    `ui.Workspace.get_main_window_width/height()`(이미 포인트)를 쓴다.
    """
    try:
        dpi = float(ui.Workspace.get_dpi_scale() or 1.0)
    except Exception:  # noqa: BLE001
        dpi = 1.0
    if dpi <= 0:
        dpi = 1.0
    try:
        # Kit 코어 모듈. 없을 수 있는 환경에 대비해 여기서 import (sys.modules 기준이라 테스트가 바꿔 넣을 수 있음)
        app_window = importlib.import_module("omni.appwindow").get_default_app_window()
        if app_window is not None:
            w, h = float(app_window.get_width()), float(app_window.get_height())
            if w > 0 and h > 0:
                return w / dpi, h / dpi
    except Exception:  # noqa: BLE001 - 아래 대안으로
        pass
    try:
        w, h = float(ui.Workspace.get_main_window_width()), float(ui.Workspace.get_main_window_height())
    except Exception:  # noqa: BLE001
        return None
    return (w, h) if w > 0 and h > 0 else None


_side_ratio_warned: set[str] = set()


def read_side_ratio(get: Callable[[str], object] | None = None) -> float:
    """설정 `sideRatio`("1+3" 오른쪽 열 폭)를 읽는다. 레이아웃 전환 시작 때마다 부른다.

    숫자 아님 → 기본값, 범위 밖 → 0.1..0.5로 자름. 경고는 같은 문구당 1회 (전환마다 반복하지 않게).
    """
    if get is None:
        get = carb.settings.get_settings().get
    try:
        value = get(_SIDE_RATIO_SETTING)
    except Exception:  # noqa: BLE001 - 설정을 못 읽으면 기본값
        value = None
    ratio, warning = side_ratio_from_setting(value)
    if warning and warning not in _side_ratio_warned:
        _side_ratio_warned.add(warning)
        carb.log_warn(f"[morph.viewport.split] {warning}")
    return ratio


_vp_window_import_failed = False


def _viewport_window_module():
    """선택 의존. 없으면 None. 실패는 캐시한다 (헤드리스 probe가 주기적으로 부르므로)."""
    global _vp_window_import_failed
    if _vp_window_import_failed:
        return None
    try:
        import omni.kit.viewport.window as vp_window_mod  # noqa: PLC0415
    except ImportError:
        _vp_window_import_failed = True
        return None
    return vp_window_mod


# ---------------------------------------------------------------------- 논리 전용
class HeadlessAdapter:
    """Kit 기본 뷰포트 창이 없을 때. 창·카메라는 바꾸지 않고 다음 프레임에 전환을 끝낸다."""

    headless = True
    usd_context_name = ""

    def begin_layout(self, layout: str, camera_of: Callable[[int], str], side_ratio: float | None = None) -> None:
        pass

    def step(self) -> bool:
        return True

    def abort(self) -> None:
        pass

    def names(self, n: int) -> list[str]:
        return [""] * n

    def read_camera(self, view_id: int) -> str | None:
        return None

    def write_camera(self, view_id: int, camera_path: str) -> str | None:
        return None

    def read_active_view(self) -> int | None:
        return None

    def focus(self, view_id: int) -> None:
        pass

    def closed_views(self) -> list[int]:
        return []

    def validate_camera(self, camera_path: str) -> None:
        pass  # CONTRACT: 논리 전용 모드에서는 prim 검사 없이 저장만

    def refresh_style(self) -> None:
        pass  # 꾸밀 창이 없다 (창 모드로 바뀌면 KitWindowAdapter가 적용)

    def read_rects(self, n: int) -> list[dict | None]:
        return [None] * n  # CONTRACT: 논리 전용 모드는 rect null

    def shutdown(self, camera0: str | None) -> None:
        pass


# ---------------------------------------------------------------------- Kit 창
def find_kit_adapter(style: ViewStyle | None = None) -> "KitWindowAdapter | None":
    """Kit 기본 뷰포트 창이 있으면 그 창을 view 0으로 쓰는 어댑터."""
    mod = _viewport_window_module()
    if mod is None:
        return None
    name = default_window_name()
    try:
        for window in mod.get_viewport_window_instances(None):
            if window.title == name and window.viewport_api is not None:
                return KitWindowAdapter(window, style=style)
    except ReferenceError:
        return None
    return None


class _RenderGate:
    """기본 뷰포트(view 0)가 렌더한 프레임을 받았는지. 추가 창 생성 전 한 번 통과해야 한다.

    renderer.asyncInit=true 앱에서 렌더러가 준비되기 전에 뷰포트 창을 더 만들면 omni.rtx가
    "Multiple Hydra Engines detected" 후 "CreateCommandList failed"로 크래시한다. 그래서
    `viewport_api.subscribe_to_frame_change`(= `wait_for_rendered_frames`가 쓰는 콜백)로 실제
    프레임 전달을 센다. 구독 직후 동기로 한 번 불리는 콜백은 "텍스처가 있다"는 뜻일 뿐 렌더된
    프레임이 아니므로 세지 않는다. asyncio 태스크를 쓰지 않고 업데이트 루프에서 확인한다.
    """

    def __init__(self, frames: int, timeout_s: float, clock: Callable[[], float]):
        self.frames = frames
        self.timeout_s = timeout_s
        self._clock = clock
        self.ready = False
        self._sub = None
        self._armed = False  # 구독 호출이 끝난 뒤의 콜백만 센다
        self._seen = 0
        self._started: float | None = None

    def _on_frame(self, *_args) -> None:
        if self._armed:
            self._seen += 1

    def poll(self, api) -> bool:
        """준비됐으면 True. 타임아웃이면 대기 상태를 버리고 TimeoutError."""
        if self.ready:
            return True
        if self._started is None:
            self._started = self._clock()
        if self._sub is None and api is not None:
            subscribe = getattr(api, "subscribe_to_frame_change", None)
            if subscribe is None:
                # 프레임 구독이 없는 뷰포트 API: 기다릴 방법이 없으므로 준비된 것으로 본다.
                self.ready = True
                return True
            self._armed = False
            self._seen = 0
            self._sub = subscribe(self._on_frame)
            self._armed = True
        if self._seen >= self.frames:
            self.ready = True
            self.cancel(keep_ready=True)
            return True
        if self._clock() - self._started >= self.timeout_s:
            waited = self._clock() - self._started
            self.cancel()
            raise TimeoutError(
                f"default viewport rendered no frame within {waited:.1f}s; extra viewport windows not created"
            )
        return False

    def cancel(self, keep_ready: bool = False) -> None:
        """대기 중 구독을 버린다. 다음 poll은 처음부터 (시작 시각 포함) 다시 기다린다."""
        sub, self._sub = self._sub, None
        self._armed = False
        self._seen = 0
        if not keep_ready:
            self._started = None
        if sub is not None:
            destroy = getattr(sub, "destroy", None)
            if destroy is not None:
                try:
                    destroy()
                except Exception:  # noqa: BLE001 - 구독 해제 실패는 무시 (GC가 처리)
                    pass


class KitWindowAdapter:
    """view 0 = Kit 기본 뷰포트 창. 추가 뷰(1~)는 `MORPH Viewport <k>` 창을 만들어 도킹한다.

    추가 창은 view_id 슬롯별로 재사용한다 (같은 view_id = 같은 창). 전환 단계:
    prepare(남는 창 정리, 위치가 바뀌는 창 도킹 해제, 만들 창이 있으면 렌더 준비 게이트 대기)
    → create(프레임당 하나) → dock(프레임당 하나) → settle(2프레임).

    꾸밈(`ViewStyle`): 전환이 끝날 때 모든 뷰 창에 적용하고, 전환 중이 아닐 때는 `refresh_style`이
    STYLE_CHECK_FRAMES마다 값을 확인해 달라진 속성만 다시 쓴다 (첫 호출은 즉시 — startup 1x1 포함).
    """

    headless = False
    SETTLE_FRAMES = 2
    RENDER_READY_FRAMES = 3
    RENDER_READY_TIMEOUT_S = 120.0  # RTX 첫 셰이더 컴파일을 고려해 넉넉하게
    STYLE_CHECK_FRAMES = 30

    def __init__(
        self,
        window0,
        *,
        clock: Callable[[], float] = time.monotonic,
        style: ViewStyle | None = None,
        imgui_factory: Callable[[], tuple[object, object]] | None = None,
        main_size: Callable[[], tuple[float, float] | None] | None = None,
        side_ratio: Callable[[], float] | None = None,
    ):
        # 주의: Kit의 get_viewport_window_instances()는 ViewportWindow를 weakref.proxy로 돌려준다.
        # 프록시는 속성 접근·메서드 호출(self 자리)에는 그대로 쓸 수 있지만, pybind 함수의 **인자**로
        # 넘기면 거부되고(dock_in 등), weakref.proxy()로 다시 감쌀 수 없다. 인자로 넘길 때는
        # _as_handle(), active_window 대입은 _as_proxy()를 거친다.
        self._win0 = window0
        api = window0.viewport_api
        self._usd_context_name = api.usd_context_name if api is not None else ""
        self._extras: dict[int, object] = {}
        self._applied: dict[int, tuple[int, str, float]] = {}  # view_id -> 실제 적용된 (기준, 위치, 비율)
        self._phase: str | None = None
        self._target_n = 1
        self._plan: tuple[tuple[int, int, str, float], ...] = ()
        self._to_create: list[int] = []
        self._needs_dock: set[int] = set()
        self._to_dock: list[tuple[int, int, str, float]] = []
        self._side_ratio = side_ratio or read_side_ratio
        self._settle = 0
        self._camera_of: Callable[[int], str] | None = None
        self._render_gate = _RenderGate(self.RENDER_READY_FRAMES, self.RENDER_READY_TIMEOUT_S, clock)
        self._style = style or ViewStyle()
        self._style_attrs = window_attrs(self._style)
        self._splitter = DockSplitter(self._style.splitter_size, imgui_factory)
        self._style_countdown = 0
        self._style_original0: dict[str, object] = {}  # view 0에서 바꾸기 전 값 (shutdown 복원)
        self._style_warned: set[tuple[int, str]] = set()
        self._main_size = main_size or main_window_size

    @property
    def usd_context_name(self) -> str:
        return self._usd_context_name

    @property
    def render_ready(self) -> bool:
        """기본 뷰포트가 렌더한 프레임을 받았는지 (추가 창 생성 가능)."""
        return self._render_gate.ready

    # ------------------------------------------------------------ 창 접근
    def _window(self, view_id: int):
        return self._win0 if view_id == 0 else self._extras.get(view_id)

    @staticmethod
    def _api(window):
        if window is None:
            return None
        try:
            return window.viewport_api
        except ReferenceError:
            return None

    @staticmethod
    def _as_handle(window):
        """pybind 인자로 넘길 수 있는 창 핸들. 프록시면 제목으로 omni.ui 핸들을 다시 얻는다."""
        if isinstance(window, weakref.ProxyTypes):
            handle = ui.Workspace.get_window(window.title)
            if handle is None:
                raise RuntimeError(f"window {window.title!r} not found in omni.ui workspace")
            return handle
        return window

    @staticmethod
    def _as_proxy(window):
        """ViewportWindow.active_window에 넣을 값. 이미 프록시면 그대로."""
        return window if isinstance(window, weakref.ProxyTypes) else weakref.proxy(window)

    def abort(self) -> None:
        """전환 실패 시: 진행 중 단계를 버리고 추가 창을 모두 정리한다 (view 0은 그대로)."""
        self._reset_steps()
        for view_id in sorted(self._extras):
            try:
                self._destroy(view_id)
            except Exception as exc:  # noqa: BLE001 - 정리는 끝까지
                carb.log_warn(f"[morph.viewport.split] failed to destroy view {view_id} window: {exc!r}")
        self._extras.clear()
        self._applied.clear()

    def _reset_steps(self) -> None:
        self._phase = None
        self._to_create = []
        self._needs_dock = set()
        self._to_dock = []
        if not self._render_gate.ready:
            self._render_gate.cancel()

    # ------------------------------------------------------------ 전환
    def begin_layout(self, layout: str, camera_of: Callable[[int], str], side_ratio: float | None = None) -> None:
        self._target_n = view_count(layout)
        # sideRatio: SplitView가 넘긴 값 (1.5.0). 없으면 생성 시 받은 출처(기본: 설정)를 전환 시작 때 읽는다.
        self._plan = dock_plan(layout, self._side_ratio() if side_ratio is None else side_ratio)
        self._camera_of = camera_of
        self._phase = "prepare"

    def step(self) -> bool:
        if self._phase == "prepare":
            self._prepare()
            if self._to_create:
                self._phase = "wait_render"
            else:
                if not self._render_gate.ready:
                    self._render_gate.cancel()  # 만들 창이 없으면 기다리지 않는다 (예: 1x1)
                self._phase = "create"
            return False
        if self._phase == "wait_render":
            # 렌더러 준비 전에는 창을 만들지 않는다. 타임아웃이면 TimeoutError → 창 준비 실패 정책.
            if not self._render_gate.poll(self._api(self._win0)):
                return False
            self._phase = "create"
            return False
        if self._phase == "create":
            if self._to_create:
                self._create(self._to_create.pop(0))  # 프레임당 하나
                return False
            self._to_dock = [e for e in self._plan if e[0] in self._needs_dock and e[0] in self._extras]
            self._needs_dock = set()
            self._phase = "dock"
        if self._phase == "dock":
            if self._to_dock:
                view_id, target_id, pos, ratio = self._to_dock.pop(0)
                window, target = self._window(view_id), self._window(target_id)
                if window is not None and target is not None:
                    # target은 view 0(Kit 프록시)일 수 있으므로 핸들로 바꿔 넘긴다.
                    # ratio = 새로 붙는 창(window)의 몫 (layout.dock_plan 참고)
                    window.dock_in(self._as_handle(target), getattr(ui.DockPosition, pos), ratio)
                    self._applied[view_id] = (target_id, pos, ratio)
                return False
            self._phase = "settle"
            self._settle = self.SETTLE_FRAMES
            return False
        if self._phase == "settle":
            self._settle -= 1
            if self._settle > 0:
                return False
            self._phase = None
            self._apply_style()  # 창이 모두 생기고 도킹된 뒤 (layout_changed 전)
        return True

    def _prepare(self) -> None:
        """남는 창 정리, 남는 창의 위치 확인. 새로 만들 창은 `_to_create`에 모은다 (생성은 create 단계).

        재도킹 규칙: 도킹 계획 순서(view_id 순)로 보면서 (기준, 위치, 비율)이 이미 적용된 값과 같고
        도킹돼 있으면 그대로 둔다. 한 뷰라도 다시 도킹(또는 새로 생성)하면 그 뒤의 뷰는 모두 다시
        도킹한다 — 뒤의 뷰는 앞 뷰가 나눈 영역 안에 붙어 있어서, 앞 뷰를 떼면 자리가 무너진다
        (예: 같은 뷰 개수끼리의 2x2 ↔ 1+3 전환은 view 1의 비율부터 달라 1~3을 모두 다시 도킹).
        """
        n = self._target_n
        for view_id in sorted(self._extras):
            if view_id >= n:
                self._destroy(view_id)
        plan = {entry[0]: entry for entry in self._plan}
        self._to_create = []
        self._needs_dock = set()
        self._to_dock = []
        redock_rest = False
        for view_id in range(1, n):
            window = self._extras.get(view_id)
            entry = plan.get(view_id)
            if window is None:
                self._to_create.append(view_id)
                if entry is not None:
                    self._needs_dock.add(view_id)
                    redock_rest = True
                continue
            window.visible = True
            if entry is None:
                continue
            if not redock_rest and self._applied.get(view_id) == entry[1:] and window.docked:
                continue  # 이미 같은 자리
            redock_rest = True
            if window.docked:
                window.undock()
            self._applied.pop(view_id, None)
            self._needs_dock.add(view_id)

    def _create(self, view_id: int) -> None:
        width = max(int((self._win0.width or 1280) / 2), 320)
        height = max(int((self._win0.height or 720) / 2), 240)
        camera = self._camera_of(view_id) if self._camera_of else None
        window = vp_util.create_viewport_window(
            name=f"{EXTRA_WINDOW_PREFIX} {view_id}",
            usd_context_name=self._usd_context_name,
            width=width,
            height=height,
            camera_path=camera or None,
            dock_tab_bar_visible=not bool(carb.settings.get_settings().get(_DOCK_TAB_INVISIBLE_SETTING)),
        )
        if window is None:
            carb.log_warn(f"[morph.viewport.split] failed to create viewport window for view {view_id}")
            self._needs_dock.discard(view_id)
            return
        self._extras[view_id] = window
        self._applied.pop(view_id, None)

    def _destroy(self, view_id: int) -> None:
        window = self._extras.pop(view_id, None)
        self._applied.pop(view_id, None)
        if window is None:
            return
        try:
            window.visible = False
            window.destroy()
        except ReferenceError:
            pass

    # ------------------------------------------------------------ 조회/쓰기
    def names(self, n: int) -> list[str]:
        out = []
        for view_id in range(n):
            window = self._window(view_id)
            try:
                out.append(window.title if window is not None else "")
            except ReferenceError:
                out.append("")
        return out

    def read_camera(self, view_id: int) -> str | None:
        api = self._api(self._window(view_id))
        if api is None:
            return None
        path = api.camera_path
        if path is None:
            return None
        text = path.pathString if hasattr(path, "pathString") else str(path)
        return text or None

    def write_camera(self, view_id: int, camera_path: str) -> str | None:
        api = self._api(self._window(view_id))
        if api is None:
            return None
        if camera_path and self.read_camera(view_id) != camera_path:
            # 뷰포트 API는 유효하지 않은 카메라를 조용히 무시한다. 실제 값은 다시 읽어서 돌려준다.
            api.camera_path = camera_path
        return self.read_camera(view_id)

    def read_active_view(self) -> int | None:
        mod = _viewport_window_module()
        if mod is None:
            return None
        active = mod.ViewportWindow.active_window
        if active is None:
            return None
        try:
            title = active.title  # 죽은 프록시면 ReferenceError
        except ReferenceError:
            return None
        for view_id in [0, *sorted(self._extras)]:
            window = self._window(view_id)
            try:
                if window is not None and window.title == title:
                    return view_id
            except ReferenceError:
                continue
        return None

    def focus(self, view_id: int) -> None:
        window = self._window(view_id)
        if window is None:
            return
        window.focus()
        # omni.ui 포커스 콜백(focused_changed)이 이 프레임에 오지 않을 수 있어 Kit 활성 뷰포트도 직접 맞춘다.
        # 한계: gamepad 카메라 제어 전환(이전 활성 창 끄기/새 창 켜기)은 ViewportWindow의 비공개
        # 메서드(__set_gamepad_enabled)가 focused_changed 안에서만 한다. 여기서는 부르지 않으므로
        # window.focus()가 omni.ui 포커스 콜백을 일으키지 않는 경우 gamepad 대상이 이전 창에 남을 수 있다.
        mod = _viewport_window_module()
        if mod is not None:
            # view 0은 Kit이 준 프록시다. 프록시는 다시 감쌀 수 없으므로 그대로 넣는다.
            mod.ViewportWindow.active_window = self._as_proxy(window)

    def closed_views(self) -> list[int]:
        closed = []
        for view_id, window in self._extras.items():
            try:
                if not window.visible or window.viewport_api is None:
                    closed.append(view_id)
            except ReferenceError:
                closed.append(view_id)
        return sorted(closed)

    def validate_camera(self, camera_path: str) -> None:
        ctx = omni.usd.get_context(self._usd_context_name)
        validate_camera_prim(ctx.get_stage() if ctx else None, camera_path)

    # ------------------------------------------------------------ 화면 영역
    def read_rects(self, n: int) -> list[dict | None]:
        """view 0..n-1의 화면 영역 (앱 메인 창 기준 0..1, 원점 좌상단). 모르면 None.

        omni.ui 창(`ui.Workspace.get_window(title)`)의 position_x/position_y/width/height를 쓴다
        (탭바·안쪽 여백 포함 창 전체). 전환 중(창 생성·도킹 중)은 위치가 확정되지 않았으므로 모두 None.
        """
        if self._phase is not None:
            return [None] * n
        main = self._main_size()
        if not main:
            return [None] * n
        return [self._rect(view_id, main) for view_id in range(n)]

    def _rect(self, view_id: int, main: tuple[float, float]) -> dict | None:
        window = self._style_target(view_id)
        if window is None:
            return None
        try:
            if not window.visible:
                return None
            return normalize_rect(
                window.position_x, window.position_y, window.width, window.height, main[0], main[1]
            )
        except Exception:  # noqa: BLE001 - 죽은 프록시, 속성 없음 등: 이 뷰만 모름
            return None

    # ------------------------------------------------------------ 꾸밈
    def refresh_style(self) -> None:
        """전환 중이 아닐 때 업데이트마다. STYLE_CHECK_FRAMES마다 확인 (사용자 재도킹 등으로 달라진 값 복구)."""
        if not self._style.active or self._phase is not None:
            return
        if self._style_countdown > 0:
            self._style_countdown -= 1
            return
        self._apply_style()

    def _style_target(self, view_id: int):
        """꾸밈을 쓸 창. 제목으로 얻은 omni.ui 핸들, 없으면 창 객체 자체 (ViewportWindow도 ui.Window)."""
        window = self._window(view_id)
        if window is None:
            return None
        try:
            title = window.title
        except ReferenceError:
            return None
        try:
            handle = ui.Workspace.get_window(title) if title else None
        except Exception:  # noqa: BLE001 - 창 객체로 대신한다
            handle = None
        return handle if handle is not None else window

    def _apply_style(self) -> None:
        """모든 뷰 창에 꾸밈 적용 + 분할선 push(1회). 실패는 (뷰, 속성)당 경고 1회, 예외를 던지지 않는다."""
        self._style_countdown = self.STYLE_CHECK_FRAMES
        if not self._style.active:
            return
        self._splitter.push()
        if not self._style_attrs:
            return
        for view_id in [0, *sorted(self._extras)]:
            window = self._style_target(view_id)
            if window is None:
                continue
            before, failures = apply_window_attrs(window, self._style_attrs)
            if view_id == 0:
                for name, value in before.items():
                    self._style_original0.setdefault(name, value)
            for name, exc in failures:
                if (view_id, name) not in self._style_warned:
                    self._style_warned.add((view_id, name))
                    _style_warn(f"[morph.viewport.split] setting {name} on view {view_id} window failed: {exc!r}")

    def _restore_style0(self) -> None:
        original, self._style_original0 = self._style_original0, {}
        if not original or self._win0 is None:
            return
        window = self._style_target(0)
        if window is None:
            return
        # 비활성 해제 → 표시 순서가 되도록 적용 순서의 역순으로
        _, failures = apply_window_attrs(window, dict(reversed(list(original.items()))))
        for name, exc in failures:
            _style_warn(f"[morph.viewport.split] restoring {name} on view 0 window failed: {exc!r}")

    def shutdown(self, camera0: str | None) -> None:
        self._reset_steps()
        self._render_gate.cancel()
        for view_id in sorted(self._extras):
            self._destroy(view_id)
        if camera0:
            try:
                self.write_camera(0, camera0)
            except ReferenceError:
                pass
        try:
            self._restore_style0()
        except Exception as exc:  # noqa: BLE001 - 정리는 끝까지
            _style_warn(f"[morph.viewport.split] restoring view 0 window style failed: {exc!r}")
        self._splitter.pop()
        self._win0 = None
