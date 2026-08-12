import os
import shutil
import tempfile
from urllib.parse import urlparse

from .rom_view import RomPointCloud
from .twin_runner import TwinRunner


class TwinView:

    # 임시폴더는 세션당 하나만 만들어 재사용한다. 로드할 때마다 새로 파면
    # 정리할 대상이 흩어진다.
    _temp_dir = None

    # 마지막으로 로드한 .twin 경로. _runners 에서 현재 대상을 고르는 키다.
    _local_path = ""

    # twin path → TwinRunner
    _runners = {}  # type: dict

    # prim path → {rom name → RomPointCloud}
    # prim path 별로 나눠 담아야 같은 rom 을 여러 경로 아래에 따로 띄울 수 있다.
    _rom_views = {}  # type: dict

    # ------------------------------------------------------------ 다운로드

    @classmethod
    def download_twin(cls, s3_uri: str) -> str:
        """s3 uri 의 .twin 을 임시폴더에 받고 로컬 경로를 반환한다.

        실패하면 예외를 올린다 — 실패 이유를 UI에 그대로 보여주기 위해서다.
        """
        bucket, key = cls._parse_s3_uri(s3_uri)

        name = os.path.basename(key)
        if not name:
            raise ValueError("s3 uri 가 파일이 아니라 폴더를 가리킨다: {}".format(s3_uri))

        # boto3 는 Kit 기동 시점에 없을 수 있다. 모듈 import 를 막지 않도록 늦게 올린다.
        import boto3

        local_path = os.path.join(cls._get_temp_dir(), name)
        boto3.client("s3").download_file(bucket, key, local_path)

        cls._local_path = local_path
        return local_path

    @classmethod
    def get_local_path(cls) -> str:
        """마지막으로 받은 로컬 경로. 없으면 빈 문자열."""
        return cls._local_path

    # ------------------------------------------------------------ 로드

    @classmethod
    def load_twin(cls, path: str) -> bool:
        """로컬 .twin 경로로 러너를 세운다.

        s3 를 거치지 않고 경로를 바로 넣어도 되게 다운로드와 분리해 둔다.
        """
        path = path.strip()
        if not path:
            raise ValueError("경로가 비어 있다")

        if not os.path.isfile(path):
            raise ValueError("파일이 없다: {}".format(path))

        runner = cls._runners.get(path)
        if runner is None:
            # 실패하면 _runners 에 넣지 않는다. 반쯤 세워진 러너가 남으면
            # 다음 동작이 그쪽에 걸린다.
            runner = TwinRunner(path)
            cls._runners[path] = runner

        cls._local_path = path
        return True

    @classmethod
    def get_runner(cls, path: str = "") -> object:
        """path 의 TwinRunner. path 를 비우면 현재 대상. 없으면 None."""
        return cls._runners.get(path or cls._local_path)

    @classmethod
    def is_loaded(cls) -> bool:
        return cls.get_runner() is not None

    # ------------------------------------------------------------ 표시

    @classmethod
    def rom_show(cls, path: str, runner) -> bool:
        """prim path 아래에 rom 포인트 클라우드를 띄운다.

        지금은 트윈에 TBROM이 하나라고 보고 첫 번째만 쓴다.
        """
        rom_name = runner.tbrom_names[0]

        if not runner.set_rom_selected(rom_name, True):
            return False

        view = cls._get_rom_view(path, rom_name)
        if view is not None:
            view.ensure_prim(runner.rom_points[rom_name])

        return True

    # ------------------------------------------------------------ 재생

    @classmethod
    def play(cls, runner) -> None:
        runner.start()

    @classmethod
    def stop(cls, runner) -> None:
        runner.stop()

    @classmethod
    def _get_rom_view(cls, path: str, name: str):
        """(prim path, rom 이름) 에 물린 RomPointCloud 를 준다. 없으면 만든다.

        stage 를 못 찾으면 None. 스테이지가 아직 안 열린 상태에서 눌린 경우다.
        """
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None

        views = cls._rom_views.setdefault(path, {})
        view = views.get(name)

        if view is None:
            # path 가 "/World/" 로 들어와도 "//" 가 생기지 않게 한다.
            new_path = "{}/{}".format(path.rstrip("/"), name)
            view = RomPointCloud(stage, new_path)
            views[name] = view

        return view

    # ------------------------------------------------------------ 임시폴더

    @classmethod
    def _get_temp_dir(cls) -> str:
        # 밖에서 지웠을 수도 있으니 매번 실재하는지 확인한다.
        if cls._temp_dir is None or not os.path.isdir(cls._temp_dir):
            cls._temp_dir = tempfile.mkdtemp(prefix="twinsub_")
        return cls._temp_dir

    @classmethod
    def cleanup(cls) -> None:
        """받아둔 임시폴더를 지운다."""
        if cls._temp_dir:
            shutil.rmtree(cls._temp_dir, ignore_errors=True)
        cls._temp_dir = None
        cls._local_path = ""
        cls._runners = {}
        cls._rom_views = {}

    # ------------------------------------------------------------ 내부

    @staticmethod
    def _parse_s3_uri(s3_uri: str) -> tuple:
        """s3://bucket/key → (bucket, key)."""
        parsed = urlparse(s3_uri.strip())

        if parsed.scheme != "s3":
            raise ValueError("s3:// 로 시작해야 한다: {}".format(s3_uri))

        bucket = parsed.netloc
        key = parsed.path.lstrip("/")

        if not bucket or not key:
            raise ValueError("bucket 과 key 가 모두 필요하다: {}".format(s3_uri))

        return bucket, key
