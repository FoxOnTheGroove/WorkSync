# morph.camera.control CONTRACT

> 다른 익스텐션 에이전트가 이 익스텐션을 쓸 때 읽는 **유일한** 문서. 여기 없는 것은 내부 구현이므로 의존하지 않는다.

- 버전: 1.1.1
- 상태: stable
- 담당 에이전트: ext-camera
- 의존: morph.stream.messaging, morph.perf.monitor
- 의존 버전 (`extension.toml` 범위): morph.stream.messaging `1.0` = ^1.0 (type 이름 규칙·회신 규칙·`ctx.reply_ok`/`reply_error` 사용), morph.perf.monitor `1.0` = ^1.0 (`scope`/`counter`만 사용)
- 이벤트만 사용 (의존 선언 없음): morph.viewport.split의 `morph.viewport.focus_changed` (없는 앱에서도 동작)

## 목적
뷰포트 카메라 제어의 단일 창구. 포커스 뷰 카메라의 오빗/팬/줌/look_at(보간), 프리셋 저장·적용·목록·삭제, 포커스 뷰 카메라 전환을 제공하고 변경을 이벤트와 웹 메시지로 알린다.

## 언제 쓰나 / 쓰지 않나
- 쓴다: 코드나 웹 입력으로 카메라를 움직이거나, 포커스 뷰 카메라 상태를 알아야 할 때
- 쓰지 않는다: 카메라 prim의 xform을 직접 쓰는 것 (금지, 상태 이벤트·보간이 깨짐)
- 쓰지 않는다: 포커스 뷰가 **아닌** 뷰에 카메라 prim을 연결하는 것 (→ `morph.viewport.split`의 `set_view_camera`)
- 쓰지 않는다: 렌즈(시야각·초점거리) 변경. 이 익스텐션은 fov를 읽기만 한다.

## 조작 대상 (포커스 뷰)
- **조작 대상 = 포커스 뷰 = Kit 활성 뷰포트.** startup 시 `omni.kit.viewport.utility.get_active_viewport()`로 잡는다. 그때 뷰포트가 없으면(헤드리스·테스트, 뷰포트 창이 아직 안 뜸) "대상 없음"으로 시작하고, 대상이 생길 때까지 약 0.5초마다 다시 찾는다.
- 대상은 다음 네 경우에만 바뀐다:
  1. `morph.viewport.focus_changed`(소유 morph.viewport.split) 수신 → `get_viewport_from_window_name(viewport_name)`. `viewport_name`이 `""`이거나 그 이름의 뷰포트를 찾을 수 없으면 무시하고 현재 대상을 유지한다.
  2. `set_active_camera` 호출 시 Kit 활성 뷰포트를 그 자리에서 다시 확인해, 현재 대상과 다르면 먼저 대상을 바꾼다 (대상이 없을 때도 여기서 한 번 찾는다). 이 전환은 이후 검사가 실패해 예외가 나도 유지된다.
  3. 대상이 없을 때의 주기적 재탐색 (위).
  4. 대상 뷰포트가 무효해졌을 때의 활성 뷰포트 재확인 (아래).
- **분할화면(morph.viewport.split)이 없는 앱에서는** startup(또는 최초 획득) 시점의 활성 뷰포트를 계속 대상으로 쓴다. Kit 활성 뷰포트가 바뀌어도 매 프레임 따라가지 않는다 (위 2의 `set_active_camera` 호출 시점 재확인만 한다). 의존 선언 없이 이벤트만 쓰므로 split이 있든 없든 그대로 동작한다.
- **큐잉 명령의 대상 확정 시점**: orbit/pan/zoom/look_at/apply_preset은 반영 프레임의 대상에 적용된다. split의 `focus_view` 직후에는 `focus_changed`가 다음 업데이트에서 오므로, 같은 프레임에 넣은 큐잉 명령은 대상 전환과 함께 취소된다 (아래). 포커스를 바꾼 뒤 움직이려면 `morph.camera.active_changed` 또는 `morph.viewport.focus_changed`를 받은 뒤 명령을 넣는다.
- **대상이 바뀌면(포커스 전환, `set_active_camera`, 외부에서 포커스 뷰 카메라가 바뀜) 진행 중인 보간과 아직 반영되지 않은 명령(orbit/pan/zoom/look_at/apply_preset)은 모두 취소된다.** 스테이지가 열리거나 닫힐 때도 같은 이유로 취소된다.
- 대상 창이 닫혀 뷰포트가 무효가 되면(접근 시 예외 또는 카메라 경로가 빔. 예: 레이아웃 축소 후 `focus_changed` 도착 전) 그 자리에서 Kit 활성 뷰포트를 한 번 다시 확인한다. 무효해진 뷰포트가 아닌 유효한 활성 뷰포트가 있으면 곧바로 그쪽으로 대상을 바꾼다 (대기 명령·보간 취소, `active_changed`는 새 카메라 경로로 1회. 같은 카메라면 발행 안 함). 그런 뷰포트가 없을 때만 "대상 없음"으로 처리하고 `active_changed`를 `camera_path: ""`로 낸다. 어느 경우든 예외나 에러 로그는 없다. 이어서 오는 `focus_changed`가 같은 뷰포트를 가리키면 무시된다.
- `get_state()`가 `None`인 경우: 대상(포커스 뷰포트)이 없을 때, 대상 뷰포트에 열린 스테이지가 없을 때, 카메라 경로가 비었거나 그 prim을 읽을 수 없을 때(없음·Xformable 아님). 이때 큐에 들어온 명령은 다음 프레임에 버려진다.

## 단위와 방향
- 좌표: 월드 좌표, 스테이지 단위(`metersPerUnit` 환산 없음), 스테이지 up 축 기준 (Y-up/Z-up 모두 지원).
- `orbit(d_yaw, d_pitch)`: **도(deg)**. target이 중심이고 target은 움직이지 않는다.
  - `d_yaw > 0`: up 축 기준 오른손 회전 (up 축 위에서 내려다볼 때 반시계). 카메라 입장에서는 **카메라가 화면 오른쪽 방향으로 돌아간다** (장면은 화면에서 왼쪽으로 흐른다).
  - `d_pitch > 0`: **카메라가 위로** 올라간다 (아래를 더 내려다봄). 고도각(수평면 기준 eye 방향)은 ±89°에서 멈춘다.
  - 설정 `orbitSpeed` 배율이 곱해진다.
- `pan(dx, dy)`: **월드 단위** (픽셀 아님, 거리로 스케일하지 않음). eye·target을 함께 옮긴다.
  - `dx > 0`: 카메라가 화면 오른쪽으로 (장면은 왼쪽으로). `dy > 0`: 카메라가 화면 위쪽으로 (장면은 아래로). 화면 축은 현재 카메라 기준 right / up.
  - 픽셀 드래그를 맞추려면 target 거리에서의 화면 폭 `2 · |eye − target| · tan(fov/2)`(fov = 수평 시야각)을 뷰 폭 픽셀로 나눈 값을 곱한다 (아래 JS 사용 예).
- `zoom(factor)`: target까지 거리 × `factor` (배율, 단위 없음). **`factor < 1`이면 다가가고 `> 1`이면 멀어진다.** target은 그대로, 최소 거리 1e-4.
- `duration_s`: 초. `CameraState.fov`: 수평 시야각(deg).

## 명령 반영 모델
- `orbit`/`pan`/`zoom`/`look_at`/`apply_preset`은 큐에 넣고 즉시 반환한다 (아무 스레드에서 호출 가능, 인자 검증은 호출 시점). USD 쓰기는 다음 업데이트 프레임에 메인 스레드에서 **프레임당 한 번** 한다.
- 같은 프레임의 상대 명령은 병합한다: orbit는 각도 합산, pan은 이동량 합산, zoom은 배율 곱. 적용 순서는 orbit → pan → zoom.
- 절대 명령(`look_at`, `apply_preset`)은 그보다 먼저 큐에 있던 상대 명령을 버린다. 그 뒤에 온 상대 명령은 절대 명령 위에 적용된다. 같은 프레임에 절대 명령이 여럿이면 마지막 것만 반영된다.
- `duration_s > 0`이면 현재 포즈에서 목표까지 smoothstep 이징으로 보간한다. target은 직선으로, eye는 target 기준 방향을 구면 보간하고 거리를 선형 보간한다 (eye 경로가 target을 관통하지 않음).
- 보간과 상대 명령: **같은 프레임에서** 보간 `look_at`/`apply_preset` 뒤에 온 상대 명령은 보간 **목표 포즈**에 적용된다 (보간은 그대로 진행). **이전 프레임에 시작된** 보간 중에 상대 명령이 오면 보간을 멈추고 그 시점 포즈에서 이어서 움직인다. 새 절대 명령은 진행 중인 보간을 대체한다.
- 외부(예: Kit 마우스 내비게이션)에서 카메라가 움직여도 매 프레임 다시 읽으므로 상태·이벤트에 반영된다.

## 공개 Python API
import: `from morph.camera.control import get_camera_controller, CameraController, CameraState, EVENT_CHANGED, EVENT_ACTIVE_CHANGED`

`Vec3` = `tuple[float, float, float]` (월드 좌표, 스테이지 up 축 기준. Y-up/Z-up 모두 지원). 단위·부호는 위 "단위와 방향".

| 심볼 | 시그니처 | 설명 | 주의 |
|---|---|---|---|
| `get_camera_controller` | `() -> CameraController \| None` | 싱글턴 | startup 이후 유효, shutdown 이후 `None`. 캐시하지 말고 필요할 때 다시 얻는다 |
| `CameraController.orbit` | `(d_yaw: float, d_pitch: float) -> None` | target 중심 회전 (deg). `d_yaw > 0` = 카메라가 화면 오른쪽으로 돎(up 축 위에서 볼 때 반시계), `d_pitch > 0` = 카메라가 위로 | 큐잉 (위 "명령 반영 모델"). 설정 `orbitSpeed` 배율 적용. 고도각은 ±89°에서 멈춘다. 유한한 수가 아니면 `ValueError` |
| `CameraController.pan` | `(dx: float, dy: float) -> None` | 화면 기준 평행 이동 (**월드 단위**). `dx > 0` = 카메라가 화면 오른쪽, `dy > 0` = 화면 위쪽. eye·target 함께 이동 | 큐잉. 유한한 수가 아니면 `ValueError` |
| `CameraController.zoom` | `(factor: float) -> None` | target까지 거리 × `factor` (`< 1` = 다가감, `> 1` = 멀어짐) | 큐잉. `factor > 0`이고 유한해야 함, 아니면 `ValueError`. 최소 거리 1e-4 |
| `CameraController.look_at` | `(eye: Vec3, target: Vec3, duration_s: float = 0.0) -> None` | 지정 포즈로 이동 | 큐잉. `duration_s > 0`이면 보간 (음수는 0). 길이 3 유한 수가 아니거나 eye == target이면 `ValueError` |
| `CameraController.save_preset` | `(name: str) -> None` | 현재 포커스 뷰 카메라의 eye/target/fov를 이름으로 저장 (같은 이름은 덮어씀) | **메인 스레드.** 빈 이름 `ValueError`, 대상 없음 `RuntimeError` |
| `CameraController.apply_preset` | `(name: str, duration_s: float = 0.0) -> None` | 프리셋의 eye/target으로 `look_at` | 큐잉. 없는 이름은 `KeyError`. fov는 적용하지 않는다 |
| `CameraController.delete_preset` | `(name: str) -> None` | 프리셋 삭제 | **메인 스레드** (save_preset과 같음). 없는 이름(str 아닌 값 포함) `KeyError`. 이미 큐에 들어간 `apply_preset`·진행 중인 보간에는 영향 없음 (값은 큐잉 시점에 복사됨). 대상이 없어도 동작 |
| `CameraController.list_presets` | `() -> list[str]` | 저장된 프리셋 이름 목록 | **이름순: Python `sorted()` = 유니코드 코드 포인트 오름차순, 대소문자 구분** (`"A" < "a" < "b"`). 매번 새 list. 아무 스레드 가능 (읽기). 대상이 없어도 동작 |
| `CameraController.get_preset` | `(name: str) -> dict \| None` | 프리셋 내용 `{ "name": str, "eye": [x,y,z], "target": [x,y,z], "fov": float }` (웹 `list_presets` 항목과 같은 형태, JSON 직렬화 가능) | 없는 이름(str 아닌 값 포함)은 `None`. 매번 새 dict (바꿔도 저장본 무관). 아무 스레드 가능 (읽기). `fov`는 저장 시점 값(참고용, `apply_preset`은 적용 안 함) |
| `CameraController.set_active_camera` | `(prim_path: str) -> None` | **포커스 뷰**의 `camera_path`를 바꾼다 | **메인 스레드.** 호출 시점의 Kit 활성 뷰포트를 다시 확인해 대상으로 삼는다 (같은 프레임의 split `focus_view` 결과 반영). 즉시 반영. 빈 문자열·prim 없음·Camera prim 아님 `ValueError`, 포커스 뷰나 스테이지가 없음·대상 뷰포트가 무효 `RuntimeError`. `active_changed` 발행 규칙은 아래 별도 문단. 이후 morph.viewport.split(로드돼 있으면)도 `morph.viewport.view_camera_changed`를 발행한다 |
| `CameraController.get_state` | `() -> CameraState \| None` | 포커스 뷰 카메라 상태 (마지막으로 반영된 프레임 기준) | 대상 없음·열린 스테이지 없음·카메라를 읽을 수 없음이면 `None` (위 "조작 대상"). 큐에 있는 명령은 아직 반영 전 |
| `CameraController.viewport_name` | 프로퍼티 `-> str` | 조작 대상(포커스 뷰) 뷰포트 창 이름 | 대상이 없거나 이름을 모르면 `""`. `active_changed`의 `viewport_name`과 달리 같은 카메라를 보는 뷰로 포커스만 바뀌어도 즉시 새 이름이 된다 |
| `CameraState` | frozen dataclass: `.camera_path: str`, `.eye: Vec3`, `.target: Vec3`, `.fov: float`, `.to_payload() -> dict` | 상태 스냅샷. `fov` = 수평 시야각(deg). `target` = Kit `omni:kit:centerOfInterest` 기준 | `to_payload()`는 `morph.camera.changed` 페이로드와 같은 JSON 직렬화 가능 dict |
| `EVENT_CHANGED`, `EVENT_ACTIVE_CHANGED` | `str` 상수 | 아래 발행 이벤트 이름 | 문자열을 직접 써도 된다 |

프리셋은 **전역**이다 (카메라 prim별 아님). eye/target/fov만 저장하고, 적용 대상은 적용 시점의 포커스 뷰 카메라다. 세션 메모리에만 있고 파일·스테이지에 저장하지 않는다. Python API와 웹 메시지는 같은 저장소를 쓴다. Python API에는 이름 길이·개수 상한이 없다 (상한은 웹 `save_preset`에만, 아래 "웹 메시지"). 프리셋 목록 변경은 이벤트·웹 푸시로 알리지 않는다 (필요하면 다시 조회).

`set_active_camera`의 `active_changed` 발행 규칙:
- 한 호출에 **최대 1회**, 동기 발행 (반환 전). 페이로드는 호출이 끝난 시점의 대상 기준 (`camera_path` = 최종 카메라 경로, `viewport_name` = 최종 대상 창 이름).
- 호출 전 제어하던 카메라 경로와 최종 경로가 같으면 발행하지 않는다. 대상 전환 없이 포커스 뷰가 이미 `prim_path`를 보고 있으면 발행도, 대기 명령·보간 취소도 없다 (대상 전환이 있었으면 경로가 같아도 취소는 된다).
- 활성 뷰포트 재확인으로 대상이 바뀐 뒤 카메라까지 바뀌어도 (예: A 카메라를 보던 뷰 → B를 보는 뷰로 전환 → C로 변경) 중간 경로 B로는 발행하지 않고 C로 1회만 발행한다.
- 대상이 바뀐 뒤 검사가 실패해 예외가 나면 카메라 연결은 바꾸지 않지만 대상 전환은 유지되므로, 새 대상의 카메라 경로가 이전과 다르면 그 경로로 1회 발행된 뒤 예외가 난다.

공개 이름은 위 표가 전부다. `CameraController`의 밑줄 접두어 메서드(`_update` 등)는 이 익스텐션 내부 배선용이며, 다른 익스텐션이 부르면 같은 프레임에 명령이 이중 반영되는 등 오동작한다. `CameraController`를 직접 생성하지 말고 `get_camera_controller()`를 쓴다.

## 이벤트
이름 규칙 `morph.<domain>.<event>`, `carb.eventdispatcher` 사용. 모두 메인 스레드에서 발행된다.

### 발행
| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `morph.camera.changed` | `{ "camera_path": str, "eye": [x,y,z], "target": [x,y,z], "fov": float }` | 포커스 뷰 카메라 상태가 바뀐 프레임 끝 (프레임당 최대 1회). 이 익스텐션의 명령, 외부 이동, 대상 전환 모두 포함 |
| `morph.camera.active_changed` | `{ "camera_path": str, "viewport_name": str }` | 제어 대상 카메라 경로가 **바뀔 때만**: `set_active_camera`(호출당 최대 1회, 위 규칙), 다른 카메라를 보는 뷰로 포커스 전환, 외부에서 포커스 뷰 카메라 변경, 대상 최초 획득, 대상 상실(`camera_path: ""`). 경로가 같으면 발행하지 않는다 (같은 카메라를 보는 다른 뷰로 포커스만 바뀐 경우 포함 — 그때는 `viewport_name` 프로퍼티만 바뀐다). `viewport_name`은 발행 시점 대상 뷰포트 창 이름 (모르면 `""`) |

### 수신
| 이벤트 | 소유 익스텐션 | 반응 |
|---|---|---|
| `morph.stream.client_connected` | morph.stream.messaging | 현재 상태를 웹에 `morph.camera.control.state`로 1회 송신 (`stateRateHz` 제한 무관, 대상이 없으면 송신 안 함) |
| `morph.viewport.focus_changed` | morph.viewport.split | 조작 대상을 `get_viewport_from_window_name(viewport_name)`으로 바꾸고 보간·대기 명령 취소 (`viewport_name == ""`이면 무시). 의존 선언 없이 이벤트만 사용 |

## 웹 메시지 (morph.stream.messaging 1.x 경유)
owner `morph.camera.control`. 모든 타입 `v = 1`. 봉투·스키마 위반은 핸들러에 오지 않고 stream이 `morph.stream.messaging.error`(`invalid_payload` 등)로 회신한다. 단위·부호는 Python API와 같다 (위 "단위와 방향").

| 방향 | 메시지 타입 | 페이로드 | 매핑 / 성공 회신 |
|---|---|---|---|
| 웹→Kit | `morph.camera.control.orbit` | `{ "d_yaw": number, "d_pitch": number }` (deg) | `orbit`. `{ "ok": true }` |
| 웹→Kit | `morph.camera.control.pan` | `{ "dx": number, "dy": number }` (월드 단위) | `pan`. `{ "ok": true }` |
| 웹→Kit | `morph.camera.control.zoom` | `{ "factor": number }` (`< 1` = 다가감) | `zoom`. `{ "ok": true }` |
| 웹→Kit | `morph.camera.control.apply_preset` | `{ "name": string, "duration_s"?: number }` (초, 기본 0) | `apply_preset`. `{ "ok": true, "name": str }` |
| 웹→Kit | `morph.camera.control.save_preset` | `{ "name": string }` | `save_preset` (현재 반영된 포즈, 같은 이름은 덮어씀). `{ "ok": true, "name": str }`. 즉시 저장됨 (큐잉 아님). 저장 포즈는 마지막으로 반영된 프레임 기준이라 아직 반영 전인 orbit/pan/zoom 등은 포함되지 않는다 |
| 웹→Kit | `morph.camera.control.list_presets` | `{}` | `{ "ok": true, "presets": [{ "name": str, "eye": [x,y,z], "target": [x,y,z], "fov": number }, ...] }` — 전체 목록, `list_presets()`와 같은 이름순. 없으면 `[]`. 실패 없음 |
| 웹→Kit | `morph.camera.control.delete_preset` | `{ "name": string }` | `delete_preset`. `{ "ok": true, "name": str }` |
| Kit→웹 | `morph.camera.control.state` | `morph.camera.changed` 페이로드와 동일: `{ "camera_path": str, "eye": [x,y,z], "target": [x,y,z], "fov": number }` | 푸시 (`id: null`). 아래 "상태 푸시" |

회신 (stream 회신 규칙: 같은 type·같은 id, `id`가 문자열인 요청마다 정확히 한 번):
| 실패 code | 대상 타입 | 의미 | 추가 필드 |
|---|---|---|---|
| `invalid_argument` | orbit, pan, zoom, apply_preset, save_preset | 값이 유한한 수가 아님, `factor <= 0`, `duration_s`가 유한하지 않음. save_preset: 이름이 빈 문자열이거나 **64자 초과** (유니코드 코드 포인트 기준. JS에서는 `[...name].length`. `"x".repeat(64)`는 허용) | apply_preset은 `name`. save_preset은 `name`(빈 이름이면 `""`, 64자 초과면 앞 64자) + 길이 초과 시 `limit: 64` |
| `not_found` | apply_preset, delete_preset | 그런 이름의 프리셋 없음 (`message: "unknown preset"`) | `name` |
| `resource_exhausted` | save_preset | 새 이름인데 저장된 프리셋이 이미 **64개** 이상 (Python API로 저장한 것 포함). 기존 이름 덮어쓰기는 상한과 무관하게 허용. 지우고 다시 저장한다 | `name`, `limit: 64` |
| `failed_precondition` | orbit, pan, zoom, apply_preset, save_preset | 조작할 카메라가 없음 (`get_state()`가 `None`인 조건과 같음: 포커스 뷰 없음, 스테이지 없음, 카메라를 읽을 수 없음). 명령은 큐에 넣지 않는다 / 저장하지 않는다 | apply_preset·save_preset은 `name` |

- 실패 판정 순서: `invalid_argument` → `not_found` / `resource_exhausted` → `failed_precondition`. 하나라도 실패하면 명령은 큐에 들어가지 않는다 (save_preset은 저장하지 않는다).
- `list_presets`·`delete_preset`은 카메라가 없어도 동작한다 (`failed_precondition` 없음).
- 이름 길이·개수 상한(각 64)은 1.x 동안 고정이며 웹 `save_preset`에만 적용된다. `list_presets`는 상한과 무관하게 전체를 돌려준다 (Python으로 64개 넘게 저장했을 수 있음).
- `resource_exhausted`는 stream 권장 code 목록 밖에서 이 익스텐션이 추가한 code다 (stream 회신 규칙상 허용).
- 프리셋 목록이 바뀌어도 푸시는 없다. 저장·삭제 뒤 목록이 필요하면 `list_presets`를 다시 요청한다 (다른 클라이언트·Python 쪽 변경도 같음).
- orbit/pan/zoom/apply_preset의 `ok: true`는 **접수**(큐잉)를 뜻한다 (save_preset/list_presets/delete_preset은 회신 시점에 이미 완료). 실제 반영은 다음 업데이트 프레임이며, 그 사이 대상이 바뀌면(포커스 전환 등) 위 "조작 대상" 규칙대로 취소될 수 있다. 결과 포즈는 `morph.camera.control.state`로 확인한다.
- 드래그처럼 빈도가 높은 orbit/pan/zoom은 `id: null`(회신 없음)로 보내는 것을 권장한다. 같은 프레임에 온 것은 병합된다. 이 경우 실패도 웹에 알려지지 않는다 (stream 회신 규칙).
- 핸들러에서 잡지 않은 예외는 stream이 `handler_error`로 회신한다 (정상 동작에서는 나지 않음).

상태 푸시 (`morph.camera.control.state`):
- **웹 클라이언트가 연결된 동안에만** 보낸다 (기본 설정에서는 `morph.stream.messaging.hello` 이후 ~ `bye` 전. 앱이 stream `connectEvents`를 설정했으면 그 연결 이벤트 기준). hello 전에는 오지 않는다.
- 연결(`morph.stream.client_connected`) 직후 현재 상태를 1회 보낸다 (대상이 없으면 보내지 않고, 이후 대상이 생겨 상태가 바뀌면 보낸다). hello 회신과의 도착 순서는 보장하지 않는다.
- 이후 상태가 바뀔 때마다 최대 `stateRateHz`로 보낸다 (제한에 걸린 변경은 다음 가능 시점에 최신 상태로).
- 대상을 잃어도(`camera_path: ""`) 상태 푸시는 오지 않는다. 웹이 대상 상실을 알아야 하면 요청의 `failed_precondition`으로 판단한다.

## 설정
| 경로 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `/exts/morph.camera.control/orbitSpeed` | float | 1.0 | 오빗 각도 배율 (실행 중 변경 반영) |
| `/exts/morph.camera.control/stateRateHz` | float | 30.0 | 웹 상태 송신 최대 빈도. `<= 0`이면 제한 없음 (실행 중 변경 반영) |

## 성능 측정 (morph.perf.monitor)
| 지표 | 종류 | 의미 |
|---|---|---|
| `morph.camera.control.update_ms` | scope | 프레임당 업데이트(대상 동기화, 병합·보간, USD 쓰기, 이벤트) 시간 |
| `morph.camera.control.commands` | counter | 대상이 있는 프레임에서 처리된 명령 수 (병합 전 개수. 같은 프레임의 절대 명령에 밀려 버려진 상대 명령 포함, 대상 없음·대상 전환으로 버려진 명령 제외) |
| `morph.camera.control.state_sends` | counter | 웹 `morph.camera.control.state` 송신 수 |

## 사용 예
`config/extension.toml`:
```toml
[dependencies]
"morph.camera.control" = {}
```

Python (Kit 익스텐션). `EXT_ID`를 자기 익스텐션 ID로 바꾸면 그대로 동작한다:
```python
import omni.ext
from carb.eventdispatcher import get_eventdispatcher
from morph.camera.control import EVENT_ACTIVE_CHANGED, EVENT_CHANGED, get_camera_controller

EXT_ID = "morph.foo.bar"


class MyExt(omni.ext.IExt):
    def on_startup(self, ext_id):
        ed = get_eventdispatcher()
        self._cam_subs = [
            ed.observe_event(
                observer_name=f"{EXT_ID}:camera_changed",
                event_name=EVENT_CHANGED,              # "morph.camera.changed"
                on_event=self._on_camera_changed,
            ),
            ed.observe_event(
                observer_name=f"{EXT_ID}:camera_active_changed",
                event_name=EVENT_ACTIVE_CHANGED,       # "morph.camera.active_changed"
                on_event=self._on_active_changed,
            ),
        ]

    def _on_camera_changed(self, e):
        # 프레임마다 올 수 있다. 무거운 처리는 주기 제한한다.
        eye, target, fov = list(e["eye"]), list(e["target"]), e["fov"]

    def _on_active_changed(self, e):
        if not e["camera_path"]:
            return  # 대상 상실 (포커스 뷰 없음)
        print("now controlling", e["camera_path"], "in", e["viewport_name"])

    # 아래는 메인 스레드(UI 콜백, 업데이트 루프 등)에서 호출한다.
    def show_overview(self):
        cam = get_camera_controller()  # 캐시하지 않는다
        if cam is None or cam.get_state() is None:  # 포커스 뷰·카메라 없음
            return
        cam.save_preset("before_overview")  # 현재(반영된) 포즈 저장, 전역
        cam.look_at(eye=(10.0, 5.0, 10.0), target=(0.0, 0.0, 0.0), duration_s=0.5)  # 다음 프레임부터 0.5초 보간

    def nudge(self):
        cam = get_camera_controller()
        if cam is None:
            return
        cam.orbit(15.0, 0.0)  # 카메라가 target 둘레를 화면 오른쪽으로 15° (큐잉, 아무 스레드 가능)
        cam.zoom(0.8)         # 거리 × 0.8 = 다가감. 같은 프레임 명령은 병합

    def restore(self):
        cam = get_camera_controller()
        if cam is None:
            return
        try:
            cam.apply_preset("before_overview", duration_s=0.3)  # 지금 포커스 뷰 카메라에 적용
        except KeyError:
            pass  # 저장한 적 없음

    def dump_presets(self):
        cam = get_camera_controller()
        if cam is None:
            return
        for name in cam.list_presets():      # 이름순 (코드 포인트 오름차순)
            p = cam.get_preset(name)         # {"name", "eye", "target", "fov"} 또는 None
            if p is not None:
                print(name, p["eye"], p["target"], p["fov"])

    def forget(self, name="before_overview"):
        cam = get_camera_controller()       # 메인 스레드에서
        if cam is None:
            return
        try:
            cam.delete_preset(name)
        except KeyError:
            pass  # 없는 이름

    def use_camera(self, prim_path="/World/Camera"):
        cam = get_camera_controller()
        if cam is None:
            return
        try:
            cam.set_active_camera(prim_path)  # 포커스 뷰의 카메라 연결 변경. active_changed 최대 1회
        except ValueError as e:
            print("not a camera:", e)
        except RuntimeError as e:
            print("no focus viewport / stage:", e)

    def on_shutdown(self):
        self._cam_subs = []
```

웹 클라이언트. morph.stream.messaging CONTRACT "사용 예"의 JS 헬퍼(`request`, `notify`, `onCustomEvent`, `MorphRequestError`)를 `./morph-stream.js`로 저장해 두고 쓴다. 그 `onCustomEvent`의 상태 푸시 분기에 `if (env.type === CAM_STATE) onCameraState(env.payload);` 한 줄을 넣는다.
```js
import { request, notify } from "./morph-stream.js";

const CAM = "morph.camera.control";
export const CAM_STATE = `${CAM}.state`;
let camState = null;   // { camera_path, eye, target, fov } — hello 이후 첫 푸시 전에는 null

export function onCameraState(payload) {
  camState = payload;
}

// 드래그 입력은 빈도가 높으므로 notify(id: null, 회신 없음). Kit이 프레임당 1회로 병합한다.
// 그랩 방식: 장면이 마우스를 따라 움직인다. dxPx/dyPx = 마우스 이동량(px, 브라우저 y는 아래가 +).
export function orbitDrag(dxPx, dyPx, degPerPx = 0.3) {
  // d_yaw > 0 = 카메라가 화면 오른쪽으로 돎(장면은 왼쪽), d_pitch > 0 = 카메라가 위로
  notify(`${CAM}.orbit`, { d_yaw: -dxPx * degPerPx, d_pitch: dyPx * degPerPx });
}

export function panDrag(dxPx, dyPx, viewWidthPx) {
  if (!camState) return;
  const [ex, ey, ez] = camState.eye;
  const [tx, ty, tz] = camState.target;
  const dist = Math.hypot(ex - tx, ey - ty, ez - tz);
  const worldPerPx = (2 * dist * Math.tan((camState.fov * Math.PI) / 360)) / viewWidthPx;  // fov = 수평 시야각(deg)
  // dx > 0 = 카메라가 화면 오른쪽(장면은 왼쪽), dy > 0 = 카메라가 화면 위쪽 (월드 단위)
  notify(`${CAM}.pan`, { dx: -dxPx * worldPerPx, dy: dyPx * worldPerPx });
}

export function wheelZoom(deltaY) {
  // factor < 1 = 다가감. 휠을 위로(deltaY < 0) 굴리면 다가간다.
  notify(`${CAM}.zoom`, { factor: Math.exp(deltaY * 0.001) });
}

// 결과가 필요한 요청은 request (성공 { ok: true, name }, 실패는 MorphRequestError로 reject)
export async function goToPreset(name, durationS = 0.5) {
  try {
    await request(`${CAM}.apply_preset`, { name, duration_s: durationS });
    return true;
  } catch (e) {
    if (e.code === "not_found") console.warn("no such preset:", e.payload.name);
    else if (e.code === "failed_precondition") console.warn("no camera to control:", e.message);
    else throw e;   // invalid_argument, invalid_payload, handler_error, timeout ...
    return false;
  }
}

// 프리셋 관리. 목록 변경 푸시는 없으므로 저장·삭제 후 listPresets()를 다시 부른다.
export const PRESET_NAME_MAX = 64;   // 코드 포인트 기준 ([...name].length)
export const PRESET_COUNT_MAX = 64;

export async function listPresets() {
  // [{ name, eye: [x,y,z], target: [x,y,z], fov }] — 이름순(코드 포인트 오름차순)
  const res = await request(`${CAM}.list_presets`, {});
  return res.presets;
}

export async function savePreset(name) {
  if (!name || [...name].length > PRESET_NAME_MAX) throw new Error(`preset name must be 1..${PRESET_NAME_MAX} chars`);
  try {
    await request(`${CAM}.save_preset`, { name });   // 현재 포즈 저장 (같은 이름은 덮어씀)
    return true;
  } catch (e) {
    if (e.code === "resource_exhausted") console.warn(`too many presets (max ${e.payload.limit}); delete one first`);
    else if (e.code === "failed_precondition") console.warn("no camera to save:", e.message);
    else throw e;   // invalid_argument, invalid_payload, handler_error, timeout ...
    return false;
  }
}

export async function deletePreset(name) {
  try {
    await request(`${CAM}.delete_preset`, { name });
    return true;
  } catch (e) {
    if (e.code === "not_found") return false;   // 이미 없음
    throw e;
  }
}

// 예: 저장 후 목록 갱신
// if (await savePreset("front")) renderPresetList(await listPresets());
```

## 금지 / 주의
- 카메라 prim xform 직접 수정 금지. 이 API로만 움직인다.
- `morph.camera.changed`는 프레임마다 올 수 있다. 구독 측에서 무거운 처리는 주기 제한한다.
- 조작 대상은 항상 포커스 뷰다. 특정 뷰의 카메라를 움직이려면 먼저 그 뷰에 포커스를 준다 (`morph.viewport.split`의 `focus_view`). 포커스가 바뀌면 진행 중인 보간·대기 명령은 취소된다.
- `set_active_camera`는 포커스 뷰의 카메라 연결을 바꾼다. 분할화면 앱에서는 이어서 `morph.viewport.view_camera_changed`(소유 morph.viewport.split)도 발행되므로, 두 이벤트를 모두 구독하는 쪽은 중복 처리에 주의한다.
- 카메라 쓰기는 기존 xform op 형식(translate + rotate 계열, 필요 시 scale)을 유지하고, 호환되지 않으면 단일 `xformOp:transform`으로 바꾼다. 부모에 scale이 있으면 카메라 local scale로 상쇄해 카메라 월드 행렬이 정규 직교가 되게 쓴다. undo 기록은 남기지 않는다.
- 쓰는 레이어: Kit 세션 카메라(`/OmniverseKit_*`)는 세션 레이어. 그 외 카메라는 `xformOpOrder`의 가장 강한 의견이 세션 레이어에 있을 때만 세션 레이어, 아니면 스테이지의 현재 편집 대상 (사용자 카메라에 세션 over를 만들어 Kit 마우스 내비게이션 쓰기를 가리지 않기 위해).

- 밑줄 접두어 이름(`CameraController._update` 등)이나 `morph.camera.control.controller`/`web` 등 하위 모듈을 쓰지 않는다. 공개 import는 위 "공개 Python API"의 import 줄뿐이다.

## 호환성 정책 (stable)
- 1.x 동안 깨지 않는 것: 공개 Python API 시그니처와 예외 종류, 단위·부호("단위와 방향"), 명령 반영 모델, 이벤트 이름·페이로드 필드와 발행 조건, 웹 메시지 type·`v`·페이로드 필드, 회신 형식과 실패 code의 의미, 설정 경로와 의미, perf 지표 이름, 프리셋 이름 정렬 규칙, 웹 프리셋 상한(이름 64자·64개).
- minor: API·설정·이벤트/웹 페이로드 필드 추가, 웹 메시지 type 추가, 실패 code 추가 (웹은 모르는 code를 일반 실패로 처리한다). patch: 내부 수정·버그 수정.
- 호환 깨지는 웹 페이로드 변경은 같은 type의 `v`를 올리고 major로 한다. major 변경은 메인 세션에 먼저 제안한 뒤에만 한다.
- 의존: morph.stream.messaging 1.x(≥1.0), morph.perf.monitor 1.x(≥1.0)에 맞춘다. perf API 변경은 이 익스텐션 안에서 흡수하며 위 계약에는 영향을 주지 않는다. morph.viewport.split의 `morph.viewport.focus_changed` 이름·`viewport_name` 필드에 기댄다 (이벤트만, 의존 선언 없음).

## 변경 이력
- 1.1.1 — 내부 수정 (공개 동작 변화 없음). `extension.toml` 의존에 버전 범위 명시: `morph.stream.messaging` `1.0`(^1.0), `morph.perf.monitor` `1.0`(^1.0). 헤더 의존 버전 표기 갱신 (perf는 stable 1.x).
- 1.1.0 — 기능 추가 (호환 유지). 웹에서 프리셋을 저장·조회·삭제할 수 있게 함.
  - Python API: `CameraController.delete_preset(name)` (메인 스레드, 없으면 `KeyError`), `list_presets() -> list[str]` (코드 포인트 오름차순), `get_preset(name) -> dict | None` (`{name, eye, target, fov}`).
  - 웹 메시지: `morph.camera.control.save_preset` / `list_presets` / `delete_preset`. 웹 `save_preset` 상한: 이름 1~64자(코드 포인트, 넘으면 `invalid_argument` + `limit`), 프리셋 64개(새 이름이면 `resource_exhausted` + `limit`, 덮어쓰기는 허용). 새 실패 code `resource_exhausted`. delete의 없는 이름은 `not_found`.
  - 프리셋 목록 변경 푸시는 없음 (다시 조회). 기존 type·회신·Python 동작 변화 없음 (Python `save_preset`에는 상한 없음).
- 1.0.0 — **호환 깨짐 (stable 확정, morph.stream.messaging 1.0 대응)**.
  - 웹 type 이름 변경 (stream 1.x type 규칙 `<owner ext-id>.<name>`): `morph.camera.orbit` / `pan` / `zoom` / `apply_preset` / `state` → `morph.camera.control.orbit` / `pan` / `zoom` / `apply_preset` / `state`. carb 이벤트 이름(`morph.camera.changed`, `morph.camera.active_changed`)은 그대로.
  - 웹 회신 통일 (stream 1.x 회신 규칙): orbit/pan/zoom도 성공 시 `{ ok: true }` 회신 (0.x는 성공 시 회신 없음). 실패는 `{ ok: false, error: code, message }` — 잘못된 값 `invalid_argument` (0.x는 `error`에 설명 문자열), 없는 프리셋 `not_found` + `name` (0.x는 `error: "unknown preset"`), 조작할 카메라가 없으면 `failed_precondition` (0.x는 `ok: true`를 주고 명령을 조용히 버렸음). 실패 판정 순서와 "ok = 접수" 의미 명시.
  - 내부 메서드 비공개화: `CameraController.update` / `set_target_viewport` / `on_focus_changed` / `on_stage_opened` / `on_client_connected` / `shutdown`은 CONTRACT에 없던 이름이며 밑줄 접두어로 바뀌었다 (다른 익스텐션이 `update()`를 부르면 이중 반영됐음).
  - `set_active_camera`: `active_changed`를 호출당 최대 1회, 최종 경로로 발행 (0.2.0은 활성 뷰포트 재확인으로 대상이 바뀌면 중간 경로로 한 번 더 발행할 수 있었음). 같은 경로면 미발행·대기 명령 유지, 실패 시에도 대상 전환은 유지됨을 명시. 무효해진 대상 뷰포트에 쓰려 하면 `RuntimeError`.
  - (호환) `CameraController.viewport_name` 공개 프로퍼티. "단위와 방향" 절 (deg·월드 단위·zoom 배율 방향·pan 픽셀 환산). 상태 푸시는 웹 클라이언트 연결(hello) 이후에만, 대상 상실 시 푸시 없음 명시. 의존 버전 표기, 호환성 정책 추가. 사용 예(Python IExt 전체·JS 드래그/휠/프리셋)를 새 규칙으로 교체.
- 0.2.0 — 분할화면 대응 + 기본 구현. 조작 대상 = 포커스 뷰(Kit 활성 뷰포트) 명시, `morph.viewport.focus_changed` 수신(의존 선언 없음), 대상 전환 시 보간·대기 명령 취소. `set_active_camera`를 "포커스 뷰 카메라 변경"으로 정의하고 예외(`ValueError`/`RuntimeError`) 명시. `active_changed`가 포커스 전환·외부 변경·대상 획득/상실에도 발행되고 페이로드에 `viewport_name` 추가. 프리셋은 전역(eye/target/fov 저장, fov는 미적용). **호환 주의**: `get_state()`가 대상이 없으면 `None`을 반환 (0.1.0은 항상 `CameraState`). 명령 병합·보간 규칙, 입력 검증 `ValueError`, 웹 메시지 회신 규칙, `CameraState.to_payload`, 이벤트 이름 상수, perf 지표, 설정 `stateRateHz <= 0` 의미 추가. (리뷰 반영, 수용 전 0.2.0 유지) 대상 변경 경로 4가지와 split 없는 앱 동작 명시, `set_active_camera`가 호출 시점 Kit 활성 뷰포트를 재확인(대상 없으면 탐색), 큐잉 명령의 대상 확정 시점 명시, 스테이지 열림/닫힘 시 취소, 무효 뷰포트는 먼저 Kit 활성 뷰포트로 전환을 시도하고 없을 때만 대상 없음 처리(예외·에러 로그 없음, 레이아웃 축소 시 일시적인 `camera_path: ""` 방지), `get_state()`가 `None`인 조건 정확화, 같은 프레임의 보간 절대 명령 + 상대 명령은 목표 포즈에 적용, 보간 경로(target 직선 + eye 구면 보간), `commands` 지표 정의, 쓰기 레이어 규칙, 부모 scale 상쇄·COI 왕복 수정.
- 0.1.0 — 최초 초안
