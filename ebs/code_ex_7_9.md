# 7회차 · 판정에서 패널로 (`build_verdict` · 오버레이 패널)

> 목표: 판정이 **`verdict` dict** 로 정리되고, 오버레이가 그것을 읽어 **패널을 만들고(생성) 화면에 앉히는(배치)** 과정을 끝까지 따라갈 수 있게. 마지막에 "어떤 수정은 어디서 하나" 표를 직접 만든다.
> 시간: 약 1시간 20분.

---

## 0. 오늘의 지도

```
_collide_steps 의 verdict 단계
 └─ build_verdict(ebs, cells, distances, inside, boxes)     ebs_simulate.py 2058
     ├─ 높이별 기준점: centre(80%) · inside_at(45%) · name_at(45%, _pinned)
     ├─ Collide._face_marks(...)   면마다 mark (상태·거리·선 두 끝·패널 자리)  collide.py 296
     ├─ _grip_spot(...)            손잡이 자리 (8회차)
     └─ return verdict  →  self._verdict

run_simulate 의 panel.show
 └─ EbsSimulateOverlay.show()  →  refresh()                 overlay.py 416
     ├─ clear()
     ├─ _fill(verdict)            ← 패널 생성
     │    ├─ _verdict_panel       판정 판 · 이름표 · 이격 판
     │    └─ _face_panel × 3      면 판 (먼 것부터)
     ├─ EbsSimulateGrip.place     손잡이 (8회차)
     ├─ _place()                  ← 패널 배치
     └─ _start()                  매 프레임 _tick 구독

매 프레임 _tick: 작업중 판 → 실패 판 → 손잡이 hover → 앞뒤 재정렬 → _place
```

---

## 1. `build_verdict` (2058) (15분)

### 1-1. 기준점 (모두 EBS 로컬 상자 기준 → 월드)
| 키 | 식 | 쓰는 패널 |
|---|---|---|
| `centre` | 상자 가운데, 높이만 `VERDICT_HEIGHT`(0.8) | 판정 판 |
| `inside_at` | 〃 `CLASH_HEIGHT`(0.45) | (지금 안 씀 — 내부 간섭 판은 비활성) |
| `name_at` | 〃 `NAME_HEIGHT`(0.45), **`_pinned`** 적용 | 이름표 |

- **`_pinned`** (2095) : 넛지만큼 **반대로 되돌린** 자리 → EBS 를 밀어도 **이름표는 제자리**. (판정 판은 EBS 를 따라간다 — 8회차 slide)

### 1-2. 반환 키
`marks, right, grip, offset, centre, inside_at, name_at, name, inside, boxes, faces, blocked, placeable`
- `faces` : 상태가 clear 가 아닌 면 목록 `[{face, name, state}]` → "외부 간섭" 여부.
- **`placeable` = 내부 간섭 없음 and 막힌/빠듯한 면 없음.**
- `right` : EBS 오른쪽 방향 (`_right_way(0)` = 카메라 x 축).

### 1-3. 면 mark — `Collide._face_marks` (collide 296)
면마다 하나:

| 키 | 뜻 |
|---|---|
| `face` | left / ceiling / right |
| `state` | 막힘 → `clash`, 거리 < 최소 여유 → `tight`, 아니면 `clear` |
| `distance` | **미터** (월드 길이 × metersPerUnit). 파고들면 음수. 못 찾으면 None |
| `min_gap` | 그 면의 최소 여유 |
| `name` | 가장 가까운 것(또는 막은 것)의 `owner_name` |
| `from` / `to` | 거리선 두 끝 (면 → 바깥) |
| `at` | 선 가운데 = **면 패널 기준점** |
| `lead` / `spot` / `tick` | 안내선 꺾은선, 실제 닿은 점, 끝 눈금 방향 |
| `way` | 면 바깥 방향 |
| `stale` | 넛지로 옛 값이 된 표시 (8회차) |

- 선 시작점: 면 가운데, 단 앞뒤는 **EBS 앞쪽 끝**(`LEAD_FRONT = -1` → 로컬 front 축 최소).
- 안내선(`_lead_patch`, `_lead_path`, `_stop_at`, `_sliced`, `_same_patch`) : 거리선 끝에서 실제 닿은 점(`spot`)까지 **축을 따라 꺾어** 간다. 지나는 길의 메시를 잘라 둔 단면(patch)에 부딪히면 멈춘다. **이름과 역할만.**

---

## 2. 오버레이 뼈대 (15분) — `ebs_simulate_overlay.py` 141–

### 2-1. 클래스 API (모두 classmethod, 뷰포트별 인스턴스 하나)
| 메서드 | 하는 일 | 누가 |
|---|---|---|
| `wake` | 인스턴스(프레임)만 세움 | run_* 시작 |
| `hide` | 그린 판 지우기 (`clear`), 프레임은 남김 | SIM 시작, Clear |
| `show` | 판정을 읽어 다시 그리고 배치 (`refresh`) | SIM 끝 |
| `build` / `reveal` | 그리기만 / 배치만 (나눠 쓰고 싶을 때) | — |
| `fail(why)` | 실패 문구 등록 (일이 끝나면 가운데에 잠깐) | SIM 실패 |
| `restate` | **다시 만들지 않고** 자리·글자·색만 | 넛지, 재판정 |
| `destroy` | 프레임까지 놓음 | 종료 |

- `_get` (201) : 뷰포트 창 이름으로 인스턴스 찾기, 없으면 `_build`.
- `sim()` : `attach` 로 걸어 둔 EbsSimulate (1회차).

### 2-2. `_build` (251)
- 뷰포트 창에 `FRAME_ID` 프레임 → ZStack 안에 **`_stack`**(판들이 들어갈 곳), **작업중 판**(`_build_work`), **실패 판**(`_build_fail`).
- 작업중·실패 판은 **한 번 지어 두고 보였다 숨겼다** 한다.
- `viewport_api` 를 잡아 투영에 쓴다. `_start()` 로 프레임 구독.

### 2-3. 매 프레임 — `_tick` (722)
1. `_work_place` : 바쁘면 가운데에 작업 이름·진행 막대·% (`busy()`, `get_progress()`).
2. `_fail_place` : 일이 끝났고 실패 문구가 있으면 가운데에 `FAIL_HOLD`(1초) 두고 `FAIL_FADE`(1초) 동안 흐리게.
3. `EbsSimulateGrip.sweep()` : 손잡이 hover (8회차).
4. `_reorder` : 면 판 앞뒤가 바뀌었으면 다시 짓기.
5. `_place` : 판 배치.

---

## 3. 패널 생성 (20분)

### 3-1. `_fill` (435)
- 면마다 깊이 기준점(`_depths[face]` = mark 의 `from` 또는 `at`)을 적고, **카메라에서 먼 것부터** 정렬 (`_depth` = 카메라 공간 z).
- `_stack` 안에 **판정 판들 → 면 판들(먼 → 가까운)** 순서로 짓는다. omni.ui 는 **나중에 지은 것이 위** → 가까운 면 판이 위에 온다.

### 3-2. 판 하나 — `_floating(at, fill, ground, anchor, step, share, group, key, on, wide)` (478)
- `Placer` → `ZStack(폭 wide 또는 내용 맞춤)` → 배경 `Rectangle(ground)` + `fill()`(글줄).
- `self._marks` 에 **항목 하나** 를 추가:

  `[placer, panel, at, anchor, step, share, group, key, on, wide]`

  | 자리 | 뜻 |
  |---|---|
  | `at` | 매달 월드 좌표 |
  | `anchor` | 기준점의 어느 쪽에 붙나: MIDDLE / ABOVE / BELOW / LEFT / RIGHT |
  | `step`, `share` | 같은 쪽에 여러 판을 쌓을 때 몇 번째, 몇 개 중 |
  | `group` | 같은 줄에 나란히 서는 판들 (폭을 맞춘다) |
  | `key` | 나중에 찾아 고칠 이름 (`("verdict","centre")`, `("face","left")` …) |
  | `on` | 보일지 |

- `_grounds[key]` 에 배경 사각형을 모아 둔다 → 색만 바꿀 때.

### 3-3. 글줄 — `_one`, `_label`, `_say` (522, 621, 632)
- `_one(text, ink, key, size)` : `_floating` 에 넘길 "그리기 함수" 를 만들어 준다. 크기에 비례해 여백.
- `_label(..., key)` : 글줄을 만들고 `_texts[key]` 에 적어 둠. `_say(key, text)` 로 글자만 갈아 끼운다.

### 3-4. 판정 판 · 이름표 · 이격 판 — `_verdict_panel` (497)
| 판 | 기준점 | 색 | 글 | 비고 |
|---|---|---|---|---|
| 판정 | `centre` | `_judge` 가 녹/적 | `_judge` 가 채움 | 손잡이 잡는 동안 숨김 |
| 이름표 | `name_at` | `COLOR_NAME` | `" 장비id "`, 크기 `NAME_SIZE` | 이름 없으면 숨김 |
| 이격 | 손잡이 아래(`grip.under`) | `COLOR_CAN` | `"+12mm"` / `"0mm"` | `_dial` 이 숫자 모양별 글줄 |

- **`_judge`** (511) : `placeable` 이면 `CAN` + `COLOR_PLACE`(녹), 아니면 `CANNOT` + 사유(`OUTER_HIT` / `INNER_HIT` / `BOTH_HIT`) + `COLOR_CANNOT`(적). `_restate` 에서도 불린다.
- `_dial` (547) : 숫자만 바뀌는 글을 **글자 모양(숫자→0)별로 글줄을 따로** 두고 보이기만 바꾼다 → 깜박임 방지. 작업중 판의 `_work_word` 도 같은 수법.

### 3-5. 면 판 — `_face_panel` (656)
면마다 최대 세 판, 모두 기준점 `mark["at"]`(선 가운데):

| 판 | 내용 | 붙는 쪽 (좌·우 면) | 붙는 쪽 (천장) |
|---|---|---|---|
| 상태 | `" 충돌"` / `" 간섭"` / `" 여유"` | ABOVE | LEFT |
| 거리 | `" 412mm"` (폭 `GAP_WIDTH` 고정) | BELOW, step 0 | RIGHT, step 0 |
| 최소 여유 | `" (최소간격 : 600mm)"` | BELOW, step 1 | RIGHT, step 1 |

- 천장만 좌우로 나란히(`SIDE_BY_SIDE`), 좌·우 면은 위아래.
- 색: clear 면 `COLOR_CAN`(밝은 판, 검은 글), 아니면 `COLOR_CANNOT`(붉은 판, 흰 글). `_repaint` 가 다시 칠한다.
- `_wall(mark)` (695) : 좌·우 면은 선이 시작하는 **벽 자리**(`from`)를 `_walls` 에 적어 둔다 → 배치에서 판이 벽에서 너무 멀어지지 않게.

---

## 4. 패널 배치 — `_place` (744) (20분)

**모든 판의 화면 자리는 여기 한 곳에서 정해진다.**

1. 프레임 크기. `hold` = 지금 SIM 중인가.
2. 1차 순회: 그룹별 가장 넓은 판(`widest`), 벽에 붙는 판의 가장 넓은 폭(`broad`).
3. 2차 순회 (판마다):
   1. **투영** `base = _to_screen(at, clip=벽판이 아니면)` — 카메라 뒤면 None, clip 이면 화면 밖도 None.
   2. 벽판이면 **`_hug`** (705) : 선 가운데가 벽에서 `최대폭/2 + FACE_MARGIN`(12px) 보다 멀면 거기까지만 띄운다.
   3. 크기 `_size(entry)` (798) : 막 지어 0 이면 **같은 자리의 지난 크기** 를 쓴다 (첫 프레임 튐 방지).
   4. **선과 판 사이 여백** `room = _room_at(at, base)` (810) : 월드 `PANEL_ROOM`(0.024) 이 화면에서 몇 px 인지. 너무 작으면 `LINE_ROOM`(6px). **벽에 붙여 옮기기 전 자리(base)로** 잰다.
   5. 쌓기 간격 `stack = 판높이 × (1 + PANEL_GAP 0.1)`.
   6. anchor 별 좌표:
      - MIDDLE : 가운데 정렬
      - ABOVE : 기준점 위로 `판높이 + room + step×stack`
      - BELOW : 아래로 `room + step×stack`
      - LEFT / RIGHT : 옆으로 `room`, 세로는 `(step − (share−1)/2) × stack` 만큼 (여러 판을 가운데 기준으로 쌓기)
   7. 프레임 밖이면 숨김 (`_outside`).
   8. **SIM 중이거나 크기 0 이면 화면 밖(`PANEL_AWAY`)에 둔다** — 숨기지 않고 치워 두어야 크기 계산이 계속된다.
   9. `placer.offset_x/y` 에 쓰고 보이기.

### 4-1. 투영 — `_to_screen` (832), `_to_window` (853)
- 월드 → 카메라 공간 z ≥ 0 이면 카메라 뒤 → None.
- world_to_ndc → 픽셀 `((x+1)/2 × W, (1−y)/2 × H)`.
- `_to_window` : 프레임의 화면 위치를 더한 창 좌표 (손잡이용).

### 4-2. 앞뒤 재정렬 — `_reorder` (456)
- 매 프레임 면 판 깊이를 다시 보고, 지은 순서와 **2%(`ORDER_SLACK`) 넘게** 뒤바뀌었으면 판만 다시 짓는다 (`_forget` → `_fill`, 손잡이는 그대로).

### 4-3. 다시 만들지 않고 고치기 — `_restate` (590)
- 판정을 다시 읽어 **기준점(entry[2])** , 글(`_say`, `_dial`, `_judge`), 색(`_repaint`), 판정 판 보이기(손잡이 잡는 중이면 숨김) 만 고친다. 넛지 중 매번 부른다.

### 4-4. 지우기
- `clear` = 손잡이 숨김 + `_forget`. `_forget` = 판 위젯과 사전들만 비움 (프레임·구독 유지).

---

## 5. "어디를 고치나" 표 (5분 — 직접 채워 보기)

| 바꾸고 싶은 것 | 고칠 곳 |
|---|---|
| 판정 판 / 이름표 높이 | `shared` 의 `VERDICT_HEIGHT` / `NAME_HEIGHT` |
| 판정 문구, 사유 문구 | `overlay` 의 `CAN`, `CANNOT`, `OUTER_HIT`, `INNER_HIT`, `BOTH_HIT` |
| 면 판 문구 | `CLASH`, `TIGHT`, `GAP`, `SPAN`, `LEAST` |
| 판 색 | `COLOR_CAN`, `COLOR_CANNOT`, `COLOR_PLACE`, `COLOR_NAME` |
| 글자 크기 | `TEXT_SIZE`, `NAME_SIZE`, `FACE_SIZE` |
| 판 안쪽 여백 | `PAD_X`, `PAD_Y`, `TEXT_DROP` |
| 선과 판 사이 | `PANEL_ROOM` (최소 `LINE_ROOM`) |
| 쌓인 판 사이 | `PANEL_GAP` |
| 좌우 판이 벽에서 떨어지는 한계 | `FACE_MARGIN` |
| 천장 판을 위아래로 | `SIDE_BY_SIDE` |
| 실패 판 시간 | `FAIL_HOLD`, `FAIL_FADE` |
| 판 위치 규칙 자체 | `_place` 의 anchor 분기 |
| 판 구성(줄 수 등) | `_verdict_panel`, `_face_panel` |

> 지난번 "두 줄 → 아래 여백" 버그: 줄 수는 **생성**(`_one`/`_face_panel`)에서 바뀌고, 그 높이가 **배치**의 `_size` → ABOVE/BELOW 식(`판높이 + room + step×stack`)으로 번진다. 증상은 배치에, 원인은 생성에.

---

## 6. 눈에 띈 것 (참고)

- 항목이 **리스트 + 숫자 자리** (`entry[2]`, `entry[7]`, `entry[8]`) 로 다뤄져 읽기 어렵다. 위 표의 자리 이름을 옆에 두고 읽을 것.
- `inside_at` 은 만들지만 쓰는 판이 없다.
- `build`, `reveal` 은 지금 부르는 곳이 없다.

---

## 7. 마무리 메모 (5분)

1. `verdict` 의 기준점 셋과 `placeable` 조건
2. mark 의 주요 키
3. 판 하나가 생성되어 `_marks` 에 들어가는 모양
4. `_place` 의 순서
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

### A. build_verdict
1. `centre`, `name_at` 의 높이 상수와 값.
2. `_pinned` 가 하는 일. 넛지하면 이름표와 판정 판은 각각?
3. `placeable` 조건.
4. `faces` 와 `blocked` 의 차이.
5. mark 의 `state` 세 규칙.
6. mark 의 `distance` 단위와 부호.
7. 면 판의 기준점 `at` 은 어디인가?
8. 거리선의 시작점 (앞뒤 위치 포함).

### B. 뼈대
9. `wake`, `hide`, `show`, `restate` 의 차이.
10. `_build` 가 프레임 안에 짓는 셋.
11. `_tick` 의 다섯 단계.
12. 실패 판이 뜨는 조건과 시간.
13. 작업중 판에 무엇이 나오나?

### C. 생성
14. 면 판을 먼 것부터 짓는 이유.
15. `_marks` 항목의 열 자리 이름.
16. `key` 는 무엇에 쓰나? 예 셋.
17. `_say` 와 `_repaint` 가 바꾸는 것.
18. 판정 판이 숨는 때.
19. `_judge` 의 사유 문구 셋과 고르는 규칙.
20. `_dial` 이 숫자 모양별로 글줄을 따로 두는 이유.
21. 면 판 셋과, 좌·우 면 / 천장에서 붙는 쪽.
22. `_wall` 이 적어 두는 것.

### D. 배치
23. `_place` 의 단계를 순서대로.
24. 벽판과 아닌 판의 투영 차이 (`clip`).
25. `_hug` 의 한계 거리 식.
26. `_size` 가 지난 크기를 쓰는 때.
27. `_room_at` 이 재는 것과 최솟값. 왜 base 로 재나?
28. ABOVE / BELOW 의 y 식.
29. SIM 중에 판을 숨기지 않고 `PANEL_AWAY` 로 치우는 이유.
30. `_to_screen` 이 None 을 주는 경우 둘.
31. `_reorder` 가 다시 짓는 조건.
32. `_restate` 가 고치는 것과 안 고치는 것.

### E. 응용
33. "이름표를 조금 더 위로" — 어디?
34. "거리 판과 선 사이를 넓게" — 어디?
35. "판정 문구를 두 줄로" — 무엇이 영향을 받나?
36. "카메라를 돌리면 면 판이 서로 가린다" — 관련 함수.
37. "SIM 직후 한 프레임 판이 엉뚱한 데 보인다" — 막는 장치 둘.

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. `VERDICT_HEIGHT` 0.8, `NAME_HEIGHT` 0.45 (EBS 높이 비율).
2. 넛지만큼 반대로 되돌려 이름표를 제자리에 둔다. 이름표는 그대로, 판정 판은 EBS 를 따라간다.
3. 내부 간섭 없음 그리고 clear 아닌 면 없음.
4. `faces` 는 clear 가 아닌 면 목록(tight 포함), `blocked` 는 막힌(cells True) 면 수.
5. 막힘 → clash, 거리 < 최소 여유 → tight, 그 외 clear.
6. 미터. 파고들면 음수, 못 찾으면 None.
7. 거리선 두 끝의 가운데.
8. 면 가운데, 앞뒤는 EBS 앞쪽 끝(로컬 front 최소, `LEAD_FRONT=-1`).
9. wake 프레임만, hide 판 지우기, show 다시 그리고 배치, restate 다시 만들지 않고 자리·글·색만.
10. `_stack`(판), 작업중 판, 실패 판.
11. 작업중 판 → 실패 판 → 손잡이 hover → 앞뒤 재정렬 → 배치.
12. 일이 끝났고(바쁘지 않음) 실패 문구가 있을 때. 1초 유지 + 1초 페이드.
13. 작업 이름(busy), 진행 막대, %.
14. 나중에 지은 것이 위에 그려지므로, 가까운 판이 위에 오게.
15. placer, panel, at, anchor, step, share, group, key, on, wide.
16. 나중에 찾아 자리·글·색을 고치려고. `("verdict","centre")`, `("verdict","name_at")`, `("face","left")`.
17. `_say` 글자, `_repaint` 배경색과 글자색.
18. 손잡이를 잡고 있는 동안.
19. `OUTER_HIT`, `INNER_HIT`, `BOTH_HIT`. 외부와 내부 둘 다면 BOTH, 내부만이면 INNER, 아니면 OUTER.
20. 숫자만 바뀌는 글의 폭 계산·재생성으로 인한 깜박임을 막으려고.
21. 상태 / 거리 / 최소 여유. 좌·우 면은 위(상태)·아래(거리, 최소), 천장은 왼쪽(상태)·오른쪽(거리, 최소).
22. 좌·우 면 거리선의 시작점(`from`, 벽 자리).
23. 프레임 크기·SIM 여부 → 그룹 최대 폭 → (판마다) 투영 → 벽 붙이기 → 크기 → 여백 → 쌓기 간격 → anchor 별 좌표 → 밖이면 숨김 → SIM 중/크기 0 이면 PANEL_AWAY → offset 쓰기.
24. 벽판은 clip 없이(화면 밖 점도) 투영해 벽 쪽으로 끌어오고, 나머지는 화면 밖이면 None.
25. 가장 넓은 판의 반폭 + 12px.
26. 막 지어 computed 크기가 0 일 때 (같은 key·anchor·step·group 자리의 지난 크기).
27. 월드 0.024 가 화면에서 몇 px 인가. 최소 6px. 벽에 붙여 옮긴 자리로 재면 위아래 간격이 들쑥날쑥해져서.
28. ABOVE: 기준y − 판높이 − room − step×stack. BELOW: 기준y + room + step×stack.
29. 숨기면 크기 계산이 멈출 수 있어서. 화면 밖에 두면 크기는 계속 계산되고 끝나는 순간 제자리에 다 같이 뜬다.
30. 점이 카메라 뒤, clip 일 때 화면(NDC) 밖. (+ 프레임 크기 0)
31. 지은 순서와 지금 깊이 순서가 2% 넘게 뒤바뀌었을 때.
32. 기준점, 글, 색, 판정 판 보이기. 판 구성(개수·줄 수)은 안 고친다.
33. `shared.NAME_HEIGHT`.
34. `overlay.PANEL_ROOM` (또는 `LINE_ROOM`).
35. 판정 판 높이 → `_place` 의 MIDDLE 정렬 위치, 이름표와의 겹침. 생성(`_one`)이 바뀌고 배치 식이 그 높이를 그대로 쓴다.
36. `_fill` 의 깊이 정렬, `_reorder`, `ORDER_SLACK`.
37. `_size` 의 지난 크기 기억, SIM 중·크기 0 일 때 `PANEL_AWAY`.

</details>
