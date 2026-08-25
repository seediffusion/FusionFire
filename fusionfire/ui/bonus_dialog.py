"""The item bonus round.

Thirteen notes, arrow keys to move and space to mark. The clock ticks
audibly the whole time and a horn ends it, which is the entire interface —
the window exists to own the keyboard focus, not to be looked at.

How long it runs is passed in: the player's own setting offline, the host's
online. Ten seconds by default, and anything from one to thirty.

The round is deliberately generous about what counts as an input: the arrow
keys, the gamepad D-pad and the left stick all move; space and the pad's A
button both mark. Set short there is no time to go hunting for the right
control, and set long there is no reason to make anyone.
"""

from __future__ import annotations

import wx

from ..game import constants as K
from ..game.bonus import DEFAULT_FOE, BonusResult, BonusRound
from ..input.actions import Action


class BonusDialog(wx.Dialog):
    """Modal for one bonus round. Closes itself when the horn sounds."""

    #: A dialog, but not a menu: the pad keeps its match bindings here so the
    #: D-pad and stick still move between notes rather than trying to move a
    #: focus ring around a window with one control on it.
    gamepad_navigation = False

    def __init__(
        self,
        parent: wx.Window,
        difficulty,
        audio,
        speech,
        *,
        seconds: int = K.DEFAULT_BONUS_SECONDS,
        foe: str = DEFAULT_FOE,
    ) -> None:
        super().__init__(
            parent,
            title="Pick a note",
            style=wx.DEFAULT_DIALOG_STYLE & ~(wx.CLOSE_BOX | wx.SYSTEM_MENU),
        )
        self.round = BonusRound(difficulty, foe)
        self.audio = audio
        self.speech = speech
        self.result: BonusResult | None = None
        self.seconds = max(1, min(K.MAX_BONUS_SECONDS, int(seconds)))
        self._remaining = float(self.seconds)
        self._clock_handle = None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        heading = wx.StaticText(
            panel,
            label=(
                f"{self.round.foe} has hidden items in {K.BONUS_NOTE_COUNT} notes.\n"
                "Left and right to move, space to mark. Mark as many as you dare."
            ),
        )
        outer.Add(heading, 0, wx.ALL, 12)

        self.status = wx.StaticText(panel, label=self.round.describe_cursor())
        self.status.SetName("Current note")
        outer.Add(self.status, 0, wx.ALL, 12)

        panel.SetSizer(outer)
        outer.Fit(panel)
        self.Fit()
        self.CentreOnParent()

        # CHAR_HOOK, not KEY_DOWN: left and right are dialog navigation keys,
        # so wx consumes them moving focus between controls before a KEY_DOWN
        # binding sees anything. That is why the arrows did nothing here.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        panel.SetFocus()

        self._start()

    # ------------------------------------------------------------------
    def _start(self) -> None:
        plural = "" if self.seconds == 1 else "s"
        self.speech.report(
            f"Pick a note. {self.seconds} second{plural}. "
            "Left and right to move, space to mark."
        )
        self.audio.play(self.round.note_sound)
        self._clock_handle = self.audio.play("itemclock", looping=True, volume=0.7)

        self._ticker = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self._ticker)
        self._ticker.Start(int(K.BONUS_TICK * 1000))

    def _on_tick(self, event: wx.TimerEvent) -> None:
        self._remaining -= K.BONUS_TICK
        if self._remaining <= 0:
            self._finish()

    # ------------------------------------------------------------------
    def _on_key(self, event: wx.KeyEvent) -> None:
        """Claim the three keys the round uses; skip everything else.

        Escape is deliberately not handled. The round ends on its own, so
        there is nothing to escape from however long it is set to, and
        swallowing the key would trap focus if it ever failed to close
        itself.
        """
        code = event.GetKeyCode()
        if code == wx.WXK_LEFT:
            self.handle_action(Action.MOVE_LEFT)
        elif code == wx.WXK_RIGHT:
            self.handle_action(Action.MOVE_RIGHT)
        elif code == wx.WXK_SPACE:
            self.handle_action(Action.MARK_NOTE)
        else:
            event.Skip()

    def handle_action(self, action: Action) -> None:
        """Entry point for both keyboard and gamepad."""
        if self.round.finished:
            return
        if action is Action.MOVE_LEFT:
            self._move(-1)
        elif action is Action.MOVE_RIGHT:
            self._move(1)
        elif action in (Action.MARK_NOTE, Action.TAUNT, Action.FIRE_GUN):
            # Space, the pad's A button and the trigger that fires the gun.
            # A is the taunt during a match and marks a note here; a round
            # set short leaves no time to go looking for one right button.
            self._toggle()

    def _move(self, delta: int) -> None:
        before = self.round.cursor
        after = self.round.move(delta)
        if after == before:
            return
        # The note itself is the position indicator — the octave rises left
        # to right, so the pitch tells you where you are without counting.
        self.audio.play(self.round.note_sound)
        self._refresh()

    def _toggle(self) -> None:
        marked = self.round.toggle()
        self.audio.play("itemclick")
        self.speech.speak("Marked." if marked else "Unmarked.", interrupt=True)
        self._refresh()

    def _refresh(self) -> None:
        label = self.round.describe_cursor()
        self.status.SetLabel(label)
        self.status.GetParent().Layout()

    # ------------------------------------------------------------------
    def _finish(self) -> None:
        if self.round.finished:
            return
        try:
            self._ticker.Stop()
        except Exception:
            pass
        if self._clock_handle is not None:
            self._clock_handle.stop()
        self.audio.play("itemtimeout")
        self.result = self.round.finish()
        # The horn is the announcement that time is up. The results are read
        # after it finishes, timed from the file rather than a guess.
        delay = self.audio.length_of("itemtimeout")
        wx.CallLater(int(delay * 1000), self._close)

    def _close(self) -> None:
        if self.IsModal():
            self.EndModal(wx.ID_OK)
        else:  # pragma: no cover - only if shown non-modally
            self.Destroy()
