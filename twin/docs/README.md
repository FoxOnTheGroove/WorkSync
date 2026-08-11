# twin — Twin Viewer

Ansys Twin Builder에서 export한 `.twin` 런타임을 평가해, TBROM 필드 결과를
USD 포인트 클라우드(`UsdGeom.Points` + `primvars:displayColor`)로 뷰포트에 표시한다.

설계 배경과 구현 근거, 검증 기록은 [DESIGN.md](DESIGN.md) 에 있다.
다음 세션에서 이어받을 때는 그쪽을 먼저 읽을 것.

## 동작

```
Load      →  지오메트리만 회색 포인트로 표시 + 입력/출력 목록
입력값 지정 →  Evaluate  →  필드 평가 후 색 입힘, 출력값 갱신
▶/❚❚/■    →  트윈 시각을 진행/일시정지/리셋 (동적 트윈에서만 의미 있음)
```

## 구성

```
twin/                       <- 익스텐션 루트 (= 익스텐션 ID "twin")
├── config/extension.toml
├── docs/
└── twin/                   <- python 모듈 "twin"
    ├── __init__.py
    ├── extension.py
    ├── twin_viewer.py
    ├── twin_viewer_service.py
    └── dummy_ui.py
```

Kit의 익스텐션 검색 경로는 **이 폴더의 부모**(`.../WorkSync`)를 가리켜야 한다.
`.../WorkSync/twin` 을 검색 경로로 주면 Kit이 자식인 `config/` 를 익스텐션으로 오인한다.

| 파일 | 역할 |
|---|---|
| `twin/extension.py` | `omni.ext.IExt` 진입점 |
| `twin/twin_viewer.py` | 구현부. pytwin 호출과 USD 기록 |
| `twin/twin_viewer_service.py` | 외부 API. 다른 익스텐션은 이것만 쓴다 |
| `twin/dummy_ui.py` | service API만 사용하는 더미 UI |

## 사용

```python
from twin import twin_viewer_service as twin

twin.load_twin(r"C:/Users/OPTI/Documents/HXVelVectorTBROM_23R2.twin")
twin.evaluate({"Mass_Flow_HX": 75.0, "Tube_temperature": 1115.0, "shell_inlet_temp": 300.0})
```

## 요구사항

- **pytwin[graphics]** — Kit 익스텐션이 아니라 pip 패키지라 `config/extension.toml` 의
  `[python.pipapi]` 가 startup 때 받아온다.

  `[graphics]` extra(pyvista/vtk)는 선택이 아니라 **필수**다. TBROM이 포함된 `.twin` 은
  `TwinModel` 인스턴스화 시점에 graphics를 요구하며, 없으면 다음과 같이 실패한다.

  ```
  Twin model failed during instantiation.
  Graphics are required for this method.
  ```

- **pywin32 부트스트랩** — pytwin이 의존하는 pywin32는 `pywin32.pth` 로 `win32`,
  `win32/lib`, `pythonwin` 을 `sys.path` 에 올린다. 그런데 Kit의 pipapi 설치 경로는
  `site.addsitedir()` 없이 `sys.path` 에 붙기만 해서 `.pth` 가 처리되지 않고,
  `No module named 'win32api'` 로 실패한다. `twin_viewer._bootstrap_pywin32()` 가
  해당 경로와 `pywin32_system32` DLL 디렉터리를 직접 등록해 이를 우회한다.

- **Python 3.10 ~ 3.13** (pytwin 지원 범위)

- **Ansys 라이선스** — `.twin` export 방식에 따라 갈린다.

  | 조건 | 필요 feature |
  |---|---|
  | 2023 R1 | `twin_builder_deployer` |
  | 2023 R1 SP1+ / licensed export | `twin_runtime` |
  | 2023 R1 SP1+ / unlicensed export | 없음 |

  ROM 필드값은 파일에 저장된 게 아니라 입력값으로부터 런타임이 계산한다.
  따라서 "읽기만" 하는 경로는 없고, 시각화하려면 반드시 런타임을 실행해야 한다.

## 알려진 제약

- `TwinModel` 은 **로컬 파일 경로**를 받는다. S3 등 원격 경로를 쓰려면 로컬로
  내려받은 뒤 그 경로를 넘겨야 한다.
- 벡터 필드(예: velocity)의 스냅샷은 포인트 수 `N` 이 아니라 `3N` 길이로 나온다.
  포인트당 하나의 색으로 매핑하려면 magnitude 로 환산한다 — **방향 정보는 버려진다.**
- 정적 ROM(정상상태 해로 학습)은 시간축이 있어도 답할 내용이 없다. 재생해도
  그림이 바뀌지 않는다. `print_model_info()` 의 `Parametric Field History` 로 판별한다.
- 재생 기능은 **미검증**이다. 개발에 쓴 트윈이 정적이라 확인할 수 없었다.
  자세한 내용은 [DESIGN.md](DESIGN.md) 7절.
