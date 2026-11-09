"""Picking a world position (and surface normal) under a viewport pixel.

- Position: ``viewport_api.request_query(pixel, callback, query_name=...)``. The callback is
  ``(prim_path: str, world_pos: Double3, pixel: Uint2)``; an empty ``prim_path`` is a miss.
- Normal: the query has none, so two neighbour pixels (+2 px in x and in y, or -2 px at the edge)
  are queried too. If all three hit the same prim, the cross product of the two edge vectors is
  the normal, flipped to face the camera. Otherwise the normal is ``None``.
- The prim path is only compared, never returned or stored (annotations are not bound to prims).
  The internal ``raw`` mode (face picking) also hands it to the face computation, which uses it
  for that one computation only.
"""

__all__ = [
    "toward_camera",
    "PICK_TIMEOUT_S",
    "NEIGHBOR_PX",
    "Picker",
    "surface_normal",
    "validate_pick_args",
    "default_viewport_resolver",
]

import asyncio
import math
from typing import Callable, List, Optional, Sequence

import carb

PICK_TIMEOUT_S = 5.0
NEIGHBOR_PX = 2
_EXT_ID = "morph.annotation.overlay"


def default_viewport_resolver(name: str):
    """Viewport API of the viewport window titled ``name`` (``None`` if there is none)."""
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name
    except ImportError:
        return None
    try:
        return get_viewport_from_window_name(name)
    except Exception as e:  # noqa: BLE001
        carb.log_warn(f"[{_EXT_ID}] viewport lookup {name!r} failed: {e}")
        return None


def _finite_unit(name: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"'{name}' must be a number, got {type(v).__name__}")
    f = float(v)
    if not math.isfinite(f) or f < 0.0 or f > 1.0:
        raise ValueError(f"'{name}' must be in 0..1, got {v}")
    return f


def validate_pick_args(x, y):
    """(x, y) as floats in 0..1, else ``ValueError``."""
    return _finite_unit("x", x), _finite_unit("y", y)


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def surface_normal(p0: Sequence[float], p1: Sequence[float], p2: Sequence[float],
                   toward: Optional[Sequence[float]]) -> Optional[List[float]]:
    """Unit normal of the triangle (p0, p1, p2), flipped so that ``dot(n, toward) >= 0``.

    ``None`` when the points are (nearly) collinear or coincident.
    """
    e1, e2 = _sub(p1, p0), _sub(p2, p0)
    n = _cross(e1, e2)
    length = math.sqrt(_dot(n, n))
    scale = max(math.sqrt(_dot(e1, e1)) * math.sqrt(_dot(e2, e2)), 1e-300)
    if length < 1e-12 or length / scale < 1e-6:
        return None
    n = [n[0] / length, n[1] / length, n[2] / length]
    if toward is not None and _dot(n, toward) < 0.0:
        n = [-n[0], -n[1], -n[2]]
    return n


def toward_camera(viewport_api, point) -> Optional[List[float]]:
    """Vector from ``point`` to the camera (perspective) or the camera's backward axis (ortho)."""
    try:
        from pxr import Gf

        inv = Gf.Matrix4d(viewport_api.view).GetInverse()
        proj = viewport_api.projection
        perspective = abs(proj[3][3]) < 1e-9
        if perspective:
            cam = inv.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
            return [cam[0] - point[0], cam[1] - point[1], cam[2] - point[2]]
        back = inv.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0))
        return [back[0], back[1], back[2]]
    except Exception:  # noqa: BLE001
        return None


def _resolution(viewport_api):
    try:
        res = viewport_api.resolution
        w, h = int(res[0]), int(res[1])
    except Exception:  # noqa: BLE001
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def _hit(args) -> Optional[tuple]:
    """(prim_path, [x, y, z]) for a query callback that hit something, else None."""
    if not args:
        return None
    path = args[0]
    if not path:
        return None
    try:
        pos = [float(args[1][0]), float(args[1][1]), float(args[1][2])]
    except Exception:  # noqa: BLE001
        return None
    if not all(math.isfinite(c) for c in pos):
        return None
    return str(path), pos


def _failed_future(exc: BaseException) -> "asyncio.Future":
    fut = asyncio.get_event_loop().create_future()
    fut.set_exception(exc)
    return fut


class _PickJob:
    """Three queries (centre, +x, +y) resolving one future.

    Never raises: a failure to start (no resolution, query rejected) sets the future's exception.
    """

    def __init__(self, picker: "Picker", viewport_api, x: float, y: float, timeout: float, raw: bool = False):
        self._picker = picker
        self._api = viewport_api
        self._raw = raw
        self._loop = asyncio.get_event_loop()
        self.future = self._loop.create_future()
        # a caller cancelling the future (editor cancel) must not leave the job behind
        self.future.add_done_callback(lambda _f: self._cleanup())
        self._results: List[Optional[tuple]] = [None, None, None]
        self._got = [False, False, False]
        self._timer = None
        try:
            self._start(x, y, timeout)
        except Exception as e:  # noqa: BLE001
            self._fail(e if isinstance(e, RuntimeError) else RuntimeError(f"viewport query failed: {e}"))

    def _start(self, x: float, y: float, timeout: float) -> None:
        res = _resolution(self._api)
        if res is None:
            raise RuntimeError("viewport has no render resolution yet")
        w, h = res
        px = min(int(x * w), w - 1)
        py = min(int(y * h), h - 1)
        nx = px + NEIGHBOR_PX if px + NEIGHBOR_PX < w else max(px - NEIGHBOR_PX, 0)
        ny = py + NEIGHBOR_PX if py + NEIGHBOR_PX < h else max(py - NEIGHBOR_PX, 0)
        pixels = [(px, py), (nx, py), (px, ny)]
        self._picker._seq += 1
        base = f"{_EXT_ID}.pick.{self._picker._seq}"
        for i, pixel in enumerate(pixels):
            try:
                ok = self._api.request_query(pixel, self._callback(i), query_name=f"{base}.{i}")
            except Exception as e:  # noqa: BLE001
                if i == 0:
                    raise RuntimeError(f"viewport query failed: {e}") from None
                ok = False
            # False = rejected, None = the viewport has no hydra texture (both: no callback will come)
            if not ok and i == 0:
                raise RuntimeError("viewport rejected the query")
            if not ok:
                self._got[i] = True  # neighbour unavailable: normal will be None
            if self.future.done():
                return  # the centre already answered "miss" synchronously
        if not self.future.done():
            self._timer = self._loop.call_later(timeout, self._on_timeout)

    def _callback(self, i: int) -> Callable:
        def on_query(*args):
            try:
                self._loop.call_soon_threadsafe(self._on_result, i, args)
            except RuntimeError:  # loop closed
                pass

        return on_query

    def _on_result(self, i: int, args) -> None:
        if self.future.done() or self._got[i]:
            return
        self._got[i] = True
        self._results[i] = _hit(args)
        if i == 0 and self._results[0] is None:
            self._finish(None)  # empty space: no need to wait for the neighbours
            return
        if all(self._got):
            self._finish(self._compose())

    def _compose(self) -> Optional[dict]:
        centre = self._results[0]
        if centre is None:
            return None
        path, p0 = centre
        normal = None
        a, b = self._results[1], self._results[2]
        if a is not None and b is not None and a[0] == path and b[0] == path:
            normal = surface_normal(p0, a[1], b[1], toward_camera(self._api, p0))
        if self._raw:
            return {"position": p0, "normal": normal, "path": path}
        return {"position": p0, "normal": normal}

    def _on_timeout(self) -> None:
        self._timer = None
        if self.future.done():
            return
        if self._got[0] and self._results[0] is not None:
            self._finish(self._compose())  # neighbours never answered: position only
            return
        self._fail(TimeoutError("viewport query did not complete"))

    def _finish(self, result) -> None:
        self._cleanup()
        if not self.future.done():
            self.future.set_result(result)

    def _fail(self, exc: BaseException) -> None:
        self._cleanup()
        if not self.future.done():
            self.future.set_exception(exc)

    def cancel(self) -> None:
        self._cleanup()
        if not self.future.done():
            self.future.cancel()

    def _cleanup(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._picker._jobs.discard(self)


class Picker:
    """Turns (viewport, normalized x/y) into a world hit. ``resolver`` maps a viewport window
    name to its viewport API (injectable for tests)."""

    def __init__(self, resolver: Optional[Callable] = None, timeout: float = PICK_TIMEOUT_S):
        self._resolver = resolver or default_viewport_resolver
        self._timeout = timeout
        self._jobs = set()
        self._seq = 0
        self._closed = False

    def resolve(self, viewport_name: str):
        """Viewport API for a window name, else ``KeyError``."""
        if not isinstance(viewport_name, str) or not viewport_name:
            raise KeyError(viewport_name)
        api = self._resolver(viewport_name)
        if api is None:
            raise KeyError(viewport_name)
        return api

    def pick(self, viewport_name: str, x, y) -> "asyncio.Future":
        """``ValueError`` (x, y) and ``KeyError`` (no viewport) are raised right away. Every other
        failure (shut down, no render resolution, query rejected) is set on the returned future."""
        x, y = validate_pick_args(x, y)
        api = self.resolve(viewport_name)
        return self.pick_api(api, x, y)

    def pick_api(self, viewport_api, x, y, raw: bool = False) -> "asyncio.Future":
        """Pick on a viewport API directly (the editor's click path). Only ``ValueError`` is raised
        right away; other failures come back as a future holding ``RuntimeError``.

        ``raw`` (internal, face picking): the result also has ``"path"`` (hit prim path)."""
        x, y = validate_pick_args(x, y)
        if self._closed:
            return _failed_future(RuntimeError("annotation overlay is shut down"))
        if not callable(getattr(viewport_api, "request_query", None)):
            return _failed_future(RuntimeError("viewport cannot be queried"))
        job = _PickJob(self, viewport_api, x, y, self._timeout, raw)
        if not job.future.done():
            self._jobs.add(job)
        return job.future

    @property
    def pending(self) -> int:
        return len(self._jobs)

    def shutdown(self) -> None:
        self._closed = True
        for job in list(self._jobs):
            job.cancel()
        self._jobs = set()
