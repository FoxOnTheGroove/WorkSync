# Overview

`morph.annotation.overlay`는 주석(label / inlet / outlet / plane)을 월드 좌표로 보관하고 `omni.ui.scene`으로 모든 뷰포트에 그린다.
장면이 열리면 같은 폴더의 사이드카 JSON을 자동으로 읽는다. 뷰 클릭(pick)·선택·데스크톱 편집 창("MORPH Annotations")으로 편집한다. 면 선택(`pick_face`)은 클릭한 평면 영역 전체의 중심·법선·면적을 계산해 강조하고 주석을 면 중심에 놓는 데 쓴다. 공개 계약은 `CONTRACT.md` 참고.

내부 구성: `model.py`(검증), `sidecar.py`(파일), `overlay.py`(API·이벤트), `web.py`(웹 메시지), `drawing.py`(그리기·클릭 잡기), `pick.py`(뷰포트 쿼리·normal), `face.py`(면 계산·Mesh 캐시·크기 상한·작업 스레드 빌드, numpy), `editor.py`(편집 모델·창·메뉴·도킹), `fonts.py`(편집 창 글꼴 설정·앱 글꼴 경고), `extension.py`(연결). `data/fonts/korean_region.txt`는 앱 `/app/font/customRegionFiles`용 한글 글자 목록 참고 사본 (앱은 `source/apps/fonts/korean_region.txt`). 한글은 앱 글꼴 설정으로 켜고, 편집 창·라벨은 앱 글꼴을 따른다.
