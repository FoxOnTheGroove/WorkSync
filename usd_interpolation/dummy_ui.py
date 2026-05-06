from pxr import Usd, UsdGeom
import omni.ui as ui


def get_mesh_st_primvar(usd_file_path: str) -> dict | None:
    """
    로컬 USD 파일을 열고 첫 번째 Mesh 프림의 primvars:st 데이터를 반환.

    Returns dict:
        - prim_path    : 찾은 메시 프림의 경로
        - values       : GfVec2f 배열 원본
        - indices      : 인덱스 배열 (없으면 빈 리스트)
        - interpolation: faceVarying / vertex / uniform / constant
        - flattened    : 인덱스가 풀린 UV 배열 (face-vertex 순서)
    또는 None (파일 열기 실패 / Mesh 없음 / st primvar 없음)
    """
    stage = Usd.Stage.Open(usd_file_path)
    if not stage:
        return None

    mesh_prim = None
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh_prim = prim
            break

    if mesh_prim is None:
        return None

    primvars_api = UsdGeom.PrimvarsAPI(mesh_prim)
    st = primvars_api.GetPrimvar("st")

    if not st.IsValid():
        return None

    return {
        "prim_path": str(mesh_prim.GetPath()),
        "values": list(st.Get() or []),
        "indices": list(st.GetIndices() or []),
        "interpolation": st.GetInterpolation(),
        "flattened": list(st.ComputeFlattened() or []),
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
                    "아직 추출하지 않았습니다.",
                    word_wrap=True,
                    height=0,
                )

    def _on_extract(self):
        file_path = self._path_field.model.get_value_as_string().strip()
        if not file_path:
            self._set_result("파일 경로를 입력해주세요.")
            return

        data = get_mesh_st_primvar(file_path)
        if data is None:
            self._set_result(f"[오류] '{file_path}' 에서 primvars:st 를 찾지 못했습니다.")
            return

        count = len(data["flattened"])
        idx_info = f"인덱스 {len(data['indices'])}개" if data["indices"] else "인덱스 없음"
        preview = str(data["flattened"][:8])[:-1] + (", ...]" if count > 8 else "]")

        text = (
            f"Mesh Prim     : {data['prim_path']}\n"
            f"Interpolation : {data['interpolation']}\n"
            f"UV 값 수 (raw): {len(data['values'])}\n"
            f"인덱스 정보   : {idx_info}\n"
            f"Flattened UV  : {count}개\n"
            f"미리보기      : {preview}"
        )
        self._set_result(text)
        print(f"[usd_interpolation] {file_path} → {data}")

    def _set_result(self, text: str):
        if self._result_label:
            self._result_label.text = text

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
