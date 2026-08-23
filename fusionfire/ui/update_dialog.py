"""Telling the player about a new version, and fetching it.

Two dialogs. The first says what was found and asks; the second reports the
download and can stop it. Neither of them does the work — that lives in
:mod:`fusionfire.update`, which knows nothing about wx — so the awkward parts
(what counts as newer, what a zip is allowed to contain, how a folder in
Program Files gets written to) stay testable without a window.

The release notes get a real read-only edit box rather than a static block of
text, because a static text cannot be arrowed through. "There is a new
version, here is what changed" is worth reading at your own pace, and on a
screen reader that means a control the caret can enter.
"""

from __future__ import annotations

import wx

from .dialog_base import finish_dialog
from .widgets import field_label, stack


class UpdatePrompt(wx.Dialog):
    """"A new version is out. Do you want it?"

    Answered with :data:`wx.ID_OK` to update now, anything else to leave it.
    """

    def __init__(self, parent: wx.Window, release, current: str) -> None:
        super().__init__(parent, title="Update Fusion Fire", style=wx.DEFAULT_DIALOG_STYLE)
        self.release = release

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        summary = wx.StaticText(
            panel,
            label=(
                f"Fusion Fire {release.tag} is available. You are running "
                f"{current}."
            ),
        )
        summary.Wrap(430)
        outer.Add(summary, 0, wx.ALL, 10)

        notes_label = field_label(panel, "What changed in this release:")
        self.notes = wx.TextCtrl(
            panel,
            value=release.notes or "This release published no notes.",
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(430, 180),
        )
        outer.Add(stack(notes_label, self.notes), 1, wx.ALL | wx.EXPAND, 10)

        explain = wx.StaticText(
            panel,
            label=(
                "Fusion Fire will close, update and reopen. Windows may ask "
                "for permission. Nothing changes if you cancel."
            ),
        )
        explain.Wrap(430)
        outer.Add(explain, 0, wx.ALL, 10)

        buttons = finish_dialog(self, panel, outer, focus=self.notes)
        update = buttons.GetAffirmativeButton()
        if update is not None:
            update.SetLabel("&Update now")
        later = buttons.GetCancelButton()
        if later is not None:
            later.SetLabel("Not &now")

    def announcement(self) -> str:
        """The same news, for the player who is listening rather than reading."""
        return f"Fusion Fire {self.release.tag} is available."


class UpdateProgressDialog(wx.Dialog):
    """Shows the download getting on with it, and can call it off.

    The percentage is spoken as well as shown, but only as it crosses each
    tenth: a line per chunk would be a solid wall of speech that talks over
    itself and tells the player nothing they can act on.
    """

    def __init__(self, parent: wx.Window, presenter=None) -> None:
        super().__init__(
            parent, title="Updating Fusion Fire", style=wx.CAPTION | wx.SYSTEM_MENU
        )
        self.cancelled = False
        self._presenter = presenter
        self._spoken_tenth = -1

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        status_label = field_label(panel, "Progress:")
        self.status = wx.TextCtrl(
            panel,
            value="Starting...",
            style=wx.TE_READONLY,
            size=(360, -1),
        )
        sizer.Add(stack(status_label, self.status), 0, wx.ALL | wx.EXPAND, 14)

        cancel = wx.Button(panel, wx.ID_CANCEL, "&Cancel")
        cancel.Bind(wx.EVT_BUTTON, self._on_cancel)
        sizer.Add(cancel, 0, wx.ALL | wx.ALIGN_CENTRE, 14)

        panel.SetSizer(sizer)
        sizer.Fit(panel)
        self.Fit()
        self.CentreOnParent()
        cancel.SetFocus()
        self.Bind(wx.EVT_CLOSE, self._on_cancel)

    # ------------------------------------------------------------------
    def _on_cancel(self, event) -> None:
        self.cancelled = True
        self.set_text("Cancelled.")

    @staticmethod
    def _on_the_main_thread(callback, *args) -> None:
        """Run ``callback`` where wx allows a control to be touched.

        Progress arrives on the download thread, and a wx control updated
        from anywhere but the main thread is not a bug that shows up in a
        test — it is a hang or a crash on somebody else's machine, weeks
        later. Everything that reaches a window from here goes through this.
        """
        try:
            if wx.IsMainThread():
                callback(*args)
            elif wx.GetApp() is not None:
                wx.CallAfter(callback, *args)
        except RuntimeError:
            pass  # the dialog, or the application, has already gone

    def set_text(self, text: str) -> None:
        self._on_the_main_thread(self._set_text, text)

    def _set_text(self, text: str) -> None:
        try:
            self.status.SetValue(text)
        except RuntimeError:
            pass  # the dialog is already gone

    def report(self, text: str) -> None:
        """Show a line and say it, for the steps that are not a percentage."""
        self.set_text(text)
        if self._presenter is not None:
            self._on_the_main_thread(self._presenter.report, text)

    def on_progress(self, done: int, total: int) -> bool:
        """Progress callback for :func:`fusionfire.update.download`.

        Called on the download thread. Returns False once Cancel has been
        pressed, which is what stops that thread; the flag is written by the
        button handler on the main thread and only read here, so a plain
        bool is all the synchronisation it needs.
        """
        if total > 0:
            fraction = done / total
            self.set_text(f"Downloading: {fraction * 100:.0f} percent.")
            tenth = int(fraction * 10)
            if tenth > self._spoken_tenth and self._presenter is not None:
                self._spoken_tenth = tenth
                if 0 < tenth < 10:
                    self._on_the_main_thread(self._presenter.report, f"{tenth * 10} percent.")
        else:
            self.set_text(f"Downloading: {done / (1024 * 1024):.0f} megabytes.")
        return not self.cancelled
