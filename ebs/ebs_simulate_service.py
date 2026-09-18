from .ebs_simulate import instance

__all__ = ["EbsSimulateService"]


class EbsSimulateService:
    """웹이 부르는 API 표면"""

    # -- 동작 -------------------------------------------------------------------

    @classmethod
    def auto_init(cls):
        """USD 를 열고 색인과 포트 표를 만든다"""
        return instance().init()

    @classmethod
    async def simulate(cls, equipment=""):
        """그 장비에 EBS 를 세우고 충돌을 재서 화면에 띄운다"""
        return await instance().run_simulate(equipment)

    @classmethod
    async def clear(cls):
        """그린 것, 레이저, 카메라, EBS, 머티리얼을 놓는다"""
        return await instance().run_clear()

    @classmethod
    def get_result(cls, equipment=""):
        """그 장비의 마지막 판정"""
        return instance().get_result(equipment)

    # -- 질의 -------------------------------------------------------------------

    @classmethod
    def busy(cls):
        """지금 도는 일의 이름. 없으면 빈 칸"""
        return instance().busy()

    @classmethod
    def get_progress(cls):
        """지금 동작의 진행도. 0.00 에서 100.00"""
        return instance().get_progress()

    # -- 설정 -------------------------------------------------------------------

    @classmethod
    def set_usd_path(cls, path):
        """열 USD 파일"""
        return instance().set_usd_path(path)

    @classmethod
    def set_xml_path(cls, path):
        """포트 좌표가 든 XML 파일"""
        return instance().set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """포트 수로 골라 쓸 EBS 프림 둘"""
        return instance().set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_search_root(cls, path):
        """장비를 찾을 뿌리"""
        return instance().set_search_root(path)

    @classmethod
    def set_rail_root(cls, path):
        """포트 높이를 읽을 레일 뿌리"""
        return instance().set_rail_root(path)

    @classmethod
    def set_skin(cls, url):
        """대상 장비에 입힐 것. .mdl 이나 프림 경로나 색"""
        return instance().set_skin(url)

    @classmethod
    def set_near_span(cls, span):
        """카메라 near 컬링 거리"""
        return instance().set_near_span(span)

    @classmethod
    def set_min_gaps(cls, side, ceiling):
        """tight 로 볼 좌우와 천장의 최소 간격"""
        return instance().set_min_gaps(side, ceiling)
