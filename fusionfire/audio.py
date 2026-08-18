"""Audio playback on top of sound_lib (BASS).

The shape of this module follows the game's needs rather than BASS's:

* **Two buses.** Sound effects and music carry independent volumes, because
  the original bound them to separate hotkeys (Home/End and Page Up/Down)
  and players genuinely use that — the score is loud and the whip is not.
* **Short sounds live in RAM.** A gunshot fired forty times a match should
  not hit the disk forty times. Files under ``MEMORY_CACHE_LIMIT`` are read
  once into a ctypes buffer; every later playback is a new stream over the
  same buffer, which BASS is happy to do. Long files (the six-minute
  power_weapon bed, the music) stream from disk.
* **Streams are reclaimed.** Every stream handle is tracked and freed when it
  finishes. BASS will otherwise happily let a long session leak thousands of
  handles.

Nothing here raises on failure. A missing sound or a dead audio device
degrades to silence and a log line: an audio game with no audio is bad, but
an audio game that crashes is worse.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from pathlib import Path
from typing import Iterable

from . import assets, rng
from .assets import CATALOGUE, GROUPS

log = logging.getLogger(__name__)

try:
    from sound_lib.output import Output
    from sound_lib.stream import FileStream

    HAVE_AUDIO = True
except Exception as exc:  # pragma: no cover - depends on BASS binaries
    HAVE_AUDIO = False
    log.error("sound_lib unavailable, running silent: %s", exc)

#: The only music the Enter key silences.
#:
#: Everything else that lives in ``music/`` is an event piece rather than
#: accompaniment: the win and lose stings, the exit music, the studio intro,
#: and the drumroll under a power weapon shot. The original was explicit
#: about this -- its Enter toggle "does not affect win/lose/exit/end/logo/
#: loading music" -- and the drumroll is the same kind of thing. Silencing a
#: weapon's own firing sequence along with the background score means a
#: player who prefers no music loses part of the game, not part of the
#: soundtrack.
BACKGROUND_MUSIC = frozenset(
    {"level1", "level2", "level3", "level1o", "level2o", "level3o"}
)

#: Files at or below this size are cached in memory rather than streamed.
MEMORY_CACHE_LIMIT = 4 * 1024 * 1024
#: Total memory the sound cache may consume before it stops taking new entries.
MEMORY_CACHE_BUDGET = 96 * 1024 * 1024

# ----------------------------------------------------------------------
# A note on measuring sounds
# ----------------------------------------------------------------------
# An earlier design held a spoken line back only until the accompanying
# sound stopped being *loud*, measured as an energy envelope against a
# -20 dB floor. The arithmetic was sound -- a gunshot really is 1.73s of
# file and 0.74s of noise -- and the result was wrong in play: the reaction
# screams are human voices, and starting a line in the quiet tail of a cry
# still lands the speech mid-cry.
#
# The envelope analysis went with it. What the presenter needs is
# :meth:`AudioEngine.length_of`, the plain length of the file, because the
# line now waits for the sound to actually finish.

class Handle:
    """A playing sound. Thin, forgiving wrapper over a BASS channel."""

    __slots__ = ("_stream", "_bus", "_engine", "_base_volume", "_duck")

    def __init__(self, stream, bus: str, engine: "AudioEngine", base_volume: float = 1.0):
        self._stream = stream
        self._bus = bus
        self._engine = engine
        self._base_volume = base_volume
        #: Extra attenuation applied because this sound was in the way of a
        #: spoken status line. Never restored — see :meth:`duck`.
        self._duck = 1.0

    @property
    def playing(self) -> bool:
        try:
            return bool(self._stream.is_playing)
        except Exception:
            return False

    @property
    def length(self) -> float:
        try:
            return float(self._stream.length_in_seconds())
        except Exception:
            return 0.0

    @property
    def position(self) -> float:
        try:
            return float(self._stream.bytes_to_seconds(self._stream.position))
        except Exception:
            return 0.0

    def set_pan(self, pan: float) -> None:
        try:
            self._stream.pan = max(-1.0, min(1.0, pan))
        except Exception:
            pass

    def apply_bus_volume(self, bus_volume: float) -> None:
        try:
            self._stream.volume = max(
                0.0, min(1.0, self._base_volume * bus_volume * self._duck)
            )
        except Exception:
            pass

    def duck(self, level: float, seconds: float = 0.15) -> None:
        """Slide down to ``level`` of normal and stay there.

        There is deliberately no way back up. A duck that has to be lifted
        needs to know when the speech it made room for ends, and this game
        cannot know that: the screen reader will not say. Because the
        sounds this is used on are finite and end while ducked, never
        restoring costs nothing and removes the guess entirely.

        Ducking is one-way in the other sense too — a second call cannot
        make a sound louder than an earlier one left it.
        """
        level = max(0.0, min(1.0, level))
        if level >= self._duck:
            return
        self._duck = level
        bus_volume = (
            self._engine.music_volume if self._bus == "music" else self._engine.sound_volume
        )
        target = max(0.0, min(1.0, self._base_volume * bus_volume * level))
        try:
            self._stream.slide_attribute("volume", target, seconds)
        except Exception:
            try:
                self._stream.volume = target
            except Exception:
                pass

    def fade_out(self, seconds: float = 1.0) -> None:
        """Slide to silence, then stop. Falls back to a hard stop."""
        try:
            self._stream.slide_attribute("volume", 0.0, seconds)
            timer = threading.Timer(seconds + 0.05, self.stop)
            timer.daemon = True
            timer.start()
        except Exception:
            self.stop()

    def stop(self) -> None:
        try:
            self._stream.looping = False
        except Exception:
            pass
        try:
            self._stream.stop()
        except Exception:
            pass
        self._engine._release(self)

    def _free(self) -> None:
        try:
            self._stream.free()
        except Exception:
            pass


class _NullHandle(Handle):
    """Returned when audio is unavailable, so callers never branch on None."""

    def __init__(self) -> None:  # noqa: D107 - deliberately does not call super
        self._stream = None
        self._bus = "sfx"
        self._engine = None
        self._base_volume = 0.0
        self._duck = 1.0

    @property
    def playing(self) -> bool:
        return False

    @property
    def length(self) -> float:
        return 0.0

    @property
    def position(self) -> float:
        return 0.0

    def set_pan(self, pan: float) -> None:
        pass

    def apply_bus_volume(self, bus_volume: float) -> None:
        pass

    def duck(self, level: float, seconds: float = 0.15) -> None:
        pass

    def fade_out(self, seconds: float = 1.0) -> None:
        pass

    def stop(self) -> None:
        pass

    def _free(self) -> None:
        pass


NULL_HANDLE = _NullHandle()


class AudioEngine:
    """Owns the BASS output device, the sound cache and the music bed."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._output = None
        self._lock = threading.RLock()
        self._live: list[Handle] = []
        self._cache: dict[str, tuple[ctypes.Array, int]] = {}
        self._cache_bytes = 0
        self._length_cache: dict[str, float] = {}
        self._music: Handle | None = None
        self._music_name: str | None = None
        self._ambience: dict[str, Handle] = {}
        self._missing: set[str] = set()
        self._devices: list[tuple[str, int]] = []

        if not HAVE_AUDIO:
            return
        try:
            # Resolve the remembered device by name. If it has been unplugged
            # since last time this falls back to the system default rather
            # than opening whatever now happens to sit at that position.
            device_id = self.device_id_for(settings.output_device_name)
            self._output = Output(device=device_id)
        except Exception as exc:
            log.error("Audio device init failed: %s", exc)
            try:
                self._output = Output()
            except Exception as exc2:
                log.error("Default audio device also failed: %s", exc2)
                self._output = None

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------
    # Device identity is a *name*, not a position. BASS numbers its devices
    # from 0 ("No sound"), and sound_lib's ``get_device_names()`` starts at 1
    # and silently drops disabled ones while still advancing its counter — so
    # a list position maps to a BASS id only by coincidence, and the
    # coincidence breaks the moment a device is disabled or unplugged. We
    # enumerate the devices ourselves and keep the real ids alongside the
    # names, then remember the chosen device by name so plugging a headset in
    # cannot silently repoint the game at something else.

    def enumerate_devices(self) -> list[tuple[str, int]]:
        """``(name, BASS id)`` for every usable output, "Default" included."""
        if not HAVE_AUDIO:
            return [("Default", -1)]

        found: list[tuple[str, int]] = []
        try:
            import ctypes as _ctypes

            from sound_lib.external.pybass import (
                BASS_DEVICE_ENABLED,
                BASS_DEVICEINFO,
                BASS_GetDeviceInfo,
            )

            info = BASS_DEVICEINFO()
            index = 0
            while BASS_GetDeviceInfo(index, _ctypes.byref(info)):
                enabled = bool(info.flags & BASS_DEVICE_ENABLED)
                raw = info.name
                name = (
                    raw.decode("mbcs", errors="replace")
                    if isinstance(raw, bytes)
                    else str(raw)
                )
                # Device 0 is BASS's "No sound" sink. Offering it in an audio
                # game would be a trap.
                if enabled and index > 0 and name:
                    found.append((name.strip(), index))
                index += 1
        except Exception as exc:
            log.debug("Could not enumerate output devices: %s", exc)

        self._devices = found or [("Default", -1)]
        return list(self._devices)

    @property
    def available(self) -> bool:
        return self._output is not None

    def device_names(self) -> list[str]:
        return [name for name, _ in self.enumerate_devices()]

    def device_id_for(self, name: str) -> int:
        """BASS id for a device name, or -1 (system default) if it is gone."""
        for candidate, device_id in self.enumerate_devices():
            if candidate == name:
                return device_id
        return -1

    def current_device_name(self) -> str:
        return self.settings.output_device_name or "Default"

    def set_device(self, name: str) -> bool:
        """Switch output to the named device. Music restarts; one-shots stop."""
        if not HAVE_AUDIO:
            return False

        device_id = self.device_id_for(name)
        with self._lock:
            resume = self._music_name
            self.stop_all()
            # Cached buffers are fine, but every stream built from them
            # belonged to the old device and has just been freed.
            self._cache.clear()
            self._cache_bytes = 0
            try:
                if self._output is not None:
                    try:
                        self._output.free()
                    except Exception:
                        pass
                self._output = Output(device=device_id)
                self.settings.output_device_name = name
                ok = True
            except Exception as exc:
                log.error("Could not open audio device %r (BASS %s): %s", name, device_id, exc)
                try:
                    self._output = Output()
                    self.settings.output_device_name = ""
                except Exception:
                    self._output = None
                ok = False
        if resume:
            self.play_music(resume, looping=True)
        return ok

    # ------------------------------------------------------------------
    # Volume buses
    # ------------------------------------------------------------------
    @property
    def sound_volume(self) -> float:
        return self.settings.sound_volume

    @property
    def music_volume(self) -> float:
        return self.settings.music_volume

    def adjust_sound_volume(self, delta: float) -> float:
        self.settings.sound_volume = max(0.0, min(1.0, self.settings.sound_volume + delta))
        self._reapply_bus("sfx")
        return self.settings.sound_volume

    def adjust_music_volume(self, delta: float) -> float:
        self.settings.music_volume = max(0.0, min(1.0, self.settings.music_volume + delta))
        self._reapply_bus("music")
        return self.settings.music_volume

    def _reapply_bus(self, bus: str) -> None:
        volume = self.music_volume if bus == "music" else self.sound_volume
        with self._lock:
            for handle in list(self._live):
                if handle._bus == bus:
                    handle.apply_bus_volume(volume)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _path_for(self, name: str) -> Path | None:
        """Where a catalogue sound lives right now.

        One folder, no shadowing: whatever file is sitting at the sound's
        path is the sound. Replacing one means replacing the file, and the
        result is proven to be inside the sounds folder before it is opened.
        """
        ref = CATALOGUE.get(name)
        if ref is None:
            if name not in self._missing:
                self._missing.add(name)
                log.error("Unknown sound requested: %r", name)
            return None
        try:
            path = ref.resolve()
        except Exception as exc:
            log.error("Bad path for %r: %s", name, exc)
            return None
        if not path.is_file():
            if name not in self._missing:
                self._missing.add(name)
                log.warning("Missing sound file: %s", path)
            return None
        return path

    def _make_stream(self, name: str, looping: bool):
        path = self._path_for(name)
        if path is None or not self._output:
            return None

        cached = self._cache.get(name)
        if cached is None:
            try:
                size = path.stat().st_size
            except OSError:
                size = MEMORY_CACHE_LIMIT + 1
            if size <= MEMORY_CACHE_LIMIT and self._cache_bytes + size <= MEMORY_CACHE_BUDGET:
                try:
                    blob = path.read_bytes()
                    buffer = ctypes.create_string_buffer(blob, len(blob))
                    cached = (buffer, len(blob))
                    self._cache[name] = cached
                    self._cache_bytes += len(blob)
                except OSError as exc:
                    log.warning("Could not cache %s: %s", path, exc)

        try:
            if cached is not None:
                buffer, length = cached
                stream = FileStream(mem=True, file=ctypes.addressof(buffer), length=length)
            else:
                stream = FileStream(file=str(path))
            if looping:
                stream.looping = True
            return stream
        except Exception as exc:
            log.error("Could not open %s: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def play(
        self,
        name: str,
        *,
        volume: float = 1.0,
        pan: float = 0.0,
        looping: bool = False,
        bus: str = "sfx",
    ) -> Handle:
        """Play a catalogue sound. Always returns a handle, never raises."""
        if not self._output:
            return NULL_HANDLE
        stream = self._make_stream(name, looping)
        if stream is None:
            return NULL_HANDLE

        handle = Handle(stream, bus, self, base_volume=max(0.0, min(1.0, volume)))
        bus_volume = self.music_volume if bus == "music" else self.sound_volume
        handle.apply_bus_volume(bus_volume)
        if pan:
            handle.set_pan(pan)
        try:
            stream.play()
        except Exception as exc:
            log.error("Could not start %r: %s", name, exc)
            handle._free()
            return NULL_HANDLE

        with self._lock:
            self._live.append(handle)
            self._reap_locked()
        return handle

    def length_of(self, name: str) -> float:
        """Duration of a catalogue sound in seconds, or 0 if unknown.

        Read from the file header rather than from a playing channel, so a
        caller can time something against a sound before starting it. Only
        WAV is measured directly; other formats fall back to opening a
        decoding stream.
        """
        path = self._path_for(name)
        if path is None:
            return 0.0

        cached = self._length_cache.get(name)
        if cached is not None:
            return cached

        length = 0.0
        if path.suffix.lower() == ".wav":
            try:
                import wave

                with wave.open(str(path), "rb") as handle:
                    rate = handle.getframerate()
                    if rate:
                        length = handle.getnframes() / float(rate)
            except Exception as exc:
                log.debug("Could not measure %s: %s", path, exc)

        if not length and self._output is not None:
            try:
                stream = FileStream(file=str(path), decode=True)
                length = float(stream.length_in_seconds())
                stream.free()
            except Exception as exc:
                log.debug("Could not measure %s via BASS: %s", path, exc)

        self._length_cache[name] = length
        return length

    def duck_one_shots(self, level: float, fade: float = 0.15) -> None:
        """Turn down every one-shot effect still playing, for good.

        Called the moment a held-back status line is spoken, so whatever is
        still ringing from the action gets out from in front of it. Nothing
        is restored; see :meth:`Handle.duck` for why that is the point
        rather than an omission.

        Looping ambience and the music bed are exempt. Those play for the
        whole match, so a duck they never come back from would quietly
        silence them.
        """
        with self._lock:
            ambience = {id(handle) for handle in self._ambience.values()}
            handles = [
                handle
                for handle in self._live
                if handle._bus == "sfx" and id(handle) not in ambience
            ]
        for handle in handles:
            handle.duck(level, fade)

    def play_one_of(self, group: str, **kwargs) -> Handle:
        """Play a random member of a named group (``userhit``, ``note``…)."""
        members = GROUPS.get(group)
        if not members:
            log.error("Unknown sound group: %r", group)
            return NULL_HANDLE
        return self.play(rng.choice(members), **kwargs)

    def play_sequence(self, names: Iterable[str], gap: float = 0.0) -> None:
        """Play sounds back to back on a timer thread, without blocking wx."""
        queue = list(names)
        if not queue:
            return

        def step(index: int) -> None:
            if index >= len(queue):
                return
            handle = self.play(queue[index])
            delay = max(0.05, handle.length + gap)
            timer = threading.Timer(delay, step, args=(index + 1,))
            timer.daemon = True
            timer.start()

        step(0)

    # ------------------------------------------------------------------
    # Music bed
    # ------------------------------------------------------------------
    def play_music(self, name: str, *, looping: bool = True, fade: float = 0.8) -> Handle:
        """Swap the music bed to ``name``, fading whatever was there out.

        Only background score obeys the music toggle; event pieces always
        play. See :data:`BACKGROUND_MUSIC`.
        """
        if name in BACKGROUND_MUSIC and not self.settings.music_enabled:
            self.stop_music(fade=fade)
            return NULL_HANDLE
        if self._music_name == name and self._music is not None and self._music.playing:
            return self._music

        self.stop_music(fade=fade)
        handle = self.play(name, looping=looping, bus="music")
        self._music = handle if handle is not NULL_HANDLE else None
        self._music_name = name if self._music else None
        return handle

    def stop_music(self, *, fade: float = 0.8) -> None:
        music, self._music, self._music_name = self._music, None, None
        if music is None:
            return
        if fade > 0:
            music.fade_out(fade)
        else:
            music.stop()

    def toggle_music(self) -> bool:
        """Enter toggles the background score, as it did in the original.

        An event piece already playing is left alone: turning the music off
        mid-drumroll should not cut the shot you are in the middle of taking.
        """
        self.settings.music_enabled = not self.settings.music_enabled
        if not self.settings.music_enabled and self._music_name in BACKGROUND_MUSIC:
            self.stop_music()
        return self.settings.music_enabled

    @property
    def current_music(self) -> str | None:
        return self._music_name

    def playing_music(self, name: str) -> bool:
        """True while ``name`` is the current music bed and still playing.

        Checks the stream rather than just the remembered name, so a piece
        that has already finished on its own is not treated as playing.
        """
        return (
            self._music_name == name
            and self._music is not None
            and self._music.playing
        )

    # ------------------------------------------------------------------
    # Looping ambience (power_weapon beds)
    # ------------------------------------------------------------------
    def start_ambience(self, key: str, name: str, volume: float = 0.55) -> None:
        self.stop_ambience(key)
        handle = self.play(name, volume=volume, looping=True)
        if handle is not NULL_HANDLE:
            self._ambience[key] = handle

    def stop_ambience(self, key: str, fade: float = 1.5) -> None:
        handle = self._ambience.pop(key, None)
        if handle is not None:
            handle.fade_out(fade)

    def stop_all_ambience(self) -> None:
        for key in list(self._ambience):
            self.stop_ambience(key, fade=0.4)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def stop_all(self) -> None:
        with self._lock:
            handles = list(self._live)
            self._live.clear()
        for handle in handles:
            try:
                handle._stream.looping = False
                handle._stream.stop()
            except Exception:
                pass
            handle._free()
        self._music = None
        self._music_name = None
        self._ambience.clear()

    def _release(self, handle: Handle) -> None:
        with self._lock:
            try:
                self._live.remove(handle)
            except ValueError:
                return
        handle._free()

    def _reap_locked(self) -> None:
        """Free handles for sounds that have finished. Caller holds the lock."""
        still_live = []
        for handle in self._live:
            if handle.playing:
                still_live.append(handle)
            else:
                handle._free()
        self._live = still_live

    def shutdown(self) -> None:
        self.stop_all()
        self._cache.clear()
        self._cache_bytes = 0
        if self._output is not None:
            try:
                self._output.free()
            except Exception:
                pass
            self._output = None
