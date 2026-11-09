"""Face computation on a real (in-memory) stage: analytic gprims, meshes, cache, limits."""

__all__ = ["TestFaceAnalytic", "TestFaceMesh", "TestFaceCache", "TestFaceLimits"]

import asyncio
import json
import math
import time

import omni.kit.test
from pxr import Gf, Usd, UsdGeom, Vt

from ..face import (
    FACE_KINDS,
    MAX_FACES,
    MAX_OUTLINE,
    MAX_TRIS,
    FaceComputer,
    _components,
    normalize_face,
    plane_basis,
    point_face,
)


def new_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    return stage


def set_xform(prim, translate=None, rotate=None, scale=None):
    xf = UsdGeom.Xformable(prim)
    if translate is not None:
        xf.AddTranslateOp().Set(Gf.Vec3d(*translate))
    if rotate is not None:
        xf.AddRotateXYZOp().Set(Gf.Vec3f(*rotate))
    if scale is not None:
        xf.AddScaleOp().Set(Gf.Vec3f(*scale))
    return xf


def world(prim, local, time=Usd.TimeCode.Default()):
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)
    p = m.Transform(Gf.Vec3d(*local))
    return [p[0], p[1], p[2]]


def define_mesh(stage, path, points, counts, indices):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(list(counts)))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(list(indices)))
    return mesh


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _len(a):
    return math.sqrt(_dot(a, a))


def normal_world(prim, local_n):
    """Unit world normal of a local normal (inverse transpose of the local-to-world matrix)."""
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    n = m.GetInverse().GetTranspose().TransformDir(Gf.Vec3d(*local_n)).GetNormalized()
    return [n[0], n[1], n[2]]


def dir_world(prim, local_v):
    """Unit image of a local direction (what a wrong, non-inverse-transpose normal would be)."""
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    v = m.TransformDir(Gf.Vec3d(*local_v)).GetNormalized()
    return [v[0], v[1], v[2]]


def to_local(prim, p):
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    q = m.GetInverse().Transform(Gf.Vec3d(*p))
    return [q[0], q[1], q[2]]


def box_mesh_data(split=False):
    """Unit box [-1, 1]^3, every side split into 4 triangles around its centre point."""
    corners = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    vid = corners.index
    sides = [
        [vid((-1, -1, 1)), vid((1, -1, 1)), vid((1, 1, 1)), vid((-1, 1, 1))],  # +Z (top)
        [vid((-1, -1, -1)), vid((-1, 1, -1)), vid((1, 1, -1)), vid((1, -1, -1))],
        [vid((1, -1, -1)), vid((1, 1, -1)), vid((1, 1, 1)), vid((1, -1, 1))],
        [vid((-1, -1, -1)), vid((-1, -1, 1)), vid((-1, 1, 1)), vid((-1, 1, -1))],
        [vid((-1, 1, -1)), vid((-1, 1, 1)), vid((1, 1, 1)), vid((1, 1, -1))],
        [vid((-1, -1, -1)), vid((1, -1, -1)), vid((1, -1, 1)), vid((-1, -1, 1))],
    ]
    pts = list(corners)
    counts, idx = [], []
    for q in sides:
        c = tuple(sum(corners[i][k] for i in q) / 4.0 for k in range(3))
        pts.append(c)
        ci = len(pts) - 1
        for k in range(4):
            counts.append(3)
            idx += [q[k], q[(k + 1) % 4], ci]
    if split:  # triangle soup: no shared vertices at all
        pts = [pts[i] for i in idx]
        idx = list(range(len(idx)))
    return pts, counts, idx


class _Base(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.stage = new_stage()
        self.fc = FaceComputer()

    async def tearDown(self):
        self.fc.shutdown()

    def assertVec(self, a, b, places=4, msg=None):
        self.assertEqual(len(a), len(b), msg)
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, places=places, msg=msg or f"{a} != {b}")

    def face(self, path, hit, camera=None, **kw):
        toward = None if camera is None else [camera[i] - hit[i] for i in range(3)]
        f = self.fc.compute(self.stage, path, hit, toward=toward, **kw)
        self.assertEqual(normalize_face(f), f)  # always a valid Face
        self.assertLessEqual(len(f["outline"]), MAX_OUTLINE)
        text = json.dumps(f, allow_nan=False)
        self.assertNotIn("/World", text)  # the prim path never leaves the computation
        return f


class TestFaceAnalytic(_Base):
    async def test_cube_face_with_transform(self):
        cube = UsdGeom.Cube.Define(self.stage, "/World/Cube")  # size 2
        set_xform(cube.GetPrim(), translate=(10, 0, 0), rotate=(0, 0, 90), scale=(2, 3, 4))
        prim = cube.GetPrim()
        hit = world(prim, (1.0, 0.3, -0.2))  # on the local +X side
        f = self.face("/World/Cube", hit, camera=[10, 20, 0])
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], world(prim, (1, 0, 0)))  # (10, 2, 0)
        self.assertVec(f["center"], [10, 2, 0])
        self.assertVec(f["normal"], [0, 1, 0])  # local +X rotated 90 deg about Z
        self.assertAlmostEqual(f["area"], (2 * 3) * (2 * 4), places=4)  # scaled y x z
        self.assertEqual(len(f["outline"]), 4)
        for p in f["outline"]:
            self.assertAlmostEqual(p[1], 2.0, places=4)
        self.assertVec(sorted(f["size"]), [6.0, 8.0])

    async def test_cube_normal_faces_camera(self):
        UsdGeom.Cube.Define(self.stage, "/World/Cube")
        f = self.face("/World/Cube", [0.2, 0.1, 1.0], camera=[0, 0, 10])
        self.assertVec(f["normal"], [0, 0, 1])
        self.assertVec(f["center"], [0, 0, 1])
        f = self.face("/World/Cube", [0.2, 0.1, 1.0])  # no camera: outward
        self.assertVec(f["normal"], [0, 0, 1])

    async def test_cylinder_cap_and_side(self):
        cyl = UsdGeom.Cylinder.Define(self.stage, "/World/Cyl")
        cyl.GetRadiusAttr().Set(0.5)
        cyl.GetHeightAttr().Set(2.0)
        cyl.GetAxisAttr().Set("X")
        set_xform(cyl.GetPrim(), translate=(0, 0, 5), scale=(1, 2, 2))  # world radius 1, length 2
        cap = self.face("/World/Cyl", [1.0, 0.2, 5.4], camera=[10, 0, 5])
        self.assertEqual(cap["kind"], "planar")
        self.assertVec(cap["center"], [1, 0, 5])
        self.assertVec(cap["normal"], [1, 0, 0])
        self.assertAlmostEqual(cap["area"], math.pi, places=4)  # pi r^2, r = 0.5 x 2
        self.assertEqual(len(cap["outline"]), 64)
        for p in cap["outline"]:
            self.assertAlmostEqual(math.hypot(p[1], p[2] - 5), 1.0, places=4)
        self.assertVec(cap["size"], [2.0, 2.0], places=3)

        side = self.face("/World/Cyl", [0.3, 0.0, 6.0], camera=[0.3, 0, 20])
        self.assertEqual(side["kind"], "cylinder_side")
        self.assertVec(side["center"], [0.3, 0, 5])  # foot of the perpendicular on the axis
        self.assertVec(side["normal"], [0, 0, 1])
        self.assertAlmostEqual(side["area"], 2 * math.pi * 1.0 * 2.0, places=4)  # 2 pi r h
        self.assertVec(side["size"], [2.0, 2.0])
        self.assertEqual(len(side["outline"]), 64)
        for p in side["outline"]:
            self.assertAlmostEqual(p[0], 0.3, places=4)  # ring at the hit height
            self.assertAlmostEqual(math.hypot(p[1], p[2] - 5), 1.0, places=4)

    async def test_cylinder_cap_rotated_elliptic(self):
        cyl = UsdGeom.Cylinder.Define(self.stage, "/World/Cyl")  # r 1, h 2, axis Z
        set_xform(cyl.GetPrim(), translate=(1, 2, 3), rotate=(30, 0, 45), scale=(2, 3, 1))
        prim = cyl.GetPrim()
        hit = world(prim, (0.3, 0.2, 1.0))
        f = self.face("/World/Cyl", hit)
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], world(prim, (0, 0, 1)))
        self.assertAlmostEqual(f["area"], math.pi * 2 * 3, places=3)  # ellipse pi a b
        m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        n = m.GetInverse().GetTranspose().TransformDir(Gf.Vec3d(0, 0, 1)).GetNormalized()
        self.assertVec(f["normal"], [n[0], n[1], n[2]])

    def _sheared(self, schema):
        """Parent scale (1, 3, 1) + child rotateZ 45: a sheared (non-orthogonal) local-to-world map."""
        parent = UsdGeom.Xform.Define(self.stage, "/World/P")
        set_xform(parent.GetPrim(), scale=(1, 3, 1))
        geom = schema.Define(self.stage, "/World/P/G")
        set_xform(geom.GetPrim(), rotate=(0, 0, 45))
        prim = geom.GetPrim()
        ex, ey = dir_world(prim, (1, 0, 0)), dir_world(prim, (0, 1, 0))
        self.assertGreater(abs(_dot(ex, ey)), 0.5)  # the local axes are not orthogonal in world space
        return geom, prim

    def _assert_inverse_transpose(self, f, prim, local_n):
        n_ref = normal_world(prim, local_n)
        self.assertVec(f["normal"], n_ref)
        self.assertLess(_dot(n_ref, dir_world(prim, local_n)), 0.99)  # differs from the plain image

    async def test_shear_cube_face(self):
        _cube, prim = self._sheared(UsdGeom.Cube)  # size 2
        hit = world(prim, (1.0, 0.2, 0.3))  # local +X side
        n_ref = normal_world(prim, (1, 0, 0))
        f = self.face("/World/P/G", hit, camera=[h + 10 * n for h, n in zip(hit, n_ref)])
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], world(prim, (1, 0, 0)))
        self._assert_inverse_transpose(f, prim, (1, 0, 0))
        c = [world(prim, (1, a, b)) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        self.assertAlmostEqual(f["area"], _len(_cross(_sub(c[1], c[0]), _sub(c[3], c[0]))), places=4)  # parallelogram
        self.assertAlmostEqual(f["area"], 4 * math.sqrt(5), places=4)
        self.assertEqual(len(f["outline"]), 4)
        for p in f["outline"]:
            self.assertAlmostEqual(_dot(_sub(p, f["center"]), f["normal"]), 0.0, places=5)  # on the face plane

    async def test_shear_cylinder_cap(self):
        cyl, prim = self._sheared(UsdGeom.Cylinder)  # r 1, h 2
        cyl.GetAxisAttr().Set("X")
        hit = world(prim, (1.0, 0.2, 0.3))  # local +X cap
        n_ref = normal_world(prim, (1, 0, 0))
        f = self.face("/World/P/G", hit, camera=[h + 10 * n for h, n in zip(hit, n_ref)])
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], world(prim, (1, 0, 0)))
        self._assert_inverse_transpose(f, prim, (1, 0, 0))
        o = world(prim, (0, 0, 0))
        mu, mv = _sub(world(prim, (0, 1, 0)), o), _sub(world(prim, (0, 0, 1)), o)
        self.assertAlmostEqual(f["area"], math.pi * _len(_cross(mu, mv)), places=4)  # ellipse = pi r^2 x |Mu x Mv|
        self.assertAlmostEqual(f["area"], math.pi * math.sqrt(5), places=4)
        self.assertEqual(len(f["outline"]), 64)
        for p in f["outline"]:
            q = to_local(prim, p)
            self.assertAlmostEqual(q[0], 1.0, places=4)  # on the cap ...
            self.assertAlmostEqual(math.hypot(q[1], q[2]), 1.0, places=4)  # ... rim

    async def test_shear_cylinder_side(self):
        cyl, prim = self._sheared(UsdGeom.Cylinder)  # r 1, h 2
        cyl.GetAxisAttr().Set("X")
        a = 0.7
        hit = world(prim, (0.3, math.cos(a), math.sin(a)))
        n_ref = normal_world(prim, (0, math.cos(a), math.sin(a)))
        f = self.face("/World/P/G", hit, camera=[h + 10 * n for h, n in zip(hit, n_ref)])
        self.assertEqual(f["kind"], "cylinder_side")
        self._assert_inverse_transpose(f, prim, (0, math.cos(a), math.sin(a)))
        o = world(prim, (0, 0, 0))
        D = _sub(world(prim, (1, 0, 0)), o)  # axis per local unit; generator length = h x |D|
        dl = _len(D)
        dn = [c / dl for c in D]
        t = _dot(_sub(hit, o), D) / (dl * dl)
        self.assertVec(f["center"], [o[i] + t * D[i] for i in range(3)])  # foot of the perpendicular
        # right section (perpendicular to the axis): perimeter by dense sampling
        mu, mv = _sub(world(prim, (0, 1, 0)), o), _sub(world(prim, (0, 0, 1)), o)
        ring = []
        k = 20000
        for i in range(k):
            th = 2 * math.pi * i / k
            r = [math.cos(th) * mu[j] + math.sin(th) * mv[j] for j in range(3)]
            d = _dot(r, dn)
            ring.append([r[j] - d * dn[j] for j in range(3)])
        perimeter = sum(_len(_sub(ring[(i + 1) % k], ring[i])) for i in range(k))
        self.assertAlmostEqual(f["area"], perimeter * 2.0 * dl, places=3)  # perimeter x generator length
        # sanity of the reference: right-section area x generator length = volume (pi r^2 h |det|)
        acc = [0.0, 0.0, 0.0]
        for i in range(k):
            c = _cross(ring[i], ring[(i + 1) % k])
            acc = [acc[j] + c[j] for j in range(3)]
        det = abs(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetDeterminant3())
        self.assertAlmostEqual(0.5 * _len(acc) * 2.0 * dl, math.pi * 2.0 * det, places=3)
        self.assertAlmostEqual(f["size"][0], 2 * max(_len(r) for r in ring), places=3)
        self.assertAlmostEqual(f["size"][1], 2.0 * dl, places=5)
        self.assertEqual(len(f["outline"]), 64)
        for p in f["outline"]:
            self.assertAlmostEqual(_dot(_sub(p, f["center"]), dn), 0.0, places=5)  # right-section plane
            q = to_local(prim, p)
            self.assertAlmostEqual(math.hypot(q[1], q[2]), 1.0, places=4)  # on the cylinder surface
        self.assertLess(min(_len(_sub(p, hit)) for p in f["outline"]), perimeter / 64)  # through the hit

    async def test_cone_base_and_side(self):
        UsdGeom.Cone.Define(self.stage, "/World/Cone")  # r 1, h 2, apex +Z
        base = self.face("/World/Cone", [0.2, 0.1, -1.0], camera=[0, 0, -10])
        self.assertEqual(base["kind"], "planar")
        self.assertVec(base["center"], [0, 0, -1])
        self.assertVec(base["normal"], [0, 0, -1])
        self.assertAlmostEqual(base["area"], math.pi, places=4)
        side = self.face("/World/Cone", [0.5, 0.0, 0.0], camera=[10, 0, 0])
        self.assertEqual(side["kind"], "point")  # cone side: not supported
        self.assertVec(side["center"], [0.5, 0, 0])

    async def test_sphere(self):
        sph = UsdGeom.Sphere.Define(self.stage, "/World/Ball")
        sph.GetRadiusAttr().Set(2.0)
        set_xform(sph.GetPrim(), translate=(1, 1, 1))
        f = self.face("/World/Ball", [1, 1, 3], camera=[1, 1, 10])
        self.assertEqual(f["kind"], "sphere")
        self.assertVec(f["center"], [1, 1, 1])
        self.assertVec(f["normal"], [0, 0, 1])
        self.assertAlmostEqual(f["area"], 4 * math.pi * 4, places=3)
        self.assertVec(f["size"], [4, 4])
        self.assertEqual(len(f["outline"]), 64)
        for p in f["outline"]:  # on the sphere, around the hit
            self.assertAlmostEqual(math.dist(p, [1, 1, 1]), 2.0, places=4)
            self.assertGreater(p[2], 2.5)

    async def test_unsupported_and_missing_fall_back_to_point(self):
        UsdGeom.Capsule.Define(self.stage, "/World/Cap")
        f = self.face("/World/Cap", [0, 0, 1.5], pick_normal=[0, 0, 2])
        self.assertEqual(f, {"kind": "point", "center": [0.0, 0.0, 1.5], "normal": [0.0, 0.0, 1.0], "area": 0.0,
                             "size": [0.0, 0.0], "outline": []})
        for path in ("/World/Nope", "", None, "not a path", "/World"):  # missing, invalid, not a gprim
            f = self.face(path, [1, 2, 3], camera=[1, 2, 13])
            self.assertEqual(f["kind"], "point", path)
            self.assertVec(f["normal"], [0, 0, 1])  # toward the camera when there is no pick normal
        f = self.fc.compute(None, "/World/Cap", [1, 2, 3])
        self.assertEqual(f["kind"], "point")

    async def test_hit_far_from_gprim_is_point(self):
        UsdGeom.Cube.Define(self.stage, "/World/Cube")
        self.assertEqual(self.face("/World/Cube", [0, 0, 7])["kind"], "point")

    async def test_normalize_face(self):
        good = point_face([1, 2, 3], [0, 0, 1])
        self.assertEqual(normalize_face({**good, "extra": 1}), good)  # known keys only
        for bad in (None, [], {**good, "kind": "edge"}, {**good, "normal": [0, 0, 0]},
                    {**good, "center": [1, 2]}, {**good, "area": -1}, {**good, "area": True},
                    {**good, "size": [1, -1]}, {**good, "outline": [[0, 0, float("nan")]]},
                    {**good, "outline": [[0, 0, 0]] * (MAX_OUTLINE + 1)}, {k: v for k, v in good.items() if k != "size"}):
            with self.assertRaises(ValueError, msg=bad):
                normalize_face(bad)
        self.assertIn("planar", FACE_KINDS)
        n, h, v = plane_basis([0, 1, 0], [0, 0, 1])
        self.assertVec(v, [0, 0, 1])


class TestFaceMesh(_Base):
    def _box(self, split=False, **xf):
        pts, counts, idx = box_mesh_data(split)
        mesh = define_mesh(self.stage, "/World/Box", pts, counts, idx)
        if xf:
            set_xform(mesh.GetPrim(), **xf)
        return mesh.GetPrim()

    async def test_box_side_is_one_face_not_four_triangles(self):
        for split in (False, True):
            with self.subTest(split=split):
                self.stage = new_stage()
                self.fc.clear()
                self._box(split)
                f = self.face("/World/Box", [0.3, -0.6, 1.0], camera=[0, 0, 10])
                self.assertEqual(f["kind"], "planar")
                self.assertVec(f["center"], [0, 0, 1])
                self.assertVec(f["normal"], [0, 0, 1])
                self.assertAlmostEqual(f["area"], 4.0, places=5)
                self.assertEqual(len(f["outline"]), 4)  # the 4 corners (collinear points dropped)
                self.assertEqual(sorted(tuple(round(c, 4) for c in p) for p in f["outline"]),
                                 [(-1, -1, 1), (-1, 1, 1), (1, -1, 1), (1, 1, 1)])
                self.assertVec(f["size"], [2, 2])

    async def test_box_with_transform(self):
        prim = self._box(translate=(0, 0, 10), rotate=(90, 0, 0), scale=(1, 2, 3))
        hit = world(prim, (0.2, 0.3, 1.0))  # local top side
        f = self.face("/World/Box", hit)
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], world(prim, (0, 0, 1)))
        self.assertAlmostEqual(f["area"], 2 * 4, places=4)  # x 2 * y (2 x 2) on the local z side

    async def test_l_shape_area_weighted_center(self):
        pts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0), (0, 2, 0), (1, 2, 0)]
        define_mesh(self.stage, "/World/L", pts, [4, 4, 4], [0, 1, 4, 3, 1, 2, 5, 4, 3, 4, 7, 6])
        f = self.face("/World/L", [1.5, 0.5, 0.0], camera=[1, 1, 5])
        self.assertEqual(f["kind"], "planar")
        self.assertAlmostEqual(f["area"], 3.0, places=5)
        self.assertVec(f["center"], [5 / 6, 5 / 6, 0])  # not the bbox centre (1, 1)
        self.assertEqual(len(f["outline"]), 6)  # concave outline corners
        self.assertVec(f["size"], [2, 2])

    async def test_l_shape_mixed_winding(self):
        pts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0), (0, 2, 0), (1, 2, 0)]
        define_mesh(self.stage, "/World/L", pts, [4, 4, 4], [0, 1, 4, 3, 4, 5, 2, 1, 3, 4, 7, 6])  # middle one flipped
        f = self.face("/World/L", [0.5, 1.5, 0.0], camera=[1, 1, -5])
        self.assertAlmostEqual(f["area"], 3.0, places=5)
        self.assertVec(f["normal"], [0, 0, -1])  # toward the camera below
        self.assertEqual(len(f["outline"]), 6)

    async def test_curved_mesh_is_polygon(self):
        n = 16
        pts, idx = [], []
        for i in range(n):
            t = 2 * math.pi * i / n
            pts += [(math.cos(t), math.sin(t), -1), (math.cos(t), math.sin(t), 1)]
        for i in range(n):
            j = (i + 1) % n
            idx += [2 * i, 2 * j, 2 * j + 1, 2 * i + 1]
        define_mesh(self.stage, "/World/Pipe", pts, [4] * n, idx)
        a, b = pts[0], pts[2]
        hit = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.3]
        f = self.face("/World/Pipe", hit, camera=[5, 0.5, 0])
        self.assertEqual(f["kind"], "polygon")
        chord = math.dist(a, b)
        self.assertAlmostEqual(f["area"], chord * 2, places=4)
        self.assertVec(f["center"], [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0])
        self.assertEqual(len(f["outline"]), 4)
        self.assertGreater(f["normal"][0], 0.9)  # outward (toward the camera)

    async def test_over_face_limit_is_polygon(self):
        self._box()
        self.fc = FaceComputer(max_faces=4)  # the box has 24 faces
        f = self.face("/World/Box", [0.3, -0.6, 1.0], camera=[0, 0, 10])
        self.assertEqual(f["kind"], "polygon")
        self.assertAlmostEqual(f["area"], 1.0, places=5)  # one triangle of the side
        self.assertVec(f["normal"], [0, 0, 1])
        self.assertEqual(self.fc.cached_keys, [])  # over the cap: no mesh data kept
        self.assertEqual((self.fc.build_count, self.fc.scan_count), (0, 1))

    async def test_same_height_islands_not_merged(self):
        """Two flats at the same height joined by a gentle bump (every step < 1 degree, so one plane
        component) are two regions: only faces connected to the clicked one through the plane."""
        slopes = [0.0] * 40 + [0.4, 0.8, 1.6, 0.8, 0.4, 0.0, -0.4, -0.8, -1.6, -0.8, -0.4] + [0.0] * 40
        xs, zs = [0.0], [0.0]
        for s in slopes:
            a = math.radians(s)
            xs.append(xs[-1] + math.cos(a))
            zs.append(zs[-1] + math.sin(a))
        self.assertAlmostEqual(zs[-1], 0.0, places=9)  # the far flat is on the same plane
        n = len(xs)
        pts = [(xs[i], y, zs[i]) for i in range(n) for y in (0, 1)]
        idx = []
        for i in range(n - 1):
            idx += [2 * i, 2 * (i + 1), 2 * (i + 1) + 1, 2 * i + 1]
        define_mesh(self.stage, "/World/Strip", pts, [4] * (n - 1), idx)
        for hit, cx in (([10.5, 0.5, 0.0], 20.5), ([xs[-1] - 10.5, 0.5, 0.0], xs[-1] - 20.5)):
            f = self.face("/World/Strip", hit, camera=[hit[0], 0.5, 20])
            self.assertEqual(f["kind"], "planar")
            self.assertAlmostEqual(f["area"], 41.0, places=2)  # its 40 flat quads + the first 0.4 degree one
            self.assertAlmostEqual(f["center"][0], cx, places=2)  # not the merged centre (~45.7)
            self.assertVec(f["normal"], [0, 0, 1], places=3)
            self.assertVec(sorted(f["size"]), [1.0, 41.0], places=2)

    async def test_zero_area_face_does_not_split(self):
        """A zero-width quad (split vertices) between two flat quads: grouped by plane distance."""
        pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0), (2, 0, 0), (2, 1, 0)]
        define_mesh(self.stage, "/World/Z", pts, [4, 4, 4], [0, 1, 2, 3, 1, 4, 5, 2, 4, 6, 7, 5])
        for hit in ([0.5, 0.5, 0.0], [1.5, 0.3, 0.0]):
            f = self.face("/World/Z", hit, camera=[1, 0.5, 5])
            self.assertEqual(f["kind"], "planar", hit)
            self.assertAlmostEqual(f["area"], 2.0, places=5)
            self.assertVec(f["center"], [1.0, 0.5, 0.0])
            self.assertEqual(sorted(tuple(round(c, 4) for c in p) for p in f["outline"]),
                             [(0, 0, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)])

    async def test_collinear_and_sliver_bridge(self):
        """The only link between two flat faces is a collinear (zero-area) triangle, or a sliver whose
        computed normal is 45 degrees off: neither splits the face."""
        for name, mid in (("collinear", (1, 0.5, 0)), ("sliver", (1 + 1e-6, 0.5, 1e-6))):
            with self.subTest(name):
                self.stage = new_stage()
                self.fc.clear()
                # pentagon (with the bridge point on its right edge), bridge triangle, quad
                pts = [(0, 0, 0), (1, 0, 0), mid, (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)]
                define_mesh(self.stage, "/World/T", pts, [5, 3, 4], [0, 1, 2, 3, 4, 1, 3, 2, 1, 5, 6, 3])
                for hit in ([0.5, 0.5, 0.0], [1.5, 0.5, 0.0]):
                    f = self.face("/World/T", hit, camera=[1, 0.5, 5])
                    self.assertEqual(f["kind"], "planar", (name, hit))
                    self.assertAlmostEqual(f["area"], 2.0, places=4)
                    self.assertVec(f["center"], [1.0, 0.5, 0.0], places=4)
                    self.assertVec(f["normal"], [0, 0, 1], places=4)
                    self.assertEqual(len(f["outline"]), 4)

    async def test_nan_points_ignored(self):
        """Faces using non-finite points are skipped; the rest of the mesh still works."""
        mesh = define_mesh(self.stage, "/World/N", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0),
                                                    (5, 5, 0)],
                           [4, 4, 3], [0, 1, 2, 3, 1, 4, 5, 2, 4, 5, 6])
        pts = list(mesh.GetPointsAttr().Get())
        pts[6] = Gf.Vec3f(5, 5, float("nan"))
        mesh.GetPointsAttr().Set(Vt.Vec3fArray(pts))
        f = self.face("/World/N", [0.5, 0.5, 0.0], camera=[1, 0.5, 5])
        self.assertEqual(f["kind"], "planar")
        self.assertAlmostEqual(f["area"], 2.0, places=5)
        self.assertEqual(len(f["outline"]), 4)
        all_nan = [Gf.Vec3f(float("nan"), 0, 0)] * 7
        mesh.GetPointsAttr().Set(Vt.Vec3fArray(all_nan))
        self.assertEqual(self.face("/World/N", [0.5, 0.5, 0.0])["kind"], "point")

    async def test_hit_off_the_mesh_is_point(self):
        self._box()
        f = self.face("/World/Box", [0, 0, 5], camera=[0, 0, 10])  # e.g. a point instancer prototype
        self.assertEqual(f["kind"], "point")

    async def test_disc_outline_capped(self):
        k = 1000
        pts = [(0, 0, 0)] + [(2 * math.cos(2 * math.pi * i / k), 2 * math.sin(2 * math.pi * i / k), 0) for i in range(k)]
        idx = []
        for i in range(k):
            idx += [0, 1 + i, 1 + (i + 1) % k]
        define_mesh(self.stage, "/World/Disc", pts, [3] * k, idx)
        f = self.face("/World/Disc", [0.5, 0.5, 0], camera=[0, 0, 5])
        self.assertEqual(f["kind"], "planar")
        self.assertEqual(len(f["outline"]), MAX_OUTLINE)
        self.assertVec(f["center"], [0, 0, 0])
        self.assertAlmostEqual(f["area"], math.pi * 4, places=3)

    async def test_instance_proxy_uses_instance_transform(self):
        src = self.stage.CreateClassPrim("/Src")
        pts, counts, idx = box_mesh_data()
        define_mesh(self.stage, "/Src/Box", pts, counts, idx)
        inst = self.stage.DefinePrim("/World/Inst", "Xform")
        inst.GetReferences().AddInternalReference(src.GetPath())
        inst.SetInstanceable(True)
        set_xform(inst, translate=(5, 0, 0))
        proxy = self.stage.GetPrimAtPath("/World/Inst/Box")
        self.assertTrue(proxy.IsInstanceProxy())
        f = self.face("/World/Inst/Box", [5.3, 0.2, 1.0], camera=[5, 0, 10])
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], [5, 0, 1])
        self.assertAlmostEqual(f["area"], 4.0, places=5)

    async def test_time_sampled_xform(self):
        prim = self._box()
        op = UsdGeom.Xformable(prim).AddTranslateOp()
        op.Set(Gf.Vec3d(0, 0, 0), 0)
        op.Set(Gf.Vec3d(0, 0, 10), 10)
        f0 = self.face("/World/Box", [0.2, 0.2, 1.0], time=Usd.TimeCode(0))
        f10 = self.face("/World/Box", [0.2, 0.2, 11.0], time=Usd.TimeCode(10))
        self.assertVec(f0["center"], [0, 0, 1])
        self.assertVec(f10["center"], [0, 0, 11])
        self.assertEqual(len(self.fc.cached_keys), 2)  # one entry per time code

    async def test_large_planar_mesh_click_time(self):
        k = 300  # 90k quads, one plane
        pts = [(x, y, 0) for y in range(k + 1) for x in range(k + 1)]
        idx = []
        for r in range(k):
            for c in range(k):
                v0 = r * (k + 1) + c
                idx += [v0, v0 + 1, v0 + k + 2, v0 + k + 1]
        define_mesh(self.stage, "/World/Floor", pts, [4] * (k * k), idx)
        f = self.face("/World/Floor", [10.5, 20.5, 0], camera=[10, 20, 50])  # builds geometry + adjacency
        self.assertEqual(f["kind"], "planar")
        self.assertAlmostEqual(f["area"], k * k, places=3)
        self.assertVec(f["center"], [k / 2, k / 2, 0])
        self.assertEqual(len(f["outline"]), 4)
        builds = self.fc.build_count
        t0 = time.perf_counter()
        f2 = self.face("/World/Floor", [200.5, 100.5, 0], camera=[200, 100, 50])
        elapsed = time.perf_counter() - t0
        self.assertEqual(self.fc.build_count, builds)  # cached: adjacency built once
        self.assertEqual(f2["center"], f["center"])
        self.assertLess(elapsed, 0.5, "cached click on a 90k-face mesh")


class TestFaceCache(_Base):
    async def test_cache_reused_and_dropped_on_change(self):
        pts, counts, idx = box_mesh_data()
        mesh = define_mesh(self.stage, "/World/Box", pts, counts, idx)
        self.face("/World/Box", [0.2, 0.2, 1.0])
        self.face("/World/Box", [0.2, 0.2, -1.0])
        self.assertEqual(self.fc.build_count, 1)
        self.assertEqual(len(self.fc.cached_keys), 1)
        # geometry edit (Usd.Notice): the entry goes, the next click sees the new points
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(p[0], p[1], p[2] + 1) for p in pts]))
        self.assertEqual(self.fc.cached_keys, [])
        f = self.face("/World/Box", [0.2, 0.2, 2.0])
        self.assertEqual(self.fc.build_count, 2)
        self.assertVec(f["center"], [0, 0, 2])
        # an ancestor transform edit drops it too
        set_xform(self.stage.GetPrimAtPath("/World"), translate=(0, 0, 1))
        self.assertEqual(self.fc.cached_keys, [])
        f = self.face("/World/Box", [0.2, 0.2, 3.0])
        self.assertVec(f["center"], [0, 0, 3])
        # an unrelated prim does not
        UsdGeom.Cube.Define(self.stage, "/Other")
        self.assertEqual(len(self.fc.cached_keys), 1)
        self.fc.clear()  # stage open / close
        self.assertEqual(self.fc.cached_keys, [])

    async def test_other_stage_drops_cache(self):
        pts, counts, idx = box_mesh_data()
        define_mesh(self.stage, "/World/Box", pts, counts, idx)
        self.face("/World/Box", [0.2, 0.2, 1.0])
        other = new_stage()
        define_mesh(other, "/World/Box", [(x * 2, y * 2, z * 2) for x, y, z in pts], counts, idx)
        f = self.fc.compute(other, "/World/Box", [0.2, 0.2, 2.0])
        self.assertVec(f["center"], [0, 0, 2])  # not the cached box of the first stage
        self.assertEqual(len(self.fc.cached_keys), 1)

    async def test_cache_byte_budget(self):
        pts, counts, idx = box_mesh_data()
        for i, name in enumerate("ABC"):
            define_mesh(self.stage, f"/World/{name}", [(x + 10 * i, y, z) for x, y, z in pts], counts, idx)

        def click_all(fc):
            for i, name in enumerate("ABC"):
                f = fc.compute(self.stage, f"/World/{name}", [10 * i + 0.2, 0.2, 1.0])
                self.assertEqual(f["kind"], "planar")
                self.assertAlmostEqual(f["area"], 4.0, places=5)

        self.fc = FaceComputer()
        click_all(self.fc)
        self.assertEqual(len(self.fc.cached_keys), 3)
        one = self.fc.cached_bytes / 3
        self.assertGreater(one, 0)
        self.fc.shutdown()
        self.fc = FaceComputer(cache_bytes=int(one * 2.5))  # room for two
        click_all(self.fc)
        self.assertEqual([k[0] for k in self.fc.cached_keys], ["/World/B", "/World/C"])  # oldest evicted
        self.assertLessEqual(self.fc.cached_bytes, int(one * 2.5))
        self.fc.shutdown()
        self.fc = FaceComputer(cache_bytes=1)  # smaller than one mesh: the newest one still stays
        click_all(self.fc)
        self.assertEqual([k[0] for k in self.fc.cached_keys], ["/World/C"])
        self.fc.compute(self.stage, "/World/C", [20.2, 0.2, -1.0])
        self.assertEqual(self.fc.build_count, 3)  # reused
        self.fc.shutdown()
        self.fc = FaceComputer(cache_size=2)  # count limit too
        click_all(self.fc)
        self.assertEqual(len(self.fc.cached_keys), 2)

    async def test_perf_scope_recorded(self):
        from morph.perf.monitor import get_monitor

        from ..face import M_FACE

        UsdGeom.Cube.Define(self.stage, "/World/Cube")
        mon = get_monitor()
        if mon is None or not mon.enabled:
            self.skipTest("morph.perf.monitor not running or disabled")
        self.face("/World/Cube", [0, 0, 1])
        self.assertIn(M_FACE, mon.flush())


class TestFaceLimits(_Base):
    def _box(self, path="/World/Box"):
        pts, counts, idx = box_mesh_data()
        return define_mesh(self.stage, path, pts, counts, idx)

    async def _until(self, cond, seconds=10.0):
        t0 = time.perf_counter()
        while not cond() and time.perf_counter() - t0 < seconds:
            await asyncio.sleep(0.01)
        return cond()

    async def test_caps(self):
        self.assertEqual((MAX_FACES, MAX_TRIS), (2_000_000, 3_000_000))

    async def test_over_triangle_cap_scans_without_cache(self):
        self._box()
        self.fc = FaceComputer(max_tris=10)  # 24 triangles
        for _ in range(2):
            f = self.face("/World/Box", [0.3, -0.6, 1.0], camera=[0, 0, 10])
            self.assertEqual(f["kind"], "polygon")
            self.assertAlmostEqual(f["area"], 1.0, places=5)
            self.assertEqual(len(f["outline"]), 3)
        self.assertEqual(self.fc.cached_keys, [])
        self.assertEqual((self.fc.build_count, self.fc.scan_count), (0, 2))  # scanned every click
        self.assertEqual(self.face("/World/Box", [0, 0, 5], camera=[0, 0, 10])["kind"], "point")  # off the mesh

    async def test_components_no_round_limit(self):
        k = 5000
        e1 = list(range(k - 2, -1, -1))  # a long chain, listed from the far end
        lab = _components(k, e1, [i + 1 for i in e1])
        self.assertEqual(set(lab.tolist()), {0})
        lab = _components(6, [0, 2, 4], [1, 3, 5])
        self.assertEqual(lab.tolist(), [0, 0, 2, 2, 4, 4])
        self.assertEqual(_components(3, [], []).tolist(), [0, 1, 2])

    async def test_async_build_matches_sync(self):
        self._box()
        hit, cam = [0.3, -0.6, 1.0], [0, 0, 10]
        toward = [c - h for c, h in zip(cam, hit)]
        ref = FaceComputer()
        sync = ref.compute(self.stage, "/World/Box", hit, toward=toward)
        ref.shutdown()
        self.fc = FaceComputer(async_faces=1)  # every mesh goes to the worker thread
        f = await self.fc.compute_async(self.stage, "/World/Box", hit, toward=toward)
        self.assertEqual(f, sync)
        self.assertEqual(f["kind"], "planar")
        self.assertEqual(self.fc.build_count, 1)
        self.assertEqual(len(self.fc.cached_keys), 1)
        f2 = await self.fc.compute_async(self.stage, "/World/Box", [0.2, 0.2, -1.0])  # cached: no new build
        self.assertVec(f2["center"], [0, 0, -1])
        self.assertEqual(self.fc.build_count, 1)
        self._box("/World/Box2")
        a, b = await asyncio.gather(  # two clicks on a new mesh share one worker build
            self.fc.compute_async(self.stage, "/World/Box2", hit, toward=toward),
            self.fc.compute_async(self.stage, "/World/Box2", [0.2, 0.2, -1.0]),
        )
        self.assertEqual(a, sync)
        self.assertVec(b["center"], [0, 0, -1])
        self.assertEqual(self.fc.build_count, 2)
        # not a mesh / missing prim: synchronous, still a Face
        UsdGeom.Cube.Define(self.stage, "/World/Cube").AddTranslateOp().Set(Gf.Vec3d(10, 0, 0))
        f3 = await self.fc.compute_async(self.stage, "/World/Cube", [10.2, 0.1, 1.0])
        self.assertEqual(f3["kind"], "planar")
        self.assertEqual((await self.fc.compute_async(self.stage, "/World/Nope", [1, 2, 3]))["kind"], "point")

    async def test_async_scan_over_cap(self):
        self._box()
        self.fc = FaceComputer(async_faces=1, max_tris=10)
        f = await self.fc.compute_async(self.stage, "/World/Box", [0.3, -0.6, 1.0], toward=[0, 0, 9])
        self.assertEqual(f["kind"], "polygon")
        self.assertAlmostEqual(f["area"], 1.0, places=5)
        self.assertEqual((self.fc.build_count, self.fc.scan_count), (0, 1))
        self.assertEqual(self.fc.cached_keys, [])

    async def test_async_rebuilds_when_changed_meanwhile(self):
        mesh = self._box()
        pts, _counts, _idx = box_mesh_data()
        self.fc = FaceComputer(async_faces=1)
        task = asyncio.ensure_future(self.fc.compute_async(self.stage, "/World/Box", [0.2, 0.2, 1.0]))
        await asyncio.sleep(0)  # the task planned its worker build and waits for it
        mesh.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(p[0], p[1], p[2] + 1) for p in pts]))  # edit meanwhile
        await task
        self.assertEqual(self.fc.build_count, 2)  # the stale build was not used for the cache
        self.assertEqual(len(self.fc.cached_keys), 1)
        f = self.face("/World/Box", [0.2, 0.2, 2.0])
        self.assertVec(f["center"], [0, 0, 2])  # the cache holds the edited geometry
        self.assertEqual(self.fc.build_count, 2)

    async def test_async_cancel_keeps_build_and_clear_drops_it(self):
        self._box()
        self.fc = FaceComputer(async_faces=1)
        task = asyncio.ensure_future(self.fc.compute_async(self.stage, "/World/Box", [0.2, 0.2, 1.0]))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(await self._until(lambda: self.fc.build_count == 1))
        self.assertEqual(len(self.fc.cached_keys), 1)  # the worker result is still cached for the next click
        self.fc.clear()
        task = asyncio.ensure_future(self.fc.compute_async(self.stage, "/World/Box", [0.2, 0.2, -1.0]))
        await asyncio.sleep(0)
        self.fc.clear()  # stage open / close while building: that build is not used
        f = await task
        self.assertEqual(f["kind"], "planar")
        self.assertVec(f["center"], [0, 0, -1])
        self.assertEqual(self.fc.build_count, 3)  # built again after clear()
        self.assertEqual(len(self.fc.cached_keys), 1)
