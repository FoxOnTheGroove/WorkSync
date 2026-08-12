"""TwinView 외부 API.

UI와 다른 익스텐션은 twinview.TwinView 를 직접 건드리지 말고 이 모듈만 쓴다.
구현부 시그니처가 바뀌어도 이 계층에서 흡수한다.

    from twinsub import twinview_service as tv

    tv.load("C:/data/sample.usd")
    print(tv.get_status())
"""

from .twinview import TwinView

__all__ = [
    "clear",
    "get_prim_path",
    "get_source",
    "get_status",
    "is_loaded",
    "load",
    "set_on_changed",
    "set_prim_path",
    "unload",
]


# ---------------------------------------------------------------------- 수명주기

def load(source: str) -> bool:
    """소스를 열고 뷰어를 준비한다. 성공하면 True."""
    return TwinView.load(source)


def unload() -> None:
    """뷰어를 닫고 상태를 초기화한다."""
    TwinView.unload()


def is_loaded() -> bool:
    return TwinView.is_loaded()


# ---------------------------------------------------------------------- 정보

def get_source() -> str:
    """로드된 소스 경로. 없으면 빈 문자열."""
    return TwinView.get_source()


def get_status() -> str:
    """UI에 그대로 띄울 수 있는 한 줄 상태 문자열."""
    return TwinView.get_status()


# ---------------------------------------------------------------------- 표시

def set_prim_path(prim_path: str) -> None:
    """결과를 기록할 prim 경로."""
    TwinView.set_prim_path(prim_path)


def get_prim_path() -> str:
    return TwinView.get_prim_path()


def clear() -> None:
    """스테이지에 기록한 것을 제거한다."""
    TwinView.clear()


# ---------------------------------------------------------------------- 이벤트 훅

def set_on_changed(callback) -> None:
    """상태가 바뀔 때 호출. fn() -> None. None 으로 해제.

    UI가 상태를 폴링하지 않고 이 훅으로 갱신하게 하려는 목적이다.
    """
    TwinView._on_changed = callback
