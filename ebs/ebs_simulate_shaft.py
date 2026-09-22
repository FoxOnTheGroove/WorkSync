"""카메라와 EBS 사이를 가리는 물체만 골라 비치게 한다."""

import math

from pxr import Gf, Sdf, Usd, UsdShade

from .ebs_simulate_collide import EbsSimulateCollide as Collide
from .ebs_simulate_shared import GRID_CELLS, OURS, SKIN_ROOT

__all__ = ["EbsSimulateShaft", "SHAFT_CULL", "SHAFT_MARGIN", "SHAFT_SLACK",
           "SHAFT_COVER", "SHAFT_BULK", "SHAFT_EPS", "SHAFT_LOUD",
           "GLASS_PATH", "GLASS_CUT"]

SHAFT_CULL   = True
SHAFT_MARGIN = 0.02
SHAFT_SLACK  = 0.05
SHAFT_COVER  = 0.02
SHAFT_BULK   = 3.0
SHAFT_EPS    = 1e-6
SHAFT_LOUD   = True
SHAFT_SHOWN  = 4
SHAFT_ROOM   = 1.6

GLASS_PATH = SKIN_ROOT + "/M_shaft"
BINDING = "material:binding"
GLASS_CUT = 0.5


class EbsSimulateShaft:
    """눈에서 EBS 까지 뻗은 절두체 안에서 EBS 를 덮는 프림에 비치는 머티리얼을 물린다"""

    def __init__(self, sim):
        """물린 것을 적어 둘 자리만 비워 둔다"""
        self._sim = sim
        self._hidden = set()
        self._on = SHAFT_CULL
        self._tally = {}
        self._close = None
        self._grid = None
        self._reach = 0.0
        self._for = ()

    @property
    def on(self) -> bool:
        """컬링이 켜져 있나"""
        return self._on

    @property
    def hidden(self) -> frozenset:
        """지금 비치게 해 둔 경로들"""
        return frozenset(self._hidden)

    def say(self) -> str:
        """마지막 판정에서 어느 관문이 몇 개를 걸렀나"""
        if not self._tally:
            return "shaft: nothing looked at yet"
        return "shaft: " + ", ".join(
            f"{key} {self._tally[key]}" for key in
            ("seen", "close", "cells", "near", "front", "cover", "bulk",
             "proxy", "missed", "hid") if self._tally.get(key))

    def enable(self, on: bool) -> bool:
        """컬링을 켜고 끈다. 끄면 물려 둔 것을 전부 뗀다"""
        self._on = bool(on)
        if not self._on:
            self.restore()
        return self._on

    def restore(self) -> int:
        """물려 둔 것을 전부 뗀다"""
        count = self._clear(self._hidden)
        self._hidden = set()
        self._tally = {}
        return count

    def forget(self) -> None:
        """미리 담아 둔 이웃 목록과 격자를 버린다"""
        self._close = None
        self._grid = None
        self._reach = 0.0
        self._for = ()

    def recull(self) -> int:
        """지금 카메라에서 가리는 것만 비치게 하고 나머지는 되돌린다"""
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
        """절두체를 못 세운 이유를 알리고 물려 둔 것을 뗀다"""
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
        close = self._local(placed[0], boxes)
        rough = self._rough(placed[0], boxes)
        limit = max(self._across(low, high) for low, high in boxes) * SHAFT_BULK
        tally = dict.fromkeys(("seen", "close", "cells", "near", "front",
                               "cover", "bulk"), 0)
        tally["seen"] = self._tally.get("seen", 0)
        tally["close"] = len(close)
        asked = self._cells_in(rough)
        tally["cells"] = len(asked)
        found = set()
        for at in asked:
            path, low, high = close[at]
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

    def _local(self, eye, boxes) -> list:
        """절두체가 닿을 수 있는 이웃만 미리 담아 둔다. 멀어질 때만 다시 담는다"""
        middle = [sum(box[at][i] for box in boxes for at in (0, 1))
                  / (len(boxes) * 2) for i in range(3)]
        span = max(self._across(low, high) for low, high in boxes) * 0.5
        want = math.sqrt(sum((eye[i] - middle[i]) ** 2
                             for i in range(3))) + span
        mine = tuple(sorted(self._spare()))
        if self._close is not None and self._for == mine and self._reach >= want:
            return self._close

        keep = want * SHAFT_ROOM
        spare = self._spare()
        under = tuple(one + "/" for one in spare)
        close, seen = [], 0
        for entry in Collide._stage_boxes(self._sim):
            seen += 1
            path, low, high = entry[0], entry[1], entry[2]
            if path in spare or path.startswith(under):
                continue
            if self._apart(middle, low, high) > keep:
                continue
            close.append((path, low, high))
        self._close = close
        self._grid = self._sift(close)
        self._reach = keep
        self._for = mine
        self._tally["seen"] = seen
        if SHAFT_LOUD:
            cells = len(self._grid[0]) if self._grid else 0
            print(f"[ebs] shaft: kept {len(close)} of {seen} within "
                  f"{keep:.1f} of the EBS, {cells} cell(s)")
        return close

    @staticmethod
    def _sift(close) -> tuple:
        """이웃을 격자 칸에 나눠 담는다. 칸마다 자리 번호만"""
        if not close:
            return {}, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 1
        low = [min(one[1][i] for one in close) for i in range(3)]
        high = [max(one[2][i] for one in close) for i in range(3)]
        spread = max(1, min(GRID_CELLS, int(round(len(close) ** (1.0 / 3.0)))))
        step = [max((high[i] - low[i]) / spread, 1e-6) for i in range(3)]
        grid = {}
        for at, one in enumerate(close):
            for cell in EbsSimulateShaft._cells(one[1], one[2], low, step,
                                                spread):
                grid.setdefault(cell, []).append(at)
        return grid, tuple(low), tuple(step), spread

    @staticmethod
    def _cells(low, high, origin, step, spread) -> list:
        """그 상자가 걸치는 격자 칸들"""
        spans = []
        for i in range(3):
            first = int((low[i] - origin[i]) / step[i])
            last = int((high[i] - origin[i]) / step[i])
            spans.append(range(max(0, min(first, spread - 1)),
                               max(0, min(last, spread - 1)) + 1))
        return [(x, y, z) for x in spans[0] for y in spans[1] for z in spans[2]]

    def _cells_in(self, rough) -> list:
        """절두체 상자가 걸치는 칸에 든 이웃 자리 번호들"""
        if not self._grid or not self._close:
            return list(range(len(self._close or ())))
        grid, origin, step, spread = self._grid
        found = set()
        for cell in self._cells(rough[0], rough[1], origin, step, spread):
            found.update(grid.get(cell, ()))
        return sorted(found)

    @staticmethod
    def _apart(spot, low, high) -> float:
        """그 점에서 상자까지의 거리. 안에 있으면 0"""
        gap = 0.0
        for i in range(3):
            if spot[i] < low[i]:
                gap += (low[i] - spot[i]) ** 2
            elif spot[i] > high[i]:
                gap += (spot[i] - high[i]) ** 2
        return math.sqrt(gap)

    def _spare(self) -> frozenset:
        """절대 안 건드리는 경로들"""
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
        """달라진 것만 물리고 뗀다"""
        self._clear(self._hidden - want)
        wrote = self._mask(want - self._hidden)
        self._hidden = (self._hidden & want) | wrote
        self._tally["hid"] = len(self._hidden)
        if SHAFT_LOUD:
            print("[ebs] " + self.say())
            for path in sorted(self._hidden)[:SHAFT_SHOWN]:
                print(f"[ebs] shaft clears {path}")
        return len(self._hidden)

    def _glass(self, stage):
        """조각을 아예 버리는 머티리얼 하나. 불투명도 0 이 문턱 아래라 정렬을 안 탄다"""
        standing = stage.GetPrimAtPath(GLASS_PATH)
        if standing is not None and standing.IsValid():
            return UsdShade.Material(standing)
        dark = Gf.Vec3f(0.0, 0.0, 0.0)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            material = UsdShade.Material.Define(stage, GLASS_PATH)
            preview = UsdShade.Shader.Define(stage, GLASS_PATH + "/shader")
            preview.CreateIdAttr("UsdPreviewSurface")
            preview.CreateInput("diffuseColor",
                                Sdf.ValueTypeNames.Color3f).Set(dark)
            preview.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.0)
            preview.CreateInput("opacityThreshold",
                                Sdf.ValueTypeNames.Float).Set(GLASS_CUT)
            material.CreateSurfaceOutput().ConnectToSource(
                preview.ConnectableAPI(), "surface")

            shader = UsdShade.Shader.Define(stage, GLASS_PATH + "/mdl")
            shader.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
            shader.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
            shader.CreateInput("diffuse_color_constant",
                               Sdf.ValueTypeNames.Color3f).Set(dark)
            shader.CreateInput("enable_emission",
                               Sdf.ValueTypeNames.Bool).Set(False)
            shader.CreateInput("enable_opacity",
                               Sdf.ValueTypeNames.Bool).Set(True)
            shader.CreateInput("opacity_constant",
                               Sdf.ValueTypeNames.Float).Set(0.0)
            shader.CreateInput("opacity_threshold",
                               Sdf.ValueTypeNames.Float).Set(GLASS_CUT)
            shader.CreateInput("opacity_mode",
                               Sdf.ValueTypeNames.Int).Set(0)
            material.CreateSurfaceOutput("mdl").ConnectToSource(
                shader.ConnectableAPI(), "out")
        return material

    def _mask(self, paths) -> set:
        """비치는 머티리얼을 물린다. 실제로 물린 경로만 돌려준다"""
        stage = self._sim._get_stage()
        if stage is None or not paths:
            return set()
        glass = self._glass(stage)
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
                try:
                    UsdShade.MaterialBindingAPI.Apply(prim).Bind(glass)
                except Exception:
                    missed += 1
                    continue
                done.add(path)
        self._tally["proxy"] = self._tally.get("proxy", 0) + proxy
        self._tally["missed"] = self._tally.get("missed", 0) + missed
        return done

    def _clear(self, paths) -> int:
        """세션 레이어에 물려 둔 머티리얼만 뗀다. 원래 머티리얼은 안 건드린다"""
        stage = self._sim._get_stage()
        if stage is None or not paths:
            return 0
        layer = stage.GetSessionLayer()
        done = 0
        with Sdf.ChangeBlock():
            for path in paths:
                spec = layer.GetPrimAtPath(path)
                if spec is None:
                    continue
                if BINDING in spec.relationships:
                    del spec.relationships[BINDING]
                    done += 1
        return done
