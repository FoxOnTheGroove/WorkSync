# 앱 글꼴 한글 글리프 범위 (morph.lab*.kit의 [settings.app] font.customRegionFiles)

- `korean_region.txt`: 한글 자모 + KS X 1001 상용 한글 2,350자 + 문장 부호. 모두 넣으면(11,172자) 글꼴 아틀라스가 16384x5303까지 커져서 상용 범위만 넣는다.
- 목록에 없는 글자(드문 음절)는 ? 로 보인다. 필요하면 이 파일에 추가한다.
- 글꼴 자체는 `font.customFontPath`(이 PC: `C:/Windows/Fonts/malgun.ttf`).
