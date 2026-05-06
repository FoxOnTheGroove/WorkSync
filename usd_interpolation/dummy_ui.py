from collections import Counter

from pxr import Usd, UsdGeom
import omni.ui as ui


def get_mesh_st_primvar(usd_file_path: str) -> dict | None:
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        print(f"[usd_interpolation] ERROR: Failed to open stage: {usd_file_path}")
        return None

    mesh_prim = None
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh_prim = prim
            break

    if mesh_prim is None:
        print(f"[usd_interpolation] ERROR: No Mesh prim found in {usd_file_path}")
        return None

    print(f"[usd_interpolation] Found mesh prim: {mesh_prim.GetPath()}")

    primvars_api = UsdGeom.PrimvarsAPI(mesh_prim)

    all_primvars = primvars_api.GetPrimvars()
    print(f"[usd_interpolation] Available primvars: {[pv.GetPrimvarName() for pv in all_primvars]}")

    st = primvars_api.GetPrimvar("st")

    if not st or not st.GetAttr().IsValid():
        print("[usd_interpolation] ERROR: 'st' primvar not found or invalid")
        return None

    print(f"[usd_interpolation] st type: {st.GetTypeName()}, interpolation: {st.GetInterpolation()}")

    # Default time 우선, 없으면 첫 번째 time sample로 fallback
    raw_values = st.Get(Usd.TimeCode.Default())
    if raw_values is None:
        time_samples = st.GetAttr().GetTimeSamples()
        print(f"[usd_interpolation] No default value. Time samples: {time_samples}")
        if time_samples:
            raw_values = st.Get(time_samples[0])

    if raw_values is None:
        print("[usd_interpolation] ERROR: st.Get() returned None — no values found")
        return None

    st_count = len(raw_values)
    interp = st.GetInterpolation()
    print(f"[usd_interpolation] st.Get() returned {st_count} values, interpolation: {interp}")

    mesh = UsdGeom.Mesh(mesh_prim)
    tc = Usd.TimeCode.Default()

    if interp == UsdGeom.Tokens.faceVarying:
        fvc = mesh.GetFaceVertexCountsAttr().Get(tc)
        expected = int(sum(fvc)) if fvc is not None else None
    elif interp == UsdGeom.Tokens.vertex:
        points = mesh.GetPointsAttr().Get(tc)
        expected = len(points) if points is not None else None
    elif interp == UsdGeom.Tokens.uniform:
        fvc = mesh.GetFaceVertexCountsAttr().Get(tc)
        expected = len(fvc) if fvc is not None else None
    else:  # constant
        expected = 1

    print(f"[usd_interpolation] Expected st count for '{interp}': {expected}")
    print(f"[usd_interpolation] Actual st count: {st_count} → {'OK' if expected and st_count == expected else 'MISMATCH'}")

    valid_count = min(st_count, expected) if expected is not None else st_count
    counter = Counter(tuple(v) for v in raw_values[:valid_count])

    return {
        "prim_path": str(mesh_prim.GetPath()),
        "interpolation": interp,
        "st_count": st_count,
        "expected": expected,
        "valid_count": valid_count,
        "counter": counter,
    }


class UsdInterpolationUI:
    """
    로컬 USD 파일에서 primvars:st UV를 조회하는 더미 UI.
    - 파일 경로 입력 필드
    - Extract 버튼
    - 결과 출력 영역
    """

    def __init__(self):
        self._window: ui.Window | None = None
        self._result_label: ui.Label | None = None
        self._path_field: ui.StringField | None = None

    def build_ui(self):
        self._window = ui.Window("USD UV Extractor", width=480, height=320)
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 8}):
                ui.Label("USD File Path:", height=20)
                self._path_field = ui.StringField(height=24)
                self._path_field.model.set_value("/path/to/model.usd")

                ui.Button("Extract primvars:st", height=32, clicked_fn=self._on_extract)

                ui.Separator()
                ui.Label("Result:", height=20)
                self._result_label = ui.Label(
                    "Not extracted yet.",
                    word_wrap=True,
                    height=0,
                )

    def _on_extract(self):
        file_path = self._path_field.model.get_value_as_string().strip()
        if not file_path:
            self._set_result("[Error] Please enter a USD file path.")
            return

        data = get_mesh_st_primvar(file_path)
        if data is None:
            self._set_result(f"[Error] primvars:st not found in '{file_path}'. Check the console log for details.")
            return

        counter = data["counter"]
        unique = len(counter)
        sc = data["st_count"]
        exp = data["expected"]
        vc = data["valid_count"]
        status = "OK" if exp and sc == exp else f"MISMATCH (expected {exp})"

        freq_lines = "\n".join(f"  {uv}: {cnt}" for uv, cnt in counter.items())
        text = (
            f"Mesh: {data['prim_path']}  |  Interp: {data['interpolation']}\n"
            f"ST raw: {sc}  |  Expected: {exp}  |  {status}\n"
            f"{unique} unique value(s) in {vc} valid entries\n"
            f"\n{freq_lines}"
        )
        self._set_result(text)
        print(f"[usd_interpolation] unique={unique}, valid_count={vc}, st_count={sc}, expected={exp}")

    def _set_result(self, text: str):
        if self._result_label:
            self._result_label.text = text

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
