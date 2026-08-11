# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.1.0] - 2026-08-12

### Added

- Kit 익스텐션 골격 (`twin`) — `twin_viewer` / `twin_viewer_service` / `dummy_ui` 3계층
- `.twin` 로드 시 지오메트리를 회색 포인트 클라우드로 표시
- 입력값 지정 후 평가 → `UsdGeom.Points` + `primvars:displayColor` 로 필드 색 입힘
- 입력/출력 목록 표시. 출력에는 섭동으로 실측한 입력 기여도(`driven by`)를 함께 표기
- 재생 컨트롤(play/pause/stop) — USD 타임라인을 쓰지 않고 트윈 시각을 직접 진행
- Turbo 컬러맵(Ansys Discovery 스타일 가이드 기본값), 33 제어점 근사
- 컬러맵 범위 자동 결정 — 분포 꼬리가 두꺼울 때만 p99 로 클램프
- 스테이지 `metersPerUnit` 에 맞춘 좌표 단위 환산
- 포인트 크기 자동 산출 (bbox 대각선 기준)
- pywin32 부트스트랩 — Kit pipapi 경로에서 `.pth` 가 처리되지 않는 문제 우회

### Notes

- 재생 기능은 **미검증**이다. 개발에 쓴 트윈이 정적 ROM이라 확인할 수 없었다.
- 데이터 추출 경로는 pytwin 의 `get_tbrom_output_field()` 와 대조해 오차 0으로 확인했다.
- 배경과 근거는 `DESIGN.md` 참고.
