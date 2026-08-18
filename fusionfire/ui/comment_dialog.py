"""Audio comments and chat.

Offline this is the original's list of three recorded jibes plus a laugh.
Online it is that same list with a free-text box underneath, because two
humans want to say more to each other than "Ooh you little beast".

Chat text is capped and stripped at the protocol layer, and rendered here as
plain text into a read-only control — it is never interpreted as markup.
"""

from __future__ import annotations

import wx

from ..assets import COMMENTS
from .dialog_base import finish_dialog
from .widgets import field_label, stack


class CommentDialog(wx.Dialog):
    """Pick a recorded comment, laugh, or type a message."""

    LAUGH_KEY = "__laugh__"

    def __init__(self, parent: wx.Window, *, allow_chat: bool = False) -> None:
        super().__init__(parent, title="Say something", style=wx.DEFAULT_DIALOG_STYLE)
        self.selected_key: str | None = None
        self.chat_text: str = ""

        self._keys = list(COMMENTS) + [self.LAUGH_KEY]
        labels = [label for label, _ in COMMENTS.values()] + ["Laugh at your opponent"]

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        comment_label = field_label(panel, "Comment:")
        self.list = wx.ListBox(panel, choices=labels, style=wx.LB_SINGLE, size=(340, 120))
        self.list.SetSelection(0)
        outer.Add(stack(comment_label, self.list), 0, wx.ALL | wx.EXPAND, 10)

        self.chat = None
        if allow_chat:
            chat_label = field_label(panel, "Or type a message (up to 300 characters):")
            self.chat = wx.TextCtrl(panel, size=(340, -1), style=wx.TE_PROCESS_ENTER)
            self.chat.SetMaxLength(300)
            outer.Add(stack(chat_label, self.chat), 0, wx.ALL | wx.EXPAND, 10)
            self.chat.Bind(wx.EVT_TEXT_ENTER, lambda e: self._finish(wx.ID_OK))

        finish_dialog(self, panel, outer, focus=self.list)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._finish(wx.ID_OK))
        self.Bind(wx.EVT_BUTTON, lambda e: self._finish(wx.ID_OK), id=wx.ID_OK)

    def _finish(self, code: int) -> None:
        if self.chat is not None:
            self.chat_text = self.chat.GetValue().strip()
        index = self.list.GetSelection()
        # A typed message wins over a highlighted canned one — if the player
        # bothered to type, that is what they meant to send.
        if not self.chat_text and index != wx.NOT_FOUND:
            self.selected_key = self._keys[index]
        self.EndModal(code)

    @property
    def is_laugh(self) -> bool:
        return self.selected_key == self.LAUGH_KEY
