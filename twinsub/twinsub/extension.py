"""Kit 진입점.

여기서는 UI를 세우고 내리는 것만 한다. 뷰어 동작은 twinview_service 를 통한다.
"""

import omni.ext

from .dummy_ui import TwinViewUI
from .twinview_service import unload


class TwinSubExtension(omni.ext.IExt):

    _ui = None

    def on_startup(self, ext_id):
        print("[twinsub] startup")
        self._ui = TwinViewUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[twinsub] shutdown")

        # UI보다 먼저 뷰어를 내린다. unload 가 상태 변경 훅을 때리므로
        # 그 시점엔 UI가 아직 살아 있어야 한다.
        unload()

        if self._ui:
            self._ui.destroy()
            self._ui = None
