"""The invisible cheat prompt.

Deliberately not a dialog. The original opened no window: a short bang told
you the prompt was live, every character you typed was spoken back by a
recorded voice, enter submitted and escape backed out. That is a genuinely
good piece of audio interface design — it keeps your hands and your ears
where they already are — so it is reproduced rather than replaced with a
text box.

This class is pure state plus sound. The game panel routes keys to it while
:attr:`active` is set.
"""

from __future__ import annotations

from ..assets import speech_sound
from ..game import cheats
from ..game import constants as K


class CheatPrompt:
    """Collects a cheat code by ear."""

    def __init__(self, audio, speech) -> None:
        self.audio = audio
        self.speech = speech
        self.active = False
        self.buffer = ""

    # ------------------------------------------------------------------
    def open(self) -> None:
        self.active = True
        self.buffer = ""
        self.audio.play("enterch")

    def cancel(self) -> None:
        if not self.active:
            return
        self.active = False
        self.buffer = ""
        self.audio.play("exitch")

    # ------------------------------------------------------------------
    def type_char(self, char: str) -> None:
        """Accept one character, echoing it the way the original did."""
        if not self.active or len(self.buffer) >= K.MAX_CHEAT_INPUT:
            return
        self.buffer += char
        self.audio.play("type", volume=0.6)

        # Letters and digits have recorded names; everything else (the space
        # between quantity and code) just gets the typing click.
        sound = speech_sound(char)
        if sound is not None:
            self.audio.play(sound)

    def backspace(self) -> None:
        if not self.active or not self.buffer:
            return
        self.buffer = self.buffer[:-1]
        self.audio.play("type", volume=0.4)

    # ------------------------------------------------------------------
    def submit(self, player, opponent, difficulty) -> cheats.CheatResult | None:
        """Validate and apply. Returns the result, or None if not open."""
        if not self.active:
            return None
        text, self.buffer = self.buffer, ""
        self.active = False

        result = cheats.apply(text, player, opponent, difficulty)
        self.audio.play("select" if result.ok else "error")
        return result
