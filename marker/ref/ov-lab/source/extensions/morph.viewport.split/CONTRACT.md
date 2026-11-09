# morph.viewport.split CONTRACT

> 다른 익스텐션 에이전트가 이 익스텐션을 쓸 때 읽는 **유일한** 문서. 여기 없는 것은 내부 구현이므로 의존하지 않는다.

- 버전: 1.5.1
- 상태: stable
- 담당 에이전트: ext-viewport
- 의존: morph.stream.messaging, morph.perf.monitor

## 목적
분할화면의 단일 창구. 뷰포트 레이아웃 프리셋(1x1 / 1x2 / 2x1 / 2x2 / 1+3)을 만들고 전환하며, 어느 뷰가 어떤 카메라 prim을 보여줄지 연결하고, 카메라 조작·입력이 적용될 포커스 뷰를 관리한다. 변경은 이벤트와 웹 메시지로 알린다.

## 언제 쓰나 / 쓰지 않나
- 쓴다: 화면을 여러 뷰포트로 나누거나, 특정 뷰에 카메라 prim을 연결하거나(연결할 수 있는 카메라 목록 포함), 포커스 뷰가 무엇인지 알아야 할 때
- 쓰지 않는다: 뷰포트 창을 직접 만들거나 닫는 것 (금지, 레이아웃 상태가 깨짐). 카메라를 움직이는 것 (→ `morph.camera.control`)

## 용어와 불변식
- **뷰(view)**: 레이아웃 안의 뷰포트 하나. `view_id`는 0부터 시작한다 (격자 레이아웃은 행 우선, `"1+3"`은 큰 뷰가 0). 레이아웃이 바뀌면 다시 매겨진다.
- **레이아웃**: `LAYOUTS`의 문자열 하나. 격자 `"<행>x<열>"`(`"1x2"`는 좌우 2분할, `"2x1"`은 상하 2분할)과 `"1+3"`(큰 뷰 하나 + 오른쪽 열 작은 뷰 셋, 1.3.0). 문자열을 해석하지 말고 목록·뷰 개수(`len(list_views())`)로 다룬다. 각 `view_id`의 화면 위치는 아래 "레이아웃별 뷰 배치".
- **view 0 = Kit 기본 뷰포트 창**(`/exts/omni.kit.viewport.window/startup/windowName`, 보통 `"Viewport"`). 추가 뷰(view 1~)만 이 익스텐션이 만들고 없앤다.
- **포커스 뷰 = Kit 활성 뷰포트.** 항상 정확히 하나. `focus_view`는 해당 뷰포트 창에 omni.ui 포커스를 줘서 Kit 활성 뷰포트도 바꾼다. 반대로 사용자가 창을 클릭하는 등 Kit 쪽에서 활성 뷰포트가 바뀌면 그것을 포커스 변경으로 받아 `focus_changed`를 발행한다. 따라서 이 익스텐션에 의존하지 않는 쪽도 `omni.kit.viewport.utility.get_active_viewport()`로 현재 포커스 뷰를 얻을 수 있다 (분할이 없는 1x1 앱에서도 같다).
- **카메라 연결의 기준은 Kit 뷰포트 API**(`viewport_api.camera_path`)다. `ViewInfo.camera_path`는 그 값을 반영한다. `set_view_camera`가 아닌 경로(예: `morph.camera.control`의 `set_active_camera`, Kit UI)로 바뀌어도 감지해서 `view_camera_changed`를 발행한다.

## 레이아웃별 뷰 배치
격자 레이아웃의 `view_id`는 행 우선(왼쪽→오른쪽, 위→아래)이다. 초기 분할 비율은 아래 표와 같지만(격자는 반반) 사용자가 분할선을 끌면 바뀌므로, 실제 화면 영역은 웹 state의 `rect`(아래 "뷰 화면 영역")로 확인한다.

| 레이아웃 | 배치 (화면 기준) |
|---|---|
| `"1x1"` | view 0 = 전체 |
| `"1x2"` (좌우) | view 0 = 왼쪽, view 1 = 오른쪽 |
| `"2x1"` (상하) | view 0 = 위, view 1 = 아래 |
| `"2x2"` | view 0 = 왼쪽 위, view 1 = 오른쪽 위, view 2 = 왼쪽 아래, view 3 = 오른쪽 아래 |
| `"1+3"` (1.3.0) | view 0 = 왼쪽 큰 뷰(전체 높이), 오른쪽 열에 view 1 = 위, view 2 = 가운데, view 3 = 아래 (셋 다 같은 높이 = 1/3) |

`"1+3"`의 오른쪽 열 폭은 설정 `sideRatio`(기본 0.25 = 창 폭의 25%, 0.1..0.5로 자르고 잘못된 값·NaN·inf는 기본값 — "설정" 표). 1920x1080 창이면 view 0 = 1440x1080, view 1~3 = 각 480x360 (모두 4:3). 실제 픽셀은 분할선·탭바·여백만큼 조금 작다. 실행 중에는 `set_side_ratio`(웹 `morph.viewport.split.set_side_ratio`)로 바꾸며, 이미 `"1+3"`이면 창을 다시 만들지 않고 곧바로 다시 배치한다 (1.5.0, 아래 "오른쪽 열 폭 바꾸기").

## 레이아웃 전환 모델
- `set_layout`은 **논리 상태를 즉시 바꾸고 반환**한다. 반환 직후 `get_layout()`, `list_views()`, `view_id`는 새 레이아웃 기준이다.
- 추가 뷰포트 창 생성·도킹·정리는 이후 몇 프레임에 걸쳐 끝난다. 그동안 새 뷰의 `ViewInfo.viewport_name`은 `""`이다. 창이 아직 없는 뷰에 `set_view_camera`를 부르면 값을 저장했다가 창이 생길 때 적용한다.
- `layout_changed`는 **창이 모두 준비된 뒤** 발행한다. 창 이름이 필요한 쪽은 이 이벤트 이후에 쓴다.
- **렌더 준비 게이트**: 추가 창 생성은 기본 뷰포트(view 0)가 렌더한 프레임을 받은 뒤에 시작한다 (한 번 확인되면 이후 전환은 기다리지 않음). `renderer.asyncInit=true` 앱에서 렌더러 준비 전에 뷰포트 창을 더 만들면 RTX가 크래시하기 때문이다. 그 전에 부른 `set_layout`(startup `defaultLayout`, 나중에 기본 창이 생겨 창 모드로 바뀌는 경우 포함)은 논리 상태만 바로 바뀌고, 창 생성과 `layout_changed`는 렌더 준비까지 늦어진다. 기다리는 동안에도 전환 중 규칙(마지막 요청만 반영, 포커스·카메라 저장)이 그대로 적용된다. 만들 창이 없는 전환(예: `"1x1"`로 축소)은 기다리지 않는다.
- 렌더 준비를 약 120초 안에 확인하지 못하면(예: 기본 뷰포트가 렌더를 하지 않는 앱) 아래 창 준비 실패 정책을 따른다 (`layout_changed("1x1")`). 이후 `set_layout`을 다시 부르면 처음부터 다시 기다린다.
- 추가 창은 view_id 슬롯마다 재사용한다 (같은 view_id = 같은 창). 레이아웃을 줄이면 남는 창을 닫고, 늘리면 모자란 창만 만든다. 남는 창은 새 레이아웃의 자리와 분할 비율이 이전과 다르면 다시 도킹한다 (한 뷰를 다시 도킹하면 그 뒤 번호의 뷰도 다시 도킹). **뷰 개수가 같은 전환(`"2x2"` ↔ `"1+3"`)** 은 창을 만들거나 닫지 않고 view 1~3을 다시 도킹해 배치만 바꾼다. 이때도 각 뷰는 같은 `view_id`의 카메라를 유지하고, 포커스 뷰도 그대로이며, 완료 시 `layout_changed`를 발행한다. 다시 도킹하면 사용자가 끌어 놓은 분할선 위치는 새 레이아웃 기본 비율로 돌아간다.
- 추가 창은 프레임당 하나씩 만든다. 2x2 전환은 렌더 준비 후 대략 창 생성 3프레임 + 도킹 3프레임 + 정리 2프레임 뒤 완료된다. 창을 만들지 않는 `"2x2"` ↔ `"1+3"` 전환은 렌더 준비를 기다리지 않고 대략 재도킹 3프레임 + 정리 2프레임 (프레임 수는 보장하지 않음, `layout_changed`로 확인).
- 전환 도중 `set_layout`을 다시 부르면 마지막 요청만 반영된다. 중간 레이아웃의 `layout_changed`는 발행하지 않는다.
- 기존 뷰는 같은 `view_id`의 카메라를 유지한다. 새로 생긴 뷰는 `defaultCameras`(없으면 Kit 세션 카메라 `/OmniverseKit_Persp`, `/OmniverseKit_Top`, `/OmniverseKit_Front`, `/OmniverseKit_Right`를 `view_id` 순으로)를 연결한다. 이때는 `view_camera_changed`를 따로 발행하지 않고 `layout_changed`의 `views`에 담는다.
- 레이아웃을 줄여 포커스 뷰가 사라지면 포커스는 view 0으로 간다 (`layout_changed` 다음에 `focus_changed`).
- 전환 도중의 포커스 변경(`focus_view` 포함)은 논리 상태에 즉시 반영되지만 `focus_changed`는 전환 완료 시 `layout_changed` 다음에 한 번만 발행한다 (전환 시작 때와 포커스 뷰가 다를 때만).
- 전환 도중 새로 생기는 뷰에 `set_view_camera`를 부르면 `view_camera_changed` 없이 저장되고 `layout_changed`의 `views`에 담긴다.
- 전환 도중에도 기존 뷰(창이 유지되는 뷰)의 외부 카메라 변경(`set_active_camera`, Kit UI)은 감지해서 `view_camera_changed`를 발행하고, 전환 완료 때 덮어쓰지 않는다.
- **창 준비 실패 정책**: 추가 창 생성·도킹이나 완료 처리가 예외로 실패하면 재시도하지 않는다. error 로그를 1회 남기고, 추가 창을 모두 정리한 뒤 `"1x1"`로 돌아가 `layout_changed`(`layout: "1x1"`)를 발행한다 (포커스 뷰가 view 0이 아니었으면 이어서 `focus_changed`). 도킹되지 않은 레이아웃을 완료로 알리지 않는다. `set_layout`은 이미 반환했으므로 호출자는 `layout_changed`의 `layout`으로 결과를 확인한다.
- Kit 활성 뷰포트를 바꾸는 데(포커스) 실패하면 error 로그만 남기고 논리 상태·레이아웃은 유지한다. `focus_view`는 이 경우에도 예외를 던지지 않는다.
- 전환 도중 사용자가 창을 클릭하는 등으로 Kit 활성 뷰포트가 바뀌어도 반영하지 않는다 (창 생성·도킹이 omni.ui 포커스를 옮길 수 있기 때문). 전환이 끝나면 포커스 뷰에 다시 포커스를 준다.

## 오른쪽 열 폭 바꾸기 (`sideRatio`, 1.5.0)
`set_side_ratio(ratio)` / `get_side_ratio()`, 웹 `morph.viewport.split.set_side_ratio`. 웹 슬라이더처럼 연속 호출해도 된다.
- **값**: 숫자(`int`/`float`, bool 아님, 유한)만 받는다. 0.1..0.5 밖이면 **경고 없이 잘라서** 적용하고 적용값을 돌려준다 (`set_side_ratio(0.9) == 0.5`). 잘못된 값은 `ValueError`(웹 `invalid_argument`)이고 상태·설정은 그대로다. `get_side_ratio()`는 지금 유효한(잘린) 값이다.
- **설정과의 관계 (양방향)**: `set_side_ratio`는 적용값을 설정 `/exts/morph.viewport.split/sideRatio`에도 쓴다. 반대로 설정을 직접 바꾸면(carb settings) 다음 `get_side_ratio()`·state 생성·업데이트 프레임에 그 값을 받아들이고 `set_side_ratio`와 똑같이 반영한다 (1.4.0까지의 "전환 시작 때만 읽음"에서 바뀜). 설정 값 해석은 설정 표 규칙 그대로다: 범위 밖은 잘라내고 경고, 숫자 아님·NaN·inf는 기본값 0.25로 보고 경고 (같은 값은 1회).
- **현재 `"1+3"`이고 창 모드일 때 — 즉시 반영**: 호출은 값만 바꾸고 반환한다. 다음 업데이트 프레임에 view 1~3을 다시 도킹해 오른쪽 열 폭을 바꾼다 (`"2x2"` ↔ `"1+3"` 재도킹과 같은 방식). **창을 만들거나 닫지 않는다** — 모든 뷰의 `viewport_name`과 뷰포트 API 객체(`get_viewport_from_window_name`으로 얻은 것)가 그대로 유효하고, 각 뷰의 카메라와 포커스 뷰도 그대로다. 사용자가 끌어 놓은 분할선 위치(오른쪽 열 안의 높이 포함)는 새 폭의 기본 배치로 돌아간다.
- **재도킹은 전환처럼 진행된다**: 대략 재도킹 3프레임 + 정리 2프레임 (보장하지 않음). 그동안은 "레이아웃 전환 모델"의 전환 중 규칙이 그대로 적용된다 (포커스 변경은 완료 때 한 번, 외부 카메라 변경 감지, 웹 rect는 null, `set_layout`을 부르면 그 전환으로 바뀜, 실패 시 "창 준비 실패 정책"). **레이아웃은 바뀌지 않으므로 `layout_changed`는 발행하지 않는다** (재도킹 중 포커스가 바뀌었으면 `focus_changed`만). 완료되면 `state`를 1회 푸시한다 (새 `side_ratio`와 새 `rect`, 약 5프레임 뒤 rect 재확인도 전환 완료와 같음).
- **병합**: 한 프레임에 여러 번 부르면 마지막 값만 반영된다 (재도킹 1회). 재도킹이나 레이아웃 전환이 진행 중일 때 부르면 그 전환이 끝난 다음 프레임에 **그때의 최신 값**으로 한 번 더 재도킹한다 (중간 값은 건너뜀). 따라서 슬라이더를 계속 끌면 재도킹이 끝날 때마다(약 6프레임마다) 최신 값으로 따라간다.
- **`"1+3"`이 아니거나 논리 전용 모드일 때**: 값만 저장한다 (창 변화 없음). 다음 `"1+3"` 전환(창 모드로 바뀔 때의 재구성 포함)에 쓰인다. `state`는 다음 업데이트 프레임에 1회 푸시한다 (`side_ratio`만 바뀜). 같은 값을 다시 주면 아무것도 하지 않는다.
- carb 이벤트는 발행하지 않는다 (값 변경 이벤트 없음). 다른 익스텐션이 값을 알아야 하면 `get_side_ratio()`를 부른다.

## 창이 없는 환경 (헤드리스 / 테스트)
- Kit 기본 뷰포트 창이 없으면(예: `omni.kit.viewport.window`가 로드되지 않은 테스트 kit) **논리 전용 모드**로 동작한다. 모든 API가 같은 규칙으로 동작하고 이벤트도 발행되지만, `viewport_name`은 항상 `""`이고 창·카메라는 실제로 바뀌지 않는다. `layout_changed`는 `set_layout` 다음 업데이트 프레임에 발행한다.
- `SplitView.is_headless`로 확인할 수 있다.
- startup 때 기본 창이 없었더라도 나중에 생기면(약 30프레임 간격으로 확인) 창 모드로 바뀌고, 현재 레이아웃을 실제 창으로 만든 뒤 `layout_changed`를 발행한다. 이때 view 0 카메라는 그 창의 현재 카메라를 따른다 (논리 전용 모드 중 `set_view_camera(0, …)`를 불렀다면 그 값을 창에 적용한다).

## 수명과 외부 변경
- 사용자가 UI에서 추가 뷰 창을 닫으면 레이아웃은 `"1x1"`로 돌아가고 `layout_changed`를 발행한다.
- 스테이지를 새로 열면 모든 뷰의 카메라를 기본값(위 규칙)으로 되돌리고 바뀐 뷰마다 `view_camera_changed`를 발행한다. 연결된 카메라 prim이 삭제되면 그 뷰만 기본값으로 되돌린다.
- `on_shutdown`에서 추가 창을 모두 닫고, view 0의 카메라는 startup 시점 값으로 되돌린다. 이벤트는 발행하지 않는다.

## 카메라 목록 (1.4.0)
뷰에 연결할 카메라를 고르는 UI(웹 드롭다운 등)를 위한 목록이다. `list_cameras()` / 웹 `morph.viewport.split.list_cameras`.
- **대상**: 열린 스테이지(창 모드는 view 0 뷰포트의 UsdContext, 논리 전용 모드는 기본 UsdContext)의 `UsdGeom.Camera` prim 전부 (`IsA(UsdGeom.Camera)`, 하위 스키마 포함). 세션 레이어의 Kit 기본 카메라 `/OmniverseKit_Persp`, `/OmniverseKit_Top`, `/OmniverseKit_Front`, `/OmniverseKit_Right`도 스테이지에 있으면 포함한다.
- **순회 규칙**: `Usd.PrimRange` 기본 predicate — 활성(active)·로드됨·정의됨(def)·추상 아님(class 아래 아님)인 prim만. Camera 아래의 Camera도 찾는다. **instance proxy는 따라가지 않는다**: instanceable prim 안의 카메라와 프로토타입은 목록에 없다 (큰 인스턴스 장면에서 비용을 막고, 인스턴스 안 카메라는 경로가 인스턴스마다 생겨 고르기 대상으로 부적합). 비활성 카메라도 빠진다.
- **항목**: `path`(절대 prim 경로 문자열), `name`(prim 이름 = 경로 마지막 요소), `builtin`(경로가 위 Kit 기본 카메라 넷 중 하나면 `true`. 어느 레이어에 정의됐는지는 보지 않는다).
- **정렬**: 사용자 카메라(`builtin: false`)를 경로 문자열 오름차순(코드 포인트 순, 자연 정렬 아님: `"/World/Cam10"` < `"/World/Cam2"`)으로 먼저, 그다음 기본 카메라를 `Persp`, `Top`, `Front`, `Right` 순(있는 것만)으로. 같은 경로는 한 번만.
- **`set_view_camera`와의 관계**: 목록의 모든 경로는 `set_view_camera` 검증(prim 있음, Camera prim)을 통과한다. 반대는 아니다 — instance proxy·비활성 카메라처럼 목록에 없어도 `set_view_camera`가 받는 경로가 있을 수 있다. 스테이지는 그 사이 바뀔 수 있으므로(prim 삭제 등) 목록을 받은 뒤의 `set_view_camera` 실패(`invalid_argument`)는 웹이 목록을 다시 요청하는 계기로 쓴다.
- **스테이지 없음**: Python `list_cameras()`는 빈 목록, 웹은 `failed_precondition`. 스테이지는 있지만 카메라가 없으면 둘 다 빈 목록(웹은 `{ ok: true, cameras: [] }`). 논리 전용 모드여도 스테이지가 열려 있으면 같은 목록을 준다 (논리 전용 모드의 `set_view_camera`는 prim 검사를 하지 않음).
- **비용**: 호출(요청)할 때마다 스테이지를 처음부터 순회한다 (캐시 없음, 업데이트 루프에서는 순회하지 않음). 비용은 스테이지 prim 수에 비례한다. 측정 지표 `morph.viewport.split.list_cameras_ms` (아래 "성능 측정"). 매 프레임 부르지 않는다.
- **변경 알림 없음**: 카메라가 추가·삭제·이름 변경돼도 이벤트나 웹 푸시는 없다 (state에도 담지 않는다). 필요할 때(드롭다운을 열 때, 스테이지를 새로 연 뒤, `set_view_camera` 실패 후 등) 다시 요청한다. 푸시는 이후 minor에서 별도 type으로 추가될 수 있다.

## 공개 Python API
import: `from morph.viewport.split import get_split_view, SplitView, ViewInfo, CameraInfo, LAYOUTS, EVENT_LAYOUT_CHANGED, EVENT_FOCUS_CHANGED, EVENT_VIEW_CAMERA_CHANGED`

모든 메서드는 **메인 스레드 전용**이다 (읽기 메서드 포함).

| 심볼 | 시그니처 | 설명 | 주의 (스레드/호출 시점/수명) |
|---|---|---|---|
| `get_split_view` | `() -> SplitView \| None` | 싱글턴 | 이 익스텐션 startup 이후 유효, shutdown 이후 `None` |
| `LAYOUTS` | `tuple[str, ...]` | 지원 레이아웃 `("1x1", "1x2", "2x1", "2x2", "1+3")` | minor 버전에서 늘어날 수 있다 (고정 개수로 가정하지 않는다) |
| `SplitView.set_layout` | `(layout: str) -> None` | 레이아웃 전환 (위 "레이아웃 전환 모델") | `LAYOUTS`에 없으면 `ValueError`. 같은 레이아웃이면 아무것도 안 함 |
| `SplitView.get_layout` | `() -> str` | 현재(논리) 레이아웃 | |
| `SplitView.list_views` | `() -> list[ViewInfo]` | 현재 뷰 목록 (`view_id` 순) | 스냅샷. 이후 변경은 반영되지 않음 |
| `SplitView.set_view_camera` | `(view_id: int, camera_path: str) -> None` | 뷰에 카메라 prim 연결 (포커스 뷰가 아니어도 됨) | 없는 `view_id`는 `IndexError`. 경로에 prim이 없거나 Camera prim이 아니면 `ValueError`. 열린 스테이지가 없으면 `RuntimeError`. 같은 경로면 아무것도 안 함. 논리 전용 모드에서는 prim 검사 없이 저장만 (빈 문자열·문자열 아님은 모드와 관계없이 `ValueError`) |
| `SplitView.focus_view` | `(view_id: int) -> None` | 포커스 뷰 변경 (Kit 활성 뷰포트도 변경) | 없는 `view_id`는 `IndexError`. 이미 포커스 뷰이고 Kit 활성 뷰포트도 그 창이면 아무것도 안 함. 이미 포커스 뷰지만 Kit 활성 뷰포트가 다른 창이면(외부 뷰포트 창 활성화 등) 그 창에 다시 포커스를 주고, 논리 포커스는 바뀌지 않았으므로 `focus_changed`는 발행하지 않는다. 창이 아직 없는 뷰(전환 중)면 전환 완료 때 포커스를 준다 |
| `SplitView.get_focused_view` | `() -> ViewInfo` | 포커스 뷰 | |
| `SplitView.is_headless` | 프로퍼티 `-> bool` | 논리 전용 모드 여부 | |
| `SplitView.set_side_ratio` | `(ratio: float) -> float` | `"1+3"` 오른쪽 열 폭(창 폭 대비)을 바꾸고 실제 적용값(0.1..0.5로 자른 값)을 돌려준다 (1.5.0, 위 "오른쪽 열 폭 바꾸기") | 숫자가 아니거나(bool·`None`·문자열 포함) 유한하지 않으면(NaN, inf) `ValueError`. 범위 밖은 예외 없이 자름. shutdown 이후는 `RuntimeError`(값 검사가 먼저). 설정 `sideRatio`에도 쓴다. 현재 `"1+3"`(창 모드)이면 다음 업데이트 프레임부터 재도킹(창 유지, `layout_changed` 없음). 논리 전용 모드에서도 저장·반환은 같다 |
| `SplitView.get_side_ratio` | `() -> float` | 지금 유효한 `"1+3"` 오른쪽 열 폭 (1.5.0) | 설정을 직접 바꿨으면 그 값을 해석한 결과 (잘못된 값 → 0.25). 재도킹이 아직 끝나지 않았어도 요청한 값을 돌려준다 (화면 배치는 웹 `rect`로 확인) |
| `SplitView.list_cameras` | `() -> list[CameraInfo]` | 고를 수 있는 카메라 prim 목록 (1.4.0, 위 "카메라 목록" — 대상·정렬·instance proxy 규칙) | 열린 스테이지가 없으면 `[]` (예외 없음). 호출마다 스테이지를 순회한다 (매 프레임 부르지 않는다). 이벤트·푸시 없음. 새 리스트를 돌려준다 |
| `CameraInfo` | frozen dataclass: `.path: str`, `.name: str`, `.builtin: bool`, 메서드 `.to_dict() -> dict` (`{"path", "name", "builtin"}`, 웹 항목과 같은 모양) | 카메라 prim 하나 (1.4.0) | `path`는 `set_view_camera`에 그대로 넘길 수 있다 |
| `ViewInfo` | frozen dataclass: `.view_id: int`, `.viewport_name: str`, `.camera_path: str`, `.focused: bool` | 뷰 정보 스냅샷 | `viewport_name`은 Kit 뷰포트 창 이름 (창 준비 전·논리 전용 모드는 `""`). `omni.kit.viewport.utility.get_viewport_from_window_name(viewport_name)`으로 뷰포트 API를 얻는다 |
| `EVENT_LAYOUT_CHANGED`, `EVENT_FOCUS_CHANGED`, `EVENT_VIEW_CAMERA_CHANGED` | `str` 상수 | 아래 발행 이벤트 이름 | 문자열을 직접 써도 된다 |

## 이벤트
이름 규칙 `morph.<domain>.<event>`, `carb.eventdispatcher` 사용. **이벤트 이름(`morph.viewport.*`)은 웹 메시지 type(`morph.viewport.split.*`, 아래 "웹 메시지")과 다른 이름 공간이다.** 익스텐션끼리는 이벤트를, 웹은 type을 쓴다. 웹 type이 1.0.0에서 바뀌었어도 이벤트 이름과 페이로드는 그대로다. **모든 이벤트는 메인 업데이트 루프에서 발행된다.** API 호출(`set_view_camera`, `focus_view` 등) 안에서 동기로 발행되지 않고, API가 반환된 뒤 이 익스텐션의 다음 업데이트 처리에서(호출 시점에 따라 같은 프레임의 뒤쪽 또는 다음 프레임) 발생 순서대로 발행된다. 따라서 이벤트 관찰자에서 이 익스텐션 API를 불러도 재진입 문제가 없다.

포커스 뷰를 따라가는 쪽(`morph.camera.control` 포함)은 `focus_changed`를 받은 뒤에야 대상이 바뀐다. `focus_view` 직후 같은 프레임에 보낸 카메라 명령은 이전 포커스 뷰에 적용될 수 있다.

### 발행
`View` = `{ "view_id": int, "viewport_name": str, "camera_path": str }` (이벤트 페이로드에는 `rect`가 없다. `rect`는 웹 state에만 — 아래 "뷰 화면 영역")

| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `morph.viewport.layout_changed` | `{ "layout": str, "views": [View, ...], "focused_view_id": int }` | 레이아웃 전환 완료 후 (창 준비 끝난 뒤), 사용자가 추가 창을 닫아 1x1로 돌아갈 때 |
| `morph.viewport.focus_changed` | `View` | 포커스 뷰가 바뀜 (`focus_view`, Kit 활성 뷰포트 변경, 레이아웃 축소로 인한 변경). 레이아웃 전환이 원인이면 `layout_changed` 다음에 발행 |
| `morph.viewport.view_camera_changed` | `View` | 기존 뷰의 카메라가 바뀜 (`set_view_camera`, 외부 변경 감지, 스테이지 열기/prim 삭제로 기본값 복귀) |

### 수신
| 이벤트 | 소유 익스텐션 | 반응 |
|---|---|---|
| `morph.stream.client_connected` | morph.stream.messaging | 현재 상태(`morph.viewport.split.state`)를 웹에 1회 송신 |

## 웹 메시지 (morph.stream.messaging 1.x 경유)
- type은 stream 규칙 `<owner ext-id>.<name>`을 따라 모두 **`morph.viewport.split.`** 으로 시작한다 (위 carb 이벤트 이름 `morph.viewport.*`와 다름).
- 모든 타입 `v = 1` (회신 봉투 `v`도 1).
- **회신 규칙 (stream 1.x)**: 웹→Kit 요청마다 같은 type·같은 id로 정확히 한 번 회신한다. 성공 `{ "ok": true, ... }`, 실패 `{ "ok": false, "error": <code>, "message": str, ... }`. `id: null`이면 stream이 회신 송신을 생략한다 (요청은 그대로 실행됨).
- 스키마 위반·미지원 `v`는 핸들러까지 오지 않고 stream이 `morph.stream.messaging.error`(`invalid_payload` / `unsupported_version`)로 회신한다.
- 아래 표의 실패 code 외의 예상 밖 예외는 stream이 같은 type·id로 `handler_error`를 회신한다 (버그로 보고 로그 확인).

| 방향 | 메시지 타입 | 페이로드 스키마 | 매핑 / 성공 회신 |
|---|---|---|---|
| 웹→Kit | `morph.viewport.split.set_layout` | `{"type":"object","required":["layout"],"properties":{"layout":{"type":"string","enum":["1x1","1x2","2x1","2x2","1+3"]}}}` | `set_layout`. 논리 전환 직후 `{ ok: true }` (창 준비 완료는 `morph.viewport.split.state` 푸시로 확인) |
| 웹→Kit | `morph.viewport.split.set_view_camera` | `{"type":"object","required":["view_id","camera_path"],"properties":{"view_id":{"type":"integer"},"camera_path":{"type":"string"}}}` | `set_view_camera` → `{ ok: true }` |
| 웹→Kit | `morph.viewport.split.focus_view` | `{"type":"object","required":["view_id"],"properties":{"view_id":{"type":"integer"}}}` | `focus_view` → `{ ok: true }` (Kit 활성 뷰포트 변경 실패도 성공으로 회신. 위 "레이아웃 전환 모델") |
| 웹→Kit | `morph.viewport.split.get_state` | `{"type":"object"}` | `{ ok: true, layout, focused_view_id, views, side_ratio }` (= `ok: true` + `state` 페이로드) |
| 웹→Kit | `morph.viewport.split.list_cameras` (1.4.0) | `{"type":"object"}` | `list_cameras` → `{ ok: true, cameras: [{ path: string, name: string, builtin: boolean }, ...] }` (위 "카메라 목록"의 순서). 열린 스테이지가 없으면 `failed_precondition` |
| 웹→Kit | `morph.viewport.split.set_side_ratio` (1.5.0) | `{"type":"object","required":["ratio"],"properties":{"ratio":{"type":"number"}}}` | `set_side_ratio` → `{ ok: true, side_ratio: number }` (실제 적용값, 0.1..0.5로 자른 값). 바로 회신하며 재도킹 완료는 `state` 푸시로 확인. 숫자 아님(bool 포함)·NaN·inf·`ratio` 없음은 `invalid_argument` — stream 스키마 검증이 bool·NaN을 거른다고 가정하지 않고(검증이 꺼져 있을 수도 있음) 핸들러가 다시 검사한다 |
| Kit→웹 | `morph.viewport.split.state` | `{ "layout": string, "focused_view_id": integer, "views": [WebView, ...], "side_ratio": number }` | 푸시 (아래 "상태 푸시"). 회신이 아니므로 `id: null`, `ok` 없음. `side_ratio`(1.5.0, 항상 있음, outbound 스키마 필수)는 `get_side_ratio()`와 같은 값이며 레이아웃과 관계없이 담긴다 |

웹 state(`state` 푸시와 `get_state` 회신)의 뷰는 이벤트 `View`에 `rect`를 더한 것이다 (1.2.0):
`WebView` = `{ "view_id": int, "viewport_name": str, "camera_path": str, "rect": Rect | null }`, `Rect` = `{ "x": float, "y": float, "w": float, "h": float }` (object면 네 필드 모두 있다. outbound 스키마 `required: ["x","y","w","h"]`, 1.3.0)

### 뷰 화면 영역 (`rect`, 1.2.0)
- **좌표계**: 앱 메인 창(omni.appwindow 기본 창 = 스트리밍 영상 전체) 크기 기준 **0..1 정규화, 원점 좌상단**, x는 오른쪽, y는 아래로 증가. `x`,`y`는 뷰의 왼쪽 위 모서리, `w`,`h`는 폭·높이. 영상 픽셀로는 `x * videoWidth` 등 (영상이 앱 창 전체를 그대로 담는다고 가정).
- **값의 출처**: 뷰포트 창(`ui.Workspace.get_window(viewport_name)`)의 `position_x` / `position_y` / `width` / `height`를 앱 창 크기(omni.ui 좌표 단위로 맞춤: 픽셀 ÷ `ui.Workspace.get_dpi_scale()`)로 나눈 값. **창 전체 영역**이라 도킹 탭바(보일 때)와 안쪽 여백(`viewPadding`)을 포함한다. 렌더 이미지 영역과 거의 같게 하려면 `hideTabBar=true`, `viewPadding=0`을 쓴다. 앱 창 밖으로 나간 부분은 잘라낸다 (항상 0..1 안). 소수 5자리로 반올림.
- **`rect: null`** 인 경우: 논리 전용 모드(헤드리스·테스트 kit, 항상 null), 창이 아직 없음(전환 중 새 뷰), 레이아웃 전환 중(창 생성·도킹 중 — `layout_changed` 전에 나가는 푸시는 모든 뷰가 null. `sideRatio` 재도킹 중도 같음, 1.5.0 — 단 재도킹·레이아웃 전환 중에는 `side_ratio` 변경 푸시를 보내지 않고 완료 때 보낸다, 1.5.1), 창이 숨겨짐, 앱 창 크기를 모름, 크기가 0이거나 앱 창과 겹치지 않음, 그 뷰의 창 속성을 읽다 예외(죽은 창 등 — 그 뷰만 null, 경고 없음). rect 읽기 전체가 실패하면(예상 밖 예외) 모든 뷰가 null이고 경고를 1회만 남긴다. 웹은 null인 뷰를 판정 대상에서 빼야 한다.
- **판정 예**: 정규화 좌표 `(u, v)`가 `x <= u < x + w`이고 `y <= v < y + h`인 뷰. 뷰 사이 분할선(수 px)은 어느 뷰에도 속하지 않을 수 있다. pan 픽셀→월드 환산의 뷰 픽셀 폭은 `rect.w * videoWidth`.
- **재푸시 조건**: 전환 중이 아닐 때 약 30프레임마다 rect를 다시 읽어, **마지막으로 만든 state payload**(`state` 푸시, `get_state` 회신, 연결 시 송신 중 가장 최근 것. 웹 미연결로 실제로 보내지 않았거나 송신이 실패한 payload도 포함)에 담긴 값과 비교한다. 어느 뷰든 어느 성분이든 **0.002 이상** 차이나거나 null↔값으로 바뀌면 `state`를 1회 보낸다 (분할선 드래그, 앱 창 크기 변경, 창 숨김 등. 0.002 미만은 무시). 레이아웃 전환 완료 푸시(`layout_changed`와 같은 프레임) 뒤에는 약 5프레임 뒤에 한 번 더 확인하므로, 도킹 크기가 그 사이 자리 잡으면 한 번 더 푸시된다. 프레임 수는 보장하지 않는다 (patch에서 바뀔 수 있음).
- rect 변화만으로는 carb 이벤트를 발행하지 않는다 (웹 state 전용).

### 실패 code (핸들러 회신)
| code | 해당 요청 | 원인 | 추가 필드 |
|---|---|---|---|
| `not_found` | `set_view_camera`, `focus_view` | 현재 레이아웃에 없는 `view_id` (view_id 검사가 다른 검사보다 먼저) | `view_id: int` (요청 값) |
| `invalid_argument` | `set_layout`, `set_view_camera`, `set_side_ratio` | 지원하지 않는 레이아웃, 빈/잘못된 `camera_path`, prim 없음, Camera prim 아님, 뷰포트가 카메라를 받지 않음, `ratio`가 숫자 아님(bool 포함)·NaN·inf·없음 (범위 밖은 실패가 아니라 잘라서 성공) | |
| `failed_precondition` | `set_view_camera`, `list_cameras` | 열린 스테이지가 없음 (`set_view_camera`는 창 모드에서만 — 논리 전용 모드는 prim 검사를 하지 않음. `list_cameras`는 모드와 관계없이) | |
| `unavailable` | 모두 | 이 익스텐션이 실행 중이 아님 (shutdown 진행 중 등). 핸들러가 시작된 뒤 처리 도중 shutdown되어 Python API가 `RuntimeError`를 낸 경우도 `failed_precondition`이 아니라 이 code (1.5.1) | |

실패한 요청은 상태를 바꾸지 않고 이벤트·푸시도 없다. `message`는 표시·로그용이다 (분기는 `error`로).

### 상태 푸시 (`morph.viewport.split.state`)
- **웹 클라이언트가 연결된 동안에만 보낸다.** 연결 여부는 stream의 `Messaging.is_connected`(기본 설정에서는 웹이 `morph.stream.messaging.hello`를 보낸 뒤 `bye` 전까지)로 판단한다. hello 전에 일어난 변경은 푸시하지 않으므로, 웹은 hello 뒤에 오는 상태 푸시(아래, hello 회신과의 도착 순서는 보장하지 않음)나 `get_state`로 초기 상태를 얻는다.
- 연결 시(`morph.stream.client_connected`) 현재 상태를 1회 보낸다.
- 연결된 동안 위 세 이벤트(`layout_changed` / `focus_changed` / `view_camera_changed`) 중 하나라도 발생한 업데이트 프레임 끝에 1회 보낸다 (병합). 이벤트 순서별 상태가 필요하면 Kit 쪽 이벤트를 쓴다.
- 뷰 `rect`가 바뀐 것을 감지했을 때도 1회 보낸다 (위 "뷰 화면 영역"의 재푸시 조건, 1.2.0).
- `side_ratio`가 바뀌었을 때도 1회 보낸다 (1.5.0). **재도킹이나 레이아웃 전환이 진행 중이면 보내지 않고 그 전환이 끝난 프레임에 보낸다** (전환 중 state는 rect가 모두 null이므로. 1.5.1에서 정정 — 1.5.0은 재도킹만 기다리고 다른 레이아웃→`"1+3"` 같은 레이아웃 전환 중에는 바로 보냈다). 전환 중이 아니면 바뀐 다음 업데이트 프레임에. 같은 프레임의 다른 변경과 합쳐 1회다. 예: `"2x2"`→`"1+3"` 전환 중에 바꾸면 `layout_changed`와 같은 프레임의 푸시에 새 `side_ratio`가 담기고(rect는 아직 이전 폭의 배치), 이어지는 재도킹이 끝나면 새 rect로 1회 더 보낸다.
- 송신 실패는 경고 로그만 남긴다 (재시도 없음).
- stream "연결 상태의 한계"를 그대로 따른다: 브라우저가 `bye` 없이 닫히면 `is_connected`가 `True`로 남아 푸시가 계속 큐잉된다 (전달되지 않을 뿐 무해).

## 설정
| 경로 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `/exts/morph.viewport.split/defaultLayout` | str | `"1x1"` | startup 시 적용할 레이아웃 (`LAYOUTS` 중 하나, `"1+3"` 포함). 목록에 없으면 경고 후 `"1x1"` |
| `/exts/morph.viewport.split/sideRatio` | float | 0.25 | `"1+3"`의 오른쪽 열 폭 (창 폭 대비, 1.3.0). 설정이 없으면 경고 없이 기본값 0.25. 0.1..0.5 밖이면 잘라내고, 숫자가 아니거나(bool·문자열 포함) 유한하지 않으면(NaN, inf, -inf) 기본값 0.25. 둘 다 경고 로그(같은 값은 1회). **1.5.0부터 실행 중 값과 양방향으로 맞춘다**: `set_side_ratio`가 잘린 적용값을 여기 쓰고, 이 설정을 직접 바꾸면 다음 업데이트 프레임(또는 `get_side_ratio()`)에 받아들여 `set_side_ratio`와 똑같이 반영한다 — 이미 `"1+3"`이면 곧바로 재도킹 (1.4.0까지는 전환 시작 때만 읽어 이미 `"1+3"`인 상태에는 적용되지 않았음). 다른 레이아웃에는 영향 없음 (값만 저장). 위 "오른쪽 열 폭 바꾸기". **주의**: carb 설정은 프로세스 단위라 shutdown 때 되돌리지 않는다 — 같은 프로세스에서 익스텐션을 다시 켜면 `.kit`/`extension.toml` 값이 아니라 마지막 `set_side_ratio`(또는 직접 바꾼) 값으로 시작한다 (1.5.1 명시) |
| `/exts/morph.viewport.split/focusOnClick` | bool | true | 뷰포트 클릭 시 포커스 이동. false면 포커스는 `focus_view`로만 바뀌고, 클릭으로 Kit 활성 뷰포트가 바뀌어도 포커스 뷰로 되돌린다 (한 프레임 지연 가능) |
| `/exts/morph.viewport.split/defaultCameras` | list[str] | `[]` | 새로 생기는 뷰에 `view_id` 순으로 연결할 카메라 prim 경로. 비거나 모자라면 Kit 세션 카메라 순서 |
| `/exts/morph.viewport.split/hideTabBar` | bool | false | true면 모든 뷰 창(view 0 포함)의 도킹 탭바를 숨기고 비활성화한다 (`dock_tab_bar_visible = False`, `dock_tab_bar_enabled = False` — 사용자가 다시 켤 수 없음). 1x1에서도 view 0에 유지된다. 아래 "뷰 창 꾸밈" (1.1.0) |
| `/exts/morph.viewport.split/viewPadding` | float | 설정 없음 (건드리지 않음) | 지정하면 모든 뷰 창의 안쪽 여백 `padding_x` / `padding_y` (Kit 기본 4.0). 0이면 뷰 사이 간격이 줄어든다. 음수·숫자 아님은 경고 후 무시 (1.1.0) |
| `/exts/morph.viewport.split/dockSplitterSize` | float | 설정 없음 (건드리지 않음) | 지정하면 ImGui 전역 스타일 `DockSplitterSize`(도킹 분할선 두께)를 그 값으로 한 번 push한다. **앱 전체의 모든 도킹 분할선에 적용된다** (아래 금지/주의). 0에 가까우면 분할선을 끌어 크기를 조절하기 어려워지므로 1 이상 권장. 음수·숫자 아님은 경고 후 무시 (1.1.0) |

### 뷰 창 꾸밈 (`hideTabBar` / `viewPadding` / `dockSplitterSize`, 1.1.0)
- 셋 다 기본은 아무것도 바꾸지 않는다. 설정은 startup 때 한 번 읽는다 (실행 중 바꿔도 반영되지 않음).
- 적용 대상은 **이 익스텐션의 뷰 창 전부**: view 0(기본 `Viewport` 창)과 추가 뷰 창. 이 익스텐션이 만들지 않은 다른 창은 건드리지 않는다 (`dockSplitterSize`의 전역 효과는 예외).
- 적용 시점: (1) 창 모드로 동작을 시작한 첫 업데이트 프레임 (startup `1x1`이면 view 0), (2) 레이아웃 전환이 끝날 때 — 창 생성·도킹이 끝난 뒤, `layout_changed` 발행 전 (새 레이아웃의 새 창 포함, 논리 전용 모드에서 창 모드로 바뀔 때 포함), (3) 전환 중이 아닐 때 약 30프레임마다 값을 확인해 달라진 것만 다시 쓴다. 사용자가 창을 다시 도킹·분리해서 탭바·여백이 되돌아가도 이 확인으로 복구된다 (최대 약 30프레임 지연). 전환 도중에는 새 창의 탭바가 잠시 보일 수 있다.
- `dockSplitterSize`는 창 모드가 된 뒤 한 번만 push한다. 논리 전용 모드(헤드리스·테스트 kit)에서는 적용을 미루고, 나중에 창 모드로 바뀌면 그때 적용한다. `omni.kit.imgui`(`omni.kit.renderer.imgui`)가 없거나 push가 실패하면 경고 1회 후 생략한다.
- 적용 실패(창 핸들 없음, omni.ui 속성 예외)는 (뷰, 속성)마다 경고 1회만 남기고 레이아웃 동작·이벤트를 막지 않는다.
- shutdown 때 view 0의 탭바·여백은 이 익스텐션이 바꾸기 전 값으로 되돌리고(추가 창은 닫힘), `dockSplitterSize`는 push한 스타일 변수를 한 번 pop한다.
- 실제 앱(Kit 110.1.3, 2x2)에서 `hideTabBar=true`, `viewPadding=0`, `dockSplitterSize=1`이면 뷰 사이 간격은 약 3px(가운데 분할선 1px + 양쪽 창 가장자리 1px씩), 각 뷰는 탭 높이(약 24px)만큼 커진다.

## 성능 측정 (morph.perf.monitor)
| 지표 | 종류 | 의미 |
|---|---|---|
| `morph.viewport.split.layout_switches` | counter | 레이아웃 전환 횟수 (`sideRatio` 재도킹은 세지 않음) |
| `morph.viewport.split.layout_step_ms` | scope | 레이아웃 전환 중 각 업데이트 프레임에서 이 익스텐션이 쓴 시간 (await를 넘지 않는 동기 구간만). `sideRatio` 재도킹 중 프레임도 포함 (1.5.0) |
| `morph.viewport.split.view_count` | gauge | 현재 뷰 개수 (startup 첫 업데이트와 값이 바뀔 때 기록. perf.monitor가 마지막 값을 유지) |
| `morph.viewport.split.list_cameras_ms` | scope | `list_cameras`(Python·웹) 한 번의 스테이지 순회 시간 (1.4.0). 스테이지가 없으면 기록하지 않음 |

뷰 개수에 따른 렌더 비용 증가는 perf.monitor의 `frame_ms`를 `view_count`와 함께 본다.

## 사용 예
`config/extension.toml`:
```toml
[dependencies]
"morph.viewport.split" = {}
```

앱 `.kit`에서 분할 뷰 사이 간격을 최소로 (탭바 숨김, 여백 0, 분할선 1px — 분할선은 앱 전체에 적용):
```toml
[settings]
exts."morph.viewport.split".defaultLayout = "2x2"
exts."morph.viewport.split".hideTabBar = true
exts."morph.viewport.split".viewPadding = 0.0
exts."morph.viewport.split".dockSplitterSize = 1.0
```

큰 뷰 하나 + 작은 뷰 셋 (오른쪽 열 30%):
```toml
[settings]
exts."morph.viewport.split".defaultLayout = "1+3"
exts."morph.viewport.split".sideRatio = 0.3
```

Python (메인 스레드):
```python
from morph.viewport.split import get_split_view

split = get_split_view()
split.set_layout("1x2")                          # 논리 상태는 즉시 바뀜
split.set_view_camera(1, "/OmniverseKit_Top")    # 창이 아직 없어도 됨 (생길 때 적용)
split.focus_view(1)
for v in split.list_views():
    print(v.view_id, v.viewport_name, v.camera_path, v.focused)

split.set_layout("2x2")
split.set_layout("1+3")   # 뷰 개수가 같으면 창을 재사용하고 배치만 바뀜 (카메라·포커스 유지)

# "1+3" 오른쪽 열 폭 (1.5.0). 적용값(0.1..0.5로 자른 값)을 돌려주고 설정 sideRatio에도 쓴다.
# 이미 "1+3"이면 다음 프레임부터 재도킹 (창·카메라·포커스 유지, layout_changed 없음). 아니면 저장만.
applied = split.set_side_ratio(0.35)              # -> 0.35
assert split.set_side_ratio(0.9) == 0.5           # 범위 밖은 잘림 (예외 아님)
print("side ratio:", split.get_side_ratio())     # 0.5
try:
    split.set_side_ratio(float("nan"))           # 숫자 아님(bool 포함)·NaN·inf는 ValueError, 값은 그대로
except ValueError as exc:
    print("bad ratio:", exc)

# 연결할 수 있는 카메라 (사용자 카메라 경로순, 그다음 Kit 기본 카메라). 스테이지가 없으면 []
for cam in split.list_cameras():
    print(cam.path, cam.name, "builtin" if cam.builtin else "user")
user_cams = [c.path for c in split.list_cameras() if not c.builtin]
if user_cams:
    split.set_view_camera(0, user_cams[0])         # 목록의 경로는 그대로 넘길 수 있다

# 실패는 예외 (웹 code와 대응: IndexError=not_found, ValueError=invalid_argument, RuntimeError=failed_precondition,
# 단 shutdown 뒤의 RuntimeError는 웹에서 unavailable)
try:
    split.set_view_camera(3, "/OmniverseKit_Front")   # 1x2에는 view 3이 없음
except IndexError as exc:
    print("no such view:", exc)
```

레이아웃 전환 완료 기다리기 (창 이름이 필요할 때):
```python
from carb.eventdispatcher import get_eventdispatcher
from morph.viewport.split import EVENT_LAYOUT_CHANGED, get_split_view


def _on_layout(e):
    print("layout ready:", e["layout"], [(v["view_id"], v["viewport_name"]) for v in e["views"]])


# 반환값을 보관해야 구독이 유지된다 (None을 대입하면 해제)
layout_sub = get_eventdispatcher().observe_event(
    event_name=EVENT_LAYOUT_CHANGED,              # "morph.viewport.layout_changed" (웹 type 아님)
    on_event=_on_layout,
    observer_name="morph.foo.bar layout watcher",
)
get_split_view().set_layout("2x2")
```

포커스 뷰 따라가기 — 이 익스텐션에 **의존 선언 없이** (예: 카메라 조작 대상 추적):
```python
import omni.ext
from carb.eventdispatcher import get_eventdispatcher
from omni.kit.viewport.utility import get_active_viewport, get_viewport_from_window_name


class MyExt(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._viewport_api = get_active_viewport()   # 초기값: 포커스 뷰 = Kit 활성 뷰포트
        self._focus_sub = get_eventdispatcher().observe_event(
            event_name="morph.viewport.focus_changed",
            on_event=self._on_focus,
            observer_name="morph.foo.bar focus watcher",
        )

    def _on_focus(self, e):
        name = e["viewport_name"]
        if name:
            self._viewport_api = get_viewport_from_window_name(name)

    def on_shutdown(self):
        self._focus_sub = None
        self._viewport_api = None
```

웹 클라이언트 (`@nvidia/omniverse-webrtc-streaming-library`의 `AppStreamer`). 전송 형식·`request` 헬퍼는 `morph.stream.messaging` CONTRACT의 웹 예와 같다 (여기서는 그대로 붙여 넣어 단독으로 동작하게 했다):
```js
import { AppStreamer } from "@nvidia/omniverse-webrtc-streaming-library";

const VIEWPORT = "morph.viewport.split";     // 웹 type 접두어 (carb 이벤트 morph.viewport.* 와 다름)
const pending = new Map();                    // id -> { resolve, reject, timer }

class MorphRequestError extends Error {
  constructor(payload) {
    super(payload.message);
    this.code = payload.error;   // "not_found", "invalid_argument", "failed_precondition", "unavailable", "handler_error", ...
    this.payload = payload;
  }
}

function send(envelope) {
  AppStreamer.sendMessage(JSON.stringify({
    event_type: "morph.stream.in",
    payload: { message: JSON.stringify(envelope) },
  }));
}

function request(type, payload = {}, { timeoutMs = 5000 } = {}) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new MorphRequestError({ ok: false, error: "timeout", message: `${type} timed out` }));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    send({ type, v: 1, id, payload });
  });
}

let viewportState = null;   // { layout, focused_view_id, views: [{ view_id, viewport_name, camera_path, rect }], side_ratio }

// 영상 위 좌표 → view_id (rect: 앱 창 기준 0..1, 원점 좌상단. null인 뷰는 판정하지 않음)
// <video>가 object-fit: contain으로 레터박스될 수 있어 실제 영상 영역을 계산한다.
// 반환 widthPx/heightPx는 화면(CSS px) 기준 뷰 크기, videoWidthPx는 영상(=앱 창) 픽셀 기준 뷰 폭.
export function viewAt(videoEl, clientX, clientY) {
  if (!viewportState || !videoEl.videoWidth || !videoEl.videoHeight) return null;
  const box = videoEl.getBoundingClientRect();
  const scale = Math.min(box.width / videoEl.videoWidth, box.height / videoEl.videoHeight);
  const cw = videoEl.videoWidth * scale, ch = videoEl.videoHeight * scale;
  const u = (clientX - box.left - (box.width - cw) / 2) / cw;
  const v = (clientY - box.top - (box.height - ch) / 2) / ch;
  const hit = viewportState.views.find(
    ({ rect: r }) => r && u >= r.x && u < r.x + r.w && v >= r.y && v < r.y + r.h,
  );
  if (!hit) return null;
  const r = hit.rect;
  return { viewId: hit.view_id, widthPx: r.w * cw, heightPx: r.h * ch, videoWidthPx: r.w * videoEl.videoWidth };
}

// AppStreamer.connect({ ..., onCustomEvent })
export function onCustomEvent(raw) {
  const msg = typeof raw === "string" ? JSON.parse(raw) : raw;
  if (msg.event_type !== "morph.stream.out") return;
  const env = JSON.parse(msg.payload.message);
  const p = env.id && pending.get(env.id);
  if (p) {
    pending.delete(env.id);
    clearTimeout(p.timer);
    env.payload.ok === false ? p.reject(new MorphRequestError(env.payload)) : p.resolve(env.payload);
    return;
  }
  if (env.type === `${VIEWPORT}.state`) {   // 푸시: hello 이후, 변경이 있던 프레임마다 (rect 변경 포함)
    viewportState = env.payload;
    console.log("viewport", viewportState.layout, "focus", viewportState.focused_view_id);
  }
}

// 스트림 연결 직후
await request("morph.stream.messaging.hello", {});              // 이후 상태 푸시가 온다
const { ok, ...state } = await request(`${VIEWPORT}.get_state`); // 초기 상태 (푸시와 같은 형태 + ok)
viewportState = state;

await request(`${VIEWPORT}.set_layout`, { layout: "1x2" });      // { ok: true } — 창 준비 완료는 state 푸시로
await request(`${VIEWPORT}.set_view_camera`, { view_id: 1, camera_path: "/OmniverseKit_Top" });
await request(`${VIEWPORT}.focus_view`, { view_id: 1 });

// 뷰마다 카메라 드롭다운: 열 때마다 다시 요청한다 (목록 변경 푸시 없음). 스테이지 없으면 failed_precondition
const { cameras } = await request(`${VIEWPORT}.list_cameras`);   // [{ path, name, builtin }] 사용자 카메라 먼저
// <option value={c.path}>{c.builtin ? `(Kit) ${c.name}` : c.name}</option> → 고르면 set_view_camera { view_id, camera_path: c.path }

// "1+3" 오른쪽 열 폭 슬라이더 (1.5.0). 회신은 적용값(0.1..0.5로 자름). 드래그 중 연속 호출해도 되고
// 재도킹이 끝나면 state 푸시(side_ratio, 새 rect)가 온다. 재도킹 중에는 rect가 null이 될 수 있다.
// <input type="range" min="0.1" max="0.5" step="0.01" id="side">
// sideEl.value = String(viewportState.side_ratio);              // state를 받을 때마다 맞춘다
// sideEl.addEventListener("input", async () => {
//   const { side_ratio } = await request(`${VIEWPORT}.set_side_ratio`, { ratio: Number(sideEl.value) });
//   sideEl.value = String(side_ratio);
// });
const { side_ratio } = await request(`${VIEWPORT}.set_side_ratio`, { ratio: 0.3 });   // { ok: true, side_ratio: 0.3 }
try {
  await request(`${VIEWPORT}.set_side_ratio`, { ratio: true });   // bool은 숫자가 아님
} catch (e) {
  if (e.code !== "invalid_argument") throw e;
}

// 클릭한 뷰로 포커스 (videoEl = 스트리밍 <video>)
// videoEl.addEventListener("pointerdown", (e) => {
//   const hit = viewAt(videoEl, e.clientX, e.clientY);
//   if (hit) request(`${VIEWPORT}.focus_view`, { view_id: hit.viewId });
// });

try {
  await request(`${VIEWPORT}.focus_view`, { view_id: 3 });       // 1x2에는 view 3이 없음
} catch (e) {
  if (e.code === "not_found") console.warn("no view", e.payload.view_id);
  else throw e;
}
```

## 금지 / 주의
- 이 익스텐션이 만든 뷰포트 창을 코드로 직접 닫거나 옮기지 않는다. 레이아웃은 `set_layout`으로만 바꾼다.
- `view_id`는 레이아웃이 바뀌면 다시 매겨진다. 오래 들고 있지 말고 `layout_changed` 이후 다시 조회한다.
- 뷰 개수만큼 렌더 비용이 늘어난다. 스트리밍 앱에서는 앱 창 전체가 한 영상으로 전송되므로 분할된 뷰가 함께 보인다.
- 카메라 이동은 `morph.camera.control` 몫이고, 조작 대상은 포커스 뷰(Kit 활성 뷰포트)다. 포커스 뷰가 아닌 뷰에 카메라 prim을 연결하는 것은 이 익스텐션의 `set_view_camera`로 한다. `morph.camera.control`의 `set_active_camera`로 포커스 뷰 카메라가 바뀌면 `morph.camera.active_changed`와 `morph.viewport.view_camera_changed`가 둘 다 발행된다.
- 추가 창이 닫히면(레이아웃 축소, 사용자가 닫음, shutdown) 그 창의 뷰포트 API(`get_viewport_from_window_name`으로 얻은 객체)는 무효가 된다. 들고 있지 말고 `layout_changed` / `focus_changed` 이후 다시 얻는다.
- 이 익스텐션이 만들지 않은 뷰포트 창(예: Window 메뉴의 `"Viewport 2"`)이 활성화되면 무시한다. 포커스 뷰와 `focus_changed`는 바뀌지 않지만, 그동안 `get_active_viewport()`는 그 창을 돌려주므로 "포커스 뷰 = Kit 활성 뷰포트" 불변식이 일시적으로 성립하지 않는다. 다음 `focus_view`(현재 포커스 뷰와 같은 `view_id`여도 됨, 이때 `focus_changed`는 없음)나 우리 창 클릭으로 다시 맞춰진다. 분할 앱에서는 그런 창을 쓰지 않는다.
- 기본 `Viewport` 창(view 0)을 사용자가 숨겨도 닫힘으로 보지 않는다. 레이아웃·view 0은 그대로이고 카메라 연결도 유지된다.
- gamepad 카메라 제어의 대상 창 전환은 Kit의 omni.ui 포커스 콜백에 맡긴다. `focus_view`가 그 콜백을 일으키지 않는 경우 gamepad 대상이 이전 창에 남을 수 있다.
- **`dockSplitterSize`는 ImGui 전역 스타일이다.** 뷰 사이뿐 아니라 앱의 모든 도킹 분할선(Stage / Property / Console 사이 등)이 같은 두께가 된다. 분할 뷰만 따로 바꾸는 방법은 없다. shutdown 때의 pop은 스타일 스택 맨 위를 되돌리므로, 그 사이 다른 코드가 pop 없이 전역 스타일 변수를 push했다면 그쪽 값이 되돌려진다. 이 설정을 쓰는 앱에서는 다른 익스텐션이 전역 ImGui 스타일을 push한 채로 두지 않는다.
- `hideTabBar=true`면 뷰 창의 탭바가 비활성이라 사용자가 탭을 끌어 뷰 창을 옮기거나 분리할 수 없다 (의도된 동작. 레이아웃은 `set_layout`으로만 바꾼다). 뷰 창의 탭바·여백을 다른 코드에서 바꾸지 않는다 (약 30프레임 안에 다시 덮어씀).

## 호환성 정책 (stable)
- 1.x 동안 다음은 깨지 않는다: 공개 Python API 시그니처와 예외 종류, 이벤트 이름(`morph.viewport.layout_changed` / `focus_changed` / `view_camera_changed`)과 기존 페이로드 필드, 웹 type 이름(`morph.viewport.split.*`)과 요청 스키마·회신 형식, 위 실패 code의 의미, 설정 경로와 기본값의 의미, 용어와 불변식(view 0 = 기본 창, 포커스 뷰 = Kit 활성 뷰포트, 카메라 기준 = `viewport_api.camera_path`).
- minor: API·설정·레이아웃 프리셋 추가, 이벤트·웹 payload 필드 추가 (수신 측은 모르는 필드를 무시한다), 실패 code 추가 (웹은 모르는 code를 일반 실패로 처리한다), 새 웹 type 추가. patch: 내부 수정, 프레임 수 등 보장하지 않는다고 적은 값의 변화.
- 호환 깨지는 웹 payload 변경은 해당 type의 `v`를 올리고 기존 `v`를 한동안 같이 받는다. major 변경은 메인 세션에 먼저 제안한 뒤에만 한다.

## 변경 이력
- 1.5.1 — patch (공개 API·이벤트·웹 type·설정 추가/변경 없음).
  - 의존 버전 범위 명시: `morph.stream.messaging` ^1.0, `morph.perf.monitor` ^1.0 (`extension.toml`. 쓰는 기능이 모두 1.0.0에 있음, perf 1.1.0 시스템 지표는 쓰지 않음)
  - 정정(문서에 맞춤): `side_ratio` 변경 `state` 푸시가 재도킹뿐 아니라 **레이아웃 전환 중에도** 미뤄져 완료 프레임에 나간다. 1.5.0은 다른 레이아웃→`"1+3"` 전환 중에 값이 바뀌면 rect가 모두 null인 state를 바로 보냈다 ("상태 푸시"가 약속한 동작이 아니었음)
  - 정정: 웹 핸들러 처리 도중 shutdown되어 생긴 `RuntimeError`를 `failed_precondition` 대신 `unavailable`로 회신 (실패 code 표의 `unavailable` 의미 그대로)
  - 문서: 설정 `sideRatio`는 `set_side_ratio`가 쓴 값이 프로세스에 남아, 같은 프로세스에서 다시 켜면 마지막 런타임 값으로 시작함을 명시
- 1.5.0 — 기능 추가 (호환 유지). `"1+3"` 오른쪽 열 폭을 실행 중에 바꾸기 (웹 슬라이더용).
  - Python `SplitView.set_side_ratio(ratio) -> float`(0.1..0.5로 잘라 적용값 반환, 숫자 아님·bool·NaN·inf는 `ValueError`), `SplitView.get_side_ratio() -> float` 추가. 논리 전용 모드에서도 저장·반환
  - 웹 `morph.viewport.split.set_side_ratio` 추가: 요청 `{ ratio: number }` → `{ ok: true, side_ratio }`, 잘못된 값은 `invalid_argument`
  - 웹 `state` 푸시와 `get_state` 회신에 `side_ratio`(number, 항상 있음) 추가. 값이 바뀌면 `state` 재푸시 (`"1+3"` 창 모드는 재도킹 완료 때)
  - `"1+3"`(창 모드)에서 값이 바뀌면 창을 다시 만들지 않고 view 1~3만 재도킹 (뷰포트 API 객체·카메라·포커스 유지, `layout_changed` 없음). 한 프레임의 여러 호출은 마지막 값만, 전환·재도킹 중 호출은 끝난 뒤 최신 값으로. 규칙은 "오른쪽 열 폭 바꾸기"
  - **설정 `sideRatio` 읽는 시점 변경**: 1.4.0까지는 레이아웃 전환 시작 때만 읽었다. 이제 실행 중 값과 양방향으로 맞춘다 — `set_side_ratio`가 설정에 쓰고, 설정을 직접 바꾸면 다음 프레임에 받아들여 이미 `"1+3"`이면 재도킹한다. 설정 경로·기본값·잘못된 값 처리는 같다
  - 성능 지표: `layout_switches`는 재도킹을 세지 않고, `layout_step_ms`는 재도킹 프레임을 포함
  - carb 이벤트 이름·페이로드, 기존 API·웹 type·요청 스키마는 변경 없음. 웹 `v`는 1 그대로 (필드·type 추가)
- 1.4.0 — 기능 추가 (호환 유지). 웹에서 뷰마다 카메라를 고를 수 있게 카메라 목록 공개.
  - Python `SplitView.list_cameras() -> list[CameraInfo]`, `CameraInfo`(frozen: `path`, `name`, `builtin`, `to_dict()`) 추가. 스테이지 없으면 `[]`
  - 웹 `morph.viewport.split.list_cameras` 추가: 요청 `{}` → `{ ok: true, cameras: [{ path, name, builtin }] }`, 스테이지 없으면 `failed_precondition`
  - 규칙("카메라 목록"): `Usd.PrimRange` 기본 predicate로 `UsdGeom.Camera` 전부, instance proxy·비활성·class 아래 제외, Kit 기본 카메라 넷은 `builtin`. 사용자 카메라 경로 문자열순 먼저, 기본 카메라 Persp/Top/Front/Right 순 나중. 목록의 경로는 모두 `set_view_camera` 검증 통과
  - 카메라 목록 변경 푸시는 없음 (필요할 때 다시 요청). 요청 시에만 순회
  - 성능 지표 `morph.viewport.split.list_cameras_ms`(scope) 추가
  - 기존 API·이벤트·웹 type·설정은 변경 없음
- 1.3.1 — 문서 정정 (동작 변경 없음). 설정 `sideRatio` 설명에 기존 코드 동작을 명시: 유한하지 않은 값(NaN, inf, -inf)은 숫자 아님과 같이 경고 후 기본값 0.25, 설정 없음은 경고 없이 기본값
- 1.3.0 — 기능 추가 (호환 유지). 큰 뷰 + 작은 뷰 셋 레이아웃.
  - 레이아웃 `"1+3"` 추가 (`LAYOUTS`, 웹 `set_layout` enum, `defaultLayout`). 배치는 "레이아웃별 뷰 배치". 레이아웃 이름이 모두 `"<행>x<열>"`이라는 가정은 더 이상 성립하지 않는다 (문자열을 해석하던 쪽은 `LAYOUTS`·뷰 개수로)
  - 설정 `sideRatio` 추가 (float, 기본 0.25, 0.1..0.5로 자름, 레이아웃 전환 시작 때 읽음)
  - 전환 모델 명시: 창 슬롯 재사용, 자리·비율이 바뀐 창만 재도킹(뒤 번호 포함), 같은 뷰 개수 전환(`"2x2"` ↔ `"1+3"`)은 재도킹만. 격자 레이아웃 동작은 1.2.0과 같음
  - 웹 state outbound 스키마: `rect`가 object면 `x`,`y`,`w`,`h` 필수 (보내는 값은 원래 항상 네 필드 — 송신 측 변화 없음)
  - 문구 정정 (동작 변경 없음): rect의 뷰 하나 읽기 예외는 경고 없이 그 뷰만 null(경고 1회는 전체 실패일 때), 재푸시 비교 기준은 마지막으로 만든 state payload
  - carb 이벤트 이름·페이로드, 공개 Python API 시그니처, 웹 type·`v`는 변경 없음
- 1.2.0 — 기능 추가 (호환 유지). 웹에서 뷰의 화면 위치를 알 수 있게.
  - 웹 `morph.viewport.split.state` 푸시와 `get_state` 회신의 각 뷰에 `rect` 추가: `{x, y, w, h}` (앱 메인 창 기준 0..1, 원점 좌상단) 또는 `null`. 정의·null 조건·재푸시 조건은 "뷰 화면 영역". outbound 스키마에서 `rect`는 필수(`object | null`)
  - 상태 푸시 조건 추가: 전환 중이 아닐 때 약 30프레임마다(전환 완료 직후엔 약 5프레임 뒤) rect를 확인해 0.002 이상 바뀌면 `state` 1회 (분할선 드래그, 앱 창 크기 변경)
  - "레이아웃별 뷰 배치" 표 추가 (동작 변경 없음, 1.1.0까지와 같은 배치)
  - carb 이벤트 페이로드·공개 Python API·설정은 변경 없음. 웹 `v`는 1 그대로 (필드 추가)
- 1.1.0 — 기능 추가 (호환 유지). 분할 뷰 사이 간격 줄이기.
  - 설정 추가: `hideTabBar`(bool, 기본 false: 뷰 창 탭바 숨김+비활성, view 0 포함·1x1에서도 유지), `viewPadding`(float, 기본 설정 없음: 뷰 창 `padding_x/y`), `dockSplitterSize`(float, 기본 설정 없음: ImGui 전역 `DockSplitterSize` push, 앱 전체 적용). 적용 시점·실패 처리·shutdown 복원은 "뷰 창 꾸밈"
  - 선택 의존 추가: `omni.kit.renderer.imgui` (`omni.kit.imgui`, 없으면 `dockSplitterSize`만 경고 1회 후 생략)
  - 공개 Python API·이벤트·웹 메시지 변경 없음. 설정을 주지 않으면 1.0.0과 동작이 같다.
- 1.0.0 — **호환 깨짐 (stable 확정, `morph.stream.messaging` 1.0.0 대응)**.
  - 웹 type 이름 변경 (stream 1.x type 규칙 `<owner ext-id>.<name>`): `morph.viewport.set_layout` / `set_view_camera` / `focus_view` / `get_state` / `state` → `morph.viewport.split.set_layout` / `set_view_camera` / `focus_view` / `get_state` / `state`.
  - carb 이벤트 이름·페이로드는 **바뀌지 않음** (`morph.viewport.layout_changed` 등). 이벤트와 웹 type이 다른 이름 공간임을 명시.
  - 회신 형식 통일: `get_state` 회신이 `{ ok: true, layout, focused_view_id, views }` (0.x는 `ok` 없음 — stream 1.x에서는 `ValueError`가 됐을 것). 예상된 실패는 예외 대신 같은 type·id로 `{ ok: false, error, message }` 회신: `not_found`(+`view_id`), `invalid_argument`, `failed_precondition`, `unavailable`. 0.x의 `morph.stream.error`(`handler error: <예외>`) 회신은 없어짐.
  - 상태 푸시는 웹 클라이언트가 연결된 동안(hello 이후)만 보낸다는 점 명시 (동작 변경 없음).
  - 공개 Python API·설정·이벤트는 변경 없음. 호환성 정책 추가.
- 0.1.2 — 내부 수정. 공개 API 시그니처·이벤트·설정 변경 없음.
  - 수정: `renderer.asyncInit=true` 앱에서 렌더러 준비 전(app ready 직후, startup `defaultLayout`) 추가 뷰포트 창을 한 프레임에 여러 개 만들어 RTX가 크래시하던 문제. 기본 뷰포트가 렌더한 프레임을 받은 뒤에 창을 만들고(렌더 준비 게이트, 약 120초 타임아웃 시 창 준비 실패 정책), 창은 프레임당 하나씩 만든다. 그만큼 `layout_changed`가 늦어질 수 있음
  - 동작 명확화/수정: `focus_view`에 이미 포커스인 `view_id`를 줘도 Kit 활성 뷰포트가 그 창이 아니면 다시 포커스를 준다 (`focus_changed` 없음). 금지/주의의 외부 뷰포트 창 서술과 맞춤
  - 웹 상태 송신(`morph.viewport.state`) 실패는 경고 로그로만 남기고 업데이트 루프·이벤트 디스패치로 새지 않음
  - CONTRACT에 없던 `SplitView.state_payload()`를 내부(`_state_payload`)로 돌림 (사용처 없음)
- 0.1.1 — 기본 구현. 공개 API·이벤트·설정 변경 없음. 동작 명확화:
  - 이벤트는 API 반환 뒤 이 익스텐션의 다음 업데이트 처리에서 발행. 포커스 추종 쪽의 한 프레임 지연 명시
  - 전환 중 포커스 변경은 완료 시 한 번으로 합치고, 전환 중 Kit 활성 뷰포트 변경은 반영하지 않음 (완료 시 포커스 뷰로 되돌림)
  - 전환 중에도 기존 뷰의 외부 카메라 변경을 감지하고 완료 때 덮어쓰지 않음
  - 전환 중 새 뷰의 카메라 설정은 `layout_changed`에만 반영
  - 기본 창이 나중에 생기면 창 모드로 전환하며, view 0 카메라는 창의 값을 따름
  - `set_view_camera`의 빈 문자열/문자열이 아닌 경로는 모드와 관계없이 `ValueError`
  - `focusOnClick=false`의 동작(되돌림) 명시, `view_count`는 바뀔 때만 기록
  - 금지/주의: 닫힌 추가 창의 뷰포트 API 무효, 외부 뷰포트 창 무시 정책, view 0 숨김, gamepad 한계
  - 창 준비 실패 정책(재시도 없이 error 1회, 추가 창 정리 후 1x1, `layout_changed("1x1")`), 포커스 실패는 로그만 남김
  - 수정: Kit이 돌려주는 view 0 창(weakref.proxy)을 `dock_in` 인자와 `active_window` 대입에 그대로 써서 실제 앱에서 도킹·포커스가 실패하던 문제
- 0.1.0 — 최초 초안
