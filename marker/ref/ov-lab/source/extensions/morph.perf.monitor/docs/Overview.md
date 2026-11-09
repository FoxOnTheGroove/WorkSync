# Overview

`morph.perf.monitor`는 구간 시간(`scope`/`async_scope`), 카운터, 게이지, 프레임 타임을 모아
`periodSec`마다 count/avg/p95/max로 집계한다. `scope`는 `carb.profiler` 구간도 함께 남겨 Tracy 등에서 보인다.
1.1부터 프로세스 메모리(작업 집합/전용 바이트)와, 앱에 `omni.hydra.engine.stats`가 있으면 GPU 메모리를 리포트마다 gauge로 자동 수집한다 (`systemMetrics` 설정).
비활성 시 측정 API는 할당 없는 no-op이다. 공개 계약은 `CONTRACT.md` 참고.
