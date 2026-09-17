"""EBS 시뮬레이션 공개 API."""

from .ebs_simulate import EbsSimulate

__all__ = ["EbsSimulateService"]

WORK_SIM     = "Simulate"
WORK_CAMERA  = "Camera"
WORK_ALIGN   = "Align"
WORK_COLLIDE = "Collide"
WORK_CLEAR   = "Clear"
WORK_REFRESH = "Refresh"
WORK_SETTLE  = "Recheck"


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API."""

    _simulate = None
    _busy = ""

    # -- 작업중 ----------------------------------------------------------------

    @classmethod
    def busy(cls):
        """지금 도는 일의 이름. 아무것도 안 돌면 빈 칸

        도는 동안 버튼도 손잡이도 안 받는다. 도중에 끼어들면 판정 한 벌을
        바깥에서 갈아엎게 된다. 세우는 것은 일을 띄우는 쪽이 직접 한다
        """
        return cls._busy

    @classmethod
    def begin_work(cls, label):
        """그 일이 시작됐다고 세운다. 띄우기 전에 그 자리에서 세운다"""
        cls._busy = label or "Working"
        return cls._busy

    @classmethod
    def end_work(cls):
        """일이 끝났다고 내린다. finally 에서 부른다"""
        cls._busy = ""

    @classmethod
    def mark_move(cls):
        """손잡이를 잡은 순간을 적어 둔다. 처음 민 뒤 보고에 쓴다"""
        if cls._simulate:
            cls._simulate.mark_move()

    @classmethod
    async def watch_move(cls):
        """처음 민 뒤 무엇이 남았는지 한 줄로 찍는다"""
        if cls._simulate:
            await cls._simulate.watch_move()

    @classmethod
    def settle(cls, name="overlay"):
        """그린 것이 화면에 다 뜰 때까지 기다린다. 작업중 표가 그때까지 선다"""
        return cls._simulate.settle(name)

    @classmethod
    def get_step(cls):
        """지금 도는 단계의 이름. 진행도 옆에 적을 것"""
        return cls._simulate.get_step() if cls._simulate else ""

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
    def set_near_span(cls, span):
        """근평면을 EBS 폭 절반의 몇 배 앞에 둘지. 0 이면 안 자른다"""
        return cls._simulate.set_near_span(span)

    @classmethod
    def set_checks(cls, outer, inner):
        """collide 가 외부(3면)와 내부(장비끼리) 중 무엇을 잴지"""
        return cls._simulate.set_checks(outer, inner)

    @classmethod
    def nudge(cls, step):
        """놓을 자리를 EBS 좌우로 step(m) 만큼 더 민다. 누적 거리를 돌려준다"""
        return cls._simulate.nudge(step)

    @classmethod
    def watch_grip(cls, grip):
        """뷰포트 기즈모가 마우스를 먼저 보도록 걸어 둔다"""
        return cls._simulate.watch_grip(grip)

    @classmethod
    def hold_camera(cls, on):
        """궤도 조작을 잠깐 놓는다. 뷰포트 손잡이를 끄는 동안"""
        return cls._simulate.hold_camera(on)

    @classmethod
    def hold_clash(cls, on):
        """내부충돌연출을 켜고 끈다. 켤 때 그 자리에서 내부 충돌을 다시 잰다"""
        return cls._simulate.hold_clash(on)

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
    def add_phase(cls, name, spent):
        """바깥에서 잰 시간을 이번 단계 보고에 얹는다"""
        return cls._simulate.add_phase(name, spent)

    @classmethod
    def say_phases(cls):
        """이번 단계에 어디서 얼마나 걸렸나 한 줄로 찍는다"""
        return cls._simulate.say_phases()

    @classmethod
    def clear_all(cls):
        """Clear 버튼 한 번. 어디서 얼마가 걸렸는지 로그에 찍는다"""
        return cls._simulate.clear_all()

    @classmethod
    def align_async(cls, equipment=""):
        """align 인데 화면이 잦아들 때까지의 시간도 같이 잰다"""
        return cls._simulate.align_async(equipment)

    @classmethod
    def clear_all_async(cls):
        """clear_all 인데 화면이 잦아들 때까지의 시간도 같이 잰다"""
        return cls._simulate.clear_all_async()

    @classmethod
    def set_skin_use(cls, on):
        """머티리얼을 갈아입힐지. 끄면 받지도 걸지도 않는다"""
        return cls._simulate.set_skin_use(on)

    @classmethod
    def set_skin(cls, url):
        """대상 장비에 입힐 머티리얼. 씬 안 프림 경로도 .mdl 경로도 받는다"""
        return cls._simulate.set_skin(url)

    @classmethod
    def strip_skin(cls):
        """갈아입힌 머티리얼을 걷고 원래 색으로"""
        return cls._simulate.strip_skin()

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

    # -- 2단계 align ---------------------------------------------------------

    @classmethod
    def align(cls, equipment=""):
        """포트 위치를 계산해 EBS 를 놓는다. prepare 를 품는다"""
        return cls._simulate.align(equipment)

    # -- 3단계 collide -------------------------------------------------------

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

    # -- 1단계 camera --------------------------------------------------------

    @classmethod
    def focus(cls, equipment=""):
        """EBS 가 설 자리에 카메라를 놓는다. prepare 를 품는다"""
        return cls._simulate.focus(equipment)

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
        """camera -> align -> collide 를 연속으로. UI 의 SIM"""
        return cls._simulate.simulate(equipment)

    @classmethod
    async def simulate_async(cls, equipment=""):
        """simulate 인데 collide 만 프레임에 나눠 돈다. UI 의 SIM"""
        return await cls._simulate.simulate_async(equipment)

    # -- 검증용 스윕 (단계와 무관) -------------------------------------------

    @classmethod
    def sweep_ports(cls):
        """색인된 장비 전부의 포트 1 자리를 한 번에 찍어본다. UI 버튼은 없다"""
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """스윕이 그린 것을 지운다"""
        return cls._simulate.clear_sweep()
