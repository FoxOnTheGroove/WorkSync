"""EBS 시뮬레이션 공개 API. 구현은 ebs_simulate.py, 카메라만 ebs_simulate_camera.py.

주석은 "뭘 바꾸려면 어디를 보라"는 색인이다. 본문은 전부 한 줄 위임.
단계: init -> prepare -> align -> focus -> collide. simulate()는 뒤 넷 연속.
상수는 전부 ebs_simulate.py 최상단. 오버레이 것만 ebs_simulate_overlay.py.
"""

from .ebs_simulate import EbsSimulate

__all__ = ["EbsSimulateService"]


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
        """teardown -> show_equipment, EbsSimulateCamera.remove, clear_markers,
        clear_port_lasers, clear_sweep, hide_ebs.

        카메라 프림을 실제로 지우는 곳은 여기뿐이다 — 세션 중에는 남긴다.
        """
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

        init -> EbsSimulateCamera.make (없을 때만), hide_ebs, build_index,
                load_ports.
        레일 인덱스는 여기서 안 만든다.
        느리면 로그의 'build index' / 'XML: parse' 비교 후 해당 쪽.

        스테이지 색인 -> build_index -> _walk.
        포트 테이블 -> load_ports. 캐시 -> _load_cache, _save_cache,
          _source_stamp. <xml>.ebscache.json, size/mtime/CACHE_VERSION 이 같으면
          파싱 생략. 못 쓰면 파싱으로 진행하고 로그만 남긴다.
          파서 -> _PortScan (모듈 최상단). expat, 트리 안 만듦.
          읽기 -> _feed_parser, READ_BLOCK 8 MB 씩.
          320 MB 실측: I/O 0.1s, expat 3.4s, 나머지 전부 _PortScan.
          키 이름 -> PORT_ID_KEY, OFFSET_KEY, CADX_KEY, CADY_KEY, NEXT_KEY,
            PULS_KEY. 이름 규칙 -> ADDR_PATTERN, PORT_PATTERN.
        """
        return cls._simulate.init()

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

        focus -> _do_focus -> side_band, hide_other_equipment,
                              EbsSimulateCamera.place.

        이웃 찾기 -> side_band + NEIGHBOUR_REACH. side_neighbours 는 그 껍데기.
          점이 아니라 상자로 본다. 장비 월드 AABB 를 EBS 좌우축(_sideways,
          3면 검사와 같은 축)과 그 수직축에 눕혀(_cast) 구간 두 개를 만든다.
          같은 줄 = 깊이 구간이 겹침. 대각선은 여기서 빠진다.
          그 다음 좌우축 간격이 가장 작은 것, 방향당 하나. 간격 한도는
          대상의 좌우 폭 x NEIGHBOUR_REACH.
          예전엔 pivot 점 하나로 봤다. pivot 은 장비 중심이 아니고(resolve_anchor
          가 첫 자식을 타고 내려간 자리) 순위도 좌우 성분만 봐서, 정면 쪽
          대각선 장비가 진짜 옆보다 자주 이겼다.
          상자 -> equipment_boxes. 장비당 한 번 캐시, init 이 비움.
          side_band 는 고른 구간(side/deep/축)도 같이 돌려준다 — 눈으로
          확인하고 싶으면 그걸로 그리면 된다.
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
        카메라는 전부 ebs_simulate_camera.EbsSimulateCamera 다. 구현부는
          _camera 로 들고 있고, 껍데기는 안 둔다 — _do_focus 와 init 이 직접
          부른다. 밖으로 나가는 것은 release_camera 하나뿐이다 (장비 되돌리기가
          붙어 있어서).
          make -> Define 만. release 를 부르면 숨긴 장비가 도로 살아난다.
            초점거리/센서 -> FOCAL, APERTURE_H, APERTURE_V.
          place -> 상자와 바라볼 프림을 받아 놓는다. 상자 중앙이 interest.
            축 -> _frame. 쓰기 -> _write. 근평면 CAMERA_NEAR 고정, 컬링 없음.
            궤도 회전의 중심은 omni:kit:centerOfInterest — 제자리 자전이 아니라
            그 점 둘레를 도는 공전이 되게 하는 값이다.
          _camera -> 없으면 만든다. 보통은 init 이 이미 만들어 두었다.
            있나 없나는 exists 로 묻는다. IsValid 로 묻지 말 것 — 타입 없는
            over 가 남을 수 있고 그것도 참이다. UsdGeom.Camera(prim) 으로.
          궤도 모드 -> place 가 켜고 release 가 끈다 (orbit / interest).
            Camera 때 켜지고 Clear 때 꺼진다. 켜져 있는 동안 카메라는 그
            점을 계속 바라보고, 움직임은 그 점 둘레를 도는 것이다.
          되돌리기 -> reset (_home). UI 의 Refresh 버튼.
          입력 -> _grab. 받기와 막기가 따로다.
            받기 -> _grab_sheet. 프레임(ORBIT_FRAME)에 투명한 판을 깔고 받는다.
              이 빌드에서 확인된 유일한 경로. 판은 프레임을 꽉 채워야 한다.
            막기 -> _silence. Kit 이 제공하는 스위치 셋을 내린다:
              선택       omni.kit.viewport.utility.disable_selection
              우클릭 메뉴 omni.kit.viewport.utility.disable_context_menu
              카메라 조작 CAMERA_BINDINGS 설정을 {} 로 (비우면 아무 버튼도
                        카메라를 안 옮긴다). 원래 값은 _bindings 에 쥔다.
              앞의 둘은 핸들(_no_pick, _no_menu)을 쥐고 있는 동안만 꺼진다.
              판 위에 얹기, 제스처 매니저, RegisterScene 은 다 안 됐다.
            되돌리기 -> _restore. 핸들을 놓고 바인딩을 되돌린다. 못 읽었으면
              문서의 DEFAULT_BINDINGS 로 — 빈 채로 두면 Kit 이 영영 안 움직인다.
            _drop 이 판과 스위치를 같이 걷는다. 창 찾기는 viewport_window.
            결과는 '[ebs] input:' 로 찍힌다 — 어느 스위치가 내려갔고 안 내려갔는지.
          선택 -> _mute_selection. 골라지면 바로 지운다 (SELECTION_CHANGED
            구독, _stage_event). disable_selection 이 안 먹을 때의 보험.
          제스처 -> _pressed / _moved / _end_drag / _double / _wheel.
            왼쪽만 쓴다. 나머지 버튼은 우리 쪽에서 아무것도 안 한다.
          끌기 = 공전 -> _drag -> _turn. 반지름 고정, interest 를 월드 up 으로
            바라봐서 롤이 없다. 축은 끌기당 하나로 잠근다 (AXIS_LOCK) — 요와
            피치가 안 섞인다. 극 근처는 _room 이 막는다 (PITCH_LIMIT).
            속도 -> YAW_PER_PIXEL / PITCH_PER_PIXEL.
          휠 = 줌 -> _wheel -> _zoom. 반지름만 바뀌고 방향과 자세는 그대로.
            칸마다 같은 비율을 곱한다 (ZOOM_PER_NOTCH) — 멀리서는 성큼,
            가까이서는 자잘하게. 끝은 기본 거리 대비 ZOOM_NEAREST /
            ZOOM_FURTHEST. Refresh 는 _home 을 쓰므로 줌도 같이 되돌아온다.
          팔 하나를 돌리기와 줌이 같이 쓴다 -> _hold (지금 팔) + _settle
            (그 끝에 눈을 놓고 자세를 다시 세움). 자세를 매번 다시 세우므로
            롤이 쌓일 자리가 없다.
          더블클릭 = 중심 옮기기 -> _double. 찍은 표면이 새 interest 가 되고,
            카메라는 그만큼 평행이동한다 (_look_at: 팔과 반지름 그대로 두고
            interest 만 바꿔 _settle). 방향과 거리는 안 변하고, 찍은 점이
            화면 한가운데로 온다. 미루지 않고 그 자리에서 옮긴다 — 안 그러면
            다음에 조금 돌리는 순간 화면이 홱 돌아간다.
            무엇이 찍혔나 -> viewport.request_query (화면 좌표 -> 월드 좌표를
              뷰포트가 이미 안다). omni.kit.raycast.query 는 광선을 우리가
              만들어야 하고 익스텐션도 하나 더 켜야 해서 안 쓴다.
            좌표 -> _ndc (판 기준 -1..1) -> map_ndc_to_texture_pixel.
            결과 거르기 -> _picked. 허공과 OURS(우리가 그린 것)는 흘린다.
            Refresh 는 _home 의 interest 까지 되돌린다.
        거리 -> CAMERA_BACK. interest 에서 정면으로 그만큼 뒤. 고정이다.
          화면에 맞추지 않는다 — 배율이 달라지면 여유 길이를 눈으로 못 비교한다.
        대상 바운드 -> _world_range (여기가 카메라에 넘기는 상자).
        판정 패널 높이는 별개다 -> VERDICT_HEIGHT (build_verdict).
        뷰포트 전환 -> _viewport (omni.kit 없으면 조용히 실패).
        """
        return cls._simulate.focus()

    @classmethod
    def release_camera(cls):
        """원래 카메라 복귀 + 궤도 모드 해제 + show_equipment. 프림은 남긴다.

        구현 -> EbsSimulateCamera.release. 장비 되돌리기만 구현부 몫.
        지우는 것은 remove 뿐이고 teardown 만 부른다 — 세션 내내 같은 하나를
        쓴다. 지웠다 다시 만들면 그 사이 Kit 이 그 경로에 써 둔 것이 남는다.
        """
        return cls._simulate.release_camera()

    # -- 4단계 collide -------------------------------------------------------

    @classmethod
    def refresh_camera(cls):
        """카메라를 Camera 가 놓았던 자리로 되돌린다. 궤도 모드는 그대로.

        구현 -> EbsSimulateCamera.reset. place 가 _home 에 적어둔 것을 다시 쓴다.
        Camera 를 아직 안 눌렀으면 아무것도 안 한다.
        """
        return cls._simulate.refresh_camera()

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

        단계와 시간은 collide 가 끝날 때 콘솔에 낸다 -> _report_stages.
          단계마다 탐색(후보 모으기)과 검출(실제 판정)로 나뉜다:
            faces      3면 충돌     search=바운드+셀+_gather_nearby, detect=셀 판정
            clearance  빈 면 거리   search=프리즘 합집합 순회, detect=_nearest_in_prism
            equipment  내부 간섭    search=양쪽 수집+삼각형 읽기, detect=_meetings
            verdict    판정 조립    build
            markers    씬에 그리기  draw
          이름은 '단계: 종류'. 같은 이름을 여러 번 재면 한 줄로 더한다 —
            이른 반환 때문에 토막난 탐색도 한 줄로 읽힌다.
          단계를 늘리려면 그 이름으로 _stage_timer 를 하나 더 두면 된다.
          겉을 감싸는 타이머는 두지 말 것 — 안쪽과 이중으로 센다.

        셀 분할 -> _build_cells + GRID (지금 1, 면당 셀 1개).
        후보 수집 -> _gather_nearby (대부분 여기서 걸러짐).
          순회는 단계당 2회: check_collision 1, measure_faces 1.
          measure_faces 는 세 면 프리즘의 합집합으로 한 번만 걷는다.
          시작점은 면마다 다르다 -> _by_face (충돌) / _reach_by_face (거리).
            좌우 -> 이웃 장비 둘의 서브트리만 (_side_roots -> side_band,
              Camera 가 남길 장비를 고르는 그 판단). 스테이지 전체를 안 훑는다
              — 그 값이 collide 시간의 대부분이었다.
              빠지는 것: EQP_ 가 아닌 기둥·벽·덕트. 옆이 비면 좌우도 빈다 —
              전체로 되돌아가면 장비마다 다른 잣대가 된다.
            천장 -> 그대로 스테이지 전체. 위쪽 이웃을 찾는 방법이 없다.
              대신 상자를 천장 셀 위로만 잡아 위층 말고는 위에서 잘린다.
              전체라고 트리를 걷지는 않는다 -> _from_index.
            두 단계가 면마다 같은 잣대를 써야 한다. 한쪽만 좁히면 같은 벽을
              두고 '충돌 없음' 과 '여유 0.05 m' 가 같이 나온다.
            roots=None 이면 예전대로 한 번에 다 모은다 (밖에서 부르는 쪽).
            내부 간섭은 안 좁아진다 -> roots=[ebs] / [eqp].
          opacity 로 투명해진 장비는 그대로 상대다 (_is_visible 은 visibility
            만 본다) — 안 그러면 Camera 를 눌렀냐에 따라 결과가 달라진다.
          스테이지 전체 = 상자 목록 훑기 -> _stage_boxes / _from_index.
            트리를 타고 내려가며 상자로 자르는 것을 collide 마다 하지 않는다.
            목록은 한 번 만들고 Init 까지 쓴다 (_stage_index). 파일로 안 남긴다
              — .ebscache.json 은 XML 의 size/mtime 으로 신선도를 보는데,
              스테이지 상자는 레이어 조합과 세션 오버라이드에 걸려 있어서
              그 열쇠로는 못 지킨다. 틀리면 조용히 잘못된 판정이 나온다.
            담는 단위 -> 장비(EQP_)는 통째로, 그 밖의 지오메트리는 낱개로.
              장비 안은 상자가 겹쳤을 때만 연다 (_gather_nearby roots=[그것]).
            가시성은 안 굳힌다 -> 지나온 조상을 같이 담고, 물을 때 본다.
              그래서 그룹을 숨겼다 켰다 해도 목록을 다시 안 만든다.
            equipment_boxes 도 같은 목록에서 꺼낸다 — 스테이지를 두 번 안 걷는다.
            만드는 값은 로그에 'stage: index' 한 줄. 첫 collide 에만 나온다.
          느리면 여기가 아니라 캐시를 볼 것 -> _bounds_cache.
            시간의 대부분은 순회가 아니라 ComputeWorldBound 첫 계산이다.
            증거: 같은 walk 를 더 큰 상자로 도는 clearance: search 가
            faces: search 의 50분의 1이다 (데워진 캐시를 물려받는다).
            그래서 캐시는 인스턴스에 하나(_bounds), init 만 버린다.
            스테이지를 손댔으면 Init 을 다시 눌러야 한다 (_stage_index 도).
          EBS 상자 -> _ebs_bound. collide 한 번에 다섯 군데가 부르는데 부를
            때마다 BBoxCache 를 둘 만들어 EBS 모델을 두 번 훑었다 (뒤엣것은
            extentsHint 진단). 결과를 _ebs_box 에 들고 있다가 그대로 준다.
            버리는 곳 -> _forget_ebs: align 이 옮긴 뒤, _show_ebs 로 켜고 끈 뒤.
          가시성 -> _is_visible. 상자로 자른 다음에 묻는다. 훑는 것 전부에
            물으면 USD 값 해석이 프림마다 걸려 상자 계산보다 비싸진다.
            잘라내는 결과는 같다 (안 보이면 자식으로도 안 내려간다).
            표(_visible)는 collide 마다 새로 만든다 — 스테이지 트리에서
            숨긴 것이 다음 판정에 바로 먹어야 한다.
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

