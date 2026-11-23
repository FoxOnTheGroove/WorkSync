"""마커 기즈모: Kit 기본 변환 기즈모 클래스 (omni.kit.manipulator.transform.TransformManipulator) 를
마커 층 (뷰포트 하나의 SceneView) 에 직접 띄운다. Kit 선택 / 편집 대상 / 전역 변환 도구 설정과 상관없다.
기즈모 모델의 translate / rotate 가 바뀌면 owner._gizmo_changed(view, 피봇 자리, 붙인 뒤 회전) 로 알린다"""

import math

from .annotation_constant import GIZMO_SIZE, TAG

__all__ = ["ViewGizmo", "euler_xyz"]


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
            with self._root:
                try:
                    g = cls(size=GIZMO_SIZE)
                    self.how["init"] = "size"
                except TypeError:
                    g = cls()
                    self.how["init"] = "()"
            self._sub = g.model.add_item_changed_fn(self._changed)
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
        model = g.model
        self._quiet = True
        try:
            item = self._item(model, "translate")
            if item is not None:
                model.set_floats(item, [float(c) for c in pivot])
                self.how["place"] = "translate"
            rot = self._item(model, "rotate")
            if rot is not None:
                model.set_floats(rot, [0.0, 0.0, 0.0])
            if item is None:
                tf = self._item(model, "transform")
                if tf is not None:
                    model.set_floats(tf, [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0] + [float(c) for c in pivot] + [1.0])
                    self.how["place"] = "transform"
        except Exception as e:
            self.how["place"] = f"failed: {type(e).__name__}: {e}"
        finally:
            self._quiet = False
        self._base = self._rotation(model)
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

    def _changed(self, model, _item) -> None:
        if self._quiet or not self.visible():
            return
        translate = self._translate(model)
        now = self._rotation(model)
        base = self._base
        delta = None
        if now is not None:
            if base is None:
                delta = now
            else:  # 붙일 때 기준: delta = base^T x now (회전 행렬)
                delta = [[sum(base[k][i] * now[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        self._view.owner._gizmo_changed(self._view, translate, delta)

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
