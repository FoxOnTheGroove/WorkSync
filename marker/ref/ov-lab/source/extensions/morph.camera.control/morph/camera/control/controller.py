"""CameraController: 포커스 뷰 카메라 제어. 명령은 큐잉 후 _update()에서 프레임당 한 번 병합 반영한다.

공개 API는 CONTRACT.md에 적힌 것뿐이다. 밑줄 접두어 메서드(_update, _set_target_viewport, _on_* 등)는
익스텐션 배선(extension.py)·웹 핸들러(web.py)·테스트 전용이다. 다른 익스텐션이 부르면 이중 반영 등 오동작한다.

뷰포트·이벤트·웹 송신·설정은 생성자 주입이라 뷰포트 없이 테스트할 수 있다.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import carb
import omni.usd
from morph.perf.monitor import counter, scope
from pxr import Sdf

from . import camera_math as cm
from . import usd_camera

EXT_ID = "morph.camera.control"

EVENT_CHANGED = "morph.camera.changed"
EVENT_ACTIVE_CHANGED = "morph.camera.active_changed"

SETTING_ORBIT_SPEED = "/exts/morph.camera.control/orbitSpeed"
SETTING_STATE_RATE_HZ = "/exts/morph.camera.control/stateRateHz"

METRIC_UPDATE_MS = "morph.camera.control.update_ms"
METRIC_COMMANDS = "morph.camera.control.commands"
METRIC_STATE_SENDS = "morph.camera.control.state_sends"

_PROBE_INTERVAL_S = 0.5  # 대상이 없을 때 활성 뷰포트를 다시 찾는 주기


@dataclass(frozen=True)
class CameraState:
    """포커스 뷰 카메라 상태 스냅샷."""

    camera_path: str
    eye: cm.Vec3
    target: cm.Vec3
    fov: float  # 수평 시야각 (deg)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "camera_path": self.camera_path,
            "eye": [float(c) for c in self.eye],
            "target": [float(c) for c in self.target],
            "fov": float(self.fov),
        }

    def close_to(self, other: Optional["CameraState"]) -> bool:
        """같은 상태로 볼지. USD 회전 op가 float32라 읽기 왕복 오차를 장면 크기 비례로 허용한다."""
        if other is None or self.camera_path != other.camera_path or abs(self.fov - other.fov) > 1e-5:
            return False
        mag = max(
            1.0, cm.length(self.eye), cm.length(self.target), cm.length(other.eye), cm.length(other.target)
        )
        tol = 1e-5 * mag
        return cm.distance(self.eye, other.eye) <= tol and cm.distance(self.target, other.target) <= tol


@dataclass(frozen=True)
class _Preset:
    eye: cm.Vec3
    target: cm.Vec3
    fov: float


@dataclass
class _Interp:
    start_eye: cm.Vec3
    start_target: cm.Vec3
    end_eye: cm.Vec3
    end_target: cm.Vec3
    t0: float
    duration: float


class ViewTarget:
    """제어 대상 = 뷰포트 하나 (Kit ViewportAPI 또는 같은 속성을 가진 객체)."""

    def __init__(self, viewport_api: Any, viewport_name: str = ""):
        self.viewport_api = viewport_api
        self.viewport_name = viewport_name or ""

    @property
    def camera_path(self) -> str:
        path = self.viewport_api.camera_path
        return str(path) if path else ""

    def set_camera_path(self, path: str) -> None:
        self.viewport_api.camera_path = Sdf.Path(path)

    def get_stage(self):
        name =getattr(self.viewport_api, "usd_context_name", "") or ""
        ctx = omni.usd.get_context(name)
        stage = ctx.get_stage() if ctx is not None else None
        if stage is None:
            stage = getattr(self.viewport_api, "stage", None)
        return stage


def _event_get(event: Any, key: str, default: Any) -> Any:
    """carb Event 또는 dict에서 키 읽기."""
    try:
        value = event[key]
    except Exception:  # noqa: BLE001 -- 키 없음 / 타입 불일치
        return default
    return default if value is None else value


class CameraController:
    """CONTRACT의 공개 API. 싱글턴은 `get_camera_controller()`로 얻는다."""

    def __init__(
        self,
        *,
        dispatch: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        send_state: Optional[Callable[[Dict[str, Any], bool], bool]] = None,
        viewport_resolver: Optional[Callable[[str], Any]] = None,
        active_viewport_getter: Optional[Callable[[], Tuple[Any, str]]] = None,
        settings_getter: Optional[Callable[[str], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._dispatch = dispatch or (lambda name, payload: None)
        self._send_state = send_state or (lambda payload, force: False)
        self._viewport_resolver = viewport_resolver
        self._active_viewport_getter = active_viewport_getter
        self._settings_getter = settings_getter or (lambda path: None)
        self._clock = clock

        self._lock = threading.Lock()
        # 큐 (lock 보호)
        self._abs: Optional[Tuple[cm.Vec3, cm.Vec3, float]] = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._dx = 0.0
        self._dy = 0.0
        self._zoom = 1.0
        self._has_orbit = False
        self._has_pan = False
        self._has_zoom = False
        self._n_commands = 0
        self._presets: Dict[str, _Preset] = {}

        # 메인 스레드 상태
        self._target: Optional[ViewTarget] = None
        self._camera_path = ""
        self._state: Optional[CameraState] = None
        self._last_emitted: Optional[CameraState] = None
        self._interp: Optional[_Interp] = None
        self._web_dirty = False
        self._last_sent = -math.inf
        self._next_probe = -math.inf

    # ------------------------------------------------------------------ 공개 API (큐잉, 아무 스레드)

    def orbit(self, d_yaw: float, d_pitch: float) -> None:
        d_yaw, d_pitch = _finite(d_yaw, "d_yaw"), _finite(d_pitch, "d_pitch")
        with self._lock:
            self._yaw += d_yaw
            self._pitch += d_pitch
            self._has_orbit = True
            self._n_commands += 1

    def pan(self, dx: float, dy: float) -> None:
        dx, dy = _finite(dx, "dx"), _finite(dy, "dy")
        with self._lock:
            self._dx += dx
            self._dy += dy
            self._has_pan = True
            self._n_commands += 1

    def zoom(self, factor: float) -> None:
        factor = _zoom_factor(factor)
        with self._lock:
            self._zoom *= factor
            self._has_zoom = True
            self._n_commands += 1

    def look_at(self, eye: Sequence[float], target: Sequence[float], duration_s: float = 0.0) -> None:
        eye_v, target_v = cm.vec3(eye), cm.vec3(target)
        if cm.distance(eye_v, target_v) < cm.MIN_DISTANCE:
            raise ValueError("eye and target must differ")
        duration = _duration(duration_s)
        with self._lock:
            self._enqueue_abs_locked(eye_v, target_v, duration)

    def apply_preset(self, name: str, duration_s: float = 0.0) -> None:
        duration = _duration(duration_s)
        with self._lock:
            preset = self._presets[name]  # 없으면 KeyError
            self._enqueue_abs_locked(preset.eye, preset.target, duration)

    # ------------------------------------------------------------------ 공개 API (메인 스레드)

    def save_preset(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("preset name must be a non-empty str")
        state = self._state if self._state is not None else self._refresh_state()
        if state is None:
            raise RuntimeError("no camera to save (no focus viewport)")
        with self._lock:
            self._presets[name] = _Preset(state.eye, state.target, state.fov)

    def delete_preset(self, name: str) -> None:
        """프리셋 삭제. 없는 이름은 KeyError. 이미 큐에 들어간 apply_preset·진행 중인 보간에는 영향 없음."""
        if not isinstance(name, str):
            raise KeyError(name)
        with self._lock:
            del self._presets[name]  # 없으면 KeyError

    def list_presets(self) -> List[str]:
        """저장된 프리셋 이름 목록. Python str 정렬(유니코드 코드 포인트 오름차순, 대소문자 구분)."""
        with self._lock:
            return sorted(self._presets)

    def get_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """프리셋 내용 `{name, eye, target, fov}` (새 dict). 없으면 None."""
        with self._lock:
            preset = self._presets.get(name) if isinstance(name, str) else None
        return _preset_payload(name, preset) if preset is not None else None

    def set_active_camera(self, prim_path: str) -> None:
        if not isinstance(prim_path, str) or not prim_path:
            raise ValueError("prim_path must be a non-empty str")
        # 포커스 뷰 = 지금의 Kit 활성 뷰포트. focus_changed는 한 프레임 늦게 오므로 여기서 다시 확인한다.
        # 대상 전환과 카메라 변경을 한 번의 상태 갱신으로 묶어 active_changed는 호출당 최대 1회 (최종 경로 기준).
        self._sync_to_active_viewport(refresh=False)
        try:
            target = self._target
            if target is None:
                raise RuntimeError("no focus viewport")
            current_path, stage, ok = self._read_target(target)
            if not ok:
                raise RuntimeError("focus viewport is no longer valid")
            if stage is None:
                raise RuntimeError("no stage open")
            if not usd_camera.is_camera(stage, prim_path):
                raise ValueError(f"not a Camera prim: {prim_path!r}")
            if current_path != prim_path:
                self._cancel_pending()
                try:
                    target.set_camera_path(prim_path)
                except Exception as e:  # noqa: BLE001 -- 창이 닫혀 뷰포트가 무효해짐
                    raise RuntimeError(f"focus viewport is no longer valid: {e}") from e
        finally:
            self._refresh_state()

    def get_state(self) -> Optional[CameraState]:
        return self._state

    @property
    def viewport_name(self) -> str:
        """조작 대상 뷰포트 창 이름. 대상이 없거나 이름을 모르면 ""."""
        target = self._target
        return target.viewport_name if target is not None else ""

    # ------------------------------------------------------------------ 내부 (익스텐션 배선/웹/테스트 전용)

    def _set_target_viewport(self, viewport_api: Any, viewport_name: str = "", *, refresh: bool = True) -> None:
        """제어 대상 뷰포트를 바꾼다 (None = 대상 없음). 대기 명령과 보간은 취소된다."""
        self._cancel_pending()
        self._target = ViewTarget(viewport_api, viewport_name) if viewport_api is not None else None
        if refresh:
            self._refresh_state()

    def _unavailable_reason(self) -> Optional[str]:
        """명령을 받아도 반영할 수 없으면 그 이유 (웹 failed_precondition 회신용). 가능하면 None."""
        if self._target is None:
            return "no target viewport"
        if self._state is None:
            return "no camera in target viewport"
        return None

    def _has_preset(self, name: str) -> bool:
        with self._lock:
            return name in self._presets

    def _preset_count(self) -> int:
        with self._lock:
            return len(self._presets)

    def _preset_payloads(self) -> List[Dict[str, Any]]:
        """전체 프리셋 내용 (이름순). 한 번의 lock으로 일관된 스냅샷."""
        with self._lock:
            items = sorted(self._presets.items())
        return [_preset_payload(name, preset) for name, preset in items]

    def _on_focus_changed(self, event: Any) -> None:
        """`morph.viewport.focus_changed` 처리."""
        name = _event_get(event, "viewport_name", "")
        if not isinstance(name, str) or not name:
            return
        if self._target is not None and name == self._target.viewport_name:
            return
        viewport_api = None
        if self._viewport_resolver is not None:
            try:
                viewport_api = self._viewport_resolver(name)
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] cannot resolve viewport {name!r}: {e}")
        if viewport_api is None:
            carb.log_info(f"[{EXT_ID}] focus_changed: viewport {name!r} not found, keeping current target")
            return
        self._set_target_viewport(viewport_api, name)

    def _on_stage_opened(self, _event: Any = None) -> None:
        """스테이지 열림/닫힘: 보간·대기 명령이 새 스테이지로 넘어가지 않게 취소."""
        self._cancel_pending()
        self._last_emitted = None

    def _on_client_connected(self, _event: Any = None) -> None:
        """웹 클라이언트 연결 시 현재 상태 1회 송신."""
        state = self._state
        if state is None:
            return
        if self._send_state(state.to_payload(), True):
            self._last_sent = self._clock()
            self._web_dirty = False
            counter(METRIC_STATE_SENDS)

    def _update(self, now: Optional[float] = None) -> None:
        """메인 업데이트 루프에서 프레임당 1회 호출."""
        now = self._clock() if now is None else now
        with scope(METRIC_UPDATE_MS):
            self._probe_target(now)
            current = self._refresh_state()
            abs_cmd, rel, n = self._take_queue()
            if current is None:
                self._interp = None  # 대상 없음: 명령은 버려지고 세지 않는다
            else:
                if n:
                    counter(METRIC_COMMANDS, n)
                self._apply(current, abs_cmd, rel, now)
            self._emit_changed()
            self._maybe_send(now)

    def _shutdown(self) -> None:
        self._cancel_pending()
        self._target = None
        self._state = None

    # ------------------------------------------------------------------ 구현

    def _enqueue_abs_locked(self, eye: cm.Vec3, target: cm.Vec3, duration: float) -> None:
        # 절대 명령은 앞서 쌓인 상대 명령을 무효화한다.
        self._abs = (eye, target, duration)
        self._yaw = self._pitch = self._dx = self._dy = 0.0
        self._zoom = 1.0
        self._has_orbit = self._has_pan = self._has_zoom = False
        self._n_commands += 1

    def _take_queue(self):
        with self._lock:
            abs_cmd = self._abs
            rel = None
            if self._has_orbit or self._has_pan or self._has_zoom:
                rel = (
                    (self._yaw, self._pitch) if self._has_orbit else None,
                    (self._dx, self._dy) if self._has_pan else None,
                    self._zoom if self._has_zoom else None,
                )
            n = self._n_commands
            self._clear_queue_locked()
        return abs_cmd, rel, n

    def _clear_queue_locked(self) -> None:
        self._abs = None
        self._yaw = self._pitch = self._dx = self._dy = 0.0
        self._zoom = 1.0
        self._has_orbit = self._has_pan = self._has_zoom = False
        self._n_commands = 0

    def _cancel_pending(self) -> None:
        with self._lock:
            self._clear_queue_locked()
        self._interp = None

    def _lookup_active_viewport(self) -> Tuple[Any, str]:
        if self._active_viewport_getter is None:
            return None, ""
        try:
            viewport_api, name = self._active_viewport_getter()
        except Exception as e:  # noqa: BLE001
            carb.log_info(f"[{EXT_ID}] active viewport lookup failed: {e}")
            return None, ""
        return viewport_api, (name or "")

    def _probe_target(self, now: float) -> None:
        if self._target is not None or self._active_viewport_getter is None or now < self._next_probe:
            return
        self._next_probe = now + _PROBE_INTERVAL_S
        viewport_api, name = self._lookup_active_viewport()
        if viewport_api is not None:
            self._target = ViewTarget(viewport_api, name)

    def _sync_to_active_viewport(self, refresh: bool = True) -> None:
        """Kit 활성 뷰포트가 현재 대상과 다르면 대상을 바꾼다 (대기 명령·보간 취소). 메인 스레드."""
        viewport_api, name = self._lookup_active_viewport()
        if viewport_api is None:
            return
        current = self._target
        if current is not None:
            if current.viewport_api is viewport_api:
                return
            if name and current.viewport_name and name == current.viewport_name:
                return
        self._set_target_viewport(viewport_api, name, refresh=refresh)

    def _read_target(self, target: ViewTarget):
        """(camera_path, stage, ok). 뷰포트가 무효면 ok=False (레이아웃 축소·종료 시 정상 상황이라 info 로그)."""
        try:
            return target.camera_path, target.get_stage(), True
        except Exception as e:  # noqa: BLE001
            carb.log_info(f"[{EXT_ID}] focus viewport {target.viewport_name!r} is no longer valid: {e}")
            return "", None, False

    def _fallback_to_active(self, dead: ViewTarget) -> Optional[ViewTarget]:
        """무효해진 대상 대신 쓸 Kit 활성 뷰포트. 다른 유효 뷰포트가 없으면 None. 바꾸면 대기 명령·보간 취소."""
        viewport_api, name = self._lookup_active_viewport()
        if viewport_api is None or viewport_api is dead.viewport_api:
            return None
        if name and dead.viewport_name and name == dead.viewport_name:
            return None
        carb.log_info(f"[{EXT_ID}] switching target to active viewport {name!r}")
        self._cancel_pending()
        self._target = ViewTarget(viewport_api, name)
        return self._target

    def _refresh_state(self):
        """카메라 경로 동기화(바뀌면 active_changed) + 포즈 재읽기. 반환 (stage, path, pose) 또는 None."""
        path, stage = "", None
        target = self._target
        if target is not None:
            path, stage, ok = self._read_target(target)
            if not ok or not path:
                # 대상 창이 사라졌을 수 있다 (예: 레이아웃 축소 후 focus_changed 도착 전).
                # "대상 없음"을 선언하기 전에 Kit 활성 뷰포트를 한 번 다시 확인한다.
                fallback = self._fallback_to_active(target)
                if fallback is not None:
                    target = fallback
                    path, stage, ok = self._read_target(target)
                if not ok:
                    self._target = None
                    target = None
                    path, stage = "", None
        if path != self._camera_path:
            self._camera_path = path
            self._cancel_pending()
            self._dispatch(
                EVENT_ACTIVE_CHANGED,
                {"camera_path": path, "viewport_name": target.viewport_name if target is not None else ""},
            )
        pose = None
        if stage is not None and path:
            try:
                pose = usd_camera.read_camera_pose(stage, path)
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] cannot read camera {path!r}: {e}")
        if pose is None:
            self._state = None
            return None
        self._state = CameraState(path, pose[0], pose[1], pose[2])
        return stage, path, pose

    def _apply(self, current, abs_cmd, rel, now: float) -> None:
        stage, path, (eye, target, fov) = current
        up = usd_camera.stage_up_vector(stage)
        new_eye, new_target = eye, target

        if abs_cmd is not None:
            a_eye, a_target, duration = abs_cmd
            if duration > 0.0:
                if rel is not None:
                    # 같은 프레임에서 절대 명령 뒤에 온 상대 명령은 보간 목표 포즈에 적용한다.
                    a_eye, a_target = self._apply_rel(a_eye, a_target, rel, up)
                    rel = None
                self._interp = _Interp(eye, target, a_eye, a_target, now, duration)
            else:
                self._interp = None
                new_eye, new_target = a_eye, a_target

        if self._interp is not None:
            ip = self._interp
            s = (now - ip.t0) / ip.duration
            new_eye, new_target = cm.interpolate(ip.start_eye, ip.start_target, ip.end_eye, ip.end_target, s, up)
            if s >= 1.0:
                self._interp = None

        if rel is not None:
            self._interp = None  # 이전 프레임에 시작된 보간은 사용자 입력이 이긴다
            new_eye, new_target = self._apply_rel(new_eye, new_target, rel, up)

        if cm.vec_close(new_eye, eye) and cm.vec_close(new_target, target):
            return
        if not (cm.is_finite(new_eye) and cm.is_finite(new_target)) or cm.distance(new_eye, new_target) < cm.MIN_DISTANCE * 0.5:
            carb.log_warn(f"[{EXT_ID}] skipping invalid camera pose")
            self._interp = None
            return
        try:
            usd_camera.write_camera_pose(stage, path, new_eye, new_target, up)
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] cannot write camera {path!r}: {e}")
            self._interp = None
            return
        self._state = CameraState(path, new_eye, new_target, fov)

    def _apply_rel(self, eye: cm.Vec3, target: cm.Vec3, rel, up: cm.Vec3):
        orbit_d, pan_d, zoom_f = rel
        if orbit_d is not None:
            speed = self._orbit_speed()
            eye, target = cm.orbit(eye, target, up, orbit_d[0] * speed, orbit_d[1] * speed)
        if pan_d is not None:
            eye, target = cm.pan(eye, target, up, pan_d[0], pan_d[1])
        if zoom_f is not None:
            eye, target = cm.zoom(eye, target, zoom_f)
        return eye, target

    def _emit_changed(self) -> None:
        state = self._state
        if state is None:
            self._last_emitted = None
            return
        if state.close_to(self._last_emitted):
            return
        self._last_emitted = state
        self._web_dirty = True
        self._dispatch(EVENT_CHANGED, state.to_payload())

    def _maybe_send(self, now: float) -> None:
        state = self._state
        if not self._web_dirty or state is None:
            return
        rate = self._setting_float(SETTING_STATE_RATE_HZ, 30.0)
        if rate > 0.0 and now - self._last_sent < 1.0 / rate:
            return  # 다음 프레임에 다시 시도 (최신 상태가 나감)
        self._web_dirty = False
        self._last_sent = now
        if self._send_state(state.to_payload(), False):
            counter(METRIC_STATE_SENDS)

    def _orbit_speed(self) -> float:
        return self._setting_float(SETTING_ORBIT_SPEED, 1.0)

    def _setting_float(self, path: str, default: float) -> float:
        try:
            value = self._settings_getter(path)
            value = float(default if value is None else value)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default


def _preset_payload(name: str, preset: _Preset) -> Dict[str, Any]:
    return {
        "name": name,
        "eye": [float(c) for c in preset.eye],
        "target": [float(c) for c in preset.target],
        "fov": float(preset.fov),
    }


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(f):
        raise ValueError(f"{name} must be finite")
    return f


def _zoom_factor(value: Any) -> float:
    f = _finite(value, "factor")
    if f <= 0.0:
        raise ValueError("factor must be > 0")
    return f


def _duration(value: Any) -> float:
    return max(0.0, _finite(value, "duration_s"))
