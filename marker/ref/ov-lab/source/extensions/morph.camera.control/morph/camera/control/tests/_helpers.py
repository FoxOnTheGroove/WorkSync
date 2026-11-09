"""테스트 공용 도구. 뷰포트 창 없이 omni.usd 스테이지 + Camera prim으로 테스트한다."""
from __future__ import annotations

import json
import math
import re

import omni.usd
from pxr import Gf, Sdf, UsdGeom


class FakeViewport:
    """ViewportAPI 대역: camera_path(Sdf.Path), usd_context_name만 쓴다."""

    def __init__(self, camera_path: str, usd_context_name: str = ""):
        self.camera_path = Sdf.Path(camera_path)
        self.usd_context_name = usd_context_name


class BrokenViewport:
    """창이 닫혀 무효가 된 뷰포트 흉내."""

    usd_context_name = ""

    @property
    def camera_path(self):
        raise RuntimeError("viewport destroyed")


class DyingViewport:
    """레이아웃 축소로 창이 destroy되는 뷰포트 흉내. kill() 뒤 mode에 따라 동작이 바뀐다.

    - "raise": camera_path 접근 시 예외
    - "empty": camera_path가 빈 경로
    - "stale": 예전 경로를 그대로 돌려줌 (카메라 prim은 살아 있으므로 계속 조작 가능)
    """

    usd_context_name = ""

    def __init__(self, camera_path: str, mode: str):
        self._path = Sdf.Path(camera_path)
        self._mode = mode
        self._alive = True

    def kill(self):
        self._alive = False

    @property
    def camera_path(self):
        if not self._alive:
            if self._mode == "raise":
                raise RuntimeError("viewport destroyed")
            if self._mode == "empty":
                return Sdf.Path()
        return self._path

    @camera_path.setter
    def camera_path(self, value):
        if not self._alive:
            raise RuntimeError("viewport destroyed")
        self._path = Sdf.Path(value)


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class EventRecorder:
    def __init__(self):
        self.events = []

    def __call__(self, name, payload):
        self.events.append((name, dict(payload)))

    def named(self, name):
        return [p for n, p in self.events if n == name]

    def clear(self):
        self.events.clear()


class SendRecorder:
    def __init__(self, connected: bool = True):
        self.connected = connected
        self.sent = []  # (payload, force)

    def __call__(self, payload, force):
        if not (force or self.connected):
            return False
        self.sent.append((payload, force))
        return True


_CODE_RE = re.compile(r"[a-z][a-z0-9_]*\Z")


class FakeCtx:
    """morph.stream.messaging 1.x `MessageContext` 대역 (CONTRACT의 회신 규칙을 그대로 강제).

    - 컨텍스트당 한 번만 회신 (두 번째는 RuntimeError)
    - reply_ok: fields에 ok 키면 ValueError
    - reply_error: code 형식([a-z][a-z0-9_]*)·message str·fields에 ok/error 키 검사
    - reply: ok bool, 실패면 error·message 필요
    `replies`에는 회신 payload가 쌓인다 (실제 stream은 id가 None이면 송신을 생략하지만 여기선 기록한다).
    """

    def __init__(self, type_="", id_="req-1"):
        self.type = type_
        self.v = 1
        self.id = id_
        self.replied = False
        self.replies = []

    def _commit(self, payload):
        if self.replied:
            raise RuntimeError("already replied")
        json.dumps(payload, allow_nan=False)
        self.replied = True
        self.replies.append(dict(payload))

    def reply_ok(self, **fields):
        if "ok" in fields:
            raise ValueError("'ok' is reserved")
        self._commit({"ok": True, **fields})

    def reply_error(self, code, message, **fields):
        if not isinstance(code, str) or not _CODE_RE.match(code):
            raise ValueError(f"bad error code {code!r}")
        if not isinstance(message, str):
            raise ValueError("message must be str")
        if "ok" in fields or "error" in fields:
            raise ValueError("'ok'/'error' are reserved")
        self._commit({"ok": False, "error": code, "message": message, **fields})

    def reply(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("reply payload needs ok: bool")
        if payload["ok"] is False and not (
            isinstance(payload.get("error"), str) and isinstance(payload.get("message"), str)
        ):
            raise ValueError("failure reply needs error and message")
        self._commit(payload)


async def new_stage(up_axis: str = "Y"):
    ctx = omni.usd.get_context()
    await ctx.new_stage_async()
    stage = ctx.get_stage()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z if up_axis == "Z" else UsdGeom.Tokens.y)
    return stage


def define_camera(stage, path, eye=(0.0, 0.0, 10.0), coi_distance=10.0, ops="yxz"):
    """eye에서 -Z(로컬)를 보는 카메라. coi_distance가 None이면 COI 속성 없음."""
    cam = UsdGeom.Camera.Define(stage, path)
    xf = UsdGeom.Xformable(cam.GetPrim())
    if ops == "yxz":
        xf.AddTranslateOp().Set(Gf.Vec3d(*eye))
        xf.AddRotateYXZOp().Set(Gf.Vec3f(0, 0, 0))
    elif ops == "xyz":
        xf.AddTranslateOp().Set(Gf.Vec3d(*eye))
        xf.AddRotateXYZOp().Set(Gf.Vec3f(0, 0, 0))
    elif ops == "matrix":
        xf.AddTransformOp().Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(*eye)))
    elif ops == "none":
        pass
    else:
        raise ValueError(ops)
    if coi_distance is not None:
        cam.GetPrim().CreateAttribute("omni:kit:centerOfInterest", Sdf.ValueTypeNames.Vector3d, True).Set(
            Gf.Vec3d(0, 0, -coi_distance)
        )
    return path


def assert_vec(test, actual, expected, places=4):
    test.assertEqual(len(actual), 3)
    for a, e in zip(actual, expected):
        test.assertAlmostEqual(float(a), float(e), places=places, msg=f"{tuple(actual)} != {tuple(expected)}")


def assert_finite_list(test, value):
    test.assertIsInstance(value, list)
    test.assertEqual(len(value), 3)
    for c in value:
        test.assertIsInstance(c, float)
        test.assertTrue(math.isfinite(c))
