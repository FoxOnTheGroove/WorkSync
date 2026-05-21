from .interpolation import UVMixer


class UVMixerAPI:
    """
    Public API for UV interpolation across USD stages.
    External code should use this class rather than UVMixer directly.
    """

    # ── Setup ──────────────────────────────────────────────────────────────────

    @classmethod
    def setup(cls,
              num_slots: int = 5,
              play_duration: float = 2.5,
              *,
              correction: bool = True) -> None:
        """Configure the mixer. Call before loading any slots."""
        UVMixer.init(num_slots=num_slots, play_duration=play_duration,
                     use_correction=correction)

    # ── Loading ────────────────────────────────────────────────────────────────

    @classmethod
    def load_file(cls, path: str, slot: int) -> bool:
        """Load st-map from a USD file into slot. Returns True on success."""
        return UVMixer.load_from_file(path, slot)

    @classmethod
    def load_stage(cls, slot: int, prim_paths: list[str] | None = None) -> bool:
        """
        Read st-map from prims in the currently open stage into slot.
        If prim_paths is None, all mesh prims with an 'st' primvar are gathered.
        Returns True on success.
        """
        return UVMixer.load_from_stage(slot, prim_paths)

    @classmethod
    def unload(cls, slot: int) -> None:
        """Release data for the given slot."""
        UVMixer.unload(slot)

    @classmethod
    def loaded_slots(cls) -> list[int]:
        """Return indices of slots that have data loaded."""
        return UVMixer.get_loaded_slots()

    # ── Playback ───────────────────────────────────────────────────────────────

    @classmethod
    def play(cls, *, forward: bool = True, loop: bool = False) -> None:
        """Start animated playback."""
        UVMixer.play(forward=forward, loop=loop)

    @classmethod
    def stop(cls) -> None:
        """Stop playback."""
        UVMixer.stop()

    @classmethod
    def is_playing(cls) -> bool:
        """True while playback is running."""
        return UVMixer.is_playing()

    @classmethod
    def seek(cls, t: float) -> None:
        """Jump to interpolation position t in [0, 1]."""
        UVMixer.set_t(t)

    @classmethod
    def position(cls) -> float:
        """Current interpolation position t in [0, 1]."""
        return UVMixer.get_t()

    # ── Speed ──────────────────────────────────────────────────────────────────

    @classmethod
    def set_speed(cls, speed: float) -> None:
        """Set playback speed multiplier (default 1.0)."""
        UVMixer.set_speed(speed)

    @classmethod
    def get_speed(cls) -> float:
        return UVMixer.get_speed()

    # ── Correction ─────────────────────────────────────────────────────────────

    @classmethod
    def set_correction(cls, enabled: bool) -> None:
        """Enable or disable the correction pass applied during baking."""
        UVMixer.set_correction(enabled)

    # ── Callbacks ──────────────────────────────────────────────────────────────

    @classmethod
    def subscribe(cls, callback) -> None:
        """Register a callback(t: float) invoked on every position update."""
        UVMixer.subscribe(callback)

    @classmethod
    def unsubscribe(cls, callback) -> None:
        UVMixer.unsubscribe(callback)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @classmethod
    def destroy(cls) -> None:
        """Stop playback and clear all subscribers."""
        UVMixer.destroy()
