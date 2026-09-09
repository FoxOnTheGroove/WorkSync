"""EBS 시뮬레이션 공개 API."""

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

        teardown  카메라 프림을 실제로 지우는 유일한 곳
        """
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    # -- 설정 ----------------------------------------------------------------

    @classmethod
    def set_usd_path(cls, path):
        """열 스테이지 USD 경로. 비우면 지금 열린 것을 쓴다. omniverse:// 도 받는다.

        open_stage  여는 곳. 같은 경로가 이미 열려 있으면 안 연다
        """
        return cls._simulate.set_usd_path(path)

    @classmethod
    def set_xml_path(cls, path):
        """포트 XML 경로. omniverse:// 도 받는다.

        load_ports  파싱과 캐시 (<xml> + CACHE_SUFFIX 옆자리). 키 이름은 _PortScan
        _remote     원격 IO 는 _stamp_of / _read_bytes / _write_text 셋뿐이다
        """
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """2포트 / 3포트 EBS 프림 경로.

        _do_prepare  포트 수로 둘 중 하나를 고르는 규칙
        """
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_search_root(cls, path):
        """EQP_ 장비를 찾을 서브트리. 비우면 스테이지 전체.

        _walk  순회 범위와 가지치기. init 이 느리면 여기부터 (PRUNE_TYPES)
        """
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        """충돌 판정 정밀도. 'bbox' / 'mesh' / 'triangle'.

        check_collision     bbox<->triangle 전환 지점 (PRECISION_TRI 비교)
        _nearest_in_prism   빈 면 거리 쪽의 같은 전환
        """
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_offset_scale(cls, mode):
        """포트 offset 을 거리로 바꾸는 방식. 기본은 snap.

        _coords_by_offset / _coords_by_puls / _snap_shift  세 방식의 본체
        SCALE_MODES  모드를 늘리려면 여기 + dummy_ui 콤보
        """
        return cls._simulate.set_offset_scale(mode)

    @classmethod
    def set_show_lasers(cls, on):
        """align 이 확인용 포트 레이저를 그릴지. 기본 꺼짐.

        show_port_lasers  그리는 곳. 굵기·색은 LASER_RADIUS, LASER_COLOR
        """
        return cls._simulate.set_show_lasers(on)

    @classmethod
    def set_min_gaps(cls, side, ceiling):
        """3면 최소 여유(m). 미달이면 안 닿아도 간섭 판정.

        _face_marks  미달 판정과 색. 기본값은 MIN_GAP_SIDE / MIN_GAP_CEILING
        """
        return cls._simulate.set_min_gaps(side, ceiling)

    @classmethod
    def hide_ebs(cls):
        """EBS 프림 둘을 화면에서 끈다. init 과 Clear 가 부른다.

        _show_ebs  끄고 켜는 곳. align 이 쓴 것 하나만 다시 켠다
        """
        return cls._simulate.hide_ebs()

    @classmethod
    def set_rail_root(cls, path):
        """rail_<a>_<b> 레일 프림의 부모 경로.

        _rails_from  레일 인덱스. 첫 align 때 만든다 (RAIL_PREFIX)
        """
        return cls._simulate.set_rail_root(path)

    # -- 0단계 init ----------------------------------------------------------

    @classmethod
    def init(cls):
        """USD 를 열고, XML 을 읽고, 충돌용 캐시까지 만든다. 지오메트리는 안 읽는다.

        _stage_boxes   스테이지 상자 목록. Init 값의 대부분이다. EBS 는 안 담는다
        _bounds_cache  공유 바운드 캐시. 움직이는 EBS 는 _moving_cache 로 따로
        """
        return cls._simulate.init()

    @classmethod
    def prepare(cls, equipment=""):
        """장비를 확정하고 포트 수·EBS·피봇을 잡는다. 빈 문자열이면 뷰포트 선택.

        _resolve_by_name / _resolve_by_selection  찾는 두 길
        resolve_anchor  피봇을 어디로 볼지. 깊이는 ANCHOR_DEPTH
        """
        return cls._simulate.prepare(equipment)

    @classmethod
    def get_selected_equipment(cls):
        """뷰포트 선택에서 장비 경로를 꺼낸다. UI 의 From Sel.

        _resolve_by_selection
        """
        return cls._simulate.get_selected_equipment()

    # -- 1단계 align ---------------------------------------------------------

    @classmethod
    def align(cls, equipment=""):
        """포트 위치를 계산해 EBS 를 놓는다. prepare 를 품는다.

        compute_port_points / compute_target  포트 좌표와 놓을 목표점 (snap 보정 포함)
        find_rail  레일 고르기. 직선/코너 판정은 _rail_axis. 유격은 CAD_SLACK
        _place_ebs  이동. 회전·스케일은 _align_prims 와 _write_transform
        """
        return cls._simulate.align(equipment)

    # -- 2단계 collide -------------------------------------------------------

    @classmethod
    def collide(cls):
        """EBS 좌/우/천장 충돌과 여유 거리를 잰다. 씬에 마커도 그린다.

        _do_collide  이 단계의 순서가 전부 여기 있다
        check_collision / measure_faces / check_equipment  3면, 빈 면 거리, 내부 간섭.
                     막힌 면은 안쪽으로 파고든 깊이를 재서 음수로 준다
        _flat_gap / _mesh_parts  한 덩어리 안에서 같은 높이인 면들을 합쳐 그 중앙
                     (H 빔의 다리 둘처럼)
        _mesh_local / _cube_local  삼각형을 어디서 얻나. Cube 는 제 크기로 만들어
                     비스듬해도 정확하다. 그래도 못 얻으면 상자로 잰다 (_boxed 로 보고)
        _face_marks  세 면 다 앞 모서리 중점에서 면에 수직으로 긋는다. 메시에 안
                     묻히는 자리다 (LEAD_FACES, LEAD_FRONT)
        _lead_path   잰 자리를 가리키는 안내선. 뒤로 갔다가 한 번만 꺾는다. 꺾는
                     축은 잰 축도 앞뒤 축도 아닌 나머지 -- 좌우는 위아래, 천장은 옆
        _lead_patch / _sliced / _stop_at  갈 길 언저리(LEAD_ROOM)를 스테이지 전체에서
                     훑어 그 깊이에서 자르고 (평평하면 면, 걸치면 단면 선, 점이
                     없으면 상자), 아무 데나 처음 닿으면 멈춘다 (LEAD_TOL, LEAD_PATCH)
        EbsSimulateMarks  씬에 그리는 것은 전부 ebs_simulate_overlay 에 있다.
                     판, 선, 화살촉, 안내선, 눈금, 충돌 상자, 깜박임과 그 상수들
                     (GAP_*, LEAD_OVER, COLOR_*)
        show_markers / build_verdict  씬에 그리기와 오버레이가 읽을 판정
        """
        return cls._simulate.collide()

    @classmethod
    def get_verdict(cls):
        """마지막 판정을 오버레이용으로 꺼낸다.

        build_verdict    내용을 바꾸려면 여기
        _verdict_panel   못 세울 때만 한 줄 띄운다 (VERDICT_HEIGHT). 세울 수 있으면
                         중앙에 아무것도 안 띄운다. 내부 간섭 한 줄은 CLASH_HEIGHT
                         높이에 따로. 글은 ebs_simulate_overlay 맨 위에 모여 있다
        """
        return cls._simulate.get_verdict()

    @classmethod
    def get_result(cls, equipment=""):
        """그 장비를 마지막으로 collide 한 결과. 화면은 안 건드린다.

        장비명, 포트 수, 좌/우/상단/내부 한 줄 요약, 3면의 간격과 상대,
        내부에 걸린 메시 목록, 설치 가능 여부. 기록이 없어도 키는 다 있다
        _keep_result  collide 가 장비 이름으로 적어 둔다. Init 까지 남는다.
                      간격은 적어 둔 그대로, 여유/간섭은 지금 잣대로 읽는다
        """
        return cls._simulate.get_result(equipment)

    @classmethod
    def list_results(cls):
        """판정을 적어 둔 장비 이름 전부."""
        return cls._simulate.list_results()

    # -- 3단계 camera --------------------------------------------------------

    @classmethod
    def focus(cls):
        """카메라를 EBS 정면에 놓고 뷰포트를 그리로 넘긴다.

        EbsSimulateCamera.place  놓는 곳. 거리는 CAMERA_BACK
        _grab / _turn / _zoom / _double  좌드래그 공전, 휠 줌, 더블클릭 중심 옮기기
        FADE_OTHERS  양옆 빼고 투명하게. 느려서 기본 꺼짐 (hide_other_equipment)
        """
        return cls._simulate.focus()

    @classmethod
    def refresh_camera(cls):
        """카메라를 Camera 가 놓았던 자리로 되돌린다. 궤도 모드는 유지.

        EbsSimulateCamera.reset  place 가 적어둔 _home 을 다시 쓴다
        """
        return cls._simulate.refresh_camera()

    @classmethod
    def release_camera(cls):
        """원래 카메라로 돌아가고 궤도 모드를 끈다. 프림은 남긴다.

        EbsSimulateCamera.release
        show_equipment  투명하게 했던 것을 되돌린다 (Clear 버튼)
        """
        return cls._simulate.release_camera()

    @classmethod
    def clear_markers(cls):
        """씬에 그린 충돌 마커를 지운다. 판정과 깜박임도 같이 놓는다."""
        return cls._simulate.clear_markers()

    @classmethod
    def clear_port_lasers(cls):
        """씬에 그린 포트 레이저를 지운다."""
        return cls._simulate.clear_port_lasers()

    # -- 일괄 ----------------------------------------------------------------

    @classmethod
    def simulate(cls, equipment=""):
        """align -> collide -> focus 를 연속으로. UI 의 SIM.

        simulate  순서를 바꾸려면 여기. 오버레이는 focus 뒤에 뜬다
        """
        return cls._simulate.simulate(equipment)

    # -- 검증용 스윕 (단계와 무관) -------------------------------------------

    @classmethod
    def sweep_ports(cls):
        """색인된 장비 전부의 포트 1 자리를 한 번에 찍어본다. UI 버튼은 없다.

        sweep_ports  판정 기준은 PIVOT_TOLERANCE, PIVOT_ACROSS
        show_sweep   그리기. 색은 SWEEP_COLOR_*
        """
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """스윕이 그린 것을 지운다."""
        return cls._simulate.clear_sweep()
