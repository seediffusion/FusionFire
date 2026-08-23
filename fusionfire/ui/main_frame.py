"""The main window and the menu that lives in it.

One frame for the whole game. The menu panel and the match panel swap places
inside it rather than opening new top-level windows, so focus never lands
somewhere the player did not ask for and the screen reader never has to
work out which window went away.
"""

from __future__ import annotations

import logging

import wx

from .. import __title__, __version__
from ..game import greetings
from ..input.keymap import help_text
from .widgets import confirm, field_label, message, review_box, stack

log = logging.getLogger(__name__)


MENU_ITEMS = [
    ("Play offline", "offline"),
    ("Play online", "online"),
    ("Statistics", "stats"),
    ("Settings", "settings"),
    ("Hotkeys", "help"),
    ("About", "about"),
    ("Exit", "exit"),
]


class MenuPanel(wx.Panel):
    """The front menu. A plain list box, because that is what reads best."""

    def __init__(self, parent: wx.Window, ctx) -> None:
        super().__init__(parent)
        self.ctx = ctx

        outer = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self, label=f"{__title__}: choose what to do")
        outer.Add(heading, 0, wx.ALL, 10)

        menu_label = field_label(self, "Main menu:")
        self.list = wx.ListBox(
            self, choices=[label for label, _ in MENU_ITEMS], style=wx.LB_SINGLE
        )
        self.list.SetSelection(0)
        outer.Add(stack(menu_label, self.list), 1, wx.ALL | wx.EXPAND, 10)

        go = wx.Button(self, label="&Go")
        go.Bind(wx.EVT_BUTTON, lambda e: self._activate())
        outer.Add(go, 0, wx.ALL, 10)

        self.SetSizer(outer)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._activate())
        # CHAR_HOOK, not KEY_DOWN. Enter in a list box is claimed by the
        # frame's default-button handling before a KEY_DOWN binding sees it,
        # which left the Go button as the only way to pick an item.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.list.SetFocus()

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            # Only act when the list has focus; Enter on the Go button is the
            # button's own business.
            if wx.Window.FindFocus() is self.list:
                # The launch jingle gets one free keypress: the first Enter
                # stops the music instead of picking an item, so a player
                # who just wants to look at the menu is not launched into
                # anything.
                if self.ctx.skip_intro_music():
                    return
                self._activate()
                return
        event.Skip()

    def _activate(self) -> None:
        # The Go button and a double-click mean the same intent as Enter, so
        # either way the jingle ends before whatever they chose replaces it.
        self.ctx.skip_intro_music()
        index = self.list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self.ctx.menu_choice(MENU_ITEMS[index][1])


class MainFrame(wx.Frame):
    """The single top-level window."""

    def __init__(self, ctx) -> None:
        super().__init__(None, title=__title__, size=(760, 560))
        self.ctx = ctx
        self._content: wx.Panel | None = None
        #: Set once the exit music is playing, so the second close goes through.
        self._closing = False

        self._build_menu_bar()
        self.container = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.container)
        self.CreateStatusBar()
        self.SetStatusText("Ready.")

        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Centre()

    # ------------------------------------------------------------------
    def _build_menu_bar(self) -> None:
        bar = wx.MenuBar()

        game = wx.Menu()
        game.Append(wx.ID_NEW, "Play &offline\tCtrl+N")
        game.Append(wx.ID_OPEN, "Play on&line\tCtrl+L")
        game.AppendSeparator()
        self.leave_item = game.Append(wx.ID_STOP, "Leave &match\tCtrl+W")
        game.AppendSeparator()
        game.Append(wx.ID_EXIT, "E&xit\tAlt+F4")
        bar.Append(game, "&Game")

        tools = wx.Menu()
        tools.Append(wx.ID_PROPERTIES, "&Settings\tCtrl+,")
        stats_item = tools.Append(wx.ID_ANY, "S&tatistics\tCtrl+T")
        bar.Append(tools, "&Tools")

        help_menu = wx.Menu()
        hotkeys = help_menu.Append(wx.ID_HELP, "&Hotkeys\tF1")
        updates = help_menu.Append(wx.ID_ANY, "Check for &updates")
        help_menu.Append(wx.ID_ABOUT, "&About")
        bar.Append(help_menu, "&Help")

        self.SetMenuBar(bar)

        self.Bind(wx.EVT_MENU, lambda e: self.ctx.menu_choice("offline"), id=wx.ID_NEW)
        self.Bind(wx.EVT_MENU, lambda e: self.ctx.menu_choice("online"), id=wx.ID_OPEN)
        self.Bind(wx.EVT_MENU, lambda e: self.ctx.leave_match(), id=wx.ID_STOP)
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, lambda e: self.ctx.menu_choice("settings"), id=wx.ID_PROPERTIES)
        self.Bind(wx.EVT_MENU, lambda e: self.ctx.menu_choice("stats"), id=stats_item.GetId())
        self.Bind(wx.EVT_MENU, lambda e: self.show_help(), id=hotkeys.GetId())
        self.Bind(
            wx.EVT_MENU, lambda e: self.ctx.check_for_updates(), id=updates.GetId()
        )
        self.Bind(wx.EVT_MENU, lambda e: self.show_about(), id=wx.ID_ABOUT)

    # ------------------------------------------------------------------
    def swap_content(self, panel: wx.Panel) -> None:
        """Replace whatever is on screen, destroying the old panel cleanly."""
        old, self._content = self._content, panel
        self.container.Clear(delete_windows=False)
        if old is not None:
            teardown = getattr(old, "teardown", None)
            if callable(teardown):
                try:
                    teardown()
                except Exception:
                    log.exception("Panel teardown failed.")
            old.Destroy()
        self.container.Add(panel, 1, wx.EXPAND)
        self.Layout()
        panel.SetFocus()
        # The pad's bindings depend on what is on screen; this is the one
        # place every screen change goes through.
        self.ctx.refresh_input_mode()

    @property
    def content(self) -> wx.Panel | None:
        return self._content

    def set_status(self, text: str) -> None:
        try:
            self.SetStatusText(text)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # The launch jingle holds the front menu back until it is skipped or
    # plays out; see AppContext._begin_launch_intro.
    def show_intro(self) -> None:
        """Reveal an empty frame behind the launch jingle.

        The context started the music. Until the jingle ends, the only
        thing on screen is this hint and a frame-level Enter handler.
        """
        self.SetStatusText("Intro music. Press Enter to skip.")
        self.Bind(wx.EVT_CHAR_HOOK, self._on_intro_key)

    def _on_intro_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            # skip_intro_music ends the jingle and puts the menu up itself.
            if self.ctx.skip_intro_music():
                return
        event.Skip()

    def end_intro(self) -> None:
        """The jingle is over (skipped or finished); stop listening."""
        try:
            self.Unbind(wx.EVT_CHAR_HOOK, handler=self._on_intro_key)
        except RuntimeError:  # pragma: no cover - already gone
            pass

    # ------------------------------------------------------------------
    # Both of these are on the menu bar, so they can open over a match. The
    # pad has to come off the fight while they are up, or a press meant for
    # the box lands as a taunt behind it.
    def show_help(self) -> None:
        with self.ctx.modal_input():
            message(self, help_text(), "Hotkeys")

    def show_about(self) -> None:
        greeting = greetings.for_today(self.ctx.settings.birthday)
        extra = f"\n\n{greeting.text}" if greeting else ""
        with self.ctx.modal_input():
            message(
                self,
                f"{__title__} {__version__}\n"
                "by Seediffusion\n\n"
                "A modernised port of Acefire 2 by X-Sight Interactive\n"
                "(Damien Sadler, 2008), rebuilt in Python with wxPython,\n"
                "Prism and sound_lib.\n\n"
                "All sounds and music are the original recordings, recovered\n"
                "bit-exact from the 2008 release. Music by Quinten Pendle.\n"
                "Alphabet and number speech by Philip Bennefall." + extra,
                f"About {__title__}",
            )

    def _on_close(self, event: wx.CloseEvent) -> None:
        # Second pass: the exit music has finished, so actually go.
        if self._closing:
            self.ctx.shutdown()
            event.Skip()
            return

        if event.CanVeto() and self.ctx.settings.confirm_exit:
            with self.ctx.modal_input():
                leaving = confirm(self, "Leave Fusion Fire?", "Exit")
            if not leaving:
                event.Veto()
                return

        # The exit piece has to finish before the audio device is freed, and
        # shutdown() frees it — so play first, tear down after. The window
        # hides immediately so the game still feels like it closed on the
        # keypress rather than hanging around for eight seconds.
        duration = self.ctx.begin_exit()
        if duration > 0 and event.CanVeto():
            self._closing = True
            event.Veto()
            self.Hide()
            wx.CallLater(int(duration * 1000) + 150, self._finish_close)
            return

        self.ctx.shutdown()
        event.Skip()

    def _finish_close(self) -> None:
        try:
            self.Close(force=True)
        except RuntimeError:  # pragma: no cover - already destroyed
            pass


class StatsPanel(wx.Panel):
    """A read-only report of lifetime statistics."""

    def __init__(self, parent: wx.Window, ctx) -> None:
        super().__init__(parent)
        self.ctx = ctx
        stats = ctx.stats

        outer = wx.BoxSizer(wx.VERTICAL)
        record_label = field_label(self, "Your record:")
        report = review_box(self, "Statistics")
        report.SetValue(self._render(stats))
        outer.Add(stack(record_label, report), 1, wx.ALL | wx.EXPAND, 10)

        row = wx.BoxSizer(wx.HORIZONTAL)
        back = wx.Button(self, label="&Back to menu")
        back.Bind(wx.EVT_BUTTON, lambda e: ctx.show_menu())
        row.Add(back, 0, wx.RIGHT, 8)

        reset = wx.Button(self, label="&Reset statistics")
        reset.Bind(wx.EVT_BUTTON, self._reset)
        row.Add(reset, 0)
        outer.Add(row, 0, wx.ALL, 10)

        self.SetSizer(outer)
        self.report = report
        report.SetFocus()

    @staticmethod
    def _render(stats) -> str:
        if stats.games_played == 0:
            return "No matches played yet. Press Control N to start one."
        lines = [
            f"Matches played      {stats.games_played}",
            f"Won                 {stats.games_won}",
            f"Lost                {stats.games_lost}",
            "",
            f"Shots fired         {stats.shots_fired}",
            f"Shots on target     {stats.shots_hit} ({stats.shot_accuracy:.0f}%)",
            f"Lashes              {stats.lashes}",
            f"Lashes on target    {stats.lashes_hit} ({stats.lash_accuracy:.0f}%)",
            f"Bombs used          {stats.bombs_used}",
            f"Power weapons fired {stats.power_weapons_fired}",
            f"  of which backfired  {stats.power_weapon_backfires}",
            "",
            f"Damage dealt        {stats.damage_dealt}",
            f"Damage taken        {stats.damage_taken}",
            f"Best single match   {stats.best_points} points",
            f"Points all time     {stats.total_points}",
        ]
        return "\n".join(lines)

    def _reset(self, event: wx.CommandEvent) -> None:
        if not confirm(self, "Erase all recorded statistics?", "Reset statistics"):
            return
        self.ctx.reset_stats()
        self.report.SetValue(self._render(self.ctx.stats))
        self.ctx.speech.report("Statistics reset.")
