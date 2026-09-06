# 판정 기록 저장소 — 합의된 설계 (아직 구현 안 함)

세션이 끊겨도 잃지 않으려고 적어 둔다. 구현하면 이 파일은 지워도 된다.

## 왜

- 공장은 세션 내내 불변이다. 판정은 순수 함수다 —
  f(장비, EBS 모델, 정밀도, offset 모드, 포트 XML) → 결과.
- 이미 낸 답을 다시 보려고 재연산하는 건 성능 이전에 설계가 틀린 것.
- Init 이 사용자에게 안 보일 예정이라 사용자가 공장을 바꿀 수단이 없다.
  그래서 캐시는 항상 맞고, "기억한 값입니다" 라고 알릴 필요도 없다.
- 무엇보다 x3 을 보는 중에 get_result("x1") 으로 x1 의 답을 꺼내고 싶다.
  그래서 이건 캐시가 아니라 **기록**이다.

## API

    EbsSimulateService.get_result("6eas1201")   # EQP_ 접두 있든 없든. 없으면 {}
    EbsSimulateService.list_results()           # 판정한 장비 이름 전부
    EbsSimulateService.restore("6eas1201")      # 화면까지 (align + 마커 + 카메라)

get_result 는 화면을 안 건드린다. 값만 보려다 시점이 튀면 안 된다.

## 페이로드

    {
      "equipment": "EQP_6EAS1201", "equipment_path": "/World/Line_A/EQP_6EAS1201",
      "port_count": 2, "ebs": "/EBS_2P",
      "placeable": False, "reason": "1 cell(s) blocked, and through the equipment",
      "faces": {
        "left":    {"state": "clear", "distance": 1.482, "min_gap": 0.6,
                    "name": "EQP_0007", "at": (x, y, z)},
        "right":   {"state": "tight", "distance": 0.412, ...},
        "ceiling": {"state": "clash", "distance": None, ...},
      },
      "inside": {"hit": True, "places": 7, "with": [조각 이름들], "spots": [(x,y,z), ...]},
      "at": {"centre": (x, y, z), "transform": [16 floats]},
      "under": {"precision": "triangle", "offset_scale": "snap",
                "ebs_2port": ..., "ebs_3port": ..., "search_root": ...,
                "xml": [CACHE_VERSION, size, mtime]},
      "when": 1772600767.12,
    }

## 결정 사항

1. **저장하는 건 distance 뿐.** state(clear/tight/clash)와 min_gap 과 placeable 은
   읽을 때 지금의 최소 간격으로 계산한다. 거리는 사실, 판정은 해석.
   그래서 min gap 을 바꾸면 모든 기록이 한꺼번에 새 잣대로 읽히고 재연산이 0.
2. faces 는 리스트가 아니라 면 이름 딕셔너리.
3. under 를 넣어 기록이 스스로 조건을 말하게 한다. collide 는 해시가 아니라
   비교로 재사용 여부를 정한다. min gap 은 under 에 넣지 않는다.
4. timings / notes / rows 는 안 넣는다. 그건 실행 기록이지 판정이 아니다.
5. 버리는 정책 없음. Init 까지 전부 보관 (LRU 안 씀 — 기록이니까).
6. 메모리만. 파일로 안 내린다 — 상한 판정이 "설치 가능" 으로 조용히 틀리면
   되돌릴 수 없다. USD 스테이지는 파일 mtime 으로 못 잠근다.

## 순서

1. 기록 저장소 + get_result / list_results
2. collide 가 기록을 먼저 본다 (under 가 같으면 재사용)
3. min gap 변경 -> build_verdict 만 다시
4. restore (화면까지)
