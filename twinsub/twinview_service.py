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
    def _require_runner(cls, key: str):
        """key 의 러너. 없으면 예외."""
        runner = TwinView.get_runner(key)
        if runner is None:
            raise ValueError("로드된 트윈이 없다: {}".format(key))

        return runner

    @classmethod
    async def _to_thread(cls, fn, *args):
        """블로킹 작업을 워커 스레드로 넘긴다. USD 는 여기로 보내지 않는다."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    @classmethod
    def _show_loaded(cls, key: str, pos=None) -> None:
        """방금 로드한 트윈을 띄운다. 메인 스레드에서만 부른다."""
        TwinView.rom_show(key, cls._require_runner(key), pos)
        cls._log("show 성공: {}".format(key))

    @classmethod
    def _notify_loaded(cls) -> None:
        """로드/표시 상태가 바뀌었음을 구독자에게 알린다."""
        TwinView._notify(TwinView._on_loaded, "on_loaded")

    @classmethod
    def download_file(cls, s3_uri: str) -> str:
        """s3 에서 받기만 한다. 로컬 경로를 반환한다."""
        local_path = TwinView.download_file(s3_uri)
        cls._log("download 성공: {}".format(local_path))
        return local_path

    @classmethod
    def download_twin(cls, s3_uri: str, prim_path: str, pos=None) -> str:
        """s3 에서 받아 prim path 에 로드하고 띄운다. 임시파일은 지우고 key 를 준다."""
        key = TwinView.normalize_key(prim_path)

        local_path = cls.download_file(s3_uri)
        try:
            cls.load_twin(local_path, key, pos)
        finally:
            if TwinView.discard_temp(local_path):
                cls._log("임시파일 삭제: {}".format(local_path))

        return key

    @classmethod
    async def download_twin_async(cls, s3_uri: str, prim_path: str, pos=None) -> str:
        """download_twin 을 비동기로. 돌아오면 프림이 씬에 있다."""
        key = TwinView.normalize_key(prim_path)

        if not TwinView.begin_task(key):
            raise ValueError("이미 작업 중이다: {}".format(key))

        try:
            local_path = await TwinView.download_file_async(s3_uri)
            cls._log("download 성공: {}".format(local_path))

            try:
                await cls._load_async(local_path, key, pos)
            finally:
                if TwinView.discard_temp(local_path):
                    cls._log("임시파일 삭제: {}".format(local_path))
        finally:
            TwinView.end_task(key)

        return key

    @classmethod
    def load_twin(cls, path: str, prim_path: str, pos=None) -> str:
        """prim path 에 러너를 세우고 띄운다. 이후 키로 쓸 prim path 를 반환한다."""
        key = TwinView.load_twin(path, prim_path)
        cls._log("load 성공: {} -> {}".format(path, key))

        cls._show_loaded(key, pos)
        cls._notify_loaded()
        return key

    @classmethod
    async def load_twin_async(cls, path: str, prim_path: str, pos=None) -> str:
        """load_twin 을 비동기로. 러너 생성만 워커 스레드로 나간다."""
        key = TwinView.normalize_key(prim_path)

        if not TwinView.begin_task(key):
            raise ValueError("이미 작업 중이다: {}".format(key))

        try:
            return await cls._load_async(path, key, pos)
        finally:
            TwinView.end_task(key)

    @classmethod
    async def _load_async(cls, path: str, key: str, pos=None) -> str:
        """러너는 워커 스레드에서, 표시는 메인 스레드에서."""
        key = await cls._to_thread(TwinView.load_twin, path, key)
        cls._log("load 성공: {} -> {}".format(path, key))

        cls._show_loaded(key, pos)
        cls._notify_loaded()
        return key

    @classmethod
    def unload(cls, key: str) -> bool:
        """key 하나를 닫고 버린다. 프림은 남으니 씬에서 직접 지운다."""
        dropped = TwinView.unload(key)

        if dropped:
            cls._log("unload 성공: {}".format(key))
            cls._notify_loaded()

        return dropped

    @classmethod
    def cleanup(cls) -> None:
        """폴더를 지우고 러너/뷰/재생상태를 모두 버린다."""
        TwinView.cleanup()
        cls._log("cleanup 성공")
        cls._notify_loaded()

    @classmethod
    def list_keys(cls) -> list:
        """등록된 key(prim path) 목록."""
        return TwinView.list_keys()

    @classmethod
    def is_loaded(cls, key: str) -> bool:
        """key 에 러너가 물려 있는지."""
        return TwinView.is_loaded(key)

    @classmethod
    def get_file_path(cls, key: str) -> str:
        """key 가 물고 있는 .twin 경로. 없으면 빈 문자열."""
        return TwinView.get_file_path(key)

    @classmethod
    def get_runner(cls, key: str):
        """key 의 러너. 이 계층을 우회하는 탈출구다."""
        return TwinView.get_runner(key)

    @classmethod
    def get_inputs(cls, key: str) -> dict:
        """입력 이름 → 현재값."""
        return TwinView.get_inputs(cls._require_runner(key))

    @classmethod
    def set_input(cls, key: str, name: str, value: float) -> None:
        """입력 하나를 바꾼다."""
        TwinView.set_input(cls._require_runner(key), name, float(value))
        cls._log("set_input 성공: {} {} = {}".format(key, name, value))

    @classmethod
    def get_outputs(cls, key: str) -> dict:
        """출력 이름 → 마지막 값."""
        return TwinView.get_outputs(cls._require_runner(key))

    @classmethod
    def get_step_size(cls, key: str) -> float:
        """트윈의 step size."""
        return TwinView.get_step_size(cls._require_runner(key))

    @classmethod
    def set_step_size(cls, key: str, value: float) -> None:
        """step size 를 바꾼다. 양수가 아니면 예외."""
        TwinView.set_step_size(cls._require_runner(key), value)
        cls._log("set_step_size 성공: {} = {}".format(key, value))

    @classmethod
    def get_simulation_time(cls, key: str) -> float:
        """트윈 내부 시뮬레이션 시각(초)."""
        return TwinView.get_simulation_time(cls._require_runner(key))

    @classmethod
    def rom_show(cls, key: str, pos=None) -> bool:
        """key 자리에 rom 포인트 클라우드를 다시 띄운다. pos 를 주면 그 자리에."""
        shown = TwinView.rom_show(key, cls._require_runner(key), pos)

        if shown:
            cls._log("show 성공: {}".format(key))

        cls._notify_loaded()
        return shown

    @classmethod
    def get_deform_scale(cls, key: str) -> float:
        """그 rom 의 변형 스케일."""
        return TwinView.get_deform_scale(cls._require_runner(key))

    @classmethod
    def set_deform_scale(cls, key: str, value: float) -> None:
        """그 rom 의 변형 스케일을 바꾼다."""
        TwinView.set_deform_scale(cls._require_runner(key), value)
        cls._log("set_deform_scale 성공: {} = {}".format(key, value))

    @classmethod
    def play(cls, key: str) -> bool:
        """key 를 돌린다. 이미 재생 중이면 False."""
        started = TwinView.play(key, cls._require_runner(key))

        if started:
            cls._log("play 성공: {}".format(key))

        return started

    @classmethod
    def stop(cls, key: str) -> bool:
        """key 를 멈춘다. 재생 중이 아니면 False."""
        stopped = TwinView.stop(key, cls._require_runner(key))

        if stopped:
            cls._log("stop 성공: {}".format(key))

        return stopped

    @classmethod
    def is_playing(cls, key: str) -> bool:
        """key 가 재생 중인지."""
        return TwinView.is_playing(key)

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
        """로드/언로드/표시가 바뀌면 호출. None 으로 해제."""
        TwinView._on_loaded = callback

    @classmethod
    def set_on_time(cls, callback) -> None:
        """재생 중 매 프레임 호출. None 으로 해제."""
        TwinView._on_time = callback

    @classmethod
    def set_on_updated(cls, callback) -> None:
        """재생 중 필드를 갱신한 뒤 호출. None 으로 해제."""
        TwinView._on_updated = callback
