"""마커 기즈모: Kit 기본 변환 기즈모 클래스 (omni.kit.manipulator.transform.TransformManipulator) 를
마커 층 (뷰포트 하나의 SceneView) 에 직접 띄운다. Kit 선택 / 편집 대상 / 전역 변환 도구 설정과 상관없다.
기즈모 모델의 translate / rotate 가 바뀌면 owner._gizmo_changed(view, 피봇 자리, 붙인 뒤 회전) 로 알린다"""

import math

from .annotation_constant import GIZMO_SIZE, TAG

__all__ = ["ViewGizmo", "euler_xyz", "axis_angle"]

_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def axis_angle(axis, deg):
    """축 (단위로 맞춘다) 둘레 deg 도 회전, 행 순서 (v' = v x R). USD Gf.Rotation 과 같은 방향"""
    n = math.sqrt(sum(float(c) * float(c) for c in axis)) or 1.0
    x, y, z = (float(c) / n for c in axis)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    t = 1.0 - c
    col = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
           [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
           [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]  # 열 벡터 관례 (v' = R v)
    return [[col[j][i] for j in range(3)] for i in range(3)]  # 행 순서로 뒤집는다


def _mul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _vec(v):
    """payload 값 하나를 float 목록으로 (Gf 벡터, 목록, 숫자)"""
    try:
        return [float(c) for c in v]
    except TypeError:
        return [float(v)]


def euler_xyz(rx, ry, rz):
    """XYZ 순서 (X 먼저, USD rotateXYZ 와 같다) 오일러 각 (도) 의 회전 행렬, 행 순서 (v' = v x R)"""
    def rot(axis, deg):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        if axis == 0:
            return [[1, 0, 0], [0, c, s], [0, -s, c]]
        if axis == 1:
            return [[c, 0, -s], [0, 1, 0], [s, 0, c]]
        return [[c, s, 0], [-s, c, 0], [0, 0, 1]]

    return _mul3(_mul3(rot(0, rx), rot(1, ry)), rot(2, rz))


def _kit():
    """(transform 모듈, gestures 모듈). 없으면 예외"""
    import omni.kit.manipulator.transform as kt
    try:
        import omni.kit.manipulator.transform.gestures as kg
    except Exception:
        kg = kt
    return kt, kg


def _make_gestures(view_gizmo):
    """끌기 변화량을 받는 손짓 (Kit 변환 기즈모는 gestures=[...] 로 받은 변경 손짓에 payload 를 넘긴다)"""
    kt, kg = _kit()
    out = []
    names = []
    translate = getattr(kg, "TranslateChangedGesture", None) or getattr(kt, "TranslateChangedGesture", None)
    rotate = getattr(kg, "RotateChangedGesture", None) or getattr(kt, "RotateChangedGesture", None)
    if translate is not None:
        class Translate(translate):
            def on_began(self, *args):
                view_gizmo._seen("began", self)
                view_gizmo._last_moved = None

            def on_changed(self, *args):
                view_gizmo._seen("changed", self)
                view_gizmo._on_translate(getattr(self, "gesture_payload", None))

            def on_ended(self, *args):
                view_gizmo._seen("ended", self)

        out.append(Translate())
        names.append("translate")
    if rotate is not None:
        class Rotate(rotate):
            def on_began(self, *args):
                view_gizmo._seen("began", self)
                view_gizmo._last_angle = {}

            def on_changed(self, *args):
                view_gizmo._seen("changed", self)
                view_gizmo._on_rotate(getattr(self, "gesture_payload", None))

            def on_ended(self, *args):
                view_gizmo._seen("ended", self)

        out.append(Rotate())
        names.append("rotate")
    return out, names


class ViewGizmo:
    """뷰포트 하나의 기즈모. 처음 보일 때 한 번 만든다. Kit 판마다 API 가 달라 되는 방법을 how 에 남긴다"""

    def __init__(self, view, root):
        self._view = view      # _View: owner, release() 에 쓴다
        self._root = root      # 기즈모를 담는 sc.Transform
        self._gizmo = None
        self.matrix = list(_IDENTITY)   # 피봇 행렬 (우리가 들고, 기즈모 기본 모델에 써 넣는다)
        self.rot = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self._error = None
        self._last_moved = None
        self._last_angle = {}
        self.how = {}

    def _make(self):
        """기즈모 모델은 TransformManipulator 가 만드는 기본 모델 그대로 (이동 / 회전 표시가 그 모델에 달려 있다).
        끌기 변화량은 우리 손짓이 받아 그 모델의 transform 에 써 넣는다"""
        if self._gizmo is not None or self._error is not None:
            return self._gizmo
        try:
            kt, _kg = _kit()
            gestures, names = _make_gestures(self)
            self.how["gestures"] = names
            with self._root:
                g = None
                for kwargs, label in (({"size": GIZMO_SIZE, "gestures": gestures}, "size, gestures"),
                                      ({"gestures": gestures}, "gestures"),
                                      ({"size": GIZMO_SIZE}, "size"),
                                      ({}, "()")):
                    try:
                        g = kt.TransformManipulator(**kwargs)
                        self.how["init"] = label
                        break
                    except TypeError:
                        continue
            if g is None:
                raise RuntimeError("TransformManipulator(...) refused every argument set")
            if "gestures" not in self.how["init"] and gestures:
                try:
                    g.gestures = list(g.gestures or []) + gestures
                    self.how["init"] += " + gestures attr"
                except Exception as e:
                    self.how["init"] += f" (gestures not attached: {type(e).__name__})"
            self.how["model"] = type(g.model).__name__
            self._gizmo = g
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
            print(f"{TAG} no transform gizmo: {self._error}")
        return self._gizmo

    def _push(self) -> None:
        """피봇 자리를 기즈모 기본 모델에 (회전 기즈모가 나오던 판처럼 translate 먼저, 없으면 transform).
        기즈모 축은 월드 그대로 두고, 회전 누적은 우리가 rot 에 든다"""
        model = self._gizmo.model
        plain = list(_IDENTITY)
        plain[12:15] = list(self.matrix[12:15])
        for name, value in (("translate", list(self.matrix[12:15])), ("transform", plain)):
            try:
                item = model.get_item(name)
            except Exception:
                item = None
            if item is not None:
                try:
                    model.set_floats(item, value)
                    self.how["place"] = name
                    return
                except Exception:
                    continue
        self.how["place"] = "no transform / translate item"

    def _set_mode(self, mode: str) -> None:
        """이동 / 회전: 기즈모의 operation 에 Operation 값을 넣는다 (회전 기즈모가 나오던 판과 같은 방법)"""
        g = self._gizmo
        try:
            kt, _kg = _kit()
            ops = getattr(kt, "Operation", None)
        except Exception:
            ops = None
        value = getattr(ops, {"move": "TRANSLATE", "rotate": "ROTATE"}[mode], None) if ops is not None else None
        tries = [("operation", value), ("op", value)] if value is not None else []
        tries.append(("operation", mode))
        for attr, val in tries:
            if hasattr(g, attr):
                try:
                    setattr(g, attr, val)
                    self.how["operation"] = f"{attr}={val}"
                    return
                except Exception:
                    pass
        if value is not None and callable(getattr(g.model, "set_operation", None)):
            try:
                g.model.set_operation(value)
                self.how["operation"] = f"model.set_operation({value})"
                return
            except Exception:
                pass
        self.how["operation"] = "not found"

    # -- 끌기 (손짓 payload) ----------------------------------------------------------

    def _seen(self, phase: str, gesture) -> None:
        """진단: 끌기 신호가 오는지, payload 가 무엇인지 (처음 것만)"""
        self.how[phase] = self.how.get(phase, 0) + 1
        p = getattr(gesture, "gesture_payload", None)
        key = f"payload_{type(gesture).__mro__[1].__name__}"
        if p is not None and key not in self.how:
            self.how[key] = (type(p).__name__, [n for n in dir(p) if not n.startswith("_")][:20])

    def _on_translate(self, p) -> None:
        if p is None or not self.visible():
            return
        delta = getattr(p, "moved_delta", None)
        if delta is None and getattr(p, "moved", None) is not None:  # 누적만 주는 판
            moved = _vec(p.moved)
            last = self._last_moved or [0.0] * len(moved)
            delta, self._last_moved = [moved[i] - last[i] for i in range(len(moved))], moved
        if delta is None:
            return
        d = _vec(delta)
        if len(d) >= 3:
            self.matrix[12:15] = [self.matrix[12 + i] + d[i] for i in range(3)]
            self._push()
            self._moved()

    def _on_rotate(self, p) -> None:
        if p is None or not self.visible():
            return
        axis = getattr(p, "axis", None)
        if axis is None:
            return
        a = _vec(axis)
        step = getattr(p, "angle_delta", None)
        if step is None and getattr(p, "angle", None) is not None:  # 누적 각만 주는 판
            key = tuple(round(c, 6) for c in a)
            now = float(_vec(p.angle)[0])
            step, self._last_angle[key] = now - self._last_angle.get(key, 0.0), now
        if step is None or len(a) < 3:
            return
        self.rot = _mul3(self.rot, axis_angle(a, float(_vec(step)[0])))
        self.matrix[0:3], self.matrix[4:7], self.matrix[8:11] = (list(r) for r in self.rot)
        self._push()
        self._moved()

    def _moved(self) -> None:
        self._view.owner._gizmo_changed(self._view, list(self.matrix[12:15]), [list(r) for r in self.rot])

    # -- 보이기 / 숨기기 -------------------------------------------------------------

    def show(self, pivot, mode: str) -> None:
        """피봇 자리에 move / rotate 기즈모"""
        g = self._make()
        if g is None:
            return
        self._set_mode(mode)
        self.rot = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.matrix = list(_IDENTITY)
        self.matrix[12:15] = [float(c) for c in pivot]
        self._last_moved, self._last_angle = None, {}
        self._push()
        for attr in ("enabled", "visible"):
            if hasattr(g, attr):
                try:
                    setattr(g, attr, True)
                except Exception:
                    pass
        self._root.visible = True

    def hide(self) -> None:
        if self._root is not None:
            self._root.visible = False
        g = self._gizmo
        if g is not None and hasattr(g, "enabled"):
            try:
                g.enabled = False
            except Exception:
                pass
        self._view.release("gizmo_zone")

    def visible(self) -> bool:
        return self._gizmo is not None and self._root is not None and bool(self._root.visible)

    # -- 진단 / 치우기 ----------------------------------------------------------------

    def probe(self) -> dict:
        """실제 Kit 의 기즈모 API 가 무엇인지"""
        out = {"viewport_id": self._view.owner._view_id(self._view), "error": self._error, "how": dict(self.how),
               "visible": self.visible()}
        try:
            import inspect
            kt, kg = _kit()
            out["module"] = [n for n in dir(kt) if not n.startswith("_")]
            out["gestures_module"] = [n for n in dir(kg) if n.endswith("Gesture") or n.endswith("Payload")]
            out["init"] = str(inspect.signature(kt.TransformManipulator.__init__))
        except Exception as e:
            out["module_error"] = f"{type(e).__name__}: {e}"
        if self._gizmo is not None:
            out["transform"] = list(self.matrix)
        if self._gizmo is not None:
            g = self._gizmo
            out["attrs"] = [n for n in dir(g) if not n.startswith("__")]
        return out

    def destroy(self) -> None:
        g, self._gizmo = self._gizmo, None
        if g is not None and callable(getattr(g, "destroy", None)):
            try:
                g.destroy()
            except Exception:
                pass
        if self._root is not None:
            self._root.clear()
        self._root = None
