"""Application wiring.

:class:`AppContext` owns everything with a lifetime longer than a match —
settings, audio, speech, gamepad, statistics — and is the single object the
panels talk to when they need something outside themselves. The wx.App below
builds it, shows the frame and tears it all down again in order.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import shutil
import sys
import threading
from pathlib import Path

import wx

from . import __title__, __version__, assets, paths
from .audio import NULL_HANDLE, AudioEngine
from .config import Settings, Stats
from .game import greetings, names
from .game.constants import DEFAULT_ONLINE_SUPPLY as K_DEFAULT_SUPPLY
from .game.constants import Phase, Side
from .game.engine import Combatant, Engine
from .game.events import StatsChanged, TurnChanged
from .game.difficulty import get as get_difficulty
from .input.actions import Action
from .input.gamepad import GamepadManager
from .presenter import Presenter
from .speech import open_speech
from .ui.navigator import Navigator

log = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    """Rotating log in the user data folder. Never in the install directory."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if root.handlers:
        return
    try:
        handler = logging.handlers.RotatingFileHandler(
            paths.log_file(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    except OSError:
        pass
    if verbose:
        root.addHandler(logging.StreamHandler(sys.stderr))


class _AnyModal:
    """Stands in for a modal the game holds no object for.

    A wx message box is built, shown and destroyed inside a single call, so
    there is nothing to hand the pad. This has neither a ``handle_action``
    nor a ``gamepad_navigation``, which is exactly the wanted behaviour: the
    pad navigates the box, and nothing it does can reach the match waiting
    behind it.
    """

    __slots__ = ()


_ANY_MODAL = _AnyModal()


def _pan_for(side) -> float:
    """Put the two fighters on opposite sides of the ringside's head."""
    from .game.constants import Side as _Side

    return -0.3 if side is _Side.PLAYER else 0.3


def _weapon_sound(side, weapon) -> str:
    """Which recording an attack from ``side`` uses.

    The two sets of weapon recordings exist so a fighter can tell their own
    shot from the machine's. At the ringside neither shot is yours, so the
    same two sets do the same job for a different reason: they tell the two
    fighters apart. Either way it is the side that decides, so this needs to
    know nothing about who is listening.
    """
    from .game.constants import Side as _Side

    mine = side is _Side.PLAYER
    return {
        "gun": "usergun" if mine else "computergun",
        "whip": "userwhip" if mine else "computerwhip",
        "bomb": "userbomb" if mine else "computerbomb",
    }[weapon.value]


class AppContext:
    """Long-lived services, and the transitions between screens."""

    def __init__(self, settings: Settings | None = None) -> None:
        # The app may have loaded these already, to set the window
        # appearance before any window existed.
        self.settings = settings if settings is not None else Settings.load()
        self.stats = Stats.load()
        self.audio = AudioEngine(self.settings)
        self.speech = open_speech(self.settings)
        self.presenter = Presenter(self.audio, self.speech, self.settings)
        self.gamepad = GamepadManager(self.settings, self._gamepad_action)
        self.navigator = Navigator()
        self.frame = None
        self.engine: Engine | None = None
        self.net = None
        self._modal_input_target = None
        self._online_dialog = None
        #: True while a disconnection we caused is still on its way back to
        #: us through the session's callback.
        self._expect_disconnect = False
        #: The last line the connection reported about itself, so the same
        #: one is not spoken twice.
        self._net_status = ""
        #: Whether this player asked the relay to keep seats for onlookers.
        self._ringside = False
        #: What a seat has been told about the two fighters, by name and
        #: gender, keyed by which end of the relay they are on.
        self._fighters: dict[str, tuple[str, str]] = {}
        self._ringside_supplies = (
            self.settings.online_bullets,
            self.settings.online_restores,
        )
        #: The looping clock of a bonus round a seat is sitting through.
        self._ringside_clock = None
        #: How many are watching, so the fighters are only told when it
        #: changes rather than every time the relay repeats itself.
        self._seats = 0
        #: The bullets and restores this player asked for in the online
        #: dialog. Sent in our hello; used only if the relay makes us host.
        self._online_supplies = (
            self.settings.online_bullets,
            self.settings.online_restores,
        )
        #: Set in start(), once there is a wx.App and a frame to theme.
        self.theme = None
        #: True while the launch jingle is holding the front menu back.
        self._intro_pending = False
        self._intro_timer = None
        #: True while an update check or download is already in flight, so a
        #: second one cannot be started on top of it.
        self._update_busy = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def start(self) -> None:
        from .ui.main_frame import MainFrame
        from .ui.theme import ThemeWatcher

        self.frame = MainFrame(self)
        # Before the frame is shown, so it comes up in the right colours
        # rather than flashing white first.
        self.theme = ThemeWatcher(wx.GetApp(), self.settings)
        self.theme.attach(self.frame)
        self.frame.Show()

        # First of everything, so the answer is on its way back before the
        # launch has decided what to play, let alone started playing it.
        self._check_updates_at_startup()

        missing = assets.verify()
        if missing:
            self.presenter.report(
                f"Warning: {len(missing)} sound files are missing. "
                "The game will run but some effects will be silent."
            )

        if self.settings.gamepad_enabled:
            self.gamepad.start()

        greeting = greetings.for_today(self.settings.birthday)
        if greeting is not None:
            self.show_menu()
            if greeting.music:
                self.audio.play_music(greeting.music, looping=False)
            self.presenter.report(greeting.text)
        elif self.settings.play_intro_music:
            self._begin_launch_intro()
        else:
            self.show_menu()
            self.presenter.report(
                f"{__title__} {__version__} ready. Choose an option from the menu."
            )

    # ------------------------------------------------------------------
    # Updating
    # ------------------------------------------------------------------
    @staticmethod
    def _later(callback, *args) -> None:
        """``wx.CallAfter`` that tolerates the window having gone already.

        The update worker outlives nothing in particular: a player who quits
        while a check is in flight leaves a thread holding a callback and no
        application to run it on, and wx answers that with a RuntimeError
        from deep inside a daemon thread. There is nothing to report at that
        point -- the thing the result was for has been closed.
        """
        try:
            if wx.GetApp() is None:
                return
            wx.CallAfter(callback, *args)
        except RuntimeError:
            log.debug("Dropped a background result; the application had closed.")

    def _check_updates_at_startup(self) -> None:
        """The automatic check, if the player left it switched on.

        Quiet, and after everything else is up. A player whose connection is
        down, or who plays on a machine with no network at all, is not told
        about it: they did not ask a question, so there is no answer they
        need. The switch is its own thing because this is the only moment the
        game contacts anybody without being asked to.
        """
        if not self.settings.check_for_updates:
            return
        self.check_for_updates(announce=False)

    def check_for_updates(self, *, announce: bool = True) -> None:
        """Ask GitHub whether there is a newer release, on a worker thread.

        ``announce`` is what separates the automatic check at startup from
        the one on the Help menu. The automatic one says nothing unless there
        is something to install; the one the player asked for answers either
        way, including when the answer is that GitHub could not be reached.
        """
        if self._update_busy:
            if announce:
                self.presenter.report("Already checking for updates.")
            return
        self._update_busy = True
        if announce:
            self.presenter.report("Checking for updates...")

        def worker() -> None:
            from . import update

            try:
                release = update.check()
            except update.UpdateError as exc:
                self._later(self._update_check_failed, str(exc), announce)
                return
            self._later(self._update_check_done, release, announce)

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _update_check_failed(self, reason: str, announce: bool) -> None:
        self._update_busy = False
        log.info("Update check failed: %s", reason)
        if announce:
            self.presenter.report(reason)

    def _update_check_done(self, release, announce: bool) -> None:
        self._update_busy = False
        if not release.newer:
            log.info("Up to date; latest release is %s.", release.tag)
            if announce:
                self.presenter.report(f"Fusion Fire {__version__} is up to date.")
            return
        if not announce and self.engine is not None:
            # The startup check came back after the player had already
            # started fighting. Nobody asked for this now, and a prompt over
            # a match is worse than one at the next launch.
            log.info("Update %s available; not interrupting a match.", release.tag)
            return
        self._offer_update(release)

    def _quiet_the_launch(self) -> None:
        """Stop whatever the opening is playing, so an update can be heard.

        An update prompt arriving over the launch jingle is two things at
        once, and the one that has to be answered is the quieter of them --
        worse still if the answer is yes, because then the jingle plays on
        over the download. Skipping it also brings the front menu up, which
        is where the player is standing when the prompt closes.

        A match owns the sound outright and is left alone; the only check
        that can reach one is a check the player asked for.
        """
        if self.engine is not None:
            return
        if not self.skip_intro_music():
            # A birthday piece rather than the jingle, or nothing at all.
            self.audio.stop_music()

    def _offer_update(self, release) -> None:
        """Ask, then do it. Nothing is downloaded until the player says yes."""
        from .ui.update_dialog import UpdatePrompt

        self._quiet_the_launch()
        prompt = UpdatePrompt(self.frame, release, __version__)
        try:
            self.presenter.report(prompt.announcement())
            if prompt.ShowModal() != wx.ID_OK:
                self.presenter.report("Not updated.")
                return
        finally:
            prompt.Destroy()
        self._run_update(release)

    def _run_update(self, release) -> None:
        """Download, unpack and hand over to the swap helper.

        The download runs on a worker thread and the dialog stays modal, so
        the window keeps answering and Cancel keeps working. Everything the
        worker touches is under the user's own data directory: the installed
        copy is not opened at all until the helper takes over, by which time
        this process is on its way out.
        """
        import tempfile

        from . import update
        from .ui.update_dialog import UpdateProgressDialog

        self._update_busy = True
        dialog = UpdateProgressDialog(self.frame, self.presenter)
        outcome: dict = {}

        def worker() -> None:
            staging = update.staging_dir()
            scratch = Path(tempfile.mkdtemp(prefix="ff-update-"))
            archive = scratch / "FusionFire.zip"
            try:
                update.download(
                    archive, release.download_url, progress=dialog.on_progress
                )
                self._later(dialog.report, "Unpacking...")
                update.stage(archive, staging)
                self._later(dialog.report, "Closing to finish.")
                helper = update.install(staging)
                outcome["helper"] = helper
            except update.UpdateError as exc:
                outcome["error"] = exc
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
                self._later(self._update_finished, dialog, outcome)

        threading.Thread(target=worker, name="update-install", daemon=True).start()
        dialog.ShowModal()

    def _update_finished(self, dialog, outcome: dict) -> None:
        self._update_busy = False
        try:
            if dialog.IsModal():
                dialog.EndModal(wx.ID_OK)
            dialog.Destroy()
        except RuntimeError:
            pass

        from . import update

        error = outcome.get("error")
        if isinstance(error, update.UpdateCancelled):
            self.presenter.report(str(error))
            return
        if error is not None:
            self.presenter.report(f"Update failed: {error}")
            return

        # The helper is running and waiting for this process to go away, so
        # going away is the last thing left to do. Straight out rather than
        # through the usual exit music: something is already counting on the
        # window being gone.
        #
        # One more trip through the event loop first. This is running inside
        # the progress dialog's own modal loop, and tearing the frame down
        # from in there destroys the window the loop is still standing on.
        log.info("Update staged; handing over to %s.", outcome.get("helper"))
        self._later(self._quit_for_update)

    def _quit_for_update(self) -> None:
        """Close for good, so the helper can replace the files."""
        self.shutdown()
        if self.frame is not None:
            self.frame.Destroy()
            self.frame = None

    def _begin_launch_intro(self) -> None:
        """Play the launch jingle, holding the front menu back until it is
        skipped with Enter or plays out on its own.

        The jingle is an event piece rather than background score, so it
        plays even when the in-match music toggle is off; this setting is
        its own switch. If nothing actually starts playing (no audio device,
        missing file), the menu comes up right away with the spoken ready
        message instead.
        """
        self.audio.play_music("intro", looping=False)
        if not self.audio.playing_music("intro"):
            self.show_menu()
            self.presenter.report(
                f"{__title__} {__version__} ready. Choose an option from the menu."
            )
            return
        self._intro_pending = True
        self.frame.show_intro()
        duration = self.audio.length_of("intro")
        # Just after the last note, so the track's ending is not cut off.
        self._intro_timer = wx.CallLater(
            int(max(0.0, duration) * 1000) + 300, self._intro_played_out
        )

    def _intro_played_out(self) -> None:
        """The jingle ended on its own; put the front menu up."""
        if not self._intro_pending:
            return
        self._intro_pending = False
        self._intro_timer = None
        self.frame.end_intro()
        self.show_menu()

    def skip_intro_music(self) -> bool:
        """Stop the launch jingle. True if it was actually playing.

        The caller consumes the keypress that ended it rather than treating
        it as a menu choice. While the jingle is holding the front menu
        back, skipping it also brings the menu up.
        """
        if not self.audio.playing_music("intro"):
            return False
        self.audio.stop_music(fade=0.0)
        if self._intro_pending:
            self._intro_pending = False
            if self._intro_timer is not None:
                self._intro_timer.Stop()
                self._intro_timer = None
            self.frame.end_intro()
            self.show_menu()
        return True

    def apply_theme(self) -> bool:
        """Put the chosen theme into effect. False if a restart is needed
        for the window chrome to follow."""
        if self.theme is None:
            return True
        return self.theme.apply()

    def apply_gamepad_setting(self) -> None:
        if self.settings.gamepad_enabled:
            self.gamepad.start()
        else:
            self.gamepad.stop()

    # ------------------------------------------------------------------
    # Screen transitions
    # ------------------------------------------------------------------
    def show_menu(self) -> None:
        from .ui.main_frame import MenuPanel

        self.engine = None
        self.audio.stop_all_ambience()
        self.frame.swap_content(MenuPanel(self.frame, self))
        self.frame.set_status("Main menu.")

    def menu_choice(self, key: str) -> None:
        handlers = {
            "offline": self.start_offline,
            "online": self.start_online,
            "stats": self.show_stats,
            "settings": self.show_settings,
            "help": lambda: self.frame.show_help(),
            "about": lambda: self.frame.show_about(),
            "updates": self.check_for_updates,
            "exit": lambda: self.frame.Close(),
        }
        handler = handlers.get(key)
        if handler is not None:
            handler()

    def show_stats(self) -> None:
        from .ui.main_frame import StatsPanel

        self.frame.swap_content(StatsPanel(self.frame, self))
        self.frame.set_status("Statistics.")

    def show_settings(self) -> None:
        from .ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.frame, self)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def reset_stats(self) -> None:
        self.stats = Stats()
        self.stats.save()

    # ------------------------------------------------------------------
    # Offline match
    # ------------------------------------------------------------------
    def _ensure_character(self) -> bool:
        """Ask who the player is, once. Returns False if they backed out."""
        if self.settings.player_name:
            return True

        from .ui.setup_dialog import SetupDialog

        dialog = SetupDialog(self.frame, self.settings)
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.choice is None:
                return False
            dialog.apply_to(self.settings)
            self.settings.save()
            self.presenter.report(dialog.choice.announcement())
        finally:
            dialog.Destroy()
        return True

    def start_offline(self) -> None:
        if not self._ensure_character():
            return

        from .ui.setup_dialog import OpponentDialog

        dialog = OpponentDialog(self.frame, self.settings)
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.selected is None:
                return
            difficulty = dialog.selected
        finally:
            dialog.Destroy()
        self.settings.save()

        # The machine is entitled to say no, and is grumpier at mealtimes.
        excuse = greetings.maybe_refuse()
        if excuse is not None:
            self.presenter.report(f"{excuse} Try again in a moment.")
            return

        self._launch_match(difficulty)

    def _launch_match(self, difficulty) -> None:
        from .ui.game_panel import GamePanel

        player = Combatant(name=self.settings.player_name, gender=self.settings.player_gender)
        opponent = Combatant(name=names.random_machine_name(), gender="male")
        self.engine = Engine(player, opponent, difficulty)

        panel = GamePanel(self.frame, self, self.engine)
        self.frame.swap_content(panel)
        self.frame.set_status(f"Fighting {opponent.name} on {difficulty.label}.")
        panel.begin()

    def restart_match(self) -> None:
        self._launch_match(get_difficulty(self.settings.difficulty))

    def leave_match(self) -> None:
        if self.engine is not None:
            self.presenter.render(self.engine.abandon())
        net, self.net = self.net, None
        if net is not None:
            # That reason is written for the other player, because they are
            # the one it is sent to. Closing hands the same string back to
            # us through the disconnect callback, so the fact that this one
            # is ours is noted first -- otherwise the player who just quit,
            # or who just won, is solemnly informed that their opponent
            # left the game.
            self._expect_disconnect = True
            net.close("Your opponent left the game.")
        self.show_menu()

    # ------------------------------------------------------------------
    # Online match
    # ------------------------------------------------------------------
    def start_online(self) -> None:
        if not self._ensure_character():
            return

        from .net.session import HostSession, JoinSession, RelaySession
        from .ui.online_dialog import OnlineDialog, WaitingDialog

        dialog = OnlineDialog(self.frame, self.settings, self.presenter)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            connection, host, port, passphrase = (
                dialog.connection, dialog.host, dialog.port, dialog.passphrase
            )
            secure = dialog.secure
            hosting = connection == "p2p" and dialog.hosting
            bind_host = dialog.bind_host
            self._ringside = dialog.ringside
            shared_address = dialog._shareable_address() if hosting else ""
            self._online_supplies = (dialog.bullets, dialog.restores)
        finally:
            dialog.Destroy()
        self.settings.save()

        if connection == "relay":
            session_class: type = RelaySession
        elif hosting:
            session_class = HostSession
        else:
            session_class = JoinSession
        self._net_status = ""
        self.net = session_class(
            on_message=lambda m: wx.CallAfter(self._on_net_message, m),
            on_connected=lambda: wx.CallAfter(self._on_net_connected),
            on_disconnected=lambda r: wx.CallAfter(self._on_net_disconnected, r),
            on_status=lambda t: self._later(self._on_net_status, t),
        )

        if connection == "relay":
            # Deliberately just the first step. The session reports each one
            # after it, including the long wait this used to hide.
            waiting_text = f"Connecting to the relay server {host} on port {port}..."
        elif hosting:
            # Name the address rather than telling the player to go and find
            # one. They are waiting on somebody else to type it in, and the
            # thing they need to read out is a fact this process already
            # knows.
            where = f"{shared_address} port {port}" if shared_address else f"port {port}"
            if secure:
                waiting_text = (
                    f"Waiting for an opponent on {where}.\n\n"
                    "Give them that address and the passphrase."
                )
            else:
                waiting_text = (
                    f"Waiting for an opponent on {where}.\n\n"
                    "Give them that address. They just dial it, no passphrase."
                )
        else:
            waiting_text = f"Connecting to {host} on port {port}..."
        self._online_dialog = WaitingDialog(self.frame, waiting_text)
        self.audio.play_music("connecting", looping=True)
        self.presenter.report(waiting_text.replace("\n", " "))

        try:
            if connection == "relay":
                self.net.connect_relay(
                    host, port, passphrase, secure=secure, ringside=self._ringside
                )
            elif hosting:
                self.net.listen(
                    port=port, passphrase=passphrase, secure=secure, host=bind_host
                )
            else:
                self.net.connect(host, port, passphrase, secure=secure)
        except (OSError, ValueError) as exc:
            self._close_waiting()
            self.audio.stop_music()
            self.presenter.report(f"Could not start the online game: {exc}")
            self.net = None
            return

        if self._online_dialog.ShowModal() == wx.ID_CANCEL and self.net is not None:
            self.net.close("Cancelled.")
            self.net = None
            self.audio.stop_music()
        self._close_waiting()

    def _on_net_status(self, text: str) -> None:
        """Report what the connection is doing, while it is still doing it.

        Both halves matter. The dialog is what a sighted player is looking
        at, and speech is the only way a screen reader hears about it at
        all -- a static text quietly changing is announced by nothing.

        The same line arriving twice is not news, so it is dropped rather
        than spoken again over whatever the player was listening to.
        """
        if not text or text == self._net_status:
            return
        self._net_status = text
        if self._online_dialog is not None:
            self._online_dialog.set_text(text)
        self.presenter.report(text)

    def _close_waiting(self) -> None:
        dialog, self._online_dialog = self._online_dialog, None
        if dialog is None:
            return
        try:
            if dialog.IsModal():
                dialog.EndModal(wx.ID_OK)
            dialog.Destroy()
        except RuntimeError:
            pass

    def _on_net_connected(self) -> None:
        self.audio.stop_music()
        self._close_waiting()
        if self.net.is_spectator:
            # Nothing a seat sends is carried, so there is nobody to
            # introduce ourselves to. Wait to be told what is going on.
            self.presenter.report(
                "You have a ringside seat. Waiting for the fight to reach you."
            )
            return
        bullets, restores = self._online_supplies
        self.net.send(
            "hello",
            version=1,
            name=self.settings.player_name,
            gender=self.settings.player_gender,
            # Offered by both ends, because the relay has only just told us
            # which of us is the host. The joiner's numbers are dropped on
            # arrival; see _begin_online_match.
            bullets=bullets,
            restores=restores,
        )
        self.presenter.report("Connected. Waiting for your opponent's details.")

    def _match_finished(self) -> bool:
        """True once the match has reached its own ending.

        The two engines each reach it independently, from the same strike.
        Whatever arrives from the other end afterwards is therefore an echo
        of something already known, and the screen must not be torn down on
        the strength of it -- the result dialog is still waiting behind the
        win or lose piece.
        """
        return self.engine is not None and self.engine.phase is Phase.FINISHED

    def _on_net_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "ringside":
            self._seats_changed(int(message["seats"]))
            return
        if self.net is not None and self.net.is_spectator:
            self._watch(message)
            return
        if kind == "state":
            return  # for the ringside; a fighter already knows all of it
        if kind == "hello":
            self._begin_online_match(message)
        elif kind == "chat":
            # By name, the way every other line about them is. "Opponent
            # says" told the player nothing they did not already know, and
            # in a game where the machine is called Kernel Panic and the
            # other player picked their own name, it is the one line that
            # refused to use it. The fallback covers a chat arriving before
            # the hello that names them, which no ordinary match produces.
            who = self.engine.opponent.name if self.engine else "Your opponent"
            self.presenter.report(f"{who} says: {message['text']}")
        elif kind == "comment":
            self.audio.play(f"comment_{message['key']}", pan=0.25)
        elif kind == "laugh":
            from .assets import laugh_group

            gender = self.engine.opponent.gender if self.engine else "male"
            self.audio.play_one_of(laugh_group(gender), pan=0.25)
        elif kind == "bonus_start":
            panel = self._match_panel()
            if panel is not None:
                panel.begin_peer_bonus(message["seconds"])
        elif kind == "bonus":
            panel = self._match_panel()
            if panel is not None:
                panel.receive_peer_bonus(message)
            elif self.engine is not None:
                self.presenter.render(self.engine.apply_peer_bonus(message))
        elif kind == "resign":
            if self._match_finished():
                # Not a resignation: the peer announcing the ending we have
                # already reached ourselves. Acting on it dropped both
                # players back at the menu the moment an online match
                # finished, taking the win or lose dialog with it -- both
                # ends send this, so both ends were kicked out. It stays on
                # the wire as the safety net it is for a peer whose engine
                # never got there, which is the only case left that needs
                # telling.
                log.debug("Peer reported the finish we already have: %r",
                          message.get("reason"))
                return
            reason = message.get("reason") or "Your opponent left the game."
            self.presenter.report(reason)
            self.leave_match()
        elif self.engine is not None:
            self._apply_remote_move(message)

    def _match_panel(self):
        """The match screen, if that is what is on the frame right now.

        The bonus round belongs to the panel -- it owns the dialog, the
        timers and the modal input target -- so the two bonus messages are
        the only ones that have to be handed somewhere other than the engine.
        """
        from .ui.game_panel import GamePanel

        panel = getattr(self.frame, "content", None)
        return panel if isinstance(panel, GamePanel) else None

    def _seats_changed(self, seats: int) -> None:
        """Someone has taken or given up a ringside seat.

        Only the relay sends this, and only to the two fighters -- a seat
        cannot speak, so the relay saying so is the one way they can know
        anybody is out there. Whoever is hosting then posts the scoreboard,
        because a watcher who sat down ten minutes in has missed every
        strike that got the fight to where it is.
        """
        before, self._seats = self._seats, max(0, seats)
        if self._seats == before:
            return
        if self._seats > before:
            self.presenter.report(
                f"Someone has taken a ringside seat. {self._describe_seats()}."
            )
            self._post_the_scoreboard()
        else:
            self.presenter.report(f"A ringside seat is empty. {self._describe_seats()}.")

    def _describe_seats(self) -> str:
        if self._seats == 0:
            return "Nobody is watching"
        if self._seats == 1:
            return "One person is watching"
        return f"{self._seats} people are watching"

    def _post_the_scoreboard(self) -> None:
        """Send the whole fight in one message, for whoever just sat down."""
        if self.net is None or self.engine is None or not self.net.is_host:
            return
        us, them = self.engine.player, self.engine.opponent
        self.net.send(
            "state",
            host_name=us.name,
            host_gender=us.gender,
            host_health=max(-400, us.health),
            host_points=us.points,
            host_bullets=us.bullets,
            host_restores=us.restores,
            host_bombs=us.bombs,
            host_loaded=int(us.gun_loaded),
            join_name=them.name,
            join_gender=them.gender,
            join_health=max(-400, them.health),
            join_points=them.points,
            join_bullets=them.bullets,
            join_restores=them.restores,
            join_bombs=them.bombs,
            join_loaded=int(them.gun_loaded),
            turn="host" if self.engine.turn is Side.PLAYER else "joiner",
        )

    def _begin_online_match(self, hello: dict) -> None:
        if self.engine is not None:
            return  # a duplicate hello; ignore it

        from .game.constants import DEFAULT_ONLINE_SUPPLY
        from .ui.game_panel import GamePanel

        opponent_name, opponent_gender = hello["name"], hello["gender"]

        # One side has to decide the supplies, or the two engines start the
        # match disagreeing about how much ammunition is in it -- and the
        # host is already the side that decides the turn order. Both players
        # sent their preference; whichever of us is not the host drops its
        # own and takes what arrived. An opponent running a build from before
        # any of this sends neither field, and both ends then land on the
        # same default rather than on each other's guess.
        if self.net.is_host:
            bullets, restores = self._online_supplies
        else:
            bullets = hello.get("bullets", DEFAULT_ONLINE_SUPPLY)
            restores = hello.get("restores", DEFAULT_ONLINE_SUPPLY)

        player = Combatant(
            name=self.settings.player_name,
            gender=self.settings.player_gender,
            bullets=bullets,
            restores=restores,
        )
        opponent = Combatant(name=opponent_name, gender=opponent_gender)
        self.engine = Engine(
            player, opponent, get_difficulty(self.settings.difficulty), online=True
        )

        panel = GamePanel(self.frame, self, self.engine, net=self.net)
        self.frame.swap_content(panel)
        self.frame.set_status(f"Online against {opponent_name}.")
        # The host moves first, deterministically, so both ends agree on the
        # turn order without needing a round trip to negotiate it. For a
        # relayed match the relay assigns the roles by arrival order.
        first = Side.PLAYER if self.net.is_host else Side.OPPONENT
        panel.begin(first=first)

    # ------------------------------------------------------------------
    # The ringside
    #
    # A seat hears both fighters and is neither of them. Every message
    # arrives tagged with whoever sent it, and is applied to a local engine
    # in which the host is the player and the joiner is the opponent -- an
    # arbitrary choice, made once and kept, so that "host" always means the
    # same side of the scoreboard.
    # ------------------------------------------------------------------
    def _watch(self, message: dict) -> None:
        kind = message.get("type")
        source = message.get("source", "host")

        if kind == "hello":
            self._ringside_hello(source, message)
            return
        if kind == "state":
            self._ringside_state(message)
            return
        if self.engine is None:
            return  # nothing to apply it to yet

        side = Side.PLAYER if source == "host" else Side.OPPONENT
        who = self.engine.player if side is Side.PLAYER else self.engine.opponent

        if kind == "chat":
            self.presenter.report(f"{who.name} says: {message['text']}")
        elif kind == "comment":
            self.audio.play(f"comment_{message['key']}", pan=_pan_for(side))
        elif kind == "laugh":
            from .assets import laugh_group

            self.audio.play_one_of(laugh_group(who.gender), pan=_pan_for(side))
        elif kind == "resign":
            if not self._match_finished():
                self.presenter.report(
                    message.get("reason") or f"{who.name} left the fight."
                )
                self.leave_match()
        elif kind == "bonus_start":
            self._watch_bonus(int(message["seconds"]))
        elif kind == "bonus":
            self.presenter.render(self.engine.apply_peer_bonus(message, side))
        elif kind in ("strike", "heal", "load"):
            self._apply_move(side, message)

    def _watch_bonus(self, seconds: int) -> None:
        """Sit through the bonus round with them.

        A seat is not picking anything, so there is no dialog and no notes.
        What there is, and what was missing, is the sound of it: the fighters
        go quiet for ten seconds while they choose, and without the clock and
        the horn a watcher is left wondering whether the fight has stopped.
        """
        from .game import constants as K

        seconds = max(1, min(K.MAX_BONUS_SECONDS, seconds))
        self.presenter.report(
            f"{self.engine.opponent.name} has hidden items in "
            f"{K.BONUS_NOTE_COUNT} notes. Both fighters are picking."
            if self.engine is not None
            else "The bonus round has started."
        )
        self._ringside_clock = self.audio.play("itemclock", looping=True, volume=0.55)
        self._later_by(seconds, self._ringside_horn)

    def _ringside_horn(self) -> None:
        """Time is up on a bonus round we watched rather than played."""
        clock, self._ringside_clock = self._ringside_clock, None
        if clock is not None:
            try:
                clock.stop()
            except Exception:
                log.debug("The ringside clock had already stopped.")
        self.audio.play("itemtimeout")

    @staticmethod
    def _later_by(seconds: float, callback) -> None:
        wx.CallLater(int(seconds * 1000), callback)

    def _ringside_hello(self, source: str, hello: dict) -> None:
        """One of the fighters introduced themselves. Wait for both."""
        self._fighters[source] = (hello["name"], hello["gender"])
        supplies = (
            hello.get("bullets", K_DEFAULT_SUPPLY),
            hello.get("restores", K_DEFAULT_SUPPLY),
        )
        if source == "host":
            self._ringside_supplies = supplies
        if len(self._fighters) == 2 and self.engine is None:
            self._start_watching()

    def _start_watching(self) -> None:
        """Both fighters are known; put the fight on screen."""
        from .ui.game_panel import GamePanel

        host_name, host_gender = self._fighters["host"]
        join_name, join_gender = self._fighters["joiner"]
        bullets, restores = self._ringside_supplies

        self.engine = Engine(
            Combatant(name=host_name, gender=host_gender, bullets=bullets, restores=restores),
            Combatant(name=join_name, gender=join_gender),
            get_difficulty(self.settings.difficulty),
            online=True,
            spectating=True,
        )
        panel = GamePanel(self.frame, self, self.engine, net=self.net, spectating=True)
        self.frame.swap_content(panel)
        self.frame.set_status(f"Ringside: {host_name} against {join_name}.")
        panel.begin(first=Side.PLAYER)

    def _ringside_state(self, state: dict) -> None:
        """The scoreboard, posted by the host because we arrived late.

        Everything before this happened out of earshot, so rather than
        guessing, the fight is simply set to what the host says it is.
        """
        if self.engine is None:
            self._fighters = {
                "host": (state["host_name"], state["host_gender"]),
                "joiner": (state["join_name"], state["join_gender"]),
            }
            self._ringside_supplies = (state["host_bullets"], state["host_restores"])
            self._start_watching()
        if self.engine is None:
            return

        for who, prefix in ((self.engine.player, "host"), (self.engine.opponent, "join")):
            who.name = state[f"{prefix}_name"]
            who.gender = state[f"{prefix}_gender"]
            who.health = state[f"{prefix}_health"]
            who.points = state[f"{prefix}_points"]
            who.bullets = state[f"{prefix}_bullets"]
            who.restores = state[f"{prefix}_restores"]
            who.bombs = state[f"{prefix}_bombs"]
            who.gun_loaded = bool(state[f"{prefix}_loaded"])
        self.engine.turn = Side.PLAYER if state["turn"] == "host" else Side.OPPONENT

        self.presenter.render([StatsChanged(), TurnChanged(self.engine.turn)])
        first, second = self.engine.player, self.engine.opponent
        to_move = first if self.engine.turn is Side.PLAYER else second
        self.presenter.report(
            f"{first.name} {max(0, first.health)} health, {first.points} points. "
            f"{second.name} {max(0, second.health)} health, {second.points} points. "
            f"{to_move.name} to move."
        )

    def _apply_remote_move(self, message: dict) -> None:
        """Render the opponent's action, which they have already resolved."""
        self._apply_move(Side.OPPONENT, message)

    def _apply_move(self, side: Side, message: dict) -> None:
        """Apply a move somebody else resolved, to whichever side made it.

        A fighter only ever calls this for their opponent. A seat calls it
        for both, which is the only reason it takes a side at all.
        """
        from .game.constants import Outcome, Weapon
        from .game.engine import Strike
        from .game.events import EventLog, StatsChanged

        kind = message["type"]
        log_ = EventLog()
        mover = self.engine.player if side is Side.PLAYER else self.engine.opponent
        # Same reasoning as _weapon_sound: the side picks the recording, and
        # who is listening does not come into it.
        theirs = side is Side.OPPONENT

        if kind == "strike":
            weapon = Weapon(message["weapon"])
            outcome = Outcome(message["outcome"])
            log_.sound(_weapon_sound(side, weapon))
            self._spend_for(weapon, mover)
            strike = Strike(side, weapon, outcome, message["damage"])
            self.engine._resolve(strike, log_)
            self.presenter.render(self.engine._end_turn(log_))
        elif kind == "heal":
            mover.spend_restore()
            mover.heal(message["amount"])
            log_.add(StatsChanged())
            group = "computerrestore" if theirs else "userrestore"
            if theirs:
                log_.group(group)
            else:
                log_.sound(group)
            log_.say(
                f"{mover.name} restores {message['amount']} health.", after=group
            )
            self.presenter.render(self.engine._end_turn(log_))
        elif kind == "load":
            # Loading is a free action on both sides. Ending the turn here
            # would advance this engine while the sender's stayed put, and
            # the two would be arguing about whose go it is from then on.
            mover.gun_loaded = True
            log_.sound("computerload" if theirs else "userload")
            self.presenter.render(log_.drain())

    def _spend_for(self, weapon, other=None) -> None:
        """Run down the opponent's stock the way their own engine just did.

        Each player owns their own engine, and the opponent inside it is a
        mirror kept up to date by their messages. Rendering an attack without
        also spending what it cost left that mirror reporting a full magazine
        for a player who had just fired their last round -- so pressing 8 for
        the opponent's status answered with something that was simply not
        true. It only started mattering once the opponent stopped having
        infinite bullets to report.
        """
        from .game.constants import Weapon

        other = self.engine.opponent if other is None else other
        if weapon is Weapon.GUN:
            other.spend_bullet()
            other.gun_loaded = False
        elif weapon is Weapon.BOMB:
            other.bombs = max(0, other.bombs - 1)

    def _on_net_disconnected(self, reason: str) -> None:
        self._close_waiting()
        self.net = None
        ours, self._expect_disconnect = self._expect_disconnect, False
        if ours:
            # We hung up. The reason travelled to the other player; it is
            # not news for this one.
            return
        if self._match_finished():
            # The end of the conversation, not news. Whichever player closes
            # their result dialog first hangs up, and the other one is very
            # likely still reading theirs: reporting this would talk over the
            # ending, stopping the music would cut the win piece off, and
            # leaving the match would destroy the panel the dialog is
            # parented to while it is still on screen.
            log.debug("Peer hung up after the match finished: %s", reason)
            return
        self.audio.stop_music()
        self.presenter.report(reason)
        if self.engine is not None and self.engine.online:
            self.leave_match()

    # ------------------------------------------------------------------
    # Input routing
    # ------------------------------------------------------------------
    def set_modal_input_target(self, target) -> None:
        """Point the gamepad at a modal dialog while one is up."""
        self._modal_input_target = target
        self.refresh_input_mode()

    @contextlib.contextmanager
    def modal_input(self, target=None):
        """Hand the pad to a modal dialog for as long as it is up.

        Without this, a button pressed while a dialog was open still went to
        the match behind it — so answering the comment dialog with a pad
        fired the gun. Restores whatever held the input before rather than
        clearing it, so a dialog opened from a dialog gives it back to the
        right one.
        """
        previous = self._modal_input_target
        self.set_modal_input_target(_ANY_MODAL if target is None else target)
        try:
            yield
        finally:
            self.set_modal_input_target(previous)

    def refresh_input_mode(self) -> None:
        """Tell the pad whether it is fighting or navigating.

        Called from :meth:`MainFrame.swap_content` and whenever a modal
        claims the input, so the mode follows the screen rather than being
        set by hand at each transition and forgotten at one of them.

        A surface says which it is by setting ``gamepad_navigation``.
        Anything that does not say is treated as a menu, which is the safe
        way round: a stray navigation keystroke moves a highlight, while a
        stray combat action costs a turn.
        """
        target = self._modal_input_target
        if target is None and self.frame is not None:
            target = self.frame.content
        self.gamepad.set_navigation(bool(getattr(target, "gamepad_navigation", True)))

    def _gamepad_action(self, action: Action, navigation: bool) -> None:
        """Called on the gamepad thread; hop to the UI thread before acting."""
        wx.CallAfter(self._dispatch_action, action, navigation)

    def _dispatch_action(self, action: Action, navigation: bool = False) -> None:
        if navigation:
            # Produced while a menu or dialog was up, so it is a keystroke
            # for whatever holds focus rather than something the match
            # should ever see.
            self.navigator.dispatch(action)
            return

        target = self._modal_input_target
        if target is not None:
            handler = getattr(target, "handle_action", None)
            if callable(handler):
                try:
                    handler(action)
                except Exception:
                    log.exception("Modal gamepad handler failed.")
            return

        content = self.frame.content if self.frame else None
        handler = getattr(content, "handle_action", None)
        if callable(handler):
            try:
                handler(action)
            except Exception:
                log.exception("Gamepad handler failed.")

    # ------------------------------------------------------------------
    # Results and shutdown
    # ------------------------------------------------------------------
    def record_strike(self, event) -> None:
        """Tally one resolved attack.

        Recording pauses entirely while statistics are switched off, and
        whatever was banked before the pause is left alone — the contract the
        original documented for the backspace key.
        """
        if not self.settings.stats_enabled:
            return

        stats = self.stats
        mine = event.attacker is Side.PLAYER
        hit = event.outcome == "hit"

        if event.weapon == "gun" and mine:
            stats.shots_fired += 1
            stats.shots_hit += hit
        elif event.weapon == "whip" and mine:
            stats.lashes += 1
            stats.lashes_hit += hit
        elif event.weapon == "bomb" and mine:
            stats.bombs_used += 1
        elif event.weapon == "power_weapon":
            stats.power_weapons_fired += 1
            if event.outcome == "backfire":
                stats.power_weapon_backfires += 1

        if event.damage:
            if event.victim is Side.OPPONENT:
                stats.damage_dealt += event.damage
            else:
                stats.damage_taken += event.damage

    def record_result(self, engine: Engine, winner: Side) -> None:
        if not self.settings.stats_enabled:
            return
        self.stats.games_played += 1
        if winner is Side.PLAYER:
            self.stats.games_won += 1
        else:
            self.stats.games_lost += 1
        points = engine.player.points
        self.stats.total_points += points
        self.stats.best_points = max(self.stats.best_points, points)
        self.stats.save()

    def begin_exit(self) -> float:
        """Start the exit music and report how long it runs, in seconds.

        Split out from :meth:`shutdown` because the two cannot happen at
        once: shutdown frees the audio device, which cut the exit music off
        the instant it started. The frame plays this, waits, and only then
        tears everything down.
        """
        try:
            self.audio.stop_all_ambience()
            self.audio.stop_music(fade=0.3)
        except Exception:
            pass
        try:
            self.speech.stop()
        except Exception:
            pass
        try:
            handle = self.audio.play("exit", bus="music")
            if handle is NULL_HANDLE:
                return 0.0
            return self.audio.length_of("exit")
        except Exception:
            log.debug("Could not play the exit music.", exc_info=True)
            return 0.0

    def shutdown(self) -> None:
        self._intro_pending = False
        if self._intro_timer is not None:
            self._intro_timer.Stop()
            self._intro_timer = None
        try:
            if self.net is not None:
                self.net.close("Closing the game.")
        except Exception:
            pass
        try:
            self.gamepad.stop()
        except Exception:
            pass
        try:
            self.settings.save()
            self.stats.save()
        except Exception:
            log.exception("Could not save on exit.")
        try:
            self.speech.shutdown()
        except Exception:
            pass
        try:
            self.audio.shutdown()
        except Exception:
            pass


class FusionFireApp(wx.App):
    def OnInit(self) -> bool:  # noqa: N802 - wx naming
        self.SetAppName(__title__)

        # Before anything creates a window. wxWidgets can only switch the
        # native light/dark appearance while none exists -- afterwards the
        # call is accepted and does nothing, leaving a white window on a
        # dark desktop. So the settings are loaded here, early, purely to
        # answer this one question, and handed to the context rather than
        # being read twice.
        from .ui.theme import apply_to_app

        settings = Settings.load()
        apply_to_app(self, settings.theme)

        self.ctx = AppContext(settings)
        self.ctx.start()
        self.SetTopWindow(self.ctx.frame)
        return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    configure_logging(verbose="--verbose" in argv or "-v" in argv)
    log.info("Starting %s %s", __title__, __version__)
    app = FusionFireApp(redirect=False)
    app.MainLoop()
    return 0
