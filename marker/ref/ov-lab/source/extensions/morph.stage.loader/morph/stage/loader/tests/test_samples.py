"""내장 샘플: pipe_manifold.usda 목록·카메라·MountPlate Mesh(삼각형·윗면 중심/법선/넓이), 주석 사이드카 JSON(형식·kind 구성·좌표), JSON이 목록에 안 들어감."""

import json
import math
import os
import re

import omni.kit.test
import omni.usd
from pxr import Gf, Usd, UsdGeom

from morph.stage.loader import get_loader

from .helpers import SettingsGuard, wait_until

PIPE_ID = "samples/pipe_manifold.usda"
PIPE_CAMERAS = ("CamIso", "CamFront", "CamTop")
SIDECAR_SUFFIX = ".annotations.json"  # morph.annotation.overlay CONTRACT 기본 sidecarSuffix
BBOX_MARGIN = 0.5
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# CONTRACT "내장 샘플" MountPlate 계약 값 (월드, 미터)
PLATE_PATH = "/World/Base/MountPlate"
PLATE_TOP_CENTER = (0.293185, -1.048236, 0.065)
PLATE_TOP_NORMAL = (0.0, 0.0, 1.0)
PLATE_TOP_AREA = 1.4
PLATE_SIZE = (1.4, 1.0, 0.06)
PLATE_MIN_TOP_TRIS = 8
PLATE_TOL = 1e-5


def _sidecar_path(scene_path):
    return os.path.splitext(scene_path)[0] + SIDECAR_SUFFIX


def _vec3(v):
    return isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)


class TestSamples(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.loader = get_loader()
        self.assertIsNotNone(self.loader)
        await wait_until(lambda: not self.loader.is_loading)
        self.settings = SettingsGuard()

    async def tearDown(self):
        await wait_until(lambda: not self.loader.is_loading)
        await omni.usd.get_context().new_stage_async()
        self.settings.restore()
        self.loader.list_scenes(refresh=True)

    def _pipe_info(self):
        info = self.loader.find_scene(PIPE_ID)
        self.assertIsNotNone(info, f"{PIPE_ID} not in scene list")
        return info

    def _load_annotations(self):
        path = _sidecar_path(self._pipe_info().path)
        self.assertTrue(os.path.isfile(path), path)
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc

    async def test_pipe_in_list(self):
        ids = [s.id for s in self.loader.list_scenes(refresh=True)]
        self.assertIn(PIPE_ID, ids)
        self.assertIn("samples/morph_demo.usda", ids)
        info = self._pipe_info()
        self.assertEqual(info.root, "samples")
        self.assertEqual(info.name, "pipe_manifold.usda")
        self.assertTrue(os.path.isfile(info.path))

    async def test_sidecar_json_not_listed(self):
        # 기본 fileExtensions(.usd/.usda/.usdc/.usdz)에는 .json이 없어 사이드카가 목록에 들어가지 않는다
        scenes = self.loader.list_scenes(refresh=True)
        self.assertTrue(os.path.isfile(_sidecar_path(self._pipe_info().path)))
        self.assertFalse([s.id for s in scenes if s.name.lower().endswith(".json")])
        for s in scenes:
            self.assertIn(os.path.splitext(s.name)[1].lower(), (".usd", ".usda", ".usdc", ".usdz"), s.id)

    async def test_open_pipe_cameras(self):
        info = await self.loader.open_scene(PIPE_ID)
        self.assertEqual(info.id, PIPE_ID)
        stage = omni.usd.get_context().get_stage()
        self.assertEqual(UsdGeom.GetStageUpAxis(stage), UsdGeom.Tokens.z)
        self.assertAlmostEqual(UsdGeom.GetStageMetersPerUnit(stage), 1.0)
        self.assertEqual(stage.GetDefaultPrim().GetPath().pathString, "/World")
        center = Gf.Vec3d(0, 0.3, 1.2)
        for name in PIPE_CAMERAS:
            prim = stage.GetPrimAtPath(f"/World/Cameras/{name}")
            self.assertTrue(prim.IsValid(), name)
            self.assertTrue(prim.IsA(UsdGeom.Camera), name)
            # 카메라(로컬 -Z)가 매니폴드 중심 쪽을 본다
            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            fwd = m.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()
            to = (center - m.ExtractTranslation()).GetNormalized()
            self.assertGreater(fwd * to, 0.99, name)
        cam = stage.GetRootLayer().customLayerData["cameraSettings"]
        self.assertEqual(cam["boundCamera"], "/OmniverseKit_Persp")
        self.assertLess(Gf.Vec3d(*cam["Perspective"]["position"]).GetLength(), 30.0)

    async def test_annotations_format_and_kinds(self):
        doc = self._load_annotations()
        self.assertEqual(doc.get("version"), 1)
        anns = doc.get("annotations")
        self.assertIsInstance(anns, list)
        kinds = [a.get("kind") for a in anns]
        self.assertEqual(kinds.count("inlet"), 1)
        self.assertEqual(kinds.count("outlet"), 2)
        self.assertEqual(kinds.count("plane"), 2)
        self.assertGreaterEqual(kinds.count("label"), 2)
        self.assertTrue(set(kinds) <= {"label", "inlet", "outlet", "plane"}, kinds)
        ids = [a.get("id") for a in anns]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for a in anns:
            self.assertRegex(a["id"], ID_RE)
            self.assertIsInstance(a.get("label", ""), str)
            self.assertIsInstance(a.get("description", ""), str)
            if a["kind"] in ("label", "inlet", "outlet"):
                self.assertTrue(_vec3(a.get("position")), a["id"])
            if a["kind"] in ("inlet", "outlet"):
                self.assertTrue(_vec3(a.get("direction")), a["id"])
                self.assertGreater(Gf.Vec3d(*a["direction"]).GetLength(), 0.0, a["id"])
            if a["kind"] == "plane":
                self.assertTrue(_vec3(a.get("origin")), a["id"])
                self.assertTrue(_vec3(a.get("normal")), a["id"])
                self.assertGreater(Gf.Vec3d(*a["normal"]).GetLength(), 0.0, a["id"])
        labels = {a.get("label") for a in anns}
        self.assertIn("Section A-A", labels)
        self.assertIn("Section B-B", labels)
        self.assertIn("Valve V-101", labels)
        self.assertTrue(all(a.get("description") for a in anns if a["kind"] == "label"))

    async def test_annotations_match_geometry(self):
        doc = self._load_annotations()
        await self.loader.open_scene(PIPE_ID)
        stage = omni.usd.get_context().get_stage()
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        box = cache.ComputeWorldBound(stage.GetPrimAtPath("/World/Manifold")).ComputeAlignedRange()
        self.assertFalse(box.IsEmpty())
        lo = box.GetMin() - Gf.Vec3d(BBOX_MARGIN)
        hi = box.GetMax() + Gf.Vec3d(BBOX_MARGIN)

        def inside(p):
            return all(lo[i] <= p[i] <= hi[i] for i in range(3))

        anns = doc["annotations"]
        for a in anns:
            if a["kind"] in ("inlet", "outlet"):
                self.assertTrue(inside(a["position"]), f"{a['id']} {a['position']} outside {box}")
            if a["kind"] == "plane":
                self.assertTrue(inside(a["origin"]), f"{a['id']} {a['origin']} outside {box}")
        # 흐름 방향: 입구 +X, 출구 A +Y, 출구 B +X
        by_label = {a.get("label"): a for a in anns}
        self.assertEqual(by_label["Inlet"]["direction"], [1.0, 0.0, 0.0])
        self.assertEqual(by_label["Outlet A"]["direction"], [0.0, 1.0, 0.0])
        self.assertEqual(by_label["Outlet B"]["direction"], [1.0, 0.0, 0.0])
        # 입구·출구는 각 플랜지 바깥면 중심
        for label, prim_path in (
            ("Inlet", "/World/Manifold/MainLine/InletFlange"),
            ("Outlet A", "/World/Manifold/Branch_A/Upper/OutletA_Flange"),
            ("Outlet B", "/World/Manifold/MainLine/OutletB_Flange"),
        ):
            r = cache.ComputeWorldBound(stage.GetPrimAtPath(prim_path)).ComputeAlignedRange()
            self.assertFalse(r.IsEmpty(), prim_path)
            pos = by_label[label]["position"]
            for i in range(3):
                self.assertGreaterEqual(pos[i], r.GetMin()[i] - 1e-4, f"{label} axis {i}")
                self.assertLessEqual(pos[i], r.GetMax()[i] + 1e-4, f"{label} axis {i}")

    def _plate_world_tris(self):
        """MountPlate의 (월드 꼭짓점 3개) 목록. 모든 면이 삼각형인지도 확인한다."""
        stage = Usd.Stage.Open(self._pipe_info().path)
        self.assertIsNotNone(stage)
        prim = stage.GetPrimAtPath(PLATE_PATH)
        self.assertTrue(prim.IsValid(), PLATE_PATH)
        self.assertTrue(prim.IsA(UsdGeom.Mesh), prim.GetTypeName())
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = list(mesh.GetFaceVertexCountsAttr().Get())
        indices = list(mesh.GetFaceVertexIndicesAttr().Get())
        self.assertTrue(counts)
        self.assertEqual(set(counts), {3}, "all faces must be triangles")
        self.assertEqual(len(indices), sum(counts))
        self.assertTrue(all(0 <= i < len(points) for i in indices))
        self.assertEqual(mesh.GetOrientationAttr().Get(), UsdGeom.Tokens.rightHanded)
        # extent = 점 범위, 크기 = 계약 값
        extent = mesh.GetExtentAttr().Get()
        self.assertEqual(len(extent), 2)
        for i in range(3):
            self.assertAlmostEqual(extent[0][i], min(p[i] for p in points), places=5)
            self.assertAlmostEqual(extent[1][i], max(p[i] for p in points), places=5)
            self.assertAlmostEqual(extent[1][i] - extent[0][i], PLATE_SIZE[i], places=5)
        self.assertTrue(mesh.GetDisplayColorPrimvar().HasAuthoredValue())
        m = mesh.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world = [m.Transform(Gf.Vec3d(*p)) for p in points]
        return [tuple(world[i] for i in indices[k : k + 3]) for k in range(0, len(indices), 3)]

    async def test_mount_plate_mesh(self):
        tris = self._plate_world_tris()
        # 바깥 방향 감기(rightHanded): 부호 있는 부피가 양수이고 상자 부피와 같다
        vol = sum(a * Gf.Cross(b, c) for a, b, c in tris) / 6.0
        self.assertAlmostEqual(vol, PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2], places=5)
        # 윗면 = 월드 법선이 +Z인 삼각형
        top = []
        for a, b, c in tris:
            n = Gf.Cross(b - a, c - a)
            area = n.GetLength() / 2.0
            self.assertGreater(area, 0.0)
            n = n / (2.0 * area)
            if n[2] > 0.999:
                top.append((area, (a + b + c) / 3.0, n))
        self.assertGreaterEqual(len(top), PLATE_MIN_TOP_TRIS)
        total = sum(t[0] for t in top)
        self.assertAlmostEqual(total, PLATE_TOP_AREA, delta=PLATE_TOL)
        center = Gf.Vec3d(0)
        for area, c, n in top:
            center += c * area
            for i in range(3):
                self.assertAlmostEqual(n[i], PLATE_TOP_NORMAL[i], delta=PLATE_TOL)
        center /= total
        for i in range(3):
            self.assertAlmostEqual(center[i], PLATE_TOP_CENTER[i], delta=PLATE_TOL, msg=f"axis {i} {center}")
        # 변환이 중심을 옮긴다 (로컬 중심과 다름)
        self.assertGreater((center - Gf.Vec3d(0.2, 0.0, 0.06)).GetLength(), 0.5)

    async def test_mount_plate_clear_of_manifold(self):
        await self.loader.open_scene(PIPE_ID)
        stage = omni.usd.get_context().get_stage()
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])

        def box(path):
            r = cache.ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedRange()
            self.assertFalse(r.IsEmpty(), path)
            return r

        plate = box(PLATE_PATH)
        self.assertGreater(plate.GetMin()[2], 0.0)  # 바닥(z=0)과 겹치지 않음
        for path in ("/World/Manifold", "/World/Supports/Support_01", "/World/Supports/Support_02"):
            self.assertTrue(box(path).IntersectWith(plate).IsEmpty(), path)
