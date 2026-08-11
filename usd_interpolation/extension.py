import omni.ext

ENABLE_DUMMY_UI = True


class UsdInterpolationExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        self._ui = None

        if ENABLE_DUMMY_UI:
            from .dummy_ui import UsdInterpolationUI
            self._ui = UsdInterpolationUI()
            self._ui.build_ui()

    def on_shutdown(self):
        if self._ui:
            self._ui.destroy()
            self._ui = None

        from .UVMixer_service import UVMixerService
        UVMixerService.shutdown()
