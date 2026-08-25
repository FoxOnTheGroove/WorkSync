from .ebs_simulate import EbsSimulate

__all__ = ["EbsSimulateService"]


class EbsSimulateService:
    """EBS 시뮬레이션 공개 API. 구현부(EbsSimulate) 접근은 이 클래스로만."""

    _simulate = None

    @classmethod
    def initialize(cls):
        cls._simulate = EbsSimulate()

    @classmethod
    def finalize(cls):
        cls._simulate = None

    @classmethod
    def start(cls):
        pass

    @classmethod
    def stop(cls):
        pass

    @classmethod
    def reset(cls):
        pass

    @classmethod
    def step(cls):
        pass

    @classmethod
    def get_result(cls):
        pass
