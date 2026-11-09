# morph-kit 에이전트 구성

Kit SDK(kit-app-template 110.1.3)는 git에 넣지 않고 루트 `kit-app-template/`에 받아서 쓴다.

## 적용
1. `python tools/kit.py setup` — kit-app-template 110.1.3 clone, 버전 확인, `kit-app-template/source` → `source/` 정션 연결
2. 익스텐션 골격 생성: `python tools/kit.py repo template new` → Python Extension, ID는 `docs/EXTENSIONS.md`의 3개 (결과는 정션을 통해 `source/extensions/`에 생김)
3. 각 `config/extension.toml`의 version을 `0.1.0`, `[dependencies]`에 CONTRACT 헤더의 morph 의존 추가
4. `python tools/validate_extensions.py` → ERROR 0 확인
5. `python tools/kit.py build` → `python tools/kit.py test <ext-id>`

## 일상 사용 (Claude Code 메인 세션)
- 구현: "카메라 프리셋 기능 추가해줘" → 메인 세션이 `ext-camera`에 위임
- 병렬: "stream, perf 기본 구현 동시에 진행해줘" → 의존 순서상 같은 단계만 병렬
- 검증: `/validate-ext` 또는 `/validate-ext morph.camera.control`
- 새 익스텐션: `/new-ext` → 골격, CONTRACT, 에이전트, 레지스트리, 검증까지

## 구성 요소
| 파일 | 역할 |
|---|---|
| `CLAUDE.md` | 전체 규칙 (역할, 소유 범위, 통신, 네이밍, 보고 형식) |
| `.claude/agents/ext-*.md` | 익스텐션 담당 에이전트 |
| `.claude/agents/ext-reviewer.md` | 읽기 전용 검증 에이전트 |
| `.claude/skills/new-ext/` | 새 익스텐션 절차 + CONTRACT/에이전트 템플릿 |
| `.claude/skills/validate-ext/` | 검증 절차 |
| `source/extensions/*/CONTRACT.md` | 익스텐션별 공개 계약 (다른 에이전트가 읽는 유일한 문서) |
| `docs/EXTENSIONS.md` | 레지스트리, 의존 순서 |
| `tools/kit.py` | 로컬 Kit SDK 설치·연결, build/test/launch 래퍼 |
| `tools/validate_extensions.py` | 기계 검사 (버전·의존·이름·이벤트 소유·경계·에이전트 정의) |
