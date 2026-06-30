"""
Progress Panel Extension - entry point (깡통)

다른 익스텐션/로직이 자신의 진행도를 표기할 때 사용하는 progress 오버레이 제공.
공개 API 는 progresspanel_service.ProgressPanelService 참조.
"""

import omni.ext
from .dummy_ui import ProgressPanelDemoUI


class ProgressPanelExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[progress_panel] startup")
        self._ui = ProgressPanelDemoUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[progress_panel] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
