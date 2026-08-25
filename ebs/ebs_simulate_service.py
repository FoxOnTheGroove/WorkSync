from .ebs_simulate import EbsSimulator, EbsResult, EbsTarget

__all__ = ["EbsSimulateService"]


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API.

    구현부(EbsSimulator)를 감싸는 유일한 진입점이다.
    오버레이/UI/외부 익스텐션은 이 클래스의 classmethod만 사용한다.
    """

    _simulator: "EbsSimulator | None" = None
    _wrappers: dict = {}           # 사용자 콜백 -> 내부 래퍼

    # ── 라이프사이클 ─────────────────────────────────────────────────────────

    @classmethod
    def initialize(cls, **params) -> None:
        """서비스를 초기화한다. 익스텐션 startup에서 1회 호출."""
        if cls._simulator is None:
            cls._simulator = EbsSimulator()
        cls._simulator.setup(**params)

    @classmethod
    def finalize(cls) -> None:
        """서비스를 정리한다. 익스텐션 shutdown에서 호출."""
        if cls._simulator is not None:
            cls._simulator.teardown()
            cls._simulator = None
        cls._wrappers.clear()

    @classmethod
    def is_initialized(cls) -> bool:
        return cls._simulator is not None

    # ── 제어 ─────────────────────────────────────────────────────────────────

    @classmethod
    def configure(cls, **params) -> None:
        """시뮬레이션 파라미터를 설정한다."""
        sim = cls._get()
        if sim:
            sim.setup(**params)

    @classmethod
    def start(cls) -> None:
        sim = cls._get()
        if sim:
            sim.start()

    @classmethod
    def stop(cls) -> None:
        sim = cls._get()
        if sim:
            sim.stop()

    @classmethod
    def reset(cls) -> None:
        sim = cls._get()
        if sim:
            sim.reset()

    @classmethod
    def step(cls, dt: float = None) -> "EbsResult | None":
        """1스텝 진행 후 결과를 반환한다."""
        sim = cls._get()
        return sim.step(dt) if sim else None

    @classmethod
    def is_running(cls) -> bool:
        sim = cls._get()
        return sim.is_running() if sim else False

    # ── 대상 ─────────────────────────────────────────────────────────────────

    @classmethod
    def add_target(cls, prim_path: str) -> "EbsTarget | None":
        sim = cls._get()
        return sim.add_target(prim_path) if sim else None

    @classmethod
    def remove_target(cls, prim_path: str) -> None:
        sim = cls._get()
        if sim:
            sim.remove_target(prim_path)

    @classmethod
    def clear_targets(cls) -> None:
        sim = cls._get()
        if sim:
            sim.clear_targets()

    @classmethod
    def get_target_paths(cls) -> list[str]:
        sim = cls._get()
        return [t.path for t in sim.get_targets()] if sim else []

    @classmethod
    def get_target_names(cls) -> list[str]:
        sim = cls._get()
        return [t.name for t in sim.get_targets()] if sim else []

    # ── 결과 조회 ────────────────────────────────────────────────────────────

    @classmethod
    def get_result(cls) -> "dict | None":
        """최근 결과를 payload(dict)로 반환한다. 없으면 None."""
        sim = cls._get()
        if sim is None:
            return None
        return cls._to_payload(sim.get_result())

    @classmethod
    def get_value(cls, prim_path: str) -> "float | None":
        """대상 프림 1개의 최근 값을 반환한다."""
        sim = cls._get()
        return sim.get_value(prim_path) if sim else None

    @classmethod
    def get_anchor(cls, prim_path: str) -> "tuple | None":
        """오버레이 앵커용 월드 좌표를 반환한다."""
        sim = cls._get()
        if sim is None:
            return None
        for target in sim.get_targets():
            if target.path == prim_path:
                return EbsSimulator._world_position(target.prim)
        return None

    # ── 구독 ─────────────────────────────────────────────────────────────────

    @classmethod
    def subscribe(cls, callback) -> None:
        """스텝 결과 콜백을 등록한다. callback(result_payload: dict)."""
        sim = cls._get()
        if sim is None or callback in cls._wrappers:
            return
        wrapper = lambda r, cb=callback: cb(cls._to_payload(r))
        cls._wrappers[callback] = wrapper
        sim.add_listener(wrapper)

    @classmethod
    def unsubscribe(cls, callback) -> None:
        wrapper = cls._wrappers.pop(callback, None)
        sim = cls._get()
        if sim and wrapper:
            sim.remove_listener(wrapper)

    # ── 내부 ─────────────────────────────────────────────────────────────────

    @classmethod
    def _get(cls) -> "EbsSimulator | None":
        if cls._simulator is None:
            print("[ebs] EbsSimulateService is not initialized")
        return cls._simulator

    @staticmethod
    def _to_payload(result: EbsResult) -> dict:
        return {
            "step": result.step,
            "time": result.time,
            "values": dict(result.values),
            "finished": result.finished,
        }
