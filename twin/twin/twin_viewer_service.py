"""Twin ROM 뷰어 외부 API.

다른 익스텐션과 UI는 twin_viewer.TwinViewer 를 직접 건드리지 말고 이 모듈만 쓴다.
구현부 시그니처가 바뀌어도 이 계층에서 흡수한다.

    from twin import twin_viewer_service as twin

    twin.load_twin(r"C:/Users/OPTI/Documents/HXVelVectorTBROM_23R2.twin")
    twin.evaluate({"Mass_Flow_HX": 75.0})
"""

from .twin_viewer import DEFAULT_POINT_WIDTH, DEFAULT_PRIM_PATH, TwinViewer

__all__ = [
    "DEFAULT_POINT_WIDTH",
    "DEFAULT_PRIM_PATH",
    "clear",
    "compute_output_sources",
    "evaluate",
    "get_default_end_time",
    "get_default_step_size",
    "get_field_name",
    "get_inputs",
    "get_named_selections",
    "get_output_sources",
    "get_outputs",
    "get_point_count",
    "get_point_width",
    "get_prim_path",
    "get_rom_name",
    "get_rom_names",
    "get_sim_time",
    "get_twin_file",
    "get_value_range",
    "is_loaded",
    "is_output_field_connected",
    "is_playing",
    "load_twin",
    "pause",
    "play",
    "select_rom",
    "stop",
    "set_on_evaluated",
    "set_on_loaded",
    "set_point_width",
    "set_prim_path",
    "set_source_meters_per_unit",
    "show_geometry",
    "unload_twin",
]


# ---------------------------------------------------------------------- 수명주기

def load_twin(twin_file: str) -> bool:
    """.twin 을 열고 첫 TBROM을 선택한 뒤 지오메트리를 회색으로 띄운다."""
    return TwinViewer.load(twin_file)


def unload_twin() -> None:
    """트윈 런타임을 닫고 상태를 초기화한다."""
    TwinViewer.unload()


def is_loaded() -> bool:
    return TwinViewer.is_loaded()


# ---------------------------------------------------------------------- 모델 정보

def get_twin_file() -> str:
    """로드된 .twin 경로. 없으면 빈 문자열."""
    return TwinViewer.get_twin_file()


def get_rom_names() -> list:
    """트윈에 포함된 TBROM 이름 목록."""
    return TwinViewer.get_rom_names()


def get_rom_name() -> str:
    """현재 선택된 TBROM 이름."""
    return TwinViewer.get_rom_name()


def select_rom(rom_name: str) -> bool:
    """평가 대상 TBROM을 바꾼다."""
    return TwinViewer.select_rom(rom_name)


def get_field_name() -> str:
    """출력 필드 이름 (예: 'Velocity')."""
    return TwinViewer.get_field_name()


def get_named_selections() -> list:
    """부분 도메인 이름 목록. 전체 도메인은 None 을 넘긴다."""
    return TwinViewer.get_named_selections()


def get_inputs() -> dict:
    """입력 이름 → 현재값. UI 컨트롤을 동적으로 구성할 때 쓴다."""
    return TwinViewer.get_inputs()


def get_outputs() -> dict:
    """출력 이름 → 마지막 평가값.

    'outField_mode_{i}' 는 TBROM 모드 계수로, 이 값들이 필드를 만들어낸다.
    나머지는 트윈이 함께 내보내는 스칼라 결과다.
    """
    return TwinViewer.get_outputs()


def get_output_sources() -> dict:
    """출력 이름 → [(입력 이름, 기여도)], 기여도 큰 순.

    기여도는 그 출력에서 가장 크게 작용한 입력을 1.0 으로 놓은 상대값이다.
    compute_output_sources() 가 한 번은 돌아야 채워진다 (evaluate 시 자동 수행).
    """
    return TwinViewer.get_output_sources()


def compute_output_sources(rel_delta: float = 0.05) -> dict:
    """입력을 하나씩 섭동해 출력별 입력 기여도를 잰다.

    결합된 시스템 모델은 모든 출력이 모든 입력에 반응하므로 '연결 여부'는
    정보가 되지 않는다. 대신 어느 입력이 지배적인지를 상대값으로 낸다.
    평가 (입력 수 + 2)회가 들고, 끝나면 원래 입력값으로 되돌린다.
    """
    return TwinViewer.compute_output_sources(rel_delta)


def is_output_field_connected() -> bool:
    """TBROM 모드 계수가 트윈 출력에 연결돼 있는지.

    연결은 Twin Builder에서 트윈을 export 할 때 정해지며 런타임에 바꿀 수 없다.
    False 면 필드 계산이 성립하지 않으므로 트윈을 다시 만들어야 한다.
    """
    return TwinViewer.is_output_field_connected()


# ---------------------------------------------------------------------- 표시

def show_geometry(named_selection: "str | None" = None) -> bool:
    """평가 없이 지오메트리만 회색 포인트로 띄운다."""
    return TwinViewer.show_geometry(named_selection)


def evaluate(inputs: dict, named_selection: "str | None" = None) -> bool:
    """입력값으로 ROM을 평가하고 포인트에 색을 입힌다.

    inputs 는 get_inputs() 키의 부분집합이어도 된다 (나머지는 현재값 유지).
    named_selection 이 None 이면 전체 도메인.
    """
    return TwinViewer.evaluate(inputs, named_selection)


def get_point_count() -> int:
    """스테이지에 기록된 포인트 개수."""
    return TwinViewer.get_point_count()


# ---------------------------------------------------------------------- 재생

def play(step_size: float = 0.0, named_selection: "str | None" = None) -> bool:
    """트윈 시각을 진행시키며 매 프레임 색을 갱신한다.

    USD 타임라인을 쓰지 않는다 — 트윈 시각과 스테이지 시각은 별개이고,
    프레임마다 실제로 트윈을 한 스텝 돌리므로 모든 프레임이 실제 트윈 상태다.
    step_size 가 0 이하면 트윈의 기본 step size 를 쓴다.
    pause 후 다시 부르면 멈춘 시각부터 이어서 재개한다.
    """
    return TwinViewer.play(step_size, named_selection)


def pause() -> None:
    """재생만 멈춘다. 트윈 상태가 유지되므로 play 로 이어서 재개할 수 있다."""
    TwinViewer.pause()


def stop(named_selection: "str | None" = None) -> None:
    """재생을 멈추고 트윈 시각을 0 으로 되돌린 뒤 그 상태를 다시 그린다."""
    TwinViewer.stop(named_selection)


def is_playing() -> bool:
    return TwinViewer.is_playing()


def get_sim_time() -> float:
    """트윈 내부 시각(초). USD 타임코드가 아니다."""
    return TwinViewer.get_sim_time()


def get_default_step_size() -> float:
    """트윈에 박혀 있는 기본 step size(초). 모르면 0.0."""
    return TwinViewer.get_default_step_size()


def get_default_end_time() -> float:
    """트윈에 박혀 있는 기본 종료 시각(초). 모르면 0.0."""
    return TwinViewer.get_default_end_time()


def get_value_range() -> "tuple[float, float] | None":
    """직전 평가에서 컬러맵에 쓴 (lo, hi). 평가 전이면 None."""
    return TwinViewer.get_value_range()


# ---------------------------------------------------------------------- 표시 설정

def set_prim_path(prim_path: str) -> None:
    """포인트 클라우드를 기록할 prim 경로."""
    TwinViewer.set_prim_path(prim_path)


def get_prim_path() -> str:
    return TwinViewer.get_prim_path()


def set_point_width(width: float) -> None:
    """포인트 반경. 0 이하면 바운딩박스에서 자동 산출한다."""
    TwinViewer.set_point_width(width)


def get_point_width() -> float:
    return TwinViewer.get_point_width()


def set_source_meters_per_unit(mpu: float) -> None:
    """ROM 좌표의 길이 단위(미터 기준). 미터면 1.0, 밀리미터면 0.001.

    스테이지의 metersPerUnit 과 비교해 좌표를 환산한다. 기본값 1.0.
    """
    TwinViewer.set_source_meters_per_unit(mpu)


def clear() -> None:
    """기록한 포인트 클라우드를 스테이지에서 제거한다."""
    TwinViewer.clear()


# ---------------------------------------------------------------------- 이벤트 훅

def set_on_loaded(callback) -> None:
    """로드 성공 시 호출. fn(twin_file: str) -> None. None 으로 해제."""
    TwinViewer._on_loaded = callback


def set_on_evaluated(callback) -> None:
    """평가 성공 시 호출. fn(point_count: int, value_range) -> None. None 으로 해제."""
    TwinViewer._on_evaluated = callback
