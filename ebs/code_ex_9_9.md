# 9회차 · 바깥 층 (서비스 API · dummy UI) 과 실습

> 목표: 1~8회차에서 본 속을 **바깥에서 부르는 창구** 로 다시 정리하고, AI 없이 **작은 수정 세 개** 를 직접 해 본다.
> 시간: 약 1시간. 앞 30분 읽기, 뒤 30분 실습.

---

## 0. 오늘의 지도

```
웹 (HTTP 핸들러 — 이 저장소 밖)
 └─ EbsSimulateService.xxx()      ebs_simulate_service.py   ← 전부 한 줄 위임
       └─ instance().xxx()         EbsSimulate

킷 안
 └─ extension.py
      ├─ 자동 init → EbsDummyUI.auto_init → service.auto_init
      └─ 키 입력  → sim.nudge_by / service.cam_refresh / ebs_refresh
 └─ dummy_ui.py (숨김)              시험용 창. 버튼 → service 또는 sim 직접
```

---

## 1. 서비스 API 전체 (15분) — `ebs_simulate_service.py`

이제 각 API 가 **속에서 무엇을 하는지** 를 회차와 함께 말할 수 있어야 한다.

### 1-1. 동작
| API | 속 | async | 반환 | 회차 |
|---|---|---|---|---|
| `auto_init()` | `init` | X (동기, 킷 멈춤) | payload | 1·2 |
| `simulate(eqp)` | `run_simulate` | O | payload / **바쁘면 None** | 1·3~7 |
| `clear()` | `run_clear` | O | payload / None / init 전 거절 | 8 |
| `ebs_refresh()` | `run_ebs_refresh` | O | payload / None | 8 |
| `cam_refresh()` | `run_cam_refresh` → `refresh_camera` → `camera.reset` | X | payload / None | 4 |
| `valid_ids(ids)` | `valid_ids` | X | 유효 id 목록 | 2 |
| `get_result(eqp)` | `get_result` | X | 판정 dict | 5 |

### 1-2. 질의
| API | 반환 |
|---|---|
| `busy()` | 지금 도는 일 이름 (`"Simulate"`, `"Clear"`, `"Collision Check"` …) 또는 `""` |
| `get_progress()` | 0~100 (구간 나눔, 1·5회차) |

### 1-3. 설정 (바뀌면 무엇을 버리나 — 2회차)
`set_usd_path`, `set_xml_path`, `set_ebs_paths`, `set_search_root`, `set_rail_root`, `set_skin`, `set_near_span`, `set_min_gaps`

### 1-4. 응답 두 가지를 구분할 것
| | **payload** (`simulate` 등) | **판정** (`get_result`) |
|---|---|---|
| 뜻 | 동작이 됐나 | 그 자리에 세울 수 있나 |
| 핵심 키 | `ok`, `code`, `say`, `reason` | `placeable`, `faces`, `inside`, `offset` |
| 만드는 곳 | `_payload` (1회차) | `get_result` (5회차) |
| 실패 예 | 장비 없음, 포트 수 불일치, 피봇 이상 | 좌면 tight, 내부 clash |

> 웹 처리 순서 예: `simulate` 응답 `ok` 확인 → 실패면 `say` 를 표시 → 성공이면 `get_result` 로 `placeable` 확인.
> `None` 은 "바빠서 무시됨" 이다. 실패가 아니다.

### 1-5. 판정 기록 설계 문서
- `RESULT_STORE.md` : 장비별 판정을 기록으로 남기는 설계 (`get_result`, `list_results` 는 구현됨, `restore` 는 아직).

---

## 2. dummy UI (10분) — `dummy_ui.py`

### 2-1. 구성
- `PRESET` : 경로 기본값 (자동 init 이 쓴다). `VIEW_PATHS` : 보임/숨김 줄.
- `build_ui` (126) : Paths(접힘) · 레이저 체크 · Material · Equipment(From Sel) · Min gap · **SIM / Refresh / Clear** · near 슬라이더 · **1 Camera / 2 Align / 3 Collide** · 넛지 줄 · 상태 줄.
- `_watch_settings` : 입력칸이 바뀌는 즉시 setter 호출.

### 2-2. 버튼의 두 방식
| | `_spawn` (406) | `_start` (414) |
|---|---|---|
| 버튼 | SIM, Refresh, Clear | 1 Camera, 2 Align, 3 Collide |
| 부르는 것 | 서비스 한 줄 | sim 의 단계 함수 직접 (`focus`, `align_async`, `collide_async`) |
| 바쁨 표시 | 속(`run_*`)이 세움 | **UI 가 `begin_work` 를 직접** 세우고 끝나면 `end_work` |
| 오버레이 | 속이 처리 | UI 가 `hide`/`show` 를 직접 |

- `_watched` (431) : 일이 끝날 때까지 매 프레임 상태 줄에 `busy` + 진행률 + 경과 시간.
- `_on_refresh` → `_refreshed` : `ebs_refresh` 다음 `cam_refresh`, 넛지 줄 갱신.
- `_on_pick_selected` : 뷰포트 선택에서 장비 이름 (`get_selected_equipment`).
- `auto_init` (239) : PRESET 을 setter 로 → `service.auto_init` → 보임/숨김 적용 → 넛지 0 → 상태 줄.

### 2-3. 남은 것
- `SweepLog` : 미사용 (포트 전수조사 CSV 용).

---

## 3. 전체 흐름 다시 그리기 (5분)

보지 않고 종이에:

```
[킷 시작] → on_startup → … → [init] → open_stage → … → [_ready]
[SIM] → run_simulate → simulate_async
        → ① _do_prepare (장비·포트·EBS·피봇)
        → ② _do_stage  (pivot_target → _place_ebs)
        → ③ _do_focus  (camera.place)
        → ④ _reach_skin → wear_skin
        → ⑤ show_ebs
        → ⑥ _collide_steps (warm → sides → faces → clearance → equipment → verdict → markers)
        → panel.show (_fill → _place) → settle
[밀기] → slide (산수) → restate → (뗌) → _retest_inner
[Clear] → strip_skin → clear_markers → release_camera → hide_ebs
```

---

## 4. 실습 (30분) — AI 없이

각 과제마다: **① 고칠 곳을 먼저 예측해 적고 → ② 찾아서 맞춰 보고 → ③ 고치고 → ④ Kit 에서 확인 → ⑤ 되돌린다.**
(이 저장소의 규칙: `#` 주석 금지, 함수마다 한 줄 한국어 설명)

### 과제 1 (쉬움) — 상수
"이름표를 EBS 높이의 55% 로 올리고, 판정 판 글자를 조금 키운다."
- 예측: 파일 둘, 상수 둘.

### 과제 2 (보통) — 문구와 조건
"설치 불가 사유에서 `(외부 간섭)` 대신, 막힌 면 이름을 붙여 `(좌측 간섭)` / `(우측, 천장 간섭)` 처럼 보이게."
- 예측: 판정 판 글을 정하는 함수 하나. 그 함수가 받는 `said` 에서 막힌 면 목록은 어느 키?
- 힌트: 넛지 중에도 맞게 바뀌어야 한다 → 그 함수가 `_restate` 에서도 불리는지 확인.

### 과제 3 (보통) — 새 API
"웹에서 `get_nudge()` 로 지금 민 거리(m)를 받을 수 있게."
- 예측: 서비스에 몇 줄? 속 함수는 이미 있나?

### 과제 4 (도전, 선택) — 키 하나
"H 키를 누르면 카메라를 SIM 첫 자리로 되돌린다."
- 예측: 어느 파일, 어느 함수, 기존 어떤 키와 같은 모양?

---

## 5. 마무리 — 9회를 마치며

1. 1회차 흐름도를 다시 펼쳐, 빈칸이 남았는지 확인
2. 가장 헷갈렸던 회차 하나를 골라 그 회차 자가 테스트를 다시
3. "증상 → 볼 파일" 표를 본인 말로 한 장 (아래 G 참고)

---

## 자가 테스트

### A. 서비스
1. 동작 API 일곱과 각각의 속 함수.
2. async 인 API 셋.
3. `None` 응답의 뜻과, 그것을 주는 API 들.
4. `clear()` 가 init 전에 하는 일.
5. `cam_refresh()` 가 되돌리는 자리는? Clear 의 카메라 처리와 무엇이 다른가?
6. payload 와 판정의 차이 (뜻, 핵심 키, 만드는 곳).
7. 웹이 "세울 수 있나" 를 알려면 무엇을 보나?
8. 경로 setter 를 바꾼 뒤 해야 하는 일.

### B. dummy UI
9. 자동 init 이 PRESET 을 쓰는 경로.
10. `_spawn` 과 `_start` 의 차이 넷.
11. 단계 버튼 셋이 부르는 sim 함수.
12. 상태 줄에 진행률이 나오는 원리.
13. Refresh 버튼이 부르는 둘과 순서.

### C. 전체
14. SIM 의 ①~⑥ 과 각 단계의 대표 함수.
15. 충돌 일곱 단계.
16. 밀기에서 다시 재는 것과 산수로 고치는 것.
17. Clear 의 차례.
18. 세션 레이어에 쓰는 것들 (아는 만큼).

### D. 실습 정답 확인
19. 과제 1 의 두 상수와 파일.
20. 과제 2 의 함수, 쓰는 키, 다른 호출 지점.
21. 과제 3 의 추가 코드.
22. 과제 4 의 추가 위치와 모양.

### E. 증상 → 볼 곳 (종합)
23. "장비를 찾을 수 없음"
24. "포트 개수 불일치"
25. "유효하지 않은 pivot 데이터"
26. EBS 가 엉뚱한 방향/위치
27. 카메라가 장비 뒤에서 본다
28. 좌면이 늘 clear
29. 내부 간섭이 안 잡힌다
30. 패널이 선에서 너무 멀다
31. 밀었더니 `~`
32. 대상 장비가 빨갛다
33. API 응답이 init 뒤에 몰려 온다

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. auto_init→init, simulate→run_simulate, clear→run_clear, ebs_refresh→run_ebs_refresh, cam_refresh→run_cam_refresh, valid_ids→valid_ids, get_result→get_result.
2. simulate, clear, ebs_refresh.
3. 다른 일이 도는 중이라 무시됨. simulate, clear, ebs_refresh, cam_refresh.
4. "Init not finished, clear ignored" payload (ok False).
5. 이번 SIM 의 `place` 가 처음 잡은 자리(`_home`). Clear 는 우리 카메라를 놓고 원래 Persp 카메라를 HOME 장면으로(`go_home`).
6. payload = 동작 성공 여부(`ok`, `code`, `say`, `reason`, `_payload`), 판정 = 설치 가능 여부(`placeable`, `faces`, `inside`, `offset`, `get_result`).
7. `get_result(eqp)["placeable"]` (또는 SIM 직후 `reason`).
8. init 을 다시 (`_ready` 가 내려감).
9. `_stage_step` → `EbsDummyUI.auto_init` → `_apply_settings`(PRESET 입력칸 값을 setter 로) → `service.auto_init`.
10. 부르는 대상(서비스 / sim 단계 함수), 바쁨 표시(속 / UI 직접), 바쁠 때(속이 None / UI 가 "Busy" 표시), 오버레이 처리(속 / UI 직접).
11. `focus`, `align_async`, `collide_async`.
12. `_watched` 가 매 프레임 `busy()` 와 `get_progress()` 를 읽어 적는다.
13. `ebs_refresh` 다음 `cam_refresh`.
14. ① `_do_prepare` ② `_do_stage`(`pivot_target`, `_place_ebs`) ③ `_do_focus`(`camera.place`) ④ `_reach_skin`·`wear_skin` ⑤ `show_ebs` ⑥ `_collide_steps`.
15. warm, sides, faces, clearance, equipment, verdict, markers.
16. 다시 잼: 내부 간섭(손 뗀 뒤). 산수: 세 면 거리·상태, 선·판 위치, placeable.
17. strip_skin → clear_markers → clear_port_lasers → clear_sweep → release_camera(+go_home) → hide_ebs.
18. EBS 변환, EBS·마커·손잡이 가시성, `/EbsCamera`, Persp HOME, 마커·손잡이 프림과 머티리얼, `/EbsSkin`, 스킨 바인딩, 인스턴스 열기.
19. `shared.NAME_HEIGHT = 0.55`, `overlay.TEXT_SIZE` (판정 판은 `_one` 기본 크기 TEXT_SIZE — 이름표 `NAME_SIZE` 도 TEXT_SIZE 에 비례해 같이 커진다는 점 주의).
20. `_judge(said)`. `said["faces"]` 의 각 `face`. `_restate` 에서도 불리므로 넛지 중에도 바뀐다.
21. `EbsSimulateService` 에 `@classmethod def get_nudge(cls): """지금 민 거리(m)""" return instance().get_nudge()`.
22. `extension._on_key` 에 R/T/P 와 같은 모양: `if event.type == kind.KEY_PRESS and event.input == keys.H: EbsSimulateService.cam_refresh(); return True`. (지금 R 키가 같은 일을 한다)
23. `_resolve_by_name` (2회차) — search_root, 이름 표기.
24. XML 포트 표 (`_port_map`, 2회차) — 캐시, XML.
25. `_pick_anchor` notes (3회차) — MODULE 단계, 포트 1 거리.
26. 피봇 선택(3회차), `_frame` 로컬 Y 축·`pivot_target`(4회차).
27. 피봇의 로컬 Y 축 방향 (`_frame`, 4회차).
28. `side_band` (5회차) — notes 의 "sides judged against".
29. `check_equipment` (6회차) — `_missed` 경고, 가시성, 대상 장비 범위.
30. `PANEL_ROOM` / `LINE_ROOM`, `_room_at` (7회차).
31. `_slide_marks` 의 `stale` (8회차).
32. `_reach_skin` 로그, `_copy_skin` 의 원격 에셋 (8회차).
33. 동기 `init` 의 마지막 `_copy_skin` (1회차).

</details>
