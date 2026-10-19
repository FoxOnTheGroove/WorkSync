# 3회차 · prepare · 피봇 · 포트 좌표

> 목표: SIM ① `_do_prepare` 가 **어떤 장비에, 어떤 EBS 를, 어떤 피봇 기준으로** 세울지 정하는 과정과, 그 판단에 쓰는 **포트 월드 좌표** 가 XML 에서 어떻게 나오는지 설명할 수 있게.
> 시간: 약 1시간. 1회차 흐름도의 ① 칸을 채운다.

---

## 0. 오늘의 지도

```
_do_prepare(eqp)                         실패해도 지금 SIM 유지 (1회차)
 └─ _prepare(eqp)
     ├─ 장비 찾기       _resolve_by_name / _resolve_by_selection   (2회차)
     ├─ 포트 수         get_port_count  → 2 or 3 아니면 실패
     ├─ EBS 고르기      2포트 → _ebs_path_2port, 3포트 → _ebs_path_3port
     ├─ 포트 1 좌표     _port_one → compute_port_points           ← 오늘 후반
     ├─ 피봇 고르기     _pick_anchor                              ← 오늘 전반
     └─ _next = {equipment, eqp_id, port_count, ebs, anchor}
```

> **피봇(anchor)** = 장비 속 6뎁스 프림 하나. EBS 를 놓을 기준점이다. "어느 6뎁스냐" 를 고르는 게 `_pick_anchor`.

---

## 1. `_prepare` (1522) — 실패 갈래 표 (10분)

위에서 아래로 읽으며 **실패 지점과 돌려주는 값** 을 표로 확인한다.

| 순서 | 실패 조건 | `code` | `say` |
|---|---|---|---|
| 1 | 스테이지 없음 | other | `SAY_STAGE` |
| 2 | 장비 프림 못 찾음 | other | `SAY_EQP` |
| — | (여기서 `self._tried = eqp_id`) | | |
| 3 | XML 에 포트 정보 없음 | **port** | `SAY_XML` |
| 4 | 포트 수가 2·3 아님 | **port** | `SAY_PORTS` |
| 5 | 그 포트 수의 EBS 경로가 틀림 | other | `SAY_EBS` |
| 6 | 피봇이 포트 1 에서 너무 멂 | **pivot** | `SAY_PIVOT` |

- 입력 id 가 비었으면 **뷰포트 선택** 에서 장비를 찾는다.
- 성공하면 `_target` 이 아니라 **`_next`** 에 담는다 → `_do_prepare` 가 성공일 때만 `_target` 으로 옮긴다.
- `_tried` 는 장비를 찾은 직후에 적힌다 → 실패 패널 "사유 : id" 의 id.

---

## 2. 피봇 후보 모으기 (15분)

### 2-1. `resolve_anchor(prim, depth=6)` (2366) — 기본 후보
- 매번 **첫 자식** 으로 내려가 6단계. `Scope` 는 단계로 안 세고 통과(`PASS_TYPES`).
- 반환 `(프림, 6뎁스까지 갔나)`. 자식이 끊기면 그 자리와 False.

### 2-2. `_branch_tips(prim)` (1652) — 갈래 후보
- 1뎁스(첫 자식)에서 시작해 **1~4뎁스의 모든 자식을 갈래로** 타고 5뎁스까지 간다.
- 5뎁스마다 **그 첫 자식(=6뎁스)** 을 후보로.
- 반환 `(5뎁스 목록, 6뎁스 목록)`. `_pick_anchor` 는 6뎁스 목록만 쓴다.
- `_level_kids(parent)` (1641) : 자식을 순서대로, Scope 는 풀어서 그 자식을 넣는다.

> 결국 1뎁스는 첫 자식 하나, 2~5뎁스는 모든 갈래, 6뎁스는 각 갈래의 첫 자식.

### 2-3. `_module_of(prim)` (1671)
- 속성 이름을 **영숫자만 남기고 소문자로** 바꿔 `MODULE_ATTR` (`omni:hoops:metadata:Identity Data:MODULE`) 과 비교.
- 값이 있으면 문자열, 속성이 없으면 **None**.

---

## 3. 피봇 고르기 — `_pick_anchor` (1576) (15분)

### 3-1. 순서
1. **후보 `every`** = 기본 후보(6뎁스까지 갔을 때만) + 갈래 후보(중복 제외).
   아무것도 없으면 장비 프림(또는 끊긴 자리) 하나로.
2. **단계(tier)별 거르기** — `MODULE_TIERS` 순서로 처음 걸리는 단계의 후보만 `pool` 로.

   | 단계 | 맞는 조건 (`_module_fits`) |
   |---|---|
   | `MAINBODY` | 값이 정확히 MAINBODY |
   | `MAIN` | MAIN 을 포함, **MAINT 는 제외** |
   | `EFEM` | EFEM 을 포함 |

3. **`pool` 이 하나면** → 검증 없이 그것.
4. 여럿이거나 없으면(없으면 `every` 전체) → **포트 1 거리로 검증**:
   - 포트 1 좌표가 없으면 → 검증 못 하고 `pool[0]`.
   - 기본 후보가 pool 에 있고 포트 1 과 `PIVOT_APART`(0.505) 이내면 → 기본 후보.
   - 아니면 → **포트 1 에 가장 가까운** 후보.
   - 그 거리도 0.505 이상이면 → 실패 사유 문자열 반환 (`SAY_PIVOT`).
5. 어느 경우든 notes 에 한 줄: `"{id}: pivot {이름}, {단계} of {N} checked, {거리} from port 1"`.

### 3-2. 거리
- `_apart(prim, port)` (1634) : 프림 월드 위치와 포트 1 의 **XY 평면 거리** (높이 무시).

---

## 4. 포트 월드 좌표 — `compute_port_points` (2637) (20분)

`_port_one` (1625) 이 부른다. **지금 SIM 에서 포트 좌표는 피봇 검증에만 쓰인다.** 실제 EBS 위치는 4회차의 `pivot_target` 이 피봇에서 바로 구한다 (포트 좌표로 위치를 잡던 `compute_target` 은 현재 미사용).

### 4-1. 큰 흐름
```
대표 주소 addr_a  = _port_addr[id]
다른 주소에 걸친 포트들 spilled
레일 찾기 find_rail(addr_a, prefer=spilled)  → (rail 프림, 이웃 주소 addr_b, 축 0=X/1=Y)
레일 길이 length = (cad_b - cad_a)[축] / CAD_PER_UNIT    (CAD → 씬 단위)
레일 시작점 start = 레일 중심[축] - length/2
self._rail_frame = (시작점, 끝점, 축)        ← 진단용 _measure 만 쓴다
포트별 축 좌표 along = _coords_by_puls(...)   (기본 모드)
포트별 점 = 레일의 다른 두 축은 그대로 + 축만 along
반환 (points{번호: Vec3d}, 축, rail)      ← 좌표는 레일의 "부모 공간"
```
- `_port_one` 은 `_parent_world(rail).Transform(points[1])` 로 **월드** 좌표를 만든다.

### 4-2. 레일 찾기 — `find_rail` (2548), `_rails_from` (2587), `_rail_axis` (2609)
- 레일 프림 이름 규칙: **`rail_<주소a>_<주소b>`**. `_rails_from` 이 처음 한 번 색인(`_rail_index {a: [(프림, b)]}`)을 만든다. 레일 뿌리가 없으면 스테이지 전체 `_walk`.
- `_rail_axis(a, b)` : 두 주소 CAD 좌표 차이가 **한 축으로만** `CAD_SLACK`(0.1) 넘게 움직이면 그 축. 아니면 None (코너/대각선 → 건너뜀).
- 직선 레일이 여럿이면: spilled 주소로 끝나는 레일 → spilled 와 축이 다른 레일 → 첫 번째.

### 4-3. 포트 좌표 — `_coords_by_puls` (2720) (기본 모드)
- `_offset_scale` 기본값 `SCALE_SNAP` → puls 방식으로 계산 (snap 보정 `_snap_shift` 는 미사용 `compute_target` 안에만 있다).
- 포트마다:
  - 그 포트의 주소 블록 시작 = `start + (주소cad - 기준cad)[축] / CAD_PER_UNIT`
  - `_addr_step(주소, 축)` (2624) : 그 주소에서 같은 축으로 이어진 다음 주소까지의 **구간 길이와 펄스**
  - 축 좌표 = 블록 시작 + 방향 × `offset × 구간길이 / 펄스`
- 포트 간 평균 간격(pitch)으로 **가상 포트 0** = 포트 1 + pitch.
- 실패하면 `_fail(...)` → `_why` 에 한국어 사유, None.

### 4-4. 쓰이지 않는 갈래
- `_coords_by_offset` (2698), `_rebase_offsets` (2764), `_port_spacing` (2789) : `SCALE_FIXED` 모드용 (`offset / OFFSET_PER_UNIT`). `_offset_scale` 을 바꾸는 setter 가 없어 **지금은 안 탄다.**

### 4-5. 도우미
- `_local_translation(prim)` (2879) : 부모 안에서의 위치.
- `_parent_world(prim)` (2888) : 부모까지의 월드 행렬.

---

## 5. 눈에 띈 것 (참고)

- `_pick_anchor` 는 MODULE 속성이 **전혀 없는** 장비도 동작한다 (모든 단계에서 빈 pool → 전체 후보 검증).
- `PIVOT_APART` 와 `PIVOT_SHIFT` 가 같은 값(0.505)이지만 쓰임이 다르다: 앞은 "포트 1 과 이만큼 이내" 판정, 뒤는 4회차 "피봇에서 왼쪽으로 이만큼" 배치.
- 포트 좌표 계산은 notes 에 **계산 과정을 전부** 남긴다 → 문제가 나면 payload 의 `notes` 를 보면 된다.

---

## 6. 마무리 메모 (5분)

1. `_prepare` 의 실패 여섯 갈래
2. 피봇 후보가 모이는 방식 (기본 + 갈래)
3. MODULE 단계와 검증 규칙
4. 포트 1 월드 좌표가 나오기까지 (주소 → 레일 → 축 → puls)
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

### A. `_prepare`
1. 실패 갈래 여섯을 순서대로, 각 `code` 와 함께.
2. 입력 id 가 비었으면 장비를 어디서 찾나?
3. `_tried` 는 어느 시점에 적히나? 그 전에 실패하면 실패 패널의 id 는?
4. 2포트·3포트 EBS 는 무엇으로 고르나?
5. 성공하면 결과를 어디에 담나? 왜 `_target` 이 아닌가?
6. `_next` 의 다섯 키.

### B. 후보
7. `resolve_anchor` 는 어떻게 내려가나? Scope 는?
8. `_branch_tips` 가 갈래로 타는 뎁스 범위와, 6뎁스를 고르는 방식.
9. `_level_kids` 가 Scope 를 다루는 방식.
10. `every` 는 무엇으로 이뤄지고, 비면 무엇을 쓰나?
11. `_module_of` 가 속성 이름을 비교하는 방법. 속성이 없을 때 반환값.

### C. 고르기
12. 단계 셋과 각 단계의 맞는 조건.
13. MODULE 값이 `MAINT_PANEL` 이면 MAIN 단계에 드나?
14. 어떤 단계의 후보가 하나뿐이면?
15. 여러 개일 때 검증 규칙 (기본 후보 우선 조건, 아니면?).
16. 포트 1 좌표를 못 구하면?
17. 검증에서 실패하는 조건과 그때의 `code`, `say`.
18. 거리는 몇 차원 거리인가?

### D. 포트 좌표
19. `compute_port_points` 의 반환 셋과, 좌표가 속한 공간.
20. 레일 프림의 이름 규칙.
21. 레일이 "직선" 으로 인정되는 조건.
22. 직선 레일이 여럿일 때 고르는 순서.
23. 레일 시작점은 어떻게 구하나?
24. `_rail_frame` 에 무엇이 담기고 누가 쓰나?
25. 기본 좌표 모드와, 포트 하나의 축 좌표 식.
26. `_addr_step` 이 돌려주는 둘.
27. 가상 포트 0 은 어떻게 만드나?
28. 지금 안 쓰이는 좌표 함수 셋과 그 이유.
29. 포트 1 월드 좌표를 만드는 마지막 변환.

### E. 응용
30. "유효하지 않은 pivot 데이터" 가 뜬다 — notes 에서 무엇을 보나?
31. 어떤 장비 피봇이 엉뚱한 모듈에 잡힌다 — 무엇을 확인하나?
32. "직선 레일 없음" 이 뜬다 — 가능한 원인 둘.
33. 속성 이름이 `omni:hoops:metadata:Identity_Data:Module` 로 바뀌어도 동작하나? 왜?

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. 스테이지 없음(other) → 장비 없음(other) → XML 포트 없음(port) → 포트 수 불일치(port) → EBS 경로 오류(other) → 피봇 멂(pivot).
2. 뷰포트 선택 (`_resolve_by_selection`).
3. 장비 프림을 찾은 직후. 실패 패널은 입력 id 를 먼저 쓰고, 입력이 비었을 때(선택으로 SIM)만 `_tried` 를 쓴다. 그 전에 실패하고 입력도 비었으면 id 없이 사유만.
4. 포트 수. 2 면 `_ebs_path_2port`, 3 이면 `_ebs_path_3port`.
5. `_next`. 실패해도 지금 SIM(`_target`)을 깨지 않으려고 — `_do_prepare` 가 성공일 때만 옮긴다.
6. equipment, eqp_id, port_count, ebs, anchor.
7. 매번 첫 자식으로 6단계. Scope 는 단계로 안 센다.
8. 1~4뎁스의 모든 자식을 갈래로 5뎁스까지. 5뎁스마다 첫 자식을 6뎁스 후보로.
9. Scope 를 만나면 그 자식들을 같은 자리에 풀어 넣는다.
10. 기본 후보(6뎁스 도달 시) + 갈래 후보(중복 제외). 비면 장비 프림(또는 끊긴 자리) 하나.
11. 영숫자만 남기고 소문자로 바꿔 비교. 속성이 없으면 None.
12. MAINBODY 정확히 일치 / MAIN 포함·MAINT 제외 / EFEM 포함.
13. 안 든다 (MAINT 포함이라 제외).
14. 검증 없이 그것.
15. 기본 후보가 pool 에 있고 포트 1 과 0.505 이내면 기본 후보, 아니면 포트 1 에 가장 가까운 후보.
16. `pool[0]` 을 검증 없이.
17. 고른 후보도 포트 1 과 0.505 이상. `code=pivot`, `say=SAY_PIVOT`.
18. XY 평면 2차원 (높이 무시).
19. (포트번호→Vec3d, 축, 레일 프림). 레일의 부모 공간.
20. `rail_<주소a>_<주소b>`.
21. 두 주소 CAD 차이가 한 축으로만 `CAD_SLACK`(0.1) 넘게 움직일 때.
22. spilled 주소로 끝나는 레일 → spilled 와 축 방향 차이가 있는 레일 → 첫 번째.
23. 레일 중심(부모 안 위치)의 축 값 − 레일 길이/2.
24. (시작점, 끝점, 축). 지금은 진단용 `_measure` (sweep, snap) 만 쓴다. prepare 가 실패하면 `_do_prepare` 가 되돌린다.
25. puls (기본 `SCALE_SNAP`). 블록 시작 + 방향 × offset × 구간길이 / 펄스.
26. 구간 길이(씬 단위), 펄스.
27. 포트 1 + 포트 간 평균 간격.
28. `_coords_by_offset`, `_rebase_offsets`, `_port_spacing`. `_offset_scale` 을 FIXED 로 바꾸는 곳이 없다.
29. `_parent_world(rail).Transform(points[1])`.
30. `"{id}: pivot …, {단계} of N checked, {거리} from port 1"` 줄 — 몇 단계에서 몇 개 중 고르다 거리가 얼마였나.
31. 그 장비 6뎁스 후보들의 MODULE 값 (MAINBODY 가 둘 이상인지, MAIN 이 엉뚱한 곳에 있는지).
32. 레일 뿌리 아래에 `rail_<addr>_*` 프림이 없음, 있어도 코너/대각선이라 한 축 직선이 아님 (또는 CAD 좌표 없음).
33. 동작한다. 영숫자만 남기고 소문자로 바꿔 비교하므로 `:` `_` 공백과 대소문자 차이는 무시된다. 단 글자 자체가 빠지거나 바뀌면 안 된다.

</details>
