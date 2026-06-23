import asyncio
import omni.kit.asset_converter as asset_converter


async def convert_cad(input_path: str, output_path: str):
    ctx = asset_converter.AssetConverterContext()
    ctx.up_axis = "Y"
    ctx.merge_all_meshes = False
    ctx.smooth_normals = True

    task = asset_converter.get_instance().create_converter_task(
        input_path,
        output_path,
        lambda p, s: print(f"[{p:.1%}] {s}"),
        ctx
    )

    success = await task.wait_until_finished()
    if not success:
        print("실패:", task.get_status(), task.get_error_message())
    else:
        print("완료:", output_path)


if __name__ == "__main__":
    asyncio.ensure_future(convert_cad(
        "C:/path/to/model.stp",
        "C:/path/to/output.usd"
    ))
