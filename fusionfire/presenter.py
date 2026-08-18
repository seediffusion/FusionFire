"""Turns engine events into sound, speech and transcript lines.

This is the only place that knows both the rules and the output devices. The
engine emits events; the presenter renders them; the UI subscribes to the
handful that need a widget to react. Keeping the translation here means the
game panel never plays a sound and the engine never imports wx.

Making room for speech
----------------------
The status line is the game state; the sounds are flavour. When the two
arrive together the line has to win, and until recently it did not: the
gunshot, the reaction scream and the announcement all started at the same
instant, and whether you could make out "you hit for 12" came down to how
the player happened to have their volumes set.

The fix has to work around a hard asymmetry. Sound durations are known
exactly, straight from the files. Speech durations are not knowable at all:
the live NVDA backend reports ``supports_is_speaking``, ``supports_set_rate``
and ``supports_get_rate`` all false, so the game cannot be told when a line
finishes, cannot read the rate to estimate it, and cannot turn the speech up
to compete. Anything built on a guess at how long a line takes is therefore
built on the one quantity here that is unmeasurable.

So every number in this module is a *sound* measurement. The line waits
until every sound of that action has finished. Your attacks and the
machine's wait exactly the same way, so whoever swung, the outcome line
lands the moment its covering sounds clear.

An earlier version waited only until the sounds stopped being *loud*, on the
reasoning that a gunshot is 1.73s long but only 0.74s of that could mask a
word. It measured well and sounded wrong. The reaction screams are human
voices, and starting a status line in the quiet tail of a cry still lands
the speech mid-cry — which is what listening to it revealed and what
measuring it could not. The lesson is in the code: the wait is now the whole
sound, not the part of it a meter says is loud.

Two consequences worth knowing:

* the wait is decided at the *end* of a batch of events, once every sound
  has been started and its length is known, because the engine appends the
  status line before the reaction scream;
* braille and the transcript are written immediately, because neither
  collides with a sound, so a braille reader pays none of the latency.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import wx

from . import rng
from .assets import GROUPS
from .config import DUCK_LEVEL, SOUND_WAIT_CEILING
from .game.events import (
    BonusFinished,
    BonusStarted,
    CheatUnlocked,
    Event,
    GameOver,
    PlayMusic,
    PlaySound,
    Say,
    StartAmbience,
    StatsChanged,
    StopAmbience,
    StopMusic,
    TurnChanged,
)

log = logging.getLogger(__name__)


class Presenter:
    """Renders events, and keeps a transcript so anything missed can be reread."""

    #: How many lines of history to keep for the review pane.
    HISTORY_LIMIT = 400

    #: How long the duck takes to slide in. Long enough not to click, short
    #: enough to be under way before the first syllable.
    DUCK_FADE = 0.15

    def __init__(self, audio, speech, settings) -> None:
        self.audio = audio
        self.speech = speech
        self.settings = settings
        self.history: list[str] = []
        self._subscribers: list[Callable[[Event], None]] = []
        self._on_line: Callable[[str], None] | None = None
        #: The one line waiting to be spoken, as ``(text, interrupt)``.
        #:
        #: Exactly one, never a queue. A player who fires again before the
        #: last line was spoken wants to hear about the shot they just took,
        #: not to sit through a backlog describing shots that are already
        #: history � which is the same reasoning that already had these lines
        #: interrupting rather than queueing.
        self._pending: tuple[str, bool] | None = None
        self._pending_timer: wx.CallLater | None = None

    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[Event], None]) -> None:
        """Receive the events that need a UI reaction (turn changes, game over…)."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def set_line_sink(self, callback: Callable[[str], None] | None) -> None:
        """Where transcript lines go, usually the game panel's review box."""
        self._on_line = callback

    # ------------------------------------------------------------------
    def render(self, events: list[Event]) -> None:
        """Render one batch of events, holding any status line behind its sounds.

        The wait is decided at the *end* of the batch, once every sound of
        the action has been started and its length is known. It has to be:
        the engine appends the status line before the reaction scream, so at
        the moment the line is seen the loudest thing it has to clear has not
        been played yet. Deciding early is what left speech landing mid-cry.

        Only sounds that genuinely play are counted, which makes the two
        cases fall out for free -- a miss adds no scream, and screams
        switched off add nothing either, so both wait on the attack alone.
        """
        batch_end = 0.0
        held: tuple[str, bool] | None = None

        for event in events:
            try:
                if isinstance(event, Say):
                    stashed = self._begin_say(event)
                    if stashed is not None:
                        held = stashed
                elif isinstance(event, PlaySound):
                    batch_end = max(batch_end, self._render_sound(event))
                else:
                    self._render_one(event)
            except Exception:
                log.exception("Failed to render %r", event)

        # Schedule before the subscribers run, not after. The turn handover
        # is one of these events, and the game panel paces the opponent from
        # how long the line still has to wait -- so if the line is scheduled
        # afterwards, the panel measures a line that does not exist yet, puts
        # the opponent in ahead of it, and the opponent's own line cancels
        # the one the player was waiting for.
        if held is not None:
            self._schedule_pending(held, batch_end)

        for event in events:
            for subscriber in list(self._subscribers):
                try:
                    subscriber(event)
                except Exception:
                    log.exception("Event subscriber failed for %r", event)

    def _render_sound(self, event: PlaySound) -> float:
        """Start one effect. Returns when it will finish, in seconds from now.

        Zero for anything that does not actually play, so it contributes
        nothing to the wait.
        """
        # Reaction screams are the one category the player can silence,
        # because they are the loudest and most repeated thing in the game.
        if event.scream and not self.settings.screams_enabled:
            return 0.0

        # Resolve a group here rather than inside the audio engine, because
        # the wait has to be built from the scream that actually plays. The
        # screams run from 0.50s to 4.68s; waiting on the longest every time
        # would cost four seconds to clear a half-second cry.
        name = event.name
        if event.group:
            members = GROUPS.get(event.group)
            if not members:
                log.error("Unknown sound group: %r", event.group)
                return 0.0
            name = rng.choice(members)
        if not name:
            return 0.0

        if event.delay > 0:
            wx.CallLater(int(event.delay * 1000), self._start, name, event)
        else:
            self._start(name, event)

        try:
            return event.delay + self.audio.length_of(name)
        except Exception:
            log.exception("Could not measure %r.", name)
            return 0.0

    def _start(self, name: str, event: PlaySound) -> None:
        self.audio.play(name, volume=event.volume, pan=event.pan)

    def _render_one(self, event: Event) -> None:
        if isinstance(event, Say):
            self.announce(
                event.text, interrupt=event.interrupt, after=event.after
            )

        elif isinstance(event, PlaySound):
            self._render_sound(event)

        elif isinstance(event, PlayMusic):
            self.audio.play_music(event.name, looping=event.looping)

        elif isinstance(event, StopMusic):
            self.audio.stop_music(fade=event.fade)

        elif isinstance(event, StartAmbience):
            self.audio.start_ambience(event.key, event.name, volume=event.volume)

        elif isinstance(event, StopAmbience):
            self.audio.stop_ambience(event.key, fade=event.fade)

        elif isinstance(event, BonusFinished):
            # The bonus summary is shown in a results dialog by the UI; it is
            # recorded and brailled here, but not spoken a second time.
            self._record(event.summary)
            if self.settings.braille_status:
                self.speech.braille(event.summary)

        elif isinstance(event, CheatUnlocked):
            self.report(f"Cheat codes unlocked. They are written to {event.path}.")

        elif isinstance(event, (TurnChanged, StatsChanged, BonusStarted, GameOver)):
            pass  # purely for subscribers

    # ------------------------------------------------------------------
    # Status output
    # ------------------------------------------------------------------
    def report(self, text: str, interrupt: bool = True) -> None:
        """Speak, braille and record a line of status output, right now.

        The unconditional path, for lines nothing is competing with: a chat
        message, a cheat result, a bonus summary. Anything arriving on the
        back of a sound should go through :meth:`announce` instead.
        """
        if not text:
            return
        self.speech.report(text, interrupt=interrupt)
        self._record(text)

    def announce(
        self,
        text: str,
        interrupt: bool = True,
        after: str | None = None,
    ) -> None:
        """Report a line, holding the *spoken* half behind the sound ``after``.

        For callers outside :meth:`render`, which have only the one sound
        rather than a whole batch to wait on.
        """
        if not text:
            return
        stashed = self._begin_say(Say(text, interrupt, after))
        if stashed is None:
            return
        end = 0.0
        if after:
            try:
                end = self.audio.length_of(after)
            except Exception:
                log.exception("Could not measure %r.", after)
        self._schedule_pending(stashed, end)

    def _begin_say(self, event: Say) -> tuple[str, bool] | None:
        """Do a line's immediate half. Returns what still needs speaking.

        Braille, the transcript and the history are written straight away
        whatever happens. None of them collides with a sound effect, so there
        is no reason to make a braille reader wait, and ``repeat_last`` and
        the review pane are correct from the instant the thing happened
        rather than from whenever it gets said.

        Returns ``None`` when nothing is left to schedule -- either the line
        was spoken immediately, or speech is switched off.
        """
        text = event.text
        if not text:
            return None

        if not event.after:
            self.cancel_pending()
            self.report(text, interrupt=event.interrupt)
            return None

        self._record(text)
        if self.settings.braille_status:
            self.speech.braille(text)

        self.cancel_pending()
        if not self.settings.speak_status:
            # Braille only. Nothing is going to be spoken, so there is
            # nothing to hold back and nothing to turn the effects down for.
            return None
        return (text, event.interrupt)

    def _schedule_pending(self, line: tuple[str, bool], sound_end: float) -> None:
        """Speak ``line`` once the sounds have finished.

        Every line waits on its covering sounds and nothing else. The
        player's attacks and the machine's are treated identically, so the
        outcome lands at the same point after either kind of swing.
        """
        text, interrupt = line
        wait = min(max(0.0, sound_end), SOUND_WAIT_CEILING)
        self._pending = line
        try:
            self._pending_timer = wx.CallLater(int(wait * 1000), self.flush_pending)
            # wx does not expose how much of a CallLater has elapsed, and the
            # opponent's pacing needs it.
            self._pending_timer._ff_started = time.monotonic()
        except Exception:
            # Losing the authoritative channel is the worst outcome available,
            # so a scheduler that will not schedule falls back to the old
            # behaviour -- masked, but said.
            log.exception("Could not hold the status line back; speaking now.")
            self.flush_pending()

    def flush_pending(self) -> None:
        """Speak the held line, clearing the sounds in front of it first."""
        pending, self._pending = self._pending, None
        self._pending_timer = None
        if pending is None:
            return
        text, interrupt = pending
        if not self.settings.speak_status:
            return  # switched off while the line was in the air
        self._clear_the_way()
        self.speech.speak(text, interrupt=interrupt)

    def cancel_pending(self) -> None:
        """Drop a line that has not been spoken yet.

        It stays in the history and on the braille display; only the speech
        is abandoned, because something newer is about to take the channel.
        """
        timer, self._pending_timer = self._pending_timer, None
        self._pending = None
        if timer is not None:
            try:
                timer.Stop()
            except Exception:
                pass

    @property
    def pending_text(self) -> str | None:
        """The line waiting to be spoken, if any. For tests and diagnostics."""
        return self._pending[0] if self._pending else None

    def seconds_until_spoken(self) -> float:
        """How long until the held line is said. Zero if nothing is waiting.

        The game paces itself by this. Holding a line back only helps if it
        actually gets said, and the opponent moving on its own timer while a
        line is still waiting cancels that line — which, with the opponent
        thinking for a second and a line due in five, silenced the game
        completely.
        """
        if self._pending is None or self._pending_timer is None:
            return 0.0
        try:
            remaining = self._pending_timer.GetInterval() / 1000.0
            started = getattr(self._pending_timer, "_ff_started", None)
            if started is not None:
                remaining -= max(0.0, time.monotonic() - started)
            return max(0.0, remaining)
        except Exception:
            return 0.0

    def repeat_last(self) -> None:
        """Say the last line again, now.

        An explicit request outranks anything queued: the line the player is
        asking for *is* the pending one, so it is cancelled rather than
        allowed to arrive again a moment later. The sounds are cleared out of
        the way too — a repeat is usually asked for precisely because
        something covered the line the first time.
        """
        self.cancel_pending()
        if self.settings.speak_status:
            self._clear_the_way()
        if self.history:
            self.speech.report(self.history[-1])
        else:
            self.speech.report("Nothing to repeat.")

    # ------------------------------------------------------------------
    def _clear_the_way(self) -> None:
        """Duck whatever is still sounding, just before speaking.

        Normally nothing is: the line waits for its sounds to finish. This
        matters only where :data:`SOUND_WAIT_CEILING` cut the wait short --
        the minute-long power weapon impact, the long death sounds -- and for
        anything the opponent started while the line was waiting.
        """
        try:
            self.audio.duck_one_shots(DUCK_LEVEL, self.DUCK_FADE)
        except Exception:
            log.exception("Could not duck the effects bus.")

    def _record(self, text: str) -> None:
        """History and transcript. Never delayed; neither is heard."""
        self.history.append(text)
        if len(self.history) > self.HISTORY_LIMIT:
            del self.history[: len(self.history) - self.HISTORY_LIMIT]
        if self._on_line is not None:
            try:
                self._on_line(text)
            except Exception:
                log.exception("Transcript sink failed.")

    def transcript(self) -> str:
        return "\n".join(self.history)

    def clear_history(self) -> None:
        self.history.clear()
