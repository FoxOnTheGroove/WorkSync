---
name: validate-ext
description: 익스텐션 에이전트의 작업 결과나 에이전트 구성을 검증할 때 사용. 서브에이전트 위임이 끝난 뒤 결과를 수용하기 전에 반드시 실행한다.
---

# 검증 절차 (메인 세션 전용)

인자로 ext-id가 오면 그 익스텐션만, 없으면 전체를 검증한다.

## 1. 기계 검사
```bash
python tools/validate_extensions.py            # 전체
python tools/validate_extensions.py --ext <ext-id>
```
ERROR가 있으면 담당 에이전트에게 오류 내용을 그대로 전달해 수정을 위임한다. WARN은 판단해서 보고.

## 2. 소유 범위 검사
```bash
python tools/validate_extensions.py --changed-by <ext-id> --base <ref>
```
git이 없거나 ref가 없으면 `git diff --name-only`로 직접 본다. 담당 폴더 밖 변경은 되돌리고 메인 세션이 판단한다.

## 3. 의미 검사 (ext-reviewer 위임)
`ext-reviewer`에게 다음을 요청한다.
- CONTRACT ↔ 코드 정합성 (문서의 공개 API가 실제로 있는지, 시그니처 일치)
- CONTRACT 변경이 있으면 호환성 판정과 버전 증가가 맞는지
- 이 익스텐션을 사용하는 다른 익스텐션(레지스트리의 의존 관계 역방향)이 깨지는지
- 에이전트 정의가 목적에 맞는지 (트리거 키워드, 범위, 도구 최소화, 다른 에이전트와 겹침)

## 4. 빌드/테스트 (메인 세션만)
위가 통과하면 순서대로 실행한다. 병렬 실행 금지.
```
python tools/kit.py build
python tools/kit.py test <ext-id>   # 해당 익스텐션만. 인자 없으면 전체 익스텐션
```
SDK가 없거나 연결이 끊겼으면 `python tools/kit.py setup` 먼저.

## 5. 후속
- 에이전트 보고의 "다른 익스텐션에 대한 요청 사항"은 해당 담당 에이전트에게 위임한다.
- 결과를 사용자에게 요약: 통과/실패, 수정 위임 내역, CONTRACT 버전 변화.
