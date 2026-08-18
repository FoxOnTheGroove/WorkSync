import asyncio

from .twinview import TwinView

__all__ = ["TwinViewService"]

LOG_PREFIX = "[twinviewer]"


class TwinViewService:

    DEFAULT_PRIM_PATH = "/World/TwinRom"

    @classmethod
    def _log(cls, message: str) -> None:
        """API 동작 결과를 한 줄 찍는다."""
        print("{} {}".format(LOG_PREFIX, message))

    @classmethod
    def _require_target(cls):
        """현재 대상 (twin path, runner). 없으면 예외."""
        path = TwinView.get_local_path()
        runner = TwinView.get_runner(path)
        if runner is None:
            raise ValueError("먼저 .twin 을 로드할 것")
        return path, runner

    @classmethod
    async def _to_thread(cls, fn, *args):
        """블로킹 작업을 워커 스레드로 넘긴다. USD 는 여기로 보내지 않는다."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    @classmethod
    def _show_loaded(cls, path: str, prim_path: str, pos=None) -> None:
        """방금 로드한 트윈을 띄운다. 메인 스레드에서만 부른다."""
        runner = TwinView.get_runner(path.strip())
        if runner is not None:
            TwinView.rom_show(prim_path, runner, pos)

    @classmethod
    def _notify_loaded(cls) -> None:
        """로드/표시 상태가 바뀌었음을 구독자에게 알린다."""
        TwinView._notify(TwinView._on_loaded, "on_loaded")

    @classmethod
    def download_twin(cls, s3_uri: str, prim_path: str = "", pos=None) -> str:
        """s3 에서 받는다. prim_path 를 주면 로드와 표시까지, pos 면 그 자리에."""
        local_path = TwinView.download_twin(s3_uri)
        cls._log("download 성공: {}".format(local_path))

        if prim_path:
            cls.load_twin(local_path, prim_path, pos)
            if TwinView.discard_temp(local_path):
                cls._log("임시파일 삭제: {}".format(local_path))

        return local_path

    @classmethod
    async def download_twin_async(cls, s3_uri: str, prim_path: str = "", pos=None) -> str:
        """download_twin 을 비동기로. 돌아오면 프림이 씬에 있다."""
        key = ("download", s3_uri.strip())
        if not TwinView.begin_task(key):
            raise ValueError("이미 받는 중이다: {}".format(s3_uri))

        try:
            local_path = await TwinView.download_twin_async(s3_uri)
        finally:
            TwinView.end_task(key)

        cls._log("download 성공: {}".format(local_path))

        if prim_path:
            await cls.load_twin_async(local_path, prim_path, pos)
            if TwinView.discard_temp(local_path):
                cls._log("임시파일 삭제: {}".format(local_path))

        return local_path

    @classmethod
    def load_twin(cls, path: str, prim_path: str = "", pos=None) -> bool:
        """러너를 세운다. prim_path 를 주면 띄우고, pos 를 주면 그 자리에 둔다."""
        if not TwinView.load_twin(path):
            return False

        cls._log("load 성공: {}".format(path))

        if prim_path:
            cls._show_loaded(path, prim_path, pos)
            cls._log("show 성공: {}".format(prim_path))

        cls._notify_loaded()
        return True

    @classmethod
    async def load_twin_async(cls, path: str, prim_path: str = "", pos=None) -> bool:
        """load_twin 을 비동기로. 러너 생성만 워커 스레드로 나간다."""
        key = ("load", path.strip())
        if not TwinView.begin_task(key):
            raise ValueError("이미 로드 중이다: {}".format(path))

        try:
            if not await cls._to_thread(TwinView.load_twin, path):
                return False

            cls._log("load 성공: {}".format(path))

            if prim_path:
                cls._show_loaded(path, prim_path, pos)
                cls._log("show 성공: {}".format(prim_path))

            cls._notify_loaded()
            return True
        finally:
            TwinView.end_task(key)

    @classmethod
    def cleanup(cls) -> None:
        """폴더를 지우고 러너/뷰/재생상태를 모두 버린다."""
        TwinView.cleanup()
        cls._log("cleanup 성공")

    @classmethod
    def is_loaded(cls) -> bool:
        """로드된 트윈이 있는지."""
        return TwinView.is_loaded()

    @classmethod
    def get_local_path(cls) -> str:
        """현재 대상 .twin 경로. 없으면 빈 문자열."""
        return TwinView.get_local_path()

    @classmethod
    def get_runner(cls, path: str = ""):
        """path 의 러너. 이 계층을 우회하는 탈출구다."""
        return TwinView.get_runner(path)

    @classmethod
    def get_inputs(cls) -> dict:
        """입력 이름 → 현재값."""
        return TwinView.get_inputs(cls._require_target()[1])

    @classmethod
    def set_input(cls, name: str, value: float) -> None:
        """입력 하나를 바꾼다."""
        TwinView.set_input(cls._require_target()[1], name, float(value))
        cls._log("set_input 성공: {} = {}".format(name, value))

    @classmethod
    def get_outputs(cls) -> dict:
        """출력 이름 → 마지막 값."""
        return TwinView.get_outputs(cls._require_target()[1])

    @classmethod
    def get_step_size(cls) -> float:
        """트윈의 step size."""
        return TwinView.get_step_size(cls._require_target()[1])

    @classmethod
    def set_step_size(cls, value: float) -> None:
        """step size 를 바꾼다. 양수가 아니면 예외."""
        TwinView.set_step_size(cls._require_target()[1], value)
        cls._log("set_step_size 성공: {}".format(value))

    @classmethod
    def get_simulation_time(cls) -> float:
        """트윈 내부 시뮬레이션 시각(초)."""
        return TwinView.get_simulation_time(cls._require_target()[1])

    @classmethod
    def rom_show(cls, prim_path: str, pos=None) -> bool:
        """prim path 에 rom 포인트 클라우드를 띄운다. pos 를 주면 그 자리에."""
        shown = TwinView.rom_show(prim_path, cls._require_target()[1], pos)

        if shown:
            cls._log("show 성공: {}".format(prim_path))

        cls._notify_loaded()
        return shown

    @classmethod
    def get_prim_path(cls, path: str = "") -> str:
        """그 트윈을 띄운 prim path. 안 띄웠으면 빈 문자열."""
        return TwinView.get_prim_path(path)

    @classmethod
    def get_deform_scale(cls) -> float:
        """현재 rom 의 변형 스케일."""
        return TwinView.get_deform_scale(cls._require_target()[1])

    @classmethod
    def set_deform_scale(cls, value: float) -> None:
        """현재 rom 의 변형 스케일을 바꾼다."""
        TwinView.set_deform_scale(cls._require_target()[1], value)
        cls._log("set_deform_scale 성공: {}".format(value))

    @classmethod
    def play(cls) -> bool:
        """현재 대상을 돌린다. 이미 재생 중이면 False."""
        started = TwinView.play(*cls._require_target())

        if started:
            cls._log("play 성공: {}".format(TwinView.get_local_path()))

        return started

    @classmethod
    def stop(cls) -> bool:
        """현재 대상을 멈춘다. 재생 중이 아니면 False."""
        stopped = TwinView.stop(*cls._require_target())

        if stopped:
            cls._log("stop 성공: {}".format(TwinView.get_local_path()))

        return stopped

    @classmethod
    def is_playing(cls, path: str = "") -> bool:
        """path 가 재생 중인지. 비우면 현재 대상."""
        return TwinView.is_playing(path)

    @classmethod
    def set_interval(cls, seconds: float) -> None:
        """재생 중 필드를 다시 읽는 주기(초). 기본 0.5."""
        TwinView.set_interval(seconds)
        cls._log("set_interval 성공: {}".format(seconds))

    @classmethod
    def get_interval(cls) -> float:
        """필드 갱신 주기(초)."""
        return TwinView.get_interval()

    @classmethod
    def set_on_loaded(cls, callback) -> None:
        """로드/표시가 바뀌면 호출. None 으로 해제."""
        TwinView._on_loaded = callback

    @classmethod
    def set_on_time(cls, callback) -> None:
        """재생 중 매 프레임 호출. None 으로 해제."""
        TwinView._on_time = callback

    @classmethod
    def set_on_updated(cls, callback) -> None:
        """재생 중 필드를 갱신한 뒤 호출. None 으로 해제."""
        TwinView._on_updated = callback
