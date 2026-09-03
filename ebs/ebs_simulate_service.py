"""EBS 시뮬레이션 공개 API. 구현은 전부 ebs_simulate.py.

주석은 "뭘 바꾸려면 어디를 보라"는 색인이다. 본문은 전부 한 줄 위임.
단계: init -> prepare -> align -> focus -> collide. simulate()는 뒤 넷 연속.
상수는 전부 ebs_simulate.py 최상단. 오버레이 것만 ebs_simulate_overlay.py.
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
        """teardown -> clear_markers, clear_port_lasers, clear_sweep,
        release_camera, hide_ebs."""
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    # -- 설정 ----------------------------------------------------------------

    @classmethod
    def set_xml_path(cls, path):
        """포트 XML 경로. 바뀌면 _ready 내려감. 읽기는 load_ports."""
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        """2포트 / 3포트 EBS 프림 경로. 고르는 규칙은 _do_prepare."""
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_clearance(cls, value):
        """접촉 여유. 0이면 자동. 자동값은 _probe_depth + PROBE_RATIO."""
        return cls._simulate.set_clearance(value)

    @classmethod
    def set_search_root(cls, path):
        """EQP_ 탐색 서브트리. 비우면 스테이지 전체.

        순회 범위 -> _walk + PRUNE_TYPES, EQP_PREFIX.
        인스턴스 프록시 -> 모듈함수 _children.
        init 이 느리면 여기부터.
        """
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        """'bbox' / 'mesh' / 'triangle'. UI 는 box(=mesh)/triangle 둘만 노출.

        분기 -> check_collision 의 PRECISION_TRI 비교, _nearest_in_prism.
        삼각형 판정 -> _triangle_hits_box.
        """
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_offset_scale(cls, mode):
        """'fixed' = offset / 100000. 'puls' = offset x (구간길이 / distance-puls).
        'snap' = puls + 판정 TRUE 면 포트 1 을 피봇 유효축에 얹음. 기본값.

        fixed -> _coords_by_offset + OFFSET_PER_UNIT.
        puls  -> _coords_by_puls + _addr_step. addr 넘김도 그 안.
        snap  -> _snap_shift. 걷는 것은 puls 와 동일, 보정은 compute_target 안
                 = align 전용. 전 포트를 같은 값만큼 민다 (간격 불변).
                 판정 -> _pivot_state (스윕과 공용). sweep 은 영향 없음
                 (compute_port_points 직접 호출).
        모드 추가 -> SCALE_MODES + dummy_ui 콤보.
        """
        return cls._simulate.set_offset_scale(mode)

    @classmethod
    def set_show_lasers(cls, on):
        """align 이 포트 레이저를 그릴지. 기본 꺼짐. UI 의 Laser 체크박스.

        그리기 -> show_port_lasers. 끄면 _do_align 이 clear_port_lasers.
        """
        return cls._simulate.set_show_lasers(on)

    @classmethod
    def set_min_gaps(cls, side, ceiling):
        """3면 최소 여유, m. 기본 MIN_GAP_SIDE 0.6 / MIN_GAP_CEILING 0.1.

        미달이면 닿지 않아도 tight(간섭) -> 빨강 + placeable False.
        판정 -> _face_marks.
        UI 의 Min gap 두 칸이 매 동작 전에 이걸 넘긴다 (_apply_settings).
        """
        return cls._simulate.set_min_gaps(side, ceiling)

    @classmethod
    def hide_ebs(cls):
        """EBS 프림 둘 다 끔. init 과 Clear 가 부른다.

        hide_ebs / show_ebs -> _show_ebs (세션 레이어에 visibility 직접).
        align 이 쓴 것 하나만 다시 켠다. 장비 쪽(opacity)과 방식이 다른 것은
        프림이 둘뿐이라서다.
        """
        return cls._simulate.hide_ebs()

    @classmethod
    def set_rail_root(cls, path):
        """rail_<a>_<b> 부모 경로. 바뀌면 인덱스 폐기.

        인덱스는 첫 align 때 _rails_from 이 lazy 생성.
        """
        return cls._simulate.set_rail_root(path)

    # -- 0단계 init ----------------------------------------------------------

    @classmethod
    def init(cls):
        """스테이지 색인 + XML 포트 테이블. 지오메트리는 안 읽음.

        init -> make_camera, hide_ebs, build_index, load_ports.
        레일 인덱스는 여기서 안 만든다.
        느리면 로그의 'build index' / 'XML: parse' 비교 후 해당 쪽.
        """
        return cls._simulate.init()

    @classmethod
    def build_index(cls):
        """EQP_이름 -> 프림 경로. 순회 규칙 -> _walk."""
        return cls._simulate.build_index()

    @classmethod
    def load_ports(cls):
        """XML -> 포트 index/offset/addr, addr별 cad와 구간 puls.

        캐시 -> _load_cache, _save_cache, _source_stamp.
          <xml>.ebscache.json. size/mtime/버전 같으면 파싱 생략.
          없거나 깨졌거나 못 쓰면 파싱으로 진행, 로그만 남김.
          저장 형태 바꾸면 CACHE_VERSION 올릴 것. 파일명 CACHE_SUFFIX.
        파서 -> _PortScan (모듈 최상단). expat, 트리 안 만듦.
          읽기 루프 -> _feed_parser, READ_BLOCK 8 MB 씩.
          320 MB 실측: I/O 0.1s, expat 3.4s, 나머지 전부 _PortScan.
          더 빠르게 하려면 손댈 곳은 _PortScan 이다.
        키 이름 -> PORT_ID_KEY, OFFSET_KEY, CADX_KEY, CADY_KEY, NEXT_KEY, PULS_KEY.
        이름 규칙 -> ADDR_PATTERN, PORT_PATTERN. 네임스페이스 -> _plain.
        타이밍: 'XML: cache read' 만 있으면 캐시 히트.
        """
        return cls._simulate.load_ports()

    # -- 1단계 prepare -------------------------------------------------------

    @classmethod
    def prepare(cls, equipment=""):
        """장비 확정 + 포트 수 + EBS + 피봇. 빈 문자열이면 뷰포트 선택.

        prepare -> _do_prepare.
        장비 찾기 -> _resolve_by_name, _resolve_by_selection.
        피봇 -> resolve_anchor + ANCHOR_DEPTH, PASS_TYPES.
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
        """포트 위치 계산 -> EBS 배치 -> (옵션) 확인용 레이저.

        align -> _do_align -> compute_target, _place_ebs, _align_prims,
                              show_ebs, show_port_lasers.

        레일 고르기 -> find_rail. 후보 인덱스 -> _rails_from.
        직선/코너 -> _rail_axis + CAD_SLACK, CAD_PER_UNIT.
        포트 좌표 -> compute_port_points (본체).
          축척 -> _coords_by_offset / _coords_by_puls.
          addr 재정렬 -> _rebase_offsets. 0번 포트 -> _port_spacing.
        목표점 -> compute_target.
        이동 -> _place_ebs. 회전/스케일 -> _align_prims.
          xform op -> _write_transform, _set_rotation, _compose, _euler.
        EBS 를 다시 보이게 -> show_ebs (init/Clear 가 꺼둔 것).
        레이저 -> show_port_lasers + LASER_RADIUS, LASER_COLOR, LASER_COLOR_0,
          LASER_ROOT. 기본으로 안 그림 (set_show_lasers).
          _port_world 를 쓰므로 snap 모드에서는 보정된 자리에 뜬다.
        """
        return cls._simulate.align()

    # -- 3단계 focus ---------------------------------------------------------

    @classmethod
    def focus(cls):
        """카메라 생성 + EBS 정면 배치 + 뷰포트 전환 + 양옆 빼고 투명화.

        focus -> _do_focus -> side_neighbours, hide_other_equipment,
                              make_camera, _move_camera.

        이웃 찾기 -> side_neighbours + NEIGHBOUR_REACH, NEIGHBOUR_BAND.
          폭 x REACH 반경 원 안에서, EBS 좌우축(_sideways, 3면 검사와 같은 축)
          기준 방향당 하나씩. 줄에서 벗어난 정도가 폭 x BAND 넘으면 뺀다
          (안 그러면 대각선 장비가 이긴다).
          위치 -> equipment_spots. 장비당 한 번 캐시, init 이 비움.
          기둥·벽·천장은 안 끈다 — 빈 면 거리를 재는 상대다.
        끄기/되돌리기 -> hide_other_equipment / show_equipment.
          visibility 가 아니라 Looks 아래 쉐이더의 opacity 0.
          쉐이더 수집 -> _looks_shaders (장비당 한 번 캐시, init 이 비움).
          저작 -> _author_opacity. 입력 이름 -> GONE.
            문턱값 GONE_THRESHOLD 가 핵심. 0 이면 blend 라 안 사라진다.
            Sdf 는 ChangeBlock 안에서 안전, 스키마 헬퍼는 아님. 수집을 블록
            밖에서 먼저 끝내는 것도 같은 이유.
          쓰는 곳 -> _gone_layer (세션 위에 얹은 전용 레이어). 되돌리기가
            Clear() 한 번. 스펙 제거는 ChangeBlock 이 안전하지 않다.
          인스턴스 장비는 못 쓴다 -> _eqp_shared 에 담아 개수만 알림.
          되읽어 확인 -> _check_gone (한 줄).
          Clear 는 release_camera -> show_equipment 로 되돌린다.
        주의: opacity 0 은 충돌 검사에서 안 빠진다. _gather_nearby 는
          visibility 만 본다.
        카메라 만들기 -> make_camera. Define 만 한다 (release_camera 부르면
          숨긴 장비가 도로 살아난다). 초점거리/센서/클리핑도 여기.
        카메라 배치 -> _move_camera. 없으면 스스로 만든다 (Clear 가 지우므로).
          단 IsValid 로 묻지 말 것 — 타입 없는 over 가 남는다. UsdGeom.Camera(prim)
          으로 물어야 한다 (안 그러면 clippingRange 에서 empty typename).
          근평면은 CAMERA_NEAR 고정. 컬링 없음.
        거리 -> CAMERA_BACK. EBS 상자 중앙에서 정면으로 그만큼 뒤. 고정이다.
          화면에 맞추지 않는다 — 장비마다 배율이 달라지면 여유 길이가 눈으로
          비교가 안 된다.
        대상 바운드 -> _world_range.
        판정 패널 높이는 별개다 -> VERDICT_HEIGHT (build_verdict).
        뷰포트 전환 -> _viewport (omni.kit 없으면 조용히 실패).
        """
        return cls._simulate.focus()

    @classmethod
    def make_camera(cls):
        """세션 레이어에 /EbsCamera. init 과 _move_camera 가 호출. 뷰포트는 안 건드림."""
        return cls._simulate.make_camera()

    @classmethod
    def release_camera(cls):
        """원래 카메라 복귀 + /EbsCamera 삭제 + show_equipment."""
        return cls._simulate.release_camera()

    # -- 4단계 collide -------------------------------------------------------

    @classmethod
    def collide(cls):
        """EBS 좌/우/천장 판정 + 빈 면 거리 + 대상 장비 간섭 + 씬에 마커.

        collide -> _do_collide -> check_collision, measure_faces,
                                  check_equipment, build_verdict, show_markers.

        대상 장비는 3면 검사에서 빠진다 (exclude). 대신 따로 정확히 본다.
        3면 충돌과 내부 간섭은 서로 색을 빌려주지 않는다.

        장비 간섭 -> check_equipment. 삼각형 대 삼각형 (박스로 보면 마운트가
          전부 충돌이 된다).
          공통 영역의 삼각형만 읽기 -> _triangles_near (메시당 한 번).
          격자로 쌍 줄이기 -> _meetings, _grid_of, _cells_of + GRID_CELLS.
            격자 없이 n x m 이면 7200x7200 에서 10분 넘는다.
          판정 -> _triangles_meet, _segment_hits_triangle (여섯 모서리).
          같은 평면 = 마운트, 통째로 들어간 것 = 정상. 둘 다 안 잡는다.
          이름 쌍은 MEET_LIMIT 까지. 결과 -> payload["equipment_hit"].
          터져도 노트만 남기고 3면 결과는 살린다.
        타이밍: 'interference: gather / read triangles / test'.

        셀 분할 -> _build_cells + GRID (지금 1, 면당 셀 1개).
        후보 수집 -> _gather_nearby (대부분 여기서 걸러짐).
          순회는 단계당 2회: check_collision 1, measure_faces 1.
          measure_faces 는 세 면 프리즘의 합집합으로 한 번만 걷는다.
          BBoxCache -> _bounds_cache. _do_collide 가 셋에 나눠준다.
        박스 겹침 -> _overlaps. 삼각형 -> _triangle_hits_box.
        메시 읽기 -> _mesh_local (원본, 변환 없음) 과 그 위의 둘:
          _mesh_triangles     전체를 월드로. 3면 검사용.
          _triangles_reaching 상자를 로컬로 끌어와(_pulled_back) 거른 뒤
                              살아남은 면만 월드로. 간섭 검사용.
                              메시마다 첫 점으로 규약 확인, 어긋나면 Gf 로 물러남.
          캐시 _triangles(월드) / _local. EBS 가 움직이면 _do_align 이
          _forget_triangles 로 그 하위만 버린다. 장비 것은 남는다.
        상자로 판정한 프림 -> _boxed, 단계 끝에 한 줄.
        숨김 프림 -> _is_visible. 프림 자기 속성만 본다 (조상은 걷기가 이미 걸렀다).
        EBS 박스 -> _ebs_bound.

        빈 면 거리 -> measure_faces -> _face_prism, _nearest_in_prism,
          _gap_along, _triangle_gap.
          찾는 거리 -> REACH_RATIO. 넓히면 'gather nearby' 가 느려진다.
          거리는 최근접 꼭짓점 것 (여유는 최솟값이어야 한다).
          선을 그을 자리는 그 삼각형의 중점. 면 밖이면 꼭짓점으로 폴백.
        마커 색 -> COLOR_BLOCKED / BLOCKED_OPACITY / BLOCKED_EMISSION,
          빈 면은 COLOR_CLEAR / MARKER_OPACITY / MARKER_EMISSION.
        한쪽만 보이는 면 -> _marker_sheet, _marker_quad + SHEET_GAP.
        RTX 에서 보이는 쉐이더는 _mdl_shader. _preview_shader 는 폴백.
        오버레이 -> build_verdict / get_verdict.
        """
        return cls._simulate.collide()

    @classmethod
    def get_verdict(cls):
        """마지막 collide 의 판정. 오버레이가 읽는다. 만드는 곳 build_verdict.

        {"centre": 월드 좌표 (VERDICT_HEIGHT 높이), "span": EBS 최장변,
         "inside": 내부 간섭,
         "faces": [{"face", "name", "state"}], "blocked": 막힌 셀 수,
         "placeable": 세울 수 있나,
         "marks": [{"face", "state", "distance": m|None, "min_gap": m,
                    "name", "at": 선 중점, "from": 면 위 점, "to": 상대 위 점}]}

        state 셋: clear(황색) / tight(최소 여유 미달, 빨강) / clash(막힘).
          tight 도 faces 에 들어가고 placeable 을 내린다. 기준 -> set_min_gaps.
          3면 판도 tight 면 막힌 것처럼 빨갛게 칠한다 (show_markers 가 marks 를
          보고 판단). payload 의 cells 는 실제로 닿은 것만 말한다.
          clash 와 다른 점은 선이 있고, 거리가 나오고, 문구가 interference 인 것.
        marks -> _face_marks. 막힌 면은 면 중앙, 빈 면은 선 중점에 매단다.
          가까운 점 -> _nearest_in_prism 의 "at" (삼각형 중점 또는 _box_point).
          거리는 두 월드 점 사이로 잰다 (프리즘 수는 EBS 로컬이라 스케일에
          약하다). m 환산 -> GetStageMetersPerUnit.
          선은 씬에 그린다 -> show_markers 가 _gap_line 으로 실린더.
            MARKER_ROOT 아래 = 마커와 같이 지워진다. 색 COLOR_GAP / COLOR_TIGHT,
            굵기 _thread_radius (레이저와 같은 값, LASER_RADIUS).
          순서 주의: clear_markers 가 _verdict 를 비우므로 _do_collide 는
            판정을 그리기 전에 만들어 들고 있다가 그린 뒤에 넣는다.
        이름 -> owner_name. 막은 메시 경로를 위로 타면서 GROUP_NAMES 중 하나면
          그것, search root 바로 아래면 그 장비 이름. 먼저 만나는 쪽이 이긴다.
          어느 프림이 막았는지 -> check_collision 이 _blockers 에 면당 하나.
        비어 있으면 그릴 것이 없다는 뜻.

        그리는 쪽은 ebs_simulate_overlay. 색·크기·문구는 전부 거기.
          문구는 영문 (뷰포트 폰트에 한글 없음): CAN / CANNOT / INNER / FACE_ORDER.
          면 패널 -> _face_panel. clear 만 황색, tight 와 clash 는 빨강.
            좌우는 선 위아래, 천장은 좌우 (SIDE_BY_SIDE).
            판이 색을 지고 글자는 흰색 (COLOR_TEXT).
            선이 씬에 있어 한 판으로 두면 가린다 -> _floating 의 anchor + LINE_ROOM.
          omni.ui.scene 이 아니라 뷰포트 프레임의 ui.Placer.
            매 프레임 화면좌표로 투영 -> _to_screen, _place.
            _place 는 오프셋을 프레임 안에 가둔다 (안 그러면 뷰포트가 리사이즈됨).
        """
        return cls._simulate.get_verdict()

    @classmethod
    def clear_markers(cls):
        """/EbsCollisionMarkers 삭제. MARKER_ROOT. _verdict 도 같이 비운다."""
        return cls._simulate.clear_markers()

    @classmethod
    def clear_port_lasers(cls):
        """/EbsPortLasers 삭제. LASER_ROOT."""
        return cls._simulate.clear_port_lasers()

    # -- 일괄 ----------------------------------------------------------------

    @classmethod
    def simulate(cls, equipment=""):
        """prepare -> align -> focus -> collide 연속. 실패시 중단. init 선행.

        구현 simulate 이 _do_* 넷을 차례로 호출. 순서 변경은 거기만.
        """
        return cls._simulate.simulate(equipment)

    # -- 검증용 스윕 (단계와 무관) -------------------------------------------

    @classmethod
    def sweep_ports(cls):
        """전 장비 1번 포트(빨강) + 피봇(초록) 기둥, 장비당 1행을 rows 로.

        엑셀 출력은 dummy_ui.SweepLog. 여기서 안 씀.
        판정 -> sweep_ports 안의 분기 + PIVOT_TOLERANCE, PIVOT_ACROSS,
          MIN_PORTS, MAX_PORTS.
        측정값 열 -> _measure. 피봇 중복 -> _mark_shared. 분포 -> _report_spread.
        기둥 -> show_sweep + SWEEP_COLOR_PORT, SWEEP_COLOR_EQP, SWEEP_ROOT,
          LASER_RADIUS. 이름 규칙 -> _prim_name.
        위치 계산은 align 과 같은 경로 (compute_port_points).
        주의: rows 의 pivot_ok 문자열은 dummy_ui.SweepLog.NOTES 가 받는다.
        """
        return cls._simulate.sweep_ports()

    @classmethod
    def clear_sweep(cls):
        """/EbsPortSweep 삭제. SWEEP_ROOT."""
        return cls._simulate.clear_sweep()

    # -- 결과 조회 (각 API 반환 payload 에 이미 포함) ------------------------

    @classmethod
    def get_result(cls):
        """마지막 payload 전체. 키 추가/변경 -> _payload."""
        return cls._simulate.get_result()

    @classmethod
    def get_grid_shape(cls):
        """면별 (행, 열). GRID=1 이면 전부 (1, 1)."""
        return cls._simulate.get_grid_shape()

    @classmethod
    def get_notes(cls):
        """마지막 실행 진단 메시지. 쌓는 곳 _note."""
        return cls._simulate.get_notes()

    @classmethod
    def get_timings(cls):
        """마지막 실행 [라벨, ms]. 재는 곳 _stage_timer."""
        return cls._simulate.get_timings()
