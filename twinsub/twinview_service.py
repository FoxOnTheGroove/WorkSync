from .twinview import TwinView

__all__ = [
    "cleanup",
    "download_twin",
    "get_inputs",
    "get_local_path",
    "get_outputs",
    "get_runner",
    "is_loaded",
    "is_playing",
    "load_twin",
    "play",
    "get_interval",
    "get_scale",
    "rom_show",
    "set_input",
    "set_interval",
    "set_scale",
    "stop",
]


def _require_target():
    """현재 대상 (twin path, runner). 없으면 예외 — UI가 이유를 그대로 띄운다."""
    path = TwinView.get_local_path()
    runner = TwinView.get_runner(path)
    if runner is None:
        raise ValueError("먼저 .twin 을 로드할 것")
    return path, runner


def download_twin(s3_uri: str) -> str:
    """s3 uri 의 .twin 을 임시폴더에 받고 로컬 경로를 반환한다."""
    return TwinView.download_twin(s3_uri)


def get_local_path() -> str:
    """마지막으로 받은 로컬 경로. 없으면 빈 문자열."""
    return TwinView.get_local_path()


def load_twin(path: str) -> bool:
    """로컬 .twin 경로로 러너를 세운다."""
    return TwinView.load_twin(path)


def get_runner(path: str = ""):
    """path 의 TwinRunner. path 를 비우면 현재 대상. 없으면 None."""
    return TwinView.get_runner(path)


def is_loaded() -> bool:
    return TwinView.is_loaded()


def get_inputs() -> dict:
    """입력 이름 → 현재값."""
    return TwinView.get_inputs(_require_target()[1])


def get_outputs() -> dict:
    """출력 이름 → 마지막 값."""
    return TwinView.get_outputs(_require_target()[1])


def set_input(name: str, value: float) -> None:
    """입력 하나를 바꾼다."""
    TwinView.set_input(_require_target()[1], name, float(value))


def rom_show(prim_path: str) -> bool:
    """prim path 아래에 rom 포인트 클라우드를 띄운다."""
    return TwinView.rom_show(prim_path, _require_target()[1])


def play() -> bool:
    """현재 대상 러너를 돌린다. 이미 재생 중이면 False."""
    return TwinView.play(*_require_target())


def stop() -> bool:
    """현재 대상 러너를 멈춘다. 재생 중이 아니면 False."""
    return TwinView.stop(*_require_target())


def set_interval(seconds: float) -> None:
    """재생 중 필드를 다시 읽는 주기(초). 기본 0.5."""
    TwinView.set_interval(seconds)


def get_interval() -> float:
    return TwinView.get_interval()


def set_scale(scale: float) -> None:
    """필드 스케일. 기본 1.0."""
    TwinView.set_scale(scale)


def get_scale() -> float:
    return TwinView.get_scale()


def is_playing(path: str = "") -> bool:
    """path 가 재생 중인지. path 를 비우면 현재 대상."""
    return TwinView.is_playing(path)


def cleanup() -> None:
    """받아둔 임시폴더를 지운다."""
    TwinView.cleanup()
