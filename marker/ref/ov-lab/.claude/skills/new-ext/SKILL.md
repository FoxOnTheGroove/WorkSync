---
name: new-ext
description: 새 Omniverse 익스텐션을 추가할 때 사용. 익스텐션 골격, CONTRACT.md, 담당 서브에이전트, 레지스트리 등록, 검증까지 한 번에 진행한다.
---

# 새 익스텐션 추가 절차 (메인 세션 전용)

## 1. 입력 확인
사용자에게서 다음을 확정한다. 빠진 것만 묻는다.
- domain / name → 익스텐션 ID `morph.<domain>.<name>`
- 목적 (한두 문장), 위임 트리거가 될 키워드
- 의존 익스텐션 (기존 `docs/EXTENSIONS.md` 목록에서)
- 웹 메시지 노출 여부, 성능 측정 대상 여부

중복 검사: `docs/EXTENSIONS.md`와 `.claude/agents/`에 같은 domain/ID가 있으면 멈추고 사용자에게 알린다.

## 2. 익스텐션 골격
- `repo template new`는 대화형이다. 사용자에게 `python tools/kit.py repo template new` 실행을 요청하거나(Python Extension, ID는 위에서 확정한 값), 사용자가 허락하면 기존 익스텐션 구조를 참고해 `source/extensions/<ext-id>/`를 수동으로 만든다. `kit-app-template/source`가 이 저장소 `source/`에 연결돼 있어 생성 결과가 바로 여기에 생긴다.
- `premake5.lua`가 있어야 빌드/테스트(`tests-<ext-id>.bat`) 대상이 된다.
- `config/extension.toml`의 `[package] version = "0.1.0"`, `[dependencies]`에 의존 익스텐션 추가. morph.* 의존은 `{ version = "<major>.<minor>" }`(Kit은 ^ 범위로 푼다, minor = 쓰는 기능이 들어간 제공 측 버전. 0.x는 같은 minor만 받으므로 draft 익스텐션의 minor가 오르면 사용 측 범위도 올린다).

## 3. CONTRACT.md
`CONTRACT.template.md`를 `source/extensions/<ext-id>/CONTRACT.md`로 복사하고 `{{...}}`를 채운다.
- 의존 익스텐션의 CONTRACT를 읽고, 이 익스텐션이 쓸 API/이벤트를 "수신" 섹션에 반영한다.
- 공개 API는 이 시점에 **초안이라도 확정**한다 (다른 에이전트가 병렬로 붙을 수 있게).

## 4. 담당 에이전트
`agent.template.md`를 `.claude/agents/ext-<domain>.md`로 복사하고 채운다.
- `description`에 트리거 키워드를 구체적으로 넣는다 (메인 세션의 자동 위임 정확도가 여기서 결정된다).
- 다른 에이전트와 트리거 키워드가 겹치지 않게 한다.

## 5. 등록
- `docs/EXTENSIONS.md` 표에 한 줄 추가.
- 필요하면 `source/apps/*.kit`의 `[dependencies]`에 `{ version = "<major>.<minor>" }`로 추가하고, 레지스트리 익스텐션이 늘었으면 `py -3.12 tools/kit.py lock <app>.kit`로 버전 고정을 다시 쓴다.

## 6. 검증
1. `python tools/validate_extensions.py` 실행 → 오류 0이어야 한다.
2. `ext-reviewer` 에이전트에 "새 익스텐션 <ext-id> 구성 검토"를 위임한다.
3. 지적 사항을 반영한 뒤 사용자에게 요약 보고.
