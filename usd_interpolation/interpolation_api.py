from .interpolation import UVMixer


class UVMixer_api:
    """공개 API. UVMixer의 classmethod를 외부로 노출."""

    init = UVMixer.init
    load = UVMixer.load
    loads = UVMixer.loads
    unload = UVMixer.unload
    get_loaded_slots = UVMixer.get_loaded_slots
    set_t = UVMixer.set_t
    get_t = UVMixer.get_t
    play = UVMixer.play
    stop = UVMixer.stop
    is_playing = UVMixer.is_playing
    set_speed = UVMixer.set_speed
    get_speed = UVMixer.get_speed
    set_correction = UVMixer.set_correction
    subscribe = UVMixer.subscribe
    unsubscribe = UVMixer.unsubscribe
    destroy = UVMixer.destroy
