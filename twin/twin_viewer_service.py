"""Twin ROM 뷰어 외부 API.

다른 익스텐션과 UI는 twin_viewer.TwinViewer 를 직접 건드리지 말고 이 모듈만 쓴다.
구현부 시그니처가 바뀌어도 이 계층에서 흡수한다.

    from morph.twin import twin_viewer_service as twin

    twin.load_twin(r"D:/models/hx.twin")
    twin.evaluate({"Mass_Flow_HX": 75.0})
"""

from .twin_viewer import DEFAULT_POINT_WIDTH, DEFAULT_PRIM_PATH, TwinViewer

__all__ = [
    "DEFAULT_POINT_WIDTH",
    "DEFAULT_PRIM_PATH",
    "clear",
    "evaluate",
    "get_field_name",
    "get_input_defaults",
    "get_named_selections",
    "get_point_count",
    "get_point_width",
    "get_prim_path",
    "get_rom_name",
    "get_rom_names",
    "get_status",
    "get_twin_file",
    "get_value_range",
    "is_loaded",
    "load_twin",
    "select_rom",
    "set_on_evaluated",
    "set_on_loaded",
    "set_point_width",
    "set_prim_path",
    "unload_twin",
]


# ---------------------------------------------------------------------- 수명주기

def load_twin(twin_file: str) -> bool:
    """.twin 파일을 열고 첫 번째 TBROM을 선택한다. 성공 여부를 반환."""
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
    """출력 필드 이름 (예: 'Velocity'). 범례 표시용."""
    return TwinViewer.get_field_name()


def get_named_selections() -> list:
    """부분 도메인 이름 목록. 전체 도메인은 None 을 넘긴다."""
    return TwinViewer.get_named_selections()


def get_input_defaults() -> dict:
    """입력 이름 → 현재값. UI 컨트롤을 동적으로 구성할 때 쓴다."""
    return TwinViewer.get_inputs()


# ---------------------------------------------------------------------- 평가

def evaluate(inputs: dict, named_selection: "str | None" = None) -> bool:
    """입력값으로 ROM을 평가하고 결과를 스테이지에 기록한다.

    inputs 는 get_input_defaults() 키의 부분집합이어도 된다 (나머지는 현재값 유지).
    named_selection 이 None 이면 전체 도메인.
    """
    return TwinViewer.evaluate(inputs, named_selection)


def get_point_count() -> int:
    """직전 평가로 기록된 포인트 개수."""
    return TwinViewer.get_point_count()


def get_value_range() -> "tuple[float, float] | None":
    """직전 평가의 (min, max) 필드값. 평가 전이면 None."""
    return TwinViewer.get_value_range()


# ---------------------------------------------------------------------- 표시 설정

def set_prim_path(prim_path: str) -> None:
    """포인트 클라우드를 기록할 prim 경로."""
    TwinViewer.set_prim_path(prim_path)


def get_prim_path() -> str:
    return TwinViewer.get_prim_path()


def set_point_width(width: float) -> None:
    """포인트 반경. 다음 평가부터 반영된다."""
    TwinViewer.set_point_width(width)


def get_point_width() -> float:
    return TwinViewer.get_point_width()


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


# ---------------------------------------------------------------------- 편의

def get_status() -> dict:
    """현재 상태 한 번에 조회 — UI 갱신용."""
    return {
        "loaded":      is_loaded(),
        "twin_file":   get_twin_file(),
        "rom_name":    get_rom_name(),
        "field_name":  get_field_name(),
        "prim_path":   get_prim_path(),
        "point_width": get_point_width(),
        "point_count": get_point_count(),
        "value_range": get_value_range(),
    }
