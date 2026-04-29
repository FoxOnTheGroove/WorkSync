import os
import omni.usd
import omni.kit.commands
from pxr import UsdGeom, UsdShade, Sdf, Gf

MDL_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gradient_background.mdl").replace("\\", "/")
MATERIAL_PATH = "/World/Looks/GradientBG"
SHADER_PATH   = "/World/Looks/GradientBG/Shader"
CAMERA_PATH   = "/World/Camera"
PLANE_PATH    = "/World/Camera/GradientPlane"
CUBE_PATH     = "/World/Cube"


def init_scene():
    stage = omni.usd.get_context().get_stage()

    # Cube at (0, 0, 0)
    cube = UsdGeom.Cube.Define(stage, CUBE_PATH)
    cube.CreateSizeAttr(50.0)

    # Camera at (0, 0, 300)
    camera = UsdGeom.Camera.Define(stage, CAMERA_PATH)
    UsdGeom.Xformable(camera).AddTranslateOp().Set(Gf.Vec3d(0, 0, 300))

    # Plane: camera child, translate(0,0,-800), rotX(-90)
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

    xform = UsdGeom.Xformable(plane)
    xform.AddTranslateOp().Set(Gf.Vec3d(0, 0, -800))
    xform.AddRotateXOp().Set(-90.0)

    # MDL은 extension 폴더의 static 파일 참조 (런타임 파일 생성 없음)
    omni.kit.commands.execute(
        "CreateMdlMaterialPrim",
        mtl_url=MDL_FILE,
        mtl_name="GradientBackground",
        mtl_path=MATERIAL_PATH,
        select_new_prim=False,
    )

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
