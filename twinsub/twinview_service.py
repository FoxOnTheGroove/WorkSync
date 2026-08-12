from .twinview import TwinView

__all__ = [
    "cleanup",
    "download_twin",
    "get_local_path",
    "get_runner",
    "is_loaded",
    "load_twin",
]


def download_twin(s3_uri: str) -> str:
    """s3 uri 의 .twin 을 임시폴더에 받고 로컬 경로를 반환한다."""
    return TwinView.download_twin(s3_uri)


def get_local_path() -> str:
    """마지막으로 받은 로컬 경로. 없으면 빈 문자열."""
    return TwinView.get_local_path()


def load_twin(path: str) -> bool:
    """로컬 .twin 경로로 러너를 세운다."""
    return TwinView.load_twin(path)


def get_runner():
    """세워진 TwinRunner. 없으면 None."""
    return TwinView.get_runner()


def is_loaded() -> bool:
    return TwinView.is_loaded()


def cleanup() -> None:
    """받아둔 임시폴더를 지운다."""
    TwinView.cleanup()
