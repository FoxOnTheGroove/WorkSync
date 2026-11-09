# MORPH Stream Messaging [morph.stream.messaging]

웹 스트리밍 클라이언트와 Kit 사이의 유일한 메시지 통로.

- 다른 익스텐션은 `get_messaging()`으로 메시지 타입을 등록(`register_inbound` / `register_outbound`)하고 `send`로 송신한다. 요청 회신은 `ctx.reply_ok` / `ctx.reply_error`.
- 봉투 `{ "type", "v", "id", "payload" }` 직렬화·검증, 메인 업데이트 루프 라우팅, 핸들러 예외 격리, 연결 상태 이벤트를 제공한다.
- 전송 계층은 `omni.kit.livestream.messaging`(선택 의존)이며, 없으면 등록·라우팅·검증만 동작한다.

공개 계약은 익스텐션 루트의 `CONTRACT.md`가 기준이다.
