# 설계 노트 / 인수인계

이 문서는 다음 세션에서 맥락 없이 이어받을 수 있도록, **왜 이렇게 만들었는지**와
**무엇이 검증됐고 무엇이 안 됐는지**를 기록한다. 사용법은 `README.md`, 데이터 흐름
요약은 `Overview.md` 를 참고.

작성 시점: 2026-08-12

---

## 1. 현재 상태 한 줄 요약

`.twin` 을 로드해 TBROM 필드를 USD 포인트 클라우드로 그리는 것까지 **동작 확인 완료**.
재생(play/pause/stop)은 **구현했으나 미검증** — 대상 파일이 정적 트윈이라 검증 불가.

---

## 2. 대상 파일에 대해 실측한 사실

`C:\Users\OPTI\Documents\HXVelVectorTBROM_23R2.twin` (95.5 MB).
Ansys 가 pytwin 예제용으로 배포하는 열교환기 데모 파일이다.

| 항목 | 값 |
|---|---|
| TBROM | `test1` 1개 |
| 출력 필드 | `Velocity` (**벡터**) |
| 포인트 수 | 873,433 |
| bbox | `(-0.62, -0.94, 0.002) ~ (0.62, 0.94, 6.25)` → **미터 단위** |
| 모드 수 | **4개** (`_outbasis` shape = `(4, 873433, 3)`) |
| 입력 | `Mass_Flow_HX`, `Tube_temperature`, `shell_inlet_temp` (기본값 전부 0.0) |
| 출력 | 12개 = `outField_mode_1~4` + `Point0~6` + `Operation0` |
| named selections | `inlet`(41점), `outlet`(44점), `shell`(873,348점) |
| 기본 시뮬레이션 설정 | end 0.04s, step 0.001s, tol 0.0001 |
| Parametric Field History | **False** |
| 라이선스 | 불필요 (unlicensed export — `TwinModel()` 생성이 그냥 통과) |

### 2.1 이 트윈은 정적이다

입력을 고정하고 기본 해상도(0.001초 × 40스텝)로 돌려도 모드 계수가 **소수점까지 불변**,
필드 변화량 `max|Δ| = 0`. `get_tbrom_time_grid()` 는
`"not a parametric field history ROM"` 으로 실패한다.

정상상태 CFD 해로 학습된 ROM이라 **평형 상태만 안다**. 시간축을 붙여도 답할 내용이 없다.
따라서 이 파일로는 재생 기능을 검증할 수 없다.

### 2.2 출력 이름의 의미 (값으로 역추적)

`Unit`/`Description` 메타데이터가 전부 `TWIN_VARPROP_NOTDEFINED` 라 파일만 봐서는
알 수 없었다. 필드 값과 대조해 12자리 유효숫자까지 일치하는 것으로 확정했다.

- `Operation0` = 도메인 전체 평균 `|v|`
- `Point0`~`Point6` = 특정 프로브 지점 7곳의 `|v|` (전부 z=3.3~5.7, 출구 쪽)
- `outField_mode_1~4` = TBROM 모드 계수. **예약된 이름 규칙**이다

### 2.3 표현력의 한계 — 중요

**모드가 4개뿐이다.** 873,433점 × 3성분 = 262만 자유도의 속도장이 실제로는 4차원
공간 안에서만 움직인다. 입력을 아무리 흔들어도 필드가 변할 수 있는 방향이 4가지다.

민감도 실측 결과(입력을 각각 5% 섭동):

| 출력 | 지배 입력 | 2순위 | 3순위 |
|---|---|---|---|
| `outField_mode_1` | Mass_Flow_HX 1.00 | Tube_temperature 0.014 | shell_inlet_temp 0.004 |
| `outField_mode_2` | shell_inlet_temp 1.00 | Tube_temperature 0.33 | Mass_Flow_HX 0.24 |
| `outField_mode_3` | shell_inlet_temp 1.00 | Mass_Flow_HX 0.43 | Tube_temperature 0.08 |
| `outField_mode_4` | Tube_temperature 1.00 | shell_inlet_temp 0.45 | Mass_Flow_HX 0.027 |

`mode_1` 이 크기가 압도적(218.9 vs 나머지 4~17)이고 사실상 유량 전용이다. 그래서
**유량만 바꾸면 "모양은 그대로, 전체적으로 세지기만" 한다.** 모양을 바꾸려면
`Tube_temperature` / `shell_inlet_temp` 를 흔들어 mode 2~4 를 움직여야 한다.

"변화가 미세하다"는 인상의 근본 원인이 이것이다. 코드 문제가 아니다.

### 2.4 유량-속도가 비례가 아닌 이유

유량을 2배로 해도 속도는 1.65~1.73배다. 버그가 아니라 **아핀 관계**다.

```
Mass_Flow=0   → mode_1 = 87.87,  inlet 평균 |v| = 0.517   ← 유량 0인데 유동이 있다
Mass_Flow=75  → mode_1 = 218.93
Mass_Flow=150 → mode_1 = 367.81
```

유량과 무관한 성분이 있다(튜브 1115K vs 쉘 300K 온도차 → 부력으로 해석 가능).
상수항이 있으니 2배 입력이 2배 출력이 되지 않는다.

**외삽 주의**: `mode_4` 가 유량 0→300 구간에서 5.08 → 335 로 66배 뛴다. 다른 모드보다
증가 양상이 훨씬 급하다. 학습 범위는 파일에 안 적혀 있으나 예제가 쓰는 **75 근처가
안전**해 보인다. 300 같은 값의 결과는 신뢰하기 어렵다.

---

## 3. 아키텍처

```
dummy_ui  →  twin_viewer_service  →  twin_viewer
 (omni.ui)      (외부 API 표면)        (pytwin + USD)
```

- **`twin_viewer.py`** — 구현부. `TwinViewer` classmethod 싱글톤. 형제 익스텐션
  (`parts_manager`, `axis_controller`)과 같은 패턴.
- **`twin_viewer_service.py`** — 외부 API. `__all__` 로 표면을 고정한 얇은 위임 함수들.
  구현부 시그니처가 바뀌어도 여기서 흡수한다.
- **`dummy_ui.py`** — **`TwinViewer` 를 직접 import 하지 않는다.** service 만 쓴다.
  UI가 service API만으로 동작한다는 사실이 곧 그 API 표면이 충분한지에 대한 검증이다.

정합성은 정적으로 검사할 수 있다 (`__all__` ↔ 정의, service ↔ TwinViewer, UI ↔ service).
9절에 스크립트가 있다.

---

## 4. 환경 구성 — 가장 많이 막힌 부분

순서대로 네 번 막혔다. 전부 해결됐지만 재현될 수 있으니 기록해 둔다.

### 4.1 익스텐션 검색 경로

Kit 은 **검색 경로의 자식 폴더 하나하나를 익스텐션으로** 본다.

```
검색 경로 = D:\Public\Work\WorkSync        ← 이게 맞다 (twin 폴더의 부모)
검색 경로 = D:\Public\Work\WorkSync\twin   ← 틀리다
```

후자로 주면 자식인 `config/` 에 `extension.toml` 이 직접 들어있으니 Kit 이 그걸
익스텐션 루트로 오인한다. 실제로 `[ext: config-0.1.0]` 으로 뜨고
`No module named 'morph'` 로 실패했다.

`WorkSync` 의 다른 폴더들(`overlay_panel` 등)은 `config/extension.toml` 이 없어
Kit 이 무시하므로 부작용은 없다.

### 4.2 pipapi 의 import 체크

`[python.pipapi] requirements = ["pytwin[graphics]"]` 로 설치는 되는데, pipapi 가
설치 후 **requirement 문자열을 그대로 모듈명으로 import** 해 검증한다.
extras 표기 때문에 `No module named 'pytwin[graphics]'` 로 헛발질한다.

→ `ignore_import_check = true` 로 끈다. 설치 자체는 정상이었다.

### 4.3 `[graphics]` extra 는 필수다

pytwin 코어 의존성만 보면 numpy/pandas/tqdm/pywin32 로 가벼워 보이지만,
**TBROM 이 포함된 `.twin` 은 `TwinModel` 인스턴스화 시점에 graphics 를 요구**한다.

```
Twin model failed during instantiation.
Graphics are required for this method.
```

`generate_points`/`generate_snapshot` 이 numpy 를 반환한다고 pyvista 가 불필요할
것으로 판단했다가 틀렸다. 반환 타입이 아니라 인스턴스화 경로가 요구한다.

### 4.4 pywin32 부트스트랩

pywin32 는 `pywin32.pth` 로 `win32`, `win32/lib`, `pythonwin` 을 `sys.path` 에 올리고
`pywin32_system32` 의 DLL 을 찾게 만든다. 그런데 **Kit 의 pipapi 설치 경로는
`site.addsitedir()` 없이 `sys.path` 에 append 되기만 해서 `.pth` 가 처리되지 않는다.**
결과: `No module named 'win32api'`.

`twin_viewer._bootstrap_pywin32()` 가 우회한다:

- `win32api` 가 이미 잡히면 즉시 반환 (정상 환경에선 아무것도 안 함)
- `sys.path` 를 훑어 `pywin32_system32` 를 가진 항목을 찾는다 → **경로 하드코딩 없음**
- `os.add_dll_directory()` 로 DLL 등록, 세 서브디렉터리를 `sys.path` 에 추가
- `importlib.invalidate_caches()` — 이미 실패한 import 의 캐시를 무효화해야 재시도가 먹는다

pipapi 설치 위치(참고): `%LOCALAPPDATA%\ov\data\Kit\opt.edit\0.1\pip3-envs\default-3.12`

---

## 5. 구현 결정과 근거

각 항목은 실제로 겪은 문제의 해결책이다. 근거 없이 넣은 것은 없다.

### 5.1 좌표 캐시 키가 `(rom, named_selection)`

지오메트리는 입력값에 무관하므로 한 번만 받으면 된다. 다만 **region 을 바꾸면
좌표가 달라지므로** 단순 1회 캐시로는 좌표/필드 길이가 어긋난다.

### 5.2 단위 환산

ROM 은 미터, Omniverse 스테이지는 보통 cm(`metersPerUnit=0.01`). 환산하지 않으면
6.25m 짜리 열교환기가 6.25cm 로 들어간다. `UsdGeom.GetStageMetersPerUnit()` 을 읽어
`scale = source_mpu / stage_mpu` 를 곱한다. 환산이 일어나면 로그에 찍는다.
`set_source_meters_per_unit()` 으로 소스 단위를 바꿀 수 있다(기본 1.0 = 미터).

### 5.3 point width 자동 산출

기본값 0 = 자동. `bbox 대각선 / N^(1/3)` 으로 대략적인 포인트 간격을 잡는다.
**단위 환산 후의 좌표로 계산**하므로 스테이지 단위와 맞는다.

### 5.4 벡터 필드 magnitude 환산

`Velocity` 는 벡터라 스냅샷이 `3N` 길이로 나온다. 포인트당 색 하나로 매핑하려면
크기로 환산해야 한다. 배열 크기가 `N` 인지 `3N` 인지로 스칼라/벡터를 판별한다.

**주의**: 방향 정보를 버리고 있다. 유동 패턴을 보려면 glyph(화살표)가 맞다.

### 5.5 컬러맵은 Turbo

처음엔 viridis 근사를 썼는데 **viridis 는 위쪽이 노랑이라 빨강이 아예 안 나온다.**
[Ansys Discovery 스타일 가이드](https://developer.ansys.com/docs/discovery-style-guide-2024-r2/colors.md)의
기본 컬러맵이 Turbo(진한 파랑 → 진한 빨강)라 그것으로 교체했다.

matplotlib turbo 를 **33 제어점**으로 뽑았다. 256단계 원본 대비 최대 오차 3.2/255
(5개면 80/255, 17개면 6.8/255). 런타임 matplotlib 의존을 피하려고 테이블을 박아 넣었다.

참고: pytwin **문서 예제**는 PyVista 기본값(viridis 계열)을 쓴다. 즉 문서 그림과
Ansys 제품 화면이 서로 다르다. 제품 쪽에 맞췄다.

### 5.6 컬러맵 범위 자동 결정

```python
p99 = percentile(field, 99)
if max <= p99 * 1.5:  →  lo ~ max      # 꼬리 얇음, 자르지 않음
else:                 →  lo ~ p99      # 꼬리 두꺼움, 자름
```

**왜 필요한가** — 이 필드는 분포가 극단적으로 치우쳐 있다. 선형 min~max 정규화 시:

```
t 0.00~0.05:  56.36%    ← 절반 이상이 컬러맵 맨 밑
t 0.05~0.10:  23.93%
t 0.10~0.25:  18.54%
t 0.25~0.50:   1.11%
t 0.50~1.01:   0.07%    ← 상위 색이 사실상 안 쓰임
```

상위 1%가 범위의 72%를 독차지한다(`max/p99 = 2.23`). 자르면 대비가 3.3배 살아난다.

**항상 자르지는 않는다.** 꼬리가 얇으면 자르는 게 정보를 버리는 것이므로 그대로 쓴다.
어느 쪽을 골랐는지 근거와 함께 로그에 찍는다.

이 값은 **UI 노브가 아니다.** 한때 `clip %` 슬라이더로 노출했다가 걷어냈다 —
사용자가 정할 성질이 아니라 데이터에서 자동으로 정해져야 한다.

### 5.7 로드 시 회색 지오메트리 먼저

```
Load     → generate_points 만 → 회색 점 구름 (평가 없음)
Evaluate → generate_snapshot  → 색 입힘
```

로드 직후 형상이 보여야 스케일·카메라·위치를 먼저 확인할 수 있다. 회색은
`displayColor` 를 **constant 보간에 값 1개**로 쓴다 — 같은 회색을 87만 번 쓸 이유가 없다.
필드 색일 때만 vertex 보간으로 전환한다.

### 5.8 출력 민감도 (`driven by`)

pytwin 은 **입력↔출력 연결 정보를 노출하지 않는다** (`Description` 컬럼이 전부 비어 있다).
그래서 입력을 하나씩 5% 섭동해 실측한다. 평가 (입력 수 + 2)회, 약 0.3초.

처음엔 "반응하면 연결됨"이라는 이진 표기로 만들었는데 **모든 출력이 모든 입력에
반응**해서 정보가 0이었다(결합된 열유체 시스템이니 당연하다). 그래서 **기여도**로
바꿨다 — 같은 비율로 흔든 뒤 `|Δ출력|` 을 비교해, 최대를 1.00 으로 놓은 상대값.

값이 0인 입력은 상대 섭동이 0이 되므로 절대값 1.0 으로 흔든다.
끝나면 원래 입력값으로 복구한다. 첫 Evaluate 때 자동 1회 수행 후 캐시.

### 5.9 재생 — USD 타임라인을 쓰지 않는다

`asyncio` + `omni.kit.app.next_update_async()` 루프로 직접 돌린다.
(`axis_controller`, `usd_interpolation` 과 같은 집 패턴.)

**왜 타임라인에 굽지 않는가**

- 트윈 시각과 USD 스테이지 시각은 별개다. 묶는 건 선택이지 필연이 아니다.
- PFH ROM 은 시간 격자가 **고정**이라 임의 시각을 물어볼 수 없다. 연속인 USD
  타임코드에 억지로 매핑하면 없는 프레임을 만들어내게 된다.
- 타임라인은 씬의 다른 애니메이션과 공유되는 자원이다. 점유하면 충돌한다.
- 미리 구울 필요가 없다. 매 프레임 평가해서 색만 갱신하면 된다.

**정직성 조건** — 매 프레임이 실제 트윈 상태여야 유효하다. 매 프레임
`evaluate_step_by_step()` 을 부르므로 충족된다. 두 상태 사이를 색만 보간하는 것은
시각적 근사이며 "시뮬레이션 결과"라 부르면 안 된다.

**구현 세부**

- `play` — 현재 시각부터 재개. `pause` 후 부르면 이어서 진행 (트윈이 상태를 들고 있다)
- `pause` — 루프만 취소. 트윈 상태 유지
- `stop` — 취소 + `initialize_evaluation()` 으로 t=0 리셋 후 다시 그림
- 재생 중엔 `_write_colors()` 로 **색만** 갱신 (좌표·width·extent 는 그대로)
- **색 범위를 재생 시작 시점에 고정**한다. 프레임마다 다시 잡으면 같은 색이 프레임마다
  다른 값을 뜻해 애니메이션이 거짓말을 한다. `stop` 시 해제
- **정적 트윈 경고** — 첫 프레임과 이후 프레임이 완전히 같으면 한 번 경고한다.
  "코드가 안 도는 것"과 "트윈이 정적인 것"을 구분할 방법이 없기 때문이다
- `clear()`, `unload()`, UI `destroy()` 가 먼저 `pause()` 를 부른다. 지워진 prim 에
  색을 쓰거나 창이 닫힌 뒤에도 루프가 도는 걸 막는다

---

## 6. 검증 기록

### 6.1 데이터 추출 — 오차 0로 확인

pytwin 의 **독립 API** `get_tbrom_output_field()` (PyVista PolyData) 와 대조했다.

```
좌표 최대 절대오차  : 0
필드 최대 절대오차  : 0
필드 상대오차(최대) : 0
```

pytwin 이 자체 제공하는 `Velocity-normed` 배열과 우리가 계산한 magnitude 도 정확히
일치한다. **추출 경로는 확실히 맞다.**

### 6.2 물리 정합성

- 유량 2배 → 속도 1.65~1.73배. 아핀 관계로 설명됨 (2.4절)
- named selection 이 전부 전체 도메인 bbox 안에 포함됨
- 유량 단조 증가에 속도 단조 증가

### 6.3 성능 실측

| 작업 | 시간 |
|---|---|
| `evaluate_batch` 21 프레임 | 1.45s (≈70ms/프레임) |
| `generate_snapshot_batch` 21개 | 0.35s (≈17ms/개) |

**USD 에 87만 색을 쓰는 비용은 측정하지 않았다.** 재생 프레임레이트를 좌우할
지배적 요소일 가능성이 높다.

### 6.4 정적 코드 검사

문법(`py_compile`) + API 정합성 4종을 매 변경마다 돌렸다. 9절 참고.

---

## 7. 검증 안 된 것 / 열린 질문

- **재생(play/pause/stop) 전체.** 대상 트윈이 정적이라 동작 확인 불가.
  동적 트윈으로 시험해야 한다. Play 시 5.9절의 정적 경고가 뜨면 트윈이 정적인 것이고,
  경고가 없는데 화면이 안 변하면 우리 쪽 문제다.
- **재생 프레임레이트.** 87만 포인트 색 쓰기 비용 미측정. 느리면 step 을 키우거나
  포인트를 솎아내야 한다.
- **`ui.Pixel` 로 스택 높이를 잡는 방식**이 항목 수가 많을 때도 의도대로 되는지.
- **섭동 5%** 가 다른 트윈에서도 적절한지. 비선형이 강하면 값에 따라 기여도 순위가
  바뀔 수 있다.
- **`TwinModel.close()`** 가 모든 pytwin 버전에 있는지. `try/except` 로 감싸 뒀다.

---

## 8. 폐기한 접근과 이유

기록해 두지 않으면 다시 시도하게 된다.

| 접근 | 폐기 이유 |
|---|---|
| `morph.twin` 네임스페이스 | 이 저장소에선 불필요. 평평한 `twin` 모듈로 충분 |
| 입력 램프 + USD timeSample 굽기 | **시간 경과가 아니라 입력 스윕**이었다. 각 프레임은 진짜 트윈 결과지만 프레임 간 관계가 "시간이 흘렀다"가 아니라 "조건이 다르다"다. 화면상 과도응답과 구분이 안 돼 오해를 부른다. `evaluate_batch` 에 `Time` 컬럼을 넘긴 것도 pytwin 이 그 형식을 요구해서지 시간이 작용한 게 아니다 (Time 을 전부 0으로 채워도 결과 동일) |
| `clip %` UI 슬라이더 | 사용자가 정할 값이 아니다. 데이터 분포에서 자동 결정 (5.6절) |
| `set_value_range()` 명시 고정 | 자동 결정으로 충분. 필요해지면 되살린다 |
| 메시 투영 (`project_tbrom_on_mesh`) | PyDPF + DPF 서버 + 별도 CFD 파일(`HX_CFD.cas.h5`)이 필요하다. 포인트 클라우드가 목적이면 과하다. **다만 결과가 면으로 나와 훨씬 잘 읽히므로, 점 구름이 만족스럽지 않으면 재검토할 가치가 있다** |
| region 을 좁혀 보기 | 이 트윈에선 무의미. `shell` 이 873,348/873,433 로 사실상 전체다 |

---

## 9. 디버깅 도구

### 9.1 Kit 밖에서 pytwin 돌리기

Kit 을 띄우지 않고 파이썬만으로 검증할 수 있다. 이 방식으로 위 실측을 전부 했다.
Kit 재시작 없이 빠르게 반복할 수 있어 훨씬 효율적이다.

```python
import os, sys

ENV = r"C:\Users\OPTI\AppData\Local\ov\data\Kit\opt.edit\0.1\pip3-envs\default-3.12"
sys.path.append(ENV)                    # Kit pipapi 와 동일하게 append 만

# twin_viewer._bootstrap_pywin32 와 같은 동작
for entry in list(sys.path):
    dll = os.path.join(entry, "pywin32_system32")
    if os.path.isdir(dll):
        os.add_dll_directory(dll)
        for sub in ("win32", os.path.join("win32", "lib"), "pythonwin"):
            p = os.path.join(entry, sub)
            if os.path.isdir(p):
                sys.path.append(p)
        break

from pytwin import TwinModel
model = TwinModel(r"C:\Users\OPTI\Documents\HXVelVectorTBROM_23R2.twin")
model.initialize_evaluation()
model.print_model_info()                # 입출력/기본 설정/TBROM 정보 전부
```

실행할 파이썬은 Kit 것을 쓴다 (버전·ABI 일치):

```
D:\Public\Work\kit-app-template\_build\windows-x86_64\release\kit\python\python.exe
```

### 9.2 API 정합성 검사

`twin_viewer_service` 의 API 표면이 구현부/UI 와 어긋나지 않는지 정적으로 본다.
`__all__` ↔ 정의, service → `TwinViewer` 메서드, UI → service 를 대조한다.

```python
import ast, re
BASE = r"D:\Public\Work\WorkSync\twin\twin"

svc_src = open(f"{BASE}\\twin_viewer_service.py", encoding="utf-8").read()
svc = ast.parse(svc_src)
have = {n.name for n in svc.body if isinstance(n, ast.FunctionDef)}
imported = {a.asname or a.name for n in svc.body
            if isinstance(n, ast.ImportFrom) for a in n.names}
exported = set()
for n in svc.body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "__all__":
        exported = {e.value for e in n.value.elts}

viewer = ast.parse(open(f"{BASE}\\twin_viewer.py", encoding="utf-8").read())
cls = next(n for n in viewer.body if isinstance(n, ast.ClassDef))
methods = {m.name for m in cls.body if isinstance(m, ast.FunctionDef)}
called = set(re.findall(r"TwinViewer\.(\w+)", svc_src))
ui_used = set(re.findall(r"\btwin\.(\w+)",
                         open(f"{BASE}\\dummy_ui.py", encoding="utf-8").read()))

for label, bad in [
    ("__all__ entries not defined", exported - have - imported),
    ("defined but not exported", have - exported),
    ("service calls missing on TwinViewer",
     called - methods - {"_on_loaded", "_on_evaluated"}),
    ("ui calls missing in service", ui_used - have),
]:
    print(f"{label:38} {sorted(bad) if bad else 'OK'}")
```

### 9.3 내부 구조 들여다보기

`TbRom` 객체에 basis 가 그대로 있다. **밑줄 붙은 내부 속성이라 버전이 바뀌면
깨질 수 있다** — 조사용으로만 쓰고 제품 코드에 넣지 말 것.

```python
tbrom = model._tbroms[rom_name]
tbrom._outbasis        # ndarray (nbmodes, npoints, 3)
tbrom._nbmodes
```

필드의 정체는 단순한 선형결합이다:

```
Velocity(N, 3) = Σ  mode_coeff[i] × basis[i]
```

즉 모드 계수를 직접 주면 트윈 입력을 거치지 않고 임의의 필드를 만들 수 있다.
4 × N × 3 곱셈이라 사실상 즉시 계산되므로, 실시간 슬라이더나 두 상태 사이 보간에
쓸 수 있다. 다만 물리적으로 유효한 상태라는 보장은 없다.

---

## 10. 다음에 할 만한 것

우선순위 순.

1. **동적 트윈으로 재생 검증.** 7절의 미검증 항목이 전부 여기 걸려 있다.
2. **USD 색 쓰기 비용 측정.** 실시간 재생이 가능한지 / 미리 구워야 하는지를 가른다.
3. **입력 슬라이더.** 이 트윈에서 유일하게 의미 있는 "동작" 축이다. 드래그하면 즉시
   재평가·재색칠. 재평가 70ms + USD 쓰기이므로 쓰로틀이 필요하다.
   입력 범위는 트윈이 알려주지 않으므로(`Min`/`Max` 컬럼도 비어 있다) 지정해야 한다.
4. **`Parametric Field History` 플래그를 UI에 표시.** 새 트윈을 물렸을 때 시간축이
   있는지 즉시 보인다. `print_model_info()` 가 출력하는 값이다.
5. **벡터 방향 시각화(glyph).** magnitude 로 뭉개면서 방향을 버리고 있다. 유동
   패턴을 보려면 이게 맞다. 포인트를 솎아내야 읽힌다.
6. **S3 경로 지원.** `TwinModel` 은 로컬 경로만 받는다. 다운로드 계층이 필요하고,
   자리는 `twin_viewer_service.load_twin()` 이다.
