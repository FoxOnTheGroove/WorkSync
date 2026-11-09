# MORPH Annotation Overlay [morph.annotation.overlay]

모델 위 위치에 라벨·설명, inlet/outlet 화살표, 절단면(단면 위치) 표시를 뷰포트 안(분할 뷰 전체, 스트리밍 영상 포함)에 그린다.
클릭한 면(평면 영역 전체)을 골라 중심 좌표·법선·면적을 보이고 주석을 면 중심에 놓을 수 있다.
표시는 USD prim을 만들지 않고 prim 계층과 무관한 월드 좌표로 정의하며, 장면 파일 옆 JSON(`<장면 이름>.annotations.json`)에 저장한다. 사용법은 `CONTRACT.md`.
