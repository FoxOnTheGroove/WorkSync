"""카메라와 EBS 사이를 가리는 물체만 골라 끈다."""

import math

from pxr import Usd, UsdGeom

from .ebs_simulate_shared import OURS

__all__ = ["EbsSimulateShaft", "SHAFT_CULL", "SHAFT_MARGIN", "SHAFT_SLACK",
           "SHAFT_COVER", "SHAFT_BULK", "SHAFT_EPS", "SHAFT_LOUD"]

SHAFT_CULL   = True
SHAFT_MARGIN = 0.02
SHAFT_SLACK  = 0.05
SHAFT_COVER  = 0.02
SHAFT_BULK   = 3.0
SHAFT_EPS    = 1e-6
SHAFT_LOUD   = True
SHAFT_SHOWN  = 4


class EbsSimulateShaft:
    """눈에서 EBS 까지 뻗은 기둥 안에서 EBS 를 덮는 프림을 세션 레이어에서 끈다"""

    def __init__(self, sim):
        """끈 것을 적어 둘 자리만 비워 둔다"""
        self._sim = sim
        self._hidden = set()
        self._on = SHAFT_CULL
        self._tally = {}

    @property
    def on(self) -> bool:
        """컬링이 켜져 있나"""
        return self._on

    @property
    def hidden(self) -> frozenset:
        """지금 끄고 있는 경로들"""
        return frozenset(self._hidden)

    def say(self) -> str:
        """마지막 판정에서 어느 관문이 몇 개를 걸렀나"""
        if not self._tally:
            return "shaft: nothing looked at yet"
        return "shaft: " + ", ".join(
            f"{key} {self._tally[key]}" for key in
            ("seen", "near", "front", "cover", "bulk", "proxy", "missed",
             "hid") if self._tally.get(key))

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
        self._tally = {}
        return count

    def recull(self) -> int:
        """지금 카메라에서 가리는 것만 끄고 나머지는 켠다"""
        if not self._on:
            return 0
        placed = self._sim._camera.placed()
        boxes = self._targets()
        if placed is None:
            return self._quit("no camera frame written yet")
        if not boxes:
            return self._quit("EBS and equipment have no world box")
        shaft = self._shaft(placed, boxes)
        if shaft is None:
            return self._quit("EBS sits on or behind the eye")
        return self._apply(self._blocking(placed, shaft, boxes))

    def _quit(self, why: str) -> int:
        """기둥을 못 세운 이유를 알리고 끈 것을 되돌린다"""
        if SHAFT_LOUD:
            print(f"[ebs] shaft: {why}")
        return self.restore()

    @staticmethod
    def _across(low, high) -> float:
        """상자의 대각선 길이"""
        return math.sqrt(sum((high[i] - low[i]) ** 2 for i in range(3)))

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
        """화면에서 대상을 덮고 있는 프림 경로들"""
        from .ebs_simulate_collide import Collide

        rough = self._rough(placed[0], boxes)
        spare = self._spare()
        under = tuple(one + "/" for one in spare)
        limit = max(self._across(low, high) for low, high in boxes) * SHAFT_BULK
        tally = dict.fromkeys(("seen", "near", "front", "cover", "bulk"), 0)
        found = set()
        for path, low, high, _box, _prim, _chain in Collide._stage_boxes(
                self._sim):
            tally["seen"] += 1
            if path in spare or path.startswith(under):
                continue
            if not self._rough_hit(rough, low, high):
                continue
            tally["near"] += 1
            covered = self._covers(placed, shaft, low, high)
            if covered is None:
                continue
            tally["front"] += 1
            if covered < SHAFT_COVER:
                continue
            tally["cover"] += 1
            if self._across(low, high) > limit:
                tally["bulk"] += 1
                continue
            found.add(path)
        self._tally = tally
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

    def _covers(self, placed, shaft, low, high):
        """대상보다 앞이면 대상 화면 넓이의 몇 할을 덮나. 앞이 아니면 None"""
        left, right, down, up, deep = shaft
        one = two = three = four = None
        for spot in self._corners(low, high):
            across, tall, away = self._camera_space(placed, spot)
            if away <= SHAFT_EPS or away >= deep:
                return None
            u, v = across / away, tall / away
            one = u if one is None else min(one, u)
            two = u if two is None else max(two, u)
            three = v if three is None else min(three, v)
            four = v if four is None else max(four, v)
        wide = min(right, two) - max(left, one)
        tall = min(up, four) - max(down, three)
        if wide <= 0.0 or tall <= 0.0:
            return 0.0
        room = (right - left) * (up - down)
        return wide * tall / room if room > SHAFT_EPS else 0.0

    def _apply(self, want: set) -> int:
        """달라진 것만 끄고 켠다"""
        self._clear(self._hidden - want)
        wrote = self._mask(want - self._hidden)
        self._hidden = (self._hidden & want) | wrote
        self._tally["hid"] = len(self._hidden)
        if SHAFT_LOUD:
            print("[ebs] " + self.say())
            for path in sorted(self._hidden)[:SHAFT_SHOWN]:
                print(f"[ebs] shaft hides {path}")
        return len(self._hidden)

    def _mask(self, paths) -> set:
        """끌 수 있는 것만 끄고, 실제로 꺼진 경로를 돌려준다"""
        stage = self._sim._get_stage()
        if stage is None or not paths:
            return set()
        proxy, missed, done = 0, 0, set()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in paths:
                prim = stage.GetPrimAtPath(path)
                if prim is None or not prim.IsValid():
                    missed += 1
                    continue
                if prim.IsInstanceProxy():
                    proxy += 1
                    continue
                imageable = UsdGeom.Imageable(prim)
                if not imageable:
                    missed += 1
                    continue
                try:
                    imageable.CreateVisibilityAttr().Set(
                        UsdGeom.Tokens.invisible)
                except Exception:
                    missed += 1
                    continue
                self._sim._visible[path] = True
                done.add(path)
        self._tally["proxy"] = self._tally.get("proxy", 0) + proxy
        self._tally["missed"] = self._tally.get("missed", 0) + missed
        return done

    def _clear(self, paths) -> int:
        """세션 레이어에 써 둔 가시성만 걷는다. 다른 레이어 의견은 안 건드린다"""
        stage = self._sim._get_stage()
        if stage is None or not paths:
            return 0
        done = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in paths:
                prim = stage.GetPrimAtPath(path)
                self._sim._visible.pop(path, None)
                if prim is None or not prim.IsValid():
                    continue
                imageable = UsdGeom.Imageable(prim)
                if not imageable:
                    continue
                try:
                    attribute = imageable.GetVisibilityAttr()
                    if attribute:
                        attribute.Clear()
                    done += 1
                except Exception:
                    continue
        return done
