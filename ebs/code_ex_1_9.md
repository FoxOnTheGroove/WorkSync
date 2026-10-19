# 1회차 · 지도와 SIM 뼈대

> 목표: **"킷이 켜지고 → init 이 돌고 → SIM 한 번이 끝나기까지"** 를 함수 이름으로 그릴 수 있게.
> 시간: 약 1시간 10분. 함수 본문 속으로 들어가지 말 것. **누가 누구를 어떤 순서로 부르나**만 본다.
> 오늘 그린 흐름도가 2~8회차의 지도가 된다. 매 회차는 이 그림의 한 칸을 채우는 일이다.

---

## 0. 큰 그림 (5분)

| 파일 | 줄 | 역할 | 언제 보나 |
|---|---|---|---|
| `extension.py` | 250 | 킷 진입점. 시작/종료, 자동 init, 키보드 | **오늘** (키는 8회차) |
| `ebs_simulate_shared.py` | 157 | 상수 모음 + `children()` | **오늘 훑기** |
| `ebs_simulate.py` | 3,372 | 본체. `EbsSimulate` 클래스 하나 | **오늘 뼈대**, 2~8회차 속 |
| `ebs_simulate_camera.py` | 661 | 카메라 | 4회차 |
| `ebs_simulate_collide.py` | 1,595 | 간섭 엔진 | 5·6회차 |
| `ebs_simulate_overlay.py` | 1,928 | 패널 / 기즈모 / 3D 마커 | 7·8회차 |
| `ebs_simulate_service.py` | 98 | 웹 API 창구 (전부 한 줄 위임) | 9회차 |
| `dummy_ui.py` | 485 | 시험용 창 (숨겨져 있음) | 9회차 |

호출 방향:

```
킷 ─▶ extension ─▶ (dummy_ui) ─▶ service ─▶ EbsSimulate ─┬─▶ camera
웹 ──────────────────────────▶ service ─▶ EbsSimulate   ├─▶ collide
                                                        └─▶ overlay
overlay 는 attach() 로 받아 둔 EbsSimulate 를 sim() 으로 거꾸로 참조한다.
```

> service 와 dummy_ui 는 9회차에 본다. 오늘은 **"자동 init 이 dummy_ui 를 거쳐 돌고, 경로 기본값(PRESET)도 거기 있다"** 는 것만 기억.

---

## 1. `extension.py` — 시작과 종료 (15분)

키 입력(`_watch_keys`, `_on_key`, `_repeat`, `_step`, `_settle*`, `_hold_off`, `_let_go`, `_log_camera`, `_ebs_refresh`)은 **건너뛴다** (8회차).

### 1-1. 상수 (12–20)
- `SHOW_DUMMY_UI = False` : 시험 창은 만들되 숨긴다.
- `BLOCK_SELECT = True` : 뷰포트 클릭 선택을 끈다.
- `RAISE_FRAMES = 300` : 창 되살리기를 최대 몇 프레임 시도하나.

### 1-2. `on_startup` (26) — 시작점
1. `instance()` → `EbsSimulate` 를 **딱 하나** 만든다.
2. `attach_overlay(self._sim)` → 오버레이가 `sim()` 으로 그 하나를 찾게 걸어 둔다.
3. `EbsDummyUI` 생성 → `build_ui()` → `show(False)` (만들지만 숨김).
4. 상태 변수 초기화.
5. 구독 셋: `_watch_layout`, `_watch_stage`, `_watch_keys`.

### 1-3. 자동 init 경로
- **`_watch_stage` (174)** : 매 프레임 `_stage_step` 을 부르는 구독.
- **`_stage_step` (184)** : `_stage_ready()` 가 참인 첫 프레임에
  구독 끊기(`self._stage = None`) → `_lock_select()` → `self._ui.auto_init()`.
- **`_stage_ready` (206)** : 스테이지가 있고 `loaded >= total`.
- **`_lock_select` (192)** : `PickLock.hold(window)` 로 뷰포트 선택 끔.
- 그 뒤 (9회차에 자세히): `EbsDummyUI.auto_init` → PRESET 값을 `set_*` 로 넘김 → `EbsSimulateService.auto_init` → **`EbsSimulate.init`**.

### 1-4. 창 되살리기
- **`_watch_layout` (215) → `_raise_step` (225)** : 레이아웃이 창을 감추면 되살리고 우측 패널에 도킹. 도킹되거나 300 프레임이 지나면 멈춤.

### 1-5. `on_shutdown` (236)
구독 해제 → 키보드 해제 → 반복/대기 작업 취소 → 선택 잠금 해제(`PickLock.drop`) → UI 파괴 → `forget()`.

---

## 2. `ebs_simulate_shared.py` 훑기 (5분)

외우지 말고 **묶음** 만 눈에 넣는다.

| 묶음 | 예 | 다시 볼 회차 |
|---|---|---|
| 작업 이름 `WORK_*` | `WORK_SIM`, `WORK_CLEAR`, `WORK_SETTLE` | 오늘 |
| 단계 표 `PHASES` | camera / place / skin / collide / overlay | 오늘 |
| 실패 `CODE_*`, `SAY_*` | `CODE_PIVOT`, `SAY_PIVOT "유효하지 않은 pivot 데이터"` | 오늘 |
| XML `*_KEY`, `*_PATTERN`, `CACHE_*` | `PORT_ID_KEY`, `CACHE_SUFFIX` | 2회차 |
| 피봇 `ANCHOR_DEPTH`, `PIVOT_*`, `MODULE_*` | 6뎁스, MAINBODY → MAIN → EFEM | 3회차 |
| 면 `FACE_*`, `COLLIDE_STEPS` | left / ceiling / right, 단계별 진행률 비중 | 5회차 |
| 높이 비율 `*_HEIGHT` | `VERDICT_HEIGHT 0.8`, `NAME_HEIGHT 0.45` | 7회차 |
| 프림 루트 `*_ROOT`, `OURS` | `/EbsCollisionMarkers`, `/EbsGrip`, `/EbsSkin` | 5·8회차 |

- 유일한 함수 **`children(prim)`** : 인스턴스 안쪽까지 자식을 본다. 코드 전체의 자식 순회가 이걸 쓴다.

---

## 3. `EbsSimulate` 의 몸통 (15분)

### 3-1. 파일 맨 위 (1–153)
- `_remote`, `_stamp_of`, `_read_bytes`, `_write_text` : 로컬 / `omniverse://` 둘 다 읽고 쓰는 도우미. (2회차)
- `_PortScan` (91) : XML 훑기 수집기. (2회차)

### 3-2. `__init__` (157–233) — 상태 변수 약 75개, 무리로만

| 무리 | 대표 |
|---|---|
| 설정 | `_usd_path`, `_xml_path`, `_ebs_path_2port/3port`, `_search_root`, `_rail_root`, `_skin`, `_min_gap` |
| 색인 / 표 | `_eqp_index`, `_port_map`, `_port_offsets`, `_addr_cad`, `_addr_next`, `_rail_index` |
| 충돌 캐시 | `_triangles`, `_eqp_boxes`, `_bounds`, `_stage_index`, `_leaves`, `_parts`, `_faces` |
| 스킨 | `_skin_made`, `_skin_worn`, `_skin_opened`, `_skin_remote` |
| 진행 / 계측 | `_busy`, `_progress`, `_legs`, `_leg`, `_phases`, `_timings`, `_notes`, `_started` |
| 현재 SIM | `_ready`, `_target`, `_next`, `_tried`, `_aligned`, `_nudge`, `_verdict`, `_result`, `_results` |
| 부품 | `_camera` (EbsSimulateCamera) |

> **핵심 다섯**: `_ready`(init 끝났나) · `_busy`(지금 도는 일) · `_target`(지금 SIM 대상 묶음) · `_aligned`(EBS 를 놓았나) · `_result`(마지막 결과).

### 3-3. 작업 / 로그 기반 (957–1048)

| 함수 | 하는 일 |
|---|---|
| `_begin(step)` | 단계 시작. 진행률·시간·로그 비우고 시작 시각 기록 |
| `_done(payload)` | notes 에 한 줄 남기고 그대로 반환 |
| `_fail(key, reason, short)` | 사유를 `_why` 와 notes 에 두고 **None 반환** |
| `_phase(name)` *(with)* | 블록 시간을 `_phases[name]` 에 누적 → 콘솔 한 줄용 |
| `_stage_timer(label)` *(with)* | 구간 ms 를 `_timings` 목록에 → payload 용 |
| `add_phase` / `phase_line` / `say_phases` | `[ebs] simulate : 3.2s \| Camera : 0.4s \| …` |
| `busy` / `begin_work` / `end_work` | `_busy` 세우기/내리기. 바쁘면 다른 run_* 이 거절 |
| `_note` / `_loud` | notes 에 추가 (지금은 둘이 같다) |
| `_hush(loud)` | loud 가 아니면 그 안의 print 를 삼킨다 |

### 3-4. 진행률 (1774–1803) — 짧게
- `set_legs(n)` : 막대 하나를 n 구간으로 나눔. `next_leg()` : 다음 구간. `get_progress()` : `(leg + 구간 안 진행) / legs × 100`.
- `_at(step, done)` : 충돌 단계 안의 진행. `COLLIDE_STEPS` 비중을 쓴다.

### 3-5. 파일 맨 끝 (3323–3372)
- **`_payload(ok, reason, …)`** : 모든 동작의 반환값. **웹이 받는 응답이 이것.** 부를 때마다 `self._result` 에도 저장한다.
  키: `ok, code, reason, say, equipment, equipment_id, port_count, ebs, anchor, cells, hit_count, distances, rows, equipment_hit, timings, notes, total_ms`
- `instance()` / `forget()` : 싱글턴 생성 / `teardown()` 후 정리.

---

## 4. init 뼈대 (`init`, 1050) (5분)

속은 2회차. 오늘은 순서만.

```
_begin("init") → 상태 비우기 (_ready=False, _target=None …)
→ open_stage()            USD 열기 (동기)
→ camera.make()           SIM 카메라 프림 준비
→ hide_ebs()              EBS 두 개 숨기기
→ build_index()           장비 색인            ┐
→ Collide._stage_boxes()  장비 상자 목록       ├ 2회차
→ load_ports()            포트 표 (캐시 우선)  ┘
→ _ready = 장비>0 and 포트>0
→ camera.go_home()        처음 카메라 자리
→ _copy_skin()            씬 머티리얼 복사 (스킨이 "/" 경로일 때)
→ _payload(...)
```

> init 은 **동기** 다. 도는 동안 킷 메인 스레드가 멈춰 API 응답이 init 끝(skin copy 뒤)에 몰린다.

---

## 5. SIM 뼈대 (25분) — 오늘의 핵심

### 5-1. `run_simulate` (1357) — 바깥 껍데기
```
_busy 면 그냥 return (None)
_tried = ""
패널 hide → wake
begin_work(WORK_SIM)
_veil(True)                     마커·손잡이 뿌리 통째로 숨김
try:
    told = await simulate_async(eqp)
    패널 show
    실패면 패널 fail("사유 : 시도한 id")
    _veil(False)                다 같이 다시 보이기
    await settle()              킷이 잠잠해질 때까지 프레임 돌리기
    say_phases()                콘솔 한 줄
    return told
finally:
    _veil(False); end_work()    어떻게 끝나든 정리
```
- `_panel()` (1344) : 오버레이 클래스를 늦게 import.
- `_paint(work)` (1349) : 오버레이 작업 시간을 "overlay" 단계에 얹는다.
- `_veil(on)` (1383) : `MARKER_ROOT`, `GRIP_ROOT` 의 visibility 를 세션 레이어에 씀.

### 5-2. `simulate_async` (1477) — 실제 순서
```
_begin("simulate"); set_legs(3)
_ready 아니면 → 실패 (SAY_INIT)
① _do_prepare(eqp)      장비·EBS·피봇 고르기          → 3회차
② _do_stage()           마커 걷고 EBS 배치            → 4회차
③ _do_focus()           카메라 맞추기                 → 4회차
④ await _reach_skin()   원격 MDL 연결 확인/재연결     → 8회차
   wear_skin() 했으면 await settle_skin()
   next_leg()                                         (막대 1/3 → 2/3)
⑤ show_ebs()            EBS 보이기
⑥ for _ in _collide_steps(): await _wait_frame()      → 5·6회차
   next_leg()                                         (막대 2/3 → 3/3)
⑦ result = self._result  ← _collide_steps 가 마지막에 _payload 로 채워 둔 것
   timings / notes / total_ms 붙여 반환
```
어느 단계든 `ok` 가 아니면 그 자리에서 `_done(…)` 으로 반환.

### 5-3. 꼭 이해할 세 가지
1. **`_do_prepare` (1510) 는 실패해도 지금 SIM 을 안 깬다.** 새 후보를 `_next` 에만 담고, 성공했을 때만 `_target` 으로 바꾼다. 실패면 `_rail_frame` 도 되돌린다.
2. **`_collide_steps` 는 제너레이터.** 단계마다 `yield` 하고, 그때마다 바깥에서 프레임 하나를 넘긴다 → 킷이 안 멈추고 진행률이 움직인다. 결과는 반환값이 아니라 **`self._result` 로** 나온다.
3. **`settle()` (563)** : 프레임 간격이 `SETTLE_FRAME`(0.02초) 이하로 `SETTLE_CALM`(3) 번 연속 나오면 "잠잠해졌다" 고 보고 끝. 최대 `SETTLE_MOST`(600) 프레임.

### 5-4. 같은 모양의 형제들 (모양만 확인, 1분)
`run_clear` (1402), `run_ebs_refresh` (1420), `run_nudge_settle` (1454), `run_cam_refresh` (1471) 모두
**`_busy` 확인 → `begin_work` → try … finally `end_work`** 틀이다. 속은 8·9회차.

### 5-5. 단계별 시험 함수 (이름만)
`prepare` (1090), `align` (1097), `align_async` (1107), `focus` (1113), `collide` (1126), `collide_async` (1765) — dummy UI 의 1·2·3 버튼용.
`simulate` (1320, 동기판) — **지금 아무도 안 부른다.**

---

## 6. 흐름도 그리기 (5분)

종이에 아래 칸을 직접 채운다. 위를 보지 않고.

```
[킷 시작] → ? → ? → [init] → ? → ? → ? → ? → [_ready]
[SIM]     → run_simulate → simulate_async → ① ? → ② ? → ③ ? → ④ ? → ⑤ ? → ⑥ ? → [결과]
          → 패널 show / fail → ? → ? → end_work
```

---

## 7. 읽으며 눈에 띈 것 (참고, 수정 대상 아님)

- `simulate` (동기판) · `SweepLog`(dummy_ui) 미사용.
- `_note` 와 `_loud` 가 같은 동작.
- 상수 중복: `STATE_*` 는 shared 와 overlay, `EQP_PREFIX` 는 shared 와 dummy_ui.
- `run_*` 은 바쁘면 **아무것도 반환하지 않는다(None)** — 웹은 None 을 "바빠서 무시됨" 으로 받아야 한다.

---

## 8. 마무리 메모 (5분)

본인 말로 5줄:
1. 킷이 켜지면 무엇이 생기나
2. init 은 누가 언제 누르고, 무엇을 순서대로 하나
3. SIM 한 번의 7단계
4. 결과 dict 는 어디서 만들어져 어디로 가나
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

> 답을 먼저 쓰고 아래 정답과 맞춰 볼 것. 80% 이상이면 2회차로.

### A. 구조
1. 파일 8개를 역할과 함께 말하라.
2. `EbsSimulate` 객체는 몇 개 존재하나? 누가, 어떤 함수로 만드나?
3. 오버레이는 `EbsSimulate` 를 어떻게 찾나? 그 연결은 언제 걸리나?
4. 오늘 건너뛴 파일 둘과, 그중 오늘 꼭 알아야 했던 사실 하나.

### B. 시작과 종료
5. `on_startup` 에서 일어나는 일을 순서대로 5단계.
6. `SHOW_DUMMY_UI = False` 인데 dummy UI 객체는 만들어지나? 왜?
7. 자동 init 이 도는 조건은? 몇 번 도나?
8. 자동 init 의 호출 경로를 extension 부터 `EbsSimulate.init` 까지.
9. 자동 init 직전에 함께 실행되는 동작은?
10. `_watch_layout` 은 무엇을 하고 언제 멈추나?
11. `on_shutdown` 에서 정리하는 것을 빠짐없이.
12. `forget()` 은 무엇을 부르나?

### C. shared
13. `children(prim)` 이 `prim.GetChildren()` 과 다른 점은?
14. 실패 사유 한국어 문구의 상수 묶음 이름과 예 2개.
15. `PHASES` 에 들어 있는 단계 다섯.
16. `OURS` 는 무엇의 목록일까?

### D. EbsSimulate 몸통
17. 핵심 상태 변수 다섯과 각 의미.
18. `_fail` 은 무엇을 반환하고, 사유는 어디에 남나?
19. `_phase` 와 `_stage_timer` 의 차이 (저장 위치와 용도).
20. `begin_work` / `end_work` 가 바꾸는 변수는? 그 변수가 차 있으면 어떤 일이 생기나?
21. `get_progress` 가 0~100 을 만드는 방식. SIM 은 몇 구간이고 각 구간은 무엇인가?
22. `_payload` 의 반환 키를 최소 10개.
23. `_payload` 를 부르면 반환 말고 또 무슨 일이 생기나?
24. 웹이 실패 사유를 사람에게 보여 주려면 어떤 키를, 실패 갈래를 기계적으로 나누려면 어떤 키를 쓰나?

### E. init
25. init 의 순서를 빠짐없이 (함수 이름).
26. `_ready` 는 어떤 조건에서 True 가 되나?
27. init 이 동기라서 생기는 실제 증상은?
28. 스킨 복사는 init 의 어디에서, 어떤 조건일 때 하나?

### F. SIM
29. `run_simulate` 가 `simulate_async` 앞뒤로 하는 일을 순서대로.
30. `_veil` 은 무엇을 어떻게 숨기나? 왜 SIM 중에 숨기나?
31. `_veil(False)` 가 두 번(본문, finally) 있는 이유는?
32. `simulate_async` 의 단계 ①~⑦.
33. 단계 중 하나가 실패하면 어떻게 되나?
34. 실패 패널 문구는 어떻게 만들어지나? "시도한 id" 는 어디서 오나?
35. `_do_prepare` 가 실패했을 때 지금 보이던 SIM 은 어떻게 되나? 그 원리(`_next`, `_target`)는?
36. `_collide_steps` 가 제너레이터인 이유와, 결과가 나오는 곳.
37. `settle()` 은 언제 끝나나? (세 상수)
38. 바쁜 중에 `run_simulate` 를 또 부르면 무엇이 반환되나?
39. `run_clear` 가 `run_simulate` 와 다르게 먼저 확인하는 조건 하나.
40. `run_*` 형제들의 공통 틀.
41. 동기판 `simulate` 는 지금 쓰이나?

### G. 응용
42. "SIM 이 진행률 66% 에서 오래 멈춘다" — 어느 단계를 의심하나?
43. "SIM 이 끝났는데 마커와 손잡이가 안 보인다" — 오늘 본 것 중 의심할 곳.
44. "웹 SIM 요청이 None 으로 끝났다" — 이유는? "응답이 한참 늦게 왔다" 와는 원인이 어떻게 다른가?
45. "SIM 실패 후 이전 장비 판정 화면이 그대로 남는다" — 버그인가, 의도인가?
46. 새 단계(예: 판정 전 경고음)를 SIM 에 끼우려면 어느 함수의 어디쯤?

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. extension(진입·자동 init·키), shared(상수), ebs_simulate(본체), camera(카메라), collide(간섭 엔진), overlay(패널·기즈모·마커), service(웹 API 창구), dummy_ui(시험 창, auto init 경유).
2. 하나. `instance()` 가 처음 불릴 때 (`on_startup` 첫 줄).
3. `attach(simulate)` 로 모듈 전역 `_SIM` 에 걸고 `sim()` 으로 꺼낸다. `on_startup` 두 번째 줄.
4. service, dummy_ui. 자동 init 이 dummy_ui 의 `auto_init` 을 거쳐 돌고 경로 기본값(PRESET)이 거기 있다.
5. instance 생성 → overlay attach → DummyUI 생성·build·숨김 → 상태 변수 초기화 → 레이아웃/스테이지/키 구독.
6. 만들어진다. 자동 init 경로와 경로 기본값이 거기 있기 때문.
7. 스테이지가 열리고 `loaded >= total` 인 첫 프레임. 한 번 (돌자마자 구독을 끊는다).
8. `_watch_stage` → `_stage_step` → `EbsDummyUI.auto_init` → `EbsSimulateService.auto_init` → `EbsSimulate.init`.
9. `_lock_select` → `PickLock.hold` (뷰포트 클릭 선택 끔).
10. 레이아웃이 창을 감추면 되살리고 우측 패널에 도킹. 도킹 성공 또는 300 프레임 후 멈춤.
11. 레이아웃·스테이지 구독, 키보드 구독(`_drop_keys`), 반복 작업(`_let_go`), 재측정 대기(`_hold_off`), 선택 잠금(`PickLock.drop`), UI 파괴(오버레이 포함), `_sim=None`, `forget()`.
12. `teardown()` 을 부르고 싱글턴을 None 으로.
13. 인스턴스 프록시 안쪽 자식까지 본다.
14. `SAY_*`. 예: `SAY_PIVOT "유효하지 않은 pivot 데이터"`, `SAY_PORTS "포트 개수 불일치 ({0}개)"`, `SAY_INIT "초기화되지 않음"`.
15. camera, place, skin, collide, overlay.
16. 우리가 만든 프림 루트들 (마커, 손잡이, 레이저, 스윕, 카메라, 스킨). 순회·판정에서 자기 것을 빼려고.
17. `_ready` init 완료 / `_busy` 지금 도는 일 이름 / `_target` 현재 SIM 대상 묶음 / `_aligned` EBS 배치 완료 / `_result` 마지막 payload.
18. None. 짧은 사유는 `_why`, 긴 사유는 `_notes`.
19. `_phase` 는 이름별 초를 `_phases` 에 누적 (콘솔 한 줄 요약용), `_stage_timer` 는 구간 ms 를 `_timings` 목록에 (payload 용).
20. `_busy`. 차 있으면 다른 `run_*` 이 즉시 return (거절).
21. `(_leg + _progress/100) / _legs × 100`. SIM 은 3구간: 준비~스킨 / 충돌 / 마무리.
22. ok, code, reason, say, equipment, equipment_id, port_count, ebs, anchor, cells, hit_count, distances, rows, equipment_hit, timings, notes, total_ms.
23. 같은 내용이 `self._result` 에 저장된다.
24. 사람용은 `say` (없으면 `reason`), 갈래는 `code` (`ok`/`port`/`pivot`/`other`).
25. `_begin` → 상태 비우기 → `open_stage` → `camera.make` → `hide_ebs` → `build_index` → `Collide._stage_boxes` → `load_ports` → `_ready` 결정 → `camera.go_home` → `_copy_skin` → `_payload`.
26. 장비 수 > 0 이고 포트 항목 수 > 0.
27. 도는 동안 메인 스레드가 막혀, 그 사이 들어온 API 응답이 init 끝(skin copy 뒤)에 몰려 나온다.
28. `go_home` 다음, 마지막. 스킨 설정이 "/" 로 시작하는 씬 경로일 때.
29. 바쁨 확인 → `_tried` 비움 → 패널 hide·wake → `begin_work(WORK_SIM)` → `_veil(True)` → (simulate_async) → 패널 show → 실패면 fail 패널 → `_veil(False)` → `settle` → `say_phases` → finally `_veil(False)`·`end_work`.
30. `MARKER_ROOT`, `GRIP_ROOT` 의 visibility 를 세션 레이어에 invisible 로. 중간 상태가 보였다 사라지지 않고 끝나서 한꺼번에 뜨게.
31. 본문은 정상 종료 때 settle 전에 보이게, finally 는 예외로 빠져나가도 숨긴 채 남지 않게.
32. ① `_do_prepare` ② `_do_stage` ③ `_do_focus` ④ `_reach_skin` + `wear_skin`(+`settle_skin`) ⑤ `show_ebs` ⑥ `_collide_steps` 를 프레임마다 ⑦ `self._result` 에 시간·로그 붙여 반환.
33. 그 자리에서 그 단계의 payload 를 `_done` 으로 반환. 뒤 단계는 안 돈다.
34. `f"{say 또는 reason} : {입력 id 또는 _tried}"`. 입력이 비었으면 `_prepare` 가 적어 둔 `_tried`.
35. 그대로 남는다. 새 후보는 `_next` 에만 담기고, 성공했을 때만 `_target` 으로 바뀐다. 실패면 `_rail_frame` 도 원래대로.
36. 단계마다 멈춰 프레임을 넘겨 킷이 안 얼고 진행률이 보이게. 결과는 `self._result` (마지막 `_payload` 호출).
37. 프레임 간격 ≤ `SETTLE_FRAME`(0.02초) 가 `SETTLE_CALM`(3) 번 연속이면. 최대 `SETTLE_MOST`(600) 프레임.
38. None (아무것도 안 하고 return).
39. `_ready` — init 전이면 "Init not finished, clear ignored".
40. `_busy` 확인 → `begin_work` → try 본문 → finally `end_work`.
41. 아니다. 미사용.
42. 66% 는 2구간(충돌) 끝 = 3구간 시작. `_collide_steps` 의 마지막 단계(판정·마커)나 그 직후 패널 show. `settle` 이 돌기 시작하면 다시 올라간다. (구간 경계는 `next_leg` 위치로 판단)
43. `_veil` — `_veil(False)` 가 실행됐는지, 프림이 세션 레이어에서 invisible 로 남았는지.
44. None 은 다른 작업이 도는 중(`_busy`)이라 거절된 것. 늦게 온 것은 동기 init 이 메인 스레드를 막아 요청 처리 자체가 밀린 것.
45. 의도. `_do_prepare` 가 실패하면 지금 SIM 을 유지하도록 만들었다.
46. `simulate_async` 의 ⑥(`_collide_steps`) 앞뒤, 또는 판정 직후라면 `run_simulate` 의 `panel.show` 근처.

</details>
