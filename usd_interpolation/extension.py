import omni.ext

ENABLE_DUMMY_UI   = True
ENABLE_OVERLAY_UI = True


class UsdInterpolationExtension(omni.ext.IExt):

    def on_startup(self, ext_id):
        self._ui = None
        self._overlay_mgr = None

        if ENABLE_OVERLAY_UI:
            from .dummy_overlay import OverlayManager
            self._overlay_mgr = OverlayManager()

        if ENABLE_DUMMY_UI:
            from .dummy_ui import UsdInterpolationUI
            self._ui = UsdInterpolationUI()
            self._ui._overlay_mgr = self._overlay_mgr
            self._ui.build_ui()

    def on_shutdown(self):
        if self._overlay_mgr:
            self._overlay_mgr.destroy()
            self._overlay_mgr = None
        if self._ui:
            self._ui.destroy()
            self._ui = None
