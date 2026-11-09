# morph.perf.monitor CONTRACT

> 다른 익스텐션 에이전트가 이 익스텐션을 쓸 때 읽는 **유일한** 문서. 여기 없는 것은 내부 구현이므로 의존하지 않는다.

- 버전: 1.1.1
- 상태: stable
- 담당 에이전트: ext-perf
- 의존: morph.stream.messaging, omni.hydra.engine.stats (optional, GPU 지표용)

## 목적
모든 morph 익스텐션이 공통으로 쓰는 성능 측정 API와 지표 수집기. 프레임 타임, 구간 타이밍, 카운터, 게이지, 시스템 지표(프로세스·GPU 메모리 등, 아래 "시스템 지표")를 모아 주기적으로 집계(count/avg/p95/max)해 이벤트로 발행하고, 설정 시 웹으로 송신한다. 측정 자체가 성능을 해치지 않는 것이 최우선이다.

## 언제 쓰나 / 쓰지 않나
- 쓴다: 자기 코드 구간 시간을 재거나, 카운터/게이지 지표를 남길 때
- 쓰지 않는다: 자체 `time.perf_counter()` 타이머로 지표를 만드는 것 (금지, 집계·전송에서 빠짐)

## 이름 정리 (이벤트 vs 웹 type)
같은 리포트가 두 통로로 나가며 **이름이 다르다**. 섞어 쓰지 않는다.

| 통로 | 이름 | 규칙 | 상수 |
|---|---|---|---|
| carb 이벤트 (익스텐션끼리) | `morph.perf.report` | 이벤트 규칙 `morph.<domain>.<event>` | `EVENT_REPORT` |
| 웹 메시지 Kit→웹 | `morph.perf.monitor.report` | stream type 규칙 `<owner ext-id>.<name>` | `MSG_REPORT` |
| 웹 메시지 웹→Kit | `morph.perf.monitor.set_enabled` | 위와 같음 | `MSG_SET_ENABLED` |

## 공개 Python API
import: `from morph.perf.monitor import scope, async_scope, counter, gauge, get_monitor`
시스템 지표 이름 (1.1): `from morph.perf.monitor import SYS_MEM_WORKING_SET_MB, SYS_MEM_PRIVATE_MB, SYS_COLLECT_MS, GPU_MEM_USED_MB, GPU_MEM_BUDGET_MB`

| 심볼 | 시그니처 | 설명 | 주의 |
|---|---|---|---|
| `scope` | `(name: str) -> ContextManager` | **동기** 구간 시간 측정 (ms). `carb.profiler` 구간도 함께 기록 (Tracy 등에서 보임) | 비활성·미기동 시 공유 no-op 객체 반환(할당 없음). `await`를 넘기지 않는다 → `async_scope` |
| `async_scope` | `(name: str) -> ContextManager` (`with` / `async with` 둘 다) | `await`를 넘어 여러 프레임에 걸치는 구간 시간 측정 (ms) | `carb.profiler` 구간은 **남기지 않는다**. 같은 이름을 `scope`와 섞어 써도 된다 (같은 scope 지표로 집계) |
| `counter` | `(name: str, inc: int \| float = 1) -> None` | 누적 카운터 | 주기마다 0으로 리셋되어 발행 (이름은 계속 리포트에 남음). `bool`·숫자 아닌 값·NaN/inf는 경고 1회 후 무시 (이름도 등록하지 않음) |
| `gauge` | `(name: str, value: int \| float) -> None` | 현재 값 기록 | **마지막 값을 유지**해 이후 매 주기 리포트에 포함 (한 번만 기록해도 계속 나옴). `bool`·숫자 아닌 값·NaN/inf는 경고 1회 후 무시 (이름도 등록하지 않음). 플래그는 `gauge(name, 1.0 if flag else 0.0)` |
| `get_monitor` | `() -> Monitor \| None` | 싱글턴 | 이 익스텐션 startup 이후 유효, shutdown 이후 `None`. 캐시하지 말고 필요할 때 다시 얻는다 |
| `Monitor.snapshot` | `() -> dict[str, MetricStat]` | 최근 완료된 주기 집계 (프레임 타임 제외) | 사본 반환 |
| `Monitor.frame_stat` | `() -> MetricStat` | 최근 완료된 주기의 프레임 타임 (ms) | |
| `Monitor.last_report` | `() -> dict \| None` | 최근 리포트 페이로드의 깊은 사본 (아래 "리포트 페이로드", JSON 직렬화 가능) | 아직 주기가 한 번도 안 끝났으면 `None` |
| `Monitor.enabled` | 프로퍼티 `-> bool` | 측정 활성 여부 | |
| `Monitor.set_enabled` | `(enabled: bool) -> None` | 측정 on/off. `enabled` 설정 값도 함께 바꿈 | 메인 스레드에서 호출 |
| `Monitor.flush` | `() -> dict[str, MetricStat]` | 주기를 기다리지 않고 지금 집계·발행(이벤트+웹), 새 snapshot 반환. 주기가 새로 시작됨 | 테스트·디버그용. 메인 스레드에서 호출. **비활성 중에는 아무것도 하지 않고**(집계·이벤트·웹 송신 없음) 최근 snapshot 사본만 반환 |
| `Monitor.reset` | `() -> None` | 모든 지표 이름·누적값·gauge 값·최근 집계 삭제 | 테스트용. 다른 익스텐션 지표도 지워진다 |
| `MetricStat` | frozen dataclass: `count: int, avg: float, p95: float, max: float, kind: str, value: float`, `.to_dict() -> dict` | 한 주기의 지표 집계 | 모든 float는 유한값 (NaN/inf 없음) |
| `EVENT_REPORT` | `str` 상수 `"morph.perf.report"` | 발행 carb 이벤트 이름 | 문자열을 직접 써도 된다 |
| `MSG_REPORT` | `str` 상수 `"morph.perf.monitor.report"` | Kit→웹 리포트 type | 웹 클라이언트 구현 참고용. 다른 익스텐션이 이 type으로 `send`하지 않는다 |
| `MSG_SET_ENABLED` | `str` 상수 `"morph.perf.monitor.set_enabled"` | 웹→Kit on/off 요청 type | 위와 같음 |
| `SYS_MEM_WORKING_SET_MB`, `SYS_MEM_PRIVATE_MB`, `SYS_COLLECT_MS`, `GPU_MEM_USED_MB`, `GPU_MEM_BUDGET_MB` | `str` 상수 (1.1) | 아래 "시스템 지표" 표의 지표 이름 | 리포트 `metrics`에서 찾을 때 쓴다. 다른 익스텐션이 이 이름으로 `gauge`를 기록하지 않는다 |

### MetricStat 의미 (`kind`별)
| kind | count | avg / p95 / max | value | 주기 내 샘플 0개일 때 |
|---|---|---|---|---|
| `scope` | 구간 수 | 구간 시간 ms (p95는 nearest-rank) | 주기 내 총 시간 ms | 모두 0 |
| `counter` | 호출 수 | 모두 `value`와 같음 | 주기 내 `inc` 합 | 모두 0 |
| `gauge` | 주기 내 기록 수 | 주기 내 기록 값들의 통계 | 마지막 값 | count 0, avg/p95/max/value = 마지막 값 |
| `frame` | 프레임 수 | 프레임 간격 ms | 총 ms | 모두 0 (`frame_ms`에만 쓰임) |

- `to_dict()` 형식: `{ "kind": str, "count": int, "avg": float, "p95": float, "max": float, "value": float }`
- **p95와 `maxSamples`**: count/avg/max/value는 주기 내 모든 기록을 정확히 반영한다. p95만 지표당 주기마다 최대 `maxSamples`개(기본 4096)의 표본으로 계산한다. 기록 수가 이를 넘으면 **저장소 샘플링**으로 주기 전체에서 균등한 무작위 표본을 유지하므로 주기 앞쪽에 치우치지 않지만, 그 경우 p95는 근사값이다 (같은 입력이어도 실행마다 조금 다를 수 있음). 기록 수가 `maxSamples` 이하면 정확한 값이다.

### 스레드 규칙
- `scope` / `async_scope` / `counter` / `gauge`는 **워커 스레드에서 호출해도 된다** (내부 락 하나, 기록만 함). `carb.profiler` 구간은 호출한 스레드에 기록된다.
- `scope`는 시작한 스레드에서 끝나야 한다 (다른 스레드에서 끝나면 시간은 기록되나 profiler 구간이 닫히지 않음, 경고 1회).
- 집계·이벤트 발행·웹 송신은 메인 업데이트 루프에서만 일어난다. `Monitor`의 메서드(`set_enabled`, `flush`, `reset`)는 메인 스레드에서 호출한다.

### asyncio / 여러 프레임에 걸친 구간
- `scope`는 **동기 구간 전용**이다. `with scope(...)` 안에서 `await`하지 않는다.
- `await`를 넘기면 시간(ms)은 정확히 기록되지만, 그 사이 다른 코루틴의 `scope`와 교차할 수 있어 `carb.profiler` 구간 경계가 틀어진다 (구간 수는 맞춰서 닫는다). 교차가 감지되면 경고 1회.
- `await`를 넘는 구간은 `async_scope`를 쓴다: 시간만 기록하고 profiler 구간은 남기지 않는다. 필요하면 안쪽 동기 부분만 `scope`로 감싼다.

지표 이름 규칙: `<ext-id>.<metric>` (예: `morph.camera.control.interp_ms`). 규칙과 다르면 이름별 경고 1회 (기록은 됨). 한 지표 이름은 한 종류(scope/counter/gauge)로만 쓴다 — 다른 종류로 기록하면 경고 1회 후 무시.

## 리포트 페이로드
이벤트·웹 메시지·`last_report()`가 모두 **같은 한 가지 형태**다:
```json
{ "period_s": 1.0,
  "frame_ms": { "kind": "frame", "count": 60, "avg": 16.6, "p95": 17.1, "max": 20.3, "value": 1000.0 },
  "metrics": { "morph.camera.control.update_ms": { "kind": "scope", "count": 60, "avg": 0.2, "p95": 0.3, "max": 0.9, "value": 12.0 } } }
```
- `metrics`에는 지금까지 기록된 모든 이름이 들어간다 (그 주기에 기록이 없어도 0 또는 gauge 마지막 값).
- 모든 값은 JSON 안전 (float는 유한값, 키는 str).
- 1.1부터 `systemMetrics`가 켜져 있으면(기본) 아래 시스템 지표가 `metrics`에 `kind: "gauge"`로 함께 들어간다. 페이로드 형태는 그대로다.

## 시스템 지표 (1.1, 자동 수집)
이 익스텐션이 직접 읽어 `gauge`로 기록하는 지표. 다른 익스텐션은 아무것도 하지 않아도 리포트에 나온다.

| 이름 | 단위 | 원천 | 나오는 조건 |
|---|---|---|---|
| `morph.perf.monitor.sys.mem_working_set_mb` | MB (MiB, 2^20 바이트) | 프로세스 작업 집합 / RSS. psutil `memory_info().rss` → (없으면) Windows `K32GetProcessMemoryInfo().WorkingSetSize` → Linux `/proc/self/statm` resident | `systemMetrics` true |
| `morph.perf.monitor.sys.mem_private_mb` | MB | 프로세스 전용 바이트. Windows `PrivateUsage`(psutil `private`), Linux는 근사값 RSS − shared | `systemMetrics` true |
| `morph.perf.monitor.sys.collect_ms` | ms | 이번 시스템 지표 수집에 걸린 시간 (측정 비용 자체) | `systemMetrics` true |
| `morph.perf.monitor.gpu.mem_used_mb` | MB | `omni.hydra.engine.stats.get_device_info()`의 `usage` 합 (모든 GPU) | 앱에 `omni.hydra.engine.stats`가 있을 때 |
| `morph.perf.monitor.gpu.mem_budget_mb` | MB | 같은 함수의 `budget` 합 (드라이버가 주는 사용 가능 예산) | 위와 같음 |

- 수집 시점: 활성 상태의 리포트(주기·`flush`) 직전, 메인 스레드에서. 단 **최소 0.5초 간격** — `periodSec`가 더 짧으면 그 사이 리포트는 `count` 0에 마지막 값(gauge 규칙)이다. 측정 API 핫패스에는 비용이 없다.
- `count`가 1이면 이번 주기에 읽은 값, 0이면 이전 값이다.
- 비활성(`enabled` false)이면 수집하지 않는다. `systemMetrics`를 런타임에 끄면 다음 리포트부터 수집하지 않지만, 이미 기록된 이름은 gauge 규칙대로 마지막 값으로 남는다 (`count` 0).
- 원천이 앱에 없으면(예: `omni.hydra.engine.stats` 미포함) 그 지표는 **아예 나오지 않는다** (info 로그 1회, 경고 아님). 이 익스텐션은 `omni.hydra.engine.stats`를 optional 의존으로만 선언하므로 앱 `.kit`이 넣어야 GPU 지표가 생긴다.
- 읽기가 예외로 실패하면 경고 1회 후 그 원천의 지표만 그 주기에 건너뛴다 (다음 주기에 다시 시도, 경고는 반복하지 않음). 유한한 숫자가 아닌 값도 건너뛴다.
- 이 지표들도 지표 이름 수 상한(`maxMetrics`)에 포함된다 (최대 5개).
- **GPU 프레임 시간은 없다.** Kit 110.1.3에서 공개 API로 얻지 못함: `omni.hydra.engine.stats.HydraEngineStats().get_gpu_profiler_result()`가 GPU 프로파일러 off면 장치 0개, `/rendergraph/enableGPUProfiler`·`/renderer/gpuProfiler/record`를 켜도 장치 1개·노드 0개였다 (렌더러 준비 후 900프레임, `usd_context_name`/`hydra_engine_name` 조합 모두, 2026-11-01 실측). CPU 프레임 간격은 `frame_ms`를 쓴다.
- **스트리밍 지연은 없다.** Kit 110.1의 `omni.kit.livestream.*`에는 지연·RTT 통계 공개 API가 없다. 웹 클라이언트가 왕복 시간을 직접 잰다 (예: `morph.perf.monitor.set_enabled`처럼 회신이 오는 요청의 송신~회신 시간, 또는 브라우저 WebRTC `getStats()`).

## 이벤트
### 발행
| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `morph.perf.report` | 위 "리포트 페이로드" (중첩 dict) | 활성 상태에서 집계 주기(`periodSec`)마다, 그리고 활성 상태에서 `Monitor.flush()` 호출 시. 메인 업데이트 루프(또는 `flush`를 부른 메인 스레드)에서 발행. **비활성 중에는 어떤 경로로도 발행하지 않는다** |

- Kit 110 `carb.eventdispatcher`의 Python 바인딩은 페이로드 값으로 임의의 Python 객체를 받고(`carb.variant` Python 타입으로 보관), Python 관찰자에게 그 객체를 그대로 돌려준다. 그래서 관찰자 콜백에서 `e["frame_ms"]["avg"]`, `e["metrics"]` 처럼 **바로 dict로 읽는다**. (0.x에 있던 `{ "period_s", "json" }` 대체 형식은 없어졌다.)
- 이벤트 페이로드는 발행마다 새로 만든 사본이다. 관찰자가 고쳐도 `last_report()`·웹 송신에는 영향이 없지만, 같은 이벤트의 다른 관찰자는 같은 객체를 볼 수 있으니 **읽기 전용으로 취급**한다. 보관하려면 `get_monitor().last_report()`(깊은 사본)를 쓴다.
- 발행 자체가 실패하면(바인딩 오류 등) 경고 1회 후 그 주기 이벤트만 건너뛴다 (`last_report()`·웹 송신은 정상).

### 수신
| 이벤트 | 소유 익스텐션 | 반응 |
|---|---|---|

(이벤트 구독 없음. 웹 송신 여부는 송신 시점에 `Messaging.is_connected`로 판단한다.)

## 웹 메시지 (morph.stream.messaging 1.x 경유)
| 방향 | 메시지 타입 | 페이로드 | 매핑 |
|---|---|---|---|
| Kit→웹 | `morph.perf.monitor.report` (v1) | 위 "리포트 페이로드" (`id: null` 푸시) | 활성 상태의 주기·`flush`마다. `webReport`가 true이고 `is_connected`일 때만 |
| 웹→Kit | `morph.perf.monitor.set_enabled` (v1) | `{ "enabled": bool }` | 측정 on/off (`enabled` 설정도 바뀜). 같은 type·id로 회신 (아래) |

### set_enabled 회신
stream 회신 규칙을 따른다 (`id`가 문자열일 때만 같은 type·id로 정확히 한 번).

| 경우 | 회신 payload |
|---|---|
| 성공 | `{ "ok": true, "enabled": bool }` — 적용 후 상태 |
| `enabled`가 bool 아님 (stream `validate`가 꺼져 스키마로 못 거른 경우) | `{ "ok": false, "error": "invalid_argument", "message": str, "enabled": bool }` — `enabled`는 현재(바뀌지 않은) 상태 |

- stream 스키마 검증이 켜져 있으면(기본) `enabled` 누락·타입 오류는 핸들러 전에 `morph.stream.messaging.error`(`invalid_payload`)로 회신된다.

| error code | 의미 |
|---|---|
| `invalid_argument` | `enabled`가 bool이 아님 |

## 설정
| 경로 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `/exts/morph.perf.monitor/enabled` | bool | true | 측정 전체 on/off. 런타임 변경 즉시 반영 |
| `/exts/morph.perf.monitor/periodSec` | float | 1.0 | 집계 주기 (초, 최소 0.05). 런타임 변경 다음 프레임부터 반영 |
| `/exts/morph.perf.monitor/webReport` | bool | false | 웹으로 리포트 송신. 런타임 변경 다음 리포트부터 반영 |
| `/exts/morph.perf.monitor/maxMetrics` | int | 512 | 지표 이름 수 상한. 넘는 새 이름은 무시하고 경고 1회. startup 시 읽음 |
| `/exts/morph.perf.monitor/maxSamples` | int | 4096 | 주기당 지표별 p95용 표본 상한 (최소 1). 넘으면 저장소 샘플링. startup 시 읽음 |
| `/exts/morph.perf.monitor/systemMetrics` | bool | true | (1.1) 시스템 지표 자동 수집 on/off. 런타임 변경 다음 리포트부터 반영 |

## 사용 예
`config/extension.toml`:
```toml
[dependencies]
"morph.perf.monitor" = { version = "1.0" }  # SYS_*/GPU_* 상수·systemMetrics를 쓰면 "1.1"
```

Python (측정):
```python
import asyncio

from morph.perf.monitor import scope, async_scope, counter, gauge

# 이름은 모듈 상수로 둔다 (동적 생성 금지)
M_UPDATE = "morph.camera.control.update_ms"
M_MOVE = "morph.camera.control.move_ms"
M_CMDS = "morph.camera.control.commands"
M_QUEUE = "morph.camera.control.queue_len"


def update_camera(dt):
    with scope(M_UPDATE):          # 동기 구간: 시간 + carb.profiler 구간
        ...
    counter(M_CMDS)                # 주기마다 리셋
    gauge(M_QUEUE, 3)              # 마지막 값이 매 주기 리포트에 유지


async def move_camera_async():
    async with async_scope(M_MOVE):  # await를 넘는 구간: 시간만
        await asyncio.sleep(0.1)
```

리포트 구독 (carb.eventdispatcher). `EXT_ID`를 자기 익스텐션 ID로 바꾸면 그대로 동작한다:
```python
import omni.ext
from carb.eventdispatcher import get_eventdispatcher
from morph.perf.monitor import EVENT_REPORT

EXT_ID = "morph.foo.bar"


class MyExt(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._perf_sub = get_eventdispatcher().observe_event(
            observer_name=f"{EXT_ID}:perf_report",
            event_name=EVENT_REPORT,
            on_event=self._on_report,
        )

    def _on_report(self, e):
        # 페이로드는 읽기 전용으로 쓴다. 보관하려면 get_monitor().last_report() 사본을 쓴다.
        frame = e["frame_ms"]                                   # dict
        mine = e["metrics"].get("morph.camera.control.update_ms")  # dict 또는 None
        print(e["period_s"], frame["avg"], mine and mine["p95"])

    def on_shutdown(self):
        self._perf_sub = None  # 구독 해제
```

웹 클라이언트 (morph.stream.messaging CONTRACT의 `request`/`onCustomEvent` 헬퍼 사용):
```js
// Kit→웹 푸시 (onCustomEvent 안에서)
if (env.type === "morph.perf.monitor.report") {
  // env.payload = { period_s, frame_ms: {kind,count,avg,p95,max,value}, metrics: { name: {...} } }
  console.log("frame avg ms", env.payload.frame_ms.avg);
}

// 웹→Kit 요청: 측정 끄기
try {
  const r = await request("morph.perf.monitor.set_enabled", { enabled: false });   // → { ok: true, enabled: false }
} catch (e) {
  if (e.code === "invalid_argument") console.warn(e.message);
}
```

시스템 지표 읽기 (Python, 리포트 구독 콜백 안에서):
```python
from morph.perf.monitor import GPU_MEM_USED_MB, SYS_MEM_WORKING_SET_MB


def on_report(e):
    ws = e["metrics"].get(SYS_MEM_WORKING_SET_MB)   # dict 또는 None (systemMetrics off)
    gpu = e["metrics"].get(GPU_MEM_USED_MB)         # None이면 앱에 omni.hydra.engine.stats 없음
    print(ws and ws["value"], gpu and gpu["value"])  # MB
```

웹 (`morph.perf.monitor.report` 푸시 안에서):
```js
const m = env.payload.metrics;
const ws = m["morph.perf.monitor.sys.mem_working_set_mb"]?.value;  // MB
const gpu = m["morph.perf.monitor.gpu.mem_used_mb"]?.value;        // MB, 없을 수 있음
```

## 금지 / 주의
- 프레임당 수천 번 호출되는 루프 안쪽에 `scope`를 두지 않는다. 루프 바깥을 감싼다.
- 지표 이름을 동적으로 만들지 않는다 (f-string 등 금지, 이름 폭증 방지). 이름은 상수로 둔다. 상한(`maxMetrics`)을 넘는 새 이름은 무시된다.
- `with scope(...)` 안에서 `await`하지 않는다 (위 "asyncio" 참고). `async_scope`를 쓴다.
- 비활성 상태에서 `scope`가 돌려주는 객체는 공유 no-op이다. 반환값에 속성을 붙이거나 의존하지 않는다.
- `Monitor.reset()`은 모든 익스텐션의 지표를 지운다. 테스트 외에는 쓰지 않는다.
- 웹 type(`morph.perf.monitor.*`)과 carb 이벤트 이름(`morph.perf.report`)을 혼동하지 않는다 (위 "이름 정리").
- **웹 송신의 연결 감지 한계**: 웹 송신 여부는 morph.stream.messaging의 `is_connected`로 판단한다. 기본 설정에서 그 값은 `morph.stream.messaging.hello`/`morph.stream.messaging.bye`로만 갱신되므로, 브라우저가 `bye` 없이 닫히면 `is_connected`가 `True`로 남아 **클라이언트가 없어도 리포트를 계속 큐에 넣는다** (stream `maxQueue`에서 오래된 것부터 버려짐). 반대로 웹 클라이언트가 `hello`를 보내지 않으면 `webReport`가 true여도 송신하지 않는다. 웹 클라이언트는 연결 직후 `morph.stream.messaging.hello`, 종료 직전 `morph.stream.messaging.bye`를 보낸다.
- 측정 on/off(`enabled`) 전환 시 진행 중이던 주기의 누적값은 버린다 (gauge 마지막 값은 유지).

## 호환성 정책 (stable)
- morph.stream.messaging 1.x의 type·회신 규칙을 전제로 한다.
- 모든 morph 익스텐션이 이 측정 API에 기댄다. 1.x 동안 `scope`/`async_scope`/`counter`/`gauge`/`get_monitor`와 `Monitor` 공개 메서드의 시그니처·의미, `MetricStat` 필드, 리포트 페이로드 형태(`period_s`/`frame_ms`/`metrics`와 `to_dict()` 키), 이벤트 이름 `morph.perf.report`, 웹 type 이름과 회신 형식, 설정 경로·기본값 의미는 깨지 않는다.
- minor: API·설정·`MetricStat`/페이로드 필드 추가, `kind` 값 추가, 회신 error code 추가 (수신 측은 모르는 필드·kind·code를 무시하거나 일반 실패로 처리한다). patch: 내부 수정 (p95 표본 방식 등 근사 방법 개선 포함).
- 웹 메시지 페이로드를 호환 깨지게 바꾸면 해당 type의 `v`를 올리고 major로 한다.
- major 변경은 메인 세션에 먼저 제안한 뒤에만 한다.

## 변경 이력
- 1.1.1 — 내부 수정 (공개 동작 변화 없음). `extension.toml` 의존에 버전 범위 명시: `morph.stream.messaging` `{ version = "1.0" }` (^1.0, type 규칙·`reply_ok`/`reply_error`가 1.0에서 도입). 사용 예 toml에 버전 범위 추가 (`"1.0"`, 시스템 지표 상수를 쓰면 `"1.1"`).
- 1.1.0 — 기능 추가 (호환 유지). 시스템 지표 자동 수집: `sys.mem_working_set_mb`, `sys.mem_private_mb`, `sys.collect_ms`, `gpu.mem_used_mb`, `gpu.mem_budget_mb`(앱에 `omni.hydra.engine.stats`가 있을 때). 기존 `gauge` 경로로 `metrics`에 들어가며 페이로드 형태는 그대로. 설정 `systemMetrics`(기본 true), 이름 상수 `SYS_*`/`GPU_*` 공개. `omni.hydra.engine.stats` optional 의존 추가. 스트리밍 지연·GPU 프레임 시간은 공개 API로 얻을 수 없어 수집하지 않음을 명시. 주의: 기본 설정에서 리포트 `metrics`에 이 익스텐션 소유 이름이 최대 5개 늘어난다.
- 1.0.0 — **호환 깨짐 (stable 확정)**. morph.stream.messaging 1.0 대응.
  - 웹 type 이름 변경: `morph.perf.report` → `morph.perf.monitor.report`, `morph.perf.set_enabled` → `morph.perf.monitor.set_enabled`. carb 이벤트 이름 `morph.perf.report`는 그대로 (웹 type과 다름을 명시).
  - `set_enabled` 회신: `{ enabled }` → `{ ok: true, enabled }`. `enabled`가 bool이 아니면 `{ ok: false, error: "invalid_argument", message, enabled }` (0.x는 `bool()` 변환해 적용했음).
  - `morph.perf.report` 이벤트 페이로드를 중첩 형태 하나로 확정. 0.x의 `{ period_s, json }` 대체 형식 제거. 이벤트 페이로드는 발행마다 사본.
  - `Monitor.flush()`는 비활성 중에 집계·이벤트·웹 송신을 하지 않는다 (0.x는 비활성이어도 발행했음 — 이벤트 표의 "비활성 중 발행 안 함"과 통일).
  - `counter`/`gauge`가 `bool` 값을 받지 않는다 (경고 1회 후 무시, 이름 미등록).
  - (호환) p95 표본이 `maxSamples`를 넘으면 주기 앞쪽 샘플만 쓰던 것을 저장소 샘플링으로 변경·명시. `maxSamples` 최소 1. `MSG_REPORT`/`MSG_SET_ENABLED` 상수 공개. 호환성 정책 추가. 사용 예 갱신(이벤트 페이로드 직접 읽기, stream 1.0 웹 헬퍼).
- 0.2.1 — 내부 수정: NaN/inf·숫자 아닌 값으로 무시된 `gauge`/`counter` 호출이 이름을 0값 지표로 등록(이름 상한 소모)하던 버그 수정. 무시된 기록은 이름을 남기지 않는다.
- 0.2.0 — 기본 구현. (기능 추가, 0.1.0의 `scope`/`counter`/`gauge`/`get_monitor`/`Monitor.snapshot` 시그니처·의미 유지) `async_scope` 추가, `scope`는 동기 구간 전용 명시, 스레드 규칙 명시, gauge 마지막 값 유지 명시, `MetricStat` 필드(`kind`, `value`, `to_dict`) 확정, `Monitor.frame_stat`/`last_report`/`enabled`/`set_enabled`/`flush`/`reset`, `EVENT_REPORT` 상수, 설정 `maxMetrics`/`maxSamples`, `set_enabled` 회신, 수신 이벤트(`client_connected`/`client_disconnected`) 구독 대신 `is_connected` 사용, 이벤트 페이로드 대체 형식(`json`).
- 0.1.0 — 최초 초안
