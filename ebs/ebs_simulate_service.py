from .ebs_simulate import EbsSimulate

__all__ = ["EbsSimulateService"]

WORK_SIM     = "Simulate"
WORK_CAMERA  = "Camera"
WORK_ALIGN   = "Align"
WORK_COLLIDE = "Collide"
WORK_CLEAR   = "Clear"
WORK_REFRESH = "Refresh"
WORK_SETTLE  = "Collision Check"


class EbsSimulateService:
    """창이 쓰는 문 전부. 속은 EbsSimulate 하나에 있다"""

    _simulate = None
    _busy = ""

    # -- 수명주기 ----------------------------------------------------------------

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

    # -- 설정 ------------------------------------------------------------------

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
    def set_rail_root(cls, path):
        """rail_<a>_<b> 레일 프림의 부모 경로"""
        return cls._simulate.set_rail_root(path)

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
    def set_near_span(cls, span):
        """근평면을 EBS 폭 절반의 몇 배 앞에 둘지. 0 이면 안 자른다"""
        return cls._simulate.set_near_span(span)

    @classmethod
    def set_min_gaps(cls, side, ceiling):
        """3면 최소 여유(m). 미달이면 안 닿아도 간섭 판정"""
        return cls._simulate.set_min_gaps(side, ceiling)

    @classmethod
    def set_skin(cls, url):
        """대상 장비에 입힐 머티리얼. 씬 안 프림 경로도 .mdl 경로도 받는다"""
        return cls._simulate.set_skin(url)

    @classmethod
    def set_skin_use(cls, on):
        """머티리얼을 갈아입힐지. 끄면 받지도 걸지도 않는다"""
        return cls._simulate.set_skin_use(on)

    @classmethod
    def set_visible(cls, path, on):
        """그 프림 하나를 켜고 끈다. 몇 개를 건드렸는지 돌려준다"""
        return cls._simulate.set_visible(path, on)

    # -- 0단계 init ------------------------------------------------------------

    @classmethod
    def init(cls):
        """USD 를 열고, XML 을 읽고, 충돌용 캐시까지 만든다. 지오메트리는 안 읽는다"""
        return cls._simulate.init()

    @classmethod
    def get_selected_equipment(cls):
        """뷰포트 선택에서 장비 경로를 꺼낸다. UI 의 From Sel"""
        return cls._simulate.get_selected_equipment()

    # -- 1단계 camera ----------------------------------------------------------

    @classmethod
    def focus(cls, equipment=""):
        """EBS 가 설 자리에 카메라를 놓는다. prepare 를 품는다"""
        return cls._simulate.focus(equipment)

    @classmethod
    def refresh_camera(cls):
        """카메라를 Camera 가 놓았던 자리로 되돌린다. 궤도 모드는 유지"""
        return cls._simulate.refresh_camera()

    # -- 2단계 align -----------------------------------------------------------

    @classmethod
    def align_async(cls, equipment=""):
        """align 인데 화면이 잦아들 때까지의 시간도 같이 잰다"""
        return cls._simulate.align_async(equipment)

    # -- 3단계 collide ---------------------------------------------------------

    @classmethod
    async def collide_async(cls):
        """collide 를 프레임에 나눠 돌린다. 도는 동안 화면이 안 멈춘다"""
        return await cls._simulate.collide_async()

    @classmethod
    def get_verdict(cls):
        """마지막 판정을 오버레이용으로 꺼낸다"""
        return cls._simulate.get_verdict()

    # -- 일괄 ------------------------------------------------------------------

    @classmethod
    async def simulate_async(cls, equipment=""):
        """simulate 인데 collide 만 프레임에 나눠 돈다. UI 의 SIM"""
        return await cls._simulate.simulate_async(equipment)

    @classmethod
    def clear_all_async(cls):
        """clear_all 인데 화면이 잦아들 때까지의 시간도 같이 잰다"""
        return cls._simulate.clear_all_async()

    # -- 손잡이 -----------------------------------------------------------------

    @classmethod
    def watch_grip(cls, grip):
        """뷰포트 기즈모가 마우스를 먼저 보도록 걸어 둔다"""
        return cls._simulate.watch_grip(grip)

    @classmethod
    def slide(cls, metres):
        """민 자리로 EBS 를 옮기고 판정을 산수로 고쳐 다시 그린다. 다시 안 잰다"""
        return cls._simulate.slide(metres)

    @classmethod
    def set_nudge(cls, metres):
        """민 거리를 그 값으로. 0 이면 제자리"""
        return cls._simulate.set_nudge(metres)

    @classmethod
    def get_nudge(cls):
        """지금 민 거리(m). 오른쪽이 양수"""
        return cls._simulate.get_nudge()

    @classmethod
    def hold_clash(cls, on):
        """내부충돌연출을 켜고 끈다. 켤 때 그 자리에서 내부 충돌을 다시 잰다"""
        return cls._simulate.hold_clash(on)

    # -- 작업중 표 ---------------------------------------------------------------

    @classmethod
    def busy(cls):
        """지금 도는 일의 이름. 아무것도 안 돌면 빈 칸

        도는 동안 버튼도 손잡이도 안 받는다. 도중에 끼어들면 판정 한 벌을
        바깥에서 갈아엎게 된다. 세우는 것은 일을 띄우는 쪽이 직접 한다
        """
        return cls._busy

    @classmethod
    def begin_work(cls, label):
        """그 일이 시작됐다고 세운다. 띄우기 전에 그 자리에서 세운다

        진행도도 여기서 0 으로 되돌린다. 앞 동작이 끝나며 100 을 찍어 두고
        가므로, 안 되돌리면 표가 뜨자마자 100 으로 보였다가 다시 채워진다
        구간을 여럿 쓰는 동작은 제 코루틴 안에서 set_legs 로 다시 잡는다
        """
        cls._busy = label or "Working"
        if cls._simulate:
            cls._simulate.set_legs(1)
        return cls._busy

    @classmethod
    def end_work(cls):
        """일이 끝났다고 내린다. finally 에서 부른다"""
        cls._busy = ""

    @classmethod
    def settle(cls, name="overlay"):
        """그린 것이 화면에 다 뜰 때까지 기다린다. 작업중 표가 그때까지 선다"""
        return cls._simulate.settle(name)

    @classmethod
    def get_progress(cls):
        """지금 도는 동작이 얼마나 왔나. 0.00 에서 100.00

        작업중 표가 프레임마다 묻는 길이다. 아직 없을 때도 안 터져야 한다
        """
        return cls._simulate.get_progress() if cls._simulate else 0.0

    # -- 한 줄 보고 --------------------------------------------------------------

    @classmethod
    def add_phase(cls, name, spent):
        """바깥에서 잰 시간을 이번 단계 보고에 얹는다"""
        return cls._simulate.add_phase(name, spent)

    @classmethod
    def say_phases(cls):
        """이번 단계에 어디서 얼마나 걸렸나 한 줄로 찍는다"""
        return cls._simulate.say_phases()
