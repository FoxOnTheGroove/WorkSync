from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateOverlay"]


class EbsSimulateOverlay:
    """Viewport overlay UI driven by the public API. Not implemented yet."""

    _instances = {}

    @classmethod
    def on(cls, vp_name):
        pass

    @classmethod
    def off(cls, vp_name):
        pass

    @classmethod
    def set_visible(cls, vp_name, visible):
        pass

    @classmethod
    def destroy(cls, vp_name=None):
        pass

    def __init__(self, vp_name):
        self._vp_name = vp_name
        self._window = None

    def _build(self):
        pass

    def _update(self):
        pass

    def _destroy(self):
        pass
