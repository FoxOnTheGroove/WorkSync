# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [0.1.3] - 2026-11-04
- 내장 샘플 `pipe_manifold.usda`: 삼각형 Mesh `/World/Base/MountPlate` (1.4 x 1.0 x 0.06, 면 22개 모두 삼각형, 윗면 2x2 격자 삼각형 8개, 부모 translate + rotateZ 15도, 어두운 금속색). `pick_face` 확인용
- 테스트 `test_samples.py`: MountPlate가 Mesh, 모든 면 삼각형, extent·크기, 닫힌 상자 부피(바깥 감기), 윗면 월드 중심·법선·넓이가 CONTRACT 값과 일치, 매니폴드·받침대·바닥과 겹치지 않음
- CONTRACT: 내장 샘플 절에 MountPlate 경로·윗면 중심 (0.293185, -1.048236, 0.065000)·법선 (0,0,1)·넓이 1.4·크기
- 주석 사이드카 `pipe_manifold.annotations.json`은 변경 없음

## [0.1.2] - 2026-11-04
- 내장 샘플 `data/scenes/pipe_manifold.usda`: 배관 매니폴드 (주관·입구/출구 B 플랜지·분기관+엘보→출구 A·밸브 V-101·받침대 2·바닥·조명, 깊은 계층, 카메라 CamIso/CamFront/CamTop, Persp cameraSettings)
- 주석 사이드카 `data/scenes/pipe_manifold.annotations.json` (morph.annotation.overlay 형식 version 1: inlet 1, outlet 2, plane 2, label 3)
- 테스트 `test_samples.py`: 목록 포함, 카메라 3개, JSON 형식·kind 구성·좌표가 매니폴드 bbox(여유 0.5)·플랜지 면과 일치, `.json`이 목록에 없음
- CONTRACT: 내장 샘플 절에 pipe_manifold 추가

## [0.1.1] - 2026-11-04
- 진행 중 열기 동안 온 `OPENED`: 열린 경로가 그 열기와 같은 파일일 때만 loader 완료로 보고, 다르면 external(scene_id는 목록 조회)
- 열기 완료 콜백이 `OPENED`보다 먼저 끝나 fallback으로 처리한 경우 뒤늦은 같은 `OPENED`를 한 번 무시 (`morph.stage.opened` 중복 방지)
- 웹: 지연 회신이 취소되면 `unavailable` 회신 후 다시 raise, shutdown 뒤 요청은 `unavailable`, `list_scenes {refresh:true}` 다시 스캔 1초 1회 제한
- `StageLoader` 내부 메서드 `_` 접두어 (`_reserve_*`, `_run`, `_state_payload`, `_add_state_listener`, `_destroy`)
- 샘플 `morph_demo.usda`: `customLayerData.cameraSettings` (Persp가 장면을 비춤)
- 테스트: 이벤트 1회 확인, 경로 변형 not_found, 숨김 파일·`=` 포함 경로·링크 순환, refresh 제한, shutdown/취소 회신, 진행 중 다른 파일 열림, 샘플 카메라
- CONTRACT: 링크·정션 주의, 위 동작 명시


## [0.1.0] - 2026-11-04
- 최초 초안 (골격, CONTRACT)
- 기본 구현: 장면 스캔·목록, open_scene/open_path/reload/new_scene/current, `morph.stage.opened`/`open_failed` 이벤트, 외부 열기 감지, startupScene, 웹 메시지(지연 회신, state 푸시), 내장 샘플 `data/scenes/morph_demo.usda`, 테스트
