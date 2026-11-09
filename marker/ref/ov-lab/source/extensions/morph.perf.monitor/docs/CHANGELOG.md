# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [1.1.1] - 2026-11-04
- 의존 버전 범위 명시: `morph.stream.messaging` `{ version = "1.0" }` (^1.0). 동작 변화 없음

## [1.1.0] - 2026-11-01
- 시스템 지표 자동 수집 (gauge로 `metrics`에 포함, 페이로드 형태 유지)
  - `morph.perf.monitor.sys.mem_working_set_mb`, `sys.mem_private_mb` (psutil → Windows ctypes → Linux statm)
  - `morph.perf.monitor.sys.collect_ms` (수집 비용)
  - `morph.perf.monitor.gpu.mem_used_mb`, `gpu.mem_budget_mb` (`omni.hydra.engine.stats.get_device_info`, optional 의존)
- 설정 `systemMetrics`(기본 true). 리포트 직전 최소 0.5초 간격 수집
- 이름 상수 `SYS_*`/`GPU_*` 공개
- 스트리밍 지연은 livestream 공개 API가 없어 수집하지 않음 (웹 클라이언트가 직접 측정)
- GPU 프레임 시간은 `HydraEngineStats.get_gpu_profiler_result()`가 프로파일러를 켜도 노드 0개라 수집하지 않음

## [1.0.0] - 2026-10-31
- stable 확정 (호환 깨짐). morph.stream.messaging 1.0 대응
- 웹 type: `morph.perf.monitor.report`, `morph.perf.monitor.set_enabled` (carb 이벤트 `morph.perf.report`는 유지)
- set_enabled 회신 `{ ok: true, enabled }`, bool 아닌 값은 `invalid_argument`
- 이벤트 페이로드 중첩 형태 하나로 확정 (`json` 대체 형식 제거), 발행마다 사본
- 비활성 중 `flush()`는 아무것도 발행하지 않음
- counter/gauge의 bool 값 무시
- p95 표본 저장소 샘플링, `MSG_REPORT`/`MSG_SET_ENABLED` 공개, 호환성 정책

## [0.2.1] - 2026-10-30
- NaN/inf로 무시된 gauge/counter 호출이 이름을 등록하던 버그 수정

## [0.2.0] - 2026-10-30
- 기본 구현: scope/async_scope/counter/gauge, 프레임 타임 수집, 주기 집계(count/avg/p95/max), `morph.perf.report` 이벤트, 웹 리포트 송신, `morph.perf.set_enabled` 수신, 설정 enabled/periodSec/webReport/maxMetrics/maxSamples

## [0.1.0] - 2026-10-30
- Initial version of basic python extension template
