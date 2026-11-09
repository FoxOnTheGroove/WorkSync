"""StageLoader: 목록 설정 반영, 열기·이벤트·current, 실패, reload, new_scene, 외부 열기."""

import asyncio
import os
import tempfile

import omni.kit.test
import omni.usd
from pxr import Gf, Usd, UsdGeom

from morph.stage.loader import (
    EVENT_OPEN_FAILED,
    EVENT_OPENED,
    SceneInfo,
    StageLoadError,
    get_loader,
)

from .helpers import SAMPLE_ID, EventCollector, SettingsGuard, wait_frames, wait_until

BROKEN_NAME = "broken_scene_morph_test.usda"
PLAIN_USDA = '#usda 1.0\n(\n    defaultPrim = "W"\n)\n\ndef Xform "W"\n{\n}\n'


class TestLoader(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.loader = get_loader()
        self.assertIsNotNone(self.loader)
        await wait_until(lambda: not self.loader.is_loading)
        self.settings = SettingsGuard()
        self._tmp = tempfile.TemporaryDirectory()
        self.events = EventCollector(EVENT_OPENED, EVENT_OPEN_FAILED)

    async def tearDown(self):
        self.events.close()
        await wait_until(lambda: not self.loader.is_loading)
        await omni.usd.get_context().new_stage_async()
        self.settings.restore()
        self.loader.list_scenes(refresh=True)
        self._tmp.cleanup()

    def _make_root(self, files):
        for rel, data in files.items():
            p = os.path.join(self._tmp.name, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(data)
        return self._tmp.name

    async def test_sample_in_list(self):
        scenes = self.loader.list_scenes()
        self.assertTrue(all(isinstance(s, SceneInfo) for s in scenes))
        info = self.loader.find_scene(SAMPLE_ID)
        self.assertIsNotNone(info)
        self.assertEqual(info.root, "samples")
        self.assertEqual(info.name, "morph_demo.usda")
        self.assertTrue(os.path.isfile(info.path))
        self.assertEqual(scenes[-1].root, "samples")  # samples 맨 뒤

    async def test_settings_change_rescans(self):
        root = self._make_root({"x/one.usda": "#usda 1.0\n"})
        self.settings.set("roots", [f"tmp={root}"])
        ids = [s.id for s in self.loader.list_scenes()]  # refresh 없이 설정 변경 반영
        self.assertEqual(ids[0], "tmp/x/one.usda")
        self.assertIn(SAMPLE_ID, ids)
        self.settings.set("includeSamples", False)
        ids = [s.id for s in self.loader.list_scenes()]
        self.assertEqual(ids, ["tmp/x/one.usda"])
        self.settings.set("maxDepth", 0)
        self.assertEqual(self.loader.list_scenes(), [])
        # 캐시: 파일을 추가해도 refresh 전에는 그대로, refresh=True면 반영
        self.settings.set("maxDepth", 4)
        self.assertEqual(len(self.loader.list_scenes()), 1)
        self._make_root({"x/two.usda": "#usda 1.0\n"})
        self.assertEqual(len(self.loader.list_scenes()), 1)
        self.assertEqual(len(self.loader.list_scenes(refresh=True)), 2)

    async def test_open_scene_sample(self):
        info = await self.loader.open_scene(SAMPLE_ID)
        self.assertEqual(info.id, SAMPLE_ID)
        cur = self.loader.current()
        self.assertEqual(cur["scene_id"], SAMPLE_ID)
        self.assertEqual(os.path.normcase(cur["path"]), os.path.normcase(info.path))
        self.assertFalse(cur["loading"])
        self.assertFalse(self.loader.is_loading)
        await wait_frames(10)  # 뒤늦은 OPENED가 external로 한 번 더 나가지 않는지
        opened = self.events.named(EVENT_OPENED)
        self.assertEqual(len(opened), 1, opened)
        self.assertEqual(opened[0]["scene_id"], SAMPLE_ID)
        self.assertEqual(opened[0]["source"], "loader")
        self.assertTrue(opened[0]["path"].endswith("morph_demo.usda"))
        stage = omni.usd.get_context().get_stage()
        for cam in ("CamFront", "CamTop", "CamSide"):
            self.assertTrue(stage.GetPrimAtPath(f"/World/Cameras/{cam}").IsValid(), cam)
        self.assertEqual(stage.GetDefaultPrim().GetPath().pathString, "/World")

    async def test_not_found_and_busy(self):
        with self.assertRaises(KeyError):
            await self.loader.open_scene("samples/does_not_exist.usda")
        self.assertFalse(self.loader.is_loading)

        task = asyncio.ensure_future(self.loader.open_scene(SAMPLE_ID))
        await asyncio.sleep(0)  # 태스크가 예약(loading)까지 진행
        self.assertTrue(self.loader.is_loading)
        self.assertTrue(self.loader.current()["loading"])
        for coro in (self.loader.open_scene(SAMPLE_ID), self.loader.new_scene(), self.loader.reload()):
            with self.assertRaises(RuntimeError):
                await coro
        with self.assertRaises(RuntimeError):
            await self.loader.open_path(self.loader.find_scene(SAMPLE_ID).path)
        await task
        self.assertFalse(self.loader.is_loading)

    async def test_open_failed(self):
        root = self._make_root({BROKEN_NAME: "this is not a usd file {{{"})
        self.settings.set("roots", [f"bad={root}"])
        scene_id = f"bad/{BROKEN_NAME}"
        self.assertIsNotNone(self.loader.find_scene(scene_id))
        with self.assertRaises(StageLoadError) as cm:
            await self.loader.open_scene(scene_id)
        self.assertEqual(cm.exception.code, "open_failed")
        self.assertTrue(cm.exception.path.endswith(BROKEN_NAME))
        self.assertFalse(self.loader.is_loading)
        failed = self.events.named(EVENT_OPEN_FAILED)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["scene_id"], scene_id)
        self.assertEqual(failed[0]["error"], "open_failed")
        self.assertIsInstance(failed[0]["message"], str)

    async def test_reload_discards_changes(self):
        await self.loader.open_scene(SAMPLE_ID)
        stage = omni.usd.get_context().get_stage()
        stage.DefinePrim("/World/MorphTempPrim", "Xform")
        self.assertTrue(stage.GetPrimAtPath("/World/MorphTempPrim").IsValid())
        self.events.events.clear()
        info = await self.loader.reload()
        self.assertIsNotNone(info)
        self.assertEqual(info.id, SAMPLE_ID)
        stage = omni.usd.get_context().get_stage()
        self.assertFalse(stage.GetPrimAtPath("/World/MorphTempPrim").IsValid())
        await wait_frames(10)
        opened = self.events.named(EVENT_OPENED)
        self.assertEqual([(p["scene_id"], p["source"]) for p in opened], [(SAMPLE_ID, "loader")])

    async def test_new_scene_and_reload_precondition(self):
        await self.loader.new_scene()
        self.assertEqual(self.loader.current(), {"scene_id": None, "path": None, "loading": False})
        await wait_frames(10)
        opened = self.events.named(EVENT_OPENED)
        self.assertEqual(len(opened), 1, opened)
        self.assertEqual(opened[0]["path"], "")
        self.assertIsNone(opened[0].get("scene_id"))
        self.assertEqual(opened[0]["source"], "loader")
        with self.assertRaises(RuntimeError):
            await self.loader.reload()

    async def test_open_path(self):
        info = self.loader.find_scene(SAMPLE_ID)
        await self.loader.open_path(info.path)
        self.assertEqual(self.loader.current()["scene_id"], SAMPLE_ID)
        root = self._make_root({"plain.usda": PLAIN_USDA})
        await self.loader.open_path(os.path.join(root, "plain.usda"))
        cur = self.loader.current()
        self.assertIsNone(cur["scene_id"])  # 목록 밖
        self.assertTrue(cur["path"].endswith("plain.usda"))

    async def test_external_open_detected(self):
        info = self.loader.find_scene(SAMPLE_ID)
        ctx = omni.usd.get_context()
        ok, _err = await ctx.open_stage_async(info.path)
        self.assertTrue(ok)
        await wait_until(lambda: self.events.named(EVENT_OPENED), frames=10)
        opened = self.events.named(EVENT_OPENED)
        self.assertEqual(opened[-1]["source"], "external")
        self.assertEqual(opened[-1]["scene_id"], SAMPLE_ID)
        self.assertEqual(self.loader.current()["scene_id"], SAMPLE_ID)
        self.assertFalse(self.loader.is_loading)

        await ctx.new_stage_async()
        await wait_until(lambda: len(self.events.named(EVENT_OPENED)) >= 2, frames=10)
        last = self.events.named(EVENT_OPENED)[-1]
        self.assertEqual((last.get("scene_id"), last["path"], last["source"]), (None, "", "external"))
        self.assertIsNone(self.loader.current()["path"])

    async def test_other_file_opened_during_load_is_external(self):
        # 이 로더의 열기가 진행 중일 때 다른 파일이 열리면: external, scene_id는 그 경로의 목록 조회 값 (None)
        root = self._make_root({"plain.usda": PLAIN_USDA})
        plain = os.path.join(root, "plain.usda")
        op, info = self.loader._reserve_open_scene(SAMPLE_ID)
        try:
            ok, _err = await omni.usd.get_context().open_stage_async(plain)
            self.assertTrue(ok)
            self.assertTrue(await wait_until(lambda: self.events.named(EVENT_OPENED), frames=30))
            first = self.events.named(EVENT_OPENED)[0]
            self.assertEqual((first.get("scene_id"), first["source"]), (None, "external"))
            self.assertEqual(os.path.normcase(first["path"]), os.path.normcase(plain))
            self.assertTrue(self.loader.is_loading)  # 이 로더의 열기는 아직 진행 중
        finally:
            await self.loader._run(op)
        await wait_frames(10)
        opened = self.events.named(EVENT_OPENED)
        self.assertEqual([(p.get("scene_id"), p["source"]) for p in opened], [(None, "external"), (SAMPLE_ID, "loader")])
        self.assertEqual(os.path.normcase(opened[1]["path"]), os.path.normcase(info.path))
        self.assertEqual(self.loader.current()["scene_id"], SAMPLE_ID)

    async def test_sample_camera_settings(self):
        # 샘플은 customLayerData cameraSettings로 Persp를 장면 쪽(미터 단위, 원점 근처)에 둔다
        await self.loader.open_scene(SAMPLE_ID)
        stage = omni.usd.get_context().get_stage()
        cam = stage.GetRootLayer().customLayerData["cameraSettings"]
        self.assertEqual(cam["boundCamera"], "/OmniverseKit_Persp")
        pos = Gf.Vec3d(*cam["Perspective"]["position"])
        target = Gf.Vec3d(*cam["Perspective"]["target"])
        self.assertLess(pos.GetLength(), 30.0)
        self.assertLess(target.GetLength(), 5.0)
        # Persp는 뷰포트가 만든다 (테스트 환경에 뷰포트가 없으면 prim이 없을 수 있다). 적용 시점 때문에 몇 프레임 기다린다.
        persp_path = "/OmniverseKit_Persp"
        await wait_until(lambda: stage.GetPrimAtPath(persp_path).IsValid(), frames=30)
        prim = stage.GetPrimAtPath(persp_path)
        if not prim.IsValid():
            return
        await wait_frames(5)
        world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
        self.assertLess(world.GetLength(), 50.0, f"Persp at {world}: cameraSettings not applied (expected near {pos})")
