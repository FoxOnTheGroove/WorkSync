# 1회차 · 지도와 진입점 (+ SIM 뼈대 앞부분)

> 목표: "킷이 켜질 때부터 SIM 버튼 / 웹 API 가 속(`EbsSimulate`)에 닿기까지" 를 막힘없이 말할 수 있게.
> 시간: 1시간. 함수 본문 깊이 들어가지 말 것. **누가 누구를 부르나**만 본다.

---

## 0. 먼저 알고 들어갈 큰 그림 (5분)

파일 8개, 약 8,500줄. 역할은 이렇다.

| 파일 | 줄 | 역할 | 오늘 |
|---|---|---|---|
| `extension.py` | 250 | 킷 진입점. 시작/종료, 자동 init, 키보드 | **정독** |
| `ebs_simulate_service.py` | 98 | 웹이 부르는 API 창구. 전부 한 줄 위임 | **정독** |
| `ebs_simulate_shared.py` | 157 | 상수 모음 + `children()` | 훑기 |
| `dummy_ui.py` | 485 | 시험용 창. **숨겨져 있지만 auto init 을 대신 돌린다** | 훑기 |
| `ebs_simulate.py` | 3,372 | 속(본체). `EbsSimulate` 클래스 하나 | 앞부분만 |
| `ebs_simulate_camera.py` | 661 | 카메라 | 5·6회차 |
| `ebs_simulate_collide.py` | 1,595 | 간섭 엔진 | 6~8회차 |
| `ebs_simulate_overlay.py` | 1,928 | 패널 / 기즈모 / 3D 마커 | 9·10회차 |

호출 방향은 한 방향이다.

```
킷 ──▶ extension.py ──▶ dummy_ui.py ──▶ service ──▶ EbsSimulate (ebs_simulate.py)
웹 ─────────────────────────────────▶ service ──▶ EbsSimulate
                                                   ├─▶ camera
                                                   ├─▶ collide
                                                   └─▶ overlay (attach 로 거꾸로 sim() 을 참조)
```

---

## 1. `extension.py` (20분)

### 1-1. 읽는 순서

1. **상수 (12–20)** — 무엇이 있는지만.
   - `SHOW_DUMMY_UI = False` : 시험 창은 만들되 숨긴다.
   - `BLOCK_SELECT = True` : 뷰포트 클릭 선택을 막는다.
   - `KEY_*` : 방향키 관련 (9회차에서 다시 봄).
2. **`on_startup` (26)** — 여기가 시작점. 순서대로:
   1. `instance()` 로 `EbsSimulate` 단 하나를 만든다 (싱글턴).
   2. `attach_overlay(self._sim)` : 오버레이가 `sim()` 으로 그 하나를 찾을 수 있게 걸어 둔다.
   3. `EbsDummyUI` 생성 → `build_ui()` → `show(False)`. **창은 만들어지지만 안 보인다.**
   4. 상태 변수 초기화.
   5. 구독 3개: `_watch_layout`, `_watch_stage`, `_watch_keys`.
3. **`_watch_stage` (174) → `_stage_step` (184) → `_stage_ready` (206)**
   - 매 프레임 "스테이지가 열렸고 다 로드됐나" 를 본다.
   - 준비되면 구독을 끊고(`self._stage = None`) → `_lock_select()` → `self._ui.auto_init()`.
   - 즉 **자동 init 은 dummy_ui 를 거쳐서 돈다.**
4. **`_lock_select` (192)** — `PickLock.hold` 로 선택을 끈다. (camera 파일에 있음)
5. **`_watch_layout` (215) → `_raise_step` (225)** — 레이아웃이 창을 감추는 걸 막고, 우측 패널에 도킹. 최대 `RAISE_FRAMES` 프레임 후 포기.
6. **`_watch_keys` (47) / `_on_key` (59)** — 오늘은 "어떤 키가 무엇을 하는지" 표만 만든다.
7. **`on_shutdown` (236)** — 구독 해제 → 키 해제 → 선택 잠금 해제 → UI 파괴 → `forget()`.

### 1-2. 키 표 (직접 채워 보기 전에 보지 말 것)

| 키 | 동작 | 비고 |
|---|---|---|
| ← / → | `nudge_by(±KEY_STEP)` 5mm 이동 | SIM 중, 손잡이 안 잡았을 때만 |
| 누르고 있기 | 에디터는 `KEY_REPEAT`, 웹은 `_repeat` 가 대신 반복 | 0.5초 뒤 0.05초마다, 최대 10초 |
| 떼기 | `KEY_WAIT`(0.75초) 뒤 `run_nudge_settle` | 그 사이 또 누르면 취소 (`_hold_off`) |
| R | `cam_refresh` | 임시 |
| T | `ebs_refresh` (EBS 0mm) | 임시 |
| P | 카메라 값 콘솔 출력 | 임시 |

### 1-3. 분석 포인트

- 키 핸들러는 **항상 `True` 를 반환** 한다 (키 이벤트를 먹는다).
- `_repeats` 는 한 번이라도 `KEY_REPEAT` 이 오면 True 가 되고, 이후 자체 반복을 안 띄운다. → 에디터/웹 겸용 처리.
- 비동기 작업 두 개를 들고 있다: `_turbo`(반복), `_waiting`(재측정 대기). 둘 다 cancel 로 정리.

---

## 2. `ebs_simulate_service.py` (10분)

- 모든 메서드가 `instance().xxx()` 한 줄. **로직 없음.**
- 구획 3개: **동작 / 질의 / 설정**.

| 서비스 | 속에서 부르는 것 | async? |
|---|---|---|
| `auto_init` | `init` | X (동기 — 도는 동안 킷이 멈춘다) |
| `simulate` | `run_simulate` | O |
| `clear` | `run_clear` | O |
| `ebs_refresh` | `run_ebs_refresh` | O |
| `cam_refresh` | `run_cam_refresh` | X |
| `valid_ids` | `valid_ids` | X |
| `get_result` | `get_result` | X |
| `busy` / `get_progress` | 같은 이름 | X |
| `set_*` 8개 | 같은 이름 | X |

- 규칙: 속 함수 중 **`run_` 이 붙은 것 = 서비스용 진입점**. 바쁨 표시(`begin_work/end_work`)와 결과 정리를 감싼다 (2회차).

---

## 3. `dummy_ui.py` 훑기 (10분)

### 3-1. 볼 것만

- **`auto_init` (239)** : `_apply_settings()` → `EbsSimulateService.auto_init()` → `_apply_views()` → `_reset_nudge()` → `_render()`.
  - **`_apply_settings` (444)** : 입력칸 값(=`PRESET`)을 `set_*` 로 넘긴다. **실제 경로 설정은 여기 PRESET 에서 온다.**
  - `_apply_views` : `VIEW_PATHS` 줄마다 보임/숨김 적용.
- **`build_ui` (126)** : 화면 구성. 버튼 → 핸들러 연결만 확인.
- 버튼 두 종류의 차이:
  - SIM / Refresh / Clear → **`_spawn` (406)** : 서비스 한 줄을 띄우고 결과를 상태 줄에.
  - 1 Camera / 2 Align / 3 Collide → **`_start` (414)** : 바쁘면 거절, `begin_work` 를 UI 가 직접 세운다. (단계별 시험용)
- **`_watched` (431)** : 작업이 도는 동안 매 프레임 `busy()` + `get_progress()` 를 상태 줄에.
- `_watch_settings` : 입력칸이 바뀌는 즉시 서비스 setter 호출.

### 3-2. 건너뛸 것

- `SweepLog` 클래스 (38–98) — **현재 아무도 안 쓴다** (예전 포트 전수조사 CSV 용 잔재).

---

## 4. `ebs_simulate_shared.py` 훑기 (5분)

상수를 **묶음으로만** 인식한다. 외우지 말 것.

| 묶음 | 예 | 언제 다시 보나 |
|---|---|---|
| 작업 이름 `WORK_*` | `WORK_SIM`, `WORK_SETTLE` | 2회차 |
| XML 키/패턴 | `PORT_ID_KEY`, `ADDR_PATTERN`, `CACHE_SUFFIX` | 3회차 |
| 높이 비율 | `VERDICT_HEIGHT 0.8`, `NAME_HEIGHT 0.45` | 9회차 |
| 프림 루트 `*_ROOT` | `/EbsCollisionMarkers`, `/EbsGrip`, `/EbsSkin` | 10·11회차 |
| `OURS` | 우리가 만든 프림 루트 목록 → 간섭 판정에서 제외용 | 7회차 |
| 면 `FACE_*` | left / ceiling / right | 6회차 |
| `COLLIDE_STEPS` | 충돌 단계와 진행률 비중 | 6회차 |
| 피봇 `ANCHOR_DEPTH`, `PIVOT_*`, `MODULE_*` | 6뎁스, MAINBODY → MAIN → EFEM | 4회차 |
| 실패 `CODE_*`, `SAY_*` | 실패 코드와 한국어 사유 | 2회차 |

- 유일한 함수 `children(prim)` : **인스턴스 안쪽까지** 자식을 본다. 코드 전체에서 자식 순회는 이걸 쓴다.

---

## 5. `ebs_simulate.py` 앞부분 (10분)

### 5-1. 파일 맨 위 (1–153)

- 모듈 함수: `_remote`, `_stamp_of`, `_read_bytes`, `_write_text` → **로컬/원격(omniverse://) 파일 둘 다 다루는 입출력 도우미.** (3회차)
- `_PortScan` (91) : XML 파서 콜백. 3회차에 본다. 지금은 "XML 한 번 훑는 수집기" 로만.

### 5-2. `EbsSimulate.__init__` (157–233)

상태 변수 약 75개. **이름 앞부분으로 무리 짓기** 만 한다.

| 무리 | 변수 예 |
|---|---|
| 설정 | `_xml_path`, `_usd_path`, `_ebs_path_2port/3port`, `_search_root`, `_rail_root`, `_skin`, `_min_gap` |
| 색인/표 | `_eqp_index`, `_port_map`, `_port_offsets`, `_addr_cad`, `_addr_next`, `_rail_index` |
| 충돌 캐시 | `_triangles`, `_eqp_boxes`, `_bounds`, `_stage_index`, `_leaves`, `_parts`, `_faces` |
| 스킨 | `_skin_made`, `_skin_worn`, `_skin_opened`, `_skin_remote` … |
| 진행/계측 | `_busy`, `_progress`, `_legs`, `_leg`, `_phases`, `_timings`, `_notes`, `_started` |
| 현재 SIM | `_ready`, `_target`, `_next`, `_tried`, `_aligned`, `_nudge`, `_verdict`, `_result`, `_results` |
| 부품 | `_camera` (EbsSimulateCamera) |

> 핵심 세 개: **`_ready`**(init 끝났나), **`_target`**(지금 SIM 대상 묶음), **`_busy`**(지금 도는 일 이름).

### 5-3. 작업/로그 기반 (957–1048) — SIM 뼈대의 공용 부품

| 함수 | 하는 일 |
|---|---|
| `_begin(step)` | 한 단계 시작. 진행률·시간·로그 비움, 시작 시각 기록 |
| `_done(payload)` | 단계 결과를 notes 에 한 줄 남기고 그대로 반환 |
| `_fail(key, reason, short)` | 실패 사유를 `_why` 에 두고 **None 반환** |
| `_phase(name)` (with 문) | 그 블록 시간을 `_phases[name]` 에 누적 |
| `add_phase` / `phase_line` / `say_phases` | 단계별 시간 한 줄 (`[ebs] Simulate : 3.2s \| Camera : …`) |
| `busy` / `begin_work` / `end_work` | 바쁨 표시 세우기/내리기 |
| `_note` / `_loud` | notes 에 추가 (지금은 둘이 **똑같다**) |
| `_hush(loud)` | loud 가 아니면 print 를 삼킨다 |
| `_stage_timer(label)` (with 문) | 구간 ms 를 `_timings` 에 |

### 5-4. 파일 맨 끝 (3323–3372)

- **`_payload`** : 모든 동작의 반환값 모양. 키: `ok, code, reason, say, equipment, equipment_id, port_count, ebs, anchor, cells, hit_count, distances, rows, equipment_hit, timings, notes, total_ms`.
  → **웹이 받는 응답이 이 dict.** 키 이름을 한 번 눈에 넣어 둘 것.
- **`instance()` / `forget()`** : 싱글턴 생성/정리. `forget` 은 `teardown()` 을 부른다.

---

## 6. 읽으면서 눈에 띈 것 (참고, 수정 대상 아님)

- 상수 중복: `STATE_CLEAR/TIGHT/CLASH` 가 shared 와 overlay 에, `EQP_PREFIX` 가 shared 와 dummy_ui 에 따로 있다. 바꿀 때 두 곳.
- `set_usd_path` 에 `self._ready = False` 가 두 번.
- `_note` 와 `_loud` 가 같은 동작.
- `SweepLog` 미사용.
- 경로 기본값(PRESET)이 **dummy_ui 안에** 있다. 창이 숨겨져 있어도 auto init 이 여기 값을 쓴다.
- `auto_init` 은 동기라서 도는 동안 API 응답이 밀린다 (지난번 "skin copy 뒤에 응답" 현상의 원인).

---

## 7. 마무리 (5분) — 메모 5줄

본인 말로 적기:
1. 킷이 켜지면 무엇이 생기나
2. 자동 init 은 누가 언제 누르나
3. 웹 API 는 어디로 들어와 어디에 닿나
4. 결과 dict 는 어디서 만들어지나
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

> 답을 먼저 쓰고 아래 정답과 맞춰 볼 것. 80% 이상이면 2회차로.

### A. 구조

1. 이 익스텐션의 파일 8개를 역할과 함께 말하라.
2. `EbsSimulate` 객체는 몇 개 존재하나? 누가, 어떤 함수로 만드나?
3. 오버레이는 `EbsSimulate` 를 어떻게 찾나? 그 연결은 언제 걸리나?
4. 웹 요청과 dummy UI 버튼이 공통으로 거치는 층은?
5. 서비스 메서드 중 로직이 들어 있는 것은?

### B. 시작과 종료

6. `on_startup` 에서 일어나는 일을 순서대로 5단계 이상.
7. `SHOW_DUMMY_UI = False` 인데 dummy UI 객체는 만들어지나? 왜 필요한가?
8. 자동 init 이 도는 조건은? 몇 번 도나?
9. 자동 init 의 호출 경로를 함수 이름으로 끝까지 (extension → … → EbsSimulate).
10. 자동 init 이 쓰는 USD / XML 경로 값은 어디에 적혀 있나?
11. 자동 init 직전에 같이 실행되는 "선택" 관련 동작은?
12. `_watch_layout` 은 무엇을 하고, 언제 멈추나?
13. `on_shutdown` 에서 정리하는 것들을 빠짐없이.
14. `forget()` 은 무엇을 부르나?

### C. 키보드

15. ← → 한 번 누르면 EBS 가 얼마나, 어떤 함수로 움직이나?
16. 방향키가 안 먹히는 조건 2가지.
17. 키를 떼면 무엇이 언제 일어나나? 그 사이 다시 누르면?
18. 웹 스트리밍에서 꾹 누름이 되는 방법은? 에디터에서는 왜 그게 중복 실행되지 않나?
19. 꾹 누름 자동 반복의 시작 지연, 간격, 최대 시간은?
20. 임시 키 3개와 각각의 동작.
21. `_on_key` 가 항상 True 를 반환하는 의미는?

### D. 서비스

22. 서비스의 3개 구획 이름과 각 구획 메서드.
23. 서비스 중 async 인 것을 모두.
24. `auto_init` 이 동기라서 생기는 실제 증상은?
25. 속 함수 이름에 `run_` 이 붙은 것의 의미는?
26. `valid_ids` 는 무엇을 기준으로 유효를 판정하나? (지난 대화 복습)

### E. dummy UI

27. `SIM` 버튼과 `2 Align` 버튼은 실행 방식이 어떻게 다른가? (`_spawn` vs `_start`)
28. 작업 중 상태 줄에 진행률이 나오는 원리는?
29. 입력칸 값을 바꾸면 언제 서비스에 반영되나?
30. Refresh 버튼은 서비스 무엇을 몇 번 부르나?
31. `SweepLog` 는 지금 쓰이나?

### F. shared

32. `OURS` 는 무엇의 목록이고 왜 필요할까?
33. `children(prim)` 이 `prim.GetChildren()` 과 다른 점은?
34. 실패 사유 한국어 문구는 어느 상수 묶음인가? 예 2개.
35. 이름표 높이, 판정 패널 높이 상수와 값.
36. 피봇 선정 모듈 우선순위 상수와 그 순서.

### G. EbsSimulate 앞뒤

37. `__init__` 의 상태 변수 중 "init 끝났나", "현재 대상", "지금 도는 일" 은 각각 무엇인가?
38. `_fail` 은 무엇을 반환하나? 사유는 어디에 남나?
39. `_phase` 와 `_stage_timer` 의 차이는?
40. `begin_work` / `end_work` 가 바꾸는 변수와, 그것을 밖에서 읽는 서비스 메서드.
41. `_payload` 반환 dict 의 키를 아는 만큼 (최소 8개).
42. 웹이 SIM 실패 사유를 사람에게 보여 주려면 어떤 키를 쓰면 되나?
43. `_remote` / `_read_bytes` 같은 모듈 함수가 따로 있는 이유는?

### H. 응용

44. "이름표 높이를 바꾸고 싶다" — 오늘 본 범위에서 어디부터 찾나?
45. "웹에서 API 가 응답이 안 온다" — 오늘 본 것 중 의심할 곳 2개.
46. 새 API `foo()` 를 웹에 열려면 최소 어디 두 곳을 손대나?
47. 키 하나(예: H)로 카메라 홈 복귀를 넣으려면 어느 함수에 몇 줄?

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. extension(진입/키), service(API 창구), shared(상수), dummy_ui(시험 창·auto init 경유), ebs_simulate(본체), camera, collide(간섭 엔진), overlay(패널·기즈모·마커).
2. 하나. `ebs_simulate.instance()` 가 처음 불릴 때 만든다 (`on_startup` 첫 줄).
3. `attach(simulate)` 로 모듈 전역 `_SIM` 에 걸고 `sim()` 으로 꺼낸다. `on_startup` 두 번째 줄.
4. `EbsSimulateService`.
5. 없다. 전부 `instance().xxx()` 한 줄.
6. instance 생성 → overlay attach → DummyUI 생성·build·숨김 → 상태 변수 초기화 → 레이아웃/스테이지/키 구독.
7. 만들어진다. 자동 init 이 dummy UI 의 `auto_init` 을 거치고, 경로 PRESET 도 거기 있기 때문.
8. 스테이지가 열리고 `loaded >= total` 인 첫 프레임. 한 번 (돌자마자 구독을 끊음).
9. `_watch_stage` → `_stage_step` → `EbsDummyUI.auto_init` → `_apply_settings` → `EbsSimulateService.auto_init` → `EbsSimulate.init`.
10. `dummy_ui.py` 의 `PRESET` (입력칸 기본값).
11. `_lock_select` → `PickLock.hold` 로 뷰포트 클릭 선택 끔.
12. 레이아웃이 창을 감추면 되살리고 우측에 도킹. 도킹 성공 또는 `RAISE_FRAMES`(300) 프레임 후 멈춤.
13. 레이아웃·스테이지 구독, 키보드 구독, 반복 작업(`_let_go`), 재측정 대기(`_hold_off`), 선택 잠금(`PickLock.drop`), UI 파괴(오버레이 포함), `_sim=None`, `forget()`.
14. `teardown()` 후 싱글턴을 None 으로.
15. 5mm (`KEY_STEP 0.005`), `EbsSimulate.nudge_by`.
16. SIM 상태가 아님(`nudge_by` 가 ok 아님), 기즈모 손잡이를 잡고 있음(`EbsSimulateGrip.held()`).
17. 0.75초 뒤 `run_nudge_settle`(재판정). 그 전에 다시 누르면 `_hold_off` 로 취소되고 다시 대기.
18. PRESS 때 `_repeat` 코루틴을 띄워 직접 반복. 에디터는 KEY_REPEAT 이 오면 `_repeats=True` 가 되어 이후 자체 반복을 안 띄우고, REPEAT 이 오면 도는 반복도 `_let_go` 로 끈다.
19. 0.5초 / 0.05초 / 10초.
20. R = cam_refresh, T = ebs_refresh(0mm), P = 카메라 값 콘솔 출력.
21. 그 키 이벤트를 소비했다는 뜻 (`True` 반환).
22. 동작: auto_init, simulate, clear, ebs_refresh, cam_refresh, valid_ids, get_result / 질의: busy, get_progress / 설정: set_usd_path, set_xml_path, set_ebs_paths, set_search_root, set_rail_root, set_skin, set_near_span, set_min_gaps.
23. simulate, clear, ebs_refresh 3개. 나머지는 전부 동기.
24. init 동안 킷 메인 스레드가 막혀 그 사이 들어온 API 응답이 init 끝(skin copy 뒤)에 몰려 나온다.
25. 서비스용 진입점. 바쁨 표시·결과 정리·예외 처리를 감싼다.
26. XML(캐시)의 포트 표에 있고 포트 수가 2 또는 3.
27. `_spawn` 은 서비스를 그냥 띄우고 결과만 적음. `_start` 는 바쁘면 거절하고, UI 가 `begin_work` 를 직접 세우고 끝나면 `end_work`.
28. `_watched` 가 작업이 끝날 때까지 매 프레임 `busy()` 와 `get_progress()` 를 읽어 적는다.
29. 바뀌는 즉시 (`_watch_settings` 의 value_changed 콜백). auto init 때는 `_apply_settings` 로 한꺼번에.
30. `ebs_refresh` 1번, `cam_refresh` 1번.
31. 안 쓰인다.
32. 우리가 만든 프림 루트들(마커, 손잡이, 레이저, 스윕, 카메라, 스킨). 간섭 판정·순회에서 자기 것을 빼기 위해.
33. 인스턴스 프록시 안쪽 자식까지 본다.
34. `SAY_*`. 예: `SAY_PIVOT "유효하지 않은 pivot 데이터"`, `SAY_PORTS "포트 개수 불일치 ({0}개)"`.
35. `NAME_HEIGHT 0.45`, `VERDICT_HEIGHT 0.8` (EBS 높이 비율).
36. `MODULE_TIERS = (MAINBODY, MAIN, EFEM)`, MAINT 는 `MODULE_SKIP` 으로 제외.
37. `_ready`, `_target`, `_busy`.
38. None. 사유는 `_why`(짧은 것) 와 `_notes`(긴 것).
39. `_phase` 는 이름별 초 누적(`_phases`, 한 줄 요약용), `_stage_timer` 는 구간 ms 를 목록(`_timings`, payload 용)에.
40. `_busy`. 서비스 `busy()`.
41. ok, code, reason, say, equipment, equipment_id, port_count, ebs, anchor, cells, hit_count, distances, rows, equipment_hit, timings, notes, total_ms.
42. `say` (사람용 한국어 사유). `code` 로 갈래 구분.
43. 로컬 경로와 omniverse:// 원격 경로를 같은 방식으로 읽고 쓰려고.
44. shared 의 `NAME_HEIGHT`. (실제 적용 위치는 9회차 `build_verdict`)
45. init 이 아직 도는 중(동기라 막힘), `busy()` 상태로 run_* 이 거절 중.
46. `EbsSimulate` 에 함수 추가, `EbsSimulateService` 에 한 줄 위임 추가.
47. `extension.py` 의 `_on_key` 에 R/T/P 와 같은 모양으로 3줄 (조건 + 호출 + return True).

</details>
