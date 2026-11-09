"""논리 상태 (순수 파이썬, Kit 의존 없음).

레이아웃·뷰 목록·포커스·카메라 값과 이벤트 순서를 맡는다. 창·카메라를 실제로 바꾸는 일은
어댑터가 하고, 둘을 엮는 것은 `SplitView`다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .layout import default_camera, view_count

__all__ = [
    "ViewInfo",
    "SplitCore",
    "EVENT_LAYOUT_CHANGED",
    "EVENT_FOCUS_CHANGED",
    "EVENT_VIEW_CAMERA_CHANGED",
]

EVENT_LAYOUT_CHANGED = "morph.viewport.layout_changed"
EVENT_FOCUS_CHANGED = "morph.viewport.focus_changed"
EVENT_VIEW_CAMERA_CHANGED = "morph.viewport.view_camera_changed"


@dataclass(frozen=True)
class ViewInfo:
    """뷰 정보 스냅샷."""

    view_id: int
    viewport_name: str
    camera_path: str
    focused: bool


class _View:
    __slots__ = ("camera_path", "viewport_name", "camera_valid")

    def __init__(self, camera_path: str, viewport_name: str = ""):
        self.camera_path = camera_path
        self.viewport_name = viewport_name
        # 마지막으로 확인한 카메라 prim 존재 여부 (None = 모름). 있음→없음 전이에서만 기본값 복귀.
        self.camera_valid: bool | None = None


class SplitCore:
    """레이아웃 논리 상태와 이벤트 큐."""

    def __init__(self, default_cameras: Sequence[str] = (), camera0: str | None = None, name0: str = ""):
        self._default_cameras = tuple(p for p in default_cameras if isinstance(p, str))
        self._layout = "1x1"
        self._views: list[_View] = [_View(camera0 or self.default_camera(0), name0)]
        self._focused = 0
        self._target: str | None = None  # 전환 중이면 목표 레이아웃
        # 같은 레이아웃의 배치만 다시 잡는 전환(sideRatio 변경 재도킹) 중이면 True. 완료 시 layout_changed 없음.
        self._relayout = False
        self._stable_count = 1  # 마지막으로 완료된 레이아웃의 뷰 개수
        self._focus_at_start = 0
        self._events: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------ 조회
    @property
    def layout(self) -> str:
        return self._layout

    @property
    def transitioning(self) -> bool:
        return self._target is not None

    @property
    def relayout(self) -> bool:
        """진행 중인 전환이 같은 레이아웃의 재배치(`begin_relayout`)인지."""
        return self._target is not None and self._relayout

    @property
    def focused(self) -> int:
        return self._focused

    @property
    def count(self) -> int:
        return len(self._views)

    @property
    def stable_count(self) -> int:
        """전환 중에도 창이 유지되는 기존 뷰 개수 (마지막 완료 레이아웃 기준, 현재 개수 이하)."""
        return min(self._stable_count, len(self._views))

    def default_camera(self, view_id: int) -> str:
        return default_camera(view_id, self._default_cameras)

    def camera_of(self, view_id: int) -> str:
        return self._views[view_id].camera_path

    def name_of(self, view_id: int) -> str:
        return self._views[view_id].viewport_name

    def view_dict(self, view_id: int) -> dict:
        v = self._views[view_id]
        return {"view_id": view_id, "viewport_name": v.viewport_name, "camera_path": v.camera_path}

    def list_views(self) -> list[ViewInfo]:
        return [
            ViewInfo(i, v.viewport_name, v.camera_path, i == self._focused) for i, v in enumerate(self._views)
        ]

    def state_payload(self) -> dict:
        return {
            "layout": self._layout,
            "focused_view_id": self._focused,
            "views": [self.view_dict(i) for i in range(len(self._views))],
        }

    def check_view_id(self, view_id: object) -> int:
        if isinstance(view_id, bool) or not isinstance(view_id, int) or not 0 <= view_id < len(self._views):
            raise IndexError(f"view_id {view_id!r} out of range (layout {self._layout}, {len(self._views)} views)")
        return view_id

    # ------------------------------------------------------------------ 변경
    def set_layout(self, layout: str) -> bool:
        """논리 상태를 즉시 바꾼다. 바뀌었으면 True (어댑터 전환을 시작해야 함)."""
        n = view_count(layout)  # ValueError
        if layout == self._layout:
            return False
        if self._target is None:
            self._focus_at_start = self._focused
        old = self._views
        # 기존 뷰는 같은 view_id의 카메라·창을 유지하고, 새 뷰는 기본 카메라와 빈 창 이름으로 시작한다.
        self._views = old[:n] + [_View(self.default_camera(i)) for i in range(len(old), n)]
        if self._focused >= n:
            self._focused = 0
        self._layout = layout
        self._target = layout
        self._relayout = False  # 재배치 중이었어도 실제 레이아웃 전환으로 바뀐다 (완료 시 layout_changed)
        return True

    def begin_relayout(self) -> bool:
        """현재 레이아웃의 배치만 다시 잡는 전환 (뷰·카메라·창 그대로). 이미 전환 중이면 False.

        전환 중 규칙(포커스 이벤트 병합 등)은 같고, 완료 시 `layout_changed`는 발행하지 않는다
        (포커스가 바뀌었으면 `focus_changed`만).
        """
        if self._target is not None:
            return False
        self._focus_at_start = self._focused
        self._target = self._layout
        self._relayout = True
        return True

    def abort_to_1x1(self) -> None:
        """창 준비 실패 시: 논리 상태를 1x1로 되돌리고 전환 중 상태로 둔다 (곧바로 complete_transition)."""
        if self._target is None:
            self._focus_at_start = self._focused
        self._views = self._views[:1]
        self._focused = 0
        self._layout = "1x1"
        self._target = "1x1"
        self._relayout = False  # 실패 정책: layout_changed("1x1")는 항상 발행
        self._stable_count = min(self._stable_count, 1)

    def restart_transition(self, name0: str = "") -> None:
        """현재 레이아웃을 창으로 다시 만들 때 (논리 전용 → 창 모드). 완료 시 `layout_changed`."""
        if self._target is None:
            self._focus_at_start = self._focused
        self._views[0].viewport_name = name0
        self._target = self._layout
        self._relayout = False

    def complete_transition(self, names: Sequence[str], cameras: Sequence[str | None]) -> None:
        """창 준비가 끝났을 때. `layout_changed`, 필요하면 `focus_changed`를 큐에 넣는다."""
        if self._target is None:
            return
        for i, v in enumerate(self._views):
            if i < len(names):
                v.viewport_name = names[i] or ""
            if i < len(cameras) and cameras[i]:
                if cameras[i] != v.camera_path:
                    v.camera_path = cameras[i]
                    v.camera_valid = None
        relayout, self._relayout = self._relayout, False
        self._target = None
        self._stable_count = len(self._views)
        if not relayout:
            self._events.append((EVENT_LAYOUT_CHANGED, self.state_payload()))
        if self._focused != self._focus_at_start:
            self._events.append((EVENT_FOCUS_CHANGED, self.view_dict(self._focused)))

    def set_focus(self, view_id: int) -> bool:
        """포커스 변경. 바뀌었으면 True. 전환 중이면 이벤트는 전환 완료 시 한 번으로 합친다."""
        self.check_view_id(view_id)
        if view_id == self._focused:
            return False
        self._focused = view_id
        if self._target is None:
            self._events.append((EVENT_FOCUS_CHANGED, self.view_dict(view_id)))
        return True

    def set_camera(self, view_id: int, camera_path: str, emit: bool = True) -> bool:
        """카메라 값 저장. 바뀌었으면 True.

        전환 중에 새로 생기는 뷰(마지막 완료 레이아웃에 없던 뷰)는 이벤트 없이 저장만 한다
        (`layout_changed`의 `views`에 담긴다).
        """
        self.check_view_id(view_id)
        v = self._views[view_id]
        if camera_path == v.camera_path:
            return False
        v.camera_path = camera_path
        v.camera_valid = None
        is_new_view = self._target is not None and view_id >= self._stable_count
        if emit and not is_new_view:
            self._events.append((EVENT_VIEW_CAMERA_CHANGED, self.view_dict(view_id)))
        return True

    def views_with_deleted_camera(self, prim_exists: Callable[[str], bool | None]) -> list[int]:
        """카메라 prim이 있다가 없어진 뷰 목록. `prim_exists`가 None을 주면(스테이지 없음) 판단 보류."""
        gone = []
        for i, v in enumerate(self._views):
            exists = prim_exists(v.camera_path)
            if exists is None:
                continue
            if v.camera_valid and not exists:
                gone.append(i)
            v.camera_valid = bool(exists)
        return gone

    def pop_events(self) -> list[tuple[str, dict]]:
        events, self._events = self._events, []
        return events

    def clear_events(self) -> None:
        self._events = []
