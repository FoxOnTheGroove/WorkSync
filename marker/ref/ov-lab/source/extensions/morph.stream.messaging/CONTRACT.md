# morph.stream.messaging CONTRACT

> 다른 익스텐션 에이전트가 이 익스텐션을 쓸 때 읽는 **유일한** 문서. 여기 없는 것은 내부 구현이므로 의존하지 않는다.

- 버전: 1.0.1
- 상태: stable
- 담당 에이전트: ext-stream
- 의존: (없음)

## 목적
웹 스트리밍 클라이언트와 Kit 사이의 유일한 메시지 통로. 메시지 타입 등록, 봉투 직렬화, 스키마 검증, 메인 스레드 라우팅, 요청 회신 형식을 맡는다.

## 언제 쓰나 / 쓰지 않나
- 쓴다: 웹 클라이언트에서 명령을 받거나, 웹으로 상태·이벤트를 보낼 때
- 쓰지 않는다: 익스텐션끼리의 내부 통신 (→ `carb.eventdispatcher` 이벤트 사용)
- 쓰지 않는다: `omni.kit.livestream.messaging` 직접 사용 (금지)

## 메시지 봉투
```json
{ "type": "morph.viewport.split.set_layout", "v": 1, "id": "optional-correlation-id", "payload": { } }
```
- `type`: `<owner ext-id>.<name>` (아래 "type 이름 규칙"). 타입 하나 = 소유 익스텐션 하나.
- `v`: 메시지 스키마 버전(양의 정수). 호환 깨지는 페이로드 변경 시 올린다. 수신 시 생략하면 1.
- `id`: 요청/응답 상관 ID (`str` 또는 `null`). 수신 시 생략하면 `null`. `null`이면 "회신이 필요 없는 요청"이다 (아래 "회신 규칙").
- `payload`: 항상 JSON object. 수신 시 생략하면 `{}`.

### type 이름 규칙
- 형식: **`<owner>.<name>`**. `<owner>`는 등록하는 익스텐션 ID 전체(`morph.<domain>.<name>`), `<name>`은 정규식 `[a-z][a-z0-9_]*` (소문자로 시작, 소문자·숫자·`_`만, **점 금지**).
  - 예: owner `morph.viewport.split` → `morph.viewport.split.set_layout`, `morph.viewport.split.state`
  - 거부(`ValueError`): `morph.viewport.set_layout`(domain 접두어만), `morph.viewport.split.view.focus`(name에 점), `morph.viewport.split.SetLayout`(대문자), 다른 익스텐션 ID 접두어
- `<name>`에 점을 금지하는 이유: type은 항상 정확히 4개 세그먼트가 되어 **type만 보고 소유 익스텐션을 알 수 있다** (마지막 점 앞 = owner). 웹 측 라우팅·로그 필터도 이 규칙에 기댈 수 있다. 계층이 필요하면 `_`로 잇는다 (`view_focus`).
- 소유권은 이름에서 결정되므로 다른 익스텐션의 type은 등록 자체가 불가능하다.

## 회신 규칙 (웹 요청의 성공/실패)
웹→Kit 요청(`id`가 문자열)의 회신은 **요청과 같은 type·같은 id**로, payload는 항상 아래 둘 중 하나다.

```json
{ "ok": true,  "...": "결과 필드 (선택)" }
{ "ok": false, "error": "<code>", "message": "<사람이 읽는 설명>", "...": "추가 필드 (선택)" }
```
- `error` code: 정규식 `[a-z][a-z0-9_]*` (소문자 snake_case). 웹 코드가 분기하는 값이므로 CONTRACT에 표로 적는다. `message`는 표시·로그용이며 내용에 기대어 분기하지 않는다.
- 핸들러는 `id`가 문자열인 요청마다 **정확히 한 번** 회신한다 (성공도 회신. 결과가 없으면 `ctx.reply_ok()`). stream은 자동 성공 회신을 하지 않는다 (지연 회신을 허용하기 위해).
- 핸들러가 잡지 않은 예외: stream이 같은 type·id로 `{ "ok": false, "error": "handler_error", "message": "<예외 클래스>: <예외 메시지>" }`를 회신한다 (message 최대 500자, 넘으면 잘림). 핸들러가 이미 회신했으면 다시 회신하지 않고 로그만 남긴다.
- **`id`가 `null`이면 같은 type으로는 아무것도 회신하지 않는다** (`ctx.reply*`는 형식 검사만 하고 송신 생략, `handler_error`도 로그만). 이유: 상관 ID 없는 같은 type 메시지는 웹에서 Kit→웹 푸시와 구별되지 않는다.
- 핸들러까지 가지 못한 실패는 같은 type이 아니라 **`morph.stream.messaging.error`** 로 회신한다 (아래 "웹 메시지"). 이것은 `id`가 `null`이어도 보낸다 (`id: null`).
- 권장 핸들러 code: `invalid_argument`(값이 범위 밖 등 스키마로 못 거른 입력 오류), `not_found`, `failed_precondition`(상태상 지금 불가), `unavailable`(일시적 불가). 필요하면 각 익스텐션이 추가하고 자기 CONTRACT에 적는다. `handler_error`와 아래 stream 실패 code는 핸들러가 쓰지 않는다.

### 실패 code (stream이 만드는 것)
| code | 회신 type | 원인 |
|---|---|---|
| `bad_envelope` | `morph.stream.messaging.error` | 봉투 형식 오류 (JSON 아님/객체 아님, `type`·`id`·`v`·`payload` 형식, 너무 깊은 중첩) |
| `unknown_type` | `morph.stream.messaging.error` | 등록된 inbound 핸들러가 없는 type |
| `unsupported_version` | `morph.stream.messaging.error` | 수신 `v` > 등록 `version` |
| `invalid_payload` | `morph.stream.messaging.error` | 등록 스키마 위반 |
| `internal_error` | `morph.stream.messaging.error` | 수신 처리 중 stream 내부 오류 (`type: ""`, `id: null`) |
| `handler_error` | 요청과 같은 type | 핸들러가 잡지 않은 예외 |

## 공개 Python API
import: `from morph.stream.messaging import get_messaging`

| 심볼 | 시그니처 | 설명 | 주의 |
|---|---|---|---|
| `get_messaging` | `() -> Messaging \| None` | 싱글턴 | 이 익스텐션 startup 이후 유효, shutdown 이후 `None` |
| `Messaging.register_inbound` | `(type: str, handler: Callable[[dict, MessageContext], None], schema: dict \| None = None, *, owner: str, version: int = 1) -> Subscription` | 웹→Kit 메시지 핸들러 등록 | 핸들러는 메인 업데이트 루프에서 호출됨. 같은 type 중복 등록, type 이름 규칙 위반(다른 owner 접두어 포함), 잘못된 스키마, 호출 불가 handler면 `ValueError`. `v > version`인 수신 메시지는 `unsupported_version`으로 거부 |
| `Messaging.register_outbound` | `(type: str, schema: dict \| None = None, *, owner: str, version: int = 1) -> Subscription` | Kit→웹 메시지 타입 선언 | `send` 전에 반드시 선언. 이름 규칙 위반·잘못된 스키마면 `ValueError`. 같은 owner의 재선언은 스키마/버전 교체이며, **재선언하면 이전에 받은 `Subscription`은 무효가 되어 `unsubscribe()`가 아무 일도 하지 않는다** — 마지막에 받은 것만 보관한다 |
| `Messaging.send` | `(type: str, payload: dict, reply_to: str \| None = None) -> None` | 웹으로 송신 (푸시) | 아무 스레드에서 호출 가능 (내부 큐잉, 다음 업데이트에 전달). 미선언 타입·스키마 위반·dict 아님·JSON 직렬화 불가(NaN 포함)면 `ValueError`. payload는 호출 시점에 직렬화(이후 변경 무관). 봉투 `v` = outbound 선언 `version`. shutdown 이후 호출은 무시. 요청 회신에는 쓰지 않는다 (→ `MessageContext`) |
| `Messaging.is_connected` | 프로퍼티 `-> bool` | 웹 클라이언트가 하나 이상 연결돼 있는지 | 아래 "연결 상태의 한계" |
| `Subscription.unsubscribe` | `() -> None` | 등록 해제 | 익스텐션 `on_shutdown`에서 호출. 여러 번 호출·이 익스텐션 shutdown 이후 호출 모두 안전. **GC로 자동 해제되지 않음** |
| `MessageContext` | `.type: str`, `.v: int`(수신 봉투 v), `.id: str \| None`, `.replied: bool` | 수신 컨텍스트 (핸들러 두 번째 인자) | 핸들러가 반환한 뒤에도 보관했다가 회신할 수 있다 (지연 회신) |
| `MessageContext.reply_ok` | `(**fields) -> None` | 성공 회신 `{ "ok": true, **fields }` | `fields`에 `ok` 키면 `ValueError` |
| `MessageContext.reply_error` | `(code: str, message: str, **fields) -> None` | 실패 회신 `{ "ok": false, "error": code, "message": message, **fields }` | `code` 형식 위반·`message`가 str 아님·`fields`에 `ok`/`error` 키면 `ValueError` (`code`/`message`라는 이름의 추가 필드는 둘 수 없음) |
| `MessageContext.reply` | `(payload: dict) -> None` | 원시 회신. payload가 회신 규칙(`ok: bool`, 실패면 `error`·`message`)을 지켜야 함 | 위반·dict 아님·직렬화 불가면 `ValueError` (이 경우 회신하지 않은 것으로 남아 다시 시도 가능). 보통은 `reply_ok`/`reply_error`를 쓴다 |
| `EVENT_CLIENT_CONNECTED`, `EVENT_CLIENT_DISCONNECTED`, `EVENT_INVALID_MESSAGE` | `str` 상수 | 아래 발행 이벤트 이름 | 문자열을 직접 써도 된다 |

`reply_ok` / `reply_error` / `reply` 공통:
- 같은 type·같은 id로 회신한다. `register_outbound` 불필요하며 outbound 스키마로 검증하지 않는다 (회신 형식 검사만).
- 회신 봉투의 `v`는 **요청의 `v`가 아니라 수신 시점의 inbound 등록 `version`** 이다 (예: `version=2`로 등록한 핸들러가 `v: 1` 요청에 회신하면 `v: 2`). 웹은 회신 `v`로 회신 payload 형식을 판단한다.
- 컨텍스트당 **한 번만** 성공한다. 두 번째 호출은 `RuntimeError`.
- `id`가 `null`이면 형식 검사 후 송신만 생략한다 (`replied`는 `True`가 됨).
- 아무 스레드에서 호출 가능 (큐잉, 다음 업데이트에 전달).
- **이 익스텐션 shutdown 이후 호출은 조용히 무시된다** (예외 없음, 형식 검사도 안 함).

기타:
- `owner`는 키워드 전용 인자이며(위치 인자로 주면 `TypeError`), 등록하는 익스텐션 ID(`morph.<domain>.<name>`)를 넣는다.
- 이 익스텐션이 shutdown된 뒤 예전 `Messaging` 인스턴스에 `register_inbound` / `register_outbound`를 호출하면 `RuntimeError`. 인스턴스를 캐시하지 말고 필요할 때 `get_messaging()`으로 다시 얻는다 (`send`·`ctx.reply*`는 무시, `unsubscribe`는 안전).
- 너무 깊게 중첩된 payload: 수신 시 `bad_envelope`(JSON 해석 단계) 또는 `invalid_payload`(스키마 단계), 송신·회신 시 `ValueError`.
- `schema`는 JSON Schema 부분집합: `type`(object/array/string/number/integer/boolean/null, 목록 가능), `required`, `properties`, `items`, `enum`, `additionalProperties`(bool만). 그 외 키워드는 무시. 잘못된 스키마는 등록 시 `ValueError`.
  - 키워드별 적용 대상 (JSON Schema와 같음): `type`·`enum`은 모든 값에 적용된다. **`required`·`properties`·`additionalProperties`는 값이 object일 때만 적용되고, 그 외 값(배열·문자열·숫자·`null` 등)에는 무시된다.** `items`는 값이 배열일 때만 적용된다.
  - 따라서 `type` 없이 `required`만 둔 스키마(예: `{ "required": ["x"] }`)는 object가 아닌 값을 통과시킨다. 그 값이 반드시 object여야 하면 `"type": "object"`를 함께 적는다 (최상위 `payload`는 봉투 규칙상 항상 object라 해당 없음. 중첩 `properties` 값에서 중요).
  - `properties`의 하위 스키마는 그 키가 있을 때만 검사한다 (필수 여부는 `required`로). `{}` 스키마는 모든 값을 통과시킨다.
- 핸들러 전 실패(봉투 형식, 미등록 type, 미지원 `v`, 스키마)는 핸들러에 오지 않고 `morph.stream.invalid_message`로 발행되며, 웹에 `morph.stream.messaging.error`가 같은 id로 회신된다.
- 핸들러 예외는 격리된다: 다른 핸들러·연결에 영향 없음, 에러 로그(트레이스백 포함) + `handler_error` 회신(위 규칙). `invalid_message` 이벤트는 내지 않는다.
- 수신 처리 중 예상치 못한 내부 오류도 그 메시지 하나로 격리된다: 에러 로그 + `invalid_message`(`type: ""`, `error: "internal_error"`) + `morph.stream.messaging.error`(`id: null`). 같은 프레임의 나머지 수신과 송신은 그대로 처리된다.

## 이벤트
### 발행
| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `morph.stream.client_connected` | `{ "client_id": str }` | 웹 클라이언트 연결 (`morph.stream.messaging.hello` 수신 또는 연결 이벤트 설정). 같은 client_id 중복 시 재발행 안 함 |
| `morph.stream.client_disconnected` | `{ "client_id": str }` | 연결 종료 (`morph.stream.messaging.bye` 수신, 연결 이벤트 설정, 이 익스텐션 shutdown) |
| `morph.stream.invalid_message` | `{ "type": str, "error": str, "reason": str }` | 핸들러 전 수신 실패. `type`은 해석 불가 시 `""`, `error`는 위 실패 code, `reason`은 설명(= 웹 회신의 `message`) |

모두 메인 업데이트 루프에서 발행된다. (이벤트 이름은 이벤트 규칙 `morph.<domain>.<event>`를 따르며 웹 메시지 type 규칙과 무관하다.)

### 수신
| 이벤트 | 소유 익스텐션 | 반응 |
|---|---|---|

## 웹 메시지
자체 소유 타입 (owner `morph.stream.messaging`):
| 방향 | 메시지 타입 | 페이로드 | 매핑 |
|---|---|---|---|
| Kit→웹 | `morph.stream.messaging.error` | `{ "ok": false, "error": str, "message": str, "type": str }` | 핸들러 전 실패 회신 (위 "실패 code" 표의 앞 5개). `id` = 원 요청 id (모르면 `null`), `type` = 원 요청 type (해석 불가 시 `""`), `v` = 1 |
| 웹→Kit | `morph.stream.messaging.hello` | `{ "client_id"?: str }` | 연결 핸드셰이크 → `client_connected`. 같은 type·id로 `{ "ok": true, "client_id": str, "protocol": 2 }` 회신 (client_id 생략 시 생성. `id: null`이면 회신 없음) |
| 웹→Kit | `morph.stream.messaging.bye` | `{ "client_id"?: str }` | 연결 종료 → `client_disconnected` (client_id 생략 시 전체). 같은 type·id로 `{ "ok": true }` 회신 (`id: null`이면 회신 없음) |

`protocol`은 핸드셰이크 프로토콜 번호다: 2 = 이 CONTRACT 1.x 규칙 (type 이름 규칙, 회신 규칙).

### 전송 형식 (웹 클라이언트 구현자용)
livestream 메시징 위에서 봉투는 **JSON 문자열**로 두 채널을 오간다. 다른 익스텐션은 이 채널 이벤트를 관찰·발행하지 않는다 (내부 전송 계층).
- 웹→Kit: `{ "event_type": "morph.stream.in", "payload": { "message": "<봉투 JSON 문자열>" } }`
- Kit→웹: `{ "event_type": "morph.stream.out", "payload": { "message": "<봉투 JSON 문자열>" } }`
- 웹→Kit은 `message`에 봉투를 JSON 객체로 실어도 되고, `payload` 자체를 봉투로 보내도 받는다. Kit→웹은 항상 JSON 문자열이다 (`id: null` 보존).
- 앱에 `omni.kit.livestream.messaging`이 없으면 웹 전송은 일어나지 않지만 등록·라우팅·검증 API는 그대로 동작한다.

## 설정
| 경로 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `/exts/morph.stream.messaging/validate` | bool | true | 스키마 검증 사용 (봉투 형식·회신 형식 검사는 항상) |
| `/exts/morph.stream.messaging/maxQueue` | int | 1024 | 송신·수신 큐 최대 길이 (초과 시 오래된 메시지부터 버리고 경고. 연결 상태 항목은 버리지 않음) |
| `/exts/morph.stream.messaging/connectEvents` | list[str] 또는 str | (없음) | 발생 시 `client_connected`로 매핑할 carb 이벤트 이름 (livestream 연결 이벤트 연결용). 문자열 하나면 이름 하나로 취급. 빈 문자열은 무시 |
| `/exts/morph.stream.messaging/disconnectEvents` | list[str] 또는 str | (없음) | 발생 시 `client_disconnected`로 매핑할 carb 이벤트 이름. 형식은 `connectEvents`와 같음 |

설정은 startup 시 한 번 읽는다.

## 사용 예
`config/extension.toml`:
```toml
[dependencies]
"morph.stream.messaging" = {}
```

Python (Kit 익스텐션). `EXT_ID`를 자기 익스텐션 ID로 바꾸면 그대로 동작한다:
```python
import omni.ext
from morph.stream.messaging import get_messaging

EXT_ID = "morph.foo.bar"
MSG_STATE = f"{EXT_ID}.state"        # Kit→웹 푸시
MSG_SET_VALUE = f"{EXT_ID}.set_value"  # 웹→Kit 요청

VALUE_SCHEMA = {"type": "object", "required": ["value"], "properties": {"value": {"type": "number"}}}


class MyExt(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._value = 0.0
        msg = get_messaging()
        self._subs = [
            msg.register_outbound(MSG_STATE, schema=VALUE_SCHEMA, owner=EXT_ID),
            msg.register_inbound(MSG_SET_VALUE, self._on_set_value, schema=VALUE_SCHEMA, owner=EXT_ID),
        ]

    def _on_set_value(self, payload, ctx):
        # 메인 업데이트 루프에서 호출된다. 무거운 작업은 asyncio 태스크로 넘기고 ctx를 보관했다가 회신한다.
        value = payload["value"]
        if value < 0:
            ctx.reply_error("invalid_argument", f"value must be >= 0, got {value}")
            return
        self._value = value
        ctx.reply_ok(value=self._value)  # type MSG_SET_VALUE, id = 요청 id → { ok: true, value }
        get_messaging().send(MSG_STATE, {"value": self._value})
        # 여기서 예외가 나면 stream이 { ok: false, error: "handler_error", message: "<Class>: <msg>" } 회신

    def on_shutdown(self):
        for sub in self._subs:
            sub.unsubscribe()
        self._subs = []
```

연결 상태 구독 (carb.eventdispatcher):
```python
from carb.eventdispatcher import get_eventdispatcher

self._conn_sub = get_eventdispatcher().observe_event(
    observer_name="morph.foo.bar:client_connected",
    event_name="morph.stream.client_connected",
    on_event=lambda e: print("web client", e["client_id"]),
)
```

웹 클라이언트 측 (`@nvidia/omniverse-webrtc-streaming-library`의 `AppStreamer`):
```js
import { AppStreamer } from "@nvidia/omniverse-webrtc-streaming-library";

const STREAM_ERROR = "morph.stream.messaging.error";
const pending = new Map();   // id -> { resolve, reject, timer }

export class MorphRequestError extends Error {
  constructor(payload) {
    super(payload.message);
    this.code = payload.error;    // "invalid_argument", "handler_error", "unknown_type", ...
    this.payload = payload;
  }
}

export function send(envelope) {
  AppStreamer.sendMessage(JSON.stringify({
    event_type: "morph.stream.in",
    payload: { message: JSON.stringify(envelope) },
  }));
}

// 성공이면 회신 payload({ ok: true, ... })로 resolve, 실패면 MorphRequestError로 reject.
// 핸들러가 회신하지 않는 경우에 대비해 타임아웃을 둔다.
export function request(type, payload = {}, { v = 1, timeoutMs = 5000 } = {}) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new MorphRequestError({ ok: false, error: "timeout", message: `${type} timed out` }));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    send({ type, v, id, payload });
  });
}

// 회신이 필요 없는 메시지 (id: null → Kit은 같은 type으로 회신하지 않음)
export function notify(type, payload = {}, v = 1) {
  send({ type, v, id: null, payload });
}

// AppStreamer.connect({ ..., onCustomEvent: onCustomEvent })
export function onCustomEvent(raw) {
  const msg = typeof raw === "string" ? JSON.parse(raw) : raw;
  if (msg.event_type !== "morph.stream.out") return;
  const env = JSON.parse(msg.payload.message);   // { type, v, id, payload }
  const p = env.id && pending.get(env.id);
  if (p) {
    pending.delete(env.id);
    clearTimeout(p.timer);
    env.payload.ok === false ? p.reject(new MorphRequestError(env.payload)) : p.resolve(env.payload);
    return;
  }
  if (env.type === STREAM_ERROR) {   // id 없는 메시지의 핸들러 전 실패
    console.warn("morph stream error", env.payload.error, env.payload.type, env.payload.message);
    return;
  }
  // 상태 푸시 등: env.type 별로 처리
  if (env.type === "morph.foo.bar.state") console.log("state", env.payload.value);
}

// 스트림 연결 직후 / 종료 직전
const hello = await request("morph.stream.messaging.hello", {});   // → { ok: true, client_id, protocol: 2 }
// ...
try {
  const r = await request("morph.foo.bar.set_value", { value: 3 });   // → { ok: true, value: 3 }
} catch (e) {
  if (e.code === "invalid_argument") console.warn(e.message);
}
notify("morph.stream.messaging.bye", { client_id: hello.client_id });
```

## 금지 / 주의
- 핸들러 안에서 오래 걸리는 작업 금지 (메인 루프 지연). 무거운 작업은 비동기 태스크로 넘기고 `ctx`를 보관했다가 회신한다.
- 다른 익스텐션 ID 접두어로 타입 등록 금지 (`ValueError`).
- 요청 type을 `register_outbound`로도 선언하지 않는다 (회신은 `ctx.reply_*`로. 푸시는 별도 type, 예: `.state`).
- `morph.stream.in` / `morph.stream.out` 채널 이벤트를 직접 관찰·발행하지 않는다.
- `Subscription`을 버리지 말고 보관했다가 `on_shutdown`에서 `unsubscribe()` 한다 (해제하지 않으면 재활성화 시 inbound 중복 등록 `ValueError`).
- **연결 상태의 한계**: 기본 설정(`connectEvents`/`disconnectEvents` 없음)에서는 연결 상태를 `morph.stream.messaging.hello`/`bye`로만 안다. 브라우저가 `bye` 없이 닫히면 `client_disconnected`가 오지 않고 `is_connected`가 `True`로 남는다. `is_connected`는 "웹으로 보낼 가치가 있는지" 정도의 힌트로만 쓴다.
- hello의 `client_id`와 livestream 연결 이벤트의 client_id는 서로 다를 수 있다. 두 방식(핸드셰이크와 연결 이벤트 설정)을 섞으면 연결·끊김이 서로 매칭되지 않아 한쪽 항목이 남을 수 있다. 앱에서는 한 방식만 쓴다.

## 호환성 정책 (stable)
- 이 익스텐션은 모든 웹 연동 익스텐션의 기반이다. 1.x 동안 봉투 형식, type 이름 규칙, 회신 규칙, 위 실패 code의 의미, 공개 API 시그니처는 깨지 않는다.
- minor: API·설정·이벤트 페이로드 필드 추가, 실패 code 추가 (웹은 모르는 code를 일반 실패로 처리한다). patch: 내부 수정.
- major 변경은 메인 세션에 먼저 제안한 뒤에만 한다.

## 변경 이력
- 1.0.1 — 문서만 (동작 변경 없음). 스키마 부분집합에서 `required`·`properties`·`additionalProperties`는 object 값에만, `items`는 배열 값에만 적용되고 그 외 값에는 무시됨을 명시. 이 동작을 고정하는 단위 테스트 추가.
- 1.0.0 — **호환 깨짐 (stable 확정)**.
  - type 소유 단위를 익스텐션으로: type은 `<owner ext-id>.<name>` (`<name>` = `[a-z][a-z0-9_]*`, 점 금지). 0.x의 domain 접두어 형식(`morph.<domain>.<x>`)은 `ValueError`.
  - 자체 타입 이름 변경: `morph.stream.error` → `morph.stream.messaging.error`, `morph.stream.hello` → `morph.stream.messaging.hello`, `morph.stream.bye` → `morph.stream.messaging.bye`. hello 회신은 `{ ok: true, client_id, protocol: 2 }`, bye도 `{ ok: true }` 회신.
  - 회신 규칙 통일: 요청 회신 payload는 `{ ok: true, ... }` / `{ ok: false, error: code, message, ... }`. `MessageContext.reply`가 이 형식을 강제(`ValueError`)하고 컨텍스트당 1회만 허용(`RuntimeError`). `MessageContext.reply_ok` / `reply_error` / `replied` 추가.
  - 핸들러 예외 회신이 `morph.stream.error`(`reason: "handler error: <Class>"`) 대신 같은 type·id의 `{ ok: false, error: "handler_error", message: "<Class>: <msg>" }`. 이미 회신했으면 생략.
  - `id: null` 요청에는 같은 type으로 회신하지 않음 (0.x는 `id: null` 회신을 보냈음). `morph.stream.messaging.error`는 계속 보냄.
  - `morph.stream.messaging.error` payload: `{ type, reason }` → `{ ok: false, error, message, type }` (실패 code 표).
  - 회신 봉투 `v`는 inbound 등록 버전 (0.x는 같은 type의 outbound 선언이 있으면 그 버전·스키마를 썼음). 회신은 outbound 스키마로 검증하지 않음.
  - (호환) `morph.stream.invalid_message` 페이로드에 `error` code 추가. shutdown 이후 `ctx.reply*` 무시 명시. `register_outbound` 재선언 시 이전 `Subscription` 무효 명시. `connectEvents`/`disconnectEvents`가 문자열 하나여도 이름 하나로 처리 (0.2.0은 글자 단위로 쪼개던 버그). 호환성 정책 추가.
- 0.2.0 — 기본 구현. (기능 추가. 0.1.0 사용 예와 호환되며, 단 `owner`는 키워드 전용이라 위치 인자로는 쓸 수 없음 — 0.1.0 표기 시그니처는 파이썬에서 성립하지 않았음) `register_outbound`가 `Subscription` 반환, 다른 owner 소유 type의 outbound 선언은 `ValueError`, `version` 키워드 인자, `Messaging.is_connected`, `MessageContext.type`/`.v`, `reply` 규칙(같은 type·id) 명시, 웹 메시지 `morph.stream.hello`/`morph.stream.bye`, 전송 형식(`morph.stream.in`/`out` 채널, 웹→Kit 객체 형태 허용), 설정 `connectEvents`/`disconnectEvents`, 핸들러 예외·내부 오류 격리와 `morph.stream.error` 회신, 깊은 중첩 처리, 큐 overflow 시 연결 항목 보존, shutdown 이후 `register_*`는 `RuntimeError`, 이벤트 상수 공개.
- 0.1.0 — 최초 초안
