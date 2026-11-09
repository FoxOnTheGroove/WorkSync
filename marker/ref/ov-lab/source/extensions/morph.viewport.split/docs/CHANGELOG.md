# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [1.5.1] - 2026-11-04
- 의존 버전 범위: `morph.stream.messaging` = { version = "1.0" }, `morph.perf.monitor` = { version = "1.0" } (^1.0)
- 수정: sideRatio 변경 state 푸시를 레이아웃 전환 중에도 미루고 완료 프레임에 보냄 (1.5.0은 재도킹만 기다려 다른 레이아웃→1+3 전환 중 rect 전부 null인 state를 보냄 — CONTRACT 약속과 불일치)
- 수정: 웹 핸들러 도중 shutdown 경합 RuntimeError → `unavailable` (이전 `failed_precondition`)
- 문서: 설정 sideRatio가 프로세스에 남아 재시작 시 마지막 런타임 값으로 시작함을 명시
- 테스트: 전환 중 side_ratio 푸시 보류, shutdown 경합 unavailable, 실행 중 RuntimeError failed_precondition

## [1.5.0] - 2026-11-02
- 실행 중 "1+3" 오른쪽 열 폭: Python `SplitView.set_side_ratio(ratio) -> float`(0.1..0.5로 잘라 적용값 반환, 숫자 아님·bool·NaN·inf는 ValueError), `get_side_ratio() -> float`
- 웹 `morph.viewport.split.set_side_ratio` (`{ratio}` → `{ok, side_ratio}`, 잘못된 값 `invalid_argument`). state 푸시·`get_state`에 `side_ratio` 필드 (outbound 스키마 필수)
- "1+3" 창 모드면 다음 프레임에 view 1~3만 재도킹 (창·뷰포트 API·카메라·포커스 유지, layout_changed 없음, 완료 때 state 1회). 같은 프레임 여러 호출은 마지막 값, 전환 중이면 끝난 뒤 최신 값
- 설정 `sideRatio`와 양방향 동기: set_side_ratio가 설정에 쓰고, 설정 직접 변경도 다음 프레임에 반영 (이전: 전환 시작 때만 읽음)
- 내부: 어댑터 `begin_layout(..., side_ratio=None)`, core 재배치 전환(`begin_relayout`)
- 테스트: `tests/test_side_ratio.py` (잘림, 잘못된 값, 헤드리스, 설정 동기, 재도킹 시 창·뷰포트 API 유지, 병합, 웹 회신·실패 code, state)

## [1.4.0] - 2026-11-01
- 카메라 목록: Python `SplitView.list_cameras() -> list[CameraInfo]` (`path`, `name`, `builtin`), 웹 `morph.viewport.split.list_cameras` (`{ ok, cameras }`, 스테이지 없으면 `failed_precondition`)
- `Usd.PrimRange` 기본 predicate로 `UsdGeom.Camera` 순회 (instance proxy·비활성·class 아래 제외). 사용자 카메라 경로순 먼저, Kit 기본 카메라(Persp/Top/Front/Right) 나중
- 요청 시에만 순회, 변경 푸시 없음. 성능 지표 `morph.viewport.split.list_cameras_ms`
- 테스트: `tests/test_cameras.py` (순서, 제외 규칙, set_view_camera 검증 일치, 웹 회신/실패 code), 싱글턴 통합 테스트

## [1.3.1] - 2026-11-01
- CONTRACT: 설정 `sideRatio` 설명에 NaN/inf/-inf 처리(경고 후 기본값 0.25)와 설정 없음(경고 없이 기본값)을 명시. 동작 변경 없음
- 테스트: `sideRatio` 잘못된 값에 `-inf` 추가

## [1.3.0] - 2026-11-01
- 레이아웃 `"1+3"` 추가: view 0 왼쪽 크게, 오른쪽 열에 view 1/2/3 위→아래 같은 높이. 웹 `set_layout` enum, `defaultLayout`에도 반영
- 설정 `sideRatio` 추가 (float, 기본 0.25, 0.1..0.5로 자름, 숫자 아님은 경고 후 기본값): "1+3" 오른쪽 열 폭. 레이아웃 전환 시작 때 읽음
- 도킹 계획 단계마다 비율(`dock_in` ratio = 새로 붙는 창의 몫). 격자 레이아웃은 0.5 그대로
- 같은 뷰 개수 전환(2x2 ↔ 1+3)도 창 재사용 + 재도킹. 한 뷰를 다시 도킹하면 계획상 뒤의 뷰도 모두 다시 도킹
- 웹 state 스키마: `rect`가 object면 `x`,`y`,`w`,`h` 필수 (보내는 값은 이미 항상 네 필드)
- CONTRACT: rect 문구를 코드와 맞춤 (뷰 하나 읽기 예외는 경고 없이 null, 비교 기준은 마지막으로 만든 state payload)

## [1.2.0] - 2026-11-01
- 웹 state(`state` 푸시, `get_state` 회신)의 각 뷰에 `rect` 추가: 앱 메인 창 기준 0..1 정규화 `{x,y,w,h}`, 원점 좌상단. 논리 전용·창 없음·전환 중 등은 `null`
- rect 변경(0.002 이상) 감지 시 state 재푸시: 전환 중이 아닐 때 약 30프레임마다, 전환 완료 약 5프레임 뒤 한 번 더
- CONTRACT: 레이아웃별 view_id 배치 표. carb 이벤트·Python API·설정 변경 없음

## [1.1.0] - 2026-11-01
- 설정 추가: `hideTabBar`(뷰 창 탭바 숨김+비활성, view 0 포함), `viewPadding`(뷰 창 padding_x/y), `dockSplitterSize`(ImGui 전역 DockSplitterSize push, 앱 전체 적용). 기본은 모두 건드리지 않음
- 레이아웃 전환 완료 시(layout_changed 전)와 약 30프레임마다 확인해 적용·복구. 실패는 경고만. shutdown 때 view 0 꾸밈 복원, 분할선 스타일 pop
- 선택 의존 `omni.kit.renderer.imgui`. 공개 API·이벤트·웹 메시지 변경 없음

## [1.0.0] - 2026-10-31
- 호환 깨짐 (stable 확정, morph.stream.messaging 1.0.0 대응): 웹 type `morph.viewport.*` → `morph.viewport.split.*`
- 웹 회신 통일: 성공 `{ok:true, ...}` (`get_state` 포함), 예상된 실패는 `{ok:false, error, message}` (`not_found`/`invalid_argument`/`failed_precondition`/`unavailable`)
- carb 이벤트 이름·페이로드, Python API, 설정은 변경 없음
- CONTRACT: 상태 푸시는 웹 연결(hello) 이후만, 호환성 정책

## [0.1.2] - 2026-10-31
- 수정: 렌더러 준비 전 추가 뷰포트 창 생성으로 인한 크래시 (asyncInit). 기본 뷰포트가 렌더한 프레임을 받은 뒤 창을 만들고(타임아웃 시 1x1 복귀), 창은 프레임당 하나씩 만든다
- 수정: 같은 view_id `focus_view`도 Kit 활성 뷰포트가 다르면 다시 포커스 (focus_changed 없음)
- 웹 state 송신 예외 격리 (경고 로그)
- `SplitView.state_payload` → 내부 `_state_payload`

## [0.1.1] - 2026-10-30
- 기본 구현: 레이아웃 프리셋(1x1/1x2/2x1/2x2), 뷰별 카메라, 포커스 뷰(= Kit 활성 뷰포트), 논리 전용 모드, 웹 메시지, perf 지표
- 리뷰 반영: 전환 중 외부 카메라 변경 보존, 늦은 창 연결 시 view 0 카메라 채택, 전환 완료 예외 처리, startup 실패 롤백, 선택 의존 import 실패 캐시, view_count는 바뀔 때만 기록
- 수정: view 0 창(weakref.proxy)을 dock_in 인자·active_window 대입에 그대로 쓰던 문제. 창 준비 실패 시 1x1 복귀 정책

## [0.1.0] - 2026-10-30
- 최초 초안 (템플릿)
