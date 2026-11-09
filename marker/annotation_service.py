from .annotation import instance

__all__ = ["AnnotationService"]


class AnnotationService:
    """웹이 부르는 API 표면"""

    # -- 동작 -------------------------------------------------------------------

    # -- 질의 -------------------------------------------------------------------

    @classmethod
    def ready(cls):
        """일을 받을 수 있으면 True"""
        return instance().ready()

    @classmethod
    def busy(cls):
        """지금 도는 일의 이름. 없으면 빈 칸"""
        return instance().busy()

    # -- 설정 -------------------------------------------------------------------
