"""Twin ROM 뷰어 구현부.

.twin 런타임 평가 결과(포인트 클라우드)를 USD 스테이지에 기록한다.
현재는 구조만 잡혀 있고 pytwin 연동은 비어 있다 — TODO 표시된 지점이 실제 구현부.
외부에서는 이 클래스를 직접 쓰지 말고 twin_viewer_service 를 경유할 것.
"""

DEFAULT_PRIM_PATH = "/World/TwinROM"
DEFAULT_POINT_WIDTH = 1.0


class TwinViewer:

    _loaded:      bool  = False
    _twin_file:   str   = ""
    _rom_names:   list  = []
    _rom_name:    str   = ""
    _field_name:  str   = ""
    _inputs:      dict  = {}                        # 입력 이름 → 현재값
    _named_selections: list = []

    _prim_path:   str   = DEFAULT_PRIM_PATH
    _point_width: float = DEFAULT_POINT_WIDTH

    _point_count: int   = 0
    _value_range: "tuple[float, float] | None" = None

    # 외부 훅 — service 가 등록한다 (PartsManager 패턴과 동일)
    _on_loaded    = None                            # fn(twin_file: str) -> None
    _on_evaluated = None                            # fn(point_count: int, value_range) -> None

    # ------------------------------------------------------------------ 수명주기

    @classmethod
    def load(cls, twin_file: str) -> bool:
        """.twin 파일을 열고 첫 번째 TBROM을 선택한다."""
        if not twin_file:
            print("[twin] .twin 경로가 비어 있습니다.")
            return False

        # TODO(pytwin): TwinModel(twin_file) 생성 → tbrom_names / inputs /
        #               get_named_selections / get_field_output_name 채우기.
        #               unlicensed export가 아니면 여기서 라이선스 체크아웃 실패가 난다.
        cls._reset_model_state()
        cls._twin_file = twin_file
        cls._loaded    = False

        print(f"[twin] load 미구현: '{twin_file}'")
        return False

    @classmethod
    def unload(cls) -> None:
        # TODO(pytwin): TwinModel.close()
        cls._reset_model_state()
        cls._twin_file = ""
        cls._loaded    = False
        print("[twin] unload")

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    # ------------------------------------------------------------------ 모델 정보

    @classmethod
    def get_twin_file(cls) -> str:
        return cls._twin_file

    @classmethod
    def get_rom_names(cls) -> list:
        return list(cls._rom_names)

    @classmethod
    def get_rom_name(cls) -> str:
        return cls._rom_name

    @classmethod
    def select_rom(cls, rom_name: str) -> bool:
        """평가 대상 TBROM을 바꾼다. 지오메트리가 달라지므로 캐시를 버린다."""
        if rom_name not in cls._rom_names:
            print(f"[twin] 알 수 없는 ROM: '{rom_name}'")
            return False
        cls._rom_name    = rom_name
        cls._point_count = 0
        cls._value_range = None
        # TODO(pytwin): 선택된 ROM 기준으로 _field_name / _named_selections 갱신
        return True

    @classmethod
    def get_field_name(cls) -> str:
        return cls._field_name

    @classmethod
    def get_named_selections(cls) -> list:
        return list(cls._named_selections)

    @classmethod
    def get_inputs(cls) -> dict:
        return dict(cls._inputs)

    # ------------------------------------------------------------------ 평가

    @classmethod
    def evaluate(cls, inputs: dict, named_selection: "str | None" = None) -> bool:
        """주어진 입력으로 ROM을 평가하고 결과를 스테이지에 기록한다."""
        if not cls._loaded:
            print("[twin] 먼저 .twin 파일을 로드하세요.")
            return False

        cls._inputs.update(inputs or {})

        # TODO(pytwin): initialize_evaluation(inputs=...) →
        #               generate_points(rom, False, ns)   → (N, 3) 좌표
        #               generate_snapshot(rom, False, ns) → (N,) 또는 (3N,) 필드값
        #               지오메트리는 입력에 무관하므로 최초 1회만 받아 캐시할 것.
        points, field = None, None
        if points is None or field is None:
            print("[twin] evaluate 미구현")
            return False

        return cls._write_points(points, field)

    @classmethod
    def get_point_count(cls) -> int:
        return cls._point_count

    @classmethod
    def get_value_range(cls) -> "tuple[float, float] | None":
        """직전 평가의 (min, max) 필드값."""
        return cls._value_range

    # ------------------------------------------------------------------ 표시 설정

    @classmethod
    def set_prim_path(cls, prim_path: str) -> None:
        cls._prim_path = prim_path or DEFAULT_PRIM_PATH

    @classmethod
    def get_prim_path(cls) -> str:
        return cls._prim_path

    @classmethod
    def set_point_width(cls, width: float) -> None:
        cls._point_width = max(1e-6, float(width))

    @classmethod
    def get_point_width(cls) -> float:
        return cls._point_width

    @classmethod
    def clear(cls) -> None:
        """기록한 포인트 클라우드 prim을 스테이지에서 제거한다."""
        # TODO(usd): stage.RemovePrim(cls._prim_path)
        cls._point_count = 0
        cls._value_range = None
        print(f"[twin] clear 미구현: '{cls._prim_path}'")

    # ------------------------------------------------------------------ 내부

    @classmethod
    def _reset_model_state(cls) -> None:
        cls._rom_names        = []
        cls._rom_name         = ""
        cls._field_name       = ""
        cls._inputs           = {}
        cls._named_selections = []
        cls._point_count      = 0
        cls._value_range      = None

    @classmethod
    def _write_points(cls, points, field) -> bool:
        """포인트 좌표 + 필드값을 UsdGeom.Points 로 기록한다."""
        # TODO(usd): UsdGeom.Points.Define → points / widths / extent,
        #            필드값을 컬러맵에 태워 primvars:displayColor (vertex) 로 기록.
        #            벡터 필드는 magnitude 로 환산 (배열 크기가 N인지 3N인지로 판별).
        print("[twin] _write_points 미구현")
        return False
