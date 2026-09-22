"""카메라와 EBS 사이를 가리는 물체만 골라 끈다."""

from pxr import Sdf, UsdGeom

from .ebs_simulate_shared import OURS

__all__ = ["EbsSimulateShaft", "SHAFT_CULL", "SHAFT_MARGIN", "SHAFT_SLACK",
           "SHAFT_EPS"]

SHAFT_CULL   = True
SHAFT_MARGIN = 0.05
SHAFT_SLACK  = 0.05
SHAFT_EPS    = 1e-6

VISIBILITY = "visibility"


class EbsSimulateShaft:
    """눈에서 EBS 까지 뻗은 기둥 안에 통째로 든 프림을 세션 레이어에서 끈다"""

    def __init__(self, sim):
        """끈 것을 적어 둘 자리만 비워 둔다"""
        self._sim = sim
        self._hidden = set()
        self._on = SHAFT_CULL

    @property
    def on(self) -> bool:
        """컬링이 켜져 있나"""
        return self._on

    @property
    def hidden(self) -> frozenset:
        """지금 끄고 있는 경로들"""
        return frozenset(self._hidden)

    def enable(self, on: bool) -> bool:
        """컬링을 켜고 끈다. 끄면 끈 것을 전부 되돌린다"""
        self._on = bool(on)
        if not self._on:
            self.restore()
        return self._on

    def restore(self) -> int:
        """끈 것을 전부 도로 켠다"""
        count = self._clear(self._hidden)
        self._hidden = set()
        return count

    def recull(self) -> int:
        """지금 카메라에서 가리는 것만 끄고 나머지는 켠다"""
        if not self._on:
            return 0
        placed = self._sim._camera.placed()
        boxes = self._targets()
        if placed is None or not boxes:
            return self.restore()
        shaft = self._shaft(placed, boxes)
        if shaft is None:
            return self.restore()
        return self._apply(self._blocking(placed, shaft, boxes))

    def _targets(self) -> list:
        """가리면 안 되는 것들의 월드 상자. EBS 와 대상 장비"""
        target = self._sim._target or {}
        boxes = []
        for key in ("ebs", "equipment"):
            prim = target.get(key)
            if prim is None or not prim.IsValid():
                continue
            box = self._sim._world_range(prim)
            if box is not None and not box.IsEmpty():
                low, high = box.GetMin(), box.GetMax()
                boxes.append(((low[0], low[1], low[2]),
                              (high[0], high[1], high[2])))
        return boxes

    @staticmethod
    def _corners(low, high) -> list:
        """상자의 여덟 꼭짓점"""
        return [(low[0] if a else high[0],
                 low[1] if b else high[1],
                 low[2] if c else high[2])
                for a in (0, 1) for b in (0, 1) for c in (0, 1)]

    @staticmethod
    def _camera_space(placed, spot):
        """눈 기준 좌우, 위아래, 앞으로 얼마나"""
        eye, x_cam, y_cam, z_cam = placed
        away = [spot[i] - eye[i] for i in range(3)]
        return (sum(away[i] * x_cam[i] for i in range(3)),
                sum(away[i] * y_cam[i] for i in range(3)),
                -sum(away[i] * z_cam[i] for i in range(3)))

    def _shaft(self, placed, boxes):
        """대상을 감싸는 각도 범위와 앞면 깊이. 못 세우면 None"""
        left = down = deep = None
        right = up = None
        for low, high in boxes:
            for spot in self._corners(low, high):
                across, tall, away = self._camera_space(placed, spot)
                if away <= SHAFT_EPS:
                    return None
                u, v = across / away, tall / away
                left = u if left is None else min(left, u)
                right = u if right is None else max(right, u)
                down = v if down is None else min(down, v)
                up = v if up is None else max(up, v)
                deep = away if deep is None else min(deep, away)
        if deep is None:
            return None
        return (left - SHAFT_MARGIN, right + SHAFT_MARGIN,
                down - SHAFT_MARGIN, up + SHAFT_MARGIN, deep - SHAFT_SLACK)

    def _blocking(self, placed, shaft, boxes) -> set:
        """기둥 안에 통째로 들어온 프림 경로들"""
        from .ebs_simulate_collide import Collide

        rough = self._rough(placed[0], boxes)
        spare = self._spare()
        under = tuple(one + "/" for one in spare)
        found = set()
        for path, low, high, _box, _prim, _chain in Collide._stage_boxes(
                self._sim):
            if path in spare or path.startswith(under):
                continue
            if not self._rough_hit(rough, low, high):
                continue
            if self._swallowed(placed, shaft, low, high):
                found.add(path)
        return found

    def _spare(self) -> frozenset:
        """절대 안 끄는 경로들"""
        target = self._sim._target or {}
        keep = set(OURS)
        for key in ("ebs", "equipment"):
            prim = target.get(key)
            path = self._sim._path_of(prim) if prim is not None else ""
            if path:
                keep.add(path)
        for path in (self._sim._ebs_path_2port, self._sim._ebs_path_3port):
            if path:
                keep.add(path)
        return frozenset(keep)

    def _rough(self, eye, boxes) -> tuple:
        """눈과 대상을 함께 감싸는 상자. 대부분을 여기서 쳐낸다"""
        low = [eye[i] for i in range(3)]
        high = [eye[i] for i in range(3)]
        for one, two in boxes:
            for i in range(3):
                low[i] = min(low[i], one[i])
                high[i] = max(high[i], two[i])
        return tuple(low), tuple(high)

    @staticmethod
    def _rough_hit(rough, low, high) -> bool:
        """그 상자가 눈-대상 상자와 겹치나"""
        one, two = rough
        return all(low[i] <= two[i] and high[i] >= one[i] for i in range(3))

    def _swallowed(self, placed, shaft, low, high) -> bool:
        """여덟 꼭짓점이 모두 기둥 안이고 대상보다 앞인가"""
        left, right, down, up, deep = shaft
        for spot in self._corners(low, high):
            across, tall, away = self._camera_space(placed, spot)
            if away <= SHAFT_EPS or away >= deep:
                return False
            u, v = across / away, tall / away
            if u < left or u > right or v < down or v > up:
                return False
        return True

    def _apply(self, want: set) -> int:
        """달라진 것만 끄고 켠다"""
        gone = self._hidden - want
        fresh = want - self._hidden
        self._clear(gone)
        self._mask(fresh)
        self._hidden = want
        return len(want)

    def _mask(self, paths) -> int:
        """세션 레이어에 invisible 을 한 덩이로 쓴다"""
        return self._author(paths, UsdGeom.Tokens.invisible)

    def _clear(self, paths) -> int:
        """세션 레이어에 써 둔 가시성만 걷는다"""
        return self._author(paths, None)

    def _author(self, paths, token) -> int:
        """세션 레이어의 visibility 를 쓰거나 지운다. 충돌 검사에는 계속 있는 셈 친다"""
        stage = self._sim._get_stage()
        if stage is None or not paths:
            return 0
        layer = stage.GetSessionLayer()
        done = 0
        try:
            with Sdf.ChangeBlock():
                for path in paths:
                    spec = Sdf.CreatePrimInLayer(layer, path)
                    if spec is None:
                        continue
                    if token is None:
                        if VISIBILITY in spec.attributes:
                            del spec.attributes[VISIBILITY]
                        self._sim._visible.pop(path, None)
                    else:
                        attribute = spec.attributes.get(VISIBILITY)
                        if attribute is None:
                            attribute = Sdf.AttributeSpec(
                                spec, VISIBILITY, Sdf.ValueTypeNames.Token)
                        attribute.default = token
                        self._sim._visible[path] = True
                    done += 1
        except Exception as e:
            print(f"[ebs] could not write the shaft visibility: {e}")
        return done
