import asyncio
import os
import shutil
from urllib.parse import urlparse

from .rom_view import RomPointCloud
from .twin_runner import TwinRunner


class TwinView:

    TEMP_DIR_NAME = "twin_temp_dir"

    S3_REGION = "us-east-1"
    S3_ACCESS_KEY_ENV = "AWS_ACCESS_KEY"
    S3_SECRET_KEY_ENV = "AWS_SECRET_KEY"
    S3_ENDPOINT_ENV = "AWS_IP"

    _runners = {}
    _files = {}
    _rom_views = {}
    _playing = set()
    _inflight = set()

    _interval = 0.5
    _update_sub = None
    _elapsed = 0.0

    _on_loaded = None
    _on_time = None
    _on_updated = None

    @staticmethod
    def normalize_key(prim_path: str) -> str:
        """prim path 를 키로 다듬는다. 절대경로가 아니면 예외."""
        key = (prim_path or "").strip().rstrip("/")
        if not key or not key.startswith("/"):
            raise ValueError("prim path 는 '/' 로 시작하는 절대경로여야 한다: {}".format(prim_path))

        return key

    @staticmethod
    def _safe_key(prim_path: str) -> str:
        """조회용. 다듬기만 하고 예외를 내지 않는다."""
        return (prim_path or "").strip().rstrip("/")

    @classmethod
    def _make_s3_client(cls):
        """환경변수로 s3 클라이언트를 만든다."""
        import boto3
        from botocore.config import Config

        access_key = os.environ.get(cls.S3_ACCESS_KEY_ENV)
        secret_key = os.environ.get(cls.S3_SECRET_KEY_ENV)

        missing = [name for name, value in ((cls.S3_ACCESS_KEY_ENV, access_key),
                                            (cls.S3_SECRET_KEY_ENV, secret_key))
                   if not value]
        if missing:
            raise ValueError("환경변수가 비어 있다: {}".format(", ".join(missing)))

        endpoint_url = os.environ.get(cls.S3_ENDPOINT_ENV) or None

        config = None
        if endpoint_url:
            config = Config(signature_version="s3v4",
                            s3={"addressing_style": "path"})

        return boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=cls.S3_REGION,
            endpoint_url=endpoint_url,
            config=config,
        )

    @classmethod
    def _prepare_download(cls, s3_uri: str):
        """받기 전 준비를 끝낸다. 여기까진 네트워크를 안 탄다."""
        bucket, key = cls._parse_s3_uri(s3_uri)

        name = os.path.basename(key)
        if not name:
            raise ValueError("s3 uri 가 파일이 아니라 폴더를 가리킨다: {}".format(s3_uri))

        client = cls._make_s3_client()
        local_path = os.path.join(cls._get_temp_dir(), name)
        return client, bucket, key, local_path

    @classmethod
    def download_file(cls, s3_uri: str) -> str:
        """s3 의 .twin 을 폴더에 받고 로컬 경로를 반환한다."""
        client, bucket, key, local_path = cls._prepare_download(s3_uri)
        client.download_file(bucket, key, local_path)

        return local_path

    @classmethod
    async def download_file_async(cls, s3_uri: str) -> str:
        """전송만 워커 스레드로 넘긴다."""
        client, bucket, key, local_path = cls._prepare_download(s3_uri)
        await asyncio.to_thread(client.download_file, bucket, key, local_path)

        return local_path

    @classmethod
    def discard_temp(cls, path: str) -> bool:
        """받아둔 파일을 지운다. 우리 폴더 바로 아래의 것만 건드린다."""
        if not path:
            return False

        temp_dir = os.path.abspath(cls._temp_dir_path())
        if os.path.dirname(os.path.abspath(path)) != temp_dir:
            return False

        try:
            os.remove(path)
        except OSError:
            return False

        return True

    @classmethod
    def load_twin(cls, key: str, path: str) -> str:
        """key 자리에 러너를 세운다. 파일이 같아도 key 가 다르면 별개 러너다."""
        key = cls.normalize_key(key)

        path = path.strip()
        if not path:
            raise ValueError("경로가 비어 있다")

        runner = cls._runners.get(key)
        if runner is not None and cls._files.get(key) != path:
            cls.unload(key)
            runner = None

        if runner is None:
            if not os.path.isfile(path):
                raise ValueError("파일이 없다: {}".format(path))

            cls._runners[key] = TwinRunner(path)
            cls._files[key] = path

        return key

    @classmethod
    def unload(cls, key: str) -> bool:
        """key 하나를 멈추고 닫고 버린다. 없으면 False."""
        key = cls._safe_key(key)

        runner = cls._runners.pop(key, None)
        if runner is None:
            return False

        cls._close_runner(key, runner)

        cls._files.pop(key, None)
        cls._rom_views.pop(key, None)
        cls._playing.discard(key)

        if not cls._playing:
            cls._stop_ticking()

        return True

    @classmethod
    def list_keys(cls) -> list:
        """등록된 key(prim path) 목록."""
        return list(cls._runners)

    @classmethod
    def begin_task(cls, key) -> bool:
        """같은 작업이 이미 돌고 있으면 False."""
        if key in cls._inflight:
            return False

        cls._inflight.add(key)
        return True

    @classmethod
    def end_task(cls, key) -> None:
        """진행 중 표시를 놓는다."""
        cls._inflight.discard(key)

    @classmethod
    def get_runner(cls, key: str) -> object:
        """key 의 러너. 없으면 None."""
        return cls._runners.get(cls._safe_key(key))

    @classmethod
    def is_loaded(cls, key: str) -> bool:
        """key 에 러너가 물려 있는지."""
        return cls.get_runner(key) is not None

    @classmethod
    def get_file_path(cls, key: str) -> str:
        """key 가 물고 있는 .twin 경로. 없으면 빈 문자열."""
        return cls._files.get(cls._safe_key(key), "")

    @classmethod
    def get_inputs(cls, runner) -> dict:
        """입력 이름 → 현재값. 사본이다."""
        return dict(runner.inputs)

    @classmethod
    def get_outputs(cls, runner) -> dict:
        """출력 이름 → 마지막 값. 사본이다."""
        return dict(runner.outputs)

    @classmethod
    def set_input(cls, runner, name: str, value: float) -> None:
        """입력 하나를 바꾼다."""
        runner.set_input(name, value)

    @classmethod
    def get_step_size(cls, runner) -> float:
        """트윈의 step size."""
        return float(runner.step_size)

    @classmethod
    def set_step_size(cls, runner, value: float) -> None:
        """step size 를 바꾼다. 양수가 아니면 예외."""
        value = float(value)
        if value <= 0.0:
            raise ValueError("step size 는 양수여야 한다: {}".format(value))

        runner.step_size = value

    @classmethod
    def get_simulation_time(cls, runner) -> float:
        """트윈 내부 시뮬레이션 시각(초)."""
        return float(runner.evaluation_time)

    @classmethod
    def get_deform_scale(cls, runner, rom_name: str = "") -> float:
        """rom 의 변형 스케일. 설정이 없으면 1.0."""
        rom_name = rom_name or runner.tbrom_names[0]
        return float(runner.rom_deform_scale.get(rom_name, 1.0))

    @classmethod
    def set_deform_scale(cls, runner, value: float, rom_name: str = "") -> None:
        """rom 의 변형 스케일을 바꾼다."""
        rom_name = rom_name or runner.tbrom_names[0]
        runner.set_rom_deform_scale(rom_name, float(value))

    @classmethod
    def rom_show(cls, key: str, runner, pos=None) -> bool:
        """key 자리에 rom 포인트 클라우드를 띄운다. pos 를 주면 그 자리에."""
        key = cls.normalize_key(key)
        rom_name = runner.tbrom_names[0]

        if not runner.set_rom_selected(rom_name, True):
            return False

        view = cls._get_rom_view(key)
        if view is not None:
            view.ensure_prim(runner.rom_points[rom_name])
            cls._ensure_xform(key, pos)

        return True

    @classmethod
    def _ensure_xform(cls, prim_path: str, pos=None) -> bool:
        """xformOp 를 단다. pos 를 주면 translate 를 그 값으로 덮는다."""
        from pxr import Gf, UsdGeom

        stage = cls._get_stage()
        if stage is None:
            return False

        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return False

        xform = UsdGeom.Xformable(prim)
        if not xform:
            return False

        ops = xform.GetOrderedXformOps()
        if not ops:
            xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(0.0, 0.0, 0.0))
            xform.AddScaleOp().Set(Gf.Vec3f(1.0, 1.0, 1.0))
            ops = xform.GetOrderedXformOps()

        if pos is not None:
            cls._set_translate(ops, Gf.Vec3d(*cls._as_vec3(pos)))

        return True

    @staticmethod
    def _as_vec3(pos) -> tuple:
        """pos 를 (x, y, z) 로 만든다. 셋이 아니면 예외."""
        values = tuple(float(v) for v in pos)
        if len(values) != 3:
            raise ValueError("pos 는 값 세 개여야 한다: {}".format(pos))
        return values

    @staticmethod
    def _set_translate(ops, value) -> bool:
        """xformOp 목록에서 translate 를 찾아 값을 넣는다."""
        for op in ops:
            if op.GetOpName().endswith("translate"):
                op.Set(value)
                return True
        return False

    @classmethod
    def update_model(cls, key: str, runner, scale: float = None) -> None:
        """띄워둔 뷰에 최신 필드를 밀어 넣는다."""
        view = cls._rom_views.get(cls._safe_key(key))
        if view is None:
            return

        rom_name = runner.tbrom_names[0]

        field = runner.get_rom_field(rom_name)
        if field is not None:
            values, dim = field
            if scale is None:
                scale = cls.get_deform_scale(runner, rom_name)
            view.update_field(values, dim, scale)

    @classmethod
    def _get_rom_view(cls, key: str):
        """key 에 물린 뷰를 준다. 없으면 만든다. key 가 곧 prim path 다."""
        stage = cls._get_stage()
        if stage is None:
            return None

        view = cls._rom_views.get(key)
        if view is None:
            view = RomPointCloud(stage, key)
            cls._rom_views[key] = view

        return view

    @staticmethod
    def _get_stage():
        """현재 USD 스테이지. 안 열려 있으면 None."""
        import omni.usd

        return omni.usd.get_context().get_stage()

    @classmethod
    def play(cls, key: str, runner) -> bool:
        """러너를 돌린다. 이미 재생 중이면 넘긴다."""
        key = cls._safe_key(key)
        if key in cls._playing:
            return False

        runner.start()
        cls._playing.add(key)
        cls._start_ticking()
        return True

    @classmethod
    def stop(cls, key: str, runner) -> bool:
        """러너를 멈춘다. 재생 중이 아니면 넘긴다."""
        key = cls._safe_key(key)
        if key not in cls._playing:
            return False

        runner.stop()
        cls._playing.discard(key)

        if not cls._playing:
            cls._stop_ticking()

        return True

    @classmethod
    def is_playing(cls, key: str) -> bool:
        """key 가 재생 중인지."""
        return cls._safe_key(key) in cls._playing

    @classmethod
    def set_interval(cls, seconds: float) -> None:
        """재생 중 필드를 다시 읽는 주기(초)."""
        cls._interval = max(0.0, float(seconds))

    @classmethod
    def get_interval(cls) -> float:
        """필드 갱신 주기(초)."""
        return cls._interval

    @classmethod
    def _start_ticking(cls):
        """Kit 업데이트 스트림을 구독한다. 이미 구독 중이면 넘긴다."""
        if cls._update_sub is not None:
            return

        import omni.kit.app

        cls._elapsed = 0.0
        cls._update_sub = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(cls._on_update, name="twinsub update")
        )

    @classmethod
    def _stop_ticking(cls):
        """구독을 놓는다. 놓으면 해지된다."""
        cls._update_sub = None
        cls._elapsed = 0.0

    @classmethod
    def _on_update(cls, event):
        """매 프레임. 주기를 채우면 재생 중인 key 를 모두 갱신한다."""
        try:
            dt = event.payload["dt"]
        except (KeyError, TypeError):
            dt = 0.0

        cls._elapsed += dt
        if cls._elapsed >= cls._interval:
            cls._elapsed = 0.0

            for key in list(cls._playing):
                runner = cls._runners.get(key)
                if runner is None:
                    continue

                try:
                    cls.update_model(key, runner)
                except Exception as exc:  # noqa: BLE001
                    print("[twinviewer] update 실패 ({}): {}".format(key, exc))

            cls._notify(cls._on_updated, "on_updated")

        cls._notify(cls._on_time, "on_time")

    @classmethod
    def _notify(cls, callback, what):
        """구독자를 부른다. 예외는 삼킨다 — 구독이 죽으면 재생이 멈춘다."""
        if callback is None:
            return

        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            print("[twinviewer] {} 콜백 실패: {}".format(what, exc))

    @classmethod
    def _temp_dir_path(cls) -> str:
        """받아둔 .twin 을 두는 홈 아래 폴더 경로."""
        return os.path.join(os.path.expanduser("~"), cls.TEMP_DIR_NAME)

    @classmethod
    def _get_temp_dir(cls) -> str:
        """그 폴더를 보장하고 경로를 준다."""
        temp_dir = cls._temp_dir_path()
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    @classmethod
    def _close_runner(cls, key: str, runner) -> None:
        """러너 하나를 멈추고 닫는다. 터져도 삼킨다."""
        try:
            if key in cls._playing:
                runner.stop()
            runner.close()
        except Exception as exc:  # noqa: BLE001
            print("[twinviewer] close 실패 ({}): {}".format(key, exc))

    @classmethod
    def cleanup(cls) -> None:
        """폴더를 지우고 러너를 모두 닫고 뷰/재생상태를 버린다."""
        cls._stop_ticking()

        for key, runner in cls._runners.items():
            cls._close_runner(key, runner)

        shutil.rmtree(cls._temp_dir_path(), ignore_errors=True)

        cls._runners = {}
        cls._files = {}
        cls._rom_views = {}
        cls._playing = set()

    @staticmethod
    def _parse_s3_uri(s3_uri: str) -> tuple:
        """s3://bucket/key → (bucket, key). s3:// 는 생략해도 된다."""
        text = s3_uri.strip()
        if not text:
            raise ValueError("경로가 비어 있다")

        parsed = urlparse(text)

        if parsed.scheme == "s3":
            bucket = parsed.netloc
            key = parsed.path.lstrip("/")
            if not bucket:
                bucket, _, key = key.partition("/")

        elif parsed.scheme:
            raise ValueError("s3 경로가 아니다: {}".format(s3_uri))

        else:
            bucket, _, key = text.lstrip("/").partition("/")

        if not bucket or not key:
            raise ValueError("bucket 과 key 가 모두 필요하다: {}".format(s3_uri))

        return bucket, key
