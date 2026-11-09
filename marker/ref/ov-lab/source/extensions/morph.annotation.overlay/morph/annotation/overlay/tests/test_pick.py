__all__ = ["TestAnnotationPick"]

import asyncio
import math

import omni.kit.app
import omni.kit.test
from pxr import Gf

from .. import AnnotationOverlay
from ..pick import NEIGHBOR_PX, Picker, surface_normal, validate_pick_args

# Perspective projection (Gf row-vector convention): [3][3] == 0
PERSPECTIVE = Gf.Matrix4d(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -1, 0, 0, -0.2, 0)
ORTHO = Gf.Matrix4d(1.0)


def plane_surface(w, h, z=-5.0, path="/World/Body", scale=0.01, hole=None):
    """Fake depth: a wall at ``z`` facing the camera (camera at origin looking down -Z).

    Pixel x grows to +X, pixel y grows downwards (-Y). ``hole(px, py)`` -> True = miss.
    """

    def surface(pixel):
        px, py = int(pixel[0]), int(pixel[1])
        if hole is not None and hole(px, py):
            return None
        return path, ((px - w / 2) * scale, -(py - h / 2) * scale, z)

    return surface


class FakeQueryApi:
    """Stands in for ViewportAPI: ``request_query(pixel, callback, query_name=...)``."""

    def __init__(self, resolution=(200, 100), surface=None, respond=True, result=True, path_for=None):
        self.view = Gf.Matrix4d(1.0)
        self.projection = PERSPECTIVE
        self.resolution = resolution
        self.surface = surface or plane_surface(*resolution)
        self.respond = respond  # False: never call back (timeout)
        self.result = result  # request_query return value
        self.queries = []
        self.pending = []
        self.path_for = path_for  # optional (pixel, path) -> path override

    def request_query(self, pixel, callback, query_name=""):
        self.queries.append((tuple(int(c) for c in pixel), query_name))
        if self.result is False:
            return False
        if self.respond:
            hit = self.surface(pixel)
            if hit is None:
                callback("", (0.0, 0.0, 0.0), pixel)
            else:
                path = self.path_for(pixel, hit[0]) if self.path_for else hit[0]
                callback(path, hit[1], pixel)
        else:
            self.pending.append(callback)
        return self.result

    def map_ndc_to_texture(self, mouse):
        u, v = (mouse[0] + 1.0) * 0.5, (mouse[1] + 1.0) * 0.5
        inside = -1.0 <= mouse[0] <= 1.0 and -1.0 <= mouse[1] <= 1.0
        return (u, v), (self if inside else None)

    def subscribe_to_view_change(self, cb):
        return None


async def _wait(fut, frames=10):
    app = omni.kit.app.get_app()
    for _ in range(frames):
        if fut.done():
            break
        await app.next_update_async()
    return fut


class TestAnnotationPick(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.api = FakeQueryApi()
        self.apis = {"Viewport": self.api}
        self.ov = AnnotationOverlay(scene_file_provider=lambda: None, viewport_resolver=self.apis.get)

    async def tearDown(self):
        self.ov._shutdown()

    async def test_surface_normal(self):
        n = surface_normal([0, 0, 0], [1, 0, 0], [0, 1, 0], toward=[0, 0, 5])
        self.assertEqual(n, [0.0, 0.0, 1.0])
        n = surface_normal([0, 0, 0], [1, 0, 0], [0, 1, 0], toward=[0, 0, -5])  # flipped to the camera
        self.assertEqual(n, [-0.0, -0.0, -1.0])
        self.assertIsNone(surface_normal([0, 0, 0], [1, 0, 0], [2, 0, 0], toward=None))  # collinear
        self.assertIsNone(surface_normal([0, 0, 0], [0, 0, 0], [0, 1, 0], toward=None))

    async def test_validate_args(self):
        self.assertEqual(validate_pick_args(0, 1), (0.0, 1.0))
        for bad in ((-0.01, 0.5), (0.5, 1.01), (float("nan"), 0.5), (0.5, float("inf")), ("0.5", 0.5),
                    (True, 0.5), (None, 0.5)):
            with self.assertRaises(ValueError, msg=bad):
                validate_pick_args(*bad)

    async def test_hit_position_and_normal_toward_camera(self):
        fut = self.ov.pick("Viewport", 0.5, 0.25)
        self.assertIsInstance(fut, asyncio.Future)
        result = await fut
        # pixel (100, 25) on the wall at z = -5
        self.assertEqual([q[0] for q in self.api.queries], [(100, 25), (100 + NEIGHBOR_PX, 25), (100, 25 + NEIGHBOR_PX)])
        self.assertEqual(len({q[1] for q in self.api.queries}), 3)  # distinct query names (no replacement)
        self.assertAlmostEqual(result["position"][0], 0.0)
        self.assertAlmostEqual(result["position"][1], 0.25)
        self.assertAlmostEqual(result["position"][2], -5.0)
        n = result["normal"]
        self.assertIsNotNone(n)
        # the raw cross product of (+x, -y) edges points away (-Z): it must be flipped to the camera
        self.assertAlmostEqual(n[2], 1.0)
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in n)), 1.0)
        self.assertEqual(set(result), {"position", "normal"})  # no prim path in the result

    async def test_ortho_normal_uses_camera_axis(self):
        self.api.projection = ORTHO
        result = await self.ov.pick("Viewport", 0.5, 0.5)
        self.assertAlmostEqual(result["normal"][2], 1.0)

    async def test_edge_pixel_uses_inner_neighbours(self):
        result = await self.ov.pick("Viewport", 1.0, 1.0)
        self.assertEqual([q[0] for q in self.api.queries], [(199, 99), (197, 99), (199, 97)])
        self.assertAlmostEqual(result["normal"][2], 1.0)  # still faces the camera

    async def test_miss_returns_none(self):
        self.api.surface = lambda pixel: None
        self.assertIsNone(await self.ov.pick("Viewport", 0.5, 0.5))

    async def test_neighbour_miss_or_other_prim_gives_no_normal(self):
        self.api.surface = plane_surface(200, 100, hole=lambda px, py: px == 100 + NEIGHBOR_PX)
        result = await self.ov.pick("Viewport", 0.5, 0.5)
        self.assertIsNotNone(result["position"])
        self.assertIsNone(result["normal"])

        self.api.surface = plane_surface(200, 100)
        self.api.path_for = lambda pixel, path: "/World/Other" if int(pixel[1]) != 50 else path
        result = await self.ov.pick("Viewport", 0.5, 0.5)
        self.assertIsNone(result["normal"])

    async def test_errors(self):
        """Only KeyError (viewport) and ValueError (x, y) are raised by the call itself."""
        with self.assertRaises(KeyError):
            self.ov.pick("NoSuchViewport", 0.5, 0.5)
        with self.assertRaises(KeyError):
            self.ov.pick("", 0.5, 0.5)
        with self.assertRaises(ValueError):
            self.ov.pick("Viewport", 2.0, 0.5)
        with self.assertRaises(ValueError):
            self.ov.pick("Viewport", 0.5, float("nan"))
        self.assertEqual(self.api.queries, [])  # nothing queried for bad input

    async def _assert_fails_on_await(self, fut, text):
        self.assertIsInstance(fut, asyncio.Future)  # returned, not raised
        self.assertTrue(fut.done())
        with self.assertRaises(RuntimeError) as cm:
            await fut
        self.assertIn(text, str(cm.exception))
        self.assertEqual(self.ov._picker.pending, 0)

    async def test_rejected_query_fails_on_await(self):
        self.api.result = False
        await self._assert_fails_on_await(self.ov.pick("Viewport", 0.5, 0.5), "rejected")

    async def test_none_from_request_query_is_rejection(self):
        """ViewportAPI.request_query returns None when there is no hydra texture: no 5 s wait."""
        self.api.result = None
        self.api.respond = False
        await self._assert_fails_on_await(self.ov.pick("Viewport", 0.5, 0.5), "rejected")
        self.assertEqual(len(self.api.queries), 1)  # neighbours are not queried after the centre failed

    async def test_neighbour_rejected_keeps_position(self):
        calls = []
        surface = plane_surface(200, 100)

        def request_query(pixel, callback, query_name=""):
            calls.append(pixel)
            if len(calls) > 1:
                return None  # neighbours: no hydra texture
            hit = surface(pixel)
            callback(hit[0], hit[1], pixel)
            return True

        self.api.request_query = request_query
        result = await _wait(self.ov.pick("Viewport", 0.5, 0.5))
        self.assertTrue(result.done())
        self.assertAlmostEqual(result.result()["position"][2], -5.0)
        self.assertIsNone(result.result()["normal"])

    async def test_no_resolution_fails_on_await(self):
        self.api.resolution = (0, 0)
        await self._assert_fails_on_await(self.ov.pick("Viewport", 0.5, 0.5), "resolution")
        self.api.resolution = None
        await self._assert_fails_on_await(self.ov.pick("Viewport", 0.5, 0.5), "resolution")
        self.assertEqual(self.api.queries, [])

    async def test_request_query_raising_fails_on_await(self):
        def boom(*_a, **_k):
            raise ValueError("bad pixel")

        self.api.request_query = boom
        await self._assert_fails_on_await(self.ov.pick("Viewport", 0.5, 0.5), "bad pixel")

    async def test_not_queryable_fails_on_await(self):
        await self._assert_fails_on_await(self.ov._pick_api(object(), 0.5, 0.5), "cannot be queried")
        with self.assertRaises(ValueError):  # bad x / y stays synchronous on the editor path too
            self.ov._pick_api(self.api, 1.5, 0.5)

    async def test_timeout(self):
        self.ov._picker = Picker(self.apis.get, timeout=0.05)
        self.api.respond = False
        fut = self.ov.pick("Viewport", 0.5, 0.5)
        await _wait(fut, 200)
        self.assertTrue(fut.done())
        self.assertIsInstance(fut.exception(), TimeoutError)
        self.assertEqual(self.ov._picker.pending, 0)

    async def test_late_neighbours_timeout_keeps_position(self):
        self.ov._picker = Picker(self.apis.get, timeout=0.05)
        calls = []
        surface = plane_surface(200, 100)

        def request_query(pixel, callback, query_name=""):
            calls.append(pixel)
            if len(calls) == 1:  # only the centre answers
                hit = surface(pixel)
                callback(hit[0], hit[1], pixel)
            return True

        self.api.request_query = request_query
        result = await self.ov.pick("Viewport", 0.5, 0.5)
        self.assertAlmostEqual(result["position"][2], -5.0)
        self.assertIsNone(result["normal"])

    async def test_shutdown_cancels_pending(self):
        self.api.respond = False
        fut = self.ov.pick("Viewport", 0.5, 0.5)
        self.assertEqual(self.ov._picker.pending, 1)
        self.ov._shutdown()
        self.assertTrue(fut.cancelled())
        # after shutdown: no exception from the call, the future fails with RuntimeError
        await self._assert_fails_on_await(self.ov._pick_api(self.api, 0.5, 0.5), "shut down")
        await self._assert_fails_on_await(self.ov.pick("Viewport", 0.5, 0.5), "shut down")
        with self.assertRaises(KeyError):  # argument errors stay synchronous after shutdown
            self.ov.pick("NoSuchViewport", 0.5, 0.5)
        # late callbacks after shutdown are ignored
        for cb in self.api.pending:
            cb("/World/Body", (0, 0, 0), (0, 0))
        await omni.kit.app.get_app().next_update_async()

    async def test_real_viewport_cube(self):
        """With a real viewport window (app run), the centre of a framed cube is hit and faces the camera."""
        try:
            from omni.kit.viewport.utility import get_viewport_from_window_name
        except ImportError:
            self.skipTest("omni.kit.viewport.utility not loaded")
        api = get_viewport_from_window_name("Viewport")
        if api is None:
            self.skipTest("no viewport window in the test app (headless): covered by the fake viewport tests")
        import omni.usd
        from pxr import UsdGeom

        ctx = omni.usd.get_context()
        await ctx.new_stage_async()
        stage = ctx.get_stage()
        cube = UsdGeom.Cube.Define(stage, "/World/Cube")
        cube.GetSizeAttr().Set(100.0)
        from omni.kit.viewport.utility import frame_viewport_prims

        app = omni.kit.app.get_app()
        for _ in range(10):
            await app.next_update_async()
        frame_viewport_prims(api, ["/World/Cube"])
        for _ in range(30):
            await app.next_update_async()
        ov = AnnotationOverlay(scene_file_provider=lambda: None)
        try:
            result = await ov.pick("Viewport", 0.5, 0.5)
            self.assertIsNotNone(result, "centre of the framed cube must be hit")
            pos = result["position"]
            self.assertTrue(all(abs(c) <= 50.0 + 1.0 for c in pos), pos)  # on / near the cube surface
            self.assertGreater(max(abs(c) for c in pos), 49.0, pos)
            if result["normal"] is not None:
                cam = Gf.Matrix4d(api.view).GetInverse().Transform(Gf.Vec3d(0, 0, 0))
                to_cam = [cam[i] - pos[i] for i in range(3)]
                self.assertGreater(sum(a * b for a, b in zip(result["normal"], to_cam)), 0.0)
        finally:
            ov._shutdown()
            await ctx.new_stage_async()
