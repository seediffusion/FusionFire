"""Driving the interface from a game pad.

The menus, the settings tabs and the dialogs are all ordinary native
controls, chosen precisely because a screen reader already knows how to read
them. That decision has a consequence here: a native list box moves its
selection because Windows told it a key went down, not because wx asked it
to, and a selection changed behind the platform's back is announced to
nobody. Calling ``SetSelection`` from a pad handler would move the highlight
silently, which for a blind player is the same as not moving it at all.

So the pad presses the key. Every navigation action is turned into a real
keystroke through :class:`wx.UIActionSimulator`, and from there the button,
the list box, the notebook, the slider and the message box that the game did
not write all behave exactly as they do under a finger — including telling
the screen reader what happened. One table below replaces a handler in every
dialog, and any dialog added later is navigable the day it is written.

The simulator injects at the system level, so everything here is gated on
the game actually holding the keyboard; see :meth:`Navigator.foreground`.
"""

from __future__ import annotations

import logging

import wx

from ..input.actions import Action

log = logging.getLogger(__name__)


#: Navigation action -> the keystroke it stands for, as (key code, modifiers).
#: These are the keys the interface already answers to, which is the point:
#: the pad is a second way to press them, not a second way to be handled.
NAVIGATION_KEYS: dict[Action, tuple[int, int]] = {
    Action.MOVE_UP: (wx.WXK_UP, wx.MOD_NONE),
    Action.MOVE_DOWN: (wx.WXK_DOWN, wx.MOD_NONE),
    Action.MOVE_LEFT: (wx.WXK_LEFT, wx.MOD_NONE),
    Action.MOVE_RIGHT: (wx.WXK_RIGHT, wx.MOD_NONE),
    Action.CONFIRM: (wx.WXK_RETURN, wx.MOD_NONE),
    Action.CANCEL: (wx.WXK_ESCAPE, wx.MOD_NONE),
    Action.FOCUS_NEXT: (wx.WXK_TAB, wx.MOD_NONE),
    Action.FOCUS_PREVIOUS: (wx.WXK_TAB, wx.MOD_SHIFT),
}


class Navigator:
    """Turns a pad's navigation actions into keystrokes for whatever has focus."""

    def __init__(self) -> None:
        #: Built on first use. A simulator constructed before there is a
        #: wx.App has nothing to inject into.
        self._simulator: wx.UIActionSimulator | None = None

    def dispatch(self, action: Action) -> bool:
        """Press the key this action stands for. False if nothing was sent."""
        stroke = NAVIGATION_KEYS.get(action)
        if stroke is None:
            return False
        if not self.foreground():
            return False
        simulator = self._open()
        if simulator is None:
            return False

        code, modifiers = stroke
        try:
            simulator.KeyDown(code, modifiers)
            simulator.KeyUp(code, modifiers)
        except Exception:  # pragma: no cover - platform refused the injection
            log.debug("Could not simulate %s.", action, exc_info=True)
            return False
        return True

    def _open(self) -> "wx.UIActionSimulator | None":
        if self._simulator is None:
            try:
                self._simulator = wx.UIActionSimulator()
            except Exception:  # pragma: no cover - no simulator on this build
                log.warning(
                    "This build of wxPython cannot simulate input, so the "
                    "game pad cannot drive the menus."
                )
                return None
        return self._simulator

    @staticmethod
    def foreground() -> bool:
        """True when a window of this application holds the keyboard.

        A guard, not a courtesy. The keystrokes are injected at the system
        level and land wherever the keyboard currently points, so a player
        who alt-tabs away mid-match with a thumb still on the stick would
        otherwise be sending Escape and Enter into whatever they switched
        to. Windows reports no active window for a thread that is not in the
        foreground, which is exactly the question being asked.
        """
        try:
            return wx.GetActiveWindow() is not None
        except Exception:  # pragma: no cover - defensive
            return False
