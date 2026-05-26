import uuid

from .interpolation import UVMixer


class UVMixerService:
    _instances: dict[str, UVMixer] = {}

    # ── 팩토리 ──────────────────────────────────────────────────
    @classmethod
    def create(cls,
               target_path: 'str | None',
               *st_paths: str) -> UVMixer:
        mixer = UVMixer.create(target_path, *st_paths)
        key = target_path or uuid.uuid4().hex[:8]
        cls._instances[key] = mixer
        return mixer

    @classmethod
    def create_with_maps(cls,
                         target_path: 'str | None',
                         st_maps: list) -> UVMixer:
        mixer = UVMixer.create_with_maps(target_path, st_maps)
        key = target_path or uuid.uuid4().hex[:8]
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
    def destroy(cls, mixer: UVMixer) -> None:
        mixer.destroy()
        key = next((k for k, v in cls._instances.items() if v is mixer), None)
        if key is not None:
            del cls._instances[key]

    @classmethod
    def destroy_all(cls) -> None:
        for m in list(cls._instances.values()):
            m.destroy()
        cls._instances.clear()

    # ── 반환된 UVMixer 인스턴스 API ─────────────────────────────
    # mixer.play()                재생 시작
    # mixer.stop()                재생 정지
    # mixer.is_playing() -> bool  재생 중 여부
    # mixer.seek(t)               t (0.0~1.0) 위치로 이동
    # mixer.position() -> float   현재 t 값
    # mixer.apply_correction()    UV 보정 수동 적용
    # mixer.set_forward(v)        재생 방향 (True=정방향)
    # mixer.set_loop(v)           루프 설정
    # mixer.set_speed(v)          재생 속도 배율
    # mixer.set_correction(v)     correction on/off (bake 재실행 없음)
    # mixer.subscribe(cb)         t 변경 시 cb(t) 호출 등록
    # mixer.unsubscribe(cb)       콜백 제거
