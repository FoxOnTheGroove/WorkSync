import os
import omni.usd
import omni.kit.commands
from pxr import UsdGeom, UsdShade, Sdf, Gf

MDL_CODE = """
mdl 1.4;
import ::math::*;
import ::state::*;
import ::df::*;

export material GradientBackground(
    uniform color color_start     = color(0.15, 0.15, 0.15),
    uniform color color_end       = color(0.60, 0.60, 0.60),
    uniform float angle_deg       = 90.0,
    uniform float intensity_scale = 3000.0
) = let {
    float3 tc  = state::texture_coordinate(0);
    float2 uv  = float2(tc.x, tc.y);
    float  rad = angle_deg * (3.14159265 / 180.0);
    float2 dir = float2(math::cos(rad), math::sin(rad));
    float  t   = math::clamp(math::dot(uv - float2(0.5, 0.5), dir) + 0.5, 0.0, 1.0);
    color  col = (color_start * (1.0 - t) + color_end * t) * intensity_scale;
} in material(
    surface: material_surface(
        emission: material_emission(emission: df::diffuse_edf(), intensity: col)
    ),
    geometry: material_geometry(cutout_opacity: 1.0)
);
"""

MATERIAL_PATH = "/World/Looks/GradientBG"
SHADER_PATH   = "/World/Looks/GradientBG/Shader"
CAMERA_PATH   = "/World/Camera"
PLANE_PATH    = "/World/Camera/GradientPlane"
CUBE_PATH     = "/World/Cube"
MDL_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gradient_background.mdl").replace("\\", "/")


def init_scene():
    stage = omni.usd.get_context().get_stage()

    # Cube at (0, 0, 0)
    cube = UsdGeom.Cube.Define(stage, CUBE_PATH)
    cube.CreateSizeAttr(50.0)

    # Camera at (0, 0, 300)
    camera = UsdGeom.Camera.Define(stage, CAMERA_PATH)
    UsdGeom.Xformable(camera).AddTranslateOp().Set(Gf.Vec3d(0, 0, 300))

    # Plane: camera child, translate(0,0,-800), rotX(90)
    plane = UsdGeom.Mesh.Define(stage, PLANE_PATH)
    plane.CreatePointsAttr([
        Gf.Vec3f(-1000, 0, -1000),
        Gf.Vec3f( 1000, 0, -1000),
        Gf.Vec3f( 1000, 0,  1000),
        Gf.Vec3f(-1000, 0,  1000),
    ])
    plane.CreateFaceVertexCountsAttr([4])
    plane.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    plane.CreateDoubleSidedAttr(True)
    plane.GetPrim().CreateAttribute(
        "primvars:st", Sdf.ValueTypeNames.TexCoord2fArray
    ).Set([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    plane.GetPrim().CreateAttribute(
        "primvars:st:interpolation", Sdf.ValueTypeNames.Token
    ).Set("vertex")

    xform = UsdGeom.Xformable(plane)
    xform.AddTranslateOp().Set(Gf.Vec3d(0, 0, -800))
    xform.AddRotateXOp().Set(90.0)

    # MDL 파일 저장
    with open(MDL_FILE, "w", encoding="utf-8") as f:
        f.write(MDL_CODE)
    print(f"[gradient_bg] MDL written to: {MDL_FILE}")
    print(f"[gradient_bg] MDL file exists: {os.path.exists(MDL_FILE)}")

    # Omniverse 커맨드로 MDL 머티리얼 생성
    result = omni.kit.commands.execute(
        "CreateMdlMaterialPrim",
        mtl_url=MDL_FILE,
        mtl_name="GradientBackground",
        mtl_path=MATERIAL_PATH,
        select_new_prim=False,
    )
    print(f"[gradient_bg] CreateMdlMaterialPrim result: {result}")

    # 플레인에 머티리얼 바인드
    material = UsdShade.Material(stage.GetPrimAtPath(MATERIAL_PATH))
    UsdShade.MaterialBindingAPI(plane.GetPrim()).Bind(material)
    print("[gradient_bg] init_scene done.")


def update_gradient(color_start, color_end, angle_deg, intensity_scale=3000.0):
    stage = omni.usd.get_context().get_stage()
    prim  = stage.GetPrimAtPath(SHADER_PATH)
    if not prim.IsValid():
        print("[gradient_bg] Shader not found. Run Init first.")
        return

    shader = UsdShade.Shader(prim)
    shader.CreateInput("color_start",     Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color_start))
    shader.CreateInput("color_end",       Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color_end))
    shader.CreateInput("angle_deg",       Sdf.ValueTypeNames.Float).Set(float(angle_deg))
    shader.CreateInput("intensity_scale", Sdf.ValueTypeNames.Float).Set(float(intensity_scale))
