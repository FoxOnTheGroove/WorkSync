# MORPH Perf Monitor [morph.perf.monitor]

모든 morph 익스텐션이 공통으로 쓰는 측정 API(`scope`, `async_scope`, `counter`, `gauge`)와 지표 수집기.
주기마다 집계해 `morph.perf.report` 이벤트로 발행하고, 설정 시 morph.stream.messaging으로 웹(`morph.perf.monitor.report`)에 송신한다.
사용법은 익스텐션 폴더의 `CONTRACT.md`를 본다.
