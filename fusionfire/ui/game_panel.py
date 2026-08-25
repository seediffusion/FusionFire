"""The match screen.

Visually this is a status line and a transcript. Functionally it is a
keyboard and gamepad handler wrapped around an :class:`~fusionfire.game.engine.Engine`,
plus the timers that drive the opponent's moves and the power_weapon clock.

The panel never plays a sound directly. It calls the engine, hands the
resulting events to the presenter, and reacts to the two or three events
that need a widget to change.
"""

from __future__ import annotations

import logging
import time

import wx

from .. import rng
from ..assets import COMMENTS
from ..game import cheats
from ..game import constants as K
from ..game.constants import Phase, Side
from ..game.engine import Engine
from ..game.events import GameOver, StatsChanged, StrikeResolved, TurnChanged
from ..input.actions import Action
from ..input.keymap import action_for, help_text
from ..game.bonus import DEFAULT_FOE
from ..game.bonus import deltas as bonus_deltas
from ..game.bonus import snapshot as bonus_snapshot
from .bonus_dialog import BonusDialog
from .cheat_prompt import CheatPrompt
from .comment_dialog import CommentDialog
from .widgets import append_line, message, review_box

log = logging.getLogger(__name__)


class GamePanel(wx.Panel):
    """Drives one match, offline or online."""

    #: The pad fights here rather than navigating: while this panel is up, A
    #: is a taunt and the triggers are the weapons. See
    #: :meth:`fusionfire.app.AppContext.refresh_input_mode`.
    gamepad_navigation = False

    def __init__(
        self,
        parent: wx.Window,
        app_ctx,
        engine: Engine,
        *,
        net=None,
        spectating: bool = False,
    ) -> None:
        super().__init__(parent)
        self.ctx = app_ctx
        self.engine = engine
        self.net = net
        #: Watching from a ringside seat. Everything that reads the fight
        #: still works -- the transcript, the status keys, the volumes -- and
        #: everything that would change it is refused, because nothing this
        #: end sends is carried anywhere.
        self.spectating = spectating
        self.presenter = app_ctx.presenter
        self.audio = app_ctx.audio
        self.speech = app_ctx.speech
        self.settings = app_ctx.settings
        self.cheat = CheatPrompt(self.audio, self.speech)
        self._bonus_open = False
        #: The other player's bonus result, held until ours is out of the
        #: way. Both rounds run at once, so theirs can land while our own
        #: notes are still on screen.
        self._pending_peer_bonus: dict | None = None
        self._ai_timer: wx.CallLater | None = None
        self._power_weapon_timer: wx.CallLater | None = None
        self._intro_timer: wx.CallLater | None = None
        #: When the opponent's turn began, online. Used to notice a wait
        #: that has gone on too long.
        self._waiting_since: float | None = None
        self._warned_waiting = False

        self._build()
        self.presenter.subscribe(self._on_event)
        self.presenter.set_line_sink(self._append_line)

        self._tick_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self._tick_timer)
        self._tick_timer.Start(1000)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = wx.BoxSizer(wx.VERTICAL)

        self.status_line = wx.StaticText(self, label="Preparing...")
        self.status_line.SetName("Match status")
        outer.Add(self.status_line, 0, wx.ALL, 8)

        self.transcript = review_box(self, "Match transcript")
        outer.Add(self.transcript, 1, wx.ALL | wx.EXPAND, 8)

        hint = wx.StaticText(
            self,
            label=(
                "Ringside. 4 and 5 for each fighter, R to repeat, escape to leave."
                if self.spectating
                else "1 shoot, 2 lash, 3 load, 4 your status, 5 their status, "
                "6 power weapon, 7 heal, 8 talk, 9 bomb. F1 for the full list."
            ),
        )
        outer.Add(hint, 0, wx.ALL, 8)

        self.SetSizer(outer)

        # EVT_CHAR_HOOK rather than EVT_KEY_DOWN. Enter, Escape and the arrow
        # keys are navigation keys: wx hands them to dialog navigation and the
        # multiline transcript control before a KEY_DOWN binding ever sees
        # them, which is why Enter did nothing here. CHAR_HOOK fires first, on
        # the focused window and every parent, so the game's hotkeys work
        # wherever focus happens to be sitting.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)

    def focus_game(self) -> None:
        self.SetFocusIgnoringChildren()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def begin(self, *, first: Side | None = None) -> None:
        """Play the machine's boot-up, then start the match when it ends."""
        events = self.engine.start(
            use_power_weapon=self.settings.use_power_weapon and not self.engine.online,
            first=first,
        )
        self.presenter.render(events)
        self.focus_game()

        # Let computerstart.wav finish before the score, the idle hum and the
        # first turn arrive. Timed from the file so replacing it still works.
        delay = self.audio.length_of("computerstart")
        if delay > 0:
            self._intro_timer = wx.CallLater(int(delay * 1000), self._begin_play)
        else:
            self._begin_play()

    def _begin_play(self) -> None:
        self._intro_timer = None
        if self.engine.phase is not Phase.SETUP:
            return
        self._render(self.engine.begin_play())

    def teardown(self) -> None:
        """Stop every timer and unhook from the presenter."""
        try:
            self._tick_timer.Stop()
        except Exception:
            pass
        self._cancel_ai_timer()
        self._cancel_power_weapon_timer()
        self._cancel_intro_timer()
        # A status line still waiting for its gunshot to finish belongs to a
        # match that is over. Speaking it into the menu would be a ghost.
        self.presenter.cancel_pending()
        self.presenter.unsubscribe(self._on_event)
        self.presenter.set_line_sink(None)

    def _cancel_ai_timer(self) -> None:
        timer, self._ai_timer = self._ai_timer, None
        if timer is not None and timer.IsRunning():
            timer.Stop()

    def _cancel_intro_timer(self) -> None:
        timer, self._intro_timer = self._intro_timer, None
        if timer is not None and timer.IsRunning():
            timer.Stop()

    def _cancel_power_weapon_timer(self) -> None:
        """Stop a drumroll mid-flight, so leaving the match cannot fire it."""
        timer, self._power_weapon_timer = self._power_weapon_timer, None
        if timer is not None and timer.IsRunning():
            timer.Stop()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def _on_key(self, event: wx.KeyEvent) -> None:
        """Every keystroke during a match arrives here first.

        Not calling ``Skip()`` consumes the key, which is what stops Enter
        being eaten by the transcript control and the arrows by dialog
        navigation. Anything the game does not claim is skipped so the menu
        bar, Tab and the screen reader keep working.
        """
        code = event.GetKeyCode()

        if self.cheat.active:
            # While the prompt is open it owns the keyboard, so the number
            # row types a quantity instead of firing a gun.
            if code == wx.WXK_ESCAPE:
                self.cheat.cancel()
                self.speech.speak("Cheat prompt closed.")
            elif code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
                self._submit_cheat()
            elif code == wx.WXK_BACK:
                self.cheat.backspace()
            else:
                # Collect the code itself. CHAR_HOOK carries the unicode key,
                # so the prompt does not need a separate EVT_CHAR binding —
                # which would never fire anyway now that we consume the key.
                typed = event.GetUnicodeKey()
                if typed and 32 <= typed < 127:
                    self.cheat.type_char(chr(typed))
                else:
                    event.Skip()
            return

        if code == wx.WXK_F1:
            self._show_help()
            return

        action = action_for(event)
        if action is None:
            event.Skip()
            return
        self.handle_action(action)

    #: What a seat may still do. Everything else is somebody else's fight.
    _RINGSIDE_ACTIONS = frozenset({
        Action.PLAYER_STATUS,
        Action.OPPONENT_STATUS,
        Action.REPEAT_LAST,
        Action.SOUND_UP,
        Action.SOUND_DOWN,
        Action.MUSIC_UP,
        Action.MUSIC_DOWN,
        Action.TOGGLE_MUSIC,
        Action.TOGGLE_SCREAMS,
        Action.QUIT_MATCH,
    })

    def handle_action(self, action: Action) -> None:
        """Single entry point for keyboard and gamepad alike."""
        if self._bonus_open:
            return  # the bonus dialog is modal and handles its own input

        if self.spectating and action not in self._RINGSIDE_ACTIONS:
            self.speech.report("You are watching. Press escape to leave the ringside.")
            return

        handler = self._ACTIONS.get(action)
        if handler is None:
            return
        try:
            handler(self)
        except Exception:
            log.exception("Action %s failed.", action)

    # ------------------------------------------------------------------
    # Combat actions
    # ------------------------------------------------------------------
    def _require_turn(self) -> bool:
        if self.engine.phase is not Phase.PLAYING:
            return False
        if self._power_weapon_timer is not None:
            # The turn is still nominally the player's during the drumroll,
            # but the shot is already away — let it land before anything else.
            self.speech.report("The power weapon is already in the air.")
            return False
        if self.engine.turn is not Side.PLAYER:
            self.speech.report("Wait your turn.")
            return False
        return True

    def _do_fire(self) -> None:
        if not self._require_turn():
            return
        self._render(self.engine.fire_gun())

    def _do_whip(self) -> None:
        if not self._require_turn():
            return
        self._render(self.engine.crack_whip())

    def _do_load(self) -> None:
        if not self._require_turn():
            return
        before = self.engine.player.gun_loaded
        self._render(self.engine.load_gun())
        # Only report a load that happened. The engine refuses a second one,
        # and telling the other player about a refusal would desync them.
        if self.net is not None and self.engine.player.gun_loaded and not before:
            self.net.send("load")

    def _do_restore(self) -> None:
        if not self._require_turn():
            return
        before = self.engine.player.health
        self._render(self.engine.restore_health())
        gained = self.engine.player.health - before
        if self.net is not None and gained > 0:
            self.net.send("heal", amount=int(gained))

    def _do_bomb(self) -> None:
        if not self._require_turn():
            return
        self._render(self.engine.use_bomb())

    def _do_power_weapon(self) -> None:
        if self.engine.online:
            self._do_comment()  # 6 sends a message online, as documented
            return
        if not self._require_turn():
            return
        if self._power_weapon_timer is not None:
            return  # already in flight

        events, launched = self.engine.launch_power_weapon()
        self._render(events)
        if not launched:
            return

        # Let the drumroll run its full length before the outcome lands. The
        # delay is taken from the file rather than hard-coded, so replacing
        # dr.wav with a longer or shorter one still lines up.
        delay = self.audio.length_of("dr") or 21.0
        self._power_weapon_timer = wx.CallLater(
            int(delay * 1000), self._resolve_power_weapon
        )

    def _resolve_power_weapon(self) -> None:
        self._power_weapon_timer = None
        if self.engine.phase is not Phase.PLAYING:
            return
        self._render(self.engine.resolve_power_weapon())

    # ------------------------------------------------------------------
    # Free actions
    # ------------------------------------------------------------------
    def _do_player_status(self) -> None:
        self._render(self.engine.player_status())

    def _do_opponent_status(self) -> None:
        self._render(self.engine.opponent_status())

    def _do_repeat(self) -> None:
        self.presenter.repeat_last()

    def _do_laugh(self) -> None:
        self._render(self.engine.laugh())
        if self.net is not None:
            self.net.send("laugh")

    def _do_taunt(self) -> None:
        """Fire off one of the recorded comments without picking it.

        The comment dialog is the considered version of this: it lists all
        three and lets you choose. A taunt is the unconsidered one — a single
        button that says something rude immediately, which is what makes it
        usable in the second between your turn ending and theirs starting.
        """
        key = rng.choice(sorted(COMMENTS))
        self._render(self.engine.play_comment(key))
        if self.net is not None:
            self.net.send("comment", key=key)

    def _do_comment(self) -> None:
        dialog = CommentDialog(self, allow_chat=self.net is not None)
        try:
            with self.ctx.modal_input(dialog):
                answer = dialog.ShowModal()
            if answer != wx.ID_OK:
                return
            if dialog.chat_text and self.net is not None:
                self.net.send("chat", text=dialog.chat_text)
                self.presenter.report(f"You said: {dialog.chat_text}")
            elif dialog.is_laugh:
                self._do_laugh()
            elif dialog.selected_key:
                self._render(self.engine.play_comment(dialog.selected_key))
                if self.net is not None:
                    self.net.send("comment", key=dialog.selected_key)
        finally:
            dialog.Destroy()
            self.focus_game()

    # ------------------------------------------------------------------
    # Mixing and session
    # ------------------------------------------------------------------
    def _do_sound_up(self) -> None:
        level = self.audio.adjust_sound_volume(0.05)
        self.speech.speak(f"Sound {int(level * 100)} percent.")

    def _do_sound_down(self) -> None:
        level = self.audio.adjust_sound_volume(-0.05)
        self.speech.speak(f"Sound {int(level * 100)} percent.")

    def _do_music_up(self) -> None:
        level = self.audio.adjust_music_volume(0.05)
        self.speech.speak(f"Music {int(level * 100)} percent.")

    def _do_music_down(self) -> None:
        level = self.audio.adjust_music_volume(-0.05)
        self.speech.speak(f"Music {int(level * 100)} percent.")

    def _do_toggle_music(self) -> None:
        on = self.audio.toggle_music()
        self.speech.speak("Music on." if on else "Music off.")
        if on:
            self.presenter.render(self.engine.resume_music())

    def _do_toggle_screams(self) -> None:
        self.settings.screams_enabled = not self.settings.screams_enabled
        self.speech.speak(
            "Screams on." if self.settings.screams_enabled else "Screams off."
        )

    def _do_toggle_stats(self) -> None:
        self.settings.stats_enabled = not self.settings.stats_enabled
        # Whatever was banked before stays banked; only new activity stops
        # being recorded. Same contract the original documented.
        self.speech.report(
            "Statistics recording on."
            if self.settings.stats_enabled
            else "Statistics recording off. Everything recorded so far is kept."
        )

    def _do_cheat_prompt(self) -> None:
        # First, because it holds whatever the difficulty and the unlock say.
        # Refusing to open the prompt at all is the point: it is invisible,
        # so a player let into it would be typing a code into nothing and
        # only find out it was refused after pressing Enter.
        if self.engine.online:
            self.speech.report("Cheats are off in an online match.")
            return
        if not self.engine.difficulty.cheats_allowed:
            self.speech.report("Cheats are disabled on this difficulty.")
            return
        # Unlocked for good once earned: either the file is already there
        # from a previous session, or this player's best match cleared the
        # threshold.
        if not cheats.unlocked(self.ctx.stats.best_points):
            self.speech.report(
                f"Cheats unlock at {K.CHEAT_UNLOCK_POINTS} points in a single match."
            )
            return
        self.cheat.open()

    def _submit_cheat(self) -> None:
        result = self.cheat.submit(
            self.engine.player,
            self.engine.opponent,
            self.engine.difficulty,
            online=self.engine.online,
        )
        if result is not None:
            self.presenter.report(result.message)
            self._refresh_status()

    def _refresh_ringside_status(self) -> None:
        """Both fighters, named, because neither of them is the reader."""
        player, opponent = self.engine.player, self.engine.opponent
        to_move = player if self.engine.turn is Side.PLAYER else opponent
        turn = "fight over" if self.engine.phase is Phase.FINISHED else f"{to_move.name} to move"
        self.status_line.SetLabel(
            f"{player.name} {max(0, player.health)} health, {player.points} points. "
            f"{opponent.name} {max(0, opponent.health)} health, "
            f"{opponent.points} points. {turn}."
        )

    def _do_quit(self) -> None:
        self.ctx.leave_match()

    # ------------------------------------------------------------------
    _ACTIONS = {
        Action.FIRE_GUN: _do_fire,
        Action.CRACK_WHIP: _do_whip,
        Action.LOAD_GUN: _do_load,
        Action.RESTORE_HEALTH: _do_restore,
        Action.USE_BOMB: _do_bomb,
        Action.POWER_WEAPON: _do_power_weapon,
        Action.PLAYER_STATUS: _do_player_status,
        Action.OPPONENT_STATUS: _do_opponent_status,
        Action.REPEAT_LAST: _do_repeat,
        Action.AUDIO_COMMENT: _do_comment,
        Action.LAUGH: _do_laugh,
        Action.TAUNT: _do_taunt,
        Action.MARK_NOTE: _do_toggle_screams,
        Action.SOUND_UP: _do_sound_up,
        Action.SOUND_DOWN: _do_sound_down,
        Action.MUSIC_UP: _do_music_up,
        Action.MUSIC_DOWN: _do_music_down,
        Action.TOGGLE_MUSIC: _do_toggle_music,
        Action.TOGGLE_SCREAMS: _do_toggle_screams,
        Action.TOGGLE_STATS: _do_toggle_stats,
        Action.CHEAT_PROMPT: _do_cheat_prompt,
        Action.QUIT_MATCH: _do_quit,
    }

    # ------------------------------------------------------------------
    # Engine plumbing
    # ------------------------------------------------------------------
    def _render(self, events) -> None:
        self.presenter.render(events)

    def _on_event(self, event) -> None:
        if isinstance(event, StrikeResolved):
            self.ctx.record_strike(event)
            self._buzz_if_hit(event)
            self._send_strike(event)
        elif isinstance(event, TurnChanged):
            self._refresh_status()
            if event.side is Side.OPPONENT and self.net is None:
                self._schedule_opponent()
            elif event.side is Side.PLAYER and self.net is not None and not self.spectating:
                # The top of a round, which is where the offline game rolls
                # for a bonus too -- offline it does so just after the
                # machine's move, which is the same moment.
                self._consider_bonus()
            if self.net is not None:
                self._waiting_since = (
                    time.monotonic() if event.side is Side.OPPONENT else None
                )
                self._warned_waiting = False
        elif isinstance(event, StatsChanged):
            self._refresh_status()
        elif isinstance(event, GameOver):
            self._on_game_over(event)

    def _buzz_if_hit(self, event: StrikeResolved) -> None:
        """Vibrate the pad when something lands on the player.

        A third channel, and the only one that is not competing for the
        player's ears. The scream tells you that you were hit and the status
        line tells you how badly, but both of those have to queue behind
        whatever is already speaking; the buzz arrives on the impact itself.
        Which is why the weapon picks the pattern — a whip flick and a
        gunshot have to be tellable apart by feel alone, before either has
        been named out loud.

        Only what lands on you. Feeling your own hits land would make the
        two indistinguishable, which is the one thing this is for.
        """
        if event.victim is not Side.PLAYER or event.outcome == "miss":
            return
        self.ctx.gamepad.rumble_for(event.weapon)

    # ------------------------------------------------------------------
    # Telling the other player what happened
    #
    # Online, each side resolves its own actions and reports the result;
    # the peer applies it. Anything that is not sent simply never happened
    # as far as they are concerned, and because attacks end a turn, a
    # missing one leaves both engines waiting for the other to move.
    # ------------------------------------------------------------------
    def _send_strike(self, event: StrikeResolved) -> None:
        """Report one of our own attacks."""
        if self.net is None or self.spectating or event.attacker is not Side.PLAYER:
            return
        # The protocol carries the three weapons online play uses. The power
        # weapon is offline-only, and a backfire cannot reach the wire.
        if event.weapon not in ("gun", "whip", "bomb"):
            return
        if event.outcome not in ("hit", "miss"):
            return
        self.net.send(
            "strike",
            weapon=event.weapon,
            outcome=event.outcome,
            damage=int(event.damage),
        )

    def _schedule_opponent(self) -> None:
        """Give the machine a beat before it moves, so turns are audible.

        The beat starts after your own status line has been spoken, not the
        instant your turn ends. Anything the machine does cancels a line that
        is still waiting, so a machine that thinks for a second while your
        line is due in five will talk over every single one — measured at
        zero lines out of seventy-four actually reaching the player, which is
        a silent game rather than a fast one.
        """
        self._cancel_ai_timer()
        wait = self.presenter.seconds_until_spoken() + self.engine.opponent_delay()
        self._ai_timer = wx.CallLater(int(wait * 1000), self._opponent_move)

    def _opponent_move(self) -> None:
        self._ai_timer = None
        if self._bonus_open:
            # A bonus round owns the stage. The machine may not move while it
            # is up, and its turn is put back so nothing is lost.
            self._schedule_opponent()
            return
        if self.engine.phase is not Phase.PLAYING:
            return
        self._render(self.engine.opponent_move())
        self._consider_bonus()

    def _on_tick(self, event: wx.TimerEvent) -> None:
        if self.engine.phase is Phase.PLAYING:
            self._render(self.engine.tick())
        self._check_still_waiting()

    #: How long to wait for an online opponent before saying something.
    #: Long enough that thinking about a move is never interrupted, short
    #: enough that a stuck match does not just sit there.
    WAIT_WARNING = 45.0

    def _check_still_waiting(self) -> None:
        """Say something if the other player's turn never seems to arrive.

        A match can only be waiting on a live connection like this if the
        two engines have stopped agreeing about whose go it is, and that is
        otherwise completely silent -- both sides sit there, nothing is
        logged, and the game simply appears to have stopped. Saying so does
        not repair it, but it tells the player what they are looking at
        instead of leaving them guessing.
        """
        if self.net is None or self._waiting_since is None or self._warned_waiting:
            return
        if self.engine.phase is not Phase.PLAYING:
            return
        if time.monotonic() - self._waiting_since < self.WAIT_WARNING:
            return

        self._warned_waiting = True
        self.presenter.report(
            f"Still waiting for {self.engine.opponent.name}. If nothing "
            "happens, the connection may have got out of step; press escape "
            "to leave the match."
        )

    # ------------------------------------------------------------------
    # Bonus round
    # ------------------------------------------------------------------
    def _may_offer_bonus(self) -> bool:
        """Whether this end is the one that decides a bonus round happens.

        Offline there is only one engine, so it decides. Online there are
        two, and they roll their own dice: asked independently they would
        disagree, and a round only one player is in is worse than no round
        at all. So the host rolls and tells the other end, exactly as it
        already decides who moves first.
        """
        return self.net is None or self.net.is_host

    def _consider_bonus(self) -> None:
        """Roll for a bonus round at the top of a round, if it is ours to roll."""
        if self._bonus_open or not self._may_offer_bonus():
            return
        if self.engine.phase is not Phase.PLAYING:
            return
        if not self.engine.should_spawn_bonus():
            return
        wx.CallLater(600, self._offer_bonus)

    def _offer_bonus(self) -> None:
        """Start a bonus round here, and on the other end if there is one."""
        seconds = self.settings.bonus_seconds
        if self.net is not None:
            # Sent first. The two rounds should start together, and ours is
            # about to take the modal dialog and stop pumping anything else.
            self.net.send("bonus_start", seconds=seconds)
        self._open_bonus(seconds)

    def begin_peer_bonus(self, seconds: int) -> None:
        """The host has started a bonus round; join it."""
        self._open_bonus(seconds)

    def receive_peer_bonus(self, message: dict) -> None:
        """Apply what the other player's notes were worth.

        Held back while our own round is still up. Their result can carry a
        death, and folding one in underneath a modal dialog would put the
        result of the match on screen behind the notes.
        """
        if self._bonus_open:
            self._pending_peer_bonus = message
            return
        self._render(self.engine.apply_peer_bonus(message))

    def _drain_peer_bonus(self) -> None:
        message, self._pending_peer_bonus = self._pending_peer_bonus, None
        if message is not None and self.engine.phase is not Phase.FINISHED:
            self._render(self.engine.apply_peer_bonus(message))

    def _send_bonus(self, before_player, before_opponent) -> None:
        """Tell the other player what our notes turned out to be worth."""
        if self.net is None:
            return
        mine = bonus_deltas(before_player, bonus_snapshot(self.engine.player))
        theirs = bonus_deltas(before_opponent, bonus_snapshot(self.engine.opponent))
        self.net.send(
            "bonus",
            health=mine["health"],
            points=mine["points"],
            bullets=mine["bullets"],
            restores=mine["restores"],
            bombs=mine["bombs"],
            # What our notes did to them. Only the three a note can reach.
            foe_health=theirs["health"],
            foe_points=theirs["points"],
            foe_bombs=theirs["bombs"],
        )

    def _open_bonus(self, seconds: int | None = None) -> None:
        if self._bonus_open or not self.engine.enter_bonus():
            return
        if seconds is None:
            seconds = self.settings.bonus_seconds
        # Two locks, because the window being modal is not one. wx keeps
        # pumping timers behind a modal dialog, so anything already scheduled
        # still fires: a machine turn that fell due in the beat before the
        # notes came up, or a power weapon shot still counting down its
        # drumroll. Cancelling those timers stops what is scheduled; the
        # engine's BONUS phase refuses an attack from either side however it
        # arrives, including one routed in from a gamepad.
        self._cancel_ai_timer()
        self._cancel_power_weapon_timer()
        self._bonus_open = True
        dialog = BonusDialog(
            self,
            self.engine.difficulty,
            self.audio,
            self.speech,
            seconds=seconds,
            foe=self.engine.opponent.name if self.engine.online else DEFAULT_FOE,
        )
        self.ctx.set_modal_input_target(dialog)
        try:
            dialog.ShowModal()
            result = dialog.result
        finally:
            self.ctx.set_modal_input_target(None)
            dialog.Destroy()
            self._bonus_open = False
            self.engine.leave_bonus()
            self.focus_game()

        if result is not None:
            # The results go in a dialog, and the match waits for it to be
            # dismissed before the next turn can start.
            with self.ctx.modal_input():
                message(self, result.summary, "Bonus results")
            before_player = bonus_snapshot(self.engine.player)
            before_opponent = bonus_snapshot(self.engine.opponent)
            self._render(self.engine.apply_bonus(result))
            self._send_bonus(before_player, before_opponent)

        # Theirs was held back while ours was on screen.
        self._drain_peer_bonus()

        # The machine's turn was deferred, so it still gets to move once the
        # notes are done. Normally the turn is the player's after a bonus and
        # nothing is scheduled.
        if (
            self.net is None
            and self.engine.phase is Phase.PLAYING
            and self.engine.turn is Side.OPPONENT
        ):
            self._schedule_opponent()

    # ------------------------------------------------------------------
    # Status readout
    # ------------------------------------------------------------------
    def _refresh_status(self) -> None:
        player, opponent = self.engine.player, self.engine.opponent
        if self.spectating:
            self._refresh_ringside_status()
            return
        turn = "your turn" if self.engine.turn is Side.PLAYER else f"{opponent.name}'s turn"
        if self.engine.phase is Phase.FINISHED:
            turn = "match over"
        bullets = "unlimited" if player.unlimited_bullets else str(player.bullets)
        self.status_line.SetLabel(
            f"You {max(0, player.health)} health, {player.points} points, "
            f"{bullets} bullets, {player.bombs} bombs  |  "
            f"{opponent.name} {max(0, opponent.health)} health, "
            f"{opponent.points} points  |  {turn}"
        )
        self.Layout()

    def _append_line(self, text: str) -> None:
        append_line(self.transcript, text)

    # ------------------------------------------------------------------
    def _on_game_over(self, event: GameOver) -> None:
        self._cancel_ai_timer()
        self._cancel_power_weapon_timer()
        try:
            self._tick_timer.Stop()
        except Exception:
            pass
        self.ctx.record_result(self.engine, event.winner)

        if self.net is not None:
            self.net.send("resign", reason="Match finished.")

        points = self.engine.player.points
        # The announcement belongs to the moment of unlock, not to every
        # win afterwards: speak it only when this match crossed the
        # threshold and the file was not already there from an earlier
        # session.
        if (
            self.engine.difficulty.cheats_allowed
            and cheats.earned(points)
            and not cheats.already_unlocked()
        ):
            path = cheats.write_cheat_file()
            if path:
                self.presenter.report(f"Cheat codes unlocked. They are written to {path}.")

        # Let the death cry and the win/lose piece both finish before the
        # dialog speaks over them. Timed from the files, so replacing a wav
        # still lines up.
        won = event.winner is Side.PLAYER
        suffix = "o" if self.engine.online else ""
        # The death cries are sound effects with no online variant;
        # only the music has a separate online piece.
        death = "computerdie" if won else "userdie"
        music = ("win" if won else "die") + suffix
        delay = max(self.audio.length_of(death), self.audio.length_of(music))
        if delay <= 0:
            delay = 1.2  # nothing measured; keep a beat of silence
        wx.CallLater(int(delay * 1000), self._offer_rematch, event)

    def _offer_rematch(self, event: GameOver) -> None:
        if not self:  # panel already destroyed
            return
        won = event.winner is Side.PLAYER
        caption = "You win" if won else "You lose"
        body = f"{event.reason}\n\nPlay again?"
        if self.net is not None:
            with self.ctx.modal_input():
                message(self, event.reason, caption)
            self.ctx.leave_match()
            return
        with self.ctx.modal_input():
            again = message(self, body, caption, wx.YES_NO | wx.ICON_INFORMATION)
        if again == wx.ID_YES:
            self.ctx.restart_match()
        else:
            self.ctx.leave_match()

    def _show_help(self) -> None:
        with self.ctx.modal_input():
            message(self, help_text(), "Hotkeys")
        self.focus_game()
