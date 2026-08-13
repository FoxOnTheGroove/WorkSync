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

    # 재생 중인 twin path. 러너마다 따로 봐야 하므로 플래그 하나로 두지 않는다.
    _playing = set()  # type: set

    # 진행 중인 비동기 작업 키. 같은 작업이 겹쳐 도는 것을 막는다.
    _inflight = set()  # type: set

    # twin path → 그 트윈을 띄운 prim path. 업데이트 때 어느 뷰를 만질지 찾는다.
    _prim_paths = {}  # type: dict

    # 재생 중 필드를 다시 읽는 주기(초).
    _interval = 0.5

    # Kit 업데이트 구독. 재생 중인 트윈이 하나라도 있을 때만 살아 있다.
    _update_sub = None
    _elapsed = 0.0

    # 로드/표시 상태가 바뀔 때 부른다. UI 가 자기 버튼 밖의 변화를 따라가는 신호다.
    _on_loaded = None

    # 매 프레임 부른다. 시뮬레이션 시각처럼 싸게 읽는 값만 여기서 갱신한다.
    _on_time = None

    # 주기를 채워 필드를 갱신한 뒤 부른다. 무거운 값은 여기서 다시 읽는다.
    _on_updated = None

    # prim path → {rom name → RomPointCloud}
    # prim path 별로 나눠 담아야 같은 rom 을 여러 경로 아래에 따로 띄울 수 있다.
    _rom_views = {}  # type: dict

    # s3 접속 정보. region 만 고정이고 나머지는 환경변수에서 온다.
    S3_REGION = "us-east-1"
    S3_ACCESS_KEY_ENV = "AWS_ACCESS_KEY"
    S3_SECRET_KEY_ENV = "AWS_SECRET_KEY"
    S3_ENDPOINT_ENV = "AWS_IP"

    # ------------------------------------------------------------ 다운로드

    @classmethod
    def _make_s3_client(cls):
        """환경변수로 s3 클라이언트를 만든다.

        키가 없으면 어느 환경변수가 비었는지 짚어서 올린다. 그대로 두면
        boto3 가 한참 뒤에 NoCredentialsError 로 터져 원인이 안 보인다.
        """
        # boto3 는 Kit 기동 시점에 없을 수 있다. 모듈 import 를 막지 않도록 늦게 올린다.
        import boto3

        access_key = os.environ.get(cls.S3_ACCESS_KEY_ENV)
        secret_key = os.environ.get(cls.S3_SECRET_KEY_ENV)

        missing = [name for name, value in ((cls.S3_ACCESS_KEY_ENV, access_key),
                                            (cls.S3_SECRET_KEY_ENV, secret_key))
                   if not value]
        if missing:
            raise ValueError("환경변수가 비어 있다: {}".format(", ".join(missing)))

        return boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=cls.S3_REGION,
            # 안 잡혀 있으면 None 을 넘겨 기본 AWS 엔드포인트를 쓴다.
            endpoint_url=os.environ.get(cls.S3_ENDPOINT_ENV) or None,
        )

    @classmethod
    def download_twin(cls, s3_uri: str) -> str:
        """s3 uri 의 .twin 을 임시폴더에 받고 로컬 경로를 반환한다.

        실패하면 예외를 올린다 — 실패 이유를 UI에 그대로 보여주기 위해서다.
        """
        bucket, key = cls._parse_s3_uri(s3_uri)

        name = os.path.basename(key)
        if not name:
            raise ValueError("s3 uri 가 파일이 아니라 폴더를 가리킨다: {}".format(s3_uri))

        local_path = os.path.join(cls._get_temp_dir(), name)
        cls._make_s3_client().download_file(bucket, key, local_path)

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
    def begin_task(cls, key) -> bool:
        """같은 작업이 이미 돌고 있으면 False.

        느린 작업이 비동기로 나가면 버튼을 두 번 눌러 두 번 도는 일이 생긴다.
        같은 경로를 두 번 받으면 같은 파일에 동시에 쓴다.
        """
        if key in cls._inflight:
            return False

        cls._inflight.add(key)
        return True

    @classmethod
    def end_task(cls, key) -> None:
        cls._inflight.discard(key)

    @classmethod
    def get_runner(cls, path: str = "") -> object:
        """path 의 TwinRunner. path 를 비우면 현재 대상. 없으면 None."""
        return cls._runners.get(path or cls._local_path)

    @classmethod
    def is_loaded(cls) -> bool:
        return cls.get_runner() is not None

    # ------------------------------------------------------------ 모델 정보

    @classmethod
    def get_inputs(cls, runner) -> dict:
        """입력 이름 → 현재값. 사본이라 여기 손대도 러너는 안 바뀐다."""
        return dict(runner.inputs)

    @classmethod
    def get_outputs(cls, runner) -> dict:
        """출력 이름 → 마지막 값. 사본이다."""
        return dict(runner.outputs)

    @classmethod
    def set_input(cls, runner, name: str, value: float) -> None:
        runner.set_input(name, value)

    @classmethod
    def get_step_size(cls, runner) -> float:
        return float(runner.step_size)

    @classmethod
    def set_step_size(cls, runner, value: float) -> None:
        value = float(value)
        if value <= 0.0:
            raise ValueError("step size 는 양수여야 한다: {}".format(value))

        runner.step_size = value

    @classmethod
    def get_simulation_time(cls, runner) -> float:
        return float(runner.evaluation_time)

    @classmethod
    def get_deform_scale(cls, runner, rom_name: str = "") -> float:
        """rom 의 변형 스케일. 설정이 없으면 1.0."""
        rom_name = rom_name or runner.tbrom_names[0]
        return float(runner.rom_deform_scale.get(rom_name, 1.0))

    @classmethod
    def set_deform_scale(cls, runner, value: float, rom_name: str = "") -> None:
        rom_name = rom_name or runner.tbrom_names[0]
        runner.set_rom_deform_scale(rom_name, float(value))

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
            cls._ensure_xform(cls._rom_prim_path(path, rom_name))

        # 재생 중 업데이트가 어느 prim 아래를 갱신할지 여기서 정해진다.
        # 항상 현재 대상에 대해 불리므로 _local_path 로 물린다.
        cls._prim_paths[cls._local_path] = path
        return True

    @classmethod
    def _ensure_xform(cls, prim_path: str) -> bool:
        """포인트 프림에 xformOp 를 달아 옮길 수 있게 한다.

        Points 는 Xformable 을 상속하므로 부모 Xform 을 따로 두지 않아도 된다.
        ensure_prim 이 만든 프림에는 xformOp 가 없어 트랜스폼이 안 잡힌다.

        이미 있으면 건드리지 않는다 — 뷰포트에서 옮겨둔 것을 Show 가 되돌리면
        안 된다.
        """
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

        if xform.GetOrderedXformOps():
            return True

        xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))
        xform.AddRotateXYZOp().Set(Gf.Vec3f(0.0, 0.0, 0.0))
        xform.AddScaleOp().Set(Gf.Vec3f(1.0, 1.0, 1.0))
        return True

    @classmethod
    def update_model(cls, path: str, runner, scale: float = None) -> None:
        """띄워둔 뷰에 최신 필드를 밀어 넣는다."""
        rom_name = runner.tbrom_names[0]

        views = cls._rom_views.get(path)
        if views is None:
            return

        view = views.get(rom_name)
        if view is None:
            return

        field = runner.get_rom_field(rom_name)
        if field is not None:
            values, dim = field
            if scale is None:
                scale = cls.get_deform_scale(runner, rom_name)
            view.update_field(values, dim, scale)

    # ------------------------------------------------------------ 재생

    @classmethod
    def play(cls, path: str, runner) -> bool:
        """이미 재생 중이면 넘긴다."""
        if path in cls._playing:
            return False

        runner.start()
        cls._playing.add(path)
        cls._start_ticking()
        return True

    @classmethod
    def stop(cls, path: str, runner) -> bool:
        """재생 중이 아니면 넘긴다."""
        if path not in cls._playing:
            return False

        runner.stop()
        cls._playing.discard(path)

        # 마지막 하나가 멈추면 구독을 놓는다. 안 그러면 매 프레임 헛돈다.
        if not cls._playing:
            cls._stop_ticking()

        return True

    @classmethod
    def get_prim_path(cls, path: str = "") -> str:
        """그 트윈을 띄운 prim path. 안 띄웠으면 빈 문자열."""
        return cls._prim_paths.get(path or cls._local_path, "")

    @classmethod
    def is_playing(cls, path: str = "") -> bool:
        """path 가 재생 중인지. path 를 비우면 현재 대상."""
        return (path or cls._local_path) in cls._playing

    @classmethod
    def set_interval(cls, seconds: float) -> None:
        """재생 중 필드를 다시 읽는 주기(초)."""
        cls._interval = max(0.0, float(seconds))

    @classmethod
    def get_interval(cls) -> float:
        return cls._interval

    # ------------------------------------------------------------ 업데이트 틱

    @classmethod
    def _start_ticking(cls):
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
        # 구독 객체를 놓으면 해지된다.
        cls._update_sub = None
        cls._elapsed = 0.0

    @classmethod
    def _on_update(cls, event):
        try:
            dt = event.payload["dt"]
        except (KeyError, TypeError):
            dt = 0.0

        cls._elapsed += dt
        if cls._elapsed >= cls._interval:
            cls._elapsed = 0.0

            # 콜백 안에서 stop 이 불릴 수 있으므로 사본을 돈다.
            for twin_path in list(cls._playing):
                runner = cls._runners.get(twin_path)
                prim_path = cls._prim_paths.get(twin_path)
                if runner is None or prim_path is None:
                    continue

                # 한 트윈이 터져도 나머지 재생은 계속 가야 한다.
                try:
                    cls.update_model(prim_path, runner)
                except Exception as exc:  # noqa: BLE001
                    print("[twinsub] update 실패 ({}): {}".format(twin_path, exc))

            cls._notify(cls._on_updated, "on_updated")

        # 시간은 매 프레임 알린다. 읽기만 하므로 싸고, 주기에 묶어두면
        # 0.5초씩 튀어서 재생이 멈춘 것처럼 보인다.
        # 필드 갱신 뒤에 부른다 — 앞에서 부르면 같은 프레임에 진행된 시간을
        # 한 틱 늦게 보여준다.
        cls._notify(cls._on_time, "on_time")

    @classmethod
    def _notify(cls, callback, what):
        """콜백에서 터진 예외가 구독을 죽이면 재생이 멈춘다. 삼킨다."""
        if callback is None:
            return

        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            print("[twinsub] {} 콜백 실패: {}".format(what, exc))

    @classmethod
    def _get_rom_view(cls, path: str, name: str):
        """(prim path, rom 이름) 에 물린 RomPointCloud 를 준다. 없으면 만든다.

        stage 를 못 찾으면 None. 스테이지가 아직 안 열린 상태에서 눌린 경우다.
        """
        stage = cls._get_stage()
        if stage is None:
            return None

        views = cls._rom_views.setdefault(path, {})
        view = views.get(name)

        if view is None:
            view = RomPointCloud(stage, cls._rom_prim_path(path, name))
            views[name] = view

        return view

    @staticmethod
    def _rom_prim_path(path: str, name: str) -> str:
        """path 가 "/World/" 로 들어와도 "//" 가 생기지 않게 한다."""
        return "{}/{}".format(path.rstrip("/"), name)

    @staticmethod
    def _get_stage():
        import omni.usd

        return omni.usd.get_context().get_stage()

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
        cls._stop_ticking()
        cls._local_path = ""
        cls._runners = {}
        cls._rom_views = {}
        cls._playing = set()
        cls._prim_paths = {}

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
