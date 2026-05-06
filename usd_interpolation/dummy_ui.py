from pxr import UsdGeom
import omni.usd
import omni.ui as ui


def get_mesh_st_primvar(prim_path: str) -> dict | None:
    """
    주어진 경로의 메시 프림에서 primvars:st (UV) 데이터를 반환.

    Returns dict:
        - values       : GfVec2f 배열 원본
        - indices      : 인덱스 배열 (없으면 빈 리스트)
        - interpolation: faceVarying / vertex / uniform / constant
        - flattened    : 인덱스가 풀린 UV 배열 (메시 face-vertex 순서 기준)
    또는 None (스테이지 없음 / 경로 유효하지 않음 / st primvar 없음)
    """
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return None

    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return None

    primvars_api = UsdGeom.PrimvarsAPI(prim)
    st = primvars_api.GetPrimvar("st")

    if not st.IsValid():
        return None

    return {
        "values": list(st.Get() or []),
        "indices": list(st.GetIndices() or []),
        "interpolation": st.GetInterpolation(),
        "flattened": list(st.ComputeFlattened() or []),
    }


class UsdInterpolationUI:
    """
    USD 메시의 primvars:st UV를 조회하는 더미 UI.
    - 경로 입력 필드
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
                ui.Label("Mesh Prim Path:", height=20)
                self._path_field = ui.StringField(height=24)
                self._path_field.model.set_value("/World/Mesh")

                ui.Button("Extract primvars:st", height=32, clicked_fn=self._on_extract)

                ui.Separator()
                ui.Label("Result:", height=20)
                self._result_label = ui.Label(
                    "아직 추출하지 않았습니다.",
                    word_wrap=True,
                    height=0,
                )

    def _on_extract(self):
        prim_path = self._path_field.model.get_value_as_string().strip()
        if not prim_path:
            self._set_result("경로를 입력해주세요.")
            return

        data = get_mesh_st_primvar(prim_path)
        if data is None:
            self._set_result(f"[오류] '{prim_path}' 에서 primvars:st 를 찾지 못했습니다.")
            return

        count = len(data["flattened"])
        idx_info = f"인덱스 {len(data['indices'])}개" if data["indices"] else "인덱스 없음"
        preview = str(data["flattened"][:8])[:-1] + (", ...]" if count > 8 else "]")

        text = (
            f"Interpolation : {data['interpolation']}\n"
            f"UV 값 수 (raw): {len(data['values'])}\n"
            f"인덱스 정보   : {idx_info}\n"
            f"Flattened UV  : {count}개\n"
            f"미리보기      : {preview}"
        )
        self._set_result(text)
        print(f"[usd_interpolation] {prim_path} → {data}")

    def _set_result(self, text: str):
        if self._result_label:
            self._result_label.text = text

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None
