import uuid
from typing import Callable

from .interpolation import UVMixer


class UVMixerService:
    _instances: dict[str, UVMixer] = {}

    # ── 팩토리 ──────────────────────────────────────────────────
    @classmethod
    def create(cls,
               target_path: 'str | None',
               key: 'str | None' = None) -> UVMixer:
        mixer = UVMixer.create(target_path)
        key = key or target_path or uuid.uuid4().hex[:8]
        cls._instances[key] = mixer
        return mixer

    @staticmethod
    def make_st_map(file_path: str) -> dict:
        return UVMixer.make_st_map(file_path)

    # ── 레지스트리 ───────────────────────────────────────────────
    @classmethod
    def get_instances(cls) -> dict[str, UVMixer]:
        return dict(cls._instances)

    @classmethod
    def get_instance(cls, key: str) -> 'UVMixer | None':
        return cls._instances.get(key)

    @classmethod
    def get_key(cls, mixer: UVMixer) -> 'str | None':
        return next((k for k, v in cls._instances.items() if v is mixer), None)

    @classmethod
    def destroy(cls, key: str) -> None:
        mixer = cls._instances.pop(key, None)
        if mixer is not None:
            mixer.destroy()

    @classmethod
    def destroy_all(cls) -> None:
        for m in list(cls._instances.values()):
            m.destroy()
        cls._instances.clear()

    # ── 인스턴스 위임 (key 기반) ─────────────────────────────────
    @classmethod
    def load(cls, key: str, *st_paths: str) -> None:
        m = cls._instances.get(key)
        if m:
            m.load(*st_paths)

    @classmethod
    def play(cls, key: str) -> None:
        m = cls._instances.get(key)
        if m:
            m.play()

    @classmethod
    def stop(cls, key: str) -> None:
        m = cls._instances.get(key)
        if m:
            m.stop()

    @classmethod
    def is_playing(cls, key: str) -> bool:
        m = cls._instances.get(key)
        return m.is_playing() if m else False

    @classmethod
    def set_value(cls, key: str, t: float) -> None:
        m = cls._instances.get(key)
        if m:
            m.set_value(t)

    @classmethod
    def get_value(cls, key: str) -> float:
        m = cls._instances.get(key)
        return m.get_value() if m else 0.0

    @classmethod
    def apply_correction(cls, key: str) -> None:
        m = cls._instances.get(key)
        if m:
            m.apply_correction()

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
    def subscribe(cls, key: str, callback: Callable[[float], None]) -> None:
        m = cls._instances.get(key)
        if m:
            m.subscribe(callback)

    @classmethod
    def unsubscribe(cls, key: str, callback: Callable[[float], None]) -> None:
        m = cls._instances.get(key)
        if m:
            m.unsubscribe(callback)
