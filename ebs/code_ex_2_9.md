# 2회차 · 입력 데이터 (장비 색인 · 포트 XML · 캐시)

> 목표: init 이 만드는 **세 가지 표** — 장비 색인, 스테이지 상자 목록, 포트 표 — 가 무엇이고, 어디서 읽어 어디에 담기는지 말할 수 있게.
> 시간: 약 1시간. 1회차 흐름도의 `init` 칸을 채운다.

---

## 0. 오늘의 지도

```
init
 ├─ open_stage()              USD 열기
 ├─ build_index()             ① 장비 색인      _eqp_index   {"EQP_6EAS1201": "/World/.../EQP_6EAS1201"}
 ├─ Collide._stage_boxes()    ② 상자 목록      _stage_index [(path, lo, hi, box, prim, chain), ...]
 └─ load_ports()              ③ 포트 표        _port_map    {"6EAS1201": [1, 2, 3]}  외 5개 표
        ├─ _load_cache()        캐시가 맞으면 여기서 끝
        └─ _scan_xml()          아니면 XML 파싱 → _collect_ports() → _save_cache()

SIM 때
 └─ _resolve_by_name()        입력 id → 장비 프림 (①을 먼저 보고, 없으면 찾아서 ①에 추가)
웹 질의
 └─ valid_ids()               ③만 본다 (USD 불필요)
```

> **열쇠 모양이 다르다**: ①은 `"EQP_" + 대문자` 이름, ③은 **`EQP_` 없는** 대문자 id. 둘을 잇는 곳마다 접두를 붙이거나 뗀다.

---

## 1. 설정 함수 (5분) — `ebs_simulate.py` 235–283, 908–931

경로가 **바뀌면 무엇을 버리나** 만 본다.

| setter | 바뀌면 |
|---|---|
| `set_usd_path` (235) | `_ready = False` |
| `set_xml_path` (270) | `_port_map = {}`, `_ready = False` |
| `set_search_root` (924) | `_eqp_index = {}`, `_ready = False` |
| `set_rail_root` (919) | `_rail_index = None` (늘 버림) |
| `set_ebs_paths` (278) | 저장만 |
| `set_min_gaps` (908) | 면별 최소 여유 dict 로 저장 |

> 경로를 바꾸면 `_ready` 가 내려가 **init 을 다시 돌려야** SIM 이 된다.

---

## 2. USD 열기 — `open_stage` (242) (5분)

- 경로가 비었으면 **아무것도 안 하고 성공** (지금 열린 스테이지를 쓴다).
- 이미 같은 URL 이 열려 있으면 다시 안 연다.
- `context.open_stage` 는 **동기** → 로드가 끝날 때까지 킷이 멈춘다.
- 실패 사유는 notes 에, 걸린 시간은 `_timings` 의 `"USD: open"` 에.

---

## 3. ① 장비 색인 — `build_index` (2140), `_walk` (2161) (10분)

### `build_index`
- 시작할 때 **색인과 충돌 캐시(`_triangles`, `_local`, `_faces`, `_parts`)와 `_rail_index` 를 비운다.**
- `_walk` 가 주는 프림 중 이름이 `EQP_` 로 시작하면 `_eqp_index[이름] = 경로`.
- 반환: 장비 수. `_timings` 에 `"build index (visited N)"`.

### `_walk(stage)` — 스테이지 순회기 (제너레이터)
- 시작점: `_search_root` 가 있으면 거기, 없거나 틀리면 **스테이지 전체** (notes 에 경고).
- 스택(DFS)으로 `children()` 을 따라간다.
- **내려가지 않는 곳**:
  - `EQP_` 프림의 안쪽 — 장비를 찾았으면 그 속은 안 본다 (빠름의 핵심).
  - `PRUNE_TYPES` (Mesh, Cube, Material, Shader, Camera …) 와 `*Light` 타입.
- 이름은 **대문자로** 돌려준다.

---

## 4. ② 스테이지 상자 목록 — `Collide._stage_boxes` (collide 1116) (10분)

간섭 판정이 "주변에 무엇이 있나" 를 빠르게 찾으려고 init 때 한 번 만든다.

- 이미 있으면 그대로 (`sim._stage_index`). init 이 `_stage_index = None` 으로 비운 뒤 부른다.
- 바운드 계산은 공유 캐시 `Collide._bounds_cache` (`UsdGeom.BBoxCache`, default+render purpose, extentsHint 사용).
- **빼는 것**: 우리 프림(`OURS`), EBS 두 개와 그 아래, `SKIP_TYPES`, `*Light`, 빈 상자.
- **담고 멈추는 것**: 지오메트리 타입(`GEOMETRY_TYPES`) 또는 **`EQP_` 프림 자체** → 장비는 통째 상자 하나.
- 그 외(Xform 등)는 자식으로 내려간다. 내려가면서 조상 목록 `chain` 을 쌓는다.
- 항목 모양: **`(경로, lo, hi, 상자, 프림, 조상chain)`**.

### 이걸 쓰는 곳
- `equipment_boxes` (2182) : ②에서 장비만 골라 `{장비이름: 월드상자}` 로 (`_eqp_boxes` 에 캐시). 옆 장비 찾기에 쓴다 (5회차).

---

## 5. ③ 포트 표 — `load_ports` (2381) (20분)

### 5-1. 흐름
1. 표 6개를 비운다.
2. XML 경로가 없으면 0.
3. **`_load_cache()`** 가 성공하면 그대로 끝.
4. 아니면 **`_scan_xml()`** → **`_collect_ports()`** → **`_save_cache()`**.
5. 반환: `_port_map` 의 장비 수.

### 5-2. XML 파싱 — `_scan_xml` (2401), `_feed_parser` (2415), `_PortScan` (91)
- 파서: 표준 `expat` (SAX 방식, 트리를 안 만든다 → 큰 파일도 메모리 적게).
- `_feed_parser` : 로컬은 `READ_BLOCK`(8MB)씩 읽어 흘리고, 원격(`omniverse://`)은 통째로 받아 8MB씩 흘린다.
- **`_PortScan`** 의 세 콜백:
  - `start(tag, attrib)` : 속성을 모아 스택에 쌓는다. `key/value` 쌍이면 **부모** 묶음에 `key → value` 로 넣는다. 이름이 `addrNNN` 이면 주소 번호를 스택에, 아니면 부모 주소를 물려받는다.
  - `data(text)` : 태그 사이 글 모으기.
  - `end(tag)` : 닫힐 때 거둔다.
    - `addrNNN` 이면 `cad-x`, `cad-y` → **`addr_cad[주소] = (x, y)`**
    - `next-address` + `distance-puls` → **`addr_next[주소].append((다음주소, 펄스))`**
    - `port-id` 가 `이름_번호` 꼴이면 → **`found[이름대문자][번호] = (offset, 주소)`**
- 정리하면 XML 에서 읽는 키는 `shared` 의 `PORT_ID_KEY, OFFSET_KEY, CADX_KEY, CADY_KEY, NEXT_KEY, PULS_KEY`, 주소 이름은 `ADDR_PATTERN`, 포트 id 는 `PORT_PATTERN`.

### 5-3. 표로 정리 — `_collect_ports` (2440)

| 표 | 모양 | 뜻 |
|---|---|---|
| `_port_map` | `{id: [1,2,3]}` | 장비별 포트 번호 (정렬) |
| `_port_offsets` | `{id: {번호: offset}}` | 포트별 레일 위 거리값 |
| `_port_addr_of` | `{id: {번호: 주소}}` | 포트별 주소 블록 |
| `_port_addr` | `{id: 주소}` | 대표 주소 (가장 큰 번호 포트의 주소) |
| `_addr_cad` | `{주소: (x, y)}` | 주소 블록의 CAD 좌표 |
| `_addr_next` | `{주소: [(다음, 펄스)]}` | 주소 사이 연결과 거리(펄스) |

- 이상한 장비는 **유형별 한 줄** 로 notes 에: 주소 블록이 여러 개에 걸친 장비, 포트 번호가 1..N 이 아닌 장비.
- 아래 셋(`_port_offsets`, `_addr_cad`, `_addr_next`)은 3·4회차에서 **포트 위치 계산** 에 쓰인다.

### 5-4. 캐시 — `_cache_path` (2463) ~ `_save_cache` (2505)
- 위치: **XML 경로 + `.ebscache.json`** (XML 바로 옆).
- 표식(stamp) `_stamp_of` (34) : `[CACHE_VERSION, 파일크기, 수정시각]`. 원격이면 `omni.client.stat` 으로.
- `_load_cache` : 표식이 다르거나 읽다 실패하면 **버리고 다시 파싱** (notes 에 이유). 맞으면 6개 표를 채우고 `"xml cache hit"`.
  - JSON 은 키가 문자열이라 읽을 때 `int(...)` 로 되돌린다.
- `_save_cache` : 6개 표 + 표식을 JSON 한 번에. `_write_text` 는 로컬이면 `.part` 에 썼다가 바꿔치기(중간에 죽어도 캐시가 안 깨짐).

### 5-5. 질의
- **`valid_ids(ids)`** (2525) : `_port_map` 이 비었으면 `load_ports()` 부터. id 를 대문자로, `EQP_` 를 떼고, **포트 수가 2 또는 3** 이면 유효. 입력 그대로의 값을 돌려준다.
- `get_port_count` / `get_port_indices` (2538, 2543) : 대문자 id 로 `_port_map` 조회.

---

## 6. 입력 id → 장비 프림 — `_resolve_by_name` (2322) (5분)

SIM 의 ① `_prepare` 가 부른다 (3회차).

1. `/` 로 시작하면 **경로로 직접**.
2. 대문자로, `EQP_` 가 없으면 붙인다.
3. **색인(①)** 에 있으면 그 경로.
4. 없으면 `search_root/EQP_xxx` 를 **직접 경로로** 시도 → 있으면 색인에 추가.
5. 그래도 없으면 `_walk` 로 **전체 순회** → 찾으면 색인에 추가.

나머지:
- `_resolve_by_selection` (2311) : 뷰포트 선택에서 위로 올라가며 `EQP_` 프림 찾기 (`get_selected_equipment` → dummy UI 의 From Sel).
- `_equipment_id` (2360) : 프림 이름에서 `EQP_` 떼기.
- `resolve_anchor` (2366) : 첫 자식을 따라 6뎁스 (Scope 는 안 셈). → 3회차 피봇.

---

## 7. 눈에 띈 것 (참고)

- 열쇠 모양 두 가지(`EQP_` 있음/없음)가 섞여 있어, 장비 이름을 다루는 코드는 늘 접두를 확인한다.
- `sweep_ports` (1131) 와 그 표시 `show_sweep` 는 지금 **아무도 안 부르는** 진단용 (포트 전수조사). 단, 그 안의 `_measure`, `_pivot_state` 는 4회차 `_snap_shift` 가 쓴다.
- `valid_ids` 는 USD 가 필요 없지만 init 이 동기라 init 중엔 응답이 밀린다 (1회차).

---

## 8. 마무리 메모 (5분)

1. init 이 만드는 세 표와 각각의 열쇠/값
2. `_walk` 가 빨리 끝나는 이유
3. XML 에서 읽는 여섯 키
4. 캐시가 버려지는 조건
5. 오늘 이해 안 된 것 1개

---

## 자가 테스트

### A. 설정
1. `set_xml_path` 로 다른 경로를 넣으면 무엇이 비워지나?
2. `set_search_root` 를 바꾼 뒤 init 없이 SIM 을 누르면?
3. `set_rail_root` 는 무엇을 버리나?

### B. USD
4. `_usd_path` 가 비어 있으면 `open_stage` 는?
5. 같은 USD 가 이미 열려 있으면?
6. `open_stage` 가 동기라서 생기는 일은?

### C. 장비 색인
7. `_eqp_index` 의 열쇠와 값의 모양은? 예를 들어라.
8. `build_index` 가 같이 비우는 것들은?
9. `_walk` 가 내려가지 않는 경우 세 가지.
10. `search_root` 가 틀린 경로면 어떻게 되나?
11. `_walk` 는 DFS 인가 BFS 인가? 무엇으로 구현했나?

### D. 상자 목록
12. `_stage_index` 항목 하나의 모양 (6개).
13. 상자 목록에서 빠지는 것들을 빠짐없이.
14. 장비(`EQP_`) 프림은 상자 목록에 어떻게 들어가나?
15. 바운드 계산에 쓰는 캐시와 그 설정.
16. `equipment_boxes` 는 무엇을 돌려주고 어디에 캐시하나?

### E. 포트 XML
17. 파서 이름과, 트리를 안 만드는 장점.
18. 로컬과 원격 XML 을 읽는 방식의 차이.
19. `_PortScan` 의 콜백 셋과 각자 하는 일.
20. `key`/`value` 쌍 태그는 어디에 담기나?
21. 주소 번호는 어떻게 정해지고, 주소가 없는 태그는?
22. `port-id` 값이 `6EAS1201_2` 면 무엇이 어디에 담기나?
23. `addr_cad`, `addr_next` 는 각각 어떤 태그 값으로 채워지나?

### F. 포트 표
24. `_collect_ports` 가 만드는 표 여섯과 각각의 모양.
25. `_port_addr` (대표 주소) 는 어떻게 정하나?
26. notes 에 남기는 이상한 장비 유형 둘.

### G. 캐시
27. 캐시 파일 위치와 이름.
28. 캐시 표식의 세 값.
29. 캐시를 버리고 다시 파싱하는 경우 둘.
30. 캐시를 읽을 때 `int(...)` 를 하는 이유.
31. 로컬 캐시를 `.part` 로 썼다가 바꾸는 이유.

### H. 질의와 해석
32. `valid_ids` 의 판정 기준과, 포트 표가 비어 있을 때의 동작.
33. `valid_ids(["eqp_6eas1201"])` 의 키 처리 과정.
34. `_resolve_by_name` 이 장비를 찾는 순서 다섯.
35. 이름으로 찾은 장비를 다음엔 왜 빨리 찾나?
36. `resolve_anchor` 는 몇 뎁스를, 어떻게 내려가나? Scope 는?

### I. 응용
37. "XML 을 고쳤는데 SIM 결과가 그대로다" — 무엇을 의심하나?
38. "init 이 매번 수십 초 걸린다" — 오늘 본 것 중 의심할 곳 셋.
39. "유효한 장비인데 valid_ids 에서 빠진다" — 가능한 원인 둘.
40. "장비를 찾을 수 없음" 이 뜬다 — `_resolve_by_name` 의 어느 단계까지 확인하나?

---

<details>
<summary><b>정답 (펼치기)</b></summary>

1. `_port_map` 을 비우고 `_ready = False`.
2. `_ready` 가 False 라 "Run Init first" (`SAY_INIT`) 로 실패.
3. `_rail_index` (늘 None 으로).
4. 아무것도 안 하고 True (지금 열린 스테이지 사용).
5. 다시 안 열고 True ("stage already open").
6. 로드가 끝날 때까지 킷 메인 스레드가 멈추고, 그동안 API 처리도 밀린다.
7. 열쇠 `"EQP_" + 대문자 이름`, 값 프림 경로 문자열. 예: `{"EQP_6EAS1201": "/World/Line/EQP_6EAS1201"}`.
8. `_rail_index`, `_triangles`, `_local`, `_faces`, `_parts`.
9. `EQP_` 프림의 안쪽, `PRUNE_TYPES` 타입, 이름이 `Light` 로 끝나는 타입.
10. notes 에 경고를 남기고 스테이지 전체를 훑는다.
11. DFS. 리스트를 스택으로 (`pop` / `extend`).
12. (경로, lo 튜플, hi 튜플, 상자 Range3d, 프림, 조상 chain).
13. `OURS` 와 그 아래, EBS 두 개와 그 아래, `SKIP_TYPES`, `*Light`, 빈 상자.
14. 장비 프림 자체가 상자 하나로 들어가고 그 속으로는 안 내려간다.
15. `UsdGeom.BBoxCache`, Default 시간, purpose default+render, `useExtentsHint=True`. `sim._bounds` 에 하나 공유.
16. `{장비이름: 월드상자}`. `_eqp_boxes`.
17. `expat`. 파일 전체 트리를 메모리에 만들지 않아 큰 XML 도 빠르고 가볍다.
18. 로컬은 8MB씩 읽으며 흘림, 원격은 통째로 받은 뒤 8MB씩 흘림.
19. `start` 속성 수집·key/value 를 부모에·주소 물려주기, `data` 글 모으기, `end` CAD·다음주소·포트 거두기.
20. 부모 묶음(스택 바로 아래)에 `key(소문자) → value`. value 속성이 없으면 태그 사이 글.
21. 이름(또는 태그)이 `addrNNN` 꼴이면 그 숫자. 아니면 부모의 주소를 물려받는다.
22. `found["6EAS1201"][2] = (offset, 지금 주소)`.
23. `addr_cad` 는 `addrNNN` 태그의 `cad-x`, `cad-y`. `addr_next` 는 `next-address` 와 `distance-puls` 가 함께 있는 곳.
24. `_port_map {id:[번호]}`, `_port_offsets {id:{번호:offset}}`, `_port_addr_of {id:{번호:주소}}`, `_port_addr {id:주소}`, `_addr_cad {주소:(x,y)}`, `_addr_next {주소:[(다음,펄스)]}`.
25. 주소가 있는 포트 중 가장 큰 번호 포트의 주소.
26. 여러 주소 블록에 걸친 장비, 포트 번호가 1..N 이 아닌 장비.
27. XML 경로 + `.ebscache.json` (XML 옆).
28. `CACHE_VERSION`, 파일 크기, 수정 시각.
29. 표식이 다를 때, 파일을 읽거나 해석하다 예외가 날 때. (XML 자체를 못 읽어도 False)
30. JSON 열쇠는 늘 문자열이라 포트 번호·주소를 정수로 되돌리려고.
31. 쓰다가 죽어도 기존 캐시가 깨지지 않게 (원자적 교체).
32. 포트 수가 2 또는 3. 표가 비었으면 `load_ports()` 로 캐시(또는 XML)부터 읽는다.
33. `"EQP_6EAS1201"` 로 대문자 → `EQP_` 떼서 `"6EAS1201"` → `_port_map` 조회. 반환은 원래 값 `"eqp_6eas1201"`.
34. `/` 경로 직접 → 대문자·접두 붙이기 → 색인 조회 → `search_root/EQP_xxx` 직접 경로 → 전체 순회.
35. 찾으면 `_eqp_index` 에 추가해 두니까.
36. 6뎁스(`ANCHOR_DEPTH`). 매번 첫 자식. Scope 는 뎁스로 안 세고 통과.
37. 캐시 표식(크기·수정시각)이 안 바뀌었는지, 다른 XML 경로를 보고 있는지, init 을 다시 안 돌렸는지(`_port_map` 이 남아 있음).
38. `open_stage`(USD 로드), `_stage_boxes`(바운드 계산), 캐시 미스로 XML 전체 파싱. (+ 1회차의 skin copy)
39. XML 에 그 장비가 없거나 포트 수가 2·3 이 아님, 또는 id 표기가 XML 의 `port-id` 앞부분과 다름.
40. 입력이 경로인지 → 색인에 있는지 → `search_root` 직접 경로 → 전체 순회 결과. 대부분 `search_root` 아래에 없거나 이름 표기가 다름.

</details>
