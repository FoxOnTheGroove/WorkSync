# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [1.0.1] - 2026-11-01
### Changed
- 문서만 (동작 변경 없음): CONTRACT 스키마 부분집합 설명에 `required`·`properties`·`additionalProperties`는 object 값에만, `items`는 배열 값에만 적용됨을 명시

### Added
- 위 동작을 고정하는 스키마 단위 테스트


## [1.0.0] - 2026-10-31
상태 stable. 호환 깨짐 — 자세한 내용은 `CONTRACT.md` 변경 이력.

### Changed (breaking)
- 메시지 type 규칙: `<owner ext-id>.<name>` (`<name>` = `[a-z][a-z0-9_]*`, 점 금지). domain 접두어 형식은 거부
- 자체 타입: `morph.stream.messaging.error` / `.hello` / `.bye` (구 `morph.stream.error` / `hello` / `bye`)
- 회신 형식 통일: `{ ok: true, ... }` / `{ ok: false, error, message, ... }`. `reply`가 형식 강제, 컨텍스트당 1회
- 핸들러 예외 → 같은 type·id로 `{ ok: false, error: "handler_error", message }` 회신
- `id: null` 요청에는 같은 type으로 회신하지 않음
- `morph.stream.messaging.error` payload `{ ok: false, error, message, type }`
- 회신 봉투 `v` = inbound 등록 버전, outbound 스키마 검증 안 함
- hello 회신 `protocol: 2`, bye도 `{ ok: true }` 회신

### Added
- `MessageContext.reply_ok`, `reply_error`, `replied`
- `morph.stream.invalid_message` 페이로드에 `error` code

### Fixed
- `connectEvents` / `disconnectEvents`가 문자열 하나일 때 글자 단위로 쪼개지던 문제
- shutdown 이후 `ctx.reply*`가 형식 오류로 예외를 낼 수 있던 문제 (이제 조용히 무시)


## [0.2.0] - 2026-10-30
### Added
- 메시지 타입 등록/해제 (`register_inbound`, `register_outbound`, `Subscription`), 타입 소유권·이름 규칙 검사
- 봉투 직렬화/역직렬화, JSON Schema 부분집합 검증
- 메인 업데이트 루프 라우팅, 핸들러 예외 격리, 스레드 안전 송신 큐 (`maxQueue`)
- `morph.stream.hello` / `morph.stream.bye` 핸드셰이크와 연결 상태 이벤트, `connectEvents` / `disconnectEvents` 설정
- livestream 메시징 전송 어댑터 (`morph.stream.in` / `morph.stream.out` 채널, 선택 의존)
- 수신 항목 단위 예외 격리(깊은 중첩 JSON의 `RecursionError` 포함), 수신 실패와 무관한 송신 플러시
- 단위·통합·전송 어댑터 테스트

### Changed
- `owner`는 키워드 전용 인자 (위치 인자로 주면 `TypeError`)
- 다른 owner가 소유한 type을 `register_outbound`로 선언하면 `ValueError`
- `register_outbound`가 `None` 대신 `Subscription`을 반환
- 수신 큐 overflow 시 연결 상태 항목은 버리지 않음

### Removed
- 템플릿 예제 코드 (`some_public_function`, `MyExtension`)


## [0.1.0] - 2026-10-30
- 골격 생성 (CONTRACT 초안)
