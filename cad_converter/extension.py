"""
CAD Converter Extension - entry point

Extension Manager 에서 활성화 시 CAD Converter 윈도우를 띄움.
사전 조건: omni.kit.converter.hoops_core 활성화
"""

import omni.ext
from .dummy_ui import CadConverterUI


class CadConverterExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        print("[cad_converter] startup")
        self._ui = CadConverterUI()
        self._ui.build_ui()

    def on_shutdown(self):
        print("[cad_converter] shutdown")
        if self._ui:
            self._ui.destroy()
            self._ui = None
