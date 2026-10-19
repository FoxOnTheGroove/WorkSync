# 8회차 · 상호작용과 부가 기능 (기즈모 · 넛지 · 재판정 · 3D 마커 · 스킨)

> 목표: SIM 이 끝난 뒤 **사용자가 EBS 를 좌우로 밀 때** 무엇이 다시 계산되고 무엇이 산수로만 고쳐지는지, 그리고 씬에 그려지는 **3D 마커**와 대상 장비 **머티리얼(스킨)** 이 어떻게 붙고 떨어지는지 설명할 수 있게.
> 시간: 약 1시간 20분. 3D 도형 생성(`_tube_part`, `_cone_part`, `_ball_part`, `_solid`, `_quad`, `_sheet`)은 이름만.

---

## 0. 오늘의 지도

```
밀기 입력 두 가지
 ├─ 기즈모 드래그   EbsSimulateGrip.press → drag → release      overlay.py 911
 └─ 방향키          extension._on_key → _step → nudge_by        extension.py 59
        │
        ▼
   hold_clash(False)        빨간 상자 감추기 (밀 동안은 내부 판정 안 함)
   slide(metres)            EBS 옮기기 + 판정을 "산수로" 고치기     ebs_simulate.py 362
   Overlay.restate()        판 자리·글·색만 고치기
        │  손 뗌 / 키 뗌 후 0.75초
        ▼
   hold_clash(True) → _retest_inner()   내부 간섭만 진짜로 다시 재기  336
   settle → restate

3D 마커   show_markers → EbsSimulateMarks.draw (면 판·거리선·안내선·충돌 상자)
스킨      _reach_skin → wear_skin → (Clear 때) strip_skin
```

> 핵심: **밀 때 외부(세 면) 판정은 다시 재지 않는다.** 거리선을 민 만큼 늘이고 줄이는 산수로 고친다. **내부 간섭만** 손을 뗀 뒤 다시 잰다.

---

## 1. 넛지 값 (5분)

| 함수 | 줄 | 하는 일 |
|---|---|---|
| `set_nudge(m)` | 478 | `±NUDGE_LIMIT`(1m) 안으로 잘라 `_nudge` 에 |
| `get_nudge()` | 483 | 지금 민 거리 (오른쪽 +) |
| `_per_unit()` | 471 | 스테이지 1 단위가 몇 m |
| `_pushed(anchor, target)` | 487 | 목표점을 `x_cam` 방향으로 `_nudge / per_unit` 만큼 |

- 기준점은 4회차의 **`_base`** (넛지 0 자리). 넛지는 늘 `_base` 에서 다시 계산 → 오차가 쌓이지 않는다.

---

## 2. `slide(metres)` — 밀기의 본체 (20분) (362)

1. `_target` 없거나 `_aligned` 아니면 실패.
2. `shift = set_nudge(metres) − 이전 값`. 0 이면 끝.
3. 옮기기 전 EBS 바운드 `box` (판정 고칠 때 기준).
4. **`_place_nudged`** (395) : `_base` 에 `_pushed` 를 적용해 `_place_ebs` (포트 계산은 다시 안 함). `_base` 가 없으면 `_align_prims`.
5. **`_slid_box`** (382) : EBS 바운드를 **다시 재지 않고** 행렬에 이동만 곱해 `_ebs_box` 에.
6. 판정이 있으면:
   - **`_slide_marks(shift, box)`** (409) — 산수로 고치기:
     - 면마다 `along` = (오른쪽 · 면 바깥방향) × shift → 오른쪽 면은 밀면 여유가 줄고, 왼쪽 면은 는다. 천장은 0.
     - `distance −= along`, 선 두 끝(`from`, `to`)과 패널 기준점(`at`)을 옮김.
     - 상태 다시: `distance < 0` → clash, `< min_gap` → tight, 아니면 clear.
     - **`stale`** = 검출 지점(`spot`)이 이제 면 발자국 밖이면 True → 패널에 `~` 표시 ("이 값은 오래됨").
     - 거리 없는 면은 `at` 만 옮김.
     - **`centre`(판정 판)와 `inside_at` 도 옮김**, 손잡이 행렬도 옮김. (이름표 `name_at` 은 안 옮김 → 제자리)
     - `faces`, `blocked`, `offset`, `placeable` 다시 계산.
   - `show_markers(..., fresh=False)` : 마커 다시 그림 (`_slid_cells` = mark 상태가 clash 인 면).
7. payload `"offset +0.012"`.

---

## 3. 기즈모 — `EbsSimulateGrip` (20분) (overlay 911)

### 3-1. 세우기
- `place(verdict, to_screen)` (923) : `verdict["grip"]` 이 있으면 하나뿐인 인스턴스(`_one`)로 `stand`.
- `stand(grip, to_screen)` (1014) : EBS 로컬의 손잡이 자리(`at`)에서 좌우(`side` 축)로 `wide/2` 씩 두 끝 `_ends`. `sim().watch_grip(self)` → **카메라보다 먼저 마우스를 받게** 건다 (4회차).
- `_draw(state)` (1031) : `/EbsGrip` 아래에 **화살표 둘 + 가운데 구체를 메시 하나로** (세션 레이어). 색은 `GRIP_COLORS[idle|hold]`.
- 자리·크기는 7회차 `build_verdict` 의 `_grip_spot` (2103) : EBS 앞쪽 면, 높이 `GRIP_HEIGHT`(0.3), 폭 `GRIP_WIDE`, 굵기 `GRIP_TALL`. 이격 판은 그 아래 `OFFSET_HEIGHT`(0.25).

### 3-2. hover — 테두리
- `sweep()` (934) : 오버레이 `_tick` 이 매 프레임 부른다. 끄는 중이 아니고 바쁘지 않으면 **커서 자리**(`_where` — carb 마우스 정규좌표 × 앱 창 크기)가 몸통 위인지 `_hit` 으로 보고, 바뀌었을 때만 `_ring`.
- `_ring(on)` (1098) : **선택 그룹 외곽선** 기능을 빌려 몸통에 테두리(`GRIP_OUTLINE` 색, 음영 없음). `_widen` 이 그동안만 외곽선 굵기 설정을 `GRIP_OUTLINE_WIDTH` 로 바꿨다 되돌린다.
- `_hit(x, y)` (1209) : 화면에 비친 몸통 선분까지 거리 ≤ 집는 반지름(`_pick_pixels`, 최소 `GRIP_PICK` 14px).

### 3-3. 끌기
| 단계 | 함수 | 하는 일 |
|---|---|---|
| 누름 | `press` (1152) | 몸통이면 시작 x, 시작 넛지(`_was`) 기억, `hold_clash(False)`, hold 색. **True 를 돌려 카메라 궤도를 막는다** |
| 끌기 | `drag` (1164) | `metres = (x − 시작x) / 단위당픽셀 × m/단위` → `slide(_was + metres)` → `restate` |
| 뗌 | `release` (1175) | idle 색, `_again = True`, `begin_work(WORK_SETTLE)`, `_settle()` 띄움 |
| 재판정 | `_settle` (1191) | 한 프레임 뒤 `hold_clash(True)` → `settle()` → `_again=False`, `restate`, `end_work` |

- `held()` (968) : 잡는 중이거나 놓고 재판정 중 → 판정 판 숨김(7회차), 방향키 무시.
- `_unit_pixels` (1230) : 몸통 양 끝의 화면 거리 / 월드 거리.

---

## 4. 방향키 — `extension.py` (10분)

| 이벤트 | 하는 일 |
|---|---|
| PRESS | 도는 반복 멈춤(`_let_go`) → `_step(±1)` → 성공했고 KEY_REPEAT 을 본 적 없으면 `_repeat` 코루틴 시작 |
| REPEAT | `_repeats = True` (이후 자체 반복 안 띄움), `_let_go`, `_step` |
| RELEASE | `_let_go`, `_settle` |

- `_step(way)` (132) : 손잡이 잡는 중이면 무시. 기다리던 재판정 취소(`_hold_off`) → `sim.nudge_by(way × KEY_STEP)` (5mm).
- `nudge_by` (ebs_simulate 1443) : 바쁘지 않고 SIM 결과(`_verdict`)가 있을 때만. `hold_clash(False)` → `slide(_nudge + m)` → `restate`.
- `_repeat` (89) : 웹 스트리밍은 KEY_REPEAT 이 안 와서 직접 반복. 0.5초 뒤 0.05초마다, 최대 10초.
- `_settle` → `_settle_later` : **0.75초 동안 더 안 눌리면** `run_nudge_settle` (1454) — `begin_work(WORK_SETTLE)` → 한 프레임 → `hold_clash(True)` → `settle` → `restate` → `end_work`.
- 연타하면 매번 `_hold_off` 로 대기가 취소되어 **마지막 키를 뗀 뒤 0.75초에 한 번만** 잰다.

---

## 5. 내부 재판정 — `hold_clash`, `_retest_inner` (5분) (328, 336)

- `hold_clash(False)` : `_clash_on=False`, 빨간 충돌 상자 뿌리만 감춤.
- `hold_clash(True)` → `_retest_inner()` : **`Collide.check_equipment` 만** 다시 돌려 `verdict` 의 `inside`, `boxes`, `placeable` 을 고치고 마커를 다시 그린다.
- `run_ebs_refresh` (1420) : 넛지가 있으면 `hold_clash(False)` → `slide(0)` → restate → 한 프레임 → `hold_clash(True)` → restate → settle. (dummy UI Refresh, T 키)

---

## 6. 3D 마커 — `EbsSimulateMarks` (10분) (overlay 1258)

- `sim._marks()` (3093) : `MARKER_ROOT`(`/EbsCollisionMarkers`) 용 인스턴스 하나. 기즈모도 자기 뿌리(`/EbsGrip`)용 인스턴스를 따로 갖는다.
- **`show_markers`** (3067) → `_face_sheets` (세 면 판의 네 점과 막힘/빠듯함) → **`draw(sheets, marks, boxes, fresh)`** (1275):
  | 그리는 것 | 함수 | 색 |
  |---|---|---|
  | 세 면 판 | `_sheets` | 막힘·빠듯 = 빨강 반투명, 아니면 흰 거의 투명 |
  | 거리선 + 양 끝 화살촉 | `_gap_lines`, `_gap_heads` | 빠듯·충돌 = 빨강, 아니면 갈색 |
  | 안내선 | `_lead_line`, `_lead_tick` | 흰색 |
  | 충돌 상자 | `_clash_boxes`, `_clash_cube` | 빨강 반투명, **2초 주기 깜박임** (`_start_pulse`, 머티리얼 opacity 를 매 프레임) |
- **지우지 않고 감춘다**: `_keep` 으로 이번에 그린 경로를 적고, `_show_only` 가 그 외를 visibility 로 감춘다 (`_light`, 바뀔 때만 씀). 프림·머티리얼은 재사용 → 다시 그릴 때 빠르다.
- `clear()` : 전부 감춤. `drop_looks()` : 뿌리째 삭제 (teardown).
- `_material` (1866) : 마커 머티리얼에 **UsdPreviewSurface + OmniPBR(MDL)** 두 셰이더를 단다 (RTX 는 MDL 쪽). 같은 설정이면 재사용.

---

## 7. 스킨 (대상 장비 머티리얼) (10분)

### 7-1. 무엇을 입히나 — `set_skin` (502), `_skin_material` (597)
`_skin` 값 모양에 따라:
| 값 | 만드는 함수 | 결과 |
|---|---|---|
| `#RRGGBB` 또는 `r,g,b` | `_make_colour` | PreviewSurface 단색 |
| `/` 로 시작 (씬 머티리얼 경로) | **`_copy_skin`** | 그 머티리얼 하위 전체를 `/EbsSkin/M_skin` 으로 **복사** |
| 그 외 (`.mdl` 경로) | `_make_skin` | MDL 셰이더 하나 |

- 모두 세션 레이어 `/EbsSkin/M_skin`. `_skin_made` 로 한 번 만들면 재사용.

### 7-2. 복사 — `_copy_skin` (635)
- init 끝에서 미리 한 번 (1회차).
- 머티리얼 아래 모든 프림·속성을 복사, 연결(connection)은 새 경로로 바꿔 잇는다.
- **에셋 경로는 "풀린 절대 경로"** 로 바꿔 쓴다 (콘솔 `[ebs] skin copy ... 'authored' -> 'resolved'`). 원격(`omniverse://` 등)이면 `_skin_remote` 에 모은다.

### 7-3. 연결 확인 — `_reach_skin` (606)
- SIM ④ 에서 `wear_skin` 직전. `_skin_remote` 의 URL 마다 `stat_async`:
  - OK 면 통과.
  - 아니면 `connection lost` → `omni.client.reconnect(url)` → 다시 stat → `reconnected` 면 **복사본을 새로** 만들게(`_skin_made=""`, `strip_skin`), 아니면 `still cannot reach ..., the skin may render red`.
- 배경: 세션이 오래 가면 Nucleus 연결이 끊겨 MDL 로드 실패 → 빨갛게 보이는 문제.

### 7-4. 입히기 — `wear_skin` (743)
1. 꺼져 있거나 값이 없으면 벗기고 끝.
2. 같은 장비·같은 스킨이면 그대로 (`_skin_worn`).
3. 벗기고(`strip_skin`), 머티리얼 준비.
4. 최대 `SKIN_DEEP`(8)번: `_skin_plan` 으로 **인스턴스 뿌리**와 **지오메트리** 를 찾고, 인스턴스면 `_open_instances` 로 세션 레이어에서 `instanceable=False` (인스턴스 안쪽에는 바인딩을 못 걸어서).
5. `_bind_all` : 지오메트리마다 `MaterialBindingAPI.Bind` (ChangeBlock 한 덩이). 건 경로는 `_skin_wrote`.
- 콘솔 `[ebs] skin plan N: …`, `[ebs] skin bind: X of Y bound`.

### 7-5. 벗기기
| 함수 | 하는 일 | 누가 |
|---|---|---|
| `strip_skin` (513) | `_skin_wrote` 의 바인딩만 해제. 연 인스턴스는 그대로 | 다음 SIM, Clear, set_skin |
| `close_skin` (540) | 연 인스턴스를 다시 묶음 | `drop_skin` |
| `drop_skin` (801) | strip + close + `/EbsSkin` 삭제 | teardown |

---

## 8. Clear 한 번 (5분) — `run_clear` (1402) → `clear_all` (3274)

`_clear_work` 차례 (하나가 실패해도 나머지는 계속, `_clear_run`):
1. `strip_skin` — 스킨 벗기기
2. `clear_markers` — `_verdict = {}`, 마커 감춤
3. `clear_port_lasers`, `clear_sweep`
4. `release_camera` — 스킨 벗기기, 카메라 놓기(원래 뷰포트 카메라), **`go_home`**
5. `hide_ebs` — EBS 숨김
그 뒤 `settle_skin`, `set_nudge(0)`.

> Clear 후에도 `_target`, `_aligned`, `_results` 는 남는다. 다음 SIM 은 `_do_prepare` 가 `_aligned` 를 내리고 새로 놓으므로 영향이 없고, 판정 기록은 계속 조회된다. 단 `_verdict` 가 비어 **넛지는 막힌다.**

---

## 9. 눈에 띈 것 (참고)

- 연 인스턴스(`_skin_opened`)는 teardown 전까지 다시 안 묶여 쌓인다 (장시간 세션 메모리 우려, 지난 대화).
- 기즈모 드래그 방향은 화면 x 축 기준이다. 카메라가 반대편에서 보면 끄는 방향과 EBS 이동 방향이 반대로 느껴질 수 있다.
- `inside_at` 은 slide 에서 옮기지만 쓰는 판이 없다 (7회차).

---

## 10. 마무리 메모 (5분)

1. 밀 때 다시 재는 것과 산수로 고치는 것
2. 기즈모 누름→끌기→뗌→재판정
3. 방향키 연타 시 재판정이 한 번만 도는 원리
4. 마커를 지우지 않고 감추는 이유
5. 스킨 값 세 종류와 연결 끊김 대처

---

## 자가 테스트

### A. 넛지
1. 넛지 한계와 단위.
2. 넛지는 무엇을 기준으로 다시 계산되나? 그 이점은?
3. `_place_nudged` 가 포트 계산을 다시 하지 않는 이유.
4. `_slid_box` 가 바운드를 다시 재지 않는 방법.

### B. slide 산수
5. 오른쪽으로 10mm 밀면 오른쪽·왼쪽·천장 면 거리는 각각?
6. 산수 후 상태 규칙.
7. `stale` 은 언제 True 이고 화면에 어떻게 보이나?
8. slide 가 옮기는 verdict 기준점과 옮기지 않는 것.
9. slide 중 외부 판정을 다시 재나?

### C. 기즈모
10. 기즈모가 카메라보다 먼저 마우스를 받는 방법.
11. 기즈모 모양과 그려지는 곳.
12. hover 판정은 누가, 언제, 무엇으로 하나?
13. 테두리는 어떤 기능을 빌려 그리나? 굵기는?
14. 누름에서 하는 일 넷.
15. 끌기의 미터 환산식.
16. 뗌 이후의 흐름.
17. `held()` 가 True 인 때와 그 영향 둘.

### D. 방향키
18. PRESS / REPEAT / RELEASE 각각.
19. `nudge_by` 가 거절하는 조건.
20. 연타해도 재판정이 마지막에 한 번만 도는 이유.
21. `run_nudge_settle` 의 순서.

### E. 재판정
22. `hold_clash(False)` / `hold_clash(True)` 가 하는 일.
23. `_retest_inner` 가 고치는 verdict 키 셋.
24. `run_ebs_refresh` 의 순서.

### F. 마커
25. `draw` 가 그리는 네 가지와 색 규칙.
26. 충돌 상자 깜박임의 원리와 주기.
27. 마커를 지우지 않고 감추는 방법과 이유.
28. 마커 머티리얼이 셰이더 둘을 다는 이유.
29. `clear` 와 `drop_looks` 의 차이.

### G. 스킨
30. 스킨 값 세 종류와 각 생성 함수.
31. `_copy_skin` 이 에셋 경로를 바꾸는 방식과, 원격 URL 을 모으는 이유.
32. `_reach_skin` 의 흐름과 세 가지 로그.
33. 재연결에 성공하면 무엇을 하나?
34. 인스턴스 장비에 머티리얼을 거는 방법.
35. `strip_skin`, `close_skin`, `drop_skin` 차이.

### H. Clear
36. Clear 의 다섯 차례.
37. Clear 뒤 남는 상태와 그 영향.

### I. 응용
38. "밀었더니 거리 판에 `~` 가 붙었다" — 의미와 해결.
39. "기즈모 위에 커서를 올려도 테두리가 안 생긴다" — 확인 순서.
40. "며칠 뒤 대상 장비가 빨갛다" — 콘솔에서 볼 로그.
41. 방향키 한 칸을 2mm 로 바꾸려면? 재판정 대기를 0.5초로?

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. ±1m (`NUDGE_LIMIT`), 미터.
2. 넛지 0 자리 `_base`. 매번 기준에서 다시 계산하므로 오차가 쌓이지 않는다.
3. EBS 가 레일을 따라 좌우로만 움직여 포트·피봇이 안 바뀌기 때문 (`_base` + 넛지로 충분).
4. 기존 BBox3d 의 행렬에 이동 행렬을 곱해 `_ebs_box` 에 넣는다.
5. 오른쪽 −10mm, 왼쪽 +10mm, 천장 그대로 (면 바깥방향과 오른쪽의 내적 × shift).
6. distance < 0 → clash, < min_gap → tight, 그 외 clear.
7. 검출 지점이 면 발자국(행·열 축 범위) 밖으로 벗어났을 때. 거리 앞에 `~`.
8. 옮김: `centre`, `inside_at`, 손잡이 행렬, 면 mark 의 from/to/at. 안 옮김: `name_at`(이름표), 검출 지점 `spot`, 안내선.
9. 아니다. 산수로만 고친다 (내부만 손 뗀 뒤 다시 잼).
10. `stand` 에서 `sim().watch_grip(self)` → 카메라의 `_pressed`/`_moved` 가 기즈모 `press`/`drag` 를 먼저 부르고 True 면 궤도 회전을 안 한다.
11. 양방향 화살표 둘 + 가운데 구체, 메시 하나. `/EbsGrip` 아래 세션 레이어.
12. 오버레이 `_tick` 이 매 프레임 `sweep` → carb 커서 좌표 → `_hit`.
13. 선택 그룹 외곽선(`register_selection_group`). 테두리 동안만 외곽선 굵기 설정을 3.5 로.
14. 시작 x 기억, 시작 넛지 기억, 충돌 상자 감춤(`hold_clash(False)`), hold 색으로 다시 그림 (+ True 반환).
15. (현재x − 시작x) / 단위당픽셀 × 미터/단위.
16. idle 색 → `_again=True` → restate·wake → `begin_work(WORK_SETTLE)` → 다음 프레임 `hold_clash(True)` → settle → `_again=False` → restate → end_work.
17. 잡는 중 또는 놓고 재판정 중. 판정 판 숨김, 방향키 `_step` 무시.
18. PRESS: 반복 멈춤·한 칸·(REPEAT 본 적 없으면) 자체 반복 시작. REPEAT: `_repeats=True`·반복 멈춤·한 칸. RELEASE: 반복 멈춤·재판정 예약.
19. 바쁨, `_target` 없음, 배치 안 됨, 판정 없음 → "Not in SIM".
20. 매 `_step` 과 `_settle` 이 이전 대기를 `_hold_off` 로 취소하고 새로 0.75초를 건다.
21. 바쁘면/판정 없으면 끝 → wake → `begin_work(WORK_SETTLE)` → 한 프레임 → `hold_clash(True)` → settle → restate → end_work.
22. False: 충돌 상자 뿌리 감춤. True: `_retest_inner` 로 내부 간섭만 다시 재고 그림.
23. `inside`, `boxes`, `placeable`.
24. 넛지가 있으면 hold_clash(False) → slide(0) → restate → 한 프레임 → hold_clash(True) → restate → settle.
25. 세 면 판(막힘·빠듯 빨강/흰), 거리선+화살촉(빠듯·충돌 빨강/갈색), 안내선(흰), 충돌 상자(빨강 반투명).
26. 매 프레임 머티리얼 opacity 입력을 코사인 파형으로 바꾼다. 2초 (0.15~0.99).
27. 이번에 그린 경로(`_keep`) 외에는 visibility 를 invisible 로 (`_show_only`). 프림·머티리얼 재사용으로 다시 그릴 때 빠르다.
28. PreviewSurface 는 일반 뷰, RTX 는 MDL(OmniPBR)을 쓰므로 둘 다.
29. clear 는 감추기만, drop_looks 는 뿌리째 삭제와 캐시 비우기.
30. 색(`_make_colour`), 씬 머티리얼 경로(`_copy_skin`), .mdl(`_make_skin`).
31. AssetPath 를 `resolvedPath`(없으면 원래 값)로 바꿔 쓴다. 원격이면 `_skin_remote` 에 모아 SIM 때 연결 확인에 쓴다.
32. URL 마다 stat → 실패면 `connection lost` → `reconnecting` → reconnect → 다시 stat → `reconnected` 또는 `still cannot reach ... red`.
33. `_skin_made` 를 비우고 `strip_skin` → 다음 `wear_skin` 이 새로 복사.
34. 세션 레이어에서 그 인스턴스를 `instanceable=False` 로 연 뒤 안쪽 지오메트리에 바인딩 (최대 8번 반복).
35. strip: 바인딩만 해제 / close: 연 인스턴스 다시 묶기 / drop: 둘 다 + `/EbsSkin` 삭제.
36. 스킨 벗기기 → 마커·판정 지우기 → 레이저·스윕 지우기 → 카메라 놓고 홈 → EBS 숨김.
37. `_target`, `_aligned`, `_results` 가 남는다. 다음 SIM 은 새로 배치하고(`_do_prepare` 가 `_aligned` 를 내림), 판정 기록은 계속 조회되며, `_verdict` 가 비어 넛지는 "Not in SIM" 으로 막힌다.
38. 민 뒤 검출 지점이 면 발자국 밖이라 거리가 믿을 수 없다. 다시 SIM 하면 새로 잰다.
39. 바쁜 중인가 → 끄는 중인가 → 커서 좌표를 못 읽었나(콘솔 "grip hover cannot ask the cursor") → `_hit` 반지름.
40. `[ebs] skin: connection lost / reconnecting / reconnected / still cannot reach`.
41. `extension.KEY_STEP = 0.002`, `KEY_WAIT = 0.5`.

</details>
