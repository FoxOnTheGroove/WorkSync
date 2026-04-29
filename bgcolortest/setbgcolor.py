import os
import omni.usd
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
MDL_FILE      = os.path.join(os.path.dirname(__file__), "gradient_background.mdl")


def init_scene():
    stage = omni.usd.get_context().get_stage()

    # Cube at (0, 0, 0)
    UsdGeom.Cube.Define(stage, CUBE_PATH)

    # Camera at (0, 0, 300)
    camera = UsdGeom.Camera.Define(stage, CAMERA_PATH)
    UsdGeom.Xformable(camera).AddTranslateOp().Set(Gf.Vec3d(0, 0, 300))

    # Plane: camera child, translate(0,0,-800), rotX(90), scale(5)
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
    plane.CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying
    ).Set([(0, 0), (1, 0), (1, 1), (0, 1)])

    xform = UsdGeom.Xformable(plane)
    xform.AddTranslateOp().Set(Gf.Vec3d(0, 0, -800))
    xform.AddRotateXOp().Set(90.0)
    xform.AddScaleOp().Set(Gf.Vec3f(5, 5, 5))

    # MDL 파일을 extension 폴더에 저장
    with open(MDL_FILE, "w", encoding="utf-8") as f:
        f.write(MDL_CODE)

    # Material 생성
    material = UsdShade.Material.Define(stage, MATERIAL_PATH)
    shader   = UsdShade.Shader.Define(stage, SHADER_PATH)
    shader.GetImplementationSourceAttr().Set("sourceAsset")
    shader.GetPrim().CreateAttribute(
        "info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset
    ).Set(Sdf.AssetPath(MDL_FILE))
    shader.GetPrim().CreateAttribute(
        "info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token
    ).Set("GradientBackground")

    out = shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput("mdl:surface").ConnectToSource(out)
    material.CreateDisplacementOutput("mdl:displacement").ConnectToSource(out)
    material.CreateVolumeOutput("mdl:volume").ConnectToSource(out)

    UsdShade.MaterialBindingAPI(plane).Bind(material)
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
