# marker 남은 일

## undo / redo (omni.kit.commands) — 나중에

검토만 끝남. 구현 전에 아래 "정할 것"부터.

### 설계 (검토안)
- `omni.kit.commands.Command` 를 상속해 `do()` / `undo()`. `omni.kit.commands.execute(...)` 로 실행하면 Kit undo 스택에 쌓이고, `omni.kit.undo.undo()` / `redo()` 와 Ctrl+Z / Ctrl+Y 가 같은 스택을 쓴다.
- 익스텐션 시작 때 등록, 끝날 때 해제. 명령 이름은 Kit 전체에서 안 겹치게 `MarkerAnnotation...` 접두어.
- **스냅샷 명령**: 바꾸기 전 / 후 마커 dict 사본을 든다.
  - 만들기 (마커, 평면): do = 추가, undo = 그 id 삭제
  - 지우기 / 모두 지우기: do = 삭제, undo = 같은 id 로 되살림
  - 고치기 (글, 글자 크기, 위치 재설정, 평면 위치 / 크기 / 끌기, 패널 이동 / 크기, 원위치, 보이기): 전 / 후 사본 적용
  - 화살표 배수, 전체 보이기: 이전 값 / 새 값
- **끌기** (패널, 평면): 끄는 동안은 지금처럼 바로 반영, 손을 뗄 때 명령 하나 (시작 전 -> 끝).
- 화살표, 평면 USD 몸체는 데이터에서 다시 그린다 (세션 레이어 직접 편집은 Kit undo 에 안 남으니 두 번 되돌려지지 않는다).
- **id 는 그대로**: 지운 것을 undo 하면 같은 id, 만들기 undo -> redo 도 같은 id. 번호 카운터는 줄이지 않는다 (웹 1:1).
- undo / redo 도 같은 알림 `(reason, marker_id)` 을 보낸다.
- 서비스 함수가 안에서 명령을 실행 -> 시험 창, 웹 어디서 바꾸든 undo 대상. 서비스 모양은 그대로.

### 정할 것
1. Kit undo 스택과 **공유** (추천: Ctrl+Z 자연스러움, 대신 다른 USD 편집과 한 줄로 섞임) vs **마커 전용** 스택 (omni command 가 아니라 직접 만든 스택, Ctrl+Z 와 연결 안 됨)
2. undo 대상에서 뺄 것: 선택 (빼는 게 보통), 보이기 / 숨기기 넣을지
3. 불러오기, 형상 바꾸기는 undo 불가 (추천). 공유 스택에서 마커 명령만 골라 지울 수 없으니, 명령에 "형상 / 불러오기 세대 번호" 를 넣고 바뀐 뒤 옛 명령은 아무것도 안 하게
4. 웹에서 온 변경도 Kit Ctrl+Z 로 되돌아가야 하는지 (아니면 웹 경로는 명령 없이)

### 위험
- 가짜 Kit 으로는 `omni.kit.commands` / `omni.kit.undo` 진짜 동작 (그룹, 기록 창, 장면 전환 때 비우기) 확인 불가 -> Kit 에서 확인
- 공유 스택이면 끄는 도중 Ctrl+Z: 끄는 동안 무시하거나 끌기를 먼저 끝내고 되돌리기

## 그 밖에 남은 일
- **면 정보** (면을 찍으면 중점, 법선, 면적 등). 레퍼런스 `ref/ov-lab/.../face.py` 를 얼마나 가져올지
- **뷰포트 연결**: `bind_viewport(vph, prim_path, usd_path)` 가 그 vph 의 `ui_zstack` 위에 SceneView 마커 층을 깔고 `viewport_api.add_scene_view` 한다 (registry 안 씀). bind 전에는 아무 뷰포트에도 안 그려짐. `unbind_viewport(viewport_id)` 는 층도 걷는다. 화살표 / 평면 USD 몸체는 스테이지에 하나라 뷰포트별로 못 숨김
- **평면 기준점**: 새 평면 가운데 = 타겟 프림 월드 바운드 중점 (`origin` 으로 저장, 만들 때 한 번). 위치(at)는 그 점에서. 남은 것: 프림이 나중에 움직이면 다시 맞출지, 크기를 바운드에 맞출지
- **화살표 배수**: 지금 전체 공통. 마커마다 따로 둘지 미정
- **장면을 다시 열 때**: 세션 레이어가 바뀌어 화살표, 평면 몸체가 사라짐 -> 열릴 때 그 장면 마커 다시 그리기 / 불러오기
- **id 를 밖에서 정하기** (웹 파라미터 id 를 그대로 쓰려면 `create(kind, marker_id=...)`)
- **같은 마커 파일을 웹과 Kit 이 함께 저장** 할 때 누가 맡을지

## Kit 에서 확인할 것
- Kit 기즈모 API (`set_gizmo` move / rotate / off): 몸체를 Kit 선택에 넣고 `/app/transform/operation` 을 바꾸며, 붙은 동안 편집 대상을 세션 레이어로. 확인: 기즈모가 뜨는지 (pickable 꺼진 몸체도), 루트 레이어가 안 더러워지는지, 끄는 동안 orbit 이 같이 도는지, 기즈모 붙은 채 다른 편집은 세션으로 가는 문제, 회전 평면의 hover / 끌기
- hytwin vph 의 `ui_zstack` 에 마커 층이 다른 기능 층 위에 겹쳐 보이는지, 눌림이 아래 층에 막히지 않는지
- orbit 양보 뒤 ui.scene 제스처가 hytwin 뷰포트에서 눌림을 받는지
- orbit 끄기: 마커가 마우스를 쓰는 동안 (패널 / 변 / 평면 hover, 끌기, 찍기 대기) 그 뷰포트 orbit manipulator `enabled = False`, 놓으면 True (메시지 버스 click_enable 은 뺌). 확인: 패널 끌 때 카메라 안 도는지, 놓으면 돌아오는지, 휠도 같이 막히는지. 진단 views 의 `orbit_off`
- orbit 드래그 중 마커 위를 지날 때: `drag:on_began_first` ~ `on_ended` 구독 (`_OrbitWatch`). 그동안 hover 는 orbit 을 안 끄고, 끝났을 때 아직 위면 그때 끈다. 확인: 메시지 버스 pop 구독이 한 프레임 늦게 와도 괜찮은지, 진단 `orbit_dragging`
- 평면에 보이는 그림자: 비쳐 보이는 것인지 (`PLANE_OPACITY = 1.0` 으로 시험), 재질 연결이 안 된 것인지
- 평면 뒤 모델 찍기가 막히는지 (`set_pickable` 을 렌더 쿼리가 따르는지)
- 평면 끌기 (`sc.Rectangle`), 앞뒤 면 X 무늬 (`PLANE_GAP_RATIO`)
- 평면 본체 조작: 뷰포트마다 맨 뒤 `sc.Screen` 하나로 hover / 누름 / 끌기. 평면은 광선 + 깊이 쿼리로 판정. 확인: Screen hover 가 마우스 움직임마다 오는지 (진단 `plane_probe.moves` 가 늘어나는지), payload 에 광선이 있는지 (`ray`: payload / camera / none), 쿼리 답 (`query`, `model_dist`, `plane_dist`). 화면 전체 Screen 이 orbit / Kit 선택을 막지 않는지. `PLANE_PRESS_AT_DOWN`
- 화살표 / 평면 클릭이 Kit 선택에 잡히지 않는지
- 커서 모양, 네 변 크기 조절
- `DRAG_SCALE` 2.0 의 실제 원인
- Nucleus 에 `_marker.json` 쓰기 (권한)
- 시험 창 목록 줄이 선택 때 사라지지 않는지

## 정리할 것
- `SELECT_RELEASE_S` (타일 구조 뒤로는 필요 없을 수 있음)
- 시험 스크립트 (가짜 Kit, 실제 USD) 가 저장소 밖 (스크래치) 에만 있음 -> 저장소에 넣기
