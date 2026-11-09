# morph.annotation.overlay CONTRACT

> 다른 익스텐션 에이전트가 이 익스텐션을 쓸 때 읽는 **유일한** 문서. 여기 없는 것은 내부 구현이므로 의존하지 않는다.

- 버전: 0.4.1
- 상태: draft
- 담당 에이전트: ext-annotation
- 의존: morph.stream.messaging, morph.perf.monitor, omni.usd, omni.ui, omni.ui.scene, omni.kit.viewport.registry (optional), omni.kit.viewport.utility (optional), omni.kit.menu.utils (optional)
- 의존 버전 (`extension.toml` 범위): morph.stream.messaging `1.0` = ^1.0 (등록·회신·`is_connected`), morph.perf.monitor `1.0` = ^1.0 (`scope`만 사용)
- 파이썬 패키지: 면 계산(`pick_face`)은 **numpy**를 쓴다. Kit 기본 `omni.kit.pip_archive`가 제공하며 따로 선언하지 않는다. numpy를 못 쓰면 면 결과가 모두 `kind: "point"`가 된다 (경고 로그 1회, 예외 없음)

## 목적
복잡한 모델 위의 위치에 **라벨·설명**, **inlet / outlet 화살표**, **절단면(단면 위치) 표시**를 뷰포트 안에 3D로 그린다. 분할 뷰 전체와 스트리밍 영상에 보인다.
주석은 Python·웹·데스크톱 편집 창에서 만들고 고칠 수 있다. 위치는 "모델 클릭(pick) + 숫자 보정"으로 정한다. 클릭은 **위치(와 표면 방향)를 얻는 데만** 쓰고 맞은 prim에 묶지 않는다.
**면 선택**: 클릭한 점이 놓인 평면 영역 전체(같은 평면에 이어진 면 묶음, 예: 플랜지 끝면 → 원판 전체)를 계산해 뷰에서 강조하고 그 중심 좌표·법선·면적을 보여 준다. 입구·출구·단면을 면 중심에 정확히 놓는 데 쓴다. 면도 월드 좌표 값일 뿐이며 prim에 묶이지 않고 저장되지 않는다.
주석은 **USD prim을 만들지 않고 prim 계층과 무관하다**. 모든 위치는 월드 좌표(스테이지 단위)이며, prim 경로로 찾거나 prim에 붙이지 않는다. 모델 USD를 고치지 않고, 장면 파일 옆 JSON 파일(사이드카)에 저장한다.

## 언제 쓰나 / 쓰지 않나
- 쓴다: 모델의 입구·출구, 단면 위치, 부위 설명을 화면에 표시할 때. 외부 시스템이 주석을 넣고 빼고 보이게 할 때 (Python·웹). 사용자가 뷰를 클릭해 주석 위치를 정할 때 (`pick`). 클릭한 면의 중심·법선·면적이 필요할 때 (`pick_face`, `select_face`)
- 쓰지 않는다: 모델 편집, prim 메타데이터 (→ `omni.usd`), 카메라 이동 (→ `morph.camera.control`), 실제 단면 절단 렌더링(지오메트리를 자르는 것. 이 익스텐션은 **위치 표시만** 한다)

## 주석 모델
주석 하나 = dict (JSON과 Python API 공통). 좌표·벡터는 `[x, y, z]` 월드 좌표, 스테이지 단위(`metersPerUnit` 환산 없음), 스테이지 up 축 그대로.

공통 필드:
| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `id` | str | 아니오 (add·set_all·파일에서 없거나 `null`이면 생성) | `"<kind>_<n>"` (n = 1부터 비어 있는 가장 작은 수) | 정규식 `[A-Za-z0-9_-]{1,64}`, 한 목록에서 유일 |
| `kind` | str | 예 | | `"label"` / `"inlet"` / `"outlet"` / `"plane"` |
| `label` | str | 아니오 | `""` | 화면에 보이는 짧은 이름 (최대 128자) |
| `description` | str | 아니오 | `""` | 설명 (최대 1000자). 라벨 아래 작은 글씨로 표시 (화면에는 약 40자에서 줄바꿈, 최대 2줄, 넘으면 `…`. `showDescription` 설정이 `"never"`면 숨김). 저장 값은 그대로 |
| `color` | `[r, g, b]` 각 0..1 | 아니오 | kind별 기본색 | 아래 표. 범위 밖이면 `ValueError` |
| `visible` | bool | 아니오 | `true` | 개별 표시 여부 |

kind별 필드:
| kind | 필드 | 그리는 모양 | 기본색 |
|---|---|---|---|
| `label` | `position` (필수) | 점 + 화면 오른쪽 위로 꺾인 지시선 + 라벨 상자 (label·description이 모두 비면 점만) | 흰색 계열 `[0.95, 0.95, 0.95]` |
| `inlet` | `position` (필수, 입구 중심), `direction` (필수, 흐름 방향 — 모델 안쪽으로), `size` (선택, 화살표 길이 > 0, 기본 1.0) | `position`을 끝점으로 `direction` 방향을 가리키는 화살표(선 + 원뿔 머리, 모델 밖에서 들어옴) + 화살표 꼬리(바깥쪽 끝)에 라벨 | 파랑 `[0.2, 0.55, 1.0]` |
| `outlet` | `position` (필수, 출구 중심), `direction` (필수, 흐름 방향 — 모델 밖으로), `size` (선택, > 0, 기본 1.0) | `position`에서 시작해 `direction` 방향으로 나가는 화살표 + 화살표 머리 끝에 라벨 | 주황 `[1.0, 0.45, 0.1]` |
| `plane` | `origin` (필수), `normal` (필수), `size` (선택 `[w, h]` 각 > 0, 기본 `[2.0, 2.0]`), `up` (선택, 0 아닌 벡터, 사각형 세로(h) 방향 힌트. 없으면 그릴 때 스테이지 up 축 — normal과 평행하면 다른 축) | `origin` 중심, `normal`에 수직인 반투명 사각형(양면) + 테두리 + normal 화살표(길이 0.35 × min(w, h)) + 사각형 위쪽 변 가운데에 라벨 | 노랑 `[1.0, 0.85, 0.2]` |

- `direction`·`normal`은 0 벡터가 아니면 길이는 상관없다 (저장은 준 값 그대로, 그릴 때 정규화).
- 숫자는 `int`/`float`만 (`bool`·문자열 숫자 거부). 벡터는 길이 3(plane `size`는 2)인 리스트·튜플(또는 같은 길이의 시퀀스, 예: `Gf.Vec3d`)이며 저장·반환은 float 리스트.
- 잘못된 주석(필수 필드 없음, 숫자 아님, NaN/inf, 0 벡터, 크기 ≤ 0, 모르는 kind, 문자열 길이 초과, id 형식·중복)은 API에서 `ValueError`(메시지에 필드·이유), 파일 읽기에서는 그 항목만 건너뛰고 경고(이유 포함)한다.
- 모르는 추가 필드는 보존한다 (읽고 다시 저장해도 남음, 깊은 사본). 다른 도구가 필드를 더 넣을 수 있다. 추가 필드도 JSON으로 직렬화할 수 있어야 한다 (NaN·임의 객체면 `ValueError`). 다른 kind의 필드(예: label의 `direction`)도 추가 필드로 취급한다. 반환 dict의 키 순서는 공통 필드 → kind 필드 → 추가 필드(원래 순서).
- 라벨 글자는 화면 크기 고정(거리와 관계없음, 기본 14px × `textScale`, 작은 뷰포트는 × 0.8, 카메라를 향함). 화살표·사각형은 월드 크기 (선 두께만 화면 px). 세부는 "그리기".
- 카메라 뒤 주석은 그리지 않는다: 주석의 기준점(`position` / `origin`)이 원근 카메라 뒤에 있으면 그 주석 전체를 숨긴다 (카메라가 움직일 때마다 다시 판정). 직교 카메라는 숨기지 않는다.
- 모델에 가려도 보인다: `omni.ui.scene`에는 깊이 테스트 옵션이 없고, 렌더 영상 위에 UI 레이어로 그려지므로 모델 지오메트리에 가려지지 않는다 (항상 위). 주석끼리 겹칠 때의 앞뒤 순서는 보장하지 않는다.

## 사이드카 파일
- 경로: 장면 파일과 같은 폴더, `<장면 파일 이름에서 확장자를 뺀 것><sidecarSuffix>` (기본 `.annotations.json`). 예: `pipe_manifold.usda` → `pipe_manifold.annotations.json`
- 장면 파일 = 스테이지 루트 레이어의 로컬 파일 경로 (`realPath`). 익명 레이어(새 장면)·`omniverse://` 등 로컬 파일이 아닌 레이어면 사이드카 없음 (`current_file` `None`).
- 형식: `{ "version": 1, "annotations": [ 주석, ... ] }`. UTF-8 (읽기는 BOM 허용, 저장은 BOM 없음·한글 이스케이프 안 함). 저장 시 들여쓰기 2칸, 주석 순서 유지, 기본값을 채운 형태로 쓴다.
- `version`이 없으면 1로 본다. 양의 정수가 아니거나 1보다 크면 읽지 않는다 (`load()`는 `ValueError`, 자동 읽기는 경고 후 빈 목록).
- 파일 속 항목에 `id`가 없으면 생성한다 (파일에 명시된 id와 겹치지 않게). `maxAnnotations`를 넘는 뒤쪽 항목은 건너뛰고 경고.
- 저장은 같은 폴더의 임시 파일에 쓰고 `os.replace`로 교체한다 (중간에 실패해도 기존 파일이 깨지지 않고 임시 파일은 지운다). 기존 파일이 있으면 그 권한을 임시 파일에 복사한 뒤 교체한다 (새 파일은 POSIX `0644`). 권한 복사는 최선 노력이며 실패해도 저장은 계속한다.
- `autoLoad`가 true면 장면이 열릴 때(`omni.usd` stage `OPENED`, 누가 열었든) 기존 주석을 지우고 사이드카를 읽는다. 파일이 없거나 깨졌으면 빈 목록 (깨진 경우 경고). 새 장면·파일 아닌 장면이면 빈 목록이고 `current_file`은 `None`. 장면이 닫히면(`CLOSED`) 목록을 비운다. 익스텐션 시작 시 이미 열린 장면이 있으면 그 사이드카를 한 번 읽는다.
- `autoLoad`가 false면 장면 열기·닫기가 목록을 바꾸지 않는다.
- 자동 저장은 하지 않는다. `save()`(또는 웹 `save`)를 불러야 파일에 쓴다.

## 공개 Python API
import: `from morph.annotation.overlay import get_overlay, AnnotationOverlay, KINDS, FACE_KINDS, EVENT_CHANGED, EVENT_SELECTION_CHANGED, EVENT_FACE_SELECTED, MSG_STATE`

- `AnnotationOverlay`는 **타입 표기용**이다 (`ov: AnnotationOverlay | None = get_overlay()`). 직접 만들지 않는다 — 장면·웹·그리기와 연결된 것은 `get_overlay()` 싱글턴뿐이다.
- 표시 옵션(`textScale`, `showDescription`)은 Python API가 아니라 carb 설정으로 바꾼다 ("설정", "사용 예"). 웹은 `set_options`.

모든 메서드는 **메인 스레드 전용**이다.

| 심볼 | 시그니처 | 설명 | 주의 |
|---|---|---|---|
| `get_overlay` | `() -> AnnotationOverlay \| None` | 싱글턴 | startup 이후 유효, shutdown 이후 `None` |
| `KINDS` | `tuple[str, ...]` | `("label", "inlet", "outlet", "plane")` | minor에서 늘어날 수 있다 |
| `AnnotationOverlay.list` | `() -> list[dict]` | 모든 주석 (순서 유지, 기본값을 채운 깊은 사본) | |
| `AnnotationOverlay.get` | `(ann_id: str) -> dict \| None` | 주석 하나 (사본) | |
| `AnnotationOverlay.add` | `(ann: dict) -> str` | 추가하고 id 반환 | 검증 실패·`maxAnnotations` 초과면 `ValueError` |
| `AnnotationOverlay.update` | `(ann_id: str, **fields) -> dict` | 필드 일부 교체, 새 값(사본) 반환. 값이 `None`인 필드는 지운다 (선택 필드는 기본값으로, 추가 필드는 삭제). `kind`·`id`는 못 바꾼다 (같은 값은 무시) | 없는 id면 `KeyError`, 검증 실패·`kind`/`id` 변경·필수 필드 `None`이면 `ValueError` (실패 시 변경 없음) |
| `AnnotationOverlay.remove` | `(ann_id: str) -> None` | 삭제 | 없는 id면 `KeyError`. 선택된 주석이면 선택 해제 |
| `AnnotationOverlay.clear` | `() -> None` | 모두 삭제 | 선택 해제 |
| `AnnotationOverlay.set_all` | `(anns: list[dict]) -> None` | 목록 통째 교체 (id 없는 항목은 생성) | 하나라도 잘못되면·id 중복·`maxAnnotations` 초과면 `ValueError`, 변경 없음. 선택된 id가 새 목록에 없으면 선택 해제 (있으면 유지) |
| `AnnotationOverlay.visible` | 프로퍼티 `-> bool` | 전체 표시 여부 (`visible` 설정) | |
| `AnnotationOverlay.set_visible` | `(visible: bool) -> None` | 전체 표시 on/off (데이터는 유지). `visible` 설정도 함께 바뀐다 | 값이 같으면 아무것도 안 함 (이벤트 없음) |
| `AnnotationOverlay.current_file` | 프로퍼티 `-> str \| None` | 지금 장면의 사이드카 경로 (파일이 아직 없어도 경로). 파일 장면이 아니면 `None` | |
| `AnnotationOverlay.load` | `(path: str \| None = None) -> int` | 사이드카 읽기 (`None`이면 `current_file`). 읽은 개수 반환 | 목록을 읽은 것으로 교체. 잘못된 항목은 건너뜀(경고). 파일 없음은 0개 (목록 비움, 예외 없음). JSON 깨짐·형식 오류·`version` 초과는 `ValueError`, 읽기 실패는 `OSError` (둘 다 기존 목록 유지). `path`도 `current_file`도 없으면 `RuntimeError` |
| `AnnotationOverlay.save` | `(path: str \| None = None) -> str` | 사이드카에 저장, 쓴 절대 경로 반환 | `path`도 `current_file`도 없으면 `RuntimeError`. 쓰기 실패(폴더 없음 등)는 `OSError`. 임시 파일에 쓴 뒤 교체 |
| `AnnotationOverlay.selected_id` | 프로퍼티 `-> str \| None` | 선택된 주석 id (없으면 `None`) | 선택은 저장하지 않는다 (사이드카에 안 씀) |
| `AnnotationOverlay.select` | `(ann_id: str \| None) -> None` | 주석 선택 (`None` = 해제). 선택된 주석은 모든 뷰포트에서 강조 | 없는 id면 `KeyError` (변경 없음). 같은 값이면 아무것도 안 함 (이벤트 없음). 선택된 주석이 삭제되거나(`remove`·`clear`·`set_all`에서 빠짐) 목록이 다시 읽히면(`load`, 장면 열기·닫기) 자동 해제 |
| `AnnotationOverlay.pick` | `(viewport_name: str, x: float, y: float) -> Awaitable[dict \| None]` | 뷰포트 렌더 이미지의 정규화 좌표(0..1, 원점 왼쪽 위) 아래 모델 표면의 월드 위치. 맞으면 `{"position": [x, y, z], "normal": [x, y, z] \| None}`, 빈 곳이면 `None` | 아래 "pick". **호출 즉시** 나는 예외는 인자 오류 두 가지뿐: `x`·`y`가 숫자 아님·NaN·0..1 밖이면 `ValueError`, 뷰포트 창이 없으면 `KeyError` (shutdown 뒤에도 같음). 그 밖의 실패는 모두 **반환된 Future**에 담겨 await할 때 난다: 렌더 해상도 없음·쿼리 거부(`request_query`가 `False` 또는 `None` — hydra texture 없음)·쿼리 호출 오류·shutdown 뒤 호출이면 `RuntimeError` (바로 실패, 5초 기다리지 않음), 5초 안에 응답 없으면 `TimeoutError`, 대기 중 shutdown으로 취소되면 `asyncio.CancelledError` |
| `AnnotationOverlay.pick_face` | `(viewport_name: str, x: float, y: float) -> Awaitable[dict \| None]` | `pick`과 같은 쿼리로 맞힌 점의 **면**을 계산한다. 맞으면 `{"position": [x, y, z], "face": Face}`, 빈 곳이면 `None` | 아래 "면 (Face)". 예외 규칙은 `pick`과 같다 (호출 즉시 `KeyError`·`ValueError`만, 나머지는 Future). **선택하지 않는다** (`select_face`는 호출자가). 계산할 수 없는 prim이면 `kind: "point"` (예외 아님). 큰 Mesh(면 20만 이상)를 처음 클릭하면 메시 데이터를 **작업 스레드**에서 만들고 끝난 뒤 메인 스레드에서 면을 계산해 Future를 정한다 (그 동안 프레임은 계속 돈다, 수 초 걸릴 수 있음. 5초 제한은 쿼리에만 적용). 대기 중 shutdown이면 취소(`CancelledError`) |
| `AnnotationOverlay.selected_face` | 프로퍼티 `-> dict \| None` | 선택된 면 (Face 사본) | 저장하지 않는다 (사이드카에 안 씀) |
| `AnnotationOverlay.select_face` | `(face: dict \| None) -> None` | 면 선택 (`None` = 해제). 모든 뷰포트에서 외곽선·중심 십자·정보 상자로 강조 | 모양이 틀린 Face면 `ValueError` (변경 없음. 알려진 키만 사본으로 보관). 같은 값이면 아무것도 안 함 (이벤트 없음). 주석 선택(`select`)과 독립. 장면을 열거나 닫으면 자동 해제 |
| `AnnotationOverlay.is_picking` | 프로퍼티 `-> bool` | 편집 창 [뷰에서 위치 찍기] 또는 [면 선택]이 대기 중(클릭 대기 또는 그 쿼리 대기)이면 `True` | 편집 창이 없거나(스트리밍 앱) 아직 안 열었으면 항상 `False`. `pick()`·`pick_face()` 호출과는 무관 |
| `AnnotationOverlay.cancel_pick` | `() -> None` | 편집 창의 대기 중인 [뷰에서 위치 찍기]·[면 선택]을 취소. 모든 뷰포트의 Kit 클릭 선택(Selection·ObjectClick 레이어)이 찍기 전 상태로 돌아온다 | 대기 중이 아니면 아무것도 안 함. `pick()`·`pick_face()`가 돌려준 Future는 건드리지 않는다 |
| `EVENT_CHANGED` | `str` 상수 `"morph.annotation.changed"` | | |
| `EVENT_SELECTION_CHANGED` | `str` 상수 `"morph.annotation.selection_changed"` | | |
| `EVENT_FACE_SELECTED` | `str` 상수 `"morph.annotation.face_selected"` | | |
| `FACE_KINDS` | `tuple[str, ...]` | `("planar", "cylinder_side", "sphere", "polygon", "point")` | minor에서 늘어날 수 있다 (모르는 kind는 `point`처럼 다룬다) |
| `MSG_STATE` | `str` 상수 `"morph.annotation.overlay.state"` | 웹 푸시 type | 웹 구현 참고용 (구독은 웹 쪽) |

### pick
- `viewport_name`은 뷰포트 **창 제목**이다 (예: `"Viewport"`, 분할 뷰가 만든 창 제목). `omni.kit.viewport.utility.get_viewport_from_window_name`으로 찾는다 (빈 문자열·없는 창·utility 없음은 `KeyError`).
- 위치는 `viewport_api.request_query`(렌더러 깊이 쿼리) 결과다. 픽셀 = `(int(x × 폭), int(y × 높이))` (렌더 해상도 기준, 끝은 폭-1·높이-1로 자름).
- `normal`: 쿼리에는 normal이 없어서 이웃 픽셀 2개(+2px x, +2px y. 가장자리면 -2px)를 함께 쿼리한다. 세 점이 **같은 prim**에 맞으면 두 변의 외적으로 단위 normal을 만들고 **카메라 쪽**을 향하게 뒤집는다(원근: 점→카메라, 직교: 카메라 뒤쪽 축). 이웃이 빗나가거나 다른 prim이거나 세 점이 한 직선이면 `None`. 모서리 근처에서는 기울어질 수 있다 (근사).
- 맞은 prim 경로는 내부 비교에만 쓰고 반환·저장하지 않는다. 주석을 만들지도 않는다 (값을 받아 `add`/`update`는 호출자가).
- 반환 awaitable은 `asyncio.Future`다 (메인 스레드 이벤트 루프). 결과는 쿼리 콜백 다음 루프 반복에 정해진다 (보통 1~3 프레임). 시작부터 실패한 경우(위 `RuntimeError`)는 이미 예외가 설정된(`done()`) Future를 돌려준다.
- 이웃 픽셀 쿼리가 거부되면(`False`/`None`) 위치만 돌려주고 `normal`은 `None`.
- 모델에 가려진 주석 그리기와 무관하게 **렌더된 지오메트리**만 맞는다 (주석 도형은 쿼리에 안 잡힘).

## 면 (Face)
`pick_face`가 돌려주고 `select_face`·이벤트·웹 `state`가 쓰는 dict. 모두 **월드 좌표, 스테이지 단위**. JSON으로 그대로 보낼 수 있다.

```json
{ "kind": "planar", "center": [0.0, 0.0, 1.0], "normal": [0.0, 0.0, 1.0], "area": 4.0, "size": [2.0, 2.0],
  "outline": [[-1.0, -1.0, 1.0], [1.0, -1.0, 1.0], [1.0, 1.0, 1.0], [-1.0, 1.0, 1.0]] }
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `kind` | str | 아래 표 (`FACE_KINDS`) |
| `center` | `[x, y, z]` | 면 중심 (kind별 정의는 아래 표) |
| `normal` | `[x, y, z]` 단위 벡터 | 면 법선. **카메라 쪽**(맞힌 점에서 카메라로 가는 쪽, 직교 카메라는 카메라 뒤쪽 축)을 향한다. 바깥에서 본 면이면 바깥쪽이다. 카메라를 모르면 해석 gprim은 표면 바깥쪽, Mesh는 다각형 감김 방향 (`pick_face`는 항상 카메라를 안다) |
| `area` | float ≥ 0 | 면적 (스테이지 단위²) |
| `size` | `[w, h]` 각 ≥ 0 | 면 평면 안의 외곽 크기 (바운딩 사각형). 축은 **같은 normal로 `up` 없이 그린 `plane` 주석의 가로·세로** (스테이지 up 축 기준, "그리기"의 plane 축과 같음). 그래서 `plane` 주석 `size`로 그대로 쓸 수 있다 (편집 창은 × 1.2) |
| `outline` | `[[x, y, z], ...]` 최대 256점 | 강조용 경계 폴리라인. **닫힌 고리**: 마지막 점이 첫 점으로 이어진다 (첫 점을 끝에 반복하지 않음). 한 직선 위의 중간 점은 뺀다. 256점을 넘으면 고르게 솎는다. 고리가 여러 개(구멍 있는 면)면 둘레가 가장 긴 고리 하나만. 없으면 `[]` |

| kind | 대상 | center | area | size | outline |
|---|---|---|---|---|---|
| `"planar"` | Mesh의 평면 묶음 (2개 이상 다각형), Cube의 맞힌 면, Cylinder·Cone의 끝 원판 | 면적 가중 중심 (원판은 원 중심) | 묶음 면적 합 (원판 πr², 비균등 스케일이면 타원 면적) | 외곽 바운딩 크기 | 묶음 경계 (원판은 64점 원, Cube는 4점) |
| `"cylinder_side"` | Cylinder 옆면 | 맞힌 점에서 축에 내린 수선의 발 (높이 범위로 자름) | **직단면 둘레 × 모선 길이** (원통이면 2πrh. 비균등 스케일·전단이면 직단면이 타원 — 둘레는 Ramanujan 근사) | `[직단면 긴 지름, 모선 길이]` (원통이면 `[2r, h]`) | center를 지나는 직단면(축에 수직인 단면) 둘레 64점 — 맞힌 점을 지난다 (center가 잘리지 않았을 때) |
| `"sphere"` | Sphere | 구 중심 | 4πr² (비균등 스케일이면 타원체 근사) | `[2r, 2r]` | 맞힌 점 둘레의 작은 원 64점 (구 위, 중심각 15°) |
| `"polygon"` | 평면 묶음이 맞힌 다각형 하나뿐인 Mesh (곡면 메시), 맞힌 다각형이 퇴화 면인 Mesh, **크기 상한을 넘는 Mesh** (아래 "성능". 묶음 없이 맞힌 다각형만) | 그 다각형의 면적 가중 중심 | 그 다각형 면적 | 외곽 바운딩 크기 | 그 다각형 |
| `"point"` | 그 밖의 경우: Capsule·Cone 옆면·곡선·점·PointInstancer, 맞힌 prim을 못 찾음, 맞힌 점이 그 prim 기하에서 멀리 떨어짐, 계산 실패 | 맞힌 점 | 0 | `[0, 0]` | `[]` |

- `point`의 `normal`: `pick`의 normal이 있으면 그것, 없으면 카메라 쪽 방향.
- Mesh 평면 묶음: 맞힌 점에서 가장 가까운 다각형(시작 다각형)의 평면을 기준으로, 법선 차이 1° 이내이고 그 평면 위(거리 허용 = 메시 월드 bbox 대각선 × 1e-4)인 다각형을 후보로 한다. 묶음 = 후보 중 **시작 다각형에서 후보끼리 변으로 이어진 것만** (flood fill). 같은 높이의 평면이라도 후보가 아닌 면(굴곡 등)을 거쳐서만 이어지면 따로다. 기준이 시작 다각형의 평면이라 완만한 곡면이 끝없이 이어지지 않는다. 정점이 같은 위치면 인덱스가 달라도 이웃으로 본다 (분리 정점 메시도 묶임). 다각형의 감김 방향이 섞여 있어도 된다. 한 다각형만 남으면 `polygon`이다 (사각형 6개로 된 상자 메시의 한 면도 `polygon`, 값은 같음).
- 퇴화 면: 두께(2 × 면적 / 가장 긴 변)가 거리 허용 이하인 다각형(면적 0, 세 점이 한 직선, 가는 조각 삼각형 — 법선이 불안정)은 **각도로 판정하지 않고 평면 거리로만** 판정한다 (모든 정점이 그 평면 위면 묶음에 들어감). 그래서 그런 면이 평면을 둘로 나누지 않는다. 퇴화 면은 시작 다각형이 되지 않는다 (가까운 다른 다각형을 먼저 고른다). 좌표가 NaN·inf인 정점을 쓰는 다각형은 무시한다 (나머지는 정상 계산).
- 해석 gprim(Cube·Cylinder·Cone·Sphere)은 지역 좌표에서 계산하고 전체 변환 행렬(이동·회전·비균등 스케일, 부모·자식 변환이 겹친 **전단** 포함)을 적용한다. 법선은 역전치 행렬로 변환한다.
- 시간: 뷰포트의 현재 시간 코드(`viewport_api.time`, 없으면 타임라인 현재 시간) 기준. 타임 샘플 xform·points도 그 시간 값을 쓴다.
- instance proxy(네이티브 인스턴스 안의 prim)는 프로토타입 기하에 그 인스턴스의 변환을 적용해 계산한다. PointInstancer 인스턴스는 지원하지 않는다 (`point`).
- 맞힌 prim 경로와 기하는 **반환·영구 저장·웹 전송하지 않는다**. prim 경로는 메모리 안 메시 캐시의 키로만, 기하는 그 캐시 안에서만 쓰며, 장면을 열거나 닫으면 버린다. 면 결과에 prim 정보는 없다.
- 성능·메모리:
  - 캐시: Mesh 기하·인접·평면 묶음 정보는 (prim 경로, 시간 코드) 기준으로 그 메시를 처음 클릭할 때 한 번 만들어 메모리에 둔다. **최근 메시 8개, 합계 512 MB** 안에서 오래된 것부터 버린다 (방금 만든 하나는 512 MB를 넘어도 남긴다). 그 prim이나 조상이 바뀌면(`Usd.Notice.ObjectsChanged`) 그 항목을 버리고, 장면을 열거나 닫으면 모두 버린다.
  - 크기 상한: 면 **200만** 또는 (팬 분할) 삼각형 **300만**을 넘는 Mesh는 메시 데이터를 만들지 않는다. 클릭마다 면을 조각(13만 면)씩 훑어 맞힌 다각형만 찾아 `polygon`으로 돌려준다 (캐시 없음. 추가 메모리 = 면 수 × 약 16바이트 + 조각 작업 약 50 MB, 1000만 면 약 0.16 GB. 1000만 면 약 5초).
  - 상한 안 메시의 데이터 크기(대략): 100만 면 약 0.25 GB, 상한 크기(150만 사각형·200만 삼각형) 약 0.36 GB. 만드는 동안 일시 최대는 그 약 2배 (상한 크기 약 0.75 GB).
  - 시간: 처음 만들 때 메시 크기에 비례 (100만 면 약 2.5초, 상한 크기 약 4초). 그 뒤 클릭은 수십 ms 안 (100만 면 평면 약 30 ms, 같은 평면을 다시 고르면 거의 0).
  - 메인 스레드: `pick_face`(웹·편집 창 포함)는 면 **20만 이상** Mesh를 처음 만들 때(와 상한 초과 Mesh를 훑을 때) numpy 계산을 작업 스레드에서 하고, 결과 확인·면 계산은 메인 스레드에서 한다. 그 사이 그 prim이 바뀌거나 장면이 바뀌면 그 결과는 캐시하지 않고 한 번 다시 만든다. 20만 미만은 메인 스레드에서 바로 만든다 (약 0.5초 이하).
  - 측정: 면 계산(메인 스레드) perf `scope("morph.annotation.overlay.face_ms")`, 메시 데이터 만들기·상한 초과 훑기(작업 스레드 포함) `scope("morph.annotation.overlay.face_build_ms")`. 만들기(또는 훑기)가 1초를 넘으면 경고 로그 1회 (면 수 포함, prim 경로 없음).

## 편집 창 (데스크톱)
- omni.ui 창 `"MORPH Annotations"`. 설정 `editorWindow`가 true면 시작할 때 연다. `omni.kit.menu.utils`가 있으면 메뉴 **Window > MORPH Annotations**로 열고 닫는다 (체크 표시 = 열림). 창의 닫기 버튼은 숨기기다.
- 배치: **처음 열 때** 2프레임 뒤 데스크톱 레이아웃 오른쪽 패널 창에 탭으로 도킹한다 (`"Property"`, 없으면 `"Stage"`. 보이는 창 중 먼저 찾은 것, `DockPosition.SAME`, 도킹 후 이 탭을 앞으로). 둘 다 없거나 이미 도킹돼 있으면(저장된 레이아웃 등) 그대로 둔다 (떠 있는 창). 다시 열 때는 사용자가 옮긴 자리를 유지한다.
- 글꼴: 기본으로 omni.ui 스타일 `font`를 지정하지 않고 **앱 글꼴을 따른다** (한글은 앱 설정으로 켠다, 아래 "한글 표시"). 설정 `fontPath`를 명시했을 때만 그 글꼴을 창 스타일로 쓴다. 창 UI 문구는 한국어다.
- 구성: 상단(전체 표시, 설명 표시, 글자 크기 슬라이더, 사이드카 파일 이름, [다시 읽기] [저장]) / 주석 목록(종류·이름·id, 클릭 = `select`, 선택 항목 강조) / [kind 고르기] [새로 만들기] / 편집 폼 / [적용] [삭제] [뷰에서 위치 찍기] [면 선택] / 빨간 오류 글씨·회색 상태 글씨 / 선택된 면 정보(종류·중심·법선·면적·크기) [면 중심을 폼에] [면 선택 해제].
- 폼: 선택된 주석(없으면 새 주석)의 kind(새 주석일 때만 바꿈), id(새 주석일 때만, 비우면 자동), label, description, color, kind별 숫자 칸 (label: position / inlet·outlet: position, direction, size / plane: origin, normal, size w·h, up(체크하면 지정, 끄면 스테이지 up 축)).
- [적용]: 선택된 주석이면 `update`, 새 주석이면 `add` 후 그것을 `select`. 검증 오류(`ValueError` 메시지)는 창 안에 빨간 글씨로 보이고 아무것도 바꾸지 않는다. 추가 필드(다른 도구가 넣은 필드)는 `update`라 보존된다.
- [뷰에서 위치 찍기]: 다음 **왼쪽 클릭 한 번**을 어느 뷰포트에서든 잡아 `pick`하고 결과를 폼에 채운다 (자동 적용 안 함, [적용]을 눌러야 반영). label → position, inlet → position과 direction = **-normal**(모델 안쪽으로 흐름), outlet → position과 direction = **+normal**, plane → origin만 (normal 유지). normal이 `None`이면 direction은 그대로. 빈 곳을 찍으면 오류 글씨. 다시 누르면 취소. 창을 닫아도 취소.
- [면 선택]: 다음 **왼쪽 클릭 한 번**을 어느 뷰포트에서든 잡아 `pick_face` → `select_face` 하고 면 정보를 보인다 (폼은 바꾸지 않음). 빈 곳을 찍으면 면 선택을 해제하고 오류 글씨. 다시 누르면 취소 ([뷰에서 위치 찍기]와 동시에 하나만 대기). 웹·Python에서 선택한 면도 같은 자리에 보인다.
- [면 중심을 폼에]: 선택된 면으로 폼을 채운다 (자동 적용 안 함, [적용]을 눌러야 반영). label → position = center. inlet → position = center, direction = **-normal**. outlet → position = center, direction = **+normal**. plane → origin = center, normal = normal, size = face `size` × 1.2 (성분이 0이면 그 성분은 기존 값 유지), up 지정 끔(스테이지 up 축 — face `size`의 축과 같음). 선택된 면이 없으면 오류 글씨.
- 찍기·면 선택 대기 중에는 모든 뷰포트에서 Kit 기본 클릭 선택(Selection·ObjectClick 레이어)을 끈다 (`omni.kit.viewport.utility.disable_selection`, 클릭 후·취소 시 복원). 왼쪽 클릭 한 번은 Kit 카메라 조작이 아니므로 카메라도 움직이지 않는다.
- 다른 곳(웹·Python)에서 선택이 바뀌면 폼이 그 주석으로 바뀐다. 선택된 주석이 다른 곳에서 고쳐지면 폼에 아직 적용 안 한 편집이 없을 때만 새 값으로 다시 채운다.

## 이벤트
이름 공간 `morph.annotation.*` (웹 type `morph.annotation.overlay.*`과 다름). 메인 스레드에서 발행.

### 발행
| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `morph.annotation.changed` | `{ "reason": "add" \| "update" \| "remove" \| "clear" \| "set_all" \| "load" \| "visible", "ids": [str, ...], "count": int }` | 목록·표시 여부가 바뀐 뒤 (같은 프레임의 여러 변경은 합치지 않는다, 호출마다 1회, 호출 안에서 동기 발행). `ids`는 바뀐 주석 id (`clear`·`load`·`set_all`·`visible`이면 `[]`). `count`는 변경 후 주석 수. `load`는 `load()`, 자동 읽기(OPENED), 장면 닫힘으로 목록을 비울 때(CLOSED, 원래 비어 있었으면 발행 안 함). 개별 주석의 `visible` 변경(웹 `set_visible` + `id` 포함)은 `update`. 선택만 바뀐 것은 발행하지 않는다 (→ `selection_changed`) |
| `morph.annotation.selection_changed` | `{ "id": str \| null }` | 선택이 바뀐 뒤 (`select`, 웹 `select`, 편집 창 목록 클릭, 자동 해제). 자동 해제는 그 원인인 `changed` **다음에** 발행한다 (그때 `selected_id`는 이미 `None`). 같은 값으로의 `select`는 발행 안 함 |
| `morph.annotation.face_selected` | `{ "face": Face \| null }` (Face = "면 (Face)", 사본) | 면 선택이 바뀐 뒤 (`select_face`, 웹 `pick_face`·`clear_face`, 편집 창 [면 선택]·[면 선택 해제], 장면 열기·닫기의 자동 해제). 같은 값이면 발행 안 함. 주석 이벤트(`changed`·`selection_changed`)와 독립 |

### 수신
| 이벤트 | 소유 | 반응 |
|---|---|---|
| omni.usd stage OPENED / CLOSED (Kit 이벤트, UsdContext.stage_event_name으로 구독) | omni.usd | 면 캐시를 버리고 면 선택 해제 (`autoLoad`와 무관, `face_selected`가 먼저). `autoLoad`면 사이드카 읽기 / 목록 비움 (`reason: "load"`). 웹 `state` 푸시 예약 (`file` 변경) |
| `morph.stream.client_connected` | morph.stream.messaging | `state` 1회 송신 |

설정 `textScale`·`showDescription`이 바뀌면(carb 설정 구독, 웹 `set_options` 포함) 다시 그리고 웹 `state` 푸시를 예약한다. 이벤트 `morph.annotation.changed`는 발행하지 않는다 (주석 목록 변경이 아님).

## 웹 메시지 (morph.stream.messaging 1.x 경유)
모든 타입 `v = 1`, stream 회신 규칙.

| 방향 | type | payload | 성공 회신 |
|---|---|---|---|
| 웹→Kit | `morph.annotation.overlay.list` | `{}` | `{ ok: true, annotations: [...], visible, file, show_description, text_scale, selected_id, face }` (`state`와 같은 필드. `file` = 사이드카 파일 **이름**만, 경로 아님. 없으면 null) |
| 웹→Kit | `morph.annotation.overlay.add` | `{ "annotation": object }` | `{ ok: true, id }` |
| 웹→Kit | `morph.annotation.overlay.update` | `{ "id": string, "fields": object }` | `{ ok: true, annotation }` (`fields` 의미는 Python `update`와 같음, `null` = 기본값으로) |
| 웹→Kit | `morph.annotation.overlay.remove` | `{ "id": string }` | `{ ok: true, id }` |
| 웹→Kit | `morph.annotation.overlay.set_visible` | `{ "visible": boolean, "id"?: string }` | `{ ok: true }` (`id` 있으면 그 주석의 `visible`, 없으면 전체) |
| 웹→Kit | `morph.annotation.overlay.set_options` | `{ "show_description"?: boolean, "text_scale"?: number }` | `{ ok: true, show_description, text_scale }` (바뀐 뒤의 현재 값. 빈 payload는 현재 값만 회신). `show_description` → 설정 `showDescription` `"always"`/`"never"`, `text_scale` → 설정 `textScale` (0.25..4, 밖이면 `invalid_argument`). 모든 뷰포트에 적용 (뷰포트별 아님). 실패하면 아무것도 바꾸지 않는다 |
| 웹→Kit | `morph.annotation.overlay.save` | `{}` | `{ ok: true, file }` (현재 장면 사이드카에만. 웹은 경로를 지정하지 못한다) |
| 웹→Kit | `morph.annotation.overlay.reload` | `{}` | `{ ok: true, count }` (사이드카 다시 읽기. 선택 해제) |
| 웹→Kit | `morph.annotation.overlay.select` | `{ "id": string \| null }` | `{ ok: true, selected_id }` (`null` = 해제. 선택된 주석이 모든 뷰포트에서 강조) |
| 웹→Kit | `morph.annotation.overlay.pick` | `{ "viewport_name": string, "x": number, "y": number }` | **지연 회신** (쿼리가 끝난 뒤 정확히 1회. 인자 오류 `not_found`·`invalid_argument`만 즉시 회신, 쿼리 거부·해상도 없음의 `unavailable`도 다음 루프 반복에 회신): 맞으면 `{ ok: true, hit: true, position: [x,y,z], normal: [x,y,z] \| null }`, 빈 곳이면 `{ ok: true, hit: false }`. `x`·`y` = 그 뷰포트 렌더 이미지 안의 0..1 정규화 좌표(원점 왼쪽 위. 웹 영상에서 그 뷰포트 영역 안 클릭 위치 / 영역 크기). 의미는 Python `pick`과 같다. 주석을 만들지 않는다 (받은 값으로 `add`/`update`를 따로 보낸다) |
| 웹→Kit | `morph.annotation.overlay.pick_face` | `{ "viewport_name": string, "x": number, "y": number }` | **지연 회신** (`pick`과 같은 규칙, 정확히 1회): 맞으면 `{ ok: true, hit: true, position: [x,y,z], face: Face }`, 빈 곳이면 `{ ok: true, hit: false }`. 맞으면 그 면을 `select_face`하고(모든 뷰포트에서 강조), 빗나가면 면 선택을 해제한 뒤 회신한다. 실패(`not_found`·`invalid_argument`·`unavailable`)면 선택을 바꾸지 않는다. Face는 "면 (Face)" (`outline` 포함, 최대 256점). 큰 Mesh 첫 클릭은 작업 스레드 계산이 끝난 뒤 회신한다 (수 초, Python `pick_face`와 같음) |
| 웹→Kit | `morph.annotation.overlay.clear_face` | `{}` | `{ ok: true }` (면 선택 해제. 없어도 성공) |
| Kit→웹 | `morph.annotation.overlay.state` | `{ "annotations": [...], "visible": bool, "file": string \| null, "show_description": bool, "text_scale": number, "selected_id": string \| null, "face": Face \| null }` | 푸시 (`id: null`, `file`은 list와 같이 이름만. `text_scale`은 설정 값(0.25..4로 자른 값), 작은 뷰 자동 축소는 반영하지 않음. `face` = 선택된 면, `outline` 포함). 변경될 때마다(주석 선택·면 선택 변경 포함, 다음 업데이트에 합쳐 최대 1회/프레임), 장면 열기·닫기 때, 표시 옵션 설정이 바뀔 때, `morph.stream.client_connected` 때 1회. 보낼 시점에 `is_connected`일 때만 (연결 전 변경은 버리고, 연결 시 최신 상태를 보낸다) |

### 실패 code
| code | 요청 | 원인 |
|---|---|---|
| `invalid_argument` | add, update, set_visible, set_options, pick, pick_face | 주석 검증 실패 (`message`에 필드·이유), update의 `kind`·`id` 변경, set_options 값 타입·범위, pick·pick_face의 `x`·`y`가 0..1 밖 |
| `not_found` | update, remove, set_visible(`id`), select, pick, pick_face | 없는 id / pick·pick_face: 그 이름의 뷰포트 창이 없음 |
| `resource_exhausted` | add | `maxAnnotations` 초과 |
| `failed_precondition` | save, reload | 파일 장면이 아님 / JSON 깨짐·형식 오류·version 초과 (reload, 목록 유지) |
| `io_error` | save, reload | 파일 쓰기·읽기 실패 (`message` = `"<OSError 종류>: <strerror> (<파일 이름>)"`, 예: `"PermissionError: Permission denied (pipe_manifold.annotations.json)"`) |
| `unavailable` | 모두 | shutdown 중. pick·pick_face: 렌더 해상도 없음·쿼리 거부, 5초 안에 쿼리 응답 없음, 대기 중 shutdown |

- 웹 회신 `message`에 들어가는 경로는 **파일 이름(basename)만**이다. 폴더·절대 경로·임시 파일 경로는 넣지 않는다. 전체 경로는 Kit 로그(`carb.log_warn`)에만 남긴다.

## 설정
| 경로 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `/exts/morph.annotation.overlay/visible` | bool | true | 전체 표시. 런타임 변경 반영 |
| `/exts/morph.annotation.overlay/autoLoad` | bool | true | 장면 열 때 사이드카 자동 읽기 |
| `/exts/morph.annotation.overlay/sidecarSuffix` | str | `".annotations.json"` | 사이드카 파일 접미사 |
| `/exts/morph.annotation.overlay/textScale` | float | 1.0 | 라벨 글자 배율 (0.25..4, 밖이면 잘라 씀, 숫자 아니면 1.0). 런타임 변경 반영, 웹 `set_options`도 이 값을 쓴다 |
| `/exts/morph.annotation.overlay/showDescription` | str | `"always"` | `"always"` / `"never"`. `"never"`면 모든 뷰포트에서 description을 숨긴다 (label은 보임). 다른 값은 `"always"`로 본다. 런타임 변경 반영, 웹 `set_options`도 이 값을 쓴다 |
| `/exts/morph.annotation.overlay/maxAnnotations` | int | 512 | 최대 개수 |
| `/exts/morph.annotation.overlay/editorWindow` | bool | false | true면 시작할 때 편집 창 "MORPH Annotations"를 연다 (시작 시 한 번 읽음. 데스크톱 앱용, 스트리밍 앱은 false). 메뉴 등록은 이 값과 무관 |
| `/exts/morph.annotation.overlay/fontPath` | str | `""` | 편집 창 스타일 글꼴 파일. `""`(기본) = 스타일 없음, **앱 글꼴을 따름** (자동 탐색한 글꼴도 스타일로 쓰지 않는다). 값을 명시하면 그 파일을 창 스타일 `font`로 쓴다 (파일이 없거나 TrueType/OpenType/TTC 글꼴 파일이 아니면 경고 후 앱 글꼴). 주의: 스타일로 준 글꼴은 omni.ui가 기본(라틴) 글리프 범위로 따로 구워 한글이 `??`가 될 수 있다 — 한글용으로 쓰지 말 것 ("한글 표시"). 편집 창을 처음 만들 때 한 번 읽음. 뷰포트 라벨에는 쓰이지 않는다 |

### 한글 표시
- **한글은 앱 설정 `/app/font/customFontPath` + `/app/font/customRegionFiles`로 켠다.** 이 익스텐션의 편집 창·뷰포트 라벨은 앱 글꼴을 따른다 (글꼴을 따로 지정하지 않는다). Kit 기본 글꼴·기본 글리프 범위에는 한글이 없어 앱 설정이 없으면 `??`로 보인다.
- 앱 .kit 설정 (앱 시작 시 glyph manager가 읽음, 런타임 변경 불가):
  ```toml
  [settings]
  app.font.customFontPath = "C:/Windows/Fonts/malgun.ttf"
  app.font.customRegionFiles = ["${app}/fonts/korean_region.txt"]
  ```
  메인 세션은 앱 쪽 `source/apps/fonts/korean_region.txt`를 쓴다. 이 익스텐션의 `data/fonts/korean_region.txt`(한글 음절 11,172자 + 자모 + CJK 문장 부호·전각 기호)는 같은 형식의 **참고용** 사본이다.
- 위젯에 omni.ui 스타일 `font`를 주지 않는다: 스타일로 지정한 글꼴은 omni.ui가 기본(라틴) 글리프 범위로만 따로 구워, 앱 글꼴에 한글이 있어도 그 위젯은 `??`가 된다. 그래서 편집 창은 기본으로 스타일 글꼴이 없다 (`fontPath`를 명시했을 때만 적용).
- 뷰포트 라벨(`omni.ui.scene` `sc.Label`)은 글꼴 옵션이 없어(`size`·`color`·`alignment`뿐) 항상 앱 글꼴로 그려진다.
- 시작할 때 `/app/font/customFontPath`가 비어 있고 `fontPath`도 비어 있으면 경고 로그를 한 번 남긴다 ("한글이 ??로 보일 수 있음, 앱 .kit에 font.customFontPath·customRegionFiles 설정" 안내 포함).

## 그리기
- `omni.kit.viewport.registry`로 scene 레이어를 등록해 **모든 뷰포트 창**(분할 뷰가 새로 만든 창 포함)에 그린다. 분할 익스텐션에 의존하지 않는다.
- `omni.kit.viewport.registry`가 없으면(헤드리스 등) 경고 로그 1회 후 그리기만 생략하고 데이터·이벤트·웹은 동작한다. 시작할 때 이미 있든 없든 그 익스텐션의 enable/disable을 따라간다: 나중에 enable되면 등록하고, disable되면 등록을 풀고, 다시 enable되면 다시 등록한다.
- 뷰포트마다 scene 항목 하나(이름 `"Annotations"`)가 생긴다. 그 항목의 `visible`(뷰포트 쪽 표시 토글)과 전체 `visible`이 모두 true여야 보인다.
- 다시 만드는 때: 목록·전체 표시·선택·면 선택·`textScale`·`showDescription`이 바뀔 때, 뷰포트 높이가 아래 작은 뷰 기준을 넘나들 때. 매 프레임 다시 만들지 않는다 (카메라 이동은 카메라 뒤 판정만 다시 한다). 갱신 시간은 perf `scope("morph.annotation.overlay.rebuild_ms")`.
- 라벨 상자 (label 지시선 끝, inlet 꼬리, outlet 머리, plane 위쪽 변 가운데):
  - 지시선: 기준점에서 화면 오른쪽 위로 28px × 배율 꺾인 선, 그 끝에 상자. 상자 첫 줄이 지시선 끝 높이에 온다.
  - 상자 = 반투명 어두운 배경 + 주석 색 테두리. 안에 주석 색 label(14px × 배율, 한 줄, 48자 넘으면 `…`) + 회색 description(label의 0.8배).
  - description은 공백 단위로 약 40자에서 줄바꿈(40자보다 긴 단어는 40자에서 자름), **최대 2줄**, 남으면 둘째 줄 끝을 `…`로 줄인다. 연속 공백·줄바꿈은 공백 하나로 합친다.
  - 상자 폭은 줄마다 어림한 글자 폭의 최댓값 + 여백. 어림은 동아시아 전각 글자(한글·한자 등) 1.0em, 그 외 0.66em로 실제보다 넉넉하게 잡아 **글자가 상자를 넘지 않게** 한다 (그 대신 영문은 오른쪽 여백이 남을 수 있다). 상자 높이는 줄 수 × 줄 간격(글자 크기 × 1.3) + 여백.
  - label·description이 모두 비었거나(또는 description만 있는데 `showDescription`이 `"never"`) 상자를 그리지 않는다 (label kind는 점만).
- 글자 배율 = `textScale` × 작은 뷰 계수. 작은 뷰 계수는 뷰포트 해상도(`viewport_api.resolution`) 높이가 400px 미만이면 0.8, 아니면 1.0 (2x2 분할의 각 뷰 등). 해상도를 알 수 없으면 1.0. 뷰포트별로 따로 적용된다. 점·지시선·여백도 같은 배율.
- 모양: 화살표 = 선(3px) + 원뿔 머리 (길이 0.25 × size, 반지름 0.08 × size). 평면 = 채움 알파 0.22 + 테두리(2px) + normal 화살표. 라벨 점 8px × 배율.
- 선택 강조 (`selected_id`): 색을 흰색 쪽으로 45% 밝게, 선 두께 2배(지시선·테두리 4px, 화살표 6px), 라벨 상자 테두리 3px(평소 1px), 점 1.5배, 평면 채움 알파 0.35. 모든 뷰포트에 같이 적용된다.
- 선택된 면 (`selected_face`): 밝은 청록색(`[0.1, 0.95, 1.0]`) `outline` 4px 선(월드) + 중심에 화면 고정 크기 십자 + 그 오른쪽 아래 정보 상자 3줄 `"(x, y, z)"`(중심, 소수 3자리), `"n (nx, ny, nz)"`, `"A = …"`(소수 3자리, 0.001 미만은 지수 표기). 글자 배율은 주석과 같다. 전체 `visible`을 따르고, 중심이 원근 카메라 뒤면 숨긴다. 주석이 아니므로 사이드카와 무관하다.
- 편집 창의 "뷰에서 위치 찍기"·"면 선택" 동안에는 각 뷰포트 scene 항목 안에 화면 전체 `sc.Screen` + `ClickGesture`(왼쪽 버튼)가 생긴다 (클릭 한 번 후·취소 시 제거).
- 주석끼리·상자끼리 겹침은 피하지 않는다 (배치 조정 없음). 작은 뷰에서 겹치면 `showDescription` `"never"`나 `textScale`을 줄여 쓴다.

## 사용 예
```python
from morph.annotation.overlay import get_overlay

ov = get_overlay()
if ov:
    ov.add({"id": "in1", "kind": "inlet", "label": "Inlet", "description": "냉각수 입구 DN50",
            "position": [-3.0, 0.0, 1.0], "direction": [1, 0, 0], "size": 0.8})
    ov.add({"kind": "outlet", "label": "Outlet A", "position": [3.0, 0.0, 1.0], "direction": [1, 0, 0]})
    ov.add({"kind": "plane", "label": "Section A-A", "origin": [0.0, 0.0, 1.0], "normal": [1, 0, 0], "size": [1.5, 1.5]})
    ov.add({"kind": "label", "label": "Valve V-101", "description": "수동 볼밸브", "position": [1.2, 0.0, 1.4]})
    ov.update("in1", description="냉각수 입구 DN65")
    if ov.current_file:
        ov.save()

# 선택 (모든 뷰포트에서 강조) + 선택 변경 구독
from carb.eventdispatcher import get_eventdispatcher
from morph.annotation.overlay import EVENT_SELECTION_CHANGED

sel_sub = get_eventdispatcher().observe_event(  # 반환값을 보관한다 (버리면 구독이 풀림)
    observer_name="my.ext:annotation_selection",
    event_name=EVENT_SELECTION_CHANGED,
    on_event=lambda e: print("selected:", e["id"]),
)
if ov:
    ov.select("in1")
    print(ov.selected_id)  # "in1"
    ov.select(None)

# 뷰 클릭 위치로 주석 만들기: 뷰포트 "Viewport" 이미지 중앙 (x, y = 0..1, 원점 왼쪽 위)
import asyncio


async def add_label_at_center():
    ov = get_overlay()
    if ov is None:
        return
    try:
        hit = await ov.pick("Viewport", 0.5, 0.5)
    except KeyError:
        print("no viewport window named 'Viewport'")
        return
    except (RuntimeError, TimeoutError) as e:
        print("pick failed:", e)
        return
    if hit is None:
        print("empty space")
        return
    pos = hit["position"]
    if hit["normal"] is not None:  # 표면에서 살짝 띄우기 (숫자 보정)
        pos = [p + 0.05 * n for p, n in zip(pos, hit["normal"])]
    ov.select(ov.add({"kind": "label", "label": "Picked", "position": pos}))


asyncio.ensure_future(add_label_at_center())

# 클릭한 면의 중심에 출구 화살표 놓기 + 그 면을 모든 뷰포트에서 강조
async def add_outlet_at_face(viewport_name="Viewport", x=0.5, y=0.5):
    ov = get_overlay()
    if ov is None:
        return
    try:
        hit = await ov.pick_face(viewport_name, x, y)
    except KeyError:
        print(f"no viewport window named {viewport_name!r}")
        return
    except (RuntimeError, TimeoutError) as e:
        print("pick_face failed:", e)
        return
    if hit is None:
        ov.select_face(None)
        print("empty space")
        return
    face = hit["face"]
    ov.select_face(face)  # pick_face itself does not select
    print(face["kind"], "center", face["center"], "normal", face["normal"], "area", face["area"])
    if face["kind"] != "point":
        ov.add({"kind": "outlet", "label": "Outlet", "position": face["center"], "direction": face["normal"]})
        # 단면이면: {"kind": "plane", "origin": face["center"], "normal": face["normal"],
        #            "size": [max(s * 1.2, 0.01) for s in face["size"]]}


asyncio.ensure_future(add_outlet_at_face())

# 면 선택 구독 (해제 = {"face": None})
from morph.annotation.overlay import EVENT_FACE_SELECTED

face_sub = get_eventdispatcher().observe_event(  # 반환값을 보관한다
    observer_name="my.ext:face_selected",
    event_name=EVENT_FACE_SELECTED,
    on_event=lambda e: print("face:", e["face"]["center"] if e["face"] else None),
)

# 편집 창의 [뷰에서 위치 찍기]·[면 선택] 대기 상태 확인·취소 (취소하면 Kit 클릭 선택이 돌아온다)
ov = get_overlay()
if ov and ov.is_picking:
    ov.cancel_pick()

# 표시 옵션 (모든 뷰포트): description 숨기기, 글자 배율
import carb.settings

settings = carb.settings.get_settings()
settings.set_string("/exts/morph.annotation.overlay/showDescription", "never")
settings.set_float("/exts/morph.annotation.overlay/textScale", 0.8)
```

## 금지 / 주의
- 주석을 USD prim으로 만들거나 prim 경로에 묶지 않는다 (요구 사항). 모델을 옮기면 주석은 따라가지 않는다.
- 웹은 사이드카 경로를 지정할 수 없다 (현재 장면 사이드카만).
- 단면 "표시"이며 지오메트리를 자르지 않는다.
- `pick`은 위치를 얻는 데만 쓴다. 맞은 prim 경로는 반환하지 않으며 주석을 prim에 묶는 수단이 아니다.
- 선택(`selected_id`)은 화면 강조용 상태다. 사이드카에 저장되지 않고 장면을 다시 열면 풀린다.
- 면(`pick_face`·`selected_face`)은 계산 순간의 월드 좌표 값이다. prim 경로를 담지 않고 저장되지 않으며, 모델을 옮기면 다시 골라야 한다. 면 중심으로 만든 주석도 월드 좌표만 저장한다.

## 변경 이력
- 0.4.1 — 0.4.0 리뷰·테스트 반영 (draft patch, 공개 API·이벤트·메시지·설정 이름과 Face 모양은 그대로):
  - 수정(메모리): 아주 큰 Mesh에서 메모리 상한이 없던 문제 — 크기 상한(면 200만 또는 삼각형 300만)을 넘으면 메시 데이터를 만들지 않고 조각 단위로 훑어 맞힌 다각형만 `polygon`으로 돌려준다. 캐시는 메시 8개에 더해 합계 512 MB 안에서 오래된 것부터 버린다. 상한 안 메시도 만드는 동안의 임시 배열을 조각 단위로 줄였다. "성능·메모리"에 수치.
  - 변경(동작): 면 20만 이상 Mesh의 첫 `pick_face`(웹·편집 창 포함)는 메시 데이터를 작업 스레드에서 만든다 (메인 스레드를 막지 않음, 결과 확인·면 계산은 메인 스레드). 그 동안 prim이 바뀌면 다시 만든다. perf `face_build_ms` 추가, 1초 넘는 만들기는 경고 1회.
  - 수정(묶음): 같은 높이의 두 평면이 후보가 아닌 면(굴곡)을 거쳐서만 이어져도 하나로 합쳐지던 것 — 시작 다각형에서 후보끼리 이어진 것만 묶는다 (flood fill).
  - 수정(퇴화 면): 면적 0·한 직선·가는 조각 삼각형이 각도 판정에 걸려 평면을 둘로 나누던 것 — 평면 거리로만 판정. NaN·inf 정점이 하나라도 있으면 Mesh 전체가 `point`가 되던 것 — 그 정점을 쓰는 다각형만 무시.
  - 수정(전단): Cylinder 옆면이 부모·자식 변환이 겹친 전단에서 면적(이전: 비스듬한 단면 둘레 × 수직 높이)·outline(지역 높이의 비스듬한 고리)·size가 center와 맞지 않던 것 — 직단면 둘레 × 모선 길이, outline = center를 지나는 직단면.
  - 수정: 평면 묶음 연결 계산이 200번 반복 후 조용히 멈출 수 있던 것 — 반복 제한 제거 (매 반복 진행이 보장됨).
  - 문서: numpy 의존(`omni.kit.pip_archive`, 없으면 `point`), prim 경로는 메모리 안 캐시 키로만 쓰고 장면 열기·닫기 때 버림.
- 0.4.0 — 면 선택 (draft minor, 기존 API·메시지와 호환. 웹 `list`·`state`에 필드 추가):
  - 추가: `AnnotationOverlay.pick_face(viewport_name, x, y)` (await → `{position, face}` / `None`), `selected_face`, `select_face(face | None)`, 이벤트 `morph.annotation.face_selected`, 상수 `EVENT_FACE_SELECTED`·`FACE_KINDS`. "면 (Face)" 절 (kind `planar`·`cylinder_side`·`sphere`·`polygon`·`point`, 평면 묶음 규칙, 캐시, perf `face_ms`).
  - 추가: 웹 `pick_face` (지연 회신, 맞으면 선택·빗나가면 해제), `clear_face`, `list` 회신·`state` 푸시에 `face`.
  - 추가: 선택된 면 그리기 (청록 외곽선·중심 십자·정보 상자), 편집 창 [면 선택]·[면 중심을 폼에]·[면 선택 해제]와 면 정보. `is_picking`·`cancel_pick`이 [면 선택] 대기도 포함.
  - 변경(동작): 장면 열기·닫기 때 `autoLoad`와 무관하게 면 선택 해제.
  - 수정: `pick()`이 돌려준 Future를 호출자가 취소하면 내부 쿼리 작업이 타임아웃까지 남던 것 (바로 정리).
- 0.3.2 — 한글 표시 수정 (draft patch, API·메시지·설정 이름 호환):
  - 수정: 앱 글꼴에 한글이 있어도 편집 창 한글이 `??`로 보이던 문제 — 편집 창이 위젯마다 스타일 `font`(맑은 고딕)를 지정해 omni.ui가 그 글꼴을 라틴 글리프 범위로만 구운 것이 원인. 이제 기본으로 스타일 글꼴을 지정하지 않고 앱 글꼴을 따른다.
  - 변경(동작): 설정 `fontPath` `""`의 의미가 "자동 탐색"에서 "스타일 없음(앱 글꼴)"으로 바뀜. 명시한 값만 스타일로 쓴다.
  - 추가: 앱 `/app/font/customFontPath`와 `fontPath`가 모두 비었으면 시작 시 경고 1회.
  - 수정: `fontPath`가 글꼴이 아닌 파일이면 omni.ui가 Kit을 크래시시키던 문제 — TrueType/OpenType/TTC 파일만 스타일로 쓰고, 아니면 경고 후 앱 글꼴.
  - 문서: "한글 표시" — 한글은 앱 설정 `customFontPath` + `customRegionFiles`로 켬. `data/fonts/korean_region.txt`는 참고용, 앱은 `source/apps/fonts/korean_region.txt`.
- 0.3.1 — 리뷰·화면 확인 반영 (draft patch, 기존 API·메시지와 호환):
  - 수정: `pick`이 렌더 해상도 없음·쿼리 거부·shutdown 뒤 호출에서 **호출 즉시** `RuntimeError`를 던지던 문제 — 계약대로 예외가 설정된 Future를 돌려준다 (호출 즉시 예외는 `KeyError`·`ValueError`뿐). 웹 `pick`의 이 경우 `unavailable` 회신은 다음 루프 반복에 온다 (code 동일).
  - 수정: `request_query`가 `None`(hydra texture 없음)을 돌려주면 5초 뒤 `TimeoutError` 대신 바로 거부(`RuntimeError`, 웹 `unavailable`). 이웃 픽셀 거부는 `normal` `None`.
  - 수정(한글 표시): 편집 창 한글이 `??`로 보이던 문제 — 설정 `fontPath` 추가(기본 `""` = 자동 탐색, 맑은 고딕 등)와 창 스타일 `font`. 뷰포트 `sc.Label`은 글꼴 옵션이 없어 앱 글꼴 설정이 필요함을 명시하고 글자 목록 `data/fonts/korean_region.txt` 제공 ("한글 표시"). 설정 추가지만 버그 수정으로 patch에 포함.
  - 수정: 편집 창이 뷰포트 위에 떠서 분할 화면을 가리던 문제 — 처음 열 때 `"Property"`(없으면 `"Stage"`) 창에 탭으로 도킹.
  - 추가: `AnnotationOverlay.is_picking`, `cancel_pick()` (편집 창 위치 찍기 상태 확인·취소, smoke 확인용).
  - 정리: 편집 창 destroy 시 visibility 콜백 해제.
- 0.3.0 — 주석 편집 (draft minor, 기존 API·메시지와 호환. 웹 `list`·`state`에 필드 추가):
  - 추가: `AnnotationOverlay.pick(viewport_name, x, y)` (await → `{position, normal}` / `None`), 웹 `pick` (지연 회신, `hit`).
  - 추가: 선택 `select(id | None)`, `selected_id`, 이벤트 `morph.annotation.selection_changed`, 웹 `select`, `list` 회신·`state` 푸시에 `selected_id`. 선택 강조 그리기. 삭제·다시 읽기 때 자동 해제.
  - 추가: 데스크톱 편집 창 "MORPH Annotations" (설정 `editorWindow`, 메뉴 Window > MORPH Annotations), 뷰에서 위치 찍기 (클릭 한 번, 찍는 동안 Kit 클릭 선택 끔).
  - 의존 추가: `omni.ui`, `omni.kit.viewport.utility` (optional), `omni.kit.menu.utils` (optional). `not_found`·`invalid_argument`·`unavailable` 사용처에 select·pick 추가.
- 0.2.0 — 리뷰·화면 확인 반영 (draft minor, 기존 API·메시지와 호환):
  - 추가: 설정 `showDescription` (`"always"` | `"never"`), 웹 `set_options` (`show_description`, `text_scale`), `list` 회신·`state` 푸시에 `show_description`·`text_scale`. 작은 뷰포트(높이 < 400px) 글자 0.8배. `invalid_argument`가 `set_options`에도 쓰임.
  - 수정: 웹 회신 `message`에 폴더 경로가 나가던 문제 — 파일 이름과 `strerror`만 담는다 (전체 경로는 로그). `io_error` message 형식 명시.
  - 수정: 라벨 상자가 글자보다 좁던 문제 — 전각/반각 폭 어림, description 40자 줄바꿈·최대 2줄·`…` (이전: 한 줄 80자). label 48자 `…`.
  - 수정: 저장 시 기존 파일 권한 유지 (새 파일 0644). registry가 시작 시 있어도 나중의 disable/enable을 따라감, registry 없음은 경고 로그.
  - 정리: 공개 import 줄에 `AnnotationOverlay`(타입 표기용)·`MSG_STATE` 명시. `AnnotationOverlayExtension`은 공개 아님(`__all__`에서 뺌). `AnnotationOverlay.closed`(문서에 없던 프로퍼티) 제거.
  - "그리기" 절을 실제 동작에 맞게 다시 씀.
- 0.1.0 — 최초 초안 + 기본 구현. 구현하면서 초안을 실제 동작에 맞게 보강 (draft, 공개 API 모양은 초안 그대로):
  - 검증 세부: bool·문자열 숫자 거부, 크기 > 0, color 0..1, 추가 필드 JSON 직렬화 필수, 다른 kind 필드는 추가 필드, 키 순서.
  - `update`의 `None` = 필드 삭제(기본값). `set_all`도 id 생성·`maxAnnotations` 검사. `plane.up`은 지정했을 때만 저장.
  - 사이드카: 로컬 파일 장면만, `version` 생략 = 1, 파일 항목 id 생성, `maxAnnotations` 초과 항목 건너뜀, 자동 읽기 시 깨진 파일은 경고 후 빈 목록, `autoLoad` false면 열기·닫기 무반응, 시작 시 이미 열린 장면 읽기, CLOSED 때 원래 비어 있으면 이벤트 없음.
  - 웹: 실패 code `io_error` 추가, state 푸시 시점(장면 열기·닫기 포함)과 미연결 시 버림 명시.
  - 그리기: `omni.ui.scene`에 깊이 테스트 옵션이 없음 — UI 레이어라 모델에 가려지지 않음을 명시, 주석끼리 겹침 순서는 보장 안 함. 카메라 뒤 판정은 기준점 기준(원근 카메라만). 라벨 위치(inlet 꼬리, outlet 머리, plane 위쪽 변), description 80자 표시.
