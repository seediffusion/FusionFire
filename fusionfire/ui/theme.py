"""Light and dark.

An audio game does not need a colour scheme to be playable, which is exactly
why it should have a good one: the people this matters to are the partially
sighted players who use the screen *and* the audio, and a white window at
two in the morning is the thing that drives them away.

Windows 10 and 11 have a setting for it, so the game follows that setting
and follows it *live* — wxWidgets 3.3 does the real work through
``SetAppearance``, which repaints the native controls rather than merely
recolouring them, and reports the system state through
``wx.SystemSettings.GetAppearance()``. The application has to opt in before
any of it happens; without the opt-in wx reports the system as light even
when it is dark.

Windows 8.1 and 7 have no such setting. There is nothing to follow, so the
game offers the choice itself and paints the windows by hand, because
``SetAppearance`` has no dark native controls to switch to there.
"""

from __future__ import annotations

import logging

import wx

from .. import platform_info

log = logging.getLogger(__name__)

#: The three things a player can ask for.
MODES = ("system", "light", "dark")
DEFAULT_MODE = "system"

MODE_LABELS = [
    ("system", "Follow my Windows setting (recommended)"),
    ("light", "Always light"),
    ("dark", "Always dark"),
]


#: Hand-painted palette, used only where wx cannot do it natively.
#:
#: Values follow the Windows dark palette rather than pure black: #202020
#: backgrounds with #2b2b2b for input fields keeps the contrast between a
#: field and its surroundings visible, which pure black does not.
DARK = {
    "window": wx.Colour(32, 32, 32),
    "field": wx.Colour(43, 43, 43),
    "text": wx.Colour(240, 240, 240),
}

#: The matching light palette, stated rather than taken from
#: ``wx.SystemSettings``. Once wx is in dark appearance the system colours
#: *are* the dark ones, so asking for SYS_COLOUR_WINDOW while repainting
#: back to light returns dark and nothing changes.
LIGHT = {
    "window": wx.Colour(240, 240, 240),
    "field": wx.Colour(255, 255, 255),
    "text": wx.Colour(0, 0, 0),
}


def available_modes() -> list[tuple[str, str]]:
    """The modes worth offering on this system.

    "Follow my Windows setting" is meaningless where Windows has no such
    setting, so on 8.1 and 7 it is not offered at all rather than being
    offered and doing nothing.
    """
    if platform_info.follows_system_theme():
        return list(MODE_LABELS)
    return [(key, label) for key, label in MODE_LABELS if key != "system"]


def default_mode() -> str:
    return DEFAULT_MODE if platform_info.follows_system_theme() else "light"


def normalise(mode: str) -> str:
    if mode not in MODES:
        return default_mode()
    if mode == "system" and not platform_info.follows_system_theme():
        return "light"
    return mode


def os_is_dark() -> bool:
    """What Windows itself is set to, read from the registry.

    The ground truth, and deliberately not wx's opinion: wx reports the
    system as light until the application has opted in with
    :func:`apply_to_app`, so asking wx before that would answer the wrong
    question and asking it afterwards could not tell success from failure.
    """
    if not platform_info.is_windows():
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            # The value is "apps use LIGHT theme", so 0 means dark.
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        # The key is absent on Windows 8.1 and 7, which is the answer.
        return False
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Could not read the Windows theme: %s", exc)
        return False


def system_is_dark() -> bool:
    """What wx currently believes the system appearance to be."""
    try:
        return bool(wx.SystemSettings.GetAppearance().IsDark())
    except Exception:
        return False


def wants_dark(mode: str) -> bool:
    mode = normalise(mode)
    if mode == "dark":
        return True
    if mode == "light":
        return False
    return os_is_dark()


def apply_to_app(app: wx.App, mode: str) -> bool:
    """Tell wx which appearance to use. True if it took it natively.

    **Call this before creating any window.** wxWidgets can only switch the
    native appearance while none exists: measured on wxWidgets 3.3.3, doing
    it first gives a dark #191919 window, and doing it after a frame has
    been created leaves a light #F0F0F0 one. That is why the application
    sets it in ``OnInit`` rather than once the frame is up.

    Where it works this is the whole job -- wx repaints the real controls,
    the title bar and the scroll bars, which setting background colours
    cannot do. Where it does not, :func:`apply_to_window` paints instead.
    """
    mode = normalise(mode)
    try:
        appearance = {
            "system": wx.App.Appearance.System,
            "light": wx.App.Appearance.Light,
            "dark": wx.App.Appearance.Dark,
        }[mode]
    except Exception:  # pragma: no cover - very old wx
        return False

    try:
        app.SetAppearance(appearance)
    except Exception as exc:
        log.debug("SetAppearance(%s) declined: %s", mode, exc)
        return False

    # Judge it by what happened, not by the return code. The result enum
    # distinguishes "changed" from "could not change" with values that are
    # both truthy, so believing it reported a failure as a success.
    return system_is_dark() == wants_dark(mode)


def apply_to_window(window: wx.Window, dark: bool) -> None:
    """Paint a window and everything inside it. The manual path.

    Only needed where wx has no native dark mode to switch to. Recursing
    from the top-level window catches the panels and dialogs that were built
    before the theme changed, which is every one of them when the player
    flips the setting mid-game.
    """
    if window is None:
        return

    palette = DARK if dark else LIGHT

    def paint(win: wx.Window) -> None:
        try:
            # Text fields get their own shade so the box is still visible
            # against the window behind it.
            if isinstance(win, (wx.TextCtrl, wx.ListBox, wx.Choice)):
                win.SetBackgroundColour(palette["field"])
            else:
                win.SetBackgroundColour(palette["window"])
            win.SetForegroundColour(palette["text"])
        except Exception:
            pass
        for child in win.GetChildren():
            paint(child)

    paint(window)
    try:
        window.Refresh()
    except Exception:
        pass


class ThemeWatcher:
    """Keeps the game's colours in step with the system's.

    Windows announces a theme change as a system colour change, which wx
    forwards as ``EVT_SYS_COLOUR_CHANGED``. Binding it is what makes the
    setting apply the moment it is flipped, rather than at the next launch.
    """

    def __init__(self, app: wx.App, settings) -> None:
        self.app = app
        self.settings = settings
        self._frame: wx.Frame | None = None

    def attach(self, frame: wx.Frame) -> None:
        self._frame = frame
        frame.Bind(wx.EVT_SYS_COLOUR_CHANGED, self._on_system_change)
        self.apply()

    def _on_system_change(self, event: wx.Event) -> None:
        # Only "system" cares; an explicit choice stays put.
        if normalise(self.settings.theme) == "system":
            log.info("Windows theme changed; following it.")
            self.apply()
        event.Skip()

    def apply(self) -> bool:
        """Put the current choice into effect. False if only partly.

        Returns whether wx took it natively. It cannot once windows exist,
        so a change made mid-session is painted on instead: the panels and
        dialogs follow, but the title bar and scroll bars are drawn by
        Windows and keep the appearance they were created with until the
        next launch. The settings dialog says so rather than leaving the
        player wondering why half the window changed.
        """
        mode = normalise(self.settings.theme)
        dark = wants_dark(mode)
        native = apply_to_app(self.app, mode)
        if not native and self._frame is not None:
            apply_to_window(self._frame, dark)
            return False
        if self._frame is not None:
            # wx repainted the controls, but panels built earlier can hold a
            # background colour set before the switch.
            try:
                self._frame.Refresh()
            except Exception:
                pass
        return True

    @property
    def is_dark(self) -> bool:
        return wants_dark(self.settings.theme)
