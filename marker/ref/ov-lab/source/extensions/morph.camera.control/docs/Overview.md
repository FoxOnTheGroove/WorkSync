# Overview

`morph.camera.control`은 포커스 뷰(Kit 활성 뷰포트)의 카메라를 움직이는 유일한 경로다.
프로그램과 웹 입력은 모두 `get_camera_controller()` API를 거치며, 명령은 큐잉되어 업데이트 루프에서 프레임당 한 번 병합 반영된다.
자세한 API·이벤트·웹 메시지는 `CONTRACT.md` 참고.
