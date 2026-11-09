# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [1.1.1] - 2026-11-04
- 의존 버전 범위 명시: `morph.stream.messaging` `1.0`(^1.0), `morph.perf.monitor` `1.0`(^1.0). 공개 동작 변화 없음

## [1.1.0] - 2026-11-01
- `CameraController.delete_preset` / `list_presets`(코드 포인트 오름차순) / `get_preset` 추가
- 웹 메시지 `morph.camera.control.save_preset` / `list_presets` / `delete_preset` 추가
- 웹 save_preset 상한: 이름 64자(`invalid_argument`), 프리셋 64개(`resource_exhausted`, 덮어쓰기는 허용)

## [1.0.0] - 2026-10-31
- stable 확정. morph.stream.messaging 1.0 대응 (호환 깨짐)
- 웹 type `morph.camera.*` → `morph.camera.control.*` (orbit/pan/zoom/apply_preset/state)
- 웹 회신을 `reply_ok` / `reply_error`로 통일: orbit/pan/zoom 성공도 `{ok:true}`, 실패 code `invalid_argument` / `not_found` / `failed_precondition` (대상 없음이면 명령을 받지 않음)
- CONTRACT에 없던 `CameraController.update` 등 내부 메서드를 밑줄 접두어로 비공개화, `viewport_name`은 공개 프로퍼티로 명시
- `set_active_camera`의 `active_changed`를 호출당 최대 1회로 합침, 무효 뷰포트 쓰기는 `RuntimeError`
- CONTRACT: 단위·방향, 상태 푸시 조건, 의존 버전, 호환성 정책, 사용 예(Python IExt·JS) 갱신

## [0.2.0] - 2026-10-30
- 기본 구현: orbit/pan/zoom/look_at(보간), 전역 프리셋, set_active_camera, get_state
- 조작 대상 = 포커스 뷰(Kit 활성 뷰포트), `morph.viewport.focus_changed` 수신
- 프레임당 병합 반영, `morph.camera.changed`/`morph.camera.active_changed` 발행
- 웹 메시지 orbit/pan/zoom/apply_preset 수신, `morph.camera.state` 송신 (`stateRateHz` 제한)
- 템플릿 예제 코드 제거
- 리뷰 반영: 부모 scale 아래 COI 왕복 수정, 쓰기 레이어 규칙, 보간 경로(eye 구면 보간), 같은 프레임 보간+상대 명령,
  set_active_camera의 활성 뷰포트 재확인, 스테이지 열림 시 취소, 무효 뷰포트 처리, 송신 ValueError 처리

## [0.1.0] - 2026-10-30
- Initial version of basic python extension template
