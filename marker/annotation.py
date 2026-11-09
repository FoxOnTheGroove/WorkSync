__all__ = ["Annotation", "instance", "forget"]


class Annotation:
    """주석의 속"""

    def __init__(self):
        """설정과 상태 자리만 비워 둔다"""
        self._busy: str = ""

    # -- 질의 -------------------------------------------------------------------

    def ready(self) -> bool:
        """일을 받을 수 있으면 True"""
        return True

    def busy(self) -> str:
        """지금 도는 일의 이름. 없으면 빈 칸"""
        return self._busy

    # -- 정리 -------------------------------------------------------------------

    def teardown(self) -> None:
        """들고 있던 것을 놓는다"""
        self._busy = ""


_ONE = None


def instance():
    """이 킷앱이 쓰는 Annotation 하나. 없으면 그 자리에서 만든다"""
    global _ONE
    if _ONE is None:
        _ONE = Annotation()
    return _ONE


def forget() -> None:
    """들고 있던 것을 치우고 놓는다"""
    global _ONE
    if _ONE is not None:
        _ONE.teardown()
    _ONE = None
