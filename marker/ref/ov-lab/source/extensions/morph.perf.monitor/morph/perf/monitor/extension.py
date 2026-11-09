"""morph.perf.monitor 익스텐션 진입점."""

import omni.ext

from . import monitor as _mon


class PerfMonitorExtension(omni.ext.IExt):
    """startup에서 Monitor 싱글턴을 만들고, shutdown에서 모든 구독을 해제한다."""

    def on_startup(self, _ext_id):
        _mon._install()

    def on_shutdown(self):
        _mon._uninstall()
