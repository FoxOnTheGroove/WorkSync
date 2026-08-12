"""TwinView 외부 API.

UI와 다른 익스텐션은 twinview.TwinView 를 직접 건드리지 말고 이 클래스만 쓴다.
구현부 시그니처가 바뀌어도 이 계층에서 흡수한다.

전부 classmethod 라 인스턴스를 만들지 않는다.

    from twinsub.twinview_service import TwinViewService as tv

    tv.load_twin(tv.download_twin("s3://bucket/key/model.twin"))
    tv.rom_show("/World")
    tv.set_input("Mass_Flow", 75.0)
    tv.play()
"""

from .twinview import TwinView

__all__ = ["TwinViewService"]


class TwinViewService:

    # ------------------------------------------------------------ 내부

    @classmethod
    def _require_target(cls):
        """현재 대상 (twin path, runner). 없으면 예외 — 부른 쪽이 이유를 그대로 띄운다."""
        path = TwinView.get_local_path()
        runner = TwinView.get_runner(path)
        if runner is None:
            raise ValueError("먼저 .twin 을 로드할 것")
        return path, runner

    # ------------------------------------------------------------ 수명주기

    @classmethod
    def download_twin(cls, s3_uri: str) -> str:
        """s3 uri 의 .twin 을 임시폴더에 받고 로컬 경로를 반환한다."""
        return TwinView.download_twin(s3_uri)

    @classmethod
    def load_twin(cls, path: str) -> bool:
        """로컬 .twin 경로로 러너를 세운다. 같은 경로면 기존 러너를 재사용한다."""
        return TwinView.load_twin(path)

    @classmethod
    def cleanup(cls) -> None:
        """임시폴더를 지우고 러너/뷰/재생상태를 모두 버린다."""
        TwinView.cleanup()

    @classmethod
    def is_loaded(cls) -> bool:
        return TwinView.is_loaded()

    @classmethod
    def get_local_path(cls) -> str:
        """현재 대상 .twin 경로. 없으면 빈 문자열."""
        return TwinView.get_local_path()

    @classmethod
    def get_runner(cls, path: str = ""):
        """path 의 TwinRunner. path 를 비우면 현재 대상. 없으면 None.

        이 계층을 우회하는 탈출구다. 되도록 아래 함수들을 쓴다.
        """
        return TwinView.get_runner(path)

    # ------------------------------------------------------------ 모델 정보

    @classmethod
    def get_inputs(cls) -> dict:
        """입력 이름 → 현재값. 사본이다."""
        return TwinView.get_inputs(cls._require_target()[1])

    @classmethod
    def set_input(cls, name: str, value: float) -> None:
        """입력 하나를 바꾼다."""
        TwinView.set_input(cls._require_target()[1], name, float(value))

    @classmethod
    def get_outputs(cls) -> dict:
        """출력 이름 → 마지막 값. 사본이다."""
        return TwinView.get_outputs(cls._require_target()[1])

    @classmethod
    def get_step_size(cls) -> float:
        return TwinView.get_step_size(cls._require_target()[1])

    @classmethod
    def set_step_size(cls, value: float) -> None:
        """step size 를 바꾼다. 양수가 아니면 예외."""
        TwinView.set_step_size(cls._require_target()[1], value)

    @classmethod
    def get_simulation_time(cls) -> float:
        """트윈 내부 시뮬레이션 시각(초)."""
        return TwinView.get_simulation_time(cls._require_target()[1])

    # ------------------------------------------------------------ 표시

    @classmethod
    def rom_show(cls, prim_path: str) -> bool:
        """prim path 아래에 rom 포인트 클라우드를 띄운다.

        여기서 정해진 prim path 아래를 재생 중 업데이트가 갱신한다.
        """
        return TwinView.rom_show(prim_path, cls._require_target()[1])

    @classmethod
    def get_deform_scale(cls) -> float:
        """현재 rom 의 변형 스케일. 설정이 없으면 1.0."""
        return TwinView.get_deform_scale(cls._require_target()[1])

    @classmethod
    def set_deform_scale(cls, value: float) -> None:
        """현재 rom 의 변형 스케일을 바꾼다."""
        TwinView.set_deform_scale(cls._require_target()[1], value)

    # ------------------------------------------------------------ 재생

    @classmethod
    def play(cls) -> bool:
        """현재 대상 러너를 돌린다. 이미 재생 중이면 False."""
        return TwinView.play(*cls._require_target())

    @classmethod
    def stop(cls) -> bool:
        """현재 대상 러너를 멈춘다. 재생 중이 아니면 False."""
        return TwinView.stop(*cls._require_target())

    @classmethod
    def is_playing(cls, path: str = "") -> bool:
        """path 가 재생 중인지. path 를 비우면 현재 대상."""
        return TwinView.is_playing(path)

    @classmethod
    def set_interval(cls, seconds: float) -> None:
        """재생 중 필드를 다시 읽는 주기(초). 기본 0.5."""
        TwinView.set_interval(seconds)

    @classmethod
    def get_interval(cls) -> float:
        return TwinView.get_interval()

    # ------------------------------------------------------------ 이벤트 훅

    @classmethod
    def set_on_time(cls, callback) -> None:
        """재생 중 매 프레임 호출. fn() -> None. None 으로 해제.

        시뮬레이션 시각처럼 싸게 읽는 값만 여기서 갱신한다.
        """
        TwinView._on_time = callback

    @classmethod
    def set_on_updated(cls, callback) -> None:
        """재생 중 필드를 갱신한 뒤 호출. fn() -> None. None 으로 해제.

        set_interval 주기로 온다. 부른 쪽이 폴링하지 않고 이 신호로 다시 읽는다.
        """
        TwinView._on_updated = callback
