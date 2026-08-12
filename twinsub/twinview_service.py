from .twinview import TwinView

__all__ = [
    "cleanup",
    "download_twin",
    "get_local_path",
]


def download_twin(s3_uri: str) -> str:
    """s3 uri 의 .twin 을 임시폴더에 받고 로컬 경로를 반환한다."""
    return TwinView.download_twin(s3_uri)


def get_local_path() -> str:
    """마지막으로 받은 로컬 경로. 없으면 빈 문자열."""
    return TwinView.get_local_path()


def cleanup() -> None:
    """받아둔 임시폴더를 지운다."""
    TwinView.cleanup()
