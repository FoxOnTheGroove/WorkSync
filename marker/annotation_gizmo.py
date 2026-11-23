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


def _make_model(view_gizmo):
    """기즈모 모델: 끌기 변화량 (on_began / on_changed / on_ended 의 payload) 을 받아 transform 을 직접 바꾼다.
    Kit 변환 기즈모는 값을 스스로 안 바꾸고 모델에 변화량만 넘긴다 (prim 기즈모는 자기 모델이 USD 에 쓴다)"""
    from omni.ui import scene as sc
    try:
        import omni.kit.manipulator.transform as kt
        base = getattr(kt, "AbstractTransformManipulatorModel", None) or sc.AbstractManipulatorModel
    except Exception:
        base = sc.AbstractManipulatorModel

    class Model(base):
        def __init__(self):
            super().__init__()
            self._items = {}
            self.matrix = list(_IDENTITY)
            self.rot = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            self._angle = {}  # 각 축의 지난 누적 각 (payload 가 누적 각만 줄 때)

        def get_item(self, name):
            item = self._items.get(name)
            if item is None:
                item = sc.AbstractManipulatorItem()
                self._items[name] = item
            return item

        def get_as_floats(self, item):
            if item is self._items.get("transform"):
                return list(self.matrix)
            if item is self._items.get("translate"):
                return list(self.matrix[12:15])
            return []

        def set_floats(self, item, value):
            if item is self._items.get("transform") and len(value) == 16:
                self.matrix = [float(c) for c in value]
                self.rot = [self.matrix[0:3], self.matrix[4:7], self.matrix[8:11]]
            elif item is self._items.get("translate") and len(value) >= 3:
                self.matrix[12:15] = [float(c) for c in value[:3]]
            self._item_changed(self._items.get("transform"))

        def place(self, pivot) -> None:
            self.rot = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
            self.matrix = list(_IDENTITY)
            self.matrix[12:15] = [float(c) for c in pivot]
            self._angle = {}
            self._item_changed(self.get_item("transform"))

        def _rebuild(self) -> None:
            r = self.rot
            self.matrix[0:3], self.matrix[4:7], self.matrix[8:11] = list(r[0]), list(r[1]), list(r[2])

        # 끌기: Kit 판마다 인자가 달라 (sender, payload) / (payload) 모두 받는다
        def on_began(self, *args):
            self._angle = {}
            view_gizmo._payload_seen("began", args)

        def on_changed(self, *args):
            payload = args[-1] if args else None
            view_gizmo._payload_seen("changed", args)
            if payload is None:
                return
            moved = getattr(payload, "moved_delta", None)
            if moved is not None:
                d = _vec(moved)
                if len(d) >= 3:
                    self.matrix[12:15] = [self.matrix[12 + i] + d[i] for i in range(3)]
            axis = getattr(payload, "axis", None)
            step = getattr(payload, "angle_delta", None)
            if axis is not None and step is None and getattr(payload, "angle", None) is not None:
                key = tuple(round(c, 6) for c in _vec(axis))
                now = float(getattr(payload, "angle"))
                step, self._angle[key] = now - self._angle.get(key, 0.0), now
            if axis is not None and step is not None and moved is None:
                a = _vec(axis)
                if len(a) >= 3:
                    self.rot = _mul3(self.rot, axis_angle(a, float(_vec(step)[0])))
                    self._rebuild()
            self._item_changed(self.get_item("transform"))
            view_gizmo._moved()

        def on_ended(self, *args):
            view_gizmo._payload_seen("ended", args)

        def on_canceled(self, *args):
            view_gizmo._payload_seen("canceled", args)

        def widget_enabled(self):
            pass

        def widget_disabled(self):
            pass

    return Model()


def euler_xyz(rx, ry, rz):
    """XYZ 순서 (X 먼저, USD rotateXYZ 와 같다) 오일러 각 (도) 의 회전 행렬, 행 순서 (v' = v x R)"""
    def rot(axis, deg):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        if axis == 0:
            return [[1, 0, 0], [0, c, s], [0, -s, c]]
        if axis == 1:
            return [[c, 0, -s], [0, 1, 0], [s, 0, c]]
        return [[c, s, 0], [-s, c, 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    return mul(mul(rot(0, rx), rot(1, ry)), rot(2, rz))


class ViewGizmo:
    """뷰포트 하나의 기즈모. 처음 보일 때 한 번 만든다. Kit 판마다 API 가 달라 되는 방법을 찾아 how 에 남긴다"""

    def __init__(self, view, root):
        self._view = view      # _View: owner, release(), _view_id 에 쓴다
        self._root = root      # 기즈모를 담는 sc.Transform
        self._gizmo = None
        self._sub = None
        self._error = None
        self._quiet = False    # 우리가 모델에 쓰는 동안의 알림은 무시
        self._base = None      # 붙일 때 회전 (이것 기준으로 돌아간 만큼)
        self.how = {}

    # -- 만들기 ---------------------------------------------------------------------

    def _make(self):
        if self._gizmo is not None or self._error is not None:
            return self._gizmo
        try:
            import omni.kit.manipulator.transform as kt
            cls = kt.TransformManipulator
            model = _make_model(self)
            with self._root:
                try:
                    g = cls(model=model, size=GIZMO_SIZE)
                    self.how["init"] = "model, size"
                except TypeError:
                    g = cls(model=model)
                    self.how["init"] = "model"
            if g.model is not model:
                g.model = model
            self.how["model_base"] = type(model).__mro__[1].__name__
            self._model = model
            self._gizmo = g
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
            print(f"{TAG} no transform gizmo: {self._error}")
        return self._gizmo

    def _set_operation(self, g, mode: str) -> None:
        """이동 / 회전"""
        try:
            import omni.kit.manipulator.transform as kt
            ops = getattr(kt, "Operation", None)
        except Exception:
            ops = None
        name = {"move": "TRANSLATE", "rotate": "ROTATE"}[mode]
        value = getattr(ops, name, None) if ops is not None else None
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

    # -- 모델 읽기 ------------------------------------------------------------------

    @staticmethod
    def _item(model, name):
        try:
            return model.get_item(name)
        except Exception:
            return None

    def _floats(self, model, name):
        item = self._item(model, name)
        if item is None:
            return None
        try:
            v = list(model.get_as_floats(item))
            return v if v else None
        except Exception:
            return None

    def _rotation(self, model):
        """지금 회전 3x3 (행 순서). transform (16) 이 있으면 그것, 아니면 rotate (XYZ 도)"""
        t = self._floats(model, "transform")
        if t is not None and len(t) == 16:
            rows = [t[0:3], t[4:7], t[8:11]]
            scale = [math.sqrt(sum(c * c for c in r)) or 1.0 for r in rows]
            return [[c / scale[i] for c in rows[i]] for i in range(3)]
        r = self._floats(model, "rotate")
        if r is not None and len(r) >= 3:
            return euler_xyz(r[0], r[1], r[2])
        return None

    def _translate(self, model):
        v = self._floats(model, "translate")
        if v is not None and len(v) >= 3:
            return v[:3]
        t = self._floats(model, "transform")
        return t[12:15] if t is not None and len(t) == 16 else None

    # -- 보이기 / 숨기기 -------------------------------------------------------------

    def show(self, pivot, mode: str) -> None:
        """피봇 자리에 move / rotate 기즈모"""
        g = self._make()
        if g is None:
            return
        self._set_operation(g, mode)
        self._model.place(pivot)
        self._base = None
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

    # -- 움직임 -> owner --------------------------------------------------------------

    def _payload_seen(self, phase: str, args) -> None:
        """진단: 끌기 신호가 오는지, payload 가 무엇인지 (처음 것만 남긴다)"""
        self.how[phase] = self.how.get(phase, 0) + 1
        if args and f"payload_{phase}" not in self.how:
            p = args[-1]
            self.how[f"payload_{phase}"] = (type(p).__name__, [n for n in dir(p) if not n.startswith("_")][:20])

    def _moved(self) -> None:
        """기즈모 끌기 -> owner (피봇 자리, 붙인 뒤 회전)"""
        if not self.visible():
            return
        m = self._model
        self._view.owner._gizmo_changed(self._view, list(m.matrix[12:15]), [list(r) for r in m.rot])

    # -- 진단 / 치우기 ----------------------------------------------------------------

    def probe(self) -> dict:
        """실제 Kit 의 기즈모 API 가 무엇인지"""
        out = {"viewport_id": self._view.owner._view_id(self._view), "error": self._error, "how": dict(self.how),
               "visible": self.visible()}
        try:
            import inspect
            import omni.kit.manipulator.transform as kt
            out["module"] = [n for n in dir(kt) if not n.startswith("_")]
            out["init"] = str(inspect.signature(kt.TransformManipulator.__init__))
        except Exception as e:
            out["module_error"] = f"{type(e).__name__}: {e}"
        g = self._gizmo
        if g is not None:
            model = g.model
            out["model"] = type(model).__name__
            out["items"] = {n: self._floats(model, n) for n in ("transform", "translate", "rotate", "scale")}
            out["attrs"] = [n for n in dir(g) if not n.startswith("_")]
        return out

    def destroy(self) -> None:
        sub, self._sub = self._sub, None
        for name in ("unsubscribe", "destroy"):
            if sub is not None and callable(getattr(sub, name, None)):
                try:
                    getattr(sub, name)()
                except Exception:
                    pass
                break
        g, self._gizmo = self._gizmo, None
        if g is not None and callable(getattr(g, "destroy", None)):
            try:
                g.destroy()
            except Exception:
                pass
        if self._root is not None:
            self._root.clear()
        self._root = None
