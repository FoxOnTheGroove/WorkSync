from .twinview import TwinView

__all__ = [
    "cleanup",
    "download_twin",
    "get_local_path",
    "get_runner",
    "is_loaded",
    "load_twin",
    "play",
    "rom_show",
    "stop",
]


def _require_runner():
    """현재 대상 러너. 없으면 예외 — UI가 이유를 그대로 띄운다."""
    runner = TwinView.get_runner()
    if runner is None:
        raise ValueError("먼저 .twin 을 로드할 것")
    return runner


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


def rom_show(prim_path: str) -> bool:
    """prim path 아래에 rom 포인트 클라우드를 띄운다."""
    return TwinView.rom_show(prim_path, _require_runner())


def play() -> None:
    """현재 대상 러너를 돌린다."""
    TwinView.play(_require_runner())


def stop() -> None:
    """현재 대상 러너를 멈춘다."""
    TwinView.stop(_require_runner())


def cleanup() -> None:
    """받아둔 임시폴더를 지운다."""
    TwinView.cleanup()
