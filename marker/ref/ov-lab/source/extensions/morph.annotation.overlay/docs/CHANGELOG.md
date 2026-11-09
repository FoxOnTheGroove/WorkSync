# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [0.4.1] - 2026-11-04
### Fixed
- 큰 Mesh 메모리 상한 없음: 크기 상한(면 200만 또는 팬 삼각형 300만, `MAX_FACES`·`MAX_TRIS`)을 넘으면 메시 데이터(월드 좌표·삼각형·임시 배열)를 만들지 않고 면을 13만 개씩 훑어 맞힌 다각형만 `polygon`으로 돌려줌 (`_scan_polygon`, 1000만 면 추가 약 0.16 GB). 캐시는 개수(8)에 더해 바이트 예산(`CACHE_BYTES` 512 MB, `nbytes` 합)으로 오래된 것부터 버림 (방금 만든 하나는 유지). 상한 안 메시도 삼각형 단위 계산을 조각(2^18)으로 나누고 인덱스를 int32로 줄임 (상한 크기 데이터 약 0.36 GB, 만드는 동안 최대 약 0.75 GB)
- 평면 묶음: 같은 높이의 섬이 후보가 아닌 면을 거쳐 합쳐지던 것 — 후보 안에서 시작 다각형과 변으로 이어진 것만 (flood fill, `_connected`). 그런 묶음은 면별로 캐시 (`region_of`)
- 퇴화 면(면적 0·한 직선·가는 조각): 두께 ≤ 평면 허용이면 각도 대신 정점의 평면 거리로만 판정해 평면을 나누지 않음, 시작 다각형으로 고르지 않음. 묶음 방향 기준을 묶음의 첫 면 대신 가장 큰 면으로
- NaN·inf 정점: Mesh 전체가 `point`가 되던 것 — 그 정점을 쓰는 다각형만 제외
- Cylinder 옆면(전단): 면적 = 직단면 둘레 × 모선 길이, outline = center를 지나는 직단면, size = `[직단면 긴 지름, 모선 길이]`
- `_components`가 200번 반복 후 조용히 멈출 수 있던 것: 제한 제거 (매 반복 루트 수가 줄어 종료 보장)

### Changed
- `pick_face`: 면 20만(`ASYNC_FACES`) 이상 Mesh의 첫 빌드와 상한 초과 훑기를 `run_in_executor` 작업 스레드에서 (`FaceComputer.compute_async`). USD 읽기는 메인 스레드, 결과는 메인 스레드에서 확인(그 사이 prim 변경·장면 전환이면 캐시하지 않고 한 번 다시 만듦) 후 면 계산. 같은 메시 동시 클릭은 빌드 하나를 공유. 취소돼도 빌드 결과는 캐시. shutdown은 진행 중 면 작업을 취소
- perf `scope("morph.annotation.overlay.face_build_ms")` (빌드·훑기, 작업 스레드 포함), 1초 넘으면 경고 1회

### Tests
- `test_public_symbols`에 `FACE_KINDS`·`EVENT_FACE_SELECTED`
- 같은 높이 섬 분리, 면적 0 면, 한 직선·조각 삼각형 다리, NaN 정점, 전단(부모 scale(1,3,1) + 자식 rotateZ 45°)의 Cube 면·Cylinder 끝 원판·옆면(역전치 법선 포함), 삼각형 상한 초과 훑기(캐시 없음), 바이트 예산·개수 제한, `_components` 긴 사슬, 비동기 빌드(동기와 같은 값, 공유, 변경 중 다시 빌드, 취소·clear), overlay `pick_face` 작업 스레드 경로·shutdown

## [0.4.0] - 2026-11-04
### Added
- 면 선택: `AnnotationOverlay.pick_face(viewport_name, x, y)` → `{position, face}` / `None` (`pick`과 같은 쿼리, 선택은 안 함), `selected_face`, `select_face(face | None)`, 이벤트 `morph.annotation.face_selected`, 상수 `EVENT_FACE_SELECTED`·`FACE_KINDS`
- Face 계산 (`face.py`): Mesh는 맞힌 다각형에서 1° 이내·같은 평면 이웃으로 넓힌 평면 묶음(면적 가중 중심, 경계 고리, 위치로 정점 용접), 해석 gprim(Cube 면, Cylinder·Cone 끝 원판, Cylinder 옆면, Sphere)은 변환 행렬(비균등 스케일 포함) 적용, 그 밖은 `point`. instance proxy, 타임 샘플, 면 수 상한(200만, 넘으면 `polygon`)
- Mesh 캐시: (prim 경로, 시간 코드)별 기하·인접·평면 묶음을 한 번 만들고(Morton 정렬 삼각형 블록, 묶음별 합·경계 미리 계산) `Usd.Notice.ObjectsChanged`·장면 열기/닫기에 버림. 100만 면 평면 클릭 약 20 ms. perf `scope("morph.annotation.overlay.face_ms")`
- 웹 `morph.annotation.overlay.pick_face` (지연 회신, 맞으면 선택·빗나가면 해제), `morph.annotation.overlay.clear_face`, `list`·`state`에 `face`
- 그리기: 선택된 면 청록 외곽선 4px + 중심 십자 + 정보 상자(중심·법선·면적)
- 편집 창: [면 선택], 선택된 면 정보, [면 중심을 폼에](label/inlet/outlet position, inlet -normal, outlet +normal, plane origin·normal·size × 1.2), [면 선택 해제]
- 테스트: 해석 gprim 면(이동·회전·비균등 스케일), 박스 메시 한 면(삼각형 4개 → 1면, 분리 정점 포함), L자 면적 가중 중심, 감김 혼합, 곡면 → polygon, 상한, instance proxy, 타임 샘플, 큰 메시 클릭 시간, 캐시 무효화, 선택 이벤트·장면 열 때 해제, 웹 pick_face/clear_face·state, 편집 창 버튼, 그리기, prim 불변(pick_face·select_face·편집 경로)

### Changed
- 장면 열기·닫기 때 `autoLoad`와 무관하게 면 선택 해제·면 캐시 비움
- `plane_basis`를 `face.py`로 옮김 (`drawing`에서 다시 내보냄)

### Fixed
- `pick()`(과 내부 `pick_face`)이 돌려준 Future를 호출자가 취소하면 쿼리 작업이 타임아웃까지 `pending`에 남던 것

## [0.3.2] - 2026-11-04
### Fixed
- 편집 창 한글 `??`: 위젯 스타일 `font`(맑은 고딕)가 라틴 글리프 범위로만 구워져 앱 글꼴(한글 범위 포함) 대신 쓰이던 것. 기본으로 스타일 글꼴을 지정하지 않고 앱 글꼴을 따른다
- Kit 크래시(`[omni.ui] setBytesData called with nullptr`): 명시 `fontPath`가 글꼴이 아닌 파일(테스트의 1바이트 가짜 ttf)이면 omni.ui가 그 스타일 글꼴을 못 읽고 프로세스가 죽던 것. `fontPath`와 `EditorWindow(font_path=...)`는 TrueType/OpenType/TTC 시그니처를 확인해(`fonts.is_font_file`) 아니면 경고 후 앱 글꼴을 쓴다
- 테스트 크래시: `test_controller_default_no_style`가 가짜 ttf를 스타일로 창을 빌드하던 것. 헤드리스 테스트에서 스타일 글꼴로 창을 빌드하는 검사(`test_window_explicit_font` 포함)를 빼고 스타일 dict 검사로 대신함. 창은 한 프레임 이상 지난 뒤 destroy

### Changed
- 설정 `fontPath` `""` = 스타일 없음(앱 글꼴). 자동 탐색한 글꼴은 스타일로 쓰지 않고, 명시한 값만 적용

### Added
- 앱 `/app/font/customFontPath`와 `fontPath`가 모두 비었으면 시작 시 경고 1회 (앱 .kit `font.customFontPath`·`customRegionFiles` 안내)
- 테스트: 기본 스타일 없음(자동 탐색 무시), 명시 `fontPath` 적용(dict), 글꼴 아닌 파일 거부, 경고 문구·1회, 컨트롤러 기본 글꼴

## [0.3.1] - 2026-11-04
### Fixed
- `pick`: 렌더 해상도 없음·쿼리 거부·shutdown 뒤 호출이 호출 즉시 `RuntimeError`를 던지던 것을 예외가 설정된 Future로 (호출 즉시 예외는 `KeyError`·`ValueError`뿐, CONTRACT대로)
- `pick`: `request_query`가 `None`(hydra texture 없음)이면 5초 타임아웃 대신 바로 거부 (`RuntimeError`, 웹 `unavailable`). 이웃 픽셀 거부·호출 오류는 normal `None`
- 한글 `??`: 설정 `fontPath`(기본 `""` = 자동 탐색, Windows 맑은 고딕 → 다른 CJK 글꼴), 편집 창 스타일 `font`. 앱 글리프 범위용 `data/fonts/korean_region.txt`
- 편집 창이 분할 뷰포트를 가리던 것: 처음 열 때 `Property`(없으면 `Stage`) 창에 탭으로 도킹
- 편집 창 destroy 시 visibility 콜백 해제

### Added
- `AnnotationOverlay.is_picking`, `cancel_pick()` (편집 창 위치 찍기 대기 확인·취소)
- 테스트: pick 실패가 Future로 오는지(거부·None·해상도 없음·호출 오류·shutdown), 웹 pick 거부 지연 회신, 글꼴 탐색·설정·스타일·글자 목록, 도킹(대상 있음·없음·destroy 전 취소), 위치 찍기 취소·창 숨김·클릭 후 Kit 선택 복원(가짜 토큰), `add_menu` 반환값

## [0.3.0] - 2026-11-04
### Added
- `AnnotationOverlay.pick(viewport_name, x, y)`: 뷰포트 이미지 정규화 좌표 → 월드 위치 + 표면 normal (`request_query` 3회, 이웃 픽셀 외적, 카메라 쪽), 빈 곳은 `None`, 5초 타임아웃
- 웹 `morph.annotation.overlay.pick` (지연 회신 `{hit, position, normal}`), `morph.annotation.overlay.select`
- 선택: `select(id | None)`, `selected_id`, 이벤트 `morph.annotation.selection_changed`, `list`·`state`에 `selected_id`, 선택 강조 그리기, 삭제·다시 읽기·장면 열기/닫기 때 자동 해제
- 데스크톱 편집 창 "MORPH Annotations": 설정 `editorWindow`(기본 false), 메뉴 Window > MORPH Annotations (`omni.kit.menu.utils` optional), 목록·폼·적용·삭제·다시 읽기·저장, "뷰에서 위치 찍기"(클릭 한 번, 찍는 동안 Kit 클릭 선택 끔)
- 의존: `omni.ui`, `omni.kit.viewport.utility`(optional), `omni.kit.menu.utils`(optional)
- 테스트: pick 콜백 흐름(가짜 viewport_api), normal 방향, 오류, 선택 규칙·이벤트, 웹 select/pick 지연 회신, 편집 모델 → add/update, 창 열기/닫기, prim 불변(pick·select·편집 경로)

## [0.2.0] - 2026-11-04
### Added
- 설정 `showDescription` (`"always"` | `"never"`, 기본 `"always"`)
- 웹 `morph.annotation.overlay.set_options` (`show_description`, `text_scale`), `list` 회신·`state` 푸시에 `show_description`·`text_scale`
- 작은 뷰포트(해상도 높이 < 400px)에서 글자 0.8배
- 테스트: 주석 작업이 prim 수·루트 레이어(dirty·내용)를 바꾸지 않음, 확장 startup/shutdown 등록 해제, 원자적 저장 실패 시 기존 파일 유지, `io_error`, 웹 update `null` = 기본값, `set_options`, 줄바꿈·상자 폭

### Fixed
- 웹 회신 `message`에 절대 경로가 나가던 문제: 파일 이름과 `strerror`만 담고 전체 경로는 로그에만
- 라벨 상자가 글자보다 좁던 문제: 전각 1.0em / 그 외 0.66em 폭 어림, description 40자 줄바꿈·최대 2줄·`…`
- 저장 시 기존 파일 권한을 임시 파일에 복사 (새 파일 0644)
- registry가 시작 시 이미 있어도 enable/disable 훅을 걸어 나중의 비활성·재활성을 따라감. registry 없음은 `log_warn`
- perf 비활성 시 테스트가 조용히 통과하던 것을 `skipTest`로

### Changed
- `__all__`에서 `AnnotationOverlayExtension` 제외, `AnnotationOverlay.closed` 프로퍼티 제거 (내부 `_closed`)
- CONTRACT "그리기" 절을 실제 동작에 맞게 다시 씀

## [0.1.0] - 2026-11-04
- 최초 초안 (골격, CONTRACT)
- 기본 구현: 주석 모델 검증·기본값·추가 필드 보존, `AnnotationOverlay` API, 사이드카 읽기/원자적 저장,
  stage OPENED/CLOSED 자동 읽기, `morph.annotation.changed` 이벤트, 웹 메시지 7종 + state 푸시,
  `omni.ui.scene` 그리기(모든 뷰포트, 카메라 뒤 숨김, 변경 시에만 재구성), 테스트
