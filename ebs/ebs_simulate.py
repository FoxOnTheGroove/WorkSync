from dataclasses import dataclass, field

from pxr import Usd, UsdGeom
import omni.usd

__all__ = ["EbsSimulator", "EbsResult", "EbsTarget"]


@dataclass
class EbsTarget:
    """시뮬레이션 대상 프림 1개."""
    path: str
    name: str
    prim: object = None          # Usd.Prim
    enabled: bool = True


@dataclass
class EbsResult:
    """한 스텝의 시뮬레이션 결과."""
    step: int = 0
    time: float = 0.0
    values: dict = field(default_factory=dict)   # prim_path -> float
    finished: bool = False


class EbsSimulator:
    """EBS 시뮬레이션 구현부.

    상태 보관과 계산만 담당하며, 외부에서는 EbsSimulateService를 통해 접근한다.
    UI/이벤트/오버레이는 이 클래스를 직접 참조하지 않는다.
    """

    DEFAULT_DT = 1.0 / 60.0

    def __init__(self):
        self._targets: dict[str, EbsTarget] = {}   # path -> EbsTarget
        self._params: dict = {}                    # 시뮬레이션 파라미터
        self._result: EbsResult = EbsResult()
        self._running: bool = False
        self._step: int = 0
        self._time: float = 0.0
        self._dt: float = self.DEFAULT_DT
        self._listeners: list = []                 # callable(EbsResult)

    # ── 라이프사이클 ─────────────────────────────────────────────────────────

    def setup(self, **params) -> None:
        """파라미터를 설정하고 내부 상태를 초기 상태로 되돌린다."""
        self._params.update(params)
        self.reset()

    def reset(self) -> None:
        self._running = False
        self._step = 0
        self._time = 0.0
        self._result = EbsResult()

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def teardown(self) -> None:
        self.stop()
        self._targets.clear()
        self._listeners.clear()
        self._result = EbsResult()

    # ── 대상 관리 ────────────────────────────────────────────────────────────

    def add_target(self, prim_path: str) -> "EbsTarget | None":
        stage = self._get_stage()
        if stage is None:
            return None
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return None
        target = EbsTarget(path=prim_path, name=prim.GetName(), prim=prim)
        self._targets[prim_path] = target
        return target

    def remove_target(self, prim_path: str) -> None:
        self._targets.pop(prim_path, None)

    def clear_targets(self) -> None:
        self._targets.clear()

    def get_targets(self) -> list[EbsTarget]:
        return list(self._targets.values())

    # ── 시뮬레이션 ───────────────────────────────────────────────────────────

    def step(self, dt: float = None) -> EbsResult:
        """dt만큼 시뮬레이션을 1스텝 진행하고 결과를 반환한다."""
        if not self._running:
            return self._result

        dt = self._dt if dt is None else dt
        self._step += 1
        self._time += dt

        values = {}
        for target in self._targets.values():
            if not target.enabled:
                continue
            values[target.path] = self._compute(target, dt)

        self._result = EbsResult(
            step=self._step,
            time=self._time,
            values=values,
            finished=self._is_finished(),
        )
        if self._result.finished:
            self._running = False
        self._notify(self._result)
        return self._result

    def get_result(self) -> EbsResult:
        return self._result

    def get_value(self, prim_path: str) -> "float | None":
        return self._result.values.get(prim_path)

    def is_running(self) -> bool:
        return self._running

    def get_params(self) -> dict:
        return dict(self._params)

    # ── 결과 구독 ────────────────────────────────────────────────────────────

    def add_listener(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self, result: EbsResult) -> None:
        for callback in list(self._listeners):
            try:
                callback(result)
            except Exception as e:
                print(f"[ebs] listener error: {e}")

    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _compute(self, target: EbsTarget, dt: float) -> float:
        """대상 1개의 스텝 값을 계산한다. (구현 예정)"""
        return 0.0

    def _is_finished(self) -> bool:
        """종료 조건 판정. (구현 예정)"""
        return False

    @staticmethod
    def _get_stage() -> "Usd.Stage | None":
        return omni.usd.get_context().get_stage()

    @staticmethod
    def _world_position(prim: Usd.Prim) -> "tuple | None":
        """프림의 월드 좌표를 (x, y, z)로 반환. 오버레이 앵커 용도."""
        if prim is None or not prim.IsValid():
            return None
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return None
        m = xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        t = m.ExtractTranslation()
        return (t[0], t[1], t[2])
