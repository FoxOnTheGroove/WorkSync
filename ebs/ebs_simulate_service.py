from .ebs_simulate import instance

__all__ = ["EbsSimulateService"]


class EbsSimulateService:
    """사용자가 부르는 문 전부. 익스텐션, 창, 오버레이가 쓰는 것은 여기 없다

    여기 있는 것은 웹이 그대로 부를 것들이다. 익스텐션이나 dummy_ui 나
    오버레이가 쓰는 것은 EbsSimulate 를 직접 잡아 쓴다. 그래야 이 표면이
    사용자 관점 그대로 남는다
    """

    # -- 동작 -------------------------------------------------------------------

    @classmethod
    def auto_init(cls):
        """USD 를 열고 색인과 포트 표를 만든다. 스테이지가 다 들어오면 한 번"""
        return instance().init()

    @classmethod
    async def simulate(cls, equipment=""):
        """그 장비에 EBS 를 세우고 충돌을 재서 화면까지 띄운다"""
        return await instance().run_simulate(equipment)

    @classmethod
    def get_result(cls, equipment=""):
        """그 장비의 마지막 판정. 면별 간격, 내부 충돌, 세울 수 있나"""
        return instance().get_result(equipment)

    # -- 질의 -------------------------------------------------------------------

    @classmethod
    def busy(cls):
        """지금 도는 일의 이름. 아무것도 안 돌면 빈 칸"""
        return instance().busy()

    @classmethod
    def get_progress(cls):
        """지금 도는 동작이 얼마나 왔나. 0.00 에서 100.00"""
        return instance().get_progress()
