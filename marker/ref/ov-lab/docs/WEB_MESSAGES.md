# 웹 메시지 레퍼런스 (웹 클라이언트 구현자용)

`morph.lab_streaming.kit` 앱이 웹 클라이언트와 주고받는 메시지 전체 목록. 메인 세션이 관리한다.
**원본은 각 익스텐션의 CONTRACT.md**다. 이 문서는 그걸 한곳에 모은 요약이고, 서로 다르면 CONTRACT가 맞다.

| 익스텐션 | CONTRACT 버전 | 원본 |
|---|---|---|
| morph.stream.messaging | 1.0.1 (stable) | [CONTRACT](../source/extensions/morph.stream.messaging/CONTRACT.md) "메시지 봉투", "회신 규칙", "웹 메시지", "전송 형식" |
| morph.perf.monitor | 1.1.0 (stable) | [CONTRACT](../source/extensions/morph.perf.monitor/CONTRACT.md) "웹 메시지" |
| morph.camera.control | 1.1.0 (stable) | [CONTRACT](../source/extensions/morph.camera.control/CONTRACT.md) "웹 메시지" |
| morph.viewport.split | 1.5.0 (stable) | [CONTRACT](../source/extensions/morph.viewport.split/CONTRACT.md) "웹 메시지" |
| morph.stage.loader | 0.1.2 (draft) | [CONTRACT](../source/extensions/morph.stage.loader/CONTRACT.md) "웹 메시지" |
| morph.annotation.overlay | 0.4.0 (draft) | [CONTRACT](../source/extensions/morph.annotation.overlay/CONTRACT.md) "주석 모델", "웹 메시지" |

## 1. 전송 형식
livestream 메시징(`omni.kit.livestream.messaging`) 위에서 채널 두 개만 쓴다. 봉투는 JSON 문자열로 `payload.message`에 싣는다.

```jsonc
// 웹 → Kit
{ "event_type": "morph.stream.in",  "payload": { "message": "<봉투 JSON 문자열>" } }
// Kit → 웹
{ "event_type": "morph.stream.out", "payload": { "message": "<봉투 JSON 문자열>" } }
```
- 웹→Kit은 `message`에 봉투를 객체로 실어도 되고, `payload` 자체를 봉투로 보내도 된다.
- Kit→웹은 항상 JSON 문자열이다.

## 2. 봉투와 type 이름
```json
{ "type": "morph.camera.control.orbit", "v": 1, "id": "req-1", "payload": {} }
```
| 필드 | 규칙 |
|---|---|
| `type` | `<소유 익스텐션 ID>.<name>` (항상 점 4개 세그먼트, `<name>`은 `[a-z][a-z0-9_]*`). 마지막 점 앞이 소유 익스텐션 |
| `v` | 스키마 버전 (현재 전부 `1`). 생략하면 1. 회신의 `v`는 Kit에 등록된 버전 |
| `id` | 요청/응답 상관 ID (`string` 또는 `null`). **`null`이면 회신이 없다** (알림용) |
| `payload` | 항상 객체. 생략하면 `{}` |

## 3. 회신 규칙 (모든 요청 공통)
`id`가 있는 요청에는 **같은 type·같은 id로 정확히 한 번** 회신이 온다.

```jsonc
{ "ok": true,  ... }                                          // 성공 (추가 필드는 type별)
{ "ok": false, "error": "<code>", "message": "<설명>", ... }   // 실패
```
| `error` code | 의미 | 회신 type |
|---|---|---|
| `invalid_argument` | 값이 잘못됨 | 요청과 같은 type |
| `not_found` | 대상(view_id, 프리셋 등)이 없음 | 요청과 같은 type |
| `failed_precondition` | 지금 상태로는 불가 (열린 스테이지 없음, 조작 대상 뷰 없음 등) | 요청과 같은 type |
| `unavailable` | 기능이 준비되지 않음 / 종료 중 | 요청과 같은 type |
| `handler_error` | Kit 쪽 예상 밖 예외 (`message` = `"<예외클래스>: <메시지>"`) | 요청과 같은 type |
| `bad_envelope`, `unknown_type`, `unsupported_version`, `invalid_payload`, `internal_error` | 핸들러까지 가지 못한 실패 | **`morph.stream.messaging.error`** (payload에 원 `type` 포함, id는 원 요청 id 또는 `null`) |

웹 쪽은 `payload.ok === false` 하나로 모든 실패를 처리하면 된다. 요청 type은 Kit이 자동으로 성공 회신하지 않으므로 타임아웃을 두는 게 좋다.

## 4. 연결
1. 연결 직후 `morph.stream.messaging.hello`를 보낸다. 이걸 보내기 전에는 Kit이 **푸시(`*.state`, `*.report`)를 보내지 않는다**.
2. 닫을 때 `morph.stream.messaging.bye`를 보낸다. 보내지 않고 닫히면 Kit은 계속 연결된 것으로 본다 (푸시 낭비만 있고 오류는 없음).

| 방향 | type | payload | 회신 |
|---|---|---|---|
| 웹→Kit | `morph.stream.messaging.hello` | `{ "client_id"?: string }` | `{ ok: true, client_id, protocol: 2 }` (client_id 생략 시 생성) |
| 웹→Kit | `morph.stream.messaging.bye` | `{ "client_id"?: string }` | `{ ok: true }` (client_id 생략 시 전체 끊김) |
| Kit→웹 | `morph.stream.messaging.error` | `{ ok: false, error, message, type }` | 위 3장 표 참고 |

## 5. 카메라 (`morph.camera.control.*`)
조작 대상은 항상 **포커스 뷰**의 카메라다 (포커스는 6장 `focus_view`로 바꾼다). 대상 뷰가 없으면 `failed_precondition`.

| 방향 | type | payload | 성공 회신 / 실패 |
|---|---|---|---|
| 웹→Kit | `morph.camera.control.orbit` | `{ "d_yaw": number, "d_pitch": number }` (deg) | `{ok:true}` / `invalid_argument` |
| 웹→Kit | `morph.camera.control.pan` | `{ "dx": number, "dy": number }` | `{ok:true}` / `invalid_argument` |
| 웹→Kit | `morph.camera.control.zoom` | `{ "factor": number }` (`<1` 다가감, `>1` 멀어짐, `<=0`은 오류) | `{ok:true}` / `invalid_argument` |
| 웹→Kit | `morph.camera.control.apply_preset` | `{ "name": string, "duration_s"?: number }` | `{ok:true, name}` / `not_found` (`name` 포함) |
| 웹→Kit | `morph.camera.control.save_preset` | `{ "name": string }` (1~64자) | `{ok:true, name}` (포커스 뷰 현재 포즈, 같은 이름 덮어씀) / `invalid_argument`, `resource_exhausted`(64개 초과), `failed_precondition` (1.1.0) |
| 웹→Kit | `morph.camera.control.list_presets` | `{}` | `{ ok: true, presets: [{ name, eye, target, fov }] }` 이름순 (1.1.0) |
| 웹→Kit | `morph.camera.control.delete_preset` | `{ "name": string }` | `{ok:true, name}` / `not_found` (1.1.0) |
| Kit→웹 | `morph.camera.control.state` | `{ "camera_path": string, "eye": [x,y,z], "target": [x,y,z], "fov": number }` | 푸시. 바뀔 때 최대 `stateRateHz`(기본 30)회/초 |

드래그처럼 자주 보내는 orbit/pan/zoom은 `id: null`로 보내면 회신 없이 처리된다. 프리셋은 세션 메모리 전역이고 목록 변경 푸시는 없다 (저장·삭제 후 `list_presets` 다시 요청).

## 6. 분할 화면 (`morph.viewport.split.*`)
`View` = `{ "view_id": int, "viewport_name": string, "camera_path": string, "rect": Rect | null }`.
- `Rect` = `{ "x", "y", "w", "h" }`: 앱 창(=스트림 프레임) 기준 0..1, 원점 좌상단 (1.2.0). 창이 없거나 전환 중이면 `null`. 분할선을 끌어 크기가 바뀌면 `state`가 다시 푸시된다.
- 배치: 1x2 = 0 왼쪽·1 오른쪽, 2x1 = 0 위·1 아래, 2x2 = 0 왼쪽 위·1 오른쪽 위·2 왼쪽 아래·3 오른쪽 아래, 1+3 = 0 왼쪽 크게(폭 1−sideRatio, 기본 75%)·1/2/3 오른쪽 열 위→아래 (1.3.0).
- 레이아웃 문자열을 "행x열"로 해석하지 않는다 (`1+3`처럼 다른 모양이 있다). 화면 위치는 `rect`를 쓴다.
- 영상 위 클릭 좌표를 `rect`로 판정하면 어느 뷰인지 알 수 있다 (`<video>` object-fit 여백 보정 필요. 구현 예: tools/webclient `hitView`).

| 방향 | type | payload | 성공 회신 / 실패 |
|---|---|---|---|
| 웹→Kit | `morph.viewport.split.set_layout` | `{ "layout": "1x1" \| "1x2" \| "2x1" \| "2x2" \| "1+3" }` | `{ok:true}`는 논리 전환 직후. 창 준비 완료는 `state` 푸시로 확인 |
| 웹→Kit | `morph.viewport.split.set_view_camera` | `{ "view_id": int, "camera_path": string }` | `{ok:true}` / `not_found`(view_id), `invalid_argument`(Camera 아닌 prim), `failed_precondition`(스테이지 없음) |
| 웹→Kit | `morph.viewport.split.focus_view` | `{ "view_id": int }` | `{ok:true}` / `not_found` |
| 웹→Kit | `morph.viewport.split.list_cameras` | `{}` | `{ ok: true, cameras: [{ path, name, builtin }] }` 사용자 카메라(경로순) 다음 Kit 기본(Persp/Top/Front/Right). 목록의 경로는 `set_view_camera`가 받는다. 푸시 없음 / `failed_precondition`(스테이지 없음) (1.4.0) |
| 웹→Kit | `morph.viewport.split.set_side_ratio` | `{ "ratio": number }` | `{ ok: true, side_ratio }` 1+3 오른쪽 열 폭(0.1..0.5로 자른 적용값). 1+3이면 창을 다시 만들지 않고 바로 재도킹, 다른 레이아웃이면 다음 1+3에 쓰인다 / `invalid_argument`(숫자 아님·bool·NaN·inf) (1.5.0) |
| 웹→Kit | `morph.viewport.split.get_state` | `{}` | `{ ok: true, layout, focused_view_id, side_ratio, views }` |
| Kit→웹 | `morph.viewport.split.state` | `{ "layout": string, "focused_view_id": int, "side_ratio": number, "views": [View, ...] }` | 푸시. hello 직후 1회, 이후 레이아웃·포커스·뷰 카메라·side_ratio가 바뀐 프레임 끝에 1회 (`side_ratio` 1.5.0) |

## 7. 성능 (`morph.perf.monitor.*`)
| 방향 | type | payload | 성공 회신 / 실패 |
|---|---|---|---|
| 웹→Kit | `morph.perf.monitor.set_enabled` | `{ "enabled": bool }` | `{ ok: true, enabled }` (적용 후 상태) / `invalid_argument` |
| Kit→웹 | `morph.perf.monitor.report` | `{ "period_s": number, "frame_ms": Stat, "metrics": { name: Stat } }` | 푸시. 앱 설정 `exts."morph.perf.monitor".webReport = true` 일 때만 (`morph.lab_streaming.kit`은 켜져 있음), `periodSec`(기본 1초)마다 |

`Stat` = `{ "kind": string, "count": int, "avg": number, "p95": number, "max": number, "value": number }`
- `metrics`에는 시스템 지표(gauge)도 실린다 (1.1.0): `morph.perf.monitor.sys.mem_working_set_mb`, `.sys.mem_private_mb`, `.sys.collect_ms`, `.gpu.mem_used_mb`, `.gpu.mem_budget_mb` (MB = MiB, gpu는 앱에 `omni.hydra.engine.stats`가 있을 때). 현재 값은 `value`. 정의는 perf CONTRACT "시스템 지표".

## 7-1. 장면 (`morph.stage.loader.*`, 0.1 draft)
목록에 있는 장면 id(`"<루트 이름>/<상대 경로>"`, 예 `samples/morph_demo.usda`)로만 연다. 임의 경로는 열 수 없다. 모든 요청은 종료 중이면 `unavailable`.
열기 요청은 **열기가 끝난 뒤** 회신이 온다 (수 초~수십 초, 클라이언트 시간 제한을 길게). 그 사이 `state` 푸시(`loading: true`)가 먼저 온다.
장면이 바뀌면 split·camera 상태도 바뀌므로 카메라 목록(`split.list_cameras`)을 다시 받는다.

| 방향 | type | payload | 성공 회신 / 실패 |
|---|---|---|---|
| 웹→Kit | `morph.stage.loader.list_scenes` | `{ "refresh"?: boolean }` (true면 폴더 다시 스캔) | `{ ok: true, scenes: [{ id, name, root, size_bytes }], current_scene_id }` |
| 웹→Kit | `morph.stage.loader.open_scene` | `{ "id": string }` | 열기 완료 후 `{ ok: true, scene: { id, name, root, size_bytes } }` / `not_found`, `invalid_argument`, `failed_precondition`(다른 열기 진행 중), `open_failed`, `unavailable`(종료 중) |
| 웹→Kit | `morph.stage.loader.reload` | `{}` | 열기 완료 후 `{ ok: true, scene_id }` (저장 안 한 변경은 버림) / `failed_precondition`(파일 장면 아님·진행 중), `open_failed` |
| 웹→Kit | `morph.stage.loader.new_scene` | `{}` | `{ ok: true }` / `failed_precondition`, `open_failed` |
| 웹→Kit | `morph.stage.loader.get_state` | `{}` | `{ ok: true, scene_id, name, loading }` |
| Kit→웹 | `morph.stage.loader.state` | `{ "scene_id": string \| null, "name": string \| null, "loading": boolean }` | 푸시. 열기 시작·끝·실패·외부 열기 때, 연결 시 1회 |

## 7-2. 주석 (`morph.annotation.overlay.*`, 0.4.0 draft)
모델 위 표시(라벨, inlet/outlet 화살표, 절단면 위치)는 **Kit 뷰포트 안에 그려져 영상으로 온다**. 웹은 데이터만 다룬다. 주석은 USD prim이 아니며 월드 좌표로 정의된다.
주석 = `{ id, kind: "label"|"inlet"|"outlet"|"plane", label, description, color?, visible, ... }` — kind별 필드(`position`, `direction`, `size`, `origin`, `normal`)는 CONTRACT "주석 모델". 장면을 열면 장면 옆 `<이름>.annotations.json`을 자동으로 읽는다. 모든 요청은 종료 중이면 `unavailable`. 실패 `message`의 경로는 파일 이름만 담긴다.
`save`는 현재 장면 옆 파일을 덮어쓴다. 내장 샘플(`samples/...`)은 저장소 원본과 연결돼 있어 저장하면 원본 JSON이 바뀐다.

| 방향 | type | payload | 성공 회신 / 실패 |
|---|---|---|---|
| 웹→Kit | `morph.annotation.overlay.list` | `{}` | `{ ok: true, annotations, visible, file, show_description, text_scale, selected_id, face }` (`file`은 파일 이름만) |
| 웹→Kit | `morph.annotation.overlay.add` | `{ "annotation": object }` | `{ ok: true, id }` / `invalid_argument`, `resource_exhausted` |
| 웹→Kit | `morph.annotation.overlay.update` | `{ "id": string, "fields": object }` | `{ ok: true, annotation }` / `not_found`, `invalid_argument` |
| 웹→Kit | `morph.annotation.overlay.remove` | `{ "id": string }` | `{ ok: true, id }` / `not_found` |
| 웹→Kit | `morph.annotation.overlay.set_visible` | `{ "visible": boolean, "id"?: string }` | `{ ok: true }` / `not_found`, `invalid_argument` |
| 웹→Kit | `morph.annotation.overlay.save` | `{}` | `{ ok: true, file }` (현재 장면 사이드카에만) / `failed_precondition`(파일 장면 아님), `io_error`(쓰기 실패) |
| 웹→Kit | `morph.annotation.overlay.reload` | `{}` | `{ ok: true, count }` / `failed_precondition`(파일 장면 아님, 또는 JSON 깨짐·형식 오류·version 초과 — 이때 기존 목록 유지), `io_error`(읽기 실패) |
| 웹→Kit | `morph.annotation.overlay.set_options` | `{ "show_description"?: boolean, "text_scale"?: number }` (0.2.0) | `{ ok: true, show_description, text_scale }` 화면 표시 옵션 (설명 글 표시, 글자 배율 0.25..4) / `invalid_argument` |
| 웹→Kit | `morph.annotation.overlay.pick` | `{ "viewport_name": string, "x": number, "y": number }` (그 뷰포트 렌더 이미지 안 0..1, 원점 왼쪽 위. 분할 뷰면 클릭한 칸 안 좌표 ÷ 칸 크기, `viewport_name`은 split state의 뷰 창 이름) (0.3.0) | 쿼리 후 `{ ok: true, hit: true, position, normal }`(`normal` null 가능) 또는 `{ ok: true, hit: false }`. 위치를 얻을 뿐 주석을 만들지 않는다 / `not_found`(뷰포트 없음), `invalid_argument`, `unavailable`(렌더 해상도 없음·쿼리 거부·5초 시간 초과·종료 중) |
| 웹→Kit | `morph.annotation.overlay.select` | `{ "id": string \| null }` (0.3.0) | `{ ok: true, selected_id }` 선택 주석은 뷰에서 강조 / `not_found` |
| 웹→Kit | `morph.annotation.overlay.pick_face` | `{ "viewport_name": string, "x": number, "y": number }` (좌표는 `pick`과 같음) (0.4.0) | 쿼리 후 `{ ok: true, hit: true, position, face }` 또는 `{ ok: true, hit: false }`. hit이면 그 면을 선택(빗나가면 선택 해제). `face` = `{ kind: "planar"\|"cylinder_side"\|"sphere"\|"polygon"\|"point", center, normal, area, size: [w, h], outline: [[x,y,z], …] }` — 클릭한 점이 놓인 평면 영역 전체(예: 플랜지 끝 원판)의 면적 가중 중심·바깥쪽 법선. prim 경로는 담지 않는다 / `pick`과 같은 실패 code |
| 웹→Kit | `morph.annotation.overlay.clear_face` | `{}` (0.4.0) | `{ ok: true }` |
| Kit→웹 | `morph.annotation.overlay.state` | `{ annotations, visible, file, show_description, text_scale, selected_id, face }` | 푸시. 바뀔 때·장면 열기/닫기 때(프레임당 최대 1회로 합침), 연결 시 1회. 미연결 중 변경은 버리고 연결 때 최신 상태 |

## 8. 최소 클라이언트 예 (JS)
`streamClient`는 livestream 웹 SDK의 메시지 송수신 객체 자리다 (SDK에 맞게 바꾼다). 더 완전한 헬퍼는 stream CONTRACT "사용 예" 참고.

```js
const pending = new Map();
function send(env) {
  streamClient.sendMessage(JSON.stringify({ event_type: "morph.stream.in", payload: { message: JSON.stringify(env) } }));
}
function notify(type, payload = {}) { send({ type, v: 1, id: null, payload }); }   // 회신 없음
function request(type, payload = {}, timeoutMs = 5000) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { pending.delete(id); reject(new Error(`timeout: ${type}`)); }, timeoutMs);
    pending.set(id, (p) => { clearTimeout(t); p.ok ? resolve(p) : reject(Object.assign(new Error(p.message), p)); });
    send({ type, v: 1, id, payload });
  });
}
function onStreamMessage(raw) {               // livestream 수신 콜백에 연결
  const msg = typeof raw === "string" ? JSON.parse(raw) : raw;
  if (msg.event_type !== "morph.stream.out") return;
  const env = JSON.parse(msg.payload.message);
  if (env.id && pending.has(env.id)) { pending.get(env.id)(env.payload); pending.delete(env.id); return; }
  if (env.type === "morph.camera.control.state") { /* env.payload.eye ... */ }
  if (env.type === "morph.viewport.split.state") { /* env.payload.layout ... */ }
}

await request("morph.stream.messaging.hello");
await request("morph.viewport.split.set_layout", { layout: "2x2" });
await request("morph.viewport.split.focus_view", { view_id: 1 });
notify("morph.camera.control.orbit", { d_yaw: 15, d_pitch: 0 });
try { await request("morph.camera.control.apply_preset", { name: "overview" }); }
catch (e) { if (e.error === "not_found") console.warn("no preset", e.name); }
```

실제 브라우저로 확인하는 테스트 페이지는 [tools/webclient](../tools/webclient/README.md) (마우스 조작·성능 표시, self test 21항목). Kit 쪽에서 이 흐름을 흉내 낸 검증은 `python tools/kit.py smoke`(`tools/smoke_app.py`)에 있다.
