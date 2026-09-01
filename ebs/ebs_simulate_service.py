"""EBS 시뮬레이션 공개 API.

바깥(UI, 다른 익스텐션)에서 구현부를 만지는 유일한 통로다.
여기 있는 건 전부 한 줄 위임이고, 실제 코드는 전부 ebs_simulate.py에 있다.

각 API 위 주석이 "이거 고치려면 어디를 보면 되는지" 지도 역할을 한다.
읽는 법:
    호출  = 이 API가 직접 부르는 구현부 메소드
    내부  = 그 아래로 딸려 들어가는 헬퍼들
    상수  = 동작을 바꾸는 값들 (전부 ebs_simulate.py 파일 맨 위)

전체 흐름은 5단계다. init -> prepare -> align -> focus -> collide.
simulate()는 이 다섯을 순서대로 한 번에 돌린다.
"""

from .ebs_simulate import EbsSimulate, FACES, GRID

__all__ = ["EbsSimulateService", "FACES", "GRID"]


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API. 구현부는 오직 여기를 통해서만 닿는다."""

    _simulate = None

    # ========================================================================
    # 수명주기 — 익스텐션 startup / shutdown 전용
    # ========================================================================

    @classmethod
    def initialize(cls):
        """구현부 인스턴스를 만든다. extension.py의 on_startup에서만 부른다."""
        cls._simulate = EbsSimulate()

    @classmethod
    def finalize(cls):
        """
        마커/레이저/스윕/카메라를 전부 걷고 캐시를 비운다.
        구현부: teardown() -> clear_markers, clear_port_lasers, clear_sweep
        """
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    # ========================================================================
    # 설정 — 5단계가 읽어가는 입력값. 부르는 즉시 필드에 저장만 된다
    # ========================================================================

    @classmethod
    def set_xml_path(cls, path):
        """
        포트 XML 경로. 경로가 바뀌면 포트 테이블을 버리고 _ready를 내려서
        init을 다시 돌게 만든다. 실제 읽기는 load_ports()가 한다.
        """
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """
        2포트/3포트 EBS 프림 경로. 장비 포트 수를 보고 prepare()가 둘 중 하나를 고른다.
        고르는 코드: _do_prepare()
        """
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_clearance(cls, value):
        """
        충돌 판정 접촉 여유. 0이면 EBS 최장변의 PROBE_RATIO(1%)로 자동 계산.
        구현부: _probe_depth()   상수: PROBE_RATIO
        """
        return cls._simulate.set_clearance(value)

    @classmethod
    def set_search_root(cls, path):
        """
        EQP_ 장비를 찾을 서브트리. 비우면 스테이지 전체를 훑는다.
        여기를 좁히는 게 init 속도에 제일 크게 먹힌다.
        구현부: _walk()   상수: EQP_PREFIX, PRUNE_TYPES
        """
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        """
        충돌 정밀도. 'bbox' / 'mesh' / 'triangle'.
        주의: 지금 bbox와 mesh는 동작이 같다 (둘 다 지오메트리 프림당 박스 하나).
        갈리는 지점: check_collision() 안의 PRECISION_TRI 분기, _nearest_in_prism()
        """
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_offset_scale(cls, mode):
        """
        offset을 거리로 바꾸는 방식. 'fixed'는 항상 100000으로 나누고,
        'puls'는 그 포트가 적힌 구간의 (길이 / distance-puls)를 쓴다.
        구현부: _coords_by_offset() / _coords_by_puls()
        상수: OFFSET_PER_UNIT, SCALE_FIXED, SCALE_PULS
        """
        return cls._simulate.set_offset_scale(mode)

    @classmethod
    def set_rail_nudge(cls, value):
        """
        모든 포트를 레일 방향으로 이만큼 민다. 원점 오차를 재보려고 둔 실험용 값.
        먹히는 곳: compute_port_points() 끝부분
        """
        return cls._simulate.set_rail_nudge(value)

    @classmethod
    def set_rail_root(cls, path):
        """
        rail_<a>_<b> 프림들이 모여있는 부모. 바뀌면 레일 인덱스를 버린다.
        인덱스는 첫 align 때 lazy 생성: _rails_from()
        """
        return cls._simulate.set_rail_root(path)

    # ========================================================================
    # 0단계 init — 스테이지와 XML을 한 번씩 훑어 캐시. 이후 단계는 전부 이걸 재사용
    # ========================================================================

    @classmethod
    def init(cls):
        """
        스테이지에서 EQP_ 장비를 색인하고, XML에서 포트 테이블을 읽는다.
        여기서 만든 캐시는 다음 init까지 살아있고, 이걸 만드는 데는 여기밖에 없다.

        호출: make_camera(), build_index(), load_ports()
        지오메트리는 한 번도 안 읽는다. 레일 인덱스도 여기서 안 만든다.
        느리면 build_index / load_ports 중 어느 쪽인지 로그 타이밍부터 볼 것.
        """
        return cls._simulate.init()

    @classmethod
    def build_index(cls):
        """
        스테이지를 훑어 "EQP_이름 -> 프림 경로" 맵을 만든다. init이 부른다.
        구현부: _walk() — EQP_ 프림을 만나면 그 안으로 안 들어가고,
                PRUNE_TYPES(Mesh/Material/Shader 등)와 *Light는 통째로 건너뛴다.
                자식은 인스턴스 프록시까지 보는 _children()으로 가져온다.
        """
        return cls._simulate.build_index()

    @classmethod
    def load_ports(cls):
        """
        포트 XML을 읽어 장비별 포트 index / offset / addr, addr별 cad와 구간 puls를 만든다.
        init이 부른다. 지금은 ET.parse로 트리를 통째로 만든 뒤 여러 번 훑는 구조.
        구현부: _provider_of(), _key_value(), _owning_addr(), _attr()
        상수: PORT_ID_KEY, OFFSET_KEY, CADX_KEY, CADY_KEY, NEXT_KEY, PULS_KEY
        """
        return cls._simulate.load_ports()

    # ========================================================================
    # 1단계 prepare — 어느 장비에 어느 EBS를 붙일지 정한다
    # ========================================================================

    @classmethod
    def prepare(cls, equipment=""):
        """
        장비를 찾고, 포트 수를 세고, 그에 맞는 EBS와 피봇 프림을 잡아둔다.
        문자열이 비면 뷰포트 선택에서 찾는다.

        호출: _do_prepare()
        내부: _resolve_by_name() / _resolve_by_selection() — 장비 찾기
              get_port_count() — XML 포트 수
              resolve_anchor() — 장비에서 ANCHOR_DEPTH만큼 첫 자식으로 내려간 피봇
        상수: ANCHOR_DEPTH(=6), PASS_TYPES(Scope는 단계로 안 침)
        """
        return cls._simulate.prepare(equipment)

    @classmethod
    def get_selected_equipment(cls):
        """뷰포트 선택에서 EQP_ 프림 경로를 하나 집어준다. 구현부: _resolve_by_selection()"""
        return cls._simulate.get_selected_equipment()

    @classmethod
    def get_port_count(cls, eqp_id):
        """XML이 아는 그 장비의 포트 개수. 구현부: get_port_indices()"""
        return cls._simulate.get_port_count(eqp_id)

    # ========================================================================
    # 2단계 align — 포트 위치를 계산해 EBS를 거기 갖다 놓는다. 제일 복잡한 단계
    # ========================================================================

    @classmethod
    def align(cls):
        """
        레일에서 포트 위치를 뽑아 목표점을 구하고, EBS를 옮겨 장비와 정렬한다.
        같이 포트 위치 확인용 빨간 레이저를 그린다.

        호출: _do_align() -> compute_target(), _place_ebs(), _align_prims(),
                             show_port_lasers()

        위치 계산 (여기가 핵심):
            compute_port_points()  포트 전부의 레일 공간 좌표. 가상 0번 포트 포함
              find_rail()          addr에서 나가는 직선 레일 고르기
                _rails_from()      rail_<a>_<b> 인덱스 (처음 한 번만 만듦)
                _rail_axis()       두 addr의 cad 차이로 X축인지 Y축인지 판정.
                                   CAD_SLACK 이하 차이는 오차로 봐준다
              _coords_by_offset()  fixed 모드
              _coords_by_puls()    puls 모드. addr 넘어가면 다음 addr의 puls를 쓴다
                _addr_step()       그 addr에서 나가는 구간의 (길이, puls)
              _rebase_offsets()    기준 addr에 맞춰 offset 재정렬
              _port_spacing()      포트 간격 평균 -> 0번 포트 위치

        배치:
            _place_ebs()      목표 월드 좌표로 이동
            _align_prims()    회전/스케일을 장비에 맞춤
              _write_transform() / _set_rotation() / _compose() / _euler()
              _extract_scale() / _normalized_rows() / _parent_world()

        상수: CAD_PER_UNIT(100/3), CAD_SLACK, OFFSET_PER_UNIT(100000),
              RAIL_PREFIX, LASER_ROOT, LASER_COLOR, LASER_COLOR_0, LASER_RADIUS
        """
        return cls._simulate.align()

    # ========================================================================
    # 3단계 focus — 카메라를 EBS 쪽으로 붙인다
    # ========================================================================

    @classmethod
    def focus(cls):
        """
        카메라를 만들고 EBS 정면에 세운 뒤 뷰포트를 그 카메라로 넘긴다.

        호출: _do_focus() -> make_camera(), _move_camera()
        내부: _viewport()      뷰포트 유틸 (omni.kit 없으면 조용히 실패)
              _world_range()   EBS 월드 바운드
              _box_corners()   그 박스의 8꼭짓점
              _fit_distance()  8꼭짓점이 화면에 CAMERA_FILL만큼 차는 거리 계산
        상수: CAMERA_PATH, CAMERA_FILL(화면 채움 비율),
              CAMERA_SLAB(근평면을 대상 앞 얼마부터), CAMERA_FAR

        카메라 동작을 바꾸고 싶으면 여기 넷만 보면 된다:
            만들기      make_camera()      — 초점거리/센서/클리핑 속성
            앉히기      _move_camera()     — 위치, 방향, 근평면
            거리 계산   _fit_distance()
            정리        release_camera()
        """
        return cls._simulate.focus()

    @classmethod
    def make_camera(cls):
        """
        세션 레이어에 /EbsCamera를 (있으면 지우고) 새로 만든다. init과 focus가 부른다.
        뷰포트는 안 건드린다 — 넘기는 건 _move_camera()가 한다.
        상수: CAMERA_PATH
        """
        return cls._simulate.make_camera()

    @classmethod
    def release_camera(cls):
        """뷰포트를 원래 카메라로 되돌리고 /EbsCamera를 지운다."""
        return cls._simulate.release_camera()

    # ========================================================================
    # 4단계 collide — EBS 좌/우/천장에 뭐가 닿는지 본다. 앞/뒤/바닥은 무시
    # ========================================================================

    @classmethod
    def collide(cls):
        """
        EBS 세 면에 셀을 깔고 각 셀에 닿는 지오메트리를 찾은 뒤, 결과를 씬에 색으로 그린다.

        호출: _do_collide() -> check_collision(), measure_faces(), show_markers()

        판정:
            _ebs_bound()        EBS 로컬 박스
            _probe_depth()      접촉 허용 두께
            _build_cells()      면당 셀 (GRID=1이라 지금은 면 하나에 셀 하나)
            _gather_nearby()    주변 지오메트리 후보 수집. 여기서 대부분 걸러진다
            _overlaps()         박스끼리 겹침
            _mesh_triangles()   삼각형 읽기 (triangle 모드에서만)
            _triangle_hits_box()  실제 삼각형 대 박스 판정
            _is_visible()       숨겨진 프림 제외

        빈 면까지 거리:
            measure_faces() -> _face_prism(), _nearest_in_prism(),
                               _gap_along(), _triangle_gap()

        그리기:
            show_markers() -> _marker_sheet(), _marker_quad(), _grid_bands(),
                              _marker_material(), _preview_shader(), _mdl_shader()
            색/투명도 바꾸려면 이 아래 상수만 만지면 된다:
              COLOR_BLOCKED, COLOR_CLEAR, MARKER_OPACITY, MARKER_EMISSION,
              GRID_COLOR, GRID_OPACITY, GRID_EMISSION, GRID_LINE, GRID_LIFT, SHEET_GAP
            RTX에서는 MDL 쉐이더가 이긴다 — _mdl_shader()가 실제로 보이는 쪽
        """
        return cls._simulate.collide()

    @classmethod
    def clear_markers(cls):
        """/EbsCollisionMarkers 스코프를 통째로 지운다. 상수: MARKER_ROOT"""
        return cls._simulate.clear_markers()

    @classmethod
    def clear_port_lasers(cls):
        """/EbsPortLasers 스코프를 통째로 지운다. 상수: LASER_ROOT"""
        return cls._simulate.clear_port_lasers()

    # ========================================================================
    # 일괄 실행
    # ========================================================================

    @classmethod
    def simulate(cls, equipment=""):
        """
        prepare -> align -> focus -> collide를 순서대로 돌린다. 중간에 실패하면 거기서 멈춘다.
        init은 미리 돌아있어야 한다. 구현부: simulate() — _do_* 넷을 차례로 호출
        """
        return cls._simulate.simulate(equipment)

    # ========================================================================
    # 검증용 스윕 — 5단계와 무관. 플랜트 전체를 한 번에 훑어보는 실험 기능
    # ========================================================================

    @classmethod
    def sweep_ports(cls):
        """
        서치 루트 아래 모든 장비에 대해 1번 포트(빨강)와 장비 피봇(초록) 기둥을 세우고,
        장비당 한 줄짜리 표를 payload["rows"]로 돌려준다.
        그 표를 엑셀로 쓰는 건 dummy_ui.SweepLog가 한다 — 여기선 안 쓴다.

        호출: sweep_ports()
        내부: resolve_anchor()        장비 피봇
              compute_port_points()   align과 똑같은 위치 계산
              _measure()              레일 방향으로 투영해 좌표/offset/차이 계산
              _mark_shared()          두 장비가 같은 피봇을 물면 둘 다 의심 표시
              _report_spread()        offset 차이 분포를 로그로
              show_sweep()            기둥 그리기 (_prim_name, _laser_cylinder)
        판정 상수: PIVOT_TOLERANCE(레일 방향 허용), PIVOT_ACROSS(수직은 그 0.5배),
                   MIN_PORTS(2), MAX_PORTS(3), ANCHOR_DEPTH(6)
        판정 자체를 고치려면 sweep_ports() 안의 pivot_ok 분기만 보면 된다.
        """
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """/EbsPortSweep 스코프를 통째로 지운다. 상수: SWEEP_ROOT"""
        return cls._simulate.clear_sweep()

    # ========================================================================
    # 마지막 실행 결과 조회 — 각 API가 돌려주는 payload에 이미 다 들어있다.
    # 나중에 결과를 다시 꺼내볼 때만 쓴다.
    # ========================================================================

    @classmethod
    def get_result(cls):
        """마지막 payload 전체."""
        return cls._simulate.get_result()

    @classmethod
    def get_grid_shape(cls):
        """면별 (행, 열). GRID=1이면 전부 (1, 1)."""
        return cls._simulate.get_grid_shape()

    @classmethod
    def get_notes(cls):
        """마지막 실행의 진단 메시지들. UI 로그 패널에 뜨는 그것."""
        return cls._simulate.get_notes()

    @classmethod
    def get_timings(cls):
        """마지막 실행의 단계별 소요 시간 [라벨, ms]. 구현부: _stage_timer()"""
        return cls._simulate.get_timings()
