import re

from pxr import Usd

from .ebs_simulate_camera import CAMERA_PATH

EQP_PREFIX = "EQP_"
PORT_ID_KEY = "port-id"
OFFSET_KEY  = "offset"
CADX_KEY    = "cad-x"
CADY_KEY    = "cad-y"
NEXT_KEY    = "next-address"
PULS_KEY    = "distance-puls"
ADDR_PATTERN = re.compile(r"^addr0*(\d+)$", re.IGNORECASE)
PORT_PATTERN = re.compile(r"^([A-Za-z0-9]+)_(\d+)$")
CACHE_SUFFIX  = ".ebscache.json"
CACHE_VERSION = 1
READ_BLOCK    = 8 << 20
CAD_PER_UNIT    = 100.0 / 3.0
CAD_SLACK       = 0.1
OFFSET_PER_UNIT = 100000.0
RAIL_PREFIX = "rail_"
SCALE_FIXED = "fixed"
SCALE_PULS  = "puls"
SCALE_SNAP  = "snap"
SCALE_MODES = (SCALE_FIXED, SCALE_PULS, SCALE_SNAP)
SKIP_TYPES = frozenset({"Material", "Shader", "NodeGraph", "GeomSubset", "Camera"})
CUBE_TYPE = "Cube"
CUBE_QUADS = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
              (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
GEOMETRY_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves",
    "Capsule", "Cone", "Cube", "Cylinder", "Sphere", "Plane",
})
VERDICT_HEIGHT = 0.8
GRIP_HEIGHT   = 0.3
OFFSET_HEIGHT = 0.25
GRIP_WIDE   = 1.5 / 8.0
GRIP_TALL   = 1.0 / 16.0
CLASH_HEIGHT   = 0.45
NEIGHBOUR_REACH = 1.5
GROUP_NAMES = ("AMH", "Construction")
STATE_CLASH = "clash"
STATE_TIGHT = "tight"
STATE_CLEAR = "clear"
MIN_GAP_CEILING = 0.1
MIN_GAP_SIDE = 0.6
MARKER_ROOT    = "/EbsCollisionMarkers"
GRIP_ROOT      = "/EbsGrip"
LASER_ROOT     = "/EbsPortLasers"
LASER_COLOR    = (1.0, 0.05, 0.05)
LASER_COLOR_0  = (1.0, 0.75, 0.0)
LASER_RADIUS   = 0.0013
SWEEP_ROOT     = "/EbsPortSweep"
SWEEP_COLOR_PORT = LASER_COLOR
SWEEP_COLOR_EQP  = (0.15, 0.8, 0.3)
SKIN_ROOT      = "/EbsSkin"
SKIN_NAME      = "M_skin"
SKIN_DEEP      = 8
OURS = (MARKER_ROOT, GRIP_ROOT, LASER_ROOT, SWEEP_ROOT, CAMERA_PATH, SKIN_ROOT)
OURS_UNDER = tuple(p + "/" for p in OURS)
NOW = Usd.TimeCode.Default()
FACE_LEFT    = "left"
FACE_RIGHT   = "right"
FACE_CEILING = "ceiling"
FACES = (FACE_LEFT, FACE_CEILING, FACE_RIGHT)
LEAD_FACES  = FACES
RESULT_ORDER  = (FACE_LEFT, FACE_RIGHT, FACE_CEILING)
RESULT_INSIDE = "inside"
COLLIDE_STEPS = (("warm", 50.0), ("sides", 2.0), ("faces", 12.0),
                 ("clearance", 8.0), ("equipment", 12.0), ("verdict", 2.0),
                 ("markers", 14.0))
NUDGE_LIMIT = 1.0
OUTER_STEPS = ("warm", "sides", "faces", "clearance")
INNER_STEPS = ("equipment",)
WARM_CHUNK = 40
LEAD_FRONT  = -1
LEAD_TOL    = 0.001
LEAD_PATCH  = 4000
LEAD_ROOM   = 0.05
GRID = 1
SETTLE_GUESS = 2.0
SETTLE_FRAME = 0.02
SETTLE_CALM  = 3
SETTLE_MOST  = 600
PHASES = (("camera", "Camera"), ("place", "Place"),
          ("skin", "Material"), ("collide", "Collision"),
          ("overlay", "Overlay"))
LOOKS = "Looks"
SHADER_TYPE = "Shader"
CLASH_MARKS   = 200
MEET_WIDE     = 256
GRID_CELLS = 24
OVERLAP_EPS = 1e-6
PROBE_RATIO = 0.01
REACH_RATIO = 1.5
FLAT_TOL    = 0.01
PRECISION_BBOX = "bbox"
PRECISION_MESH = "mesh"
PRECISION_TRI  = "triangle"
PRUNE_TYPES = frozenset({
    "Mesh", "Points", "BasisCurves", "NurbsCurves", "Capsule", "Cone", "Cube",
    "Cylinder", "Sphere", "Plane", "GeomSubset",
    "Material", "Shader", "NodeGraph", "Camera",
})
ANCHOR_DEPTH = 6
PASS_TYPES  = ("Scope",)
MIN_PORTS = 2
MAX_PORTS = 3
PIVOT_TOLERANCE = 1.0
PIVOT_ACROSS = 0.5


try:
    _EVERY_CHILD = Usd.TraverseInstanceProxies()
except Exception:
    _EVERY_CHILD = None


def children(prim):
    """자식 프림. 인스턴스 안쪽까지 본다"""
    if _EVERY_CHILD is not None:
        try:
            return prim.GetFilteredChildren(_EVERY_CHILD)
        except Exception:
            pass
    return prim.GetChildren()
