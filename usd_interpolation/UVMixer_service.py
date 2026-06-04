from typing import Callable

from .UVMixer import UVMixer
from .UVMixer_player import UVMixerPlayer


class UVMixerService:
    _instances: dict[str, UVMixer] = {}
    shared_player: UVMixerPlayer = UVMixerPlayer()  # N+1 플레이어 중 공유 플레이어 1개
    _synced: bool = True                            # sync 상태
    _panel_mgr = None                               # UVMixer_overlay.OverlayManager | None (lazy)

    # ── 팩토리 ──────────────────────────────────────────────────
    @classmethod
    def create(cls, target_path: 'str | None', key: str) -> UVMixer:
        """빈 UVMixer를 만들어 key로 등록한다. 소스는 load(key, paths)로 주입.
        target_path가 None이면 소스 경로를 그대로 사용(remap 없음).
        현재 sync 상태에 따라 shared_player에 즉시 합류한다."""
        mixer = UVMixer.create(target_path)
        cls._instances[key] = mixer
        if cls._synced:
            mixer.join_player(cls.shared_player)
        return mixer

    # ── sync 제어 ────────────────────────────────────────────────
    @classmethod
    def set_sync_all(cls, enabled: bool, ref_key: 'str | None' = None) -> bool:
        """모든 mixer의 sync 상태를 일괄 전환한다.
        enabled=True 이고 ref_key가 등록되지 않은 key면 변경 없이 False 반환."""
        if enabled and ref_key is not None and ref_key not in cls._instances:
            return False
        cls._synced = enabled
        if enabled:
            cls.shared_player.stop()
            for mixer in cls._instances.values():
                mixer.own_player.stop()
            key = ref_key if ref_key and ref_key in cls._instances else None
            if key is None:
                keys = list(cls._instances)
                key = keys[0] if keys else None
            if key:
                cls.sync(key)
        else:
            cls.shared_player.stop()
            for mixer in cls._instances.values():
                mixer.own_player.stop()
            for k in list(cls._instances):
                cls.unsync(k)
        if cls._panel_mgr is not None:
            cls._panel_mgr.refresh_all()
        return True

    @classmethod
    def is_synced(cls) -> bool:
        """현재 sync 상태를 반환한다."""
        return cls._synced

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
        """key mixer의 타겟 뷰포트에 HUD 패널을 띄운다. 성공 시 True."""
        if not cls.has_instance(key):
            return False
        mgr = cls._get_panel_mgr()
        if mgr is None:
            return False
        mgr.on_mixer_loaded(key, cls.get_target_path(key) or "")
        return mgr.is_on(key)

    @classmethod
    def panel_off(cls, key: str) -> None:
        """key mixer의 HUD 패널을 제거한다."""
        if cls._panel_mgr is not None:
            cls._panel_mgr.on_mixer_destroyed(key)

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
    def destroy(cls, key: str) -> None:
        """key의 mixer를 정지·해제하고 레지스트리에서 제거한다(패널 동반 제거)."""
        mixer = cls._instances.pop(key, None)
        if mixer is not None:
            mixer.destroy()
        cls.panel_off(key)

    @classmethod
    def destroy_all(cls) -> None:
        """모든 mixer를 해제하고 레지스트리를 비운다(서비스 패널 동반 제거)."""
        for m in list(cls._instances.values()):
            m.destroy()
        cls._instances.clear()
        if cls._panel_mgr is not None:
            cls._panel_mgr.clear_panels()

    @classmethod
    def shutdown(cls) -> None:
        """익스텐션 종료 시 호출. 모든 mixer 해제 후 클래스 상태를 초기값으로 복원한다."""
        cls.destroy_all()
        if cls._panel_mgr is not None:
            cls._panel_mgr.destroy()
            cls._panel_mgr = None
        cls.shared_player.reset()
        cls.shared_player._tick_cbs.clear()
        cls.shared_player._stopped_cbs.clear()
        cls._synced = True

    # ── shared_player 제어 ───────────────────────────────────────
    @classmethod
    def reapply(cls) -> None:
        """shared_player의 현재 t를 다시 적용한다 (correction 모드 변경 후 갱신용)."""
        cls.shared_player.set_t(cls.shared_player.t)

    @classmethod
    def reset(cls) -> None:
        """shared_player를 초기 상태(t=0, speed=1, forward, no-loop)로 리셋한다."""
        cls.shared_player.reset()

    # ── sync ─────────────────────────────────────────────────────────
    @classmethod
    def sync(cls, reference_key: str) -> None:
        """reference_key mixer의 t/speed/forward/loop를 shared_player에 복사하고,
        모든 mixer를 shared_player에 합류시킨다."""
        ref = cls._instances.get(reference_key)
        if ref:
            cls.shared_player.copy_from(ref.own_player)
        for mixer in cls._instances.values():
            mixer.join_player(cls.shared_player)

    @classmethod
    def unsync(cls, key: str) -> None:
        """해당 key의 mixer를 shared_player에서 떠나 own_player로 복귀시킨다."""
        mixer = cls._instances.get(key)
        if mixer:
            mixer.leave_player()

    # ── 인스턴스 위임 (key 기반) ─────────────────────────────────
    @classmethod
    async def load(cls, key: str, st_paths: 'list[str]', *,
                   panel: bool = False,
                   on_done: 'Callable[[list[str]], None] | None' = None
                   ) -> 'list[str]':
        """소스 USD 파일들을 mixer에 주입한다(재호출 시 재로드). 경고 목록을 반환한다.
        소스가 접근 가능해질 때까지 비동기 대기 후 로드한다.
        panel=True면 로드 완료 후 HUD 패널을 띄운다.
        on_done이 주어지면 로드 완료 시 경고 목록을 인자로 호출한다."""
        m = cls._instances.get(key)
        warnings = await m.load(st_paths) if m else []
        if panel:
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
