"""테스트용 가짜 창 어댑터 (실제 KitWindowAdapter와 같은 인터페이스)."""

from __future__ import annotations

import json
import re
import weakref

from ..layout import view_count


class FakeWindow:
    def __init__(self, title: str, camera: str):
        self.title = title
        self.camera = camera
        self.visible = True
        self.rect = None  # read_rects가 돌려줄 정규화 rect (테스트가 직접 넣는다)


class FakeWindowAdapter:
    """창 준비에 `steps` 프레임이 걸리는 창 모드 어댑터.

    `valid_cameras`를 주면 그 집합에 있는 경로만 뷰포트가 받아들인다 (Kit 뷰포트 API처럼 조용히 무시).
    `validate`를 주면 `validate_camera`에서 호출한다.
    """

    headless = False

    def __init__(self, steps: int = 3, camera0: str = "/OmniverseKit_Persp", valid_cameras=None, validate=None):
        self.steps = steps
        self.window0 = FakeWindow("Viewport", camera0)
        self.extras: dict[int, FakeWindow] = {}
        self.active: int | None = 0
        self.valid_cameras = valid_cameras
        self.validate = validate
        self.begun: list[str] = []
        self.side_ratios: list[float | None] = []  # begin_layout마다 받은 side_ratio (begun과 같은 순서)
        self.focus_calls: list[int] = []
        self.step_calls = 0
        self.shutdown_camera = None
        self.is_shut_down = False
        self._remaining = 0
        self._target_n = 1
        self._camera_of = None
        self._prepared = False

    # 전환
    def begin_layout(self, layout, camera_of, side_ratio=None):
        self.begun.append(layout)
        self.side_ratios.append(side_ratio)
        self._target_n = view_count(layout)
        self._camera_of = camera_of
        self._remaining = self.steps
        self._prepared = False

    def step(self):
        self.step_calls += 1
        if not self._prepared:
            self._prepared = True
            for vid in [v for v in self.extras if v >= self._target_n]:
                del self.extras[vid]
                if self.active == vid:
                    self.active = None
            for vid in range(1, self._target_n):
                if vid not in self.extras:
                    self.extras[vid] = FakeWindow(f"MORPH Viewport {vid}", "")
                    self._accept(vid, self._camera_of(vid))
        self._remaining -= 1
        return self._remaining <= 0

    def abort(self):
        self.aborted = True
        self.extras.clear()
        self._prepared = True
        self._remaining = 0

    def window(self, view_id):
        return self.window0 if view_id == 0 else self.extras.get(view_id)

    def _accept(self, view_id, path):
        w = self.window(view_id)
        if w is None:
            return
        if self.valid_cameras is None or path in self.valid_cameras:
            w.camera = path

    # 조회/쓰기
    def names(self, n):
        return [self.window(v).title if self.window(v) else "" for v in range(n)]

    def read_camera(self, view_id):
        w = self.window(view_id)
        return (w.camera or None) if w else None

    def write_camera(self, view_id, path):
        if self.window(view_id) is None:
            return None
        self._accept(view_id, path)
        return self.read_camera(view_id)

    def read_active_view(self):
        return self.active

    def focus(self, view_id):
        self.focus_calls.append(view_id)
        if self.window(view_id) is not None:
            self.active = view_id

    def closed_views(self):
        return sorted(v for v, w in self.extras.items() if not w.visible)

    def validate_camera(self, path):
        if self.validate is not None:
            self.validate(path)

    def refresh_style(self):
        self.style_refreshes = getattr(self, "style_refreshes", 0) + 1

    def read_rects(self, n):
        self.rect_reads = getattr(self, "rect_reads", 0) + 1
        return [(self.window(v).rect if self.window(v) is not None else None) for v in range(n)]

    def shutdown(self, camera0):
        self.is_shut_down = True
        self.shutdown_camera = camera0
        self.extras.clear()
        if camera0:
            self.window0.camera = camera0


class FakeKitSubscription:
    def __init__(self, container, fn):
        self._container, self._fn = container, fn
        container.append(fn)

    def destroy(self):
        if self._container is not None and self._fn in self._container:
            self._container.remove(self._fn)
        self._container = None


class FakeKitApi:
    """Kit ViewportAPI 흉내. `subscribe_to_frame_change`는 Kit처럼 텍스처가 있으면 구독 중 동기로 한 번 부른다.

    `render_frame()`이 렌더러가 프레임 하나를 전달한 것에 해당한다.
    """

    usd_context_name = ""

    def __init__(self, camera, has_texture=True):
        self.camera_path = camera
        self.has_texture = has_texture
        self.frame_callbacks = []

    def subscribe_to_frame_change(self, fn):
        if self.has_texture:
            fn(self)  # Kit: 유효한 상태면 즉시 호출 (렌더된 프레임이 아님)
        return FakeKitSubscription(self.frame_callbacks, fn)

    def render_frame(self):
        for fn in list(self.frame_callbacks):
            fn(self)


class FakeKitWindow:
    """Kit ViewportWindow 흉내. pybind처럼 dock_in 인자로 weakref.proxy를 거부한다.

    꾸밈 속성 기본값은 Kit 110 뷰포트 창과 같다 (탭바 표시·활성, padding 4.0).
    """

    def __init__(self, title, camera="/OmniverseKit_Persp"):
        self.title = title
        self.visible = True
        self.docked = False
        self.position_x = 0.0
        self.position_y = 0.0
        self.width = 1280
        self.height = 720
        self.dock_tab_bar_visible = True
        self.dock_tab_bar_enabled = True
        self.padding_x = 4.0
        self.padding_y = 4.0
        self.viewport_api = FakeKitApi(camera)
        self.dock_calls = []
        self.dock_ratios = []  # dock_calls와 같은 순서의 ratio
        self.focus_calls = 0
        self.destroyed = False

    def dock_in(self, window, dock_position, ratio=0.5):
        if isinstance(window, weakref.ProxyTypes):
            raise TypeError("dock_in(): incompatible function arguments (weakproxy)")
        self.dock_calls.append((window.title, dock_position))
        self.dock_ratios.append(ratio)
        self.docked = True

    def undock(self):
        self.docked = False

    def focus(self):
        self.focus_calls += 1

    def destroy(self):
        self.destroyed = True
        self.viewport_api = None


class FakeImGui:
    """omni.kit.imgui ImGui 흉내: 스타일 변수 스택."""

    def __init__(self):
        self.stack = []
        self.push_calls = 0
        self.pop_calls = 0

    def push_style_var_float(self, var, value):
        self.push_calls += 1
        self.stack.append((var, value))

    def pop_style_var(self):
        self.pop_calls += 1
        self.stack.pop()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


_CODE_RE = re.compile(r"[a-z][a-z0-9_]*\Z")


class FakeCtx:
    """stream 1.x `MessageContext` 흉내. 회신 형식 검사와 1회 제한을 같은 규칙으로 따른다.

    `replies`에는 실제로 나갈 회신 payload가 쌓인다 (형식 위반·중복 호출은 쌓이지 않음).
    """

    def __init__(self, msg_type="", msg_id="req-1"):
        self.type = msg_type
        self.v = 1
        self.id = msg_id
        self.replied = False
        self.replies: list[dict] = []

    def reply_ok(self, **fields):
        if "ok" in fields:
            raise ValueError("reply_ok fields must not contain 'ok'")
        self.reply({"ok": True, **fields})

    def reply_error(self, code, message, **fields):
        if not isinstance(code, str) or not _CODE_RE.match(code):
            raise ValueError(f"invalid error code {code!r}")
        if not isinstance(message, str):
            raise ValueError("message must be str")
        if "ok" in fields or "error" in fields:
            raise ValueError("reply_error fields must not contain 'ok'/'error'")
        self.reply({"ok": False, "error": code, "message": message, **fields})

    def reply(self, payload):
        if self.replied:
            raise RuntimeError("already replied")
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("reply payload needs ok: bool")
        if payload["ok"] is False and (
            not isinstance(payload.get("error"), str) or not isinstance(payload.get("message"), str)
        ):
            raise ValueError("failure reply needs error and message")
        json.dumps(payload, allow_nan=False)  # 직렬화 불가면 ValueError/TypeError
        self.replied = True
        self.replies.append(payload)
