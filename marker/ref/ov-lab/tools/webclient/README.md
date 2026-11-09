# MORPH Lab 웹 테스트 클라이언트

`morph.lab_streaming.kit`에 브라우저로 붙어 영상과 morph 웹 메시지 왕복을 확인하는 최소 페이지.
메시지 규칙은 [docs/WEB_MESSAGES.md](../../docs/WEB_MESSAGES.md).

## 실행
```bash
py -3.12 tools/kit.py launch morph.lab_streaming.kit   # --no-window --reset-user 자동 추가, 시그널링 49100
npm --prefix tools/webclient install                   # 처음 한 번 (.npmrc: @nvidia 패키지는 NVIDIA 저장소)
npm --prefix tools/webclient run dev                   # http://127.0.0.1:5173
```
- 다른 PC의 Kit에 붙으려면 `?server=<host>&port=<signalingPort>`.
- 버튼: 레이아웃 1x1/1x2/2x1/2x2/1+3, 포커스 0/1, orbit, zoom, **self test**.
- 1+3 오른쪽 폭: 슬라이더(0.10~0.50)로 `split.set_side_ratio` (드래그 중 초당 10회까지 최신 값만). 값은 state 푸시의 `side_ratio`로 맞춘다.
- 뷰 카메라: 뷰마다 드롭다운으로 카메라 지정 (`split.list_cameras` → `set_view_camera`). 목록은 연결 시·새로고침 버튼으로 다시 받는다 (푸시 없음).
- 프리셋: 이름을 넣고 저장(포커스 뷰 카메라 포즈), 목록에서 적용(0.5초 보간)·삭제 (`camera.control` save/list/apply/delete_preset).
- 영상 위 마우스 (Kit 기본 입력으로 넘기지 않고 morph 메시지로 보낸다):
  - 왼쪽 드래그 = orbit (0.25°/px), 오른쪽·가운데·Shift+왼쪽 드래그 = pan (target 거리 기준 픽셀→월드 환산), 휠 = zoom
  - 누른 뷰로 포커스 이동 (split state의 `rect`로 판정, 포커스 뷰는 파란 테두리)
  - 드래그는 `id: null` 알림으로 초당 30회까지 묶어 보낸다
- 성능 패널: Kit 프레임 시간(avg/p95/max, `morph.perf.monitor.report`), 상위 측정 구간, 브라우저가 받은 영상 fps, 요청 왕복 시간(`get_state` 1초마다), 입력 송신 수, 메모리(프로세스 작업 집합·전용, GPU 사용·예산 — perf 1.1 시스템 지표).
- 장면 패널: `morph.stage.loader` 목록(id)·열기·다시 불러오기·새 장면. 열기 회신은 열기가 끝난 뒤 오므로 시간 제한 60초. 열고 나면 카메라 목록을 다시 받는다
- 주석 패널: `morph.annotation.overlay` 목록(종류·이름, 설명은 마우스 올리면), 개별·전체 표시 체크, 설명 글 표시·글자 크기(`set_options`), 삭제, JSON 한 줄로 추가, 편집 폼(목록 이름을 누르면 선택·강조되어 폼에 채워짐. 종류·id·이름·설명·색·위치/흐름(단면은 중심/법선)·크기·위쪽 숫자 편집, [뷰에서 위치 찍기]를 켜고 영상의 모델 표면을 클릭하면 그 점과 표면 법선이 채워짐 — 입구는 법선 반대, 출구는 법선 방향을 흐름으로, Esc 취소. [면 선택]을 켜고 클릭하면 그 점이 놓인 평면 영역 전체를 면으로 잡아 중심·법선·면적·크기를 보이고 Kit 뷰에 외곽선·중심 좌표를 그린다. [면 중심을 폼에]는 입구·출구는 면 중심과 법선(입구는 반대) 흐름, 단면은 중심·법선·면 크기×1.2를 채운다),  사이드카 다시 읽기·저장(내장 샘플은 저장소 원본 JSON을 덮어쓰니 주의). 그림은 Kit 뷰포트 안에 그려져 영상으로 온다
- self test(52항목: 메시지 왕복, 장면 목록·샘플 열기 버튼·state 푸시·없는 id·다시 불러오기, 데모 장면 카메라 3개, 합성 마우스 이벤트로 클릭 포커스·드래그 orbit·휠 zoom·오른쪽 드래그 pan, 카메라 목록·드롭다운 지정, 프리셋 저장·적용·삭제, 1+3 오른쪽 폭 슬라이더·범위 자름·잘못된 값, 성능 리포트·시스템 메모리, 마지막에 배관 샘플을 열어 주석 자동 읽기·체크박스 숨김·JSON 추가·잘못된 값·수정·삭제 버튼·편집 폼(위치 찍기·적용·숫자 보정·삭제·면 선택·면 중심을 폼에·해제)·표시 옵션·다시 읽기) 결과(PASS/FAIL)는 화면 오른쪽과 `window.__morph.results`에 남는다. `window.__morphSave()`는 받은 영상 프레임과 결과를 `_out/frame.png`, `_out/selftest.json`으로 저장한다 (개발 서버 전용 `/__save`).

## 주의
- Kit livestream은 클라이언트를 **하나만** 받는다. 새로고침 직후에는 이전 연결이 정리될 때까지 몇 초 걸리고, 그동안 Kit 로그에 `NVST_R_BUSY`가 찍힌다.
- 스트리밍 앱(`morph.lab_streaming.kit`)은 분할 뷰포트만 보낸다 (편집 패널·메뉴·툴바·뷰포트 메뉴바·HUD·격자 없음). `--reset-user`는 이전 실행이 저장한 뷰포트별 설정(격자 표시 등)이 앱 구성을 덮어쓰지 않게 한다.
- 창 모드로 띄운 스트리밍 앱은 OS가 창을 1920x1061 등으로 줄이면 영상이 오지 않는다. `tools/kit.py launch`가 `--no-window`를 붙이는 이유.
- PC에 로그인 세션이 없으면(원격 데스크톱 연결을 끊은 상태 등) Kit 로그에 `NVST_DISCONN_SERVER_VIDEO_ADAPTER_INITIALIZE_D3D_FAILED`가 찍히고 영상이 오지 않는다. 원격 데스크톱이 연결돼 있거나 콘솔에 로그인돼 있으면 된다 (2026-11-04 RDP 연결 상태에서 확인).
- self test의 마우스 확인은 실제 클릭처럼 그 좌표의 맨 위 요소에 이벤트를 보낸다. 브라우저 창이 숨겨져 크기가 0이면 클릭 포커스 항목이 실패하니 창을 보이게 하고 돌린다.
- 라이브러리: `@nvidia/omniverse-webrtc-streaming-library` 5.18.2 (`AppStreamer`, `StreamType.DIRECT`).
