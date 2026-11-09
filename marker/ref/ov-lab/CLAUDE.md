# MORPH Omniverse Kit 프로젝트

Kit 110.x (kit-app-template 기반). 익스텐션은 계속 추가되며, **익스텐션 하나 = 폴더 하나 = 담당 에이전트 하나** 원칙을 따른다.

## 디렉터리

| 경로 | 내용 | 소유 |
|---|---|---|
| `kit-app-template/` | 로컬 Kit SDK (kit-app-template 110.1.3, **git 제외**). `source/`는 이 저장소 `source/`로 정션 연결 | 메인 세션 |
| `source/apps/*.kit` | 앱 구성: `morph.lab.kit`(데스크톱 편집), `morph.lab_streaming.kit`(livestream, 분할 뷰포트만) | 메인 세션 |
| `tools/smoke_app.py` | 실제 앱 스모크 시나리오 (CONTRACT 공개 API만 사용) | 메인 세션 |
| `tools/smoke_split.py` | 분할 전환·포커스·카메라 회귀 스모크 (데스크톱 앱, `launch ... --exec`) | 메인 세션 |
| `tools/webclient/` | 브라우저 테스트 클라이언트 (스트리밍 영상 + 웹 메시지 self test, vite) | 메인 세션 |
| `tools/kit.py` | SDK 설치·연결, 빌드/테스트/실행 래퍼 | 메인 세션 |
| `source/extensions/<ext-id>/` | 익스텐션 본체 | 해당 담당 에이전트 |
| `source/extensions/<ext-id>/CONTRACT.md` | 익스텐션 공개 계약 | 해당 담당 에이전트 |
| `docs/EXTENSIONS.md` | 익스텐션 레지스트리 | 메인 세션 |
| `.claude/agents/ext-*.md` | 익스텐션 담당 에이전트 | 메인 세션 |
| `.claude/agents/ext-reviewer.md` | 검증 전용 에이전트 (읽기 전용) | 메인 세션 |
| `.claude/skills/new-ext/`, `.claude/skills/validate-ext/` | 절차 스킬 | 메인 세션 |
| `tools/validate_extensions.py` | 구조/계약 정합성 검사 | 메인 세션 |

## 역할

### 메인 세션 (오케스트레이터 + 검증자)
- 작업을 익스텐션 단위로 쪼개 담당 에이전트(`ext-*`)에게 위임한다. 서로 의존이 없으면 병렬로 위임한다.
- 의존 관계가 있는 작업은 **제공 측 CONTRACT를 먼저 확정**한 뒤 사용 측에 위임한다.
- 공유 파일(위 표의 "메인 세션" 소유)은 메인 세션만 수정한다.
- `repo build`, `repo test`, 앱 실행은 **메인 세션만** 한다 (`_build` 충돌 방지, 동시 실행 금지). 항상 `tools/kit.py`로 실행한다 (아래 "로컬 Kit SDK").
- 위임 결과는 반드시 `/validate-ext`로 검증한 뒤 수용한다.
- 새 익스텐션은 `/new-ext`로 만든다 (익스텐션 골격 + CONTRACT + 에이전트 + 레지스트리 + 검증까지 한 번에).
- 에이전트가 보고한 "다른 익스텐션에 대한 요청 사항"은 메인 세션이 해당 담당 에이전트에게 다시 위임한다.

### 익스텐션 에이전트 (`ext-*`)
- 자기 폴더 `source/extensions/<ext-id>/`만 수정한다.
- 다른 익스텐션은 **그 익스텐션의 CONTRACT.md만** 읽고 사용한다. 내부 모듈 import, 내부 동작에 기대는 코드 금지.
- 공개 API·이벤트·설정이 바뀌면 같은 작업 안에서 CONTRACT.md와 `extension.toml` 버전을 함께 갱신한다.
- 다른 익스텐션 수정이 필요하면 직접 고치지 않고 종료 보고에 요청 사항으로 남긴다.

## 로컬 Kit SDK

kit-app-template 110.1.3을 루트 `kit-app-template/`에 두고 빌드·테스트에 쓴다 (git 제외, `.gitignore`).
`kit-app-template/source` → 이 저장소 `source/` 정션이라 SDK가 우리 익스텐션을 그대로 빌드한다. SDK 폴더 안의 파일은 직접 고치지 않는다.

```
python tools/kit.py setup              # 없으면 clone + 버전 확인 + source 연결 (재실행 안전)
python tools/kit.py status
python tools/kit.py build
python tools/kit.py test [ext-id...]   # kit.exe + omni.kit.test 직접 실행. 인자 없으면 전체 익스텐션
python tools/kit.py launch [app.kit]   # kit.exe로 앱 직접 실행 (기본 morph.lab.kit). *streaming* 앱은 --no-window --reset-user 자동
python tools/kit.py lock [app.kit] [-u] # 앱의 레지스트리 익스텐션 버전 고정(.kit 끝 GENERATED PART). -u = 최신으로 다시 풂
python tools/kit.py smoke [app.kit]    # 실제 앱 스모크: tools/smoke_app.py (2x2 분할·포커스·카메라·웹 채널·데모 장면 열기·배관 샘플 주석), 뷰별 스크린샷 _smoke/ (scene_view*.png = 데모 장면, anno_view*.png = 주석)
python tools/kit.py repo <args...>     # 그 외 repo 명령 (예: repo template new)
npm --prefix tools/webclient run dev   # 브라우저 테스트 (스트리밍 앱 실행 중일 때), tools/webclient/README.md
```

빌드 산출물은 `kit-app-template/_build/windows-x86_64/release/`에 생긴다.
스마트 앱 컨트롤이 켜져 있으면 `build`가 premake 단계(서명 없는 `premake5.exe`)에서 실패할 수 있다 (2026-10-31에는 통과). Kit SDK·의존 익스텐션 다운로드는 그 전에 끝나므로 `test`에는 지장 없다.
`launch`/`smoke`는 `repo launch` 대신 kit.exe로 `_build/.../apps/<app>.kit`을 직접 띄운다 (앱 등록 `define_app`은 SDK `premake5.lua` 수정이 필요해서 쓰지 않음). 첫 실행은 레지스트리에서 의존 익스텐션을 받느라 1분쯤 걸린다.
이 PC의 `python`은 MS Store 스텁이라 `py -3.12 tools/kit.py ...`로 실행한다.

## 계약 (CONTRACT.md)

다른 에이전트가 이 익스텐션을 "잘 쓰게" 만드는 유일한 문서다. 템플릿: `.claude/skills/new-ext/CONTRACT.template.md`.

- CONTRACT에 없는 것은 내부 구현이다. 다른 익스텐션은 의존하지 않는다.
- 필수 섹션: 헤더(버전/상태/담당 에이전트/의존), 목적, 언제 쓰나, 공개 Python API, 이벤트(발행/수신), 사용 예, 변경 이력.
- CONTRACT 버전 = `extension.toml`의 `[package] version`. 검증 스크립트가 확인한다.
- 의존 버전: morph.* 의존(익스텐션 toml, 앱 .kit)은 `{ version = "<major>.<minor>" }`. Kit은 ^ 범위(같은 major, 이 버전 이상. **0.x는 같은 minor** — `^0.1`은 0.2.0을 못 받는다)로 푼다. draft(0.x) 익스텐션의 minor를 올리면 앱 .kit 범위도 함께 올린다. 레지스트리 익스텐션(omni.kit.livestream.* 등)은 앱 .kit의 고정 블록이 정확한 버전을 잡고, Kit SDK에 든 익스텐션은 SDK 버전(110.1.3)이 잡는다. 검증 스크립트가 범위 형식·major·상한을 확인한다.
- 버전 규칙: 호환 깨짐 → major, 기능 추가 → minor, 내부 수정 → patch. 상태가 `draft`인 동안은 0.x.
- 사용 예는 **복사해서 바로 동작하는 코드**로 쓴다. 다른 에이전트가 가장 먼저 보는 곳이다.

## 익스텐션 간 통신

- **직접 호출**: `extension.toml [dependencies]`에 선언한 익스텐션의 CONTRACT 공개 API만 호출한다.
- **이벤트 (느슨한 결합 우선)**: 이름은 `morph.<domain>.<event>` (소문자, `_` 허용). 각 이벤트는 **발행하는 익스텐션 하나가 소유**하고 페이로드 스키마를 그 CONTRACT에 정의한다. Kit 110의 `carb.eventdispatcher`를 사용한다.
- **웹 클라이언트 메시지**: 웹과의 통로는 `morph.stream.messaging` 하나뿐이다. 다른 익스텐션은 그 CONTRACT의 등록 API로 자기 메시지를 노출한다. livestream 메시징을 직접 쓰지 않는다.
  - 메시지 type은 `<ext-id>.<name>` (예: `morph.viewport.split.set_layout`). 소유는 익스텐션 단위다 (2026-10-31 결정, stream 1.0).
  - 웹 요청 회신은 성공·실패 모두 요청과 같은 type·id로 `{ "ok": true, ... }` / `{ "ok": false, "error": <code>, "message": <설명> }`. 세부 규칙은 `morph.stream.messaging` CONTRACT.
- **성능 측정**: 측정 구간 표시는 `morph.perf.monitor`의 CONTRACT API를 쓴다. 각자 타이머를 만들지 않는다.

## 네이밍

- 익스텐션 ID: `morph.<domain>.<name>` (예: `morph.camera.control`)
- 폴더: `source/extensions/<ext-id>/`
- 파이썬 패키지: `morph/<domain>/<name>/`
- 담당 에이전트: `ext-<domain>` (domain이 겹치면 `ext-<domain>-<name>`)

## 에이전트 종료 보고 형식 (모든 ext-* 공통)

```
### 변경 파일
### CONTRACT 변경 (없음 / 있음: 요약, 호환성, 새 버전)
### 다른 익스텐션에 대한 요청 사항 (대상 ext-id, 내용)
### 메인 세션 확인 필요 (빌드/테스트/앱 실행 포인트)
```
