"""Screen-reader output through Prism.

The original required a screen reader and said so on the tin — it was not
self-voicing, and if nothing was running you got nothing. Prism gives us a
better deal: it talks to whatever is actually present (NVDA, JAWS, Narrator
via UIA, VoiceOver, Orca, Speech Dispatcher…) and falls back to the platform
TTS when no reader is running, so the game is playable either way.

Braille is first-class rather than an afterthought. Combat status lines are
short and numeric, which is exactly the kind of output a braille display
handles better than speech, so anything routed through :meth:`Speech.report`
goes to both.
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from typing import Any

from . import platform_info

log = logging.getLogger(__name__)

try:
    from prism import BackendId, Context

    HAVE_PRISM = True
except Exception as exc:  # pragma: no cover - depends on native build
    HAVE_PRISM = False
    log.error("Prism unavailable, speech disabled: %s", exc)


_BACKEND_ALIASES = {
    "auto": "",
    "narrator": "UIA",
    "nvda": "NVDA",
    "jaws": "JAWS",
    "sapi": "SAPI",
    "sapi5": "SAPI",
    "onecore": "ONE_CORE",
    "uia": "UIA",
    "voiceover": "VOICE_OVER",
    "orca": "ORCA",
    "speechd": "SPEECH_DISPATCHER",
    "speechdispatcher": "SPEECH_DISPATCHER",
    "zdsr": "ZDSR",
    "zoomtext": "ZOOM_TEXT",
    "systemaccess": "SYSTEM_ACCESS",
    "sensereader": "SENSE_READER",
    "pctalker": "PC_TALKER",
}


#: The span of Prism pitch values a backend actually behaves sensibly over,
#: keyed by normalised backend name. The slider's 0-100% maps onto this.
#:
#: Established by synthesising to memory and measuring the fundamental
#: frequency of the result, which is the only way to see what a pitch value
#: really does. Reading the value back out of Prism does not tell you: it
#: reports what it stored, not what the synthesiser did.
#:
#: OneCore, measured on a male voice whose own pitch is 157 Hz:
#:
#:     0.00 - 0.50  ->  302 Hz   every value, the maximum
#:     0.51         ->  162 Hz   normal
#:     0.60         ->  184 Hz
#:     0.75         ->  222 Hz
#:     1.00         ->  302 Hz   the maximum
#:
#: So the bottom half does not "do nothing" -- it jumps to maximum pitch,
#: and only 0.51 upwards is monotonic. Prism appears to mishandle the
#: below-normal half of the WinRT AudioPitch range. The consequence for a
#: player is that OneCore can raise pitch but not lower it, which the
#: settings dialog says out loud rather than offering travel that lies.
#:
#: SAPI was measured the same way and is well behaved -- 155 Hz to 271 Hz
#: monotonically across the full range -- so it keeps it.
#:
#: Look this up through :meth:`Speech.pitch_range`, never directly. The token
#: the settings dropdown carries is the BackendId name lowered ("one_core"),
#: not the spelling used here, and keying a table on the wrong spelling is
#: exactly how an earlier version of this silently clamped nothing.
PITCH_RANGES = {"onecore": (0.51, 1.0)}
DEFAULT_PITCH_RANGE = (0.0, 1.0)


def _normalise(token: str) -> str:
    return "".join(c for c in token.lower() if c.isalnum())


# ----------------------------------------------------------------------
# Working around a Prism text-encoding bug
# ----------------------------------------------------------------------
# Prism 0.17.3 (the current release) rejects a large slice of Unicode with
# "Invalid UTF-8". The failure is exact and reproducible: a character breaks
# it when its UTF-8 encoding is three bytes long *and* the continuation byte
# lands in 0x80-0x9F — the cp1252 supplement range, which is the signature of
# a code-page round-trip somewhere below the API. Verified against 634
# codepoints spanning the BMP with no exceptions either way.
#
# In codepoint terms that is every character where ``cp % 4096 < 2048``, so it
# takes out roughly half the BMP: all the typographic punctuation an English
# sentence naturally reaches for (em dash, curly quotes, ellipsis) along with
# Greek Extended, Georgian, Myanmar and much else. Speech is this game's
# primary output channel, so silently losing a status line to it is not
# survivable — the text is sanitised on the way out instead.
#
# Retrying does not help and neither does padding; only changing the
# characters does. Drop this whole section once Prism handles the range.
_SUBSTITUTIONS = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": " - ", "―": "-", "−": "-",
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "′": "'", "″": '"', "‹": "<", "›": ">",
    "•": "*", "…": "...", "⁄": "/",
    " ": " ", " ": " ", " ": " ", " ": " ",
}


def _unsupported(char: str) -> bool:
    """True if Prism will refuse this character (see the note above)."""
    encoded = char.encode("utf-8", "replace")
    return len(encoded) == 3 and 0x80 <= encoded[1] <= 0x9F


def make_speakable(text: str) -> str:
    """Rewrite ``text`` into characters the speech backend can carry.

    Known punctuation is mapped to its ASCII equivalent, which is what a
    screen reader wants to hear anyway. Anything else unsupported is
    decomposed and stripped to its ASCII skeleton, so an accented or scripted
    name degrades to something pronounceable rather than taking the entire
    sentence down with it.
    """
    if text.isascii():
        return text

    out = []
    for char in text:
        if not _unsupported(char):
            out.append(char)
            continue
        replacement = _SUBSTITUTIONS.get(char)
        if replacement is None:
            decomposed = unicodedata.normalize("NFKD", char)
            replacement = "".join(c for c in decomposed if not _unsupported(c))
        out.append(replacement if replacement else " ")
    return "".join(out)


def open_speech(settings: Any):
    """The speech layer this system can actually use.

    Prism where it works, accessible_output2 where it does not. Prism's
    native half is built against WinRT and drives OneCore voices that do not
    exist before Windows 10, so on 8.1 and 7 it either fails to load or comes
    up with nothing behind it. accessible_output2 has talked to the screen
    readers on those systems for years.

    The choice is made once, here, and the rest of the game holds an object
    with the same surface either way.
    """
    if HAVE_PRISM and platform_info.is_windows_10_or_later():
        speech = Speech(settings)
        if speech.available:
            return speech
        log.warning("Prism started but found no backend; trying accessible_output2.")
    elif HAVE_PRISM:
        log.info(
            "%s predates the voices Prism needs; using accessible_output2.",
            platform_info.describe(),
        )

    from .speech_ao2 import AO2Speech

    fallback = AO2Speech(settings)
    if fallback.available:
        log.info("Speech through accessible_output2: %s", fallback.backend_name)
        return fallback

    # Neither is available. Prism's own object degrades to silence without
    # raising, which keeps every caller simple.
    log.error("No speech library is available; the game will be silent.")
    return Speech(settings)


class Speech:
    """Speech and braille, with every call safe to make from any thread."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._context = None
        self._backend = None
        self._features = None
        if HAVE_PRISM:
            self._open(settings.speech_backend)

    # ------------------------------------------------------------------
    # Backend selection
    # ------------------------------------------------------------------
    def _open(self, requested: str) -> None:
        try:
            self._context = Context()
        except Exception as exc:
            log.error("Could not create Prism context: %s", exc)
            return

        backend = None
        token = _normalise(requested or "auto")
        mapped = _BACKEND_ALIASES.get(token, "")
        if mapped:
            try:
                backend = self._context.create(BackendId[mapped])
            except Exception as exc:
                log.warning("Backend %s unavailable (%s); using best available.", mapped, exc)
        if backend is None:
            try:
                backend = self._context.create_best()
            except Exception as exc:
                log.error("No speech backend available: %s", exc)
                return

        self._backend = backend
        try:
            self._features = backend.features
        except Exception:
            self._features = None
        self._apply_settings()
        log.info("Speech backend: %s", self.backend_name)

    # Prism takes rate, pitch and volume as floats from 0.0 to 1.0 and raises
    # PrismRangeError for anything outside that. The settings hold percentages
    # because that is what a slider and a spoken value want to be, so every
    # one of them is divided on the way in. Getting this wrong is silent: the
    # exception is swallowed and the voice simply never changes.
    @staticmethod
    def _as_unit(percent: float) -> float:
        return max(0.0, min(1.0, percent / 100.0))

    def _to_backend(self, what: str, percent: float) -> float:
        """A slider percentage as the value this backend wants.

        Rate and volume are the plain unit range. Pitch is mapped onto
        whatever span the backend actually behaves over, which for OneCore
        is only the upper half -- see :data:`PITCH_RANGES`.
        """
        unit = self._as_unit(percent)
        if what != "pitch":
            return unit
        low, high = self.pitch_range()
        return low + (high - low) * unit

    def _from_backend(self, what: str, value: float) -> float:
        """The inverse, for seeding the slider from the live voice."""
        if what != "pitch":
            return max(0.0, min(100.0, value * 100.0))
        low, high = self.pitch_range()
        if high <= low:
            return -1.0
        return max(0.0, min(100.0, (value - low) / (high - low) * 100.0))

    def _apply_settings(self) -> None:
        if self._backend is None or self._features is None:
            return

        for name, percent in (
            ("rate", self.settings.speech_rate),
            ("pitch", self.settings.speech_pitch),
            ("volume", self.settings.speech_volume),
        ):
            if percent < 0:
                continue  # leave the voice's own setting alone
            if not getattr(self._features, f"supports_set_{name}", False):
                continue
            wanted = self._to_backend(name, float(percent))
            try:
                setattr(self._backend, name, wanted)
            except Exception as exc:
                log.debug("Could not set %s: %s", name, exc)
                continue

            # Read it back. A backend that declines a value without raising
            # leaves the previous one in place, and the player hears a
            # control that does nothing -- which is how the OneCore pitch
            # floor was found. Better a log line than a silent no-op.
            if not getattr(self._features, f"supports_get_{name}", False):
                continue
            try:
                got = float(getattr(self._backend, name))
            except Exception:
                continue
            if got != got or abs(got - wanted) > 0.02:
                log.warning(
                    "%s ignored %s=%.2f and stayed at %s; the control will "
                    "appear to do nothing.",
                    self.backend_name, name, wanted,
                    "unset" if got != got else f"{got:.2f}",
                )

        if self.settings.speech_voice:
            self.set_voice(self.settings.speech_voice)

    def apply_settings(self) -> None:
        """Push the current voice, rate and pitch to the live backend."""
        with self._lock:
            self._apply_settings()

    def reload(self) -> None:
        """Re-open the backend after the player changed the setting."""
        with self._lock:
            self._backend = None
            self._features = None
            self._context = None
            if HAVE_PRISM:
                self._open(self.settings.speech_backend)

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def backend_name(self) -> str:
        if self._backend is None:
            return "none"
        try:
            return str(self._backend.name)
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------
    # Voice, rate and pitch
    #
    # A screen reader owns its own voice: NVDA reports supports_set_voice,
    # set_rate and set_pitch all false, because the user configures those in
    # NVDA and the game has no business overriding them. The platform voices
    # are the opposite -- OneCore and SAPI expose all three, and a player who
    # has no screen reader running has nowhere else to set them. So the
    # controls follow the backend rather than being always on or always off.
    # ------------------------------------------------------------------
    def supports(self, what: str) -> bool:
        """Whether the live backend can set ``voice``, ``rate`` or ``pitch``."""
        if self._features is None:
            return False
        return bool(getattr(self._features, f"supports_set_{what}", False))

    def voice_names(self) -> list[str]:
        """Every voice the live backend offers, in its own order."""
        return self._voice_names_of(self._backend)

    @staticmethod
    def _voice_names_of(backend) -> list[str]:
        if backend is None:
            return []
        try:
            features = backend.features
            if not features.supports_count_voices:
                return []
            if features.supports_refresh_voices:
                backend.refresh_voices()
            if not features.supports_get_voice_name:
                return []
            return [backend.get_voice_name(i) for i in range(backend.voices_count)]
        except Exception as exc:
            log.debug("Could not list voices: %s", exc)
            return []

    def set_voice(self, name: str) -> bool:
        """Select a voice by name. False if it is not available.

        By name rather than by index, for the same reason the audio device is
        remembered by name: indices shift when voices are installed or
        removed, and silently ending up on a different voice is worse than
        falling back to the default.
        """
        if not name or self._backend is None or not self.supports("voice"):
            return False
        with self._lock:
            for index, candidate in enumerate(self.voice_names()):
                if candidate == name:
                    try:
                        self._backend.voice = index
                        return True
                    except Exception as exc:
                        log.debug("Could not select voice %r: %s", name, exc)
                        return False
        log.info("Voice %r is not installed; keeping the current one.", name)
        return False

    def current_value(self, what: str) -> float:
        """The backend's current rate/pitch/volume as a percentage, or -1.

        Used to seed the sliders so they open where the voice actually is
        rather than at an arbitrary middle. Pitch commonly reads back as NaN
        when it has never been set, which is reported as -1.
        """
        if self._backend is None:
            return -1.0
        if not getattr(self._features, f"supports_get_{what}", False):
            return -1.0
        try:
            value = float(getattr(self._backend, what))
        except Exception:
            return -1.0
        if value != value:  # NaN: never set
            return -1.0
        return self._from_backend(what, value)

    def pitch_range(self, token: str = "") -> tuple[float, float]:
        """The Prism pitch span ``token``'s backend behaves sensibly over.

        Resolves the token the way the backend selection does, so "auto",
        "one_core" and "OneCore" all reach the same answer -- including the
        case that matters most, where "auto" has quietly resolved to OneCore
        because no screen reader is running.
        """
        name = _normalise(token or "auto")
        mapped = _BACKEND_ALIASES.get(name, "")
        if not mapped:
            # "auto", or something unrecognised: ask what is really in use.
            mapped = self.backend_name if token in ("", "auto") else name
        return PITCH_RANGES.get(_normalise(mapped), DEFAULT_PITCH_RANGE)

    def raises_pitch_only(self, token: str = "") -> bool:
        """Whether this backend can only go up from normal, never down."""
        return self.pitch_range(token) != DEFAULT_PITCH_RANGE

    def describe(self, token: str) -> dict:
        """What a backend offers, without switching to it.

        The settings dialog needs this for whichever backend is highlighted,
        which is not necessarily the one currently talking.
        """
        blank = {"voices": [], "voice": False, "rate": False, "pitch": False}
        if not HAVE_PRISM or self._context is None:
            return blank

        mapped = _BACKEND_ALIASES.get(_normalise(token or "auto"), "")
        try:
            backend = (
                self._context.create(BackendId[mapped])
                if mapped
                else self._context.create_best()
            )
        except Exception as exc:
            log.debug("Could not inspect backend %r: %s", token, exc)
            return blank

        try:
            features = backend.features
            return {
                "voices": self._voice_names_of(backend),
                "voice": bool(features.supports_set_voice),
                "rate": bool(features.supports_set_rate),
                "pitch": bool(features.supports_set_pitch),
            }
        except Exception:
            return blank

    def list_backends(self) -> list[tuple[str, str]]:
        """``(token, friendly name)`` for every backend actually present."""
        out = [("auto", "Automatic (recommended)")]
        if self._context is None:
            return out
        try:
            for index in range(self._context.backends_count):
                backend_id = self._context.id_of(index)
                if backend_id.name == "INVALID" or not self._context.exists(backend_id):
                    continue
                out.append((backend_id.name.lower(), self._context.name_of(backend_id)))
        except Exception as exc:
            log.debug("Could not enumerate backends: %s", exc)
        return out

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def speak(self, text: str, interrupt: bool = False) -> None:
        if not text or self._backend is None:
            return
        text = make_speakable(text)
        with self._lock:
            try:
                if interrupt and getattr(self._features, "supports_stop", False):
                    self._backend.stop()
                if getattr(self._features, "supports_speak", True):
                    self._backend.speak(text, interrupt=interrupt)
            except Exception as exc:
                log.debug("speak failed: %s", exc)

    def braille(self, text: str) -> None:
        if not text or self._backend is None:
            return
        text = make_speakable(text)
        with self._lock:
            try:
                if getattr(self._features, "supports_braille", False):
                    self._backend.braille(text)
            except Exception as exc:
                log.debug("braille failed: %s", exc)

    def report(self, text: str, interrupt: bool = True) -> None:
        """Speak *and* braille — the default for game status output."""
        if not text or self._backend is None:
            return
        want_speech = self.settings.speak_status
        want_braille = self.settings.braille_status
        if not (want_speech or want_braille):
            return

        text = make_speakable(text)
        with self._lock:
            try:
                if interrupt and getattr(self._features, "supports_stop", False):
                    self._backend.stop()
                if want_speech and want_braille and getattr(
                    self._features, "supports_output", False
                ):
                    self._backend.output(text, interrupt=interrupt)
                    return
            except Exception as exc:
                log.debug("output failed: %s", exc)

        if want_braille:
            self.braille(text)
        if want_speech:
            self.speak(text, interrupt=interrupt)

    def stop(self) -> None:
        if self._backend is None:
            return
        with self._lock:
            try:
                if getattr(self._features, "supports_stop", False):
                    self._backend.stop()
            except Exception:
                pass

    def shutdown(self) -> None:
        self.stop()
        with self._lock:
            self._backend = None
            self._features = None
            self._context = None
