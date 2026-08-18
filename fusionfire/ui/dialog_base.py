"""Shared dialog scaffolding.

wx parents the buttons from ``CreateStdDialogButtonSizer`` to the *dialog*,
not to whatever panel you happen to be filling. Adding that sizer to a
panel's own sizer therefore mixes two parents in one layout, which wx
asserts on in a debug build and silently mispositions in a release one.

:func:`finish_dialog` puts the pieces where wx expects them: the content
panel in the dialog's sizer, the button row beside it, and the dialog sized
to fit. Every dialog in the game goes through it so the mistake cannot come
back one file at a time.
"""

from __future__ import annotations

import wx


def finish_dialog(
    dialog: wx.Dialog,
    panel: wx.Panel,
    content: wx.Sizer,
    *,
    flags: int = wx.OK | wx.CANCEL,
    focus: wx.Window | None = None,
    resizable: bool = False,
) -> wx.Sizer:
    """Assemble ``dialog`` from its content panel and a standard button row.

    Returns the button sizer so callers can reach into it if they need to
    relabel a button. Focus is placed on ``focus`` when given, because a
    dialog that opens with focus nowhere in particular is unusable without
    sight.
    """
    panel.SetSizer(content)
    content.Fit(panel)

    outer = wx.BoxSizer(wx.VERTICAL)
    outer.Add(panel, 1, wx.EXPAND)

    buttons = dialog.CreateStdDialogButtonSizer(flags)
    outer.Add(buttons, 0, wx.ALL | wx.ALIGN_CENTRE, 10)

    dialog.SetSizer(outer)
    if resizable:
        outer.SetSizeHints(dialog)
        dialog.Layout()
    else:
        outer.Fit(dialog)
    dialog.CentreOnParent()

    if focus is not None:
        focus.SetFocus()
    return buttons
