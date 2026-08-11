# Overview

`twin` 은 Ansys 디지털 트윈(`.twin`)의 TBROM 필드 결과를 Omniverse 뷰포트에
포인트 클라우드로 그린다.

설계 배경과 구현 근거는 [DESIGN.md](DESIGN.md) 를 볼 것.

## 데이터 흐름

```
.twin ──► TwinModel.initialize_evaluation(inputs)
             ├─ generate_points(rom, False, ns)    ──► (N, 3) 좌표   [입력에 무관, 캐시]
             └─ generate_snapshot(rom, False, ns)  ──► (N,) | (3N,) 필드값
                                                         │
                                        magnitude 환산 → 범위 자동 결정 → Turbo
                                                         │
                                                         ▼
                              UsdGeom.Points  ( points / widths / extent )
                                              + primvars:displayColor
```

로드 직후에는 `generate_points` 만 호출해 **회색 점 구름**을 띄운다. 평가 전에
스케일·위치·카메라를 먼저 확인할 수 있고, 색이 입혀지는 순간이 눈에 보인다.

메시 투영(`project_tbrom_on_mesh`) 경로는 PyDPF와 DPF 서버, 별도 CFD 메시 파일이
추가로 필요하다. 포인트 클라우드가 목적이면 위 두 API로 충분하며 numpy 외에
의존성이 없다.

## 재생

트윈 시각은 **USD 스테이지 타임라인과 별개**로 다룬다. 타임라인에 굽지 않고
`asyncio` 루프로 직접 스텝을 돌리며 매 프레임 색만 갱신한다.

```
play  → 루프 { evaluate_step_by_step → generate_snapshot → displayColor 갱신 }
pause → 루프만 중단, 트윈 상태 유지 (이어서 재개 가능)
stop  → 중단 + initialize_evaluation 으로 t=0 리셋
```

프레임마다 실제로 트윈을 한 스텝 돌리므로 **모든 프레임이 실제 트윈 상태**다.
좌표는 시간에 무관하므로 다시 쓰지 않는다.

정적 ROM(정상상태 해로 학습된 것)은 시간이 흘러도 필드가 변하지 않는다.
그 경우 재생해도 그림이 바뀌지 않으며, 첫 프레임과 동일하면 경고를 남긴다.

## 계층

`dummy_ui` → `twin_viewer_service` → `twin_viewer`

UI는 구현부(`TwinViewer`)를 직접 import 하지 않는다. 구현부 시그니처가 바뀌어도
service 계층에서 흡수하며, UI가 service API만으로 동작한다는 사실이 곧 그 API 표면이
충분한지에 대한 검증이 된다.
