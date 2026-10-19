# 4회차 · EBS 배치 · 카메라

> 목표: SIM ② `_do_stage` 가 **EBS 를 어디에 어떤 자세로** 놓는지, ③ `_do_focus` 가 **카메라를 어디에** 세우고 사용자의 마우스 조작을 어떻게 받는지 설명할 수 있게.
> 시간: 약 1시간 15분. 수학 함수(`_compose`, `_euler`, `_normalized_rows`, `_extract_scale`)는 **이름과 역할만** 본다.

---

## 0. 오늘의 지도

```
② _do_stage()                                        ebs_simulate.py 1680
   ├─ clear_markers()                지난 마커 걷기
   ├─ 이미 _aligned 면 그대로
   └─ _do_align(reveal=지금 보이나, wear=False)       1719
        ├─ pivot_target(anchor)     피봇에서 왼쪽으로 0.505      2806
        ├─ _pushed(anchor, base)    넛지만큼 좌우로             487
        ├─ _place_ebs(ebs, 목표, anchor)  회전은 피봇에서       2897
        │    └─ _write_transform    세션 레이어에 xformOp 쓰기
        └─ (실패) _align_prims      피봇 프림에 통째로 맞춤
③ _do_focus()                                        1691
   ├─ camera.pick_only(장비, EBS)   더블클릭 받을 대상
   └─ camera.place(stage, _framed_box(), anchor)     camera.py 270
        ├─ _frame(anchor)           피봇이 보는 방향 → 카메라 세 축
        ├─ 관심점 = EBS 상자 중심, 높이만 53%
        ├─ 눈 = 관심점 + 카메라 뒤쪽 × 25
        ├─ _write(...)              행렬·근평면·궤도중심 쓰기
        └─ _grab()                  마우스 받는 판 깔기
```

> **세 축 하나로 모든 게 정해진다**: `camera.axes(stage, anchor)` 가 주는 `x_cam`(=오른쪽) 이 EBS 의 "좌/우" 이자 넛지 방향이고, 카메라 시선 방향이다.

---

## 1. 세 축 — `EbsSimulateCamera._frame` (camera.py 618) (10분)

먼저 이걸 이해해야 배치와 카메라가 같이 풀린다.

- 피봇(anchor) 월드 회전의 **2번째 행(로컬 Y)** = 피봇이 "보는 방향" `view`.
- 위쪽 행: 스테이지 up 이 Y 면 2번째, Z 면 3번째 행.
- `z_cam = -view` (카메라는 view 반대편에 서서 view 쪽을 본다)
- `x_cam = up × z_cam` (오른쪽), `y_cam = z_cam × x_cam` (위).
- `axes(stage, facing)` (613) 는 이것을 그대로 돌려준다.

---

## 2. 배치 (25분)

### 2-1. `_do_stage` (1680)
- `_target` 없으면 실패. 마커를 걷는다.
- 이미 `_aligned` 면 다시 안 놓는다. 단 `_do_prepare` 가 성공할 때마다 `_aligned = False` 로 내리므로, **SIM 흐름에서는 늘 다시 놓는다.**
- `_do_align(reveal=EBS가 지금 보이나, wear=False)` : init 에서 EBS 를 숨겼으므로 보통 **숨긴 채로 놓는다**. 스킨도 여기서는 안 입힌다.

### 2-2. `_do_align` (1719)
1. 마커 걷기.
2. `with _phase("place"), _stage_timer("align EBS")`:
   - `self._base = pivot_target(anchor)` — **넛지 0 기준 자리**. 넛지(8회차)가 이것을 기준으로 다시 민다.
   - `target = _pushed(anchor, _base)` — 넛지만큼 `x_cam` 방향으로.
   - 목표가 있으면 `_place_ebs`, 없으면 `_align_prims` (피봇 프림에 통째로).
   - 결과는 `self._aligned`.
3. EBS 삼각형 캐시(`Collide._forget_triangles`)와 `_ebs_box` 를 버린다 → EBS 가 움직였으니 다시 재야 한다.
4. `wear` 면 스킨, `reveal` 이면 EBS 보이기.
5. 레이저 디버그가 켜져 있으면 `show_port_lasers`, 아니면 걷기.
6. 실패면 `SAY_ALIGN`.

### 2-3. 목표점 — `pivot_target` (2806)
- 피봇 월드 위치 + **왼쪽(−x_cam) × `PIVOT_SHIFT`(0.505)**.
- 높이와 앞뒤는 피봇 그대로.

### 2-4. 놓기 — `_place_ebs` (2897)
- **회전**: 피봇 월드 행렬을 EBS 부모 공간으로 옮겨 크기를 뺀 세 축 (`_normalized_rows`).
- **크기**: EBS 가 원래 갖고 있던 크기 그대로 (`_extract_scale`).
- **이동**: 목표 월드점을 EBS 부모 공간으로 (`to_ebs_space.Transform`).
- → `_write_transform`.

### 2-5. 쓰기 — `_write_transform` (2947)
**세션 레이어** 에 쓴다 (원본 USD 는 안 건드림). 있는 xformOp 에 맞춰:
1. `xformOp:transform` 이 있으면 → 4×4 한 장 (`_compose`).
2. `xformOp:translate` + 회전 op 가 있으면 → `_set_rotation` (orient 쿼터니언 또는 rotateXYZ 계열 6종 중 있는 것, `_euler`) + translate.
3. 둘 다 없으면 → op 순서를 지우고 transform op 하나 새로.
4. 그것도 실패하면 → `XformCommonAPI` 로 이동만.

### 2-6. 대체 경로 — `_align_prims` (2915), `_pushed_local` (2935)
- 피봇 목표를 못 구했을 때. EBS 를 **피봇 프림의 월드 행렬 그대로** (회전·위치) 맞추고, 넛지는 부모 공간으로 바꿔 더한다.

### 2-7. 지금 안 쓰이는 것
- `compute_target` (2824), `_snap_shift` (2857) : 포트 좌표로 목표를 잡고 포트 1 을 피봇에 맞추던 방식. **현재 SIM 경로에서 안 부른다.**
- 그래서 `_port_world` 가 늘 비어, 디버그 레이저(`show_port_lasers`)는 그릴 점이 없다.

---

## 3. 카메라 세우기 (20분)

### 3-1. `_do_focus` (1691)
- `facing` = 피봇 (없으면 EBS).
- `camera.pick_only(장비, EBS)` : 더블클릭으로 궤도 중심을 옮길 수 있는 대상을 이 둘로 한정.
- `camera.place(stage, _framed_box(), facing)` (시간은 `"camera"` 단계에).
- 실패면 `SAY_CAMERA`.
- `_framed_box` (1708) : EBS 월드 상자. **숨긴 EBS 는 상자가 비어 나오므로** 잠깐 켜서 재고 다시 끈다.

### 3-2. `place` (camera.py 270)
1. 뷰포트를 **우리 카메라 `/EbsCamera`** 로 바꾸고 원래 카메라를 `_previous` 에 기억 (`_take_viewport`).
2. 세 축 = `_frame(stage, facing)`.
3. **관심점(궤도 중심)** = EBS 상자 중심, 단 위쪽 성분만 `low + (high-low) × LOOK_HEIGHT(0.53)`.
4. **눈** = 관심점 + `z_cam × CAMERA_BACK(25)`.
5. `_write` 로 쓰고, `_home` 에 첫 자리를 저장 (→ `cam_refresh` 가 여기로 되돌림).
6. `_orbit = True`, `_grab()` 으로 마우스 받기 시작.

### 3-3. `_write` (camera.py 638)
- 세 축과 눈으로 4×4 행렬 → `xformOp:transform` (세션 레이어).
- **근평면** `_near` : 카메라 거리 − (EBS 폭 절반 × `_span`). 기본 `NEAR_SPAN 2.5`. EBS 앞을 가리는 것을 잘라 내는 장치. (`set_near_span` 으로 바꿈)
- `omni:kit:centerOfInterest` = (0, 0, −거리) → 킷이 아는 궤도 중심도 맞춘다.

### 3-4. 카메라 프림
- `make` (191) : init 때 세션 레이어에 `/EbsCamera` 정의 (focal 50, aperture).
- `_camera` (299) : 없으면 만들어서 준다.

---

## 4. 마우스 조작 (15분)

### 4-1. 판 깔기 — `_grab` (321)
- `_grab_sheet` : 뷰포트 창 위에 **거의 투명한 사각형** 을 깔고 마우스 콜백 다섯 개(누름, 움직임, 뗌, 더블클릭, 휠)를 건다.
- `_silence` : 킷 기본 조작을 끈다 — 선택(`PickLock`), 우클릭 메뉴, **카메라 단축키 설정(bindings)을 비움** (원래 값은 `_bindings` 에 보관).
- `_drop` / `_restore` : 판을 걷고 꺼 둔 것을 되돌린다.

### 4-2. 조작

| 입력 | 함수 | 하는 일 | 한계 |
|---|---|---|---|
| 왼쪽 드래그 | `_pressed` → `_moved` → `_drag` → `_turn` | 관심점을 중심으로 좌우(yaw)·상하(pitch) 회전 | 픽셀당 0.35°, 위로 15° 아래로 10° (`_room`) |
| 휠 | `_wheel` → `_zoom` | 팔 길이 × 0.88^칸 | 25 × 0.15 ~ 25 × 8 |
| 더블클릭 | `_double` → `request_query` → `_picked` → `_look_at` | 클릭한 점을 새 관심점으로 | `pick_only` 대상 안, 우리 프림 제외 |

- **손잡이가 먼저 본다**: `watch(grip)` 로 기즈모를 걸어 두면, 누름/드래그를 기즈모가 먼저 받고 처리했으면 카메라는 안 돈다 (8회차).
- `hold(True)` : 궤도 조작을 잠깐 멈춤 (기즈모 잡는 동안).
- `_settle(hold, arm, radius)` : 관심점에서 팔 방향으로 radius 만큼 물러난 자리에, up 축을 유지하며 카메라를 다시 쓴다. 모든 조작의 끝.

### 4-3. 되돌리기
| 함수 | 하는 일 | 누가 |
|---|---|---|
| `reset` (248) | `place` 때 저장한 `_home` 으로 | `refresh_camera` ← `cam_refresh` API |
| `release` (232) | 판을 걷고 뷰포트를 원래 카메라로 | `release_camera` (Clear) |
| `go_home` (206) | 원래 Persp 카메라를 정해 둔 첫 장면(HOME_*)에 세우고 뷰포트를 그쪽으로 | init, Clear |
| `remove` (261) | release + `/EbsCamera` 삭제 | teardown |

---

## 5. 눈에 띈 것 (참고)

- 카메라와 EBS 의 "오른쪽" 이 같은 함수(`_frame`)에서 나오므로, 피봇의 로컬 Y 축이 엉뚱하면 EBS 위치·카메라 방향·넛지 방향이 **함께** 틀어진다.
- 모든 쓰기가 세션 레이어라, 파일을 저장해도 원본 USD 는 안 바뀐다.
- `CAMERA_BACK` 은 카메라 거리이자 줌 한계의 기준이다.

---

## 6. 마무리 메모 (5분)

1. 세 축은 어디서 나오고 무엇에 쓰이나
2. EBS 목표점과 회전·크기는 각각 어디서 오나
3. xformOp 를 쓰는 네 갈래
4. 카메라 관심점·눈·근평면 식
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

### A. 세 축
1. `_frame` 이 "보는 방향" 으로 쓰는 것은 피봇 행렬의 무엇인가?
2. `x_cam`, `y_cam`, `z_cam` 을 만드는 식.
3. 카메라는 피봇이 보는 방향의 앞에 서나, 뒤에 서나?
4. `axes()` 가 쓰이는 곳 셋 (오늘 본 범위).

### B. 배치
5. `_do_stage` 가 EBS 를 다시 놓지 않는 경우는?
6. `_do_stage` 가 `_do_align` 에 주는 두 인자와 그 이유.
7. `_base` 는 무엇이고 누가 다시 쓰나?
8. `pivot_target` 의 식. 높이는?
9. `_place_ebs` 가 회전·크기·이동을 각각 어디서 가져오나?
10. `_write_transform` 의 네 갈래를 순서대로.
11. 회전 op 로 지원하는 것들.
12. 쓰기는 어느 레이어에? 그 결과는?
13. 목표점을 못 구하면 어떻게 놓나?
14. 배치 뒤 버리는 캐시 둘과 그 이유.
15. 지금 SIM 경로에서 안 쓰이는 배치 함수 둘과, 그 때문에 생기는 현상.

### C. 카메라 세우기
16. `_do_focus` 의 `facing` 은? `pick_only` 에 넘기는 것은?
17. `_framed_box` 가 EBS 를 잠깐 켜는 이유.
18. `place` 가 뷰포트에 하는 일과, 원래 카메라는 어디에 기억하나?
19. 관심점의 식 (상수 포함).
20. 눈의 식 (상수 포함).
21. 근평면 식과 기본 배수. 바꾸는 API.
22. `_home` 에 무엇이 저장되고 누가 쓰나?
23. `/EbsCamera` 는 언제, 어느 레이어에 만들어지나?

### D. 마우스
24. 마우스를 받는 방법 (무엇을 깔고 무엇을 거나).
25. `_silence` 가 끄는 셋. 카메라 단축키는 어떻게 끄고 어떻게 되살리나?
26. 드래그 회전의 감도와 상하 한계.
27. 줌의 배율과 최소·최대 거리.
28. 더블클릭이 관심점을 바꾸지 않는 경우 넷.
29. 기즈모와 카메라가 같은 누름을 두고 다툴 때 누가 먼저인가? 어떻게?
30. `_settle` 이 하는 일.

### E. 되돌리기
31. `reset`, `release`, `go_home`, `remove` 의 차이와 각각을 부르는 곳.
32. `cam_refresh` 는 결국 어디로 되돌리나?

### F. 응용
33. "EBS 가 장비 오른쪽에 붙어야 하는데 왼쪽에 놓인다" — 의심할 곳.
34. "카메라가 장비 뒤에서 본다" — 의심할 곳.
35. "EBS 앞에 기둥이 가려 안 보인다" — 무엇을 조절하나?
36. "더블클릭해도 궤도 중심이 안 바뀐다" — 확인 순서.
37. 카메라를 조금 더 멀리 두려면? 시선을 더 높게 하려면?

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. 월드 회전 행렬의 2번째 행 (로컬 Y 축).
2. `z_cam = -view`, `x_cam = up × z_cam`, `y_cam = z_cam × x_cam` (모두 정규화).
3. 반대편(뒤)에 서서 view 방향을 본다. 눈 = 관심점 + z_cam × 거리 = 관심점 − view × 거리.
4. `pivot_target`(왼쪽), `_pushed`/`_pushed_local`(넛지 방향), `place`(카메라 축).
5. 이미 `_aligned` 일 때. 하지만 `_do_prepare` 가 성공하면 `_aligned` 를 내리므로 SIM 에서는 늘 다시 놓는다.
6. `reveal=지금 보이나`(숨긴 EBS 는 숨긴 채), `wear=False`(스킨은 ④에서).
7. 넛지 0 기준 목표점. 넛지(slide)가 이것을 기준으로 다시 민다.
8. 피봇 월드 위치 + (−x_cam) × 0.505. 높이와 앞뒤는 피봇 그대로.
9. 회전: 피봇 월드 행렬(EBS 부모 공간, 크기 제거) / 크기: EBS 원래 로컬 크기 / 이동: 목표 월드점을 EBS 부모 공간으로.
10. transform op → translate+회전 op → op 비우고 transform 하나 새로 → XformCommonAPI 이동만.
11. `xformOp:orient`(쿼터니언, float/half 정밀도 맞춤), `rotateXYZ` `XZY` `YXZ` `YZX` `ZXY` `ZYX`.
12. 세션 레이어. 원본 USD 파일은 안 바뀐다.
13. `_align_prims` — 피봇 프림의 월드 행렬 그대로 맞추고 넛지는 부모 공간으로 더함.
14. EBS 삼각형 캐시(`_forget_triangles`), `_ebs_box`. EBS 가 움직였으니 다시 재야 해서.
15. `compute_target`, `_snap_shift`. `_port_world` 가 비어 디버그 레이저가 안 그려진다.
16. 피봇(없으면 EBS). 대상 장비와 EBS.
17. 숨긴 프림은 바운드가 비어 나와서.
18. 뷰포트 카메라를 `/EbsCamera` 로 바꾼다. 원래 경로는 `_previous`.
19. EBS 상자 중심, 단 up 성분 = low + (high − low) × 0.53.
20. 관심점 + z_cam × 25 (`CAMERA_BACK`).
21. 거리 − EBS 폭 절반(x_cam 방향) × span, 최소 0.01. 기본 2.5. `set_near_span`.
22. (세 축, 눈, 거리, 관심점). `reset` (← `refresh_camera` ← `cam_refresh`).
23. init 의 `camera.make`. 세션 레이어.
24. 뷰포트 창 frame 에 거의 투명한 Rectangle 을 깔고, 누름·움직임·뗌·더블클릭·휠 콜백을 건다.
25. 선택, 컨텍스트 메뉴, 카메라 bindings. bindings 설정을 `{}` 로 비우고 원래 값을 `_bindings` 에 두었다가 `_restore` 에서 되돌린다.
26. 픽셀당 0.35°. 위로 15°, 아래로 10°.
27. 칸당 × 0.88. 25 × 0.15 = 3.75 ~ 25 × 8 = 200.
28. 조작 잠금(`_held`) 중, 궤도 꺼짐, 왼쪽 버튼 아님, 맞은 프림이 우리 것이거나 `pick_only` 대상 밖. (+ 뷰포트 밖)
29. 기즈모. `watch(grip)` 로 걸린 기즈모의 `press`/`drag` 가 True 를 돌려주면 카메라는 그 입력을 버린다.
30. 관심점에서 팔 방향으로 radius 만큼 물러난 자리에, up 을 유지한 세 축으로 카메라를 다시 쓴다.
31. reset: place 첫 자리로(cam_refresh) / release: 판 걷고 원래 카메라로(Clear) / go_home: Persp 를 HOME 장면에(init, Clear) / remove: release + 카메라 프림 삭제(teardown).
32. 이번 SIM 의 `place` 가 처음 잡은 자리 (`_home`).
33. 피봇의 로컬 Y 축 방향(x_cam 이 뒤집힘), 잘못 고른 피봇, `PIVOT_SHIFT` 부호.
34. 피봇 로컬 Y 축이 반대 방향인 장비, 또는 피봇이 엉뚱한 프림.
35. 근평면 배수 `set_near_span` (dummy UI near 슬라이더).
36. 궤도가 켜져 있나 → 조작 잠금 중인가 → 클릭한 프림이 `pick_only`(장비·EBS) 안인가 → 우리 프림을 찍지 않았나.
37. `CAMERA_BACK` 을 키운다. `LOOK_HEIGHT` 를 키운다.

</details>
