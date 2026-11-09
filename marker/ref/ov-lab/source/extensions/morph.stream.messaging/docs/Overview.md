# Overview

`morph.stream.messaging`은 MORPH 익스텐션들이 웹 클라이언트와 메시지를 주고받는 단일 통로다.

## 구조

```
웹 클라이언트 ──(livestream: morph.stream.in)──▶ 전송 어댑터 ──▶ 수신 큐
                                                             │ (메인 업데이트 루프)
                                                             ▼
                                   봉투 해석 → 타입 조회 → 버전/스키마 검증 → 핸들러
                                                             │
웹 클라이언트 ◀─(livestream: morph.stream.out)── 전송 어댑터 ◀── 송신 큐 ◀── send()/reply() (모든 스레드)
```

- **봉투**: `{ "type": str, "v": int, "id": str|null, "payload": dict }`, 전송 시 JSON 문자열.
- **타입 소유권**: 타입 하나는 등록한 익스텐션 하나만 소유한다. 타입 이름은 `<owner ext-id>.<name>` (예: `morph.viewport.split.set_layout`).
- **스레드**: 수신 핸들러와 이벤트 발행은 항상 메인 업데이트 루프(`update` 이벤트)에서 일어난다. `send`와 `ctx.reply_*`는 어느 스레드에서나 호출할 수 있다.
- **회신**: 요청과 같은 type·id로 `{ ok: true, ... }` / `{ ok: false, error, message }`.
- **격리**: 핸들러 예외는 로그와 `handler_error` 회신으로 끝나며 다른 핸들러나 연결에 영향을 주지 않는다.
- **검증 실패**: `morph.stream.invalid_message` 이벤트 발행 + 웹에 `morph.stream.messaging.error` 회신.
- **연결 상태**: 웹의 `morph.stream.messaging.hello` / `bye` 핸드셰이크, 또는 설정한 carb 이벤트로 `morph.stream.client_connected` / `client_disconnected`를 발행한다.

## 사용

API, 이벤트, 설정, Python/JS 사용 예는 `CONTRACT.md`를 본다.
