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
    _local_path = ""
    _runner = None

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

        try:
            cls._runner = TwinRunner(path)
        except Exception:
            # 실패한 채로 이전 러너를 남겨두면 다음 동작이 옛 트윈에 걸린다.
            cls._runner = None
            raise

        cls._local_path = path
        return True

    @classmethod
    def get_runner(cls):
        """세워진 TwinRunner. 없으면 None."""
        return cls._runner

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._runner is not None

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
        cls._runner = None

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
