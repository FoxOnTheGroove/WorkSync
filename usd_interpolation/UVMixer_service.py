from typing import Callable

from .UVMixer import UVMixer
from .UVMixer_player import UVMixerPlayer


class TabContext:
    """탭 1개의 상태: sync 여부, 공유 플레이어, 소속 mixer key 목록."""

    def __init__(self, tab_id: str):
        self.tab_id: str = tab_id
        self.sync: bool = True
        self.shared_player: UVMixerPlayer = UVMixerPlayer()
        self.keys: list[str] = []


class UVMixerService:
    _instances: dict[str, UVMixer] = {}
    _tab_contexts: dict[str, TabContext] = {}
    _key_tab: dict[str, str] = {}                   # key → tab_id
    _active_tab: 'str | None' = None                # on_tab_changed로 갱신, panel_on 시 visible 판단용
    _panel_mgr = None                               # UVMixer_overlay.OverlayManager | None (lazy)

    # ── 팩토리 ──────────────────────────────────────────────────
    @classmethod
    def create(cls, target_path: 'str | None', key: str, tab_id: str) -> UVMixer:
        """빈 UVMixer를 만들어 key로 등록한다. 소스는 load(key, paths)로 주입.
        target_path가 None이면 소스 경로를 그대로 사용(remap 없음).
        tab_id의 TabContext에 등록되며, 해당 탭의 sync 상태에 따라
        탭의 shared_player에 즉시 합류한다."""
        mixer = UVMixer.create(target_path)
        cls._instances[key] = mixer
        cls._key_tab[key] = tab_id

        tab_ctx = cls._tab_contexts.get(tab_id)
        if tab_ctx is None:
            tab_ctx = TabContext(tab_id)
            cls._tab_contexts[tab_id] = tab_ctx
        if key not in tab_ctx.keys:
            tab_ctx.keys.append(key)

        if tab_ctx.sync:
            mixer.join_player(tab_ctx.shared_player)
        return mixer

    # ── 탭 ───────────────────────────────────────────────────────
    @classmethod
    def get_tab(cls, tab_id: str) -> 'TabContext | None':
        """tab_id에 해당하는 TabContext를 반환한다(없으면 None)."""
        return cls._tab_contexts.get(tab_id)

    @classmethod
    def get_shared_player(cls, tab_id: str) -> UVMixerPlayer:
        """tab_id의 shared_player를 반환한다(없으면 생성)."""
        tab_ctx = cls._tab_contexts.get(tab_id)
        if tab_ctx is None:
            tab_ctx = TabContext(tab_id)
            cls._tab_contexts[tab_id] = tab_ctx
        return tab_ctx.shared_player

    @classmethod
    def on_tab_changed(cls, entering_tab_id: str) -> None:
        """탭 전환 진입점. 외부 on_change_tab 콜백에서 새 tab_id 하나만 받아 호출한다.
        직전 탭은 내부 `_active_tab`에서 자동으로 가져온다.
        leaving 탭: 모든 mixer의 own_player/shared_player를 정지하고 패널을 숨긴다.
        entering 탭: 패널을 보이고, shared_player의 현재 t를 재적용해 타임라인을 복원한다."""
        leaving_tab_id = cls._active_tab
        if leaving_tab_id == entering_tab_id:
            leaving_tab_id = None
        leaving = cls._tab_contexts.get(leaving_tab_id) if leaving_tab_id else None
        if leaving is not None:
            for k in leaving.keys:
                mixer = cls._instances.get(k)
                if mixer:
                    mixer.own_player.stop()
                cls.panel_hide(k)
            leaving.shared_player.stop()

        cls._active_tab = entering_tab_id

        entering = cls._tab_contexts.get(entering_tab_id)
        if entering is not None:
            for k in entering.keys:
                cls.panel_show(k)
            entering.shared_player.set_t(entering.shared_player.t)

    # ── sync 제어 ────────────────────────────────────────────────
    @classmethod
    def set_sync(cls, tab_id: str, enabled: bool, ref_key: 'str | None' = None) -> bool:
        """tab_id 탭의 sync 상태를 일괄 전환한다.
        enabled=True 이고 ref_key가 해당 탭에 없는 key면 변경 없이 False 반환."""
        tab_ctx = cls._tab_contexts.get(tab_id)
        if tab_ctx is None:
            tab_ctx = TabContext(tab_id)
            cls._tab_contexts[tab_id] = tab_ctx

        if enabled and ref_key is not None and ref_key not in tab_ctx.keys:
            return False

        tab_ctx.sync = enabled
        tab_ctx.shared_player.stop()
        for k in tab_ctx.keys:
            mixer = cls._instances.get(k)
            if mixer:
                mixer.own_player.stop()

        if enabled:
            key = ref_key if ref_key and ref_key in tab_ctx.keys else None
            if key is None:
                key = tab_ctx.keys[0] if tab_ctx.keys else None
            if key:
                cls.sync(key)
        else:
            for k in tab_ctx.keys:
                cls.unsync(k)

        if cls._panel_mgr is not None:
            cls._panel_mgr.refresh_all()
        return True

    @classmethod
    def is_synced(cls, tab_id: str) -> bool:
        """tab_id 탭의 현재 sync 상태를 반환한다."""
        tab_ctx = cls._tab_contexts.get(tab_id)
        return tab_ctx.sync if tab_ctx else True

    # ── 패널 제어 (뷰포트 HUD) ───────────────────────────────────
    @classmethod
    def _get_panel_mgr(cls):
        """OverlayManager를 lazy 생성한다. omni.ui 부재(헤드리스) 시 None."""
        if cls._panel_mgr is None:
            try:
                from .UVMixer_overlay import OverlayManager
            except Exception:
                return None
            cls._panel_mgr = OverlayManager()
        return cls._panel_mgr

    @classmethod
    def panel_on(cls, key: str) -> bool:
        """key mixer의 타겟 뷰포트에 HUD 패널을 띄운다. 성공 시 True.
        key의 소속 탭이 현재 active 탭이 아니면 visible=False로 생성한다."""
        if not cls.has_instance(key):
            return False
        mgr = cls._get_panel_mgr()
        if mgr is None:
            return False
        tab_id = cls._key_tab.get(key)
        visible = cls._active_tab is None or tab_id == cls._active_tab
        mgr.on_mixer_loaded(key, cls.get_target_path(key) or "", tab_id, visible=visible)
        return mgr.is_on(key)

    @classmethod
    def panel_off(cls, key: str) -> None:
        """key mixer의 HUD 패널을 제거한다."""
        if cls._panel_mgr is not None:
            cls._panel_mgr.on_mixer_destroyed(key)

    @classmethod
    def panel_show(cls, key: str) -> None:
        """key mixer의 HUD 패널을 보이게 한다(상태는 유지, destroy 아님)."""
        if cls._panel_mgr is not None:
            cls._panel_mgr.show(key)

    @classmethod
    def panel_hide(cls, key: str) -> None:
        """key mixer의 HUD 패널을 숨긴다(상태는 유지, destroy 아님)."""
        if cls._panel_mgr is not None:
            cls._panel_mgr.hide(key)

    @classmethod
    def panel_is_on(cls, key: str) -> bool:
        """key mixer의 HUD 패널이 떠 있으면 True."""
        return cls._panel_mgr is not None and cls._panel_mgr.is_on(key)

    # ── 레지스트리 ───────────────────────────────────────────────
    @classmethod
    def keys(cls) -> 'list[str]':
        """등록된 모든 mixer key 목록을 반환한다."""
        return list(cls._instances)

    @classmethod
    def get_instance(cls, key: str) -> 'UVMixer | None':
        """key에 해당하는 mixer를 반환한다(없으면 None)."""
        return cls._instances.get(key)

    @classmethod
    def has_instance(cls, key: str) -> bool:
        """key에 해당하는 mixer가 등록되어 있으면 True."""
        return key in cls._instances

    @classmethod
    def get_mesh_paths(cls, key: str) -> 'list[str]':
        """로드된 메쉬 경로 목록을 정렬해 반환한다(미로드 시 빈 리스트)."""
        m = cls._instances.get(key)
        return sorted(m._st_maps[0].keys()) if m and m._st_maps else []

    @classmethod
    def get_target_path(cls, key: str) -> 'str | None':
        """mixer의 target_path를 반환한다."""
        m = cls._instances.get(key)
        return m._target_path if m else None

    @classmethod
    def get_tab_id(cls, key: str) -> 'str | None':
        """key mixer가 속한 tab_id를 반환한다."""
        return cls._key_tab.get(key)

    @classmethod
    def destroy(cls, key: str) -> None:
        """key의 mixer를 정지·해제하고 레지스트리·탭에서 제거한다(패널 동반 제거)."""
        mixer = cls._instances.pop(key, None)
        if mixer is not None:
            mixer.destroy()
        tab_id = cls._key_tab.pop(key, None)
        if tab_id is not None:
            tab_ctx = cls._tab_contexts.get(tab_id)
            if tab_ctx and key in tab_ctx.keys:
                tab_ctx.keys.remove(key)
        cls.panel_off(key)

    @classmethod
    def destroy_all(cls) -> None:
        """모든 mixer를 해제하고 레지스트리·탭 목록을 비운다(서비스 패널 동반 제거)."""
        for m in list(cls._instances.values()):
            m.destroy()
        cls._instances.clear()
        cls._key_tab.clear()
        for tab_ctx in cls._tab_contexts.values():
            tab_ctx.keys.clear()
            tab_ctx.shared_player.reset()
        if cls._panel_mgr is not None:
            cls._panel_mgr.clear_panels()

    @classmethod
    def shutdown(cls) -> None:
        """익스텐션 종료 시 호출. 모든 mixer·탭·플레이어를 해제 후 클래스 상태를 초기값으로 복원한다."""
        cls.destroy_all()
        if cls._panel_mgr is not None:
            cls._panel_mgr.destroy()
            cls._panel_mgr = None
        for tab_ctx in cls._tab_contexts.values():
            tab_ctx.shared_player.reset()
            tab_ctx.shared_player._tick_cbs.clear()
            tab_ctx.shared_player._stopped_cbs.clear()
        cls._tab_contexts.clear()
        cls._active_tab = None

    # ── shared_player 제어 ───────────────────────────────────────
    @classmethod
    def reapply(cls, tab_id: str) -> None:
        """tab_id의 shared_player의 현재 t를 다시 적용한다 (correction 모드 변경 후 갱신용)."""
        sp = cls.get_shared_player(tab_id)
        sp.set_t(sp.t)

    @classmethod
    def reset(cls, tab_id: str) -> None:
        """tab_id의 shared_player를 초기 상태(t=0, speed=1, forward, no-loop)로 리셋한다."""
        cls.get_shared_player(tab_id).reset()

    # ── sync ─────────────────────────────────────────────────────────
    @classmethod
    def sync(cls, reference_key: str) -> None:
        """reference_key mixer가 속한 탭의 shared_player에 t/speed/forward/loop를 복사하고,
        같은 탭의 모든 mixer를 그 shared_player에 합류시킨다."""
        tab_id = cls._key_tab.get(reference_key)
        tab_ctx = cls._tab_contexts.get(tab_id) if tab_id else None
        if tab_ctx is None:
            return
        ref = cls._instances.get(reference_key)
        if ref:
            tab_ctx.shared_player.copy_from(ref.own_player)
        for k in tab_ctx.keys:
            mixer = cls._instances.get(k)
            if mixer:
                mixer.join_player(tab_ctx.shared_player)

    @classmethod
    def unsync(cls, key: str) -> None:
        """해당 key의 mixer를 shared_player에서 떠나 own_player로 복귀시킨다."""
        mixer = cls._instances.get(key)
        if mixer:
            mixer.leave_player()

    # ── 인스턴스 위임 (key 기반) ─────────────────────────────────
    @classmethod
    def load(cls, key: str, st_paths: 'list[str]', *,
             panel_on: bool = True,
             on_done: 'Callable[[list[str]], None] | None' = None
             ) -> 'list[str]':
        """소스 USD 파일들을 mixer에 주입한다(재호출 시 재로드). 경고 목록을 반환한다.
        panel_on=True면 로드 완료 후 같은 key(viewport id)로 HUD 패널을 띄운다.
        on_done이 주어지면 로드 완료 시 경고 목록을 인자로 호출한다."""
        m = cls._instances.get(key)
        warnings = m.load(st_paths) if m else []
        if panel_on:
            cls.panel_on(key)
        if on_done is not None:
            on_done(warnings)
        return warnings

    @classmethod
    def play(cls, key: str) -> None:
        """t=0→1 보간 재생을 시작한다."""
        m = cls._instances.get(key)
        if m:
            m.play()

    @classmethod
    def stop(cls, key: str) -> None:
        """재생을 정지한다."""
        m = cls._instances.get(key)
        if m:
            m.stop()

    @classmethod
    def is_playing(cls, key: str) -> bool:
        """재생 중이면 True."""
        m = cls._instances.get(key)
        return m.is_playing() if m else False

    @classmethod
    def set_value(cls, key: str, t: float, *,
                  correction: bool = True, drive_timeline: bool = True) -> None:
        """보간 위치 t(0.0~1.0)를 설정한다."""
        m = cls._instances.get(key)
        if m:
            m.set_value(t, correction=correction, drive_timeline=drive_timeline)

    @classmethod
    def get_value(cls, key: str) -> float:
        """현재 보간 위치 t를 반환한다."""
        m = cls._instances.get(key)
        return m.get_value() if m else 0.0

    @classmethod
    def set_forward(cls, key: str, forward: bool) -> None:
        m = cls._instances.get(key)
        if m:
            m.set_forward(forward)

    @classmethod
    def set_loop(cls, key: str, loop: bool) -> None:
        m = cls._instances.get(key)
        if m:
            m.set_loop(loop)

    @classmethod
    def set_speed(cls, key: str, speed: float) -> None:
        m = cls._instances.get(key)
        if m:
            m.set_speed(speed)

    @classmethod
    def set_correction(cls, key: str, enabled: bool) -> None:
        m = cls._instances.get(key)
        if m:
            m.set_correction(enabled)

    @classmethod
    def set_correction_mode(cls, key: str, mode: str) -> None:
        """mode: 'none' | 'boundary' | 'all'"""
        m = cls._instances.get(key)
        if m:
            m.set_correction_mode(mode)

    @classmethod
    def subscribe(cls, key: str, callback: Callable[[float], None]) -> None:
        m = cls._instances.get(key)
        if m:
            m.subscribe(callback)

    @classmethod
    def unsubscribe(cls, key: str, callback: Callable[[float], None]) -> None:
        m = cls._instances.get(key)
        if m:
            m.unsubscribe(callback)
