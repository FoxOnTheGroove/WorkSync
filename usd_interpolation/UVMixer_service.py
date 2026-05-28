from typing import Callable

from .UVMixer import UVMixer
from .UVMixer_player import UVMixerPlayer


class UVMixerService:
    _instances: dict[str, UVMixer] = {}
    _shared_player: UVMixerPlayer = UVMixerPlayer()   # N+1 플레이어 중 공유 플레이어 1개

    # ── 팩토리 ──────────────────────────────────────────────────
    @classmethod
    def create(cls, target_path: 'str | None', key: str) -> UVMixer:
        """빈 UVMixer를 만들어 key로 등록한다. 소스는 load(key, *paths)로 주입.
        target_path가 None이면 소스 경로를 그대로 사용(remap 없음)."""
        mixer = UVMixer.create(target_path)
        cls._instances[key] = mixer
        return mixer

    # ── 레지스트리 ───────────────────────────────────────────────
    @classmethod
    def get_instance(cls, key: str) -> 'UVMixer | None':
        """key에 해당하는 mixer를 반환한다(없으면 None)."""
        return cls._instances.get(key)

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
        """key의 mixer를 정지·해제하고 레지스트리에서 제거한다."""
        mixer = cls._instances.pop(key, None)
        if mixer is not None:
            mixer.destroy()

    @classmethod
    def destroy_all(cls) -> None:
        """모든 mixer를 해제하고 레지스트리를 비운다."""
        for m in list(cls._instances.values()):
            m.destroy()
        cls._instances.clear()

    # ── sync ─────────────────────────────────────────────────────────

    @classmethod
    def sync(cls, reference_key: str) -> None:
        """reference_key mixer의 t/speed/forward/loop를 shared_player에 복사하고,
        모든 mixer를 shared_player에 합류시킨다."""
        ref = cls._instances.get(reference_key)
        if ref:
            cls._shared_player.copy_from(ref.own_player)
        for mixer in cls._instances.values():
            mixer.join_player(cls._shared_player)

    @classmethod
    def unsync(cls, key: str) -> None:
        """해당 key의 mixer를 shared_player에서 떠나 own_player로 복귀시킨다."""
        mixer = cls._instances.get(key)
        if mixer:
            mixer.leave_player()

    # ── 인스턴스 위임 (key 기반) ─────────────────────────────────
    @classmethod
    def load(cls, key: str, *st_paths: str) -> 'list[str]':
        """소스 USD 파일들을 mixer에 주입한다(재호출 시 재로드). 경고 목록을 반환한다."""
        m = cls._instances.get(key)
        return m.load(*st_paths) if m else []

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
        """보간 위치 t(0.0~1.0)를 설정한다.
        correction=False면 fvli 보정을 건너뛴다(매 프레임 호출 시 성능 절약).
        drive_timeline=False면 전역 타임라인은 건드리지 않는다."""
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
        """재생 방향을 설정한다(True=정방향)."""
        m = cls._instances.get(key)
        if m:
            m.set_forward(forward)

    @classmethod
    def set_loop(cls, key: str, loop: bool) -> None:
        """재생 루프 여부를 설정한다."""
        m = cls._instances.get(key)
        if m:
            m.set_loop(loop)

    @classmethod
    def set_speed(cls, key: str, speed: float) -> None:
        """재생 속도 배율을 설정한다."""
        m = cls._instances.get(key)
        if m:
            m.set_speed(speed)

    @classmethod
    def set_correction(cls, key: str, enabled: bool) -> None:
        """UV 보정 on/off (rebake 없이 fvli 캐시만 갱신)."""
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
        """t 변경 시 callback(t)를 호출하도록 등록한다."""
        m = cls._instances.get(key)
        if m:
            m.subscribe(callback)

    @classmethod
    def unsubscribe(cls, key: str, callback: Callable[[float], None]) -> None:
        """등록된 콜백을 제거한다."""
        m = cls._instances.get(key)
        if m:
            m.unsubscribe(callback)
