# morph.stage.loader CONTRACT

> 다른 익스텐션 에이전트가 이 익스텐션을 쓸 때 읽는 **유일한** 문서. 여기 없는 것은 내부 구현이므로 의존하지 않는다.

- 버전: 0.1.3
- 상태: draft
- 담당 에이전트: ext-stage
- 의존: morph.stream.messaging, morph.perf.monitor, omni.usd
- 의존 버전 (`extension.toml` 범위): morph.stream.messaging `1.0` = ^1.0 (등록·지연 회신·`is_connected`), morph.perf.monitor `1.0` = ^1.0 (`async_scope`만 사용)

## 목적
USD 장면 파일 목록 조회, 장면 열기·다시 불러오기·새 장면을 Python API와 웹 메시지로 제공하고, 장면이 열리면 `morph.stage.opened` 이벤트를 발행한다.
웹에는 **목록에 있는 장면 id만** 열게 한다 (임의 경로·URL 금지).

## 언제 쓰나 / 쓰지 않나
- 쓴다: 장면 목록이 필요할 때, 장면을 열거나 다시 불러올 때, "장면이 바뀌었다"에 반응할 때(`morph.stage.opened`)
- 쓰지 않는다: prim 편집·저장 (→ `omni.usd` 직접), 카메라 이동 (→ `morph.camera.control`), 뷰↔카메라 연결 (→ `morph.viewport.split`)

## 장면 목록 모델
- **루트**: `roots` 설정의 폴더들 + `includeSamples`가 true면 이 익스텐션의 내장 샘플 폴더(`data/scenes`, 루트 이름 `samples`).
  - `roots` 항목 형식: `"<name>=<절대 폴더 경로>"` 또는 `"<절대 폴더 경로>"`(이름 = 폴더 이름). 이름은 `[A-Za-z0-9_-]+`로 정리한다. 이름이 겹치면(대소문자 무시) 뒤 것에 `_2`, `_3`…을 붙인다. 없는 폴더·읽을 수 없는 폴더는 경고 1회 후 건너뛴다. 상대 경로·빈 항목도 경고 후 건너뛴다.
  - `=` 앞부분에 `/`, 백슬래시, `:` 중 하나가 있으면 이름이 아니라 항목 전체를 경로로 본다 (드라이브 문자·경로 보호).
  - `samples`는 내장 샘플 루트 이름이다. `roots`에 같은 이름을 주면 샘플 루트가 `samples_2`가 되어 `samples/...` id가 바뀌므로 쓰지 않는다.
- **스캔**: 각 루트 아래를 `maxDepth`(루트 = 깊이 0) 깊이까지 훑어 확장자가 `fileExtensions`(대소문자 무시)인 파일을 모은다. 숨김 폴더(`.`으로 시작)는 건너뛴다 (숨김 파일은 포함). 링크·정션은 따라가되 같은 폴더를 두 번 훑지 않는다. 전체가 `maxScenes`를 넘으면 그 뒤는 버리고 경고 1회.
- **장면 id**: `"<루트 이름>/<루트 기준 상대 경로, / 구분>"` (예: `samples/morph_demo.usda`). 같은 파일은 다시 스캔해도 같은 id다.
- 목록 순서: 루트 순서(설정 순서, `samples`는 맨 뒤), 루트 안에서는 상대 경로 대소문자 무시 사전순.
- 스캔은 처음 `list_scenes()` 호출(또는 `startupScene` 처리) 때 한 번 하고 캐시한다. `refresh=True`일 때만 다시 스캔한다 (웹 `list_scenes {refresh: true}`는 1초에 1회로 제한, 아래 웹 메시지). `roots`·`includeSamples`·`fileExtensions`·`maxDepth`·`maxScenes` 설정이 바뀌면 다음 `list_scenes()`에서 다시 스캔한다.

## 공개 Python API
import: `from morph.stage.loader import get_loader, SceneInfo, StageLoadError, EVENT_OPENED, EVENT_OPEN_FAILED`

| 심볼 | 시그니처 | 설명 | 주의 |
|---|---|---|---|
| `get_loader` | `() -> StageLoader \| None` | 싱글턴 | startup 이후 유효, shutdown 이후 `None`. 캐시하지 말고 필요할 때 다시 얻는다 |
| `StageLoader.list_scenes` | `(refresh: bool = False) -> list[SceneInfo]` | 장면 목록 (위 "장면 목록 모델") | 메인 스레드. 동기 파일 스캔이므로 `refresh=True`를 프레임마다 부르지 않는다 |
| `StageLoader.find_scene` | `(scene_id: str) -> SceneInfo \| None` | 캐시된 목록에서 id 조회 (없으면 한 번 스캔 후 조회) | 없는 id로 인한 재스캔은 1초에 최대 1번 (웹 연타 방지). 그 사이 없는 id는 재스캔 없이 `None` |
| `StageLoader.open_scene` | `async (scene_id: str) -> SceneInfo` | 목록의 장면을 연다. 열기가 끝나고(`omni.usd` `OPENED`) 반환 | 아래 "열기 규칙". 목록에 없으면 `KeyError`, 다른 열기 진행 중이면 `RuntimeError`, 열기 실패면 `StageLoadError` |
| `StageLoader.open_path` | `async (path: str) -> None` | 목록과 관계없이 경로(로컬 경로 또는 omni/http URL)를 연다 | **Python 전용** (웹에 노출하지 않음). 상대 로컬 경로는 절대 경로로 바꾼다. 열린 장면이 목록에 있으면 `current()["scene_id"]`가 그 id가 된다. 오류는 `open_scene`과 같다 (`KeyError` 제외) + 빈 문자열이면 `ValueError` |
| `StageLoader.reload` | `async () -> SceneInfo \| None` | 현재 장면 파일을 디스크에서 다시 연다 (`omni.usd` `reopen_stage_async`, 저장 안 한 변경은 버림) | 열린 장면이 파일이 아니면(새 장면·없음) `RuntimeError`. 반환은 목록에 있으면 그 `SceneInfo`, 아니면 `None` |
| `StageLoader.new_scene` | `async () -> None` | 빈 새 장면 | 진행 중 열기가 있으면 `RuntimeError` |
| `StageLoader.current` | `() -> dict` | `{ "scene_id": str \| None, "path": str \| None, "loading": bool }` | `path`는 열린 장면 파일의 경로/URL, 새 장면·없음이면 `None`. `scene_id`는 목록에 있을 때만 |
| `StageLoader.is_loading` | 프로퍼티 `-> bool` | 이 익스텐션이 시작한 열기가 진행 중인지 | 외부(File > Open 등) 열기는 `False` |
| `SceneInfo` | frozen dataclass `id: str, name: str, root: str, path: str, size_bytes: int`, `.to_dict() -> dict` | 장면 한 개. `name` = 파일 이름(확장자 포함), `path` = 절대 경로 | `to_dict()`는 웹용으로 `path`를 **뺀** `{id, name, root, size_bytes}` |
| `StageLoadError` | `Exception` 하위, `.code: str`, `.path: str` | 열기 실패 | `code`는 아래 웹 실패 code와 같은 값 (`open_failed`) |
| `EVENT_OPENED`, `EVENT_OPEN_FAILED` | `str` 상수 | 아래 발행 이벤트 이름 | 문자열을 직접 써도 된다 |
| `MSG_STATE` | `str` 상수 `"morph.stage.loader.state"` | Kit→웹 상태 푸시 type | 웹 구현 참고용 |

### 열기 규칙
- 열기는 `omni.usd.get_context().open_stage_async` / `new_stage_async`를 쓴다. 저장하지 않은 변경 확인 창은 띄우지 않는다 (변경은 버려진다).
- 한 번에 하나만 연다. 이 익스텐션이 시작한 열기가 진행 중이면 `open_scene`/`open_path`/`reload`/`new_scene`은 바로 `RuntimeError`.
- 열기 시간은 `morph.perf.monitor`의 `async_scope("morph.stage.loader.open_ms")`로 기록한다.
- 외부에서 연 장면(데스크톱 File > Open 등)도 `omni.usd` stage 이벤트로 감지해 `current()`를 갱신하고 `morph.stage.opened`(`source: "external"`)를 발행한다.
- 외부에서 장면을 닫기만 하면(`CLOSED`, 다음 `OPENED` 없음) `current()`는 `scene_id`/`path`가 `None`이 되지만 이벤트·state 푸시는 없다.
- 이 익스텐션의 열기가 진행 중일 때 온 `OPENED`는 그 열기의 완료로 본다 (`source: "loader"`). 단 열린 경로가 그 열기의 경로와 같은 파일(경로 정규화 + Windows 대소문자 무시 비교)일 때만이다 (새 장면이면 열린 장면이 새 장면일 때). 다른 파일이면 외부 열기로 처리하고(`source: "external"`, `scene_id`는 목록에서 그 경로로 조회) 이 익스텐션의 열기는 계속 진행 중이다. 그래서 `scene_id`와 `path`는 항상 같은 파일을 가리킨다.
- 열기 하나에 `morph.stage.opened`는 정확히 한 번 나간다 (`omni.usd` 열기 완료 콜백이 `OPENED`보다 먼저 와도 뒤늦은 같은 `OPENED`로 이벤트가 한 번 더 나가지 않는다).

## 이벤트
이름 규칙 `morph.<domain>.<event>`, `carb.eventdispatcher` 사용. 이벤트 이름(`morph.stage.*`)은 웹 type(`morph.stage.loader.*`)과 다른 이름 공간이다. 모든 이벤트는 메인 스레드에서 발행한다.
- 페이로드에서 값이 `None`인 키는 **빠진다** (carb 페이로드에 None을 넣지 않음). 관찰자는 `e["scene_id"]` 또는 `e.get("scene_id")`로 읽으면 없을 때 `None`을 받는다 (`carb.eventdispatcher.Event`의 동작). `e.payload` dict로 바꿔 읽을 때는 `.get()`을 쓴다.

### 발행
| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `morph.stage.opened` | `{ "scene_id": str \| None, "path": str, "source": "loader" \| "external" }` | 장면 열기·다시 불러오기·새 장면이 끝난 직후 (`omni.usd` `OPENED` 처리 안에서). `path`는 새 장면이면 `""`. `source`는 이 익스텐션이 시작한 열기면 `"loader"` |
| `morph.stage.open_failed` | `{ "scene_id": str \| None, "path": str, "error": str, "message": str }` | 이 익스텐션이 시작한 열기가 실패함. `error`는 `open_failed` |

### 수신
| 이벤트 | 소유 익스텐션 | 반응 |
|---|---|---|
| `morph.stream.client_connected` | morph.stream.messaging | 현재 상태(`morph.stage.loader.state`)를 웹에 1회 송신 |

## 웹 메시지 (morph.stream.messaging 1.x 경유)
- 모든 타입 `v = 1`. 회신은 stream 회신 규칙(같은 type·id, `{ok: true, ...}` / `{ok: false, error, message}`).
- 열기 요청은 **열기가 끝난 뒤에** 회신한다 (지연 회신). 그 사이 `state` 푸시(`loading: true`)가 먼저 갈 수 있다.

| 방향 | 메시지 타입 | 페이로드 스키마 | 성공 회신 |
|---|---|---|---|
| 웹→Kit | `morph.stage.loader.list_scenes` | `{"type":"object","properties":{"refresh":{"type":"boolean"}}}` | `{ ok: true, scenes: [{id, name, root, size_bytes}, ...], current_scene_id: string \| null }`. `refresh: true`의 다시 스캔은 1초에 1회 (그 안의 요청은 캐시 목록을 돌려준다, 실패 아님) |
| 웹→Kit | `morph.stage.loader.open_scene` | `{"type":"object","required":["id"],"properties":{"id":{"type":"string"}}}` | 열기 완료 후 `{ ok: true, scene: {id, name, root, size_bytes} }` |
| 웹→Kit | `morph.stage.loader.reload` | `{"type":"object"}` | 열기 완료 후 `{ ok: true, scene_id: string \| null }` |
| 웹→Kit | `morph.stage.loader.new_scene` | `{"type":"object"}` | 완료 후 `{ ok: true }` |
| 웹→Kit | `morph.stage.loader.get_state` | `{"type":"object"}` | `{ ok: true, scene_id, name, loading }` (= `ok: true` + state 페이로드) |
| Kit→웹 | `morph.stage.loader.state` | `{ "scene_id": string \| null, "name": string \| null, "loading": boolean }` | 푸시. 웹 연결 중에만, 열기 시작(`loading: true`)·열기 끝/실패·외부 열기 때, 연결 시 1회. `name`은 열린 장면 파일 이름(목록에 없어도), 새 장면·없음이면 `null` |

### 실패 code (핸들러 회신)
| code | 해당 요청 | 원인 |
|---|---|---|
| `not_found` | `open_scene` | 목록에 없는 id (다시 스캔해도 없음) |
| `invalid_argument` | `open_scene` | `id`가 빈 문자열이거나 문자열이 아님 |
| `failed_precondition` | `open_scene`, `reload`, `new_scene` | 다른 열기가 진행 중 / `reload`인데 열린 장면이 파일이 아님 |
| `open_failed` | `open_scene`, `reload`, `new_scene` | `omni.usd` 열기 실패 (파일 손상 등). `message`에 Kit 오류 |
| `unavailable` | 모두 | 이 익스텐션이 실행 중이 아님 (shutdown 중·이후), 또는 열기 완료를 기다리던 지연 회신이 취소됨 |

## 설정
| 경로 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `/exts/morph.stage.loader/roots` | list[str] | `[]` | 장면 루트 폴더 (`"name=path"` 또는 `"path"`) |
| `/exts/morph.stage.loader/includeSamples` | bool | true | 내장 샘플 루트(`samples`) 포함 |
| `/exts/morph.stage.loader/fileExtensions` | list[str] | `[".usd", ".usda", ".usdc", ".usdz"]` | 목록에 넣을 확장자 |
| `/exts/morph.stage.loader/maxDepth` | int | 4 | 스캔 깊이 (루트 = 0) |
| `/exts/morph.stage.loader/maxScenes` | int | 1000 | 목록 최대 개수 |
| `/exts/morph.stage.loader/startupScene` | str | `""` | 비어 있지 않으면 startup 뒤 첫 업데이트에서 이 id를 연다 (없으면 경고). 예: `samples/morph_demo.usda` |

## 내장 샘플 (`data/scenes`)
- `morph_demo.usda` (id `samples/morph_demo.usda`): 바닥 평면, 색이 다른 큐브·구·원기둥 몇 개, 조명(DistantLight + DomeLight), 카메라 3개 `/World/Cameras/CamFront`, `/World/Cameras/CamTop`, `/World/Cameras/CamSide`. 분할 뷰·카메라 확인용. `upAxis = "Z"`, `metersPerUnit = 1.0` (미터 단위), `defaultPrim = "World"`. 외부 참조·텍스처 없이 이 파일 하나로 완결.
  - 루트 레이어 `customLayerData.cameraSettings`에 `Perspective`의 `position = (9, -9, 6.5)`, `target = (0, 0, 0.5)`(월드, 미터)와 `boundCamera = "/OmniverseKit_Persp"`를 넣어, 뷰포트가 있으면 열 때 Kit의 Persp 카메라(`/OmniverseKit_Persp`)가 장면 전체를 비춘다 (Kit 기본 위치는 약 500 단위 떨어져 미터 장면이 점처럼 보임). Front/Top/Right 직교 카메라는 Kit 기본값 그대로다.
- `pipe_manifold.usda` (id `samples/pipe_manifold.usda`): 배관 매니폴드. 주석 표시(`morph.annotation.overlay`) 확인용. `upAxis = "Z"`, `metersPerUnit = 1.0`, `defaultPrim = "World"`, 외부 참조·텍스처 없음. Persp `cameraSettings`(`position = (6, -6, 4.5)`, `target = (0, 0.3, 1.1)`)는 morph_demo와 같은 방식.
  - 형상(월드, 미터): X축 주관 반지름 0.15, x -2.0..2.0, 중심선 y=0 z=1.0. 입구 플랜지 바깥면 x=-2.1, 출구 B 플랜지 바깥면 x=2.1. x=-0.5에서 분기관(반지름 0.12)이 z 1.0..2.0으로 올라가 엘보(중심 (-0.5, 0, 2.0))에서 +Y로 꺾여 출구 A 플랜지 바깥면 y=1.28. 밸브 V-101 x=0.8 (손잡이 위 z=1.49). 받침대 2개 x=-1.3, x=1.5 (z 0..0.85). 바닥 z=0. 매니폴드(`/World/Manifold`) bbox 약 (-2.1, -0.3, 0.7)..(2.1, 1.28, 2.24).
  - 계층은 일부러 깊다 (`/World/Manifold/MainLine/Seg_01..04`, `/World/Manifold/MainLine/Seg_03/Valve_V101/...`, `/World/Manifold/Branch_A/Upper/Elbow_90/...`, `/World/Supports/Support_01..02`, 부모 Xform마다 translate). prim 경로·계층은 내부 구현이며 바뀔 수 있다. 위 월드 위치, 카메라 이름, 아래 `MountPlate` 경로·값만 계약이다.
  - 받침 판 `/World/Base/MountPlate` (`UsdGeom.Mesh`, 샘플에서 유일한 명시 Mesh 형상. 바닥 `/World/Ground`도 Mesh지만 사각형 1개): 면 선택(`morph.annotation.overlay` `pick_face`) 확인용. 다른 형상은 모두 암시적 gprim(Cylinder/Cube/Sphere)이다.
    - 모든 면이 삼각형 (22개, `orientation = rightHanded`, 바깥 방향 감기, 닫힌 상자). 윗면(+Z)은 2x2 격자 x 셀당 2개 = **삼각형 8개**, 아랫면 2개, 옆면 3개씩. `extent`·`primvars:displayColor`(어두운 금속색) 있음.
    - 크기 1.4 (X) x 1.0 (Y) x 0.06 (Z) (로컬, 회전 전). 부모 `/World/Base`가 translate, `MountPlate`가 `rotateZ` 15도라 월드에서는 Z축으로 15도 돌아 있고 윗면 중심이 로컬 중심과 다르다 (pick 결과가 변환을 적용했는지 확인용).
    - **윗면 중심(월드, 변환 적용, 윗면 삼각형들의 넓이 가중 중심)**: `(0.293185, -1.048236, 0.065000)`. **윗면 법선(월드)**: `(0, 0, 1)`. **윗면 넓이**: `1.400000` (m², 삼각형 8개 합). 윗면 높이 z=0.065, 아랫면 z=0.005 (바닥과 떨어짐). 월드 bbox 약 (-0.51, -1.71, 0.005)..(1.10, -0.38, 0.065)로 매니폴드·받침대와 겹치지 않고, Persp 카메라에서 주관 앞(-Y)에 윗면이 보인다. 이 값들은 SDK pxr로 파일을 열어 계산해 확인했고 `tests/test_samples.py`가 검사한다 (허용 오차 1e-5).
  - 카메라 3개 `/World/Cameras/CamIso`(비스듬히 위), `/World/Cameras/CamFront`(-Y 쪽), `/World/Cameras/CamTop`(위). 모두 (0, 0.3, 1.2)를 본다.
  - 옆에 주석 사이드카 `pipe_manifold.annotations.json` (`morph.annotation.overlay` CONTRACT "사이드카 파일" 형식, `version` 1): inlet 1 (입구 플랜지 면, direction +X), outlet 2 (출구 A +Y, 출구 B +X), plane 2 (`Section A-A` 주관 x=0.3 normal X, `Section B-B` 분기관 z=1.5 normal Z), label 3 (`Valve V-101`, `Elbow 90`, `Support S-02`, description 포함). 좌표는 위 월드 위치 기준. `.json`은 `fileExtensions`에 없으므로 장면 목록에 들어가지 않는다.

## 사용 예
```python
import asyncio
import carb.eventdispatcher
from morph.stage.loader import get_loader, EVENT_OPENED, StageLoadError

loader = get_loader()
if loader:
    scenes = loader.list_scenes()
    print([s.id for s in scenes])

    async def _open():
        try:
            info = await get_loader().open_scene("samples/morph_demo.usda")
            print("opened", info.path)
        except (KeyError, RuntimeError, StageLoadError) as e:
            print("open failed:", e)
    asyncio.ensure_future(_open())

# 장면이 바뀔 때 반응 (on_startup에서 구독, on_shutdown에서 sub = None)
def _on_opened(e):
    print("stage opened:", e["scene_id"], e["path"], e["source"])
sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
    event_name=EVENT_OPENED, on_event=_on_opened, observer_name="my.ext stage opened")
```

## 금지 / 주의
- 웹에서 임의 경로·URL을 여는 메시지를 만들지 않는다 (목록 id만).
- 열기는 저장하지 않은 변경을 묻지 않고 버린다.
- `omni.usd` stage 이벤트를 직접 구독해도 되지만, "어느 장면 id가 열렸나"는 이 익스텐션 이벤트로만 알 수 있다.
- 루트 아래의 링크·정션은 따라가므로, 루트 밖 파일도 목록에 들어가 웹에서 열 수 있다 (`roots`에는 신뢰할 수 있는 폴더만 넣는다).

## 변경 이력
- 0.1.3 — 내장 샘플 `pipe_manifold.usda`에 삼각형 Mesh `/World/Base/MountPlate` 추가, 윗면 중심·법선·넓이·크기를 계약에 명시 (데이터만, API 변경 없음, 주석 사이드카 그대로).
- 0.1.2 — 내장 샘플 `pipe_manifold.usda` + 주석 사이드카 `pipe_manifold.annotations.json` 추가 (데이터만, API 변경 없음).
- 0.1.1 — 리뷰 반영 (공개 API 시그니처 변경 없음, 0.1.0 사용 예 그대로 동작). 진행 중 열기 동안 온 `OPENED`는 같은 파일일 때만 그 열기의 완료로 보고 다르면 external로 처리(`scene_id`·`path` 불일치 방지), 열기 완료 콜백이 `OPENED`보다 먼저 와도 `morph.stage.opened` 1회 보장, 웹 `list_scenes {refresh:true}` 다시 스캔 1초 1회 제한(캐시 반환), shutdown 뒤·지연 회신 취소 시 웹 회신 `unavailable`, CONTRACT에 없는 `StageLoader` 메서드는 `_` 접두어(내부)로 정리, 내장 샘플에 Persp `cameraSettings` 추가, 링크·정션 주의 명시.
- 0.1.0 — 최초 초안 + 기본 구현 (draft라 버전 유지). 구현하며 정한 점: 이벤트 페이로드의 `None` 값 키 생략, `roots`의 `=` 해석·상대 경로 건너뜀·`samples` 이름 주의, 숨김 파일 포함·링크 순환 방지, 없는 id 재스캔 1초 제한, `open_path`의 상대 경로 처리와 빈 문자열 `ValueError`, `reload`는 `reopen_stage_async`, 외부 닫기 시 `current()` 갱신(이벤트 없음), 진행 중 열기 동안 온 `OPENED`는 그 열기의 완료로 봄.
