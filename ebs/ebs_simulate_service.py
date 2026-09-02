"""EBS 시뮬레이션 공개 API. 구현은 전부 ebs_simulate.py.

여기 주석은 "뭘 바꾸려면 어디를 보라"는 색인이다. 본문은 전부 한 줄 위임.
단계: init -> prepare -> align -> focus -> collide. simulate()는 뒤 넷을 연속 실행.
상수는 전부 ebs_simulate.py 최상단.
"""

from .ebs_simulate import EbsSimulate, FACES, GRID

__all__ = ["EbsSimulateService", "FACES", "GRID"]


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API."""

    _simulate = None

    # -- 수명주기 ------------------------------------------------------------

    @classmethod
    def initialize(cls):
        """extension.py on_startup 전용."""
        cls._simulate = EbsSimulate()

    @classmethod
    def finalize(cls):
        """teardown -> clear_markers, clear_port_lasers, clear_sweep, release_camera."""
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    # -- 설정 ----------------------------------------------------------------

    @classmethod
    def set_xml_path(cls, path):
        """포트 XML 경로. 바뀌면 _ready 내려가서 init 재실행 필요.

        실제 읽기는 load_ports.
        """
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """2포트 / 3포트 EBS 프림 경로.

        포트 수로 둘 중 고르는 로직 변경시 _do_prepare 참조.
        """
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_clearance(cls, value):
        """접촉 여유. 0이면 자동.

        자동값 계산 변경시 _probe_depth + PROBE_RATIO 참조.
        """
        return cls._simulate.set_clearance(value)

    @classmethod
    def set_search_root(cls, path):
        """EQP_ 탐색 서브트리. 비우면 스테이지 전체.

        순회 범위 / 가지치기 변경시 _walk + PRUNE_TYPES, EQP_PREFIX 참조.
        인스턴스 프록시 처리 변경시 모듈함수 _children 참조.
        init 속도 문제는 여기부터.
        """
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        """'bbox' / 'mesh' / 'triangle'.

        UI 는 'box'(= mesh) 와 'triangle' 둘만 노출한다. bbox 와 mesh 가
        같은 테스트라서, 셋을 보여줄 이유가 없다. API 는 셋 다 받는다.
        판정 분기 변경시 check_collision 내 PRECISION_TRI 비교, _nearest_in_prism 참조.
        삼각형 판정 자체는 _triangle_hits_box.
        """
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_offset_scale(cls, mode):
        """'fixed' = offset / 100000. 'puls' = offset x (구간길이 / distance-puls).

        fixed 계산 변경시 _coords_by_offset + OFFSET_PER_UNIT 참조.
        puls 계산 변경시 _coords_by_puls + _addr_step 참조.
        addr 넘어가는 처리는 _coords_by_puls 안.
        모드 추가시 SCALE_MODES + dummy_ui 콤보 같이.
        """
        return cls._simulate.set_offset_scale(mode)

    @classmethod
    def set_rail_root(cls, path):
        """rail_<a>_<b> 부모 경로. 바뀌면 레일 인덱스 폐기.

        인덱스는 첫 align 때 _rails_from 이 lazy 생성.
        """
        return cls._simulate.set_rail_root(path)

    # -- 0단계 init ----------------------------------------------------------

    @classmethod
    def init(cls):
        """스테이지 색인 + XML 포트 테이블. 캐시는 다음 init까지 유지.

        init -> make_camera, build_index, load_ports.
        지오메트리 안 읽음. 레일 인덱스 여기서 안 만듦.
        느릴 때: 로그의 'build index' / 'parse XML' 두 값 비교 후
                 build_index 또는 load_ports 참조.
        """
        return cls._simulate.init()

    @classmethod
    def build_index(cls):
        """EQP_이름 -> 프림 경로. init이 호출.

        순회 규칙 변경시 _walk 참조.
        """
        return cls._simulate.build_index()

    @classmethod
    def load_ports(cls):
        """XML -> 포트 index/offset/addr, addr별 cad와 구간 puls. init이 호출.

        결과를 <xml경로>.ebscache.json 에 캐시한다. 원본 size/mtime/스키마 버전이
        같으면 파싱을 건너뛴다. 캐시가 없거나 깨졌거나 못 쓰면 파싱으로 진행하고
        로그만 남긴다 (읽기 전용 드라이브에서도 동작).
        캐시 정책 변경시 _load_cache, _save_cache, _source_stamp 참조.
        저장 형태 바꾸면 CACHE_VERSION 올릴 것. 파일명은 CACHE_SUFFIX.

        파서는 expat. 트리는 안 만든다. (lxml 도 써봤으나 Kit 에 없고,
        있어도 병목이 파서가 아니라서 의미 없었음 — 아래 참고.)
        파일은 READ_BLOCK(8 MB)씩 읽어 파서에 밀어넣는다 — 파서에 맡기면
        2 kB 씩 읽어서, 공유 드라이브에서는 그 한 조각이 왕복 한 번이 된다.
        읽기 루프는 _feed_parser.
        읽는 규칙 변경시 _PortScan 참조 (모듈 최상단).
        320 MB 기준 실측: I/O 0.1s, expat 3.4s, _PortScan 핸들러가 나머지 전부.
        더 빠르게 하려면 손댈 곳은 _PortScan 이지, 파서나 읽기가 아니다.
          그룹의 키 = 자기 속성 + 직속 <value key=.. value=..> 자식.
          addr 문맥은 감싸는 addr 그룹에서 내려온다.
        키 이름 변경시 PORT_ID_KEY, OFFSET_KEY, CADX_KEY, CADY_KEY, NEXT_KEY, PULS_KEY.
        addr/포트 이름 규칙은 ADDR_PATTERN, PORT_PATTERN.
        네임스페이스 접두어 처리는 _plain.

        타이밍: 'XML: cache read' 만 있으면 캐시 히트,
                'XML: parse' + 'XML: cache write' 면 파싱한 것.
        """
        return cls._simulate.load_ports()

    # -- 1단계 prepare -------------------------------------------------------

    @classmethod
    def prepare(cls, equipment=""):
        """장비 확정 + 포트 수 + EBS + 피봇. 빈 문자열이면 뷰포트 선택.

        prepare -> _do_prepare.
        장비 찾기 변경시 _resolve_by_name, _resolve_by_selection 참조.
        피봇 내려가는 깊이/규칙 변경시 resolve_anchor + ANCHOR_DEPTH, PASS_TYPES 참조.
        """
        return cls._simulate.prepare(equipment)

    @classmethod
    def get_selected_equipment(cls):
        """뷰포트 선택 -> EQP_ 프림 경로. 구현 _resolve_by_selection."""
        return cls._simulate.get_selected_equipment()

    @classmethod
    def get_port_count(cls, eqp_id):
        """XML 기준 포트 개수. 구현 get_port_indices."""
        return cls._simulate.get_port_count(eqp_id)

    # -- 2단계 align ---------------------------------------------------------

    @classmethod
    def align(cls):
        """포트 위치 계산 -> EBS 배치 -> 확인용 빨간 레이저.

        align -> _do_align -> compute_target, _place_ebs, _align_prims, show_port_lasers.

        레일 고르기 변경시 find_rail 참조. 후보 인덱스는 _rails_from.
        직선/코너 판정 변경시 _rail_axis + CAD_SLACK, CAD_PER_UNIT 참조.
        포트 좌표 계산 변경시 compute_port_points 참조 (여기가 본체).
          축척은 _coords_by_offset / _coords_by_puls.
          addr 기준 재정렬은 _rebase_offsets.
          0번 포트 위치는 _port_spacing.
        목표점 계산 변경시 compute_target 참조.
        이동만 변경시 _place_ebs. 회전/스케일 변경시 _align_prims 참조.
          xform op 쓰는 방식은 _write_transform, _set_rotation, _compose, _euler.
        레이저 굵기/색/길이 변경시 show_port_lasers + LASER_RADIUS, LASER_COLOR,
          LASER_COLOR_0, LASER_ROOT 참조.
        """
        return cls._simulate.align()

    # -- 3단계 focus ---------------------------------------------------------

    @classmethod
    def focus(cls):
        """카메라 생성 + EBS 정면 배치 + 뷰포트 전환.

        focus -> _do_focus -> make_camera, _move_camera.

        초점거리/센서/클리핑 변경시 make_camera 참조.
        위치·방향·근평면 변경시 _move_camera + CAMERA_SLAB 참조.
        화면 채움 비율 변경시 _fit_distance + CAMERA_FILL 참조.
        대상 바운드 변경시 _world_range, _box_corners 참조.
        뷰포트 전환 안 될 때 _viewport 참조 (omni.kit 없으면 조용히 실패).
        """
        return cls._simulate.focus()

    @classmethod
    def make_camera(cls):
        """세션 레이어에 /EbsCamera 재생성. init과 focus가 호출. 뷰포트는 안 건드림."""
        return cls._simulate.make_camera()

    @classmethod
    def release_camera(cls):
        """원래 카메라 복귀 + /EbsCamera 삭제."""
        return cls._simulate.release_camera()

    # -- 4단계 collide -------------------------------------------------------

    @classmethod
    def collide(cls):
        """EBS 좌/우/천장 충돌 판정 + 빈 면 거리 + 씬에 마커.

        collide -> _do_collide -> check_collision, measure_faces, show_markers.

        셀 분할 변경시 _build_cells + GRID 참조. 지금 GRID=1 이라 면당 셀 1개.
        후보 수집 / 가지치기 변경시 _gather_nearby 참조 (대부분 여기서 걸러짐).
        박스 겹침은 _overlaps, 삼각형 판정은 _triangle_hits_box.
        메시 읽기 변경시 _mesh_triangles, _attr_value 참조.
        숨김 프림 처리 변경시 _is_visible 참조.
        EBS 박스 변경시 _ebs_bound 참조.
        빈 면 거리 변경시 measure_faces -> _face_prism, _nearest_in_prism,
          _gap_along, _triangle_gap 참조.
        마커 색/투명도 변경시 COLOR_BLOCKED, COLOR_CLEAR, MARKER_OPACITY,
          MARKER_EMISSION 참조.
        격자선 변경시 _grid_bands + GRID_COLOR, GRID_OPACITY, GRID_EMISSION,
          GRID_LINE, GRID_LIFT 참조.
        면이 한쪽만 보일 때 _marker_sheet, _marker_quad + SHEET_GAP 참조.
        RTX에서 실제로 보이는 쉐이더는 _mdl_shader. _preview_shader 는 폴백.
        """
        return cls._simulate.collide()

    @classmethod
    def clear_markers(cls):
        """/EbsCollisionMarkers 삭제. MARKER_ROOT."""
        return cls._simulate.clear_markers()

    @classmethod
    def clear_port_lasers(cls):
        """/EbsPortLasers 삭제. LASER_ROOT."""
        return cls._simulate.clear_port_lasers()

    # -- 일괄 ----------------------------------------------------------------

    @classmethod
    def simulate(cls, equipment=""):
        """prepare -> align -> focus -> collide 연속. 실패시 중단. init 선행 필요.

        구현 simulate 은 _do_prepare, _do_align, _do_focus, _do_collide 를 차례로 호출.
        단계 순서 변경시 거기만.
        """
        return cls._simulate.simulate(equipment)

    # -- 검증용 스윕 (5단계와 무관) ------------------------------------------

    @classmethod
    def sweep_ports(cls):
        """전 장비 1번 포트(빨강) + 피봇(초록) 기둥, 장비당 1행 표를 rows 로 반환.

        엑셀 출력은 dummy_ui.SweepLog. 여기서 안 씀.

        판정(pivot_ok) 변경시 sweep_ports 안의 분기만 참조.
          허용치는 PIVOT_TOLERANCE(레일 방향), PIVOT_ACROSS(수직, 0.5배).
          포트 수 경계는 MIN_PORTS, MAX_PORTS.
        측정값 열 변경시 _measure 참조.
        피봇 중복 표시 변경시 _mark_shared 참조.
        분포 로그 변경시 _report_spread 참조.
        기둥 그리기 변경시 show_sweep + SWEEP_COLOR_PORT, SWEEP_COLOR_EQP,
          SWEEP_ROOT, LASER_RADIUS 참조. 프림 이름 규칙은 _prim_name.
        위치 계산은 align 과 동일 경로 (compute_port_points).

        주의: rows 의 pivot_ok 문자열은 dummy_ui.SweepLog.NOTES 가 받는다.
              값 바꾸면 양쪽 같이.
        """
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """/EbsPortSweep 삭제. SWEEP_ROOT."""
        return cls._simulate.clear_sweep()

    # -- 결과 조회 (각 API 반환 payload 에 이미 포함) ------------------------

    @classmethod
    def get_result(cls):
        """마지막 payload 전체. 키 추가/변경시 _payload 참조."""
        return cls._simulate.get_result()

    @classmethod
    def get_grid_shape(cls):
        """면별 (행, 열). GRID=1 이면 전부 (1, 1)."""
        return cls._simulate.get_grid_shape()

    @classmethod
    def get_notes(cls):
        """마지막 실행 진단 메시지. 쌓는 곳은 _note."""
        return cls._simulate.get_notes()

    @classmethod
    def get_timings(cls):
        """마지막 실행 [라벨, ms]. 재는 곳은 _stage_timer."""
        return cls._simulate.get_timings()
