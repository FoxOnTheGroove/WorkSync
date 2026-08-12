"""TwinView 구현부.

이 모듈은 밖에서 직접 쓰지 않는다. UI와 다른 익스텐션은 twinview_service 만 본다.
그래야 여기 시그니처가 바뀌어도 service 계층에서 흡수된다.

상세 구현은 다음 커밋부터 채운다. 지금은 계층 경계와 상태 흐름만 잡아둔 뼈대다.
"""


class TwinView:
    """뷰어 상태를 들고 있는 클래스.

    인스턴스를 만들지 않고 클래스 자체를 단일 상태로 쓴다. 익스텐션 하나당
    뷰어 하나라는 전제이고, service 가 모듈 함수로 API를 내주기 때문이다.
    """

    _loaded = False
    _source = ""
    _prim_path = "/World/TwinSub"

    # service.set_on_changed 로 갈아끼운다. UI가 폴링하지 않게 하려는 목적.
    _on_changed = None

    # ------------------------------------------------------------ 수명주기

    @classmethod
    def load(cls, source: str) -> bool:
        """소스를 열고 뷰어를 준비한다.

        지금은 경로만 받아두고 성공으로 친다. 실제 로딩은 이후 구현.
        """
        if not source:
            return False

        cls._source = source
        cls._loaded = True
        cls._notify()
        return True

    @classmethod
    def unload(cls) -> None:
        """뷰어를 닫고 상태를 초기화한다."""
        cls.clear()
        cls._source = ""
        cls._loaded = False
        cls._notify()

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    # ------------------------------------------------------------ 정보

    @classmethod
    def get_source(cls) -> str:
        """로드된 소스 경로. 없으면 빈 문자열."""
        return cls._source

    @classmethod
    def get_status(cls) -> str:
        """UI에 그대로 띄울 수 있는 한 줄 상태 문자열."""
        if not cls._loaded:
            return "unloaded"
        return "loaded: {}".format(cls._source)

    # ------------------------------------------------------------ 표시

    @classmethod
    def set_prim_path(cls, prim_path: str) -> None:
        """결과를 기록할 prim 경로."""
        if prim_path:
            cls._prim_path = prim_path

    @classmethod
    def get_prim_path(cls) -> str:
        return cls._prim_path

    @classmethod
    def clear(cls) -> None:
        """스테이지에 기록한 것을 지운다. 아직 기록하는 게 없어 비어 있다."""
        pass

    # ------------------------------------------------------------ 내부

    @classmethod
    def _notify(cls) -> None:
        """상태가 바뀌었음을 구독자에게 알린다.

        콜백에서 터진 예외가 뷰어 상태 전이를 되돌리면 안 되므로 삼킨다.
        """
        if cls._on_changed is None:
            return
        try:
            cls._on_changed()
        except Exception as exc:  # noqa: BLE001 — UI 콜백이 뷰어를 망가뜨리지 않게
            print("[twinsub] on_changed 콜백 실패: {}".format(exc))
