# 익스텐션 레지스트리

메인 세션만 수정한다. `tools/validate_extensions.py`가 이 표를 파싱하므로 열 순서를 유지한다.
의존 열은 morph 익스텐션만 쉼표로 적는다 (없으면 `-`).

| 익스텐션 ID | 담당 에이전트 | 상태 | 의존 | 한 줄 설명 |
|---|---|---|---|---|
| morph.stream.messaging | ext-stream | stable | - | 웹 클라이언트 ↔ Kit 메시지 통로, 타입 등록·라우팅 |
| morph.perf.monitor | ext-perf | stable | morph.stream.messaging | 공통 측정 API, 지표 수집·전송 |
| morph.camera.control | ext-camera | stable | morph.stream.messaging, morph.perf.monitor | 카메라 조작·프리셋·전환 |
| morph.viewport.split | ext-viewport | stable | morph.stream.messaging, morph.perf.monitor | 분할화면: 레이아웃 프리셋, 뷰별 카메라, 포커스 뷰 |
| morph.stage.loader | ext-stage | draft | morph.stream.messaging, morph.perf.monitor | USD 장면 목록·열기·다시 불러오기, 장면 열림 이벤트 |
| morph.annotation.overlay | ext-annotation | draft | morph.stream.messaging, morph.perf.monitor | 뷰포트 3D 주석: 라벨, inlet/outlet, 절단면 위치 (prim 계층과 무관, 사이드카 JSON) |

## 의존 순서 (위임 순서 참고)
1. `morph.stream.messaging` (기반)
2. `morph.perf.monitor`
3. `morph.camera.control`, `morph.viewport.split`, `morph.stage.loader`, `morph.annotation.overlay` (서로 의존 없음, 포커스 뷰는 이벤트로 연결. camera.control의 포커스 연동 작업은 viewport.split CONTRACT 확정 후)

같은 단계는 병렬 위임 가능. 다음 단계는 앞 단계의 CONTRACT가 확정된 뒤 시작한다.
