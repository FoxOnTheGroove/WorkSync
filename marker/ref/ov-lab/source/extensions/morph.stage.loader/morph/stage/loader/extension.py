"""IExt: 로더·웹 브리지 수명 관리, startupScene 처리."""

from __future__ import annotations

import asyncio
import os

import carb
import carb.settings
import omni.ext
import omni.kit.app

from . import loader as _loader
from .loader import EXT_ID, S_STARTUP_SCENE, StageLoader, StageLoadError
from .web import WebBridge, push_state


class MorphStageLoaderExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        ext_path = omni.kit.app.get_app().get_extension_manager().get_extension_path(ext_id)
        samples_dir = os.path.join(ext_path, "data", "scenes") if ext_path else None
        self._loader = StageLoader(samples_dir)
        _loader._set_instance(self._loader)
        self._loader._add_state_listener(push_state)
        self._web = WebBridge()
        self._startup_task = asyncio.ensure_future(self._open_startup_scene())

    async def _open_startup_scene(self):
        scene_id = carb.settings.get_settings().get(S_STARTUP_SCENE)
        if not isinstance(scene_id, str) or not scene_id.strip():
            return
        scene_id = scene_id.strip()
        await omni.kit.app.get_app().next_update_async()
        loader = _loader.get_loader()
        if loader is None:
            return
        if loader.find_scene(scene_id) is None:
            carb.log_warn(f"[{EXT_ID}] startupScene 목록에 없음: {scene_id}")
            return
        try:
            await loader.open_scene(scene_id)
        except (KeyError, RuntimeError, StageLoadError) as e:
            carb.log_warn(f"[{EXT_ID}] startupScene 열기 실패: {scene_id}: {type(e).__name__}: {e}")

    def on_shutdown(self):
        if self._startup_task is not None and not self._startup_task.done():
            self._startup_task.cancel()
        self._startup_task = None
        self._web.destroy()
        self._web = None
        _loader._set_instance(None)
        self._loader._destroy()
        self._loader = None
