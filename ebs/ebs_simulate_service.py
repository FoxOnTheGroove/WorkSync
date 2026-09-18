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
    async def clear(cls):
        """그린 것, 레이저, 카메라, EBS, 머티리얼을 전부 놓는다"""
        return await instance().run_clear()

    @classmethod
    def get_equipment_names(cls, starts=""):
        """장비 이름 목록. starts 를 주면 EQP_starts 로 시작하는 것만"""
        return instance().get_equipment_names(starts)

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
        """장비를 찾을 뿌리. 좁힐수록 Init 이 빠르다"""
        return instance().set_search_root(path)

    @classmethod
    def set_rail_root(cls, path):
        """레일 뿌리. 포트 높이를 여기서 읽는다"""
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
        """좌우와 천장에서 이만큼 안 떨어지면 tight 로 본다"""
        return instance().set_min_gaps(side, ceiling)
