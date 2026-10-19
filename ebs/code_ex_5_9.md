# 5회차 · 충돌 지휘부

> 목표: SIM ⑥ `_collide_steps` 가 **어떤 순서로, 무엇과 무엇을 재고, 결과를 어디에 남기는지** 설명할 수 있게. 엔진(`collide.py`)의 속은 6회차. 오늘은 **누가 언제 부르나**.
> 시간: 약 1시간.

---

## 0. 오늘의 지도

```
⑥ for _ in _collide_steps(): await _wait_frame()          ebs_simulate.py 1864
   │                                         진행률 비중(COLLIDE_STEPS)
   ├─ 외부 (_outer)
   │   ├─ warm       _warm_steps          가까운 바운드 미리 재기    50
   │   ├─ sides      Collide._side_roots  좌우 옆 장비 하나씩        2
   │   │               └─ side_band       ← 오늘 후반
   │   ├─ faces      Collide.check_collision  세 면이 막혔나        12
   │   └─ clearance  Collide.measure_faces    빈 면은 몇 m 여유    8
   ├─ 내부 (_inner)
   │   └─ equipment  Collide.check_equipment  EBS 가 대상 장비를 뚫나 12
   ├─ verdict    build_verdict            판정 + 패널용 데이터 (7회차) 2
   └─ markers    show_markers             3D 마커 그리기 (8회차)    14
                 _keep_result → _results   장비별 기록
                 _payload(True, …)         → self._result
```

> **외부** = EBS 의 세 면(좌·우·천장)이 **주변 것** 에 막혔나. **내부** = EBS 가 **대상 장비 자체** 를 뚫고 지나가나.

---

## 1. 판을 나누는 장치 (10분)

### 1-1. 제너레이터 — `_collide_steps` (1864)
- 단계마다 `yield` → 바깥(`simulate_async`, `collide_async`)이 `await _wait_frame()` 으로 프레임 하나 넘김 → **킷이 안 얼고 진행률이 보인다.**
- 동기 버전 `_do_collide` (1758) 은 같은 제너레이터를 `pass` 로 끝까지 돈다.
- 반환값이 없다. 결과는 마지막 `self._payload(True, …)` 가 **`self._result`** 에 남긴다.

### 1-2. 진행률 — `_at`, `_reached` (1790, 1801)
- `COLLIDE_STEPS` = `(warm 50, sides 2, faces 12, clearance 8, equipment 12, verdict 2, markers 14)` — 합 100.
- `_at(step, done)` : 앞 단계 비중 합 + 이 단계 비중 × done.
- **`_learn_shares`** (1837) : 이번에 실제로 걸린 시간 비율로 다음 번 비중을 다시 잡는다 → 두 번째 SIM 부터 진행률이 실제 시간에 맞는다.

### 1-3. 시간 — `_spending`, `_spent_line` (1811, 1820)
- `with _spending(step)` : 그 단계에 **쓴** 시간만 `_spent` 에 (프레임 기다린 시간 제외).
- `_spent_line` : `"collide: outer 1.2s (warm …), inner 0.3s (equipment …), verdict …, markers …"` 한 줄을 notes 에.

---

## 2. 단계별로 (25분)

### 2-0. 시작 확인
- `_target` 없으면 "Run Prepare first", `_aligned` 아니면 "Run Align first" (payload 만 남기고 끝).
- `apart = [EBS, 대상 장비]` — **외부 판정에서 빼는 것**. `skip` 은 그 경로들.
- `cells = {left: False, ceiling: False, right: False}` — 면별 막힘.

### 2-1. warm — `_warm_steps` (1845)
- EBS 주변 **탐색 상자** (`Collide._reach_box` = EBS 상자 + 세 면 앞으로 뻗은 직육면체) 안에 걸리는 상자 목록 항목을 구하고 (`_index_inside`), 하나씩 그 아래 **지오메트리 잎과 바운드** 를 미리 계산해 `_leaves` 캐시에 넣는다 (`_gather_nearby` → `_subtree_leaves`).
- `WARM_CHUNK`(40)개마다 `yield` → 무거운 일을 잘게.
- 비중이 50 으로 가장 크다 = 외부 판정에서 가장 오래 걸리는 부분.

### 2-2. sides — `Collide._side_roots` (collide 1065)
- `side_band` 로 **왼쪽 옆 장비 하나, 오른쪽 옆 장비 하나** 를 고른다.
- 결과 `{left: [프림], right: [프림]}`. **좌면은 왼쪽 장비만, 우면은 오른쪽 장비만** 본다. **천장은 여전히 스테이지 전체.**
- notes: `"sides judged against left X, right Y; the ceiling still walks the stage"`.

### 2-3. faces — `Collide.check_collision`
- 세 면 각각이 무엇에 닿나 → `cells[face] = True/False`. `hit_count` = 막힌 면 수.

### 2-4. clearance — `Collide.measure_faces`
- 막히지 않은 면이 **가장 가까운 것까지 몇 m** 인지 → `distances[face] = {distance, prim, reach, …}`.
- notes: `"left: clear, nearest 0.4123 away (EQP_X)"` 또는 `"nothing within …"`.

### 2-5. equipment — `Collide.check_equipment`
- EBS 삼각형과 대상 장비 삼각형의 교차 → `meeting = {hit, pairs, boxes, tests}`.
  - `pairs` : (EBS 쪽, 장비 쪽 경로) 쌍. `boxes` : 겹친 자리 상자 (빨간 상자로 그림). `tests` : 시험한 삼각형 쌍 수.
- 예외가 나도 SIM 은 계속 (notes 에 남김).
- `_boxed` : 삼각형 대신 **상자로 판정한** 프림을 이유별로 → notes.

### 2-6. verdict — `build_verdict` (7회차)
- `cells`, `distances`, 내부 hit, 상자 → 판정 dict `verdict` (`marks`, `boxes`, 패널 위치 …). 실패해도 `{}` 로 계속.

### 2-7. markers
- `show_markers(EBS, cells, marks, boxes)` → 3D 선·면·상자 (8회차). 시간은 `"overlay"` 단계에.
- `self._verdict = verdict` — 오버레이·넛지가 이걸 본다.
- `_keep_result` → 기록, `_payload(True, 사유, cells, hit_count, distances, equipment_hit)`.
- `_reached("markers")`, `_spent_line`, `_learn_shares`.

> 단계 중간에 막혀도 **SIM 은 `ok=True`** 로 끝난다. "설치 가능/불가" 는 `ok` 가 아니라 **판정 내용**(`get_result` 의 `placeable`) 이다.

---

## 3. 옆 장비 고르기 — `side_band` (2202) (15분)

### 3-1. 방향
- `_sideways(ebs)` (2294) : EBS 바운드 행렬의 **1번째 행(로컬 X)** 을 XY 평면에 투영·정규화 → "좌우" 방향. 실패하면 (1, 0).
- `inward` = 그것을 90° 돌린 방향 → "앞뒤(깊이)".
- `_cast(box, way)` (2195) : 상자를 그 방향으로 투영한 구간 `(최소, 최대)`.

### 3-2. 고르기
1. `equipment_boxes` (2회차) 로 모든 장비 상자. 대상 장비 상자 `here`.
2. 대상의 좌우 구간 `my_side`, 깊이 구간 `my_deep`. 찾는 범위 `reach` = 대상 폭 × `NEIGHBOUR_REACH`(1.5).
3. 다른 장비마다:
   - 깊이 구간이 안 겹치면 → 건너뜀 (앞뒤 줄이 다른 장비).
   - 완전히 오른쪽 → 오른쪽 후보 (틈 = 그 장비 왼끝 − 내 오른끝).
   - 완전히 왼쪽 → 왼쪽 후보.
   - 좌우 폭이 **겹치면** → `tangled` (나중에 부품 단위로).
4. `_closer` : reach 안에서 더 가까우면 바꿔 적기.
5. `tangled` 장비는 `_part_gaps` (2266) 로 **부품(잎 지오메트리)** 하나하나 다시 본다. 안 보이는 부품·조상은 뺀다.
6. 반환: `{beside, left, right, sideways, inward, side, deep}` — `left`/`right` 는 장비 경로.

---

## 4. 기록과 조회 (10분)

### 4-1. `_keep_result` (1980)
`_results[장비이름 대문자] = {equipment, port_count, inside_hit, inside[], offset, faces{face: {hit, gap, name}}}`
- `offset` = 그때의 넛지 값. 면 정보는 `verdict["marks"]` 에서.

### 4-2. `get_result(equipment)` (2001) — 웹이 받는 판정
- 이름 찾기 `_result_key` : 경로를 줘도 끝 이름, 대문자, 없으면 `EQP_` 붙여서.
- 면마다 상태: 닿음 → `clash`, 여유 < 최소 여유(`_min_gap`) → `tight`, 아니면 `clear`.
- 반환 `{equipment, port_count, reason, faces{gap, name}, inside[], offset, placeable}`
  - `reason` 예: `"left clear / right tight / ceiling clear / inside clear"`.
  - **`placeable`** = 기록이 있고, 내부 간섭 없고, 모든 면이 `clear`.
- `get_results` / `list_results` : 전부.

### 4-3. 이름 — `owner_name(path)` (2045)
- 메시 경로에서 사람이 아는 이름: `GROUP_NAMES`(AMH, Construction) 를 만나면 그 이름, 아니면 search_root 바로 아래 이름(=장비), 그것도 없으면 마지막 이름. 패널의 "무엇에 닿았나" 표기에 쓴다.

---

## 5. 눈에 띈 것 (참고)

- `_outer`, `_inner` 를 바꾸는 setter 가 없어 **둘 다 늘 켜져 있다** (끄는 갈래는 지금 안 탄다).
- 천장 면은 옆 장비 한정이 없어 스테이지 전체를 본다 → 천장 쪽 판정이 상대적으로 무겁다.
- 판정 기록은 장비별로 **덮어쓴다** (같은 장비를 다시 SIM 하면 마지막 것만 남음).

---

## 6. 마무리 메모 (5분)

1. 일곱 단계와 각 단계가 하는 일
2. 외부와 내부의 차이, 각각 빼는 것
3. 옆 장비를 고르는 규칙
4. `placeable` 이 되는 조건
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

### A. 장치
1. `_collide_steps` 가 제너레이터인 이유. 동기 버전은 어떻게 도나?
2. 결과는 어디로 나오나?
3. `COLLIDE_STEPS` 일곱 단계와 비중.
4. `_learn_shares` 가 하는 일과 그 효과.
5. `_spending` 이 세지 않는 시간은?

### B. 단계
6. 시작할 때 실패하는 두 조건.
7. 외부 판정에서 빼는 둘.
8. warm 단계가 미리 하는 일과 그 결과가 담기는 곳, 몇 개마다 멈추나?
9. sides 단계의 결과 모양과, 천장은 무엇을 보나?
10. faces 와 clearance 의 차이.
11. `meeting` 의 네 키.
12. `_boxed` 는 무엇을 모으나?
13. verdict 단계가 실패하면?
14. `self._verdict` 는 언제 채워지고 누가 보나?
15. 판정이 "설치 불가" 여도 SIM payload 의 `ok` 는?

### C. 옆 장비
16. "좌우" 방향은 무엇에서 구하나? 실패하면?
17. `_cast` 가 돌려주는 것.
18. 찾는 범위 `reach` 의 식.
19. 다른 장비가 건너뛰어지는 조건.
20. `tangled` 는 무엇이고 어떻게 처리하나?
21. `_part_gaps` 가 빼는 부품.
22. `side_band` 반환의 `left`, `right` 값 모양.

### D. 기록
23. `_results` 의 열쇠와 항목 키.
24. `get_result` 의 면 상태 세 가지 규칙.
25. `placeable` 조건.
26. `get_result("6eas1201")` 이 `"EQP_6EAS1201"` 기록을 찾는 과정.
27. `owner_name` 이 이름을 고르는 순서.

### E. 응용
28. "SIM 진행률이 처음엔 들쭉날쭉하다가 두 번째부터 매끄럽다" — 왜?
29. "왼쪽에 장비가 있는데 왼쪽 면이 늘 clear" — 의심할 곳.
30. "옆 장비가 아닌 뒷줄 장비와 판정된다" — 의심할 곳.
31. 최소 여유를 바꾸면 이미 저장된 판정의 `placeable` 도 바뀌나? 왜?
32. 내부 간섭만 다시 재는 방법이 있을까? (8회차 예고: `_retest_inner`)

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. 단계마다 멈춰 프레임을 넘겨 킷이 안 얼고 진행률이 움직이게. 동기 `_do_collide` 는 같은 제너레이터를 `for _ in …: pass` 로 끝까지 돈다.
2. `self._result` (마지막 `_payload` 호출).
3. warm 50, sides 2, faces 12, clearance 8, equipment 12, verdict 2, markers 14.
4. 이번 실제 단계별 시간 비율로 다음 비중을 다시 잡는다. 두 번째부터 진행률이 실제 시간에 맞는다.
5. 프레임을 기다린 시간 (yield 밖).
6. `_target` 없음("Run Prepare first"), `_aligned` 아님("Run Align first").
7. EBS 와 대상 장비.
8. 탐색 상자 안에 걸린 항목 아래의 지오메트리 잎과 바운드를 미리 계산해 `_leaves` 에 캐시한다. 40개마다.
9. `{left: [옆장비], right: [옆장비]}` (없으면 빈 목록). 천장은 스테이지 전체.
10. faces 는 면이 막혔나(True/False), clearance 는 막히지 않은 면의 가장 가까운 것까지 거리.
11. hit, pairs, boxes, tests.
12. 삼각형 대신 상자로 판정한 프림을 이유별로.
13. `verdict = {}` 로 계속 (notes 에 사유).
14. markers 단계 (그리기 직후). 오버레이와 넛지.
15. True. 설치 가능 여부는 판정 내용(`placeable`).
16. EBS 바운드 행렬의 로컬 X 행을 XY 로 투영·정규화. 실패하면 (1, 0).
17. 상자를 그 방향으로 투영한 (최소, 최대) 구간.
18. 대상 폭(좌우 구간 길이) × 1.5.
19. 깊이(inward) 구간이 대상과 겹치지 않을 때, 대상 자신, 틈이 reach 보다 클 때.
20. 좌우 폭이 대상과 겹친 장비. `_part_gaps` 로 부품 단위로 좌우 틈을 다시 잰다.
21. 깊이가 안 겹치는 부품, 폭이 겹치는 부품, 자신이나 조상이 안 보이는 부품.
22. 장비 프림 경로 문자열 (없으면 "").
23. 장비이름 대문자. equipment, port_count, inside_hit, inside, offset, faces.
24. hit → clash, gap < 최소 여유 → tight, 그 외 clear.
25. 기록이 있고, 내부 간섭 없고, 모든 면이 clear.
26. `_result_key` 가 대문자 `"6EAS1201"` → 기록에 없으니 `"EQP_6EAS1201"` 로.
27. 끝에서부터 GROUP_NAMES 를 만나면 그것 → search_root 바로 아래 이름 → 마지막 이름.
28. 첫 SIM 은 고정 비중 `COLLIDE_STEPS`, 이후는 `_learn_shares` 로 실측 비중.
29. `side_band` 가 그 장비를 못 골랐나(깊이 안 겹침, reach 밖, 장비 상자 없음) — notes 의 "sides judged against" 줄.
30. 깊이 판정(`inward`) — EBS 로컬 X 방향이 틀어졌는지(`_sideways`), 장비 상자가 지나치게 큰지.
31. 바뀐다. `get_result` 가 부를 때마다 `_min_gap` 으로 tight 를 다시 계산하기 때문 (기록에는 gap 만 저장).
32. 있다. 넛지 후 `hold_clash(True)` → `_retest_inner` 가 `check_equipment` 만 다시 돈다 (8회차).

</details>
