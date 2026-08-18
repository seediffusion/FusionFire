"""Speech through accessible_output2, for Windows 8.1 and 7.

Prism is a modern library. Its native half is built against WinRT and the
current Visual C++ runtime, and the OneCore voices it can drive do not exist
before Windows 10 — so on Windows 8.1 and 7 it either fails to load or comes
up with nothing useful behind it.

accessible_output2 is the older answer and still the right one there. It has
talked to NVDA, JAWS, System Access, Window-Eyes, Dolphin and SAPI 5 since
long before any of this, through each reader's own client DLL, and it works
on every Windows this game could plausibly run on.

What it does not do is let you change anything. There is no voice list, no
rate, no pitch, no volume, and no way to stop speech once started — the
interface is speak, braille, output, and a check for whether the reader is
running. That is not a gap to paper over: on these systems the screen reader
owns those settings and the user sets them in the reader. The settings
dialog offers what this supports and nothing more, so no control is present
that would silently do nothing.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

try:
    from accessible_output2.outputs.auto import Auto

    HAVE_AO2 = True
except Exception as exc:  # pragma: no cover - optional dependency
    HAVE_AO2 = False
    log.debug("accessible_output2 unavailable: %s", exc)


class AO2Speech:
    """The same surface :class:`fusionfire.speech.Speech` presents.

    Everything the game asks for is answered; the parts accessible_output2
    cannot do report themselves as unsupported rather than pretending.
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._output = None
        if not HAVE_AO2:
            return
        try:
            self._output = Auto()
        except Exception as exc:
            log.error("Could not start accessible_output2: %s", exc)

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._output is not None

    @property
    def backend_name(self) -> str:
        if self._output is None:
            return "none"
        try:
            active = self._output.get_first_available_output()
            return getattr(active, "name", None) or "accessible_output2"
        except Exception:
            return "accessible_output2"

    def list_backends(self) -> list[tuple[str, str]]:
        """One entry. accessible_output2 picks the reader itself, in its own
        priority order, and exposing that choice would only offer the player
        a way to select a reader that is not running."""
        return [("auto", f"Automatic ({self.backend_name})")]

    # ------------------------------------------------------------------
    # Everything the library cannot do, answered honestly
    # ------------------------------------------------------------------
    def supports(self, what: str) -> bool:
        return False

    def voice_names(self) -> list[str]:
        return []

    def set_voice(self, name: str) -> bool:
        return False

    def current_value(self, what: str) -> float:
        return -1.0

    def pitch_range(self, token: str = "") -> tuple[float, float]:
        return (0.0, 1.0)

    def raises_pitch_only(self, token: str = "") -> bool:
        return False

    def describe(self, token: str) -> dict:
        return {"voices": [], "voice": False, "rate": False, "pitch": False}

    def apply_settings(self) -> None:
        """Nothing to apply: the reader owns its voice, rate and pitch."""

    def reload(self) -> None:
        if HAVE_AO2:
            try:
                self._output = Auto()
            except Exception as exc:
                log.error("Could not restart accessible_output2: %s", exc)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def speak(self, text: str, interrupt: bool = True) -> None:
        if not text or self._output is None:
            return
        from .speech import make_speakable

        try:
            self._output.speak(make_speakable(text), interrupt=interrupt)
        except Exception as exc:
            log.debug("speak failed: %s", exc)

    def braille(self, text: str) -> None:
        if not text or self._output is None:
            return
        from .speech import make_speakable

        try:
            self._output.braille(make_speakable(text))
        except Exception as exc:
            log.debug("braille failed: %s", exc)

    def report(self, text: str, interrupt: bool = True) -> None:
        """Speak and braille together, honouring the two settings."""
        if not text or self._output is None:
            return
        want_speech = self.settings.speak_status
        want_braille = self.settings.braille_status
        if not (want_speech or want_braille):
            return

        from .speech import make_speakable

        cleaned = make_speakable(text)
        if want_speech and want_braille:
            try:
                self._output.output(cleaned, interrupt=interrupt)
                return
            except Exception as exc:
                log.debug("output failed: %s", exc)
        if want_braille:
            self.braille(text)
        if want_speech:
            self.speak(text, interrupt=interrupt)

    def stop(self) -> None:
        """accessible_output2 has no stop. Speech runs to its end.

        Not an oversight to route around: the screen reader clients it uses
        mostly interrupt on the next utterance instead, which is what
        ``interrupt=True`` on the next line achieves.
        """

    def shutdown(self) -> None:
        self._output = None
