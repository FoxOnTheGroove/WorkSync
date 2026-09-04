"""EBS 시뮬레이션 공개 API. 구현은 ebs_simulate.py, 카메라만 ebs_simulate_camera.py.

주석은 "뭘 바꾸려면 어디를 보라"는 색인이다. 본문은 전부 한 줄 위임.
단계: init -> align -> collide -> focus. simulate() 는 뒤 셋 연속.
UI 버튼은 셋 (1 Align, 2 Collide, 3 Camera). prepare 는 align 이 품는다.
상수는 각 구현부 파일 최상단.
"""

from .ebs_simulate import EbsSimulate

__all__ = ["EbsSimulateService"]


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API."""

    _simulate = None

    # -- 수명주기 ------------------------------------------------------------

    @classmethod
    def initialize(cls):
        """익스텐션 시작. extension.py on_startup 전용."""
        cls._simulate = EbsSimulate()

    @classmethod
    def finalize(cls):
        """익스텐션 종료. 그린 것과 카메라 프림까지 전부 치운다.

        teardown       카메라 프림을 실제로 지우는 유일한 곳
        """
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    # -- 설정 ----------------------------------------------------------------

    @classmethod
    def set_xml_path(cls, path):
        """포트 XML 경로를 정하는 api.

        set_xml_path   바뀌면 _ready 내려감
        load_ports     파싱과 캐시. 캐시 형식 바꾸려면 여기
        _PortScan      XML 키 이름 바꾸려면 여기 (PORT_ID_KEY 등 상수)
        """
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """2포트 / 3포트 EBS 프림 경로를 정하는 api.

        _do_prepare    포트 수로 둘 중 하나를 고르는 규칙
        """
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_search_root(cls, path):
        """EQP_ 장비를 찾을 서브트리를 정하는 api. 비우면 스테이지 전체.

        _walk          순회 범위와 가지치기. init 이 느리면 여기부터
        PRUNE_TYPES    어떤 타입에서 더 안 내려갈지 조절
        EQP_PREFIX     장비 이름 규칙 바꾸려면 여기
        """
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        """충돌 판정 정밀도를 정하는 api. 'bbox' / 'mesh' / 'triangle'.

        check_collision  bbox<->triangle 전환 지점 (PRECISION_TRI 비교)
        _triangle_hits_box  삼각형 판정 자체를 손보려면 여기
        _nearest_in_prism   빈 면 거리 쪽의 같은 전환
        """
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_offset_scale(cls, mode):
        """포트 offset 을 거리로 바꾸는 방식을 정하는 api.

        _coords_by_offset  'fixed'. 나눗값은 OFFSET_PER_UNIT
        _coords_by_puls    'puls'. 구간 길이 / distance-puls
        _snap_shift        'snap'. puls + 포트 1 을 피봇에 얹음 (기본값)
        _pivot_state       snap 이 얹을지 말지 판정하는 곳
        SCALE_MODES        모드를 늘리려면 여기 + dummy_ui 콤보
        """
        return cls._simulate.set_offset_scale(mode)

    @classmethod
    def set_show_lasers(cls, on):
        """align 이 확인용 포트 레이저를 그릴지 정하는 api. 기본 꺼짐.

        show_port_lasers   그리는 곳. 굵기·색은 LASER_RADIUS, LASER_COLOR
        """
        return cls._simulate.set_show_lasers(on)

    @classmethod
    def set_min_gaps(cls, side, ceiling):
        """3면 최소 여유(m)를 정하는 api. 미달이면 안 닿아도 간섭 판정.

        _face_marks        미달 판정과 색. 기본값은 MIN_GAP_SIDE / MIN_GAP_CEILING
        """
        return cls._simulate.set_min_gaps(side, ceiling)

    @classmethod
    def hide_ebs(cls):
        """EBS 프림 둘을 화면에서 끄는 api. init 과 Clear 가 부른다.

        _show_ebs          끄고 켜는 곳. align 이 쓴 것 하나만 다시 켠다
        """
        return cls._simulate.hide_ebs()

    @classmethod
    def set_rail_root(cls, path):
        """rail_<a>_<b> 레일 프림의 부모 경로를 정하는 api.

        _rails_from        레일 인덱스. 첫 align 때 만든다
        RAIL_PREFIX        레일 이름 규칙 바꾸려면 여기
        """
        return cls._simulate.set_rail_root(path)

    # -- 0단계 init ----------------------------------------------------------

    @classmethod
    def init(cls):
        """스테이지와 XML 을 훑어 캐시를 만드는 api. 지오메트리는 안 읽는다.

        build_index    EQP_ 장비 색인. 범위는 set_search_root
        _stage_boxes   스테이지 상자 목록. collide 가 이걸 훑는다.
                       느리면 여기 -- Init 값의 대부분이다
        load_ports     XML 포트 테이블. <xml>.ebscache.json 에 캐시
        _load_cache    캐시 무효 조건 (CACHE_VERSION, size, mtime)
        EbsSimulateCamera.make   카메라 프림 생성 (없을 때만)
        """
        return cls._simulate.init()

    @classmethod
    def prepare(cls, equipment=""):
        """장비를 확정하고 포트 수·EBS·피봇을 잡는 api. 빈 문자열이면 뷰포트 선택.

        UI 에 버튼은 없다 -- align 이 품는다.

        _resolve_by_name       이름으로 찾기. EQP_ 접두는 없어도 붙는다
        _resolve_by_selection  뷰포트 선택으로 찾기
        resolve_anchor         피봇을 어디로 볼지. 깊이는 ANCHOR_DEPTH
        """
        return cls._simulate.prepare(equipment)

    @classmethod
    def get_selected_equipment(cls):
        """뷰포트 선택에서 장비 경로를 꺼내는 api. UI 의 From Sel.

        _resolve_by_selection
        """
        return cls._simulate.get_selected_equipment()

    @classmethod
    def align(cls, equipment=""):
        """포트 위치를 계산해 EBS 를 놓는 api. prepare 를 품는다.

        compute_port_points  포트 좌표 본체
        find_rail            레일 고르기. 직선/코너 판정은 _rail_axis
        compute_target       놓을 목표점. snap 보정도 여기
        _place_ebs           이동. 회전·스케일은 _align_prims
        CAD_SLACK            비유효축 허용 유격
        """
        return cls._simulate.align(equipment)

    # -- 3단계 collide -------------------------------------------------------

    @classmethod
    def collide(cls):
        """EBS 좌/우/천장 충돌과 여유 거리를 재는 api. 씬에 마커도 그린다.

        check_collision   3면 충돌. 대상 장비는 빠진다 (exclude)
        measure_faces     안 막힌 면의 최단 거리. 범위는 REACH_RATIO
        check_equipment   대상 장비와의 내부 간섭. 삼각형 대 삼각형
        build_verdict     오버레이가 쓸 판정. 패널 높이는 VERDICT_HEIGHT
        show_markers      씬에 그리기. 색은 COLOR_* 상수
        _side_roots       좌우가 무엇을 상대로 판정되는지. 이웃 장비 둘
        _by_face          면마다 후보를 어디서 모을지. 좌우는 이웃, 천장은 전체
        _gather_nearby    후보 추리기. 느리면 여기와 _stage_boxes
        _build_cells      면당 셀 분할 수는 GRID
        _report_stages    걸린 시간을 콘솔에 냄 (탐색/검출)
        """
        return cls._simulate.collide()

    @classmethod
    def get_verdict(cls):
        """마지막 판정을 오버레이용으로 꺼내는 api.

        build_verdict     내용을 바꾸려면 여기
        """
        return cls._simulate.get_verdict()

    # -- 4단계 camera --------------------------------------------------------

    @classmethod
    def focus(cls):
        """카메라를 EBS 정면에 놓고 뷰포트를 그리로 넘기는 api.

        EbsSimulateCamera.place    놓는 곳. 거리는 CAMERA_BACK
        EbsSimulateCamera._grab    좌드래그 공전 + Kit 조작 차단
        _turn / _zoom / _double    공전 / 휠 줌 / 더블클릭 중심 옮기기
        YAW_PER_PIXEL 등           속도와 줌 한계 상수
        """
        return cls._simulate.focus()

    @classmethod
    def refresh_camera(cls):
        """카메라를 Camera 가 놓았던 자리로 되돌리는 api. 궤도 모드는 유지.

        EbsSimulateCamera.reset    place 가 적어둔 _home 을 다시 쓴다
        """
        return cls._simulate.refresh_camera()

    @classmethod
    def release_camera(cls):
        """원래 카메라로 돌아가고 궤도 모드를 끄는 api. 프림은 남긴다.

        EbsSimulateCamera.release
        """
        return cls._simulate.release_camera()

    @classmethod
    def clear_markers(cls):
        """씬에 그린 충돌 마커를 지우는 api."""
        return cls._simulate.clear_markers()

    @classmethod
    def clear_port_lasers(cls):
        """씬에 그린 포트 레이저를 지우는 api."""
        return cls._simulate.clear_port_lasers()

    # -- 일괄 ----------------------------------------------------------------

    @classmethod
    def simulate(cls, equipment=""):
        """align -> collide -> focus 를 연속으로 도는 api. UI 의 SIM.

        simulate       순서를 바꾸려면 여기. 오버레이는 focus 뒤에 뜬다
        """
        return cls._simulate.simulate(equipment)

    # -- 검증용 스윕 (단계와 무관) -------------------------------------------

    @classmethod
    def sweep_ports(cls):
        """색인된 장비 전부의 포트 1 자리를 한 번에 찍어보는 api. UI 버튼은 없다.

        sweep_ports        판정 기준은 PIVOT_TOLERANCE, PIVOT_ACROSS
        show_sweep         그리기. 색은 SWEEP_COLOR_*
        """
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """스윕이 그린 것을 지우는 api."""
        return cls._simulate.clear_sweep()
