from .interpolation import UVMixer


class UVMixerService:
    _instances: list[UVMixer] = []

    @classmethod
    def create(cls,
               target_path: 'str | None',
               st_maps: 'list',
               *,
               use_correction: bool = True) -> UVMixer:
        mixer = UVMixer.create(target_path, st_maps, use_correction=use_correction)
        cls._instances.append(mixer)
        return mixer

    @classmethod
    def create_with_maps(cls,
                         target_path: 'str | None',
                         *st_paths: str,
                         use_correction: bool = True) -> UVMixer:
        mixer = UVMixer.create_with_maps(target_path, *st_paths, use_correction=use_correction)
        cls._instances.append(mixer)
        return mixer

    @classmethod
    def get_instances(cls) -> list[UVMixer]:
        return list(cls._instances)

    @staticmethod
    def make_st_map(file_path: str) -> dict:
        return UVMixer.make_st_map(file_path)
