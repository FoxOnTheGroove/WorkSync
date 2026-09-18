import array

from pxr import Usd, UsdGeom, Gf

from .ebs_simulate_shared import *


class EbsSimulateCollide:
    """충돌 기하와 여유 거리를 잰다"""

    @classmethod
    def _attr_value(cls, attr, tc):
        """속성 값. 없으면 빈 목록"""
        if not attr or not attr.IsValid():
            return None
        value = attr.Get(tc)
        if value is None or len(value) == 0:
            samples = attr.GetTimeSamples()
            if samples:
                value = attr.Get(samples[0])
        return value if value is not None and len(value) else None

    @classmethod
    def _bounds_cache(cls, sim):
        """안 움직이는 것에 쓰는 공유 바운드 캐시"""
        if sim._bounds is None:
            sim._bounds = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
                useExtentsHint=True,
            )
        return sim._bounds

    @classmethod
    def _box_patch(cls, local, room, u: int, v: int) -> list:
        """상자로만 잴 때는 그 상자의 옆넓이를 면으로 본다"""
        lo = [max(local.GetMin()[i], room.GetMin()[i]) for i in (u, v)]
        hi = [min(local.GetMax()[i], room.GetMax()[i]) for i in (u, v)]
        if lo[0] > hi[0] or lo[1] > hi[1]:
            return []
        corners = ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))
        return [(corners[0], corners[1], corners[2]),
                (corners[0], corners[2], corners[3])]

    @classmethod
    def _box_point(cls, local, prism, axis: int, outward: int, coord: float, gap: float):
        """그 상자의 중심에서 선을 뽑는다. 프리즘 안으로 눌러 담는다"""
        lo, hi = prism.GetMin(), prism.GetMax()
        point = [0.0, 0.0, 0.0]
        point[axis] = coord + (gap if outward > 0 else -gap)
        for i in range(3):
            if i == axis:
                continue
            middle = (local.GetMin()[i] + local.GetMax()[i]) * 0.5
            point[i] = min(max(middle, lo[i]), hi[i])
        return tuple(point)

    @classmethod
    def _boxed_pairs(cls, sim, stage, ours: list, theirs: list) -> list:
        """삼각형이 없는 프리미티브는 상자 겹침으로 판정한다. 상대가 메시면 면 격자로"""
        found = []
        for a_path, a_box in ours:
            a_mesh = not cls._is_boxed_shape(sim, stage, a_path)
            for b_path, b_box in theirs:
                b_mesh = not cls._is_boxed_shape(sim, stage, b_path)
                if a_mesh and b_mesh:
                    continue
                if a_mesh:
                    hit = cls._mesh_reaches(sim, stage, a_path, b_box)
                elif b_mesh:
                    hit = cls._mesh_reaches(sim, stage, b_path, a_box)
                else:
                    hit = cls._overlaps(a_box, b_box)
                if hit:
                    found.append((a_path, b_path))
        return found

    @classmethod
    def _build_cells(cls, sim, box: Gf.Range3d) -> dict:
        """EBS 세 면 앞의 상자와 사각형. 면마다 하나씩"""
        up_axis = 1 if UsdGeom.GetStageUpAxis(sim._get_stage()) == UsdGeom.Tokens.y else 2
        front_axis = 3 - up_axis
        side_axis = 3 - up_axis - front_axis
        t = cls._probe_depth(box)
        lo, hi = box.GetMin(), box.GetMax()
        cells = {}
        faces = {}

        def make(fixed_axis, outward, row_axis, col_axis):
            """한 면 앞의 얇은 상자 하나와 그 면의 네 점"""
            cmin, cmax = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
            for axis in (row_axis, col_axis):
                cmin[axis], cmax[axis] = lo[axis], hi[axis]
            surface = hi[fixed_axis] if outward > 0 else lo[fixed_axis]
            if outward > 0:
                cmin[fixed_axis], cmax[fixed_axis] = surface, surface + t
            else:
                cmin[fixed_axis], cmax[fixed_axis] = surface - t, surface

            quad = []
            for r_end, c_end in ((0, 0), (0, 1), (1, 1), (1, 0)):
                corner = [0.0, 0.0, 0.0]
                corner[fixed_axis] = surface
                corner[row_axis] = cmax[row_axis] if r_end else cmin[row_axis]
                corner[col_axis] = cmax[col_axis] if c_end else cmin[col_axis]
                quad.append(tuple(corner))
            return ((Gf.Range3d(Gf.Vec3d(*cmin), Gf.Vec3d(*cmax)), quad),
                    (fixed_axis, outward, surface, row_axis, col_axis))

        for face, args in ((FACE_RIGHT,   (side_axis, +1, up_axis, front_axis)),
                           (FACE_LEFT,    (side_axis, -1, up_axis, front_axis)),
                           (FACE_CEILING, (up_axis,   +1, front_axis, side_axis))):
            cells[face], faces[face] = make(*args)
        sim._face_planes = faces
        return cells

    @classmethod
    def _by_face(cls, sim, stage, cache, search, skip, roots, cells, margin):
        """면마다 후보를 나눠 담는다"""
        if roots is None:
            found, visited = cls._gather_nearby(sim, stage, cache, search, skip)
            return [(path, box, FACES) for path, box in found], visited

        sides = tuple(face for face in cells if face != FACE_CEILING)
        beside, visited = cls._gather_nearby(sim, stage, cache, search, skip, roots)
        candidates = [(path, box, sides) for path, box in beside]

        top = cells.get(FACE_CEILING)
        if top is not None:
            above, seen = cls._gather_nearby(
                sim, stage, cache,
                Gf.Range3d(top.GetMin() - margin, top.GetMax() + margin),
                skip)
            visited += seen
            candidates += [(path, box, (FACE_CEILING,)) for path, box in above]
        return candidates, visited

    @classmethod
    def _cells_of(cls, lo, hi, origin, step, spread):
        """그 상자가 걸치는 격자 칸들"""
        spans = []
        for i in range(3):
            first = int((lo[i] - origin[i]) / step[i])
            last = int((hi[i] - origin[i]) / step[i])
            spans.append(range(max(0, min(first, spread - 1)),
                               max(0, min(last, spread - 1)) + 1))
        return [(x, y, z) for x in spans[0] for y in spans[1] for z in spans[2]]

    @classmethod
    def _cross(cls, one, two, edge):
        """두 선분이 만나는 t. 안 만나면 None"""
        rx, ry = two[0] - one[0], two[1] - one[1]
        sx, sy = edge[1][0] - edge[0][0], edge[1][1] - edge[0][1]
        turn = rx * sy - ry * sx
        if abs(turn) <= 1e-12:
            return None
        dx, dy = edge[0][0] - one[0], edge[0][1] - one[1]
        along = (dx * sy - dy * sx) / turn
        across = (dx * ry - dy * rx) / turn
        if (-OVERLAP_EPS <= along <= 1.0 + OVERLAP_EPS
                and -OVERLAP_EPS <= across <= 1.0 + OVERLAP_EPS):
            return min(max(along, 0.0), 1.0)
        return None

    @classmethod
    def _cube_local(cls, prim):
        """Cube 프림의 점과 면"""
        if str(prim.GetTypeName()) != CUBE_TYPE:
            return None
        try:
            size = UsdGeom.Cube(prim).GetSizeAttr().Get(NOW)
        except Exception:
            size = None
        half = (float(size) if size else 2.0) * 0.5
        points = [(x * half, y * half, z * half)
                  for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
        return points, [4] * 6, [i for quad in CUBE_QUADS for i in quad]

    @classmethod
    def _ebs_bound(cls, sim, prim: Usd.Prim):
        """EBS 의 월드 상자"""
        path = sim._path_of(prim)
        if sim._ebs_box is not None and sim._ebs_box[0] == path:
            return sim._ebs_box[1]
        exact = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        ).ComputeWorldBound(prim)

        hinted = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,
        ).ComputeWorldBound(prim).ComputeAlignedRange()
        measured = exact.ComputeAlignedRange()
        if not hinted.IsEmpty() and not measured.IsEmpty():
            slack = max(abs(hinted.GetMin()[i] - measured.GetMin()[i]) for i in range(3))
            slack = max(slack, max(abs(hinted.GetMax()[i] - measured.GetMax()[i])
                                   for i in range(3)))
            span = max(measured.GetMax()[i] - measured.GetMin()[i] for i in range(3))
            if span > 0 and slack > span * 0.01:
                sim._note(f"the EBS extentsHint is off by {slack:.3f}, "
                           f"using the measured bound")
        if path:
            sim._ebs_box = (path, exact)
        return exact

    @classmethod
    def _enter(cls, one, two, triangle):
        """선분이 그 삼각형 안으로 처음 들어가는 t. 스치지도 않으면 None"""
        turn = ((triangle[1][0] - triangle[0][0]) * (triangle[2][1] - triangle[0][1])
                - (triangle[1][1] - triangle[0][1]) * (triangle[2][0] - triangle[0][0]))
        if abs(turn) <= 1e-12:
            return None
        way = -1.0 if turn > 0 else 1.0
        low, high = 0.0, 1.0
        for at in range(3):
            a, b = triangle[at], triangle[(at + 1) % 3]
            nx, ny = (b[1] - a[1]) * way, (a[0] - b[0]) * way
            here = nx * (one[0] - a[0]) + ny * (one[1] - a[1])
            step = nx * (two[0] - one[0]) + ny * (two[1] - one[1])
            if abs(step) <= 1e-12:
                if here < -OVERLAP_EPS:
                    return None
                continue
            hit = -here / step
            if step > 0:
                low = max(low, hit)
            else:
                high = min(high, hit)
            if low > high:
                return None
        return low

    @classmethod
    def _face_grid(cls, sim, path: str, data):
        """면마다 로컬 상자를 재서 격자에 담는다"""
        made = sim._faces.get(path)
        if made is not None:
            return made
        points, counts, indices = data
        start, size = array.array("i"), array.array("i")
        lows = [array.array("d") for _ in range(3)]
        highs = [array.array("d") for _ in range(3)]
        cursor, total = 0, len(indices)
        for count in counts:
            end = cursor + count
            if count < 3 or end > total:
                cursor = end
                continue
            corner = points[indices[cursor]]
            lo = [corner[0], corner[1], corner[2]]
            hi = [corner[0], corner[1], corner[2]]
            for k in range(cursor + 1, end):
                corner = points[indices[k]]
                for i in range(3):
                    v = corner[i]
                    if v < lo[i]:
                        lo[i] = v
                    elif v > hi[i]:
                        hi[i] = v
            start.append(cursor)
            size.append(count)
            for i in range(3):
                lows[i].append(lo[i])
                highs[i].append(hi[i])
            cursor = end

        faces = len(start)
        if not faces:
            made = (start, size, lows, highs, (0.0, 0.0, 0.0),
                    (1.0, 1.0, 1.0), 1, {})
            sim._faces[path] = made
            return made
        origin = tuple(min(lows[i]) for i in range(3))
        far = tuple(max(highs[i]) for i in range(3))
        spread = max(1, min(GRID_CELLS, int(round(faces ** (1.0 / 3.0)))))
        step = tuple(max((far[i] - origin[i]) / spread, 1e-9) for i in range(3))
        grid = {}
        for at in range(faces):
            for key in cls._cells_of([lows[i][at] for i in range(3)],
                                      [highs[i][at] for i in range(3)],
                                      origin, step, spread):
                grid.setdefault(key, []).append(at)
        made = (start, size, lows, highs, origin, step, spread, grid)
        sim._faces[path] = made
        return made

    @classmethod
    def _face_marks(cls, sim, local_box, to_world, cells: dict,
                    distances: dict) -> list:
        """면마다 상태·거리·선 두 끝"""
        stage = sim._get_stage()
        try:
            per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        except Exception:
            per_unit = 1.0
        lo, hi = local_box.GetMin(), local_box.GetMax()
        middle = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        up_axis = (sim._face_planes.get(FACE_CEILING) or (2,))[0]
        front_axis = 3 - up_axis

        def world(point):
            """로컬 점을 월드로"""
            got = to_world.Transform(Gf.Vec3d(*point))
            return (got[0], got[1], got[2])

        marks = []
        for face in FACES:
            plane = sim._face_planes.get(face)
            if plane is None:
                continue
            axis, outward, coord, _, _ = plane
            surface = list(middle)
            surface[axis] = coord
            surface[front_axis] = (lo if LEAD_FRONT < 0 else hi)[front_axis]

            least = sim._min_gap.get(face, 0.0)
            way = cls._outward_way(surface, axis, outward, world)
            blank = {"face": face, "distance": None, "name": "", "way": way,
                     "min_gap": least, "at": world(surface), "stale": False,
                     "from": None, "to": None, "lead": None, "spot": None,
                     "tick": None}
            hit = bool(cells.get(face))
            found = distances.get(face) or {}
            at = found.get("at")
            reach = found.get("distance")
            if at is None or reach is None:
                blank["state"] = STATE_CLASH if hit else STATE_CLEAR
                if hit:
                    blank["name"] = sim.owner_name(sim._blockers.get(face, ""))
                marks.append(blank)
                continue
            start, end = list(at), list(at)
            start[axis] = coord
            end[axis] = coord + (reach if outward > 0 else -reach)
            spot = list(end)
            lead, tick = None, None
            if face in LEAD_FACES:
                start = list(middle)
                start[front_axis] = (lo if LEAD_FRONT < 0 else hi)[front_axis]
                start[axis] = coord
                end = list(start)
                end[axis] = coord + (reach if outward > 0 else -reach)
                turn_axis = 3 - axis - front_axis
                corner = list(end)
                corner[front_axis] = spot[front_axis]
                patch = cls._lead_patch(sim, [end, corner, spot], to_world,
                                         axis, end[axis])
                walk = cls._lead_path(end, spot, turn_axis, front_axis,
                                       axis, patch)
                lead = [world(point) for point in walk]
                tick = cls._tick_way(end, axis, world)
            near, far = world(start), world(end)
            span = (sum((far[i] - near[i]) ** 2 for i in range(3)) ** 0.5) * per_unit
            gap = -span if reach < 0 else span
            marks.append({
                "face": face, "tick": tick, "way": way, "stale": False,
                "state": (STATE_CLASH if hit else
                          STATE_TIGHT if gap < least else STATE_CLEAR),
                "distance": gap, "min_gap": least,
                "name": sim.owner_name(found.get("prim", "")
                                        or sim._blockers.get(face, "")),
                "at": tuple((near[i] + far[i]) * 0.5 for i in range(3)),
                "from": near, "to": far, "lead": lead, "spot": world(spot),
            })
        return marks

    @classmethod
    def _face_prism(cls, box: Gf.Range3d, axis: int, outward: int, coord: float,
                    reach: float) -> Gf.Range3d:
        """그 면에서 바깥으로 reach 만큼 뻗은 직육면체"""
        lo = [box.GetMin()[i] for i in range(3)]
        hi = [box.GetMax()[i] for i in range(3)]
        if outward > 0:
            lo[axis], hi[axis] = coord, coord + reach
        else:
            lo[axis], hi[axis] = coord - reach, coord
        return Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))

    @classmethod
    def _faces_near(cls, sim, path: str, data, near) -> list:
        """면 격자에서 그 상자 근처 면 번호들 (1차 필터)"""
        start, size, lows, highs, origin, step, spread, grid = \
            cls._face_grid(sim, path, data)
        if not grid:
            return []
        (lx, ly, lz), (hx, hy, hz) = near
        seen = set()
        for key in cls._cells_of((lx, ly, lz), (hx, hy, hz),
                                  origin, step, spread):
            seen.update(grid.get(key, ()))
        lo0, lo1, lo2 = lows
        hi0, hi1, hi2 = highs
        return [at for at in seen
                if lo0[at] <= hx and hi0[at] >= lx and lo1[at] <= hy
                and hi1[at] >= ly and lo2[at] <= hz and hi2[at] >= lz]

    @classmethod
    def _flat_gap(cls, triangles, prism, axis: int, outward: int, coord: float,
                  slack: float = OVERLAP_EPS, parts: list = None,
                  deep: bool = False):
        """한 덩어리 안에서 같은 높이인 면들을 한 면으로 보고 그 중점을 찍는다"""
        lo, hi = prism.GetMin(), prism.GetMax()
        best, seed = None, -1
        for at, triangle in enumerate(triangles):
            found = cls._triangle_gap(triangle, prism, axis, outward,
                                              coord, deep)
            if found is not None and (best is None
                                      or (found[0] > best if deep
                                          else found[0] < best)):
                best, seed = found[0], at
        if best is None:
            return None

        picked = {}
        for at, triangle in enumerate(triangles):
            keep = []
            for vertex in triangle:
                inside = all(lo[i] - OVERLAP_EPS <= vertex[i] <= hi[i] + OVERLAP_EPS
                             for i in range(3) if i != axis)
                if not inside:
                    continue
                if deep:
                    gap = (coord - vertex[axis]) if outward > 0 else (vertex[axis] - coord)
                else:
                    gap = (vertex[axis] - coord) if outward > 0 else (coord - vertex[axis])
                if gap >= 0 and abs(gap - best) <= slack:
                    keep.append(vertex)
            if keep:
                picked[at] = keep
        if seed not in picked:
            return None

        if parts is None:
            parts = cls._mesh_parts(triangles)
        here = parts[seed]
        mins, maxs = [None, None, None], [None, None, None]
        for at, kept in picked.items():
            if parts[at] != here:
                continue
            for vertex in kept:
                for i in range(3):
                    if i == axis:
                        continue
                    mins[i] = vertex[i] if mins[i] is None else min(mins[i], vertex[i])
                    maxs[i] = vertex[i] if maxs[i] is None else max(maxs[i], vertex[i])
        if mins[0] is None and mins[1] is None and mins[2] is None:
            return None
        point = [0.0, 0.0, 0.0]
        for i in range(3):
            if i != axis:
                point[i] = min(max((mins[i] + maxs[i]) * 0.5, lo[i]), hi[i])
        way = -outward if deep else outward
        point[axis] = coord + (best if way > 0 else -best)
        return best, tuple(point)

    @classmethod
    def _flat_slack(cls, local, axis: int) -> float:
        """'같은 평면'으로 볼 깊이 오차"""
        span = max(local.GetMax()[i] - local.GetMin()[i]
                   for i in range(3) if i != axis)
        return max(span * FLAT_TOL, OVERLAP_EPS)

    @classmethod
    def _forget_ebs(cls, sim, paths=()) -> None:
        """EBS 상자 캐시를 버린다"""
        sim._ebs_box = None
        cls._forget_leaves(sim, paths)
        for path in paths:
            sim._visible.pop(path, None)

    @classmethod
    def _forget_leaves(cls, sim, roots=()) -> None:
        """그 자리를 낀 잎 캐시를 버린다"""
        for root in roots:
            for path in [p for p in sim._leaves
                         if p == root or p.startswith(root + "/")
                         or root.startswith(p + "/")]:
                del sim._leaves[path]

    @classmethod
    def _forget_triangles(cls, sim, prim: Usd.Prim) -> None:
        """그 프림의 월드 삼각형 캐시를 버린다"""
        if prim is None or not prim.IsValid():
            return
        root = str(prim.GetPath())
        for path in [p for p in sim._triangles
                     if p == root or p.startswith(root + "/")]:
            del sim._triangles[path]

    @classmethod
    def _from_index(cls, sim, stage, cache, search: Gf.Range3d, skip: list) -> tuple:
        """그 상자 목록에서 검색 상자에 걸리는 것만 꺼낸다"""
        inside, visited = cls._index_inside(sim, cache, search, skip)
        found = []
        for prim in inside:
            got, seen = cls._gather_nearby(sim, stage, cache, search, skip, [prim])
            found.extend(got)
            visited += seen
        return found, visited

    @classmethod
    def _gap_along(cls, box, axis: int, outward: int, coord: float,
                   deep: bool = False) -> "float | None":
        """면에서 상자까지의 거리. deep 이면 안으로 파고든 깊이"""
        if deep:
            depth = (coord - box.GetMin()[axis] if outward > 0
                     else box.GetMax()[axis] - coord)
            return None if depth <= 0 else depth
        if outward > 0:
            gap = box.GetMin()[axis] - coord
        else:
            gap = coord - box.GetMax()[axis]
        return None if gap < 0 else gap

    @classmethod
    def _gather_nearby(cls, sim, stage, cache, search: Gf.Range3d, skip: list,
                       roots: list = None) -> tuple:
        """검색 상자 근처의 메시들. roots 를 주면 그 아래만 훑는다"""
        if roots is None:
            return cls._from_index(sim, stage, cache, search, skip)
        found, visited = [], 0
        skip_exact = frozenset(skip)
        skip_under = tuple(s + "/" for s in skip)
        for root in roots:
            for path, box, prim, chain in cls._subtree_leaves(sim, stage, cache, root):
                visited += 1
                if path in skip_exact or (skip_under
                                          and path.startswith(skip_under)):
                    continue
                if not cls._overlaps(box, search):
                    continue
                if any(not sim._is_visible(one, where) for one, where in chain):
                    continue
                if not sim._is_visible(prim, path):
                    continue
                found.append((path, box))
        return found, visited

    @classmethod
    def _grid_of(cls, items: list, box: Gf.Range3d) -> tuple:
        """삼각형들을 칸에 나눠 담은 격자"""
        low, high = box.GetMin(), box.GetMax()
        origin = (low[0], low[1], low[2])
        size = [max(high[i] - origin[i], 1e-9) for i in range(3)]
        spread = max(1, min(GRID_CELLS, int(round(len(items) ** (1.0 / 3.0)))))
        step = [size[i] / spread for i in range(3)]
        grid = {}
        for index, (_, _, lo, hi) in enumerate(items):
            for key in cls._cells_of(lo, hi, origin, step, spread):
                grid.setdefault(key, []).append(index)
        return grid, origin, step, spread

    @classmethod
    def _index_inside(cls, sim, cache, search: Gf.Range3d, skip: list) -> tuple:
        """색인에서 검색 상자에 걸리고 보이는 프림들. 몇 개를 봤는지도"""
        skip_exact = frozenset(skip)
        skip_under = tuple(s + "/" for s in skip)
        low, high = search.GetMin(), search.GetMax()
        lo0, lo1, lo2 = low[0], low[1], low[2]
        hi0, hi1, hi2 = high[0], high[1], high[2]
        eps = OVERLAP_EPS
        inside, visited = [], 0
        for path, lo, hi, box, prim, chain in cls._stage_boxes(sim, cache):
            if path in skip_exact or (skip_under and path.startswith(skip_under)):
                continue
            visited += 1
            if (min(hi[0], hi0) - max(lo[0], lo0) <= eps
                    or min(hi[1], hi1) - max(lo[1], lo1) <= eps
                    or min(hi[2], hi2) - max(lo[2], lo2) <= eps):
                continue
            if any(not sim._is_visible(one, where) for one, where in chain):
                continue
            inside.append(prim)
        return inside, visited

    @classmethod
    def _is_boxed_shape(cls, sim, stage, path: str) -> bool:
        """삼각형이 하나도 안 나오는 조각인가"""
        cached = sim._triangles.get(path)
        if cached is not None:
            return not cached
        try:
            return cls._mesh_local(sim, stage, path) is None
        except AttributeError:
            return False

    @classmethod
    def _lead_patch(cls, sim, walk, to_world, axis: int, plane: float) -> list:
        """안내선이 지날 자리 언저리의 메시를 스테이지 전체에서 훑어 잘라 둔다"""
        stage = sim._get_stage()
        if stage is None or not walk:
            return []
        lo = [min(point[i] for point in walk) - LEAD_ROOM for i in range(3)]
        hi = [max(point[i] for point in walk) + LEAD_ROOM for i in range(3)]
        room = Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))
        skip = [str(prim.GetPath()) for prim in
                (sim._target.get("ebs"), sim._target.get("equipment"))
                if prim is not None and prim.IsValid()]
        found, _ = cls._gather_nearby(
            sim, stage, cls._bounds_cache(sim),
            Gf.BBox3d(room, to_world).ComputeAlignedRange(), skip)
        inverse = to_world.GetInverse()
        across = []
        for path, box in found:
            local = Gf.BBox3d(box, inverse).ComputeAlignedRange()
            if (local.GetMin()[axis] - LEAD_TOL <= plane
                    <= local.GetMax()[axis] + LEAD_TOL):
                across.append((path, local))
        return cls._same_patch(sim, stage, across, room, inverse, axis, plane)

    @classmethod
    def _lead_path(cls, end, spot, turn_axis: int, front_axis: int, axis: int,
                   patch) -> list:
        """선 끝에서 잰 자리로. 가다가 아무 메시에나 닿으면 거기서 멈춘다"""
        corner = list(end)
        corner[front_axis] = spot[front_axis]
        legs = [corner]
        if abs(spot[turn_axis] - corner[turn_axis]) > LEAD_TOL:
            legs.append(list(spot))
        path, here = [], list(end)
        for leg in legs:
            stop = cls._stop_at(here, leg, axis, patch)
            if stop is None:
                path.append(leg)
                here = leg
                continue
            if any(abs(stop[i] - here[i]) > LEAD_TOL for i in range(3)):
                path.append(stop)
            break
        return path

    @classmethod
    def _meetings(cls, sim, stage, mine: list, theirs: list, whole: Gf.Range3d,
                  known_pairs: list) -> tuple:
        """장비 메시마다 EBS 와 겹치는 데만 본다"""
        grid, origin, step, spread = cls._grid_of(mine, whole)
        known = {path for _, path in known_pairs}
        pairs, tests, read = [], 0, 0
        for path, box in theirs:
            if path in known or len(pairs) + len(known) >= CLASH_MARKS:
                continue
            region = Gf.Range3d.GetIntersection(box, whole)
            if region.IsEmpty():
                continue
            near = set()
            for key in cls._cells_of(region.GetMin(), region.GetMax(),
                                      origin, step, spread):
                near.update(grid.get(key, ()))
            if not near:
                continue
            yours = cls._triangles_reaching(sim, stage, path, region)
            read += len(yours)
            if not yours:
                continue
            met, spent = cls._meets_mesh(mine, yours, near, grid, origin,
                                          step, spread)
            tests += spent
            if met:
                pairs.append((met, path))
        return pairs, tests, read

    @classmethod
    def _meets_mesh(cls, mine: list, yours: list, near, grid, origin, step,
                    spread) -> tuple:
        """그 장비 메시가 EBS 를 뚫나. 처음 만난 EBS 메시 경로와 검사 횟수"""
        tests = 0
        close = list(near) if len(near) <= MEET_WIDE else None
        for _, triangle, lo, hi in yours:
            spots = close
            if spots is None:
                spots = set()
                for key in cls._cells_of(lo, hi, origin, step, spread):
                    spots.update(grid.get(key, ()))
            for index in spots:
                ebs_path, other, other_lo, other_hi = mine[index]
                if (lo[0] > other_hi[0] or hi[0] < other_lo[0]
                        or lo[1] > other_hi[1] or hi[1] < other_lo[1]
                        or lo[2] > other_hi[2] or hi[2] < other_lo[2]):
                    continue
                tests += 1
                if cls._triangles_meet(triangle, other):
                    return ebs_path, tests
        return "", tests

    @classmethod
    def _mesh_box(cls, sim, stage, path: str):
        """그 메시의 점으로 직접 잰 상자. 삼각형이 없으면 None"""
        triangles = cls._mesh_triangles(sim, stage, path)
        if not triangles:
            return None
        _, first, _ = triangles[0]
        lo = [first[0], first[1], first[2]]
        hi = [first[0], first[1], first[2]]
        for _, low, high in triangles:
            for i in range(3):
                if low[i] < lo[i]:
                    lo[i] = low[i]
                if high[i] > hi[i]:
                    hi[i] = high[i]
        return lo, hi

    @classmethod
    def _mesh_local(cls, sim, stage, path: str):
        """메시의 점과 면 색인. 메시가 아니면 None"""
        if path in sim._local:
            return sim._local[path]
        prim = stage.GetPrimAtPath(path) if stage else None
        mesh = UsdGeom.Mesh(prim) if prim and prim.IsValid() else None
        data = None
        if mesh:
            tc = Usd.TimeCode.Default()
            points = cls._attr_value(mesh.GetPointsAttr(), tc)
            counts = cls._attr_value(mesh.GetFaceVertexCountsAttr(), tc)
            indices = cls._attr_value(mesh.GetFaceVertexIndicesAttr(), tc)
            if points is None or counts is None or indices is None:
                sim._boxed.setdefault("no point data", []).append(path)
            else:
                data = (points, counts, indices)
        elif prim and prim.IsValid():
            data = cls._cube_local(prim)
            if data is None:
                sim._boxed.setdefault(f"a {prim.GetTypeName()}",
                                       []).append(path)
        sim._local[path] = data
        return data

    @classmethod
    def _mesh_parts(cls, triangles) -> list:
        """삼각형마다 몇 번째 덩어리인지. 꼭짓점을 나눠 쓰면 이어진 것으로 본다"""
        joins = {}
        for at, triangle in enumerate(triangles):
            for vertex in triangle:
                joins.setdefault(tuple(round(v, 9) for v in vertex),
                                 []).append(at)
        parts = [-1] * len(triangles)
        part = 0
        for start in range(len(triangles)):
            if parts[start] >= 0:
                continue
            waiting = [start]
            parts[start] = part
            while waiting:
                at = waiting.pop()
                for vertex in triangles[at]:
                    for other in joins.get(tuple(round(v, 9) for v in vertex), ()):
                        if parts[other] < 0:
                            parts[other] = part
                            waiting.append(other)
            part += 1
        return parts

    @classmethod
    def _mesh_reaches(cls, sim, stage, path: str, piece_box) -> bool:
        """메시 path 의 표면이 piece_box 에 실제로 닿는가"""
        data = cls._mesh_local(sim, stage, path)
        to_world = cls._to_world(stage, path)
        if not data or to_world is None:
            return False
        near = cls._pulled_back(piece_box, to_world)
        if near is None:
            return False
        candidates = cls._faces_near(sim, path, data, near)
        if not candidates:
            return False
        points, counts, indices = data
        start, size = sim._faces[path][0], sim._faces[path][1]
        box = Gf.Range3d(Gf.Vec3d(*near[0]), Gf.Vec3d(*near[1]))
        for at in candidates:
            first, count = start[at], size[at]
            fan = [points[indices[first + k]] for k in range(count)]
            for k in range(1, count - 1):
                triangle = (fan[0], fan[k], fan[k + 1])
                if cls._triangle_hits_box(triangle, box):
                    return True
        return False

    @classmethod
    def _mesh_triangles(cls, sim, stage, path: str) -> list:
        """그 메시의 월드 삼각형 전부"""
        if path in sim._triangles:
            return sim._triangles[path]
        triangles = []
        data = cls._mesh_local(sim, stage, path)
        to_world = cls._to_world(stage, path)
        if data and to_world is not None:
            points, counts, indices = data
            world = [to_world.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in points]
            cursor = 0
            for count in counts:
                if count >= 3 and cursor + count <= len(indices):
                    fan = [world[indices[cursor + k]] for k in range(count)]
                    for k in range(1, count - 1):
                        triangles.append(cls._with_box(
                            (fan[0], fan[k], fan[k + 1])))
                cursor += count
        sim._triangles[path] = triangles
        return triangles

    @classmethod
    def _missed(cls, sim, theirs: list, pairs: list, world_box) -> None:
        """EBS 상자 안에 들어와 있는데 표면이 안 만난 조각을 센다"""
        if len(pairs) >= CLASH_MARKS:
            sim._note(f"interference stopped at the {CLASH_MARKS} piece cap - "
                       f"there may be more")
        met = {path for _, path in pairs}
        deep = []
        for path, box in theirs:
            if path in met:
                continue
            shared = Gf.Range3d.GetIntersection(box, world_box)
            if shared.IsEmpty():
                continue
            lo, hi = box.GetMin(), box.GetMax()
            span = [hi[i] - lo[i] for i in range(3)]
            near, far = shared.GetMin(), shared.GetMax()
            covered = [(far[i] - near[i]) / span[i] if span[i] > 1e-9 else 1.0
                       for i in range(3)]
            if min(covered) > 0.5:
                deep.append(path.rsplit("/", 1)[-1])
        if deep:
            sim._note(f"{len(deep)} piece(s) sit well inside the EBS box but "
                       f"never touch its surface: " + ", ".join(deep[:6])
                       + (" ..." if len(deep) > 6 else ""))

    @classmethod
    def _mover(cls, sim, to_world, points, path: str):
        """로컬 점을 월드로"""
        try:
            r0, r1, r2, r3 = (to_world.GetRow(0), to_world.GetRow(1),
                              to_world.GetRow(2), to_world.GetRow(3))
            a00, a01, a02 = r0[0], r0[1], r0[2]
            a10, a11, a12 = r1[0], r1[1], r1[2]
            a20, a21, a22 = r2[0], r2[1], r2[2]
            a30, a31, a32 = r3[0], r3[1], r3[2]
            if points:
                x, y, z = points[0][0], points[0][1], points[0][2]
                mine = (x * a00 + y * a10 + z * a20 + a30,
                        x * a01 + y * a11 + z * a21 + a31,
                        x * a02 + y * a12 + z * a22 + a32)
                theirs = to_world.Transform(Gf.Vec3d(x, y, z))
                span = max(abs(theirs[i]) for i in range(3)) or 1.0
                if all(abs(mine[i] - theirs[i]) <= span * 1e-9 for i in range(3)):
                    return lambda p: (p[0] * a00 + p[1] * a10 + p[2] * a20 + a30,
                                      p[0] * a01 + p[1] * a11 + p[2] * a21 + a31,
                                      p[0] * a02 + p[1] * a12 + p[2] * a22 + a32)
                sim._boxed.setdefault("an unexpected transform", []).append(path)
        except Exception:
            pass
        return lambda p: to_world.Transform(Gf.Vec3d(p[0], p[1], p[2]))

    @classmethod
    def _moving_cache(cls, ):
        """움직이는 EBS 에 쓰는 바운드 캐시"""
        return UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,
        )

    @classmethod
    def _nearest_in_prism(cls, sim, stage, candidates, prism, to_world,
                          axis, outward, coord, deep: bool = False):
        """가장 가까운 것 하나. deep 이면 가장 깊이 파고든 것 하나"""
        if not candidates:
            return None

        inverse = to_world.GetInverse()
        bounded = []
        for path, box in candidates:
            local = Gf.BBox3d(box, inverse).ComputeAlignedRange()
            gap = cls._gap_along(local, axis, outward, coord, deep)
            if gap is not None:
                bounded.append((gap, path, local))
        bounded.sort(key=lambda item: item[0], reverse=deep)

        best, best_path, best_at = None, "", None
        for gap, path, local in bounded:
            if best is not None and (gap <= best if deep else gap >= best):
                break
            way = -outward if deep else outward
            if sim._precision != PRECISION_TRI:
                best, best_path = gap, path
                best_at = cls._box_point(local, prism, axis, way, coord, gap)
                continue
            triangles = cls._mesh_triangles(sim, stage, path)
            if not triangles:
                best, best_path = gap, path
                best_at = cls._box_point(local, prism, axis, way, coord, gap)
                continue
            local_tris = [[inverse.Transform(Gf.Vec3d(*v)) for v in triangle]
                         for triangle, _, _ in triangles]
            found = cls._flat_gap(local_tris, prism, axis, outward, coord,
                                   cls._flat_slack(local, axis),
                                   cls._parts_of(sim, path, local_tris), deep)
            if found is not None and (best is None
                                      or (found[0] > best if deep
                                          else found[0] < best)):
                best, best_path, best_at = found[0], path, found[1]
        if best is None:
            return None
        return {"distance": max(best, 0.0), "prim": best_path, "at": best_at}

    @classmethod
    def _outward_way(cls, surface, axis: int, outward: int, world) -> tuple:
        """그 면이 바라보는 바깥 방향. 월드 단위 벡터"""
        ahead = [surface[i] + (1.0 if i == axis else 0.0) for i in range(3)]
        here, there = world(surface), world(ahead)
        step = [(there[i] - here[i]) * (1.0 if outward > 0 else -1.0)
                for i in range(3)]
        size = sum(one * one for one in step) ** 0.5
        return tuple(one / size for one in step) if size else None

    @classmethod
    def _overlaps(cls, a: Gf.Range3d, b: Gf.Range3d) -> bool:
        """두 상자가 겹치나"""
        overlap = Gf.Range3d.GetIntersection(a, b)
        if overlap.IsEmpty():
            return False
        extent = overlap.GetMax() - overlap.GetMin()
        return all(extent[i] > OVERLAP_EPS for i in range(3))

    @classmethod
    def _parts_of(cls, sim, path: str, triangles) -> list:
        """그 메시의 덩어리 표"""
        found = sim._parts.get(path)
        if found is None or len(found) != len(triangles):
            found = cls._mesh_parts(triangles)
            sim._parts[path] = found
        return found

    @classmethod
    def _probe_depth(cls, box: Gf.Range3d) -> float:
        """닿았다고 볼 깊이. EBS 최장변 대비 PROBE_RATIO"""
        longest = max(box.GetMax()[i] - box.GetMin()[i] for i in range(3))
        return max(longest * PROBE_RATIO, 1e-6)

    @classmethod
    def _pulled_back(cls, box: Gf.Range3d, to_world):
        """월드 상자를 메시 로컬로 끌어온다"""
        try:
            inverse = to_world.GetInverse()
        except Exception:
            return None
        lo, hi = box.GetMin(), box.GetMax()
        corners = [inverse.Transform(Gf.Vec3d(x, y, z))
                   for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                   for z in (lo[2], hi[2])]
        return (tuple(min(c[i] for c in corners) for i in range(3)),
                tuple(max(c[i] for c in corners) for i in range(3)))

    @classmethod
    def _reach_box(cls, sim, ebs_prim):
        """collide 가 뒤지는 범위. 세 면의 프리즘과 EBS 상자를 합친 것"""
        bbox = cls._ebs_bound(sim, ebs_prim)
        whole = bbox.ComputeAlignedRange()
        if whole.IsEmpty():
            return None
        local, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local.IsEmpty():
            return None
        cls._build_cells(sim, local)
        reach = max(local.GetMax()[i] - local.GetMin()[i]
                    for i in range(3)) * REACH_RATIO
        boxes = [whole]
        for axis, outward, coord, _, _ in sim._face_planes.values():
            prism = cls._face_prism(local, axis, outward, coord, reach)
            boxes.append(Gf.BBox3d(prism, to_world).ComputeAlignedRange())
        return cls._union(boxes)

    @classmethod
    def _reach_by_face(cls, sim, stage, cache, skip, roots, wanted) -> dict:
        """면마다 거리 잴 후보를 모은다. 천장만 스테이지 전체"""
        if not wanted:
            return {}
        if roots is None:
            whole = cls._union([one[1] for one in wanted.values()])
            found, _ = cls._gather_nearby(sim, stage, cache, whole, skip)
            return {face: found for face in wanted}

        by_face = {}
        sides = {face: one for face, one in wanted.items() if face != FACE_CEILING}
        if sides:
            whole = cls._union([one[1] for one in sides.values()])
            found, _ = cls._gather_nearby(sim, stage, cache, whole, skip, roots)
            by_face.update({face: found for face in sides})
        top = wanted.get(FACE_CEILING)
        if top is not None:
            found, _ = cls._gather_nearby(sim, stage, cache, top[1], skip)
            by_face[FACE_CEILING] = found
        return by_face

    @classmethod
    def _same_patch(cls, sim, stage, nearby, room, inverse, axis: int,
                    plane: float) -> list:
        """그 깊이에서 잘라낸 모양들. 평평하면 면, 걸치면 단면 선, 점이 없으면 상자"""
        lo, hi = room.GetMin(), room.GetMax()
        u, v = [i for i in range(3) if i != axis]
        patch = []
        for path, local in nearby:
            if len(patch) >= LEAD_PATCH:
                break
            triangles = cls._mesh_triangles(sim, stage, path) or ()
            if sim._precision != PRECISION_TRI or not triangles:
                patch += cls._box_patch(local, room, u, v)
                continue
            slack = max(min(cls._flat_slack(local, axis),
                            cls._flat_slack(room, axis)), LEAD_TOL)
            for triangle, _, _ in triangles:
                if len(patch) >= LEAD_PATCH:
                    break
                here = [inverse.Transform(Gf.Vec3d(*w)) for w in triangle]
                if any(max(w[i] for w in here) < lo[i] - OVERLAP_EPS
                       or min(w[i] for w in here) > hi[i] + OVERLAP_EPS
                       for i in (u, v)):
                    continue
                shape = cls._sliced(here, axis, plane, slack, u, v)
                if shape:
                    patch.append(shape)
        return patch

    @classmethod
    def _segment_hits_triangle(cls, start, end, triangle) -> bool:
        """선분이 삼각형을 뚫나"""
        v0, v1, v2 = triangle
        direction = [end[i] - start[i] for i in range(3)]
        edge1 = [v1[i] - v0[i] for i in range(3)]
        edge2 = [v2[i] - v0[i] for i in range(3)]

        def cross(p, q):
            """외적"""
            return [p[1] * q[2] - p[2] * q[1],
                    p[2] * q[0] - p[0] * q[2],
                    p[0] * q[1] - p[1] * q[0]]

        def dot(p, q):
            """내적"""
            return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]

        pitch = cross(direction, edge2)
        slope = dot(edge1, pitch)
        if abs(slope) < 1e-12:
            return False
        scale = 1.0 / slope
        offset = [start[i] - v0[i] for i in range(3)]
        u = scale * dot(offset, pitch)
        if u < 0.0 or u > 1.0:
            return False
        turn = cross(offset, edge1)
        v = scale * dot(direction, turn)
        if v < 0.0 or u + v > 1.0:
            return False
        along = scale * dot(edge2, turn)
        return 0.0 <= along <= 1.0

    @classmethod
    def _side_roots(cls, sim) -> list:
        """좌우 판정에 쓸 옆 장비들"""
        stage = sim._get_stage()
        if stage is None:
            return []
        found = sim.side_band(stage, sim._target["ebs"],
                               sim._target["equipment"])
        beside = found.get("beside", []) if found else []
        roots = []
        for path in beside:
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                roots.append(prim)
        sim._note("left and right judged against "
                   + (", ".join(str(p).rsplit("/", 1)[-1] for p in beside)
                      if beside else "nothing -- no machine beside this one")
                   + "; the ceiling still walks the stage")
        return roots

    @classmethod
    def _sliced(cls, triangle, axis: int, plane: float, slack: float, u: int, v: int):
        """그 삼각형을 깊이 plane 에서 자른 모양. 평평하면 면, 가로지르면 선"""
        if all(abs(w[axis] - plane) <= slack for w in triangle):
            return tuple((w[u], w[v]) for w in triangle)
        cut = []
        for at in range(3):
            a, b = triangle[at], triangle[(at + 1) % 3]
            step = b[axis] - a[axis]
            if abs(step) <= 1e-12:
                continue
            hit = (plane - a[axis]) / step
            if 0.0 <= hit <= 1.0:
                cut.append((a[u] + (b[u] - a[u]) * hit,
                            a[v] + (b[v] - a[v]) * hit))
        best = None
        for at in range(len(cut)):
            for other in range(at + 1, len(cut)):
                span = ((cut[at][0] - cut[other][0]) ** 2
                        + (cut[at][1] - cut[other][1]) ** 2)
                if best is None or span > best[0]:
                    best = (span, cut[at], cut[other])
        if best is None or best[0] <= 1e-18:
            return None
        return best[1], best[2]

    @classmethod
    def _stage_boxes(cls, sim, cache=None) -> list:
        """스테이지의 상자 목록. EBS 는 뺀다"""
        if sim._stage_index is not None:
            return sim._stage_index
        stage = sim._get_stage()
        if stage is None:
            return []
        cache = cache if cache is not None else cls._bounds_cache(sim)
        ours_ebs = frozenset(p for p in (sim._ebs_path_2port,
                                         sim._ebs_path_3port) if p)
        under_ebs = tuple(p + "/" for p in ours_ebs)
        index = []
        with sim._stage_timer("stage: index"):
            stack = [(prim, ()) for prim in children(stage.GetPseudoRoot())]
            while stack:
                prim, chain = stack.pop()
                path = str(prim.GetPath())
                if path in OURS or path.startswith(OURS_UNDER):
                    continue
                if path in ours_ebs or path.startswith(under_ebs):
                    continue
                type_name = prim.GetTypeName()
                if type_name in SKIP_TYPES or type_name.endswith("Light"):
                    continue
                box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
                if box.IsEmpty():
                    continue
                if (type_name in GEOMETRY_TYPES
                        or prim.GetName().upper().startswith(EQP_PREFIX)):
                    lo, hi = box.GetMin(), box.GetMax()
                    index.append((path,
                                  (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]),
                                  box, prim, chain))
                    continue
                stack.extend((kid, chain + ((prim, path),))
                             for kid in children(prim))
        sim._stage_index = index
        sim._note(f"stage index: {len(index)} boxes")
        return index

    @classmethod
    def _stop_at(cls, one, two, axis: int, patch):
        """선분이 그 모양들에 처음 닿는 자리. 아무 데도 안 닿으면 None"""
        if not patch:
            return None
        u, v = [i for i in range(3) if i != axis]
        flat_one, flat_two = (one[u], one[v]), (two[u], two[v])
        best = None
        for shape in patch:
            hit = (cls._enter(flat_one, flat_two, shape)
                   if len(shape) == 3
                   else cls._cross(flat_one, flat_two, shape))
            if hit is not None and (best is None or hit < best):
                best = hit
        if best is None:
            return None
        return [one[i] + (two[i] - one[i]) * best for i in range(3)]

    @classmethod
    def _subtree_leaves(cls, sim, stage, cache, root) -> list:
        """그 프림 아래 지오메트리 잎들"""
        path = str(root.GetPath())
        shared = cache is sim._bounds
        got = sim._leaves.get(path) if shared else None
        if got is not None:
            return got
        found = []
        stack = [(root, ())]
        while stack:
            prim, chain = stack.pop()
            where = str(prim.GetPath())
            if where in OURS or where.startswith(OURS_UNDER):
                continue
            type_name = prim.GetTypeName()
            if type_name in SKIP_TYPES or type_name.endswith("Light"):
                continue
            box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if box.IsEmpty():
                continue
            if type_name in GEOMETRY_TYPES:
                found.append((where, box, prim, chain))
                continue
            stack.extend((kid, chain + ((prim, where),))
                         for kid in children(prim))
        if shared:
            sim._leaves[path] = found
        return found

    @classmethod
    def _tick_way(cls, end, axis: int, world):
        """멈춘 자리에 그을 눈금의 방향. 면에 수직, 장비 기준 좌우"""
        ahead = [end[i] + (1.0 if i == axis else 0.0) for i in range(3)]
        here, there = world(end), world(ahead)
        return tuple(there[i] - here[i] for i in range(3))

    @classmethod
    def _to_world(cls, stage, path: str):
        """그 프림의 로컬->월드 행렬"""
        prim = stage.GetPrimAtPath(path) if stage else None
        if prim is None or not prim.IsValid():
            return None
        try:
            return UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default())
        except Exception:
            return None

    @classmethod
    def _triangle_gap(cls, triangle, prism, axis: int, outward: int, coord: float,
                      deep: bool = False):
        """삼각형 하나에서 가장 가까운 점과 거리. deep 이면 가장 깊이 든 점"""
        lo, hi = prism.GetMin(), prism.GetMax()
        best, at = None, None
        for vertex in triangle:
            inside = all(lo[i] - OVERLAP_EPS <= vertex[i] <= hi[i] + OVERLAP_EPS
                         for i in range(3) if i != axis)
            if not inside:
                continue
            if deep:
                gap = (coord - vertex[axis]) if outward > 0 else (vertex[axis] - coord)
            else:
                gap = (vertex[axis] - coord) if outward > 0 else (coord - vertex[axis])
            if gap >= 0 and (best is None or (gap > best if deep else gap < best)):
                best, at = gap, vertex
        if best is None:
            return None
        middle = tuple(sum(v[i] for v in triangle) / 3.0 for i in range(3))
        if all(lo[i] - OVERLAP_EPS <= middle[i] <= hi[i] + OVERLAP_EPS
               for i in range(3) if i != axis):
            at = middle
        return best, at

    @classmethod
    def _triangle_hits_box(cls, triangle, box: Gf.Range3d) -> bool:
        """삼각형과 상자가 실제로 겹치나 (분리축 정리)"""
        lo, hi = box.GetMin(), box.GetMax()
        centre = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        half = [(hi[i] - lo[i]) * 0.5 for i in range(3)]
        v = [[triangle[j][i] - centre[i] for i in range(3)] for j in range(3)]

        for i in range(3):
            if min(v[0][i], v[1][i], v[2][i]) > half[i] or \
               max(v[0][i], v[1][i], v[2][i]) < -half[i]:
                return False

        edges = [[v[1][i] - v[0][i] for i in range(3)],
                 [v[2][i] - v[1][i] for i in range(3)],
                 [v[0][i] - v[2][i] for i in range(3)]]

        normal = [edges[0][1] * edges[1][2] - edges[0][2] * edges[1][1],
                  edges[0][2] * edges[1][0] - edges[0][0] * edges[1][2],
                  edges[0][0] * edges[1][1] - edges[0][1] * edges[1][0]]
        reach = sum(half[i] * abs(normal[i]) for i in range(3))
        distance = sum(normal[i] * v[0][i] for i in range(3))
        if abs(distance) > reach:
            return False

        for edge in edges:
            for i in range(3):
                j, k = (i + 1) % 3, (i + 2) % 3
                axis = [0.0, 0.0, 0.0]
                axis[j], axis[k] = -edge[k], edge[j]
                if abs(axis[j]) < 1e-12 and abs(axis[k]) < 1e-12:
                    continue
                projected = [sum(axis[m] * v[n][m] for m in range(3)) for n in range(3)]
                reach = sum(half[m] * abs(axis[m]) for m in range(3))
                if min(projected) > reach or max(projected) < -reach:
                    return False
        return True

    @classmethod
    def _triangles_meet(cls, a, b) -> bool:
        """두 삼각형이 실제로 만나나. 모서리를 상대 면에 쏜다"""
        for edge in ((a[0], a[1]), (a[1], a[2]), (a[2], a[0])):
            if cls._segment_hits_triangle(edge[0], edge[1], b):
                return True
        for edge in ((b[0], b[1]), (b[1], b[2]), (b[2], b[0])):
            if cls._segment_hits_triangle(edge[0], edge[1], a):
                return True
        return False

    @classmethod
    def _triangles_near(cls, sim, stage, meshes: list, box: Gf.Range3d) -> tuple:
        """양쪽에서 그 상자에 닿는 삼각형만 읽어 온다"""
        kept, tally = [], {"meshes": 0, "built": 0, "world": 0, "faces": 0}
        for path, mesh_box in meshes:
            if Gf.Range3d.GetIntersection(mesh_box, box).IsEmpty():
                continue
            tally["meshes"] += 1
            if path in sim._triangles:
                tally["world"] += 1
            elif path not in sim._faces:
                tally["built"] += 1
            kept.extend(cls._triangles_reaching(sim, stage, path, box))
            made = sim._faces.get(path)
            if made:
                tally["faces"] += len(made[0])
        return kept, tally

    @classmethod
    def _triangles_reaching(cls, sim, stage, path: str, box: Gf.Range3d) -> list:
        """그 상자에 닿는 삼각형만"""
        lo_box, hi_box = box.GetMin(), box.GetMax()
        x0, y0, z0 = lo_box[0], lo_box[1], lo_box[2]
        x1, y1, z1 = hi_box[0], hi_box[1], hi_box[2]

        cached = sim._triangles.get(path)
        if cached is not None:
            return [(path, tri, lo, hi) for tri, lo, hi in cached
                    if lo[0] <= x1 and hi[0] >= x0 and lo[1] <= y1
                    and hi[1] >= y0 and lo[2] <= z1 and hi[2] >= z0]

        data = cls._mesh_local(sim, stage, path)
        to_world = cls._to_world(stage, path)
        if not data or to_world is None:
            return []
        near = cls._pulled_back(box, to_world)
        if near is None:
            return []
        wanted = cls._faces_near(sim, path, data, near)
        if not wanted:
            return []

        points, counts, indices = data
        start, size = sim._faces[path][0], sim._faces[path][1]
        move = cls._mover(sim, to_world, points, path)

        kept = []
        for at in wanted:
            first, count = start[at], size[at]
            fan = [move(points[indices[first + k]]) for k in range(count)]
            for k in range(1, count - 1):
                a, b, c = fan[0], fan[k], fan[k + 1]
                low = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]),
                       min(a[2], b[2], c[2]))
                high = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]),
                        max(a[2], b[2], c[2]))
                if (low[0] <= x1 and high[0] >= x0 and low[1] <= y1
                        and high[1] >= y0 and low[2] <= z1 and high[2] >= z0):
                    kept.append((path, (a, b, c), low, high))
        return kept

    @classmethod
    def _union(cls, boxes: list) -> Gf.Range3d:
        """상자 여러 개를 하나로 감싼다"""
        lo = [min(b.GetMin()[i] for b in boxes) for i in range(3)]
        hi = [max(b.GetMax()[i] for b in boxes) for i in range(3)]
        return Gf.Range3d(Gf.Vec3d(*lo), Gf.Vec3d(*hi))

    @classmethod
    def _with_box(cls, triangle):
        """삼각형에 제 상자를 붙여 둔다"""
        a, b, c = triangle
        return (triangle,
                (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]),
                 min(a[2], b[2], c[2])),
                (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]),
                 max(a[2], b[2], c[2])))

    @classmethod
    def check_collision(cls, sim, ebs_prim: Usd.Prim, exclude: list = None,
                        cache=None, roots: list = None) -> dict:
        """EBS 좌/우/천장 세 면이 닿았나 본다. 면마다 참 거짓 하나"""
        stage = sim._get_stage()
        if stage is None:
            return {face: [] for face in FACES}
        sim._visible = {}
        cache = cache if cache is not None else cls._bounds_cache(sim)

        with sim._stage_timer("faces: search"):
            ebs_bbox = cls._ebs_bound(sim, ebs_prim)
            local_box = ebs_bbox.GetRange()
            to_world = ebs_bbox.GetMatrix()
            world_box = ebs_bbox.ComputeAlignedRange()
        if local_box.IsEmpty():
            return {face: False for face in FACES}

        with sim._stage_timer("faces: search"):
            cells = {
                face: Gf.BBox3d(rng, to_world).ComputeAlignedRange()
                for face, (rng, _) in cls._build_cells(sim, local_box).items()
            }

        with sim._stage_timer("faces: search"):
            depth = cls._probe_depth(local_box)
            margin = Gf.Vec3d(depth, depth, depth)
            search = Gf.Range3d(world_box.GetMin() - margin, world_box.GetMax() + margin)
            skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
            candidates, visited = cls._by_face(sim, stage, cache, search, skip,
                                                roots, cells, margin)
        coarse = len(candidates)

        size = local_box.GetMax() - local_box.GetMin()
        sim._note(f"precision {sim._precision}, probe depth {depth:.4f}, "
                   f"EBS size ({size[0]:.3f}, {size[1]:.3f}, {size[2]:.3f})")
        sim._note(f"EBS local box {tuple(round(v, 3) for v in local_box.GetMin())} .. "
                   f"{tuple(round(v, 3) for v in local_box.GetMax())} "
                   f"(each face patch covers one side of this)")
        sim._note(f"EBS world box {tuple(round(v, 2) for v in world_box.GetMin())} .. "
                   f"{tuple(round(v, 2) for v in world_box.GetMax())}")
        sim._note(f"visited {visited} prims, {coarse} meshes within the probe, "
                   f"skipping {skip}")

        with sim._stage_timer("faces: detect"):
            result = {face: False for face in cells}
            hits = {}
            sim._blockers = {}
            triangle_tests = 0
            boxed_only = set()
            flat = [(face, cell, tuple(cell.GetMin()), tuple(cell.GetMax()))
                    for face, cell in cells.items()]

            for path, box, mine in candidates:
                targets = [entry for entry in flat
                           if entry[0] in mine and not result[entry[0]]
                           and cls._overlaps(box, entry[1])]
                if not targets:
                    continue

                triangles = (cls._mesh_triangles(sim, stage, path)
                             if sim._precision == PRECISION_TRI else None)
                if triangles:
                    triangle_tests += len(triangles)
                    for triangle, lo, hi in triangles:
                        remaining = [e for e in targets if not result[e[0]]]
                        if not remaining:
                            break
                        for face, cell, edge, far in remaining:
                            if (lo[0] <= far[0] and hi[0] >= edge[0]
                                    and lo[1] <= far[1] and hi[1] >= edge[1]
                                    and lo[2] <= far[2] and hi[2] >= edge[2]
                                    and cls._triangle_hits_box(triangle, cell)):
                                result[face] = True
                                sim._blockers.setdefault(face, path)
                                hits.setdefault(path.rsplit("/", 1)[-1], []).append(face)
                    continue

                if sim._precision == PRECISION_TRI:
                    boxed_only.add(path)
                for face, _, _, _ in targets:
                    result[face] = True
                    sim._blockers.setdefault(face, path)
                    hits.setdefault(path.rsplit("/", 1)[-1], []).append(face)

            if triangle_tests:
                sim._note(f"{triangle_tests} triangle tests")
            elif sim._precision == PRECISION_TRI and candidates:
                sim._note("no candidate reached a cell, so no triangle was tested")
            if boxed_only:
                sim._note(f"{len(boxed_only)} of the blocking prims had no triangles, "
                           f"judged by box: "
                           f"{', '.join(sorted(p.rsplit('/', 1)[-1] for p in boxed_only))}")

        if hits:
            sim._note(f"blocked by {len(hits)}: "
                       + "; ".join(f"{name} {', '.join(where)}"
                                   for name, where in sorted(hits.items())[:4])
                       + (" ..." if len(hits) > 4 else ""))
        elif candidates:
            sim._note("candidates were near but none reached a cell")
        else:
            sim._note("nothing within clearance - raise it if that looks wrong")

        return result

    @classmethod
    def check_equipment(cls, sim, ebs_prim: Usd.Prim, eqp_prim: Usd.Prim,
                        cache=None) -> dict:
        """EBS 와 대상 장비만 본다"""
        stage = sim._get_stage()
        blank = {"hit": False, "pairs": [], "boxes": [], "tests": 0}
        if stage is None or eqp_prim is None or not eqp_prim.IsValid():
            return blank

        cache = cache if cache is not None else cls._bounds_cache(sim)
        world_box = cls._ebs_bound(sim, ebs_prim).ComputeAlignedRange()
        if world_box.IsEmpty():
            return blank

        with sim._stage_timer("equipment: search"):
            ours, _ = cls._gather_nearby(sim, stage, cls._moving_cache(), world_box,
                                          [], roots=[ebs_prim])
            theirs, _ = cls._gather_nearby(sim, stage, cache, world_box, [],
                                            roots=[eqp_prim])
        if not ours or not theirs:
            sim._note(f"no interference test: {len(ours)} EBS meshes against "
                       f"{len(theirs)} on the equipment")
            return blank

        pairs, tests = [], 0
        boxed = cls._boxed_pairs(sim, stage, ours, theirs)
        pairs.extend(boxed)
        if boxed:
            sim._note(f"{len(boxed)} pair(s) judged by box: not a mesh, so no "
                       f"triangle to test (Cube/Capsule/etc)")

        whole = cls._union([box for _, box in ours])
        with sim._stage_timer("equipment: read"):
            mine, ebs_read = cls._triangles_near(sim, stage, ours, whole)
        sim._note(f"read: EBS {ebs_read['meshes']} mesh, {ebs_read['faces']} "
                   f"faces, {ebs_read['built']} grid built, "
                   f"{ebs_read['world']} from the world cache")
        if not mine:
            if not pairs:
                sim._note(f"clear of the equipment: nothing of the EBS reaches "
                           f"its own box ({len(ours)} meshes)")
        else:
            with sim._stage_timer("equipment: detect"):
                mesh_pairs, tests, read = cls._meetings(sim, stage, mine, theirs,
                                                         whole, pairs)
            for pair in mesh_pairs:
                if pair not in pairs:
                    pairs.append(pair)
            sim._note(f"interference: {len(mine)} EBS triangles against "
                       f"{read} on the equipment, {tests} pairs tested")

        cls._missed(sim, theirs, pairs, world_box)
        where = dict(theirs)
        boxes, seen = [], set()
        for _, eqp_path in pairs:
            if eqp_path in seen:
                continue
            at = cls._mesh_box(sim, stage, eqp_path)
            if at is None:
                if eqp_path not in where:
                    continue
                box = where[eqp_path]
                at = (box.GetMin(), box.GetMax())
            seen.add(eqp_path)
            lo, hi = at
            boxes.append((eqp_path, (lo[0], lo[1], lo[2]),
                          (hi[0], hi[1], hi[2])))
        return {"hit": bool(pairs), "pairs": pairs, "boxes": boxes,
                "tests": tests}

    @classmethod
    def measure_faces(cls, sim, ebs_prim: Usd.Prim, cells: dict,
                      exclude: list = None, cache=None, roots: list = None) -> dict:
        """면마다 거리. 안 막혔으면 바깥으로 여유, 막혔으면 안으로 파고든 깊이"""
        stage = sim._get_stage()
        if stage is None:
            return {}
        bbox = cls._ebs_bound(sim, ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty() or not sim._face_planes:
            return {}

        reach = max(local_box.GetMax()[i] - local_box.GetMin()[i]
                    for i in range(3)) * REACH_RATIO
        skip = [str(p.GetPath()) for p in (exclude or []) if p and p.IsValid()]
        cache = cache if cache is not None else cls._bounds_cache(sim)

        with sim._stage_timer("clearance: search"):
            wanted = {}
            for face, (axis, outward, coord, _, _) in sim._face_planes.items():
                deep = bool(cells.get(face))
                way = -outward if deep else outward
                span = (local_box.GetMax()[axis] - local_box.GetMin()[axis]
                        if deep else reach)
                prism = cls._face_prism(local_box, axis, way, coord, span)
                wanted[face] = (prism,
                                Gf.BBox3d(prism, to_world).ComputeAlignedRange(),
                                axis, outward, coord, deep)
            candidates = cls._reach_by_face(sim, stage, cache, skip, roots, wanted)
        if not wanted:
            return {}

        with sim._stage_timer("clearance: detect"):
            results = {}
            for face, (prism, world_prism, axis, outward,
                       coord, deep) in wanted.items():
                near = [(path, box) for path, box in candidates.get(face, ())
                        if cls._overlaps(box, world_prism)]
                found = cls._nearest_in_prism(sim, stage, near, prism, to_world,
                                               axis, outward, coord, deep)
                if found is not None and deep:
                    found["distance"] = -found["distance"]
                results[face] = found or {"distance": None, "prim": "",
                                          "reach": reach}
        return results
