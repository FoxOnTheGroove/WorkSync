"""EBS 시뮬레이션 공개 API."""

from .ebs_simulate import EbsSimulate

__all__ = ["EbsSimulateService"]


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API."""

    _simulate = None

    # -- 수명주기 ------------------------------------------------------------

    @classmethod
    def initialize(cls):
        """익스텐션 시작. extension.py on_startup 전용"""
        cls._simulate = EbsSimulate()

    @classmethod
    def finalize(cls):
        """익스텐션 종료. 그린 것과 카메라 프림까지 전부 치운다"""
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    # -- 설정 ----------------------------------------------------------------

    @classmethod
    def set_usd_path(cls, path):
        """열 스테이지 USD 경로. 비우면 지금 열린 것을 쓴다. omniverse:// 도 받는다"""
        return cls._simulate.set_usd_path(path)

    @classmethod
    def set_xml_path(cls, path):
        """포트 XML 경로. omniverse:// 도 받는다"""
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """2포트 / 3포트 EBS 프림 경로"""
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_search_root(cls, path):
        """EQP_ 장비를 찾을 서브트리. 비우면 스테이지 전체"""
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        """충돌 판정 정밀도. 'bbox' / 'mesh' / 'triangle'"""
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_offset_scale(cls, mode):
        """포트 offset 을 거리로 바꾸는 방식. 기본은 snap"""
        return cls._simulate.set_offset_scale(mode)

    @classmethod
    def set_show_lasers(cls, on):
        """align 이 확인용 포트 레이저를 그릴지. 기본 꺼짐"""
        return cls._simulate.set_show_lasers(on)

    @classmethod
    def set_min_gaps(cls, side, ceiling):
        """3면 최소 여유(m). 미달이면 안 닿아도 간섭 판정"""
        return cls._simulate.set_min_gaps(side, ceiling)

    @classmethod
    def hide_ebs(cls):
        """EBS 프림 둘을 화면에서 끈다. init 과 Clear 가 부른다"""
        return cls._simulate.hide_ebs()

    @classmethod
    def set_visible(cls, path, on):
        """그 프림 하나를 켜고 끈다. 몇 개를 건드렸는지 돌려준다"""
        return cls._simulate.set_visible(path, on)

    @classmethod
    def set_rail_root(cls, path):
        """rail_<a>_<b> 레일 프림의 부모 경로"""
        return cls._simulate.set_rail_root(path)

    # -- 0단계 init ----------------------------------------------------------

    @classmethod
    def init(cls):
        """USD 를 열고, XML 을 읽고, 충돌용 캐시까지 만든다. 지오메트리는 안 읽는다"""
        return cls._simulate.init()

    @classmethod
    def prepare(cls, equipment=""):
        """장비를 확정하고 포트 수·EBS·피봇을 잡는다. 빈 문자열이면 뷰포트 선택"""
        return cls._simulate.prepare(equipment)

    @classmethod
    def get_selected_equipment(cls):
        """뷰포트 선택에서 장비 경로를 꺼낸다. UI 의 From Sel"""
        return cls._simulate.get_selected_equipment()

    # -- 1단계 align ---------------------------------------------------------

    @classmethod
    def align(cls, equipment=""):
        """포트 위치를 계산해 EBS 를 놓는다. prepare 를 품는다"""
        return cls._simulate.align(equipment)

    # -- 2단계 collide -------------------------------------------------------

    @classmethod
    def collide(cls):
        """EBS 좌/우/천장 충돌과 여유 거리를 잰다. 씬에 마커도 그린다"""
        return cls._simulate.collide()

    @classmethod
    async def collide_async(cls):
        """collide 를 프레임에 나눠 돌린다. 도는 동안 화면이 안 멈춘다"""
        return await cls._simulate.collide_async()

    @classmethod
    def get_progress(cls):
        """지금 도는 단계가 얼마나 왔나. 0.00 에서 100.00"""
        return cls._simulate.get_progress()

    @classmethod
    def get_verdict(cls):
        """마지막 판정을 오버레이용으로 꺼낸다"""
        return cls._simulate.get_verdict()

    @classmethod
    def get_result(cls, equipment=""):
        """그 장비를 마지막으로 collide 한 결과. 화면은 안 건드린다"""
        return cls._simulate.get_result(equipment)

    @classmethod
    def get_notes(cls):
        """마지막 단계가 남긴 자세한 기록. 콘솔에는 한 줄만 찍힌다"""
        return cls._simulate.get_notes()

    @classmethod
    def get_results(cls):
        """적어 둔 판정 전부를 장비 이름 -> 한 벌 로"""
        return cls._simulate.get_results()

    @classmethod
    def list_results(cls):
        """판정을 적어 둔 장비 이름 전부"""
        return cls._simulate.list_results()

    # -- 3단계 camera --------------------------------------------------------

    @classmethod
    def focus(cls):
        """카메라를 EBS 정면에 놓고 뷰포트를 그리로 넘긴다"""
        return cls._simulate.focus()

    @classmethod
    def tmp_cam(cls):
        """EBS 정면에 카메라를 맞춘다. 궤도는 안 잡는다"""
        return cls._simulate.tmp_cam()

    @classmethod
    def refresh_camera(cls):
        """카메라를 Camera 가 놓았던 자리로 되돌린다. 궤도 모드는 유지"""
        return cls._simulate.refresh_camera()

    @classmethod
    def release_camera(cls):
        """원래 카메라로 돌아가고 궤도 모드를 끈다. 프림은 남긴다"""
        return cls._simulate.release_camera()

    @classmethod
    def clear_markers(cls):
        """씬에 그린 충돌 마커를 지운다. 판정과 깜박임도 같이 놓는다"""
        return cls._simulate.clear_markers()

    @classmethod
    def clear_port_lasers(cls):
        """씬에 그린 포트 레이저를 지운다"""
        return cls._simulate.clear_port_lasers()

    # -- 일괄 ----------------------------------------------------------------

    @classmethod
    def simulate(cls, equipment=""):
        """align -> collide -> focus 를 연속으로. UI 의 SIM"""
        return cls._simulate.simulate(equipment)

    # -- 검증용 스윕 (단계와 무관) -------------------------------------------

    @classmethod
    async def simulate_async(cls, equipment=""):
        """simulate 인데 collide 만 프레임에 나눠 돈다. UI 의 SIM"""
        return await cls._simulate.simulate_async(equipment)

    @classmethod
    def sweep_ports(cls):
        """색인된 장비 전부의 포트 1 자리를 한 번에 찍어본다. UI 버튼은 없다"""
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """스윕이 그린 것을 지운다"""
        return cls._simulate.clear_sweep()
