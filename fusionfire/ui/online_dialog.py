"""Setting up an online match.

Two ways to connect, chosen at the top of the dialog:

* **Relay server (recommended).** Both players dial the same relay server,
  which pairs them by their room key and forwards their traffic byte for
  byte. Nobody forwards a port and nobody needs a public address. The first
  player to join becomes the host and moves first. The relay can be typed by
  hand or picked from a list publicized through a relay spy service.
* **Direct peer to peer.** One player listens on a port and reads their
  address out to the other. Fine on a LAN, and over the internet whenever a
  port can be forwarded.

Two ways to secure it, chosen below that:

* **Quick play (default).** No passphrase. A public *room code* just
  separates one game from another; whoever dials in first is your opponent,
  and the match runs unencrypted. For two people who just want to play.
* **Encrypted.** Both players type a shared passphrase. It stretches into the
  TLS 1.3 pre-shared key, encrypting the match and authenticating both ends,
  so someone who does not have it cannot connect at all. The relay adds no
  weaker link — it only ever sees the ciphertext.
"""

from __future__ import annotations

import wx

from ..net.relay import RELAY_DEFAULT_PORT
from ..net.session import (
    MIN_PASSPHRASE_LENGTH,
    casual_code,
    generate_passphrase,
    local_addresses,
)
from ..net.spy import SpyError, fetch_servers
from .dialog_base import finish_dialog
from .widgets import field_label, message, stack

CONNECTION_CHOICES = [
    "Relay server (recommended)",
    "Direct peer to peer",
]

SECURITY_CHOICES = [
    "Quick play - no passphrase",
    "Encrypted - requires a passphrase",
]


class OnlineDialog(wx.Dialog):
    """Choose how to connect, and collect the connection details."""

    def __init__(self, parent: wx.Window, settings) -> None:
        super().__init__(parent, title="Play online", style=wx.DEFAULT_DIALOG_STYLE)
        self.settings = settings
        self.connection = "relay"
        self.hosting = True
        self.host = ""
        self.port = RELAY_DEFAULT_PORT
        self.passphrase = ""
        self.secure = False

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        self.connection_choice = wx.RadioBox(
            panel,
            label="Connection type",
            choices=list(CONNECTION_CHOICES),
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
        )
        outer.Add(self.connection_choice, 0, wx.ALL | wx.EXPAND, 10)

        self.security_choice = wx.RadioBox(
            panel,
            label="Connection security",
            choices=list(SECURITY_CHOICES),
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
        )
        self.security_choice.SetSelection(0)  # quick play is the default
        outer.Add(self.security_choice, 0, wx.ALL | wx.EXPAND, 10)

        # --- Direct peer to peer ---------------------------------------
        self.p2p_panel = wx.Panel(panel)
        p2p = wx.BoxSizer(wx.VERTICAL)

        self.mode = wx.RadioBox(
            self.p2p_panel,
            label="Your role",
            choices=["&Host the game and wait for an opponent", "&Join someone else's game"],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
        )
        p2p.Add(self.mode, 0, wx.ALL | wx.EXPAND, 4)

        self.address_label = field_label(self.p2p_panel, "Opponent's address (name or IP):")
        self.address_field = wx.TextCtrl(self.p2p_panel, value=settings.last_host, size=(320, -1))
        self.address_field.SetMaxLength(255)
        p2p.Add(stack(self.address_label, self.address_field), 0, wx.ALL | wx.EXPAND, 4)

        port_label = field_label(self.p2p_panel, "Port:")
        self.port_field = wx.SpinCtrl(self.p2p_panel, min=1, max=65535, initial=settings.last_port)
        p2p.Add(stack(port_label, self.port_field), 0, wx.ALL | wx.EXPAND, 4)

        addresses_label = field_label(self.p2p_panel, "Give your opponent one of these addresses:")
        self.addresses = wx.TextCtrl(
            self.p2p_panel,
            value=self._address_help(),
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(320, 76),
        )
        p2p.Add(stack(addresses_label, self.addresses), 0, wx.ALL | wx.EXPAND, 4)

        self.p2p_panel.SetSizer(p2p)
        outer.Add(self.p2p_panel, 0, wx.ALL | wx.EXPAND, 4)

        # --- Relay server ----------------------------------------------
        self.relay_panel = wx.Panel(panel)
        relay = wx.BoxSizer(wx.VERTICAL)

        relay_address_label = field_label(self.relay_panel, "Relay server (name or IP):")
        self.relay_field = wx.TextCtrl(
            self.relay_panel, value=settings.last_relay_host, size=(320, -1)
        )
        self.relay_field.SetMaxLength(255)
        relay.Add(stack(relay_address_label, self.relay_field), 0, wx.ALL | wx.EXPAND, 4)

        relay_port_label = field_label(self.relay_panel, "Port:")
        self.relay_port_field = wx.SpinCtrl(
            self.relay_panel, min=1, max=65535, initial=settings.last_relay_port
        )
        relay.Add(stack(relay_port_label, self.relay_port_field), 0, wx.ALL | wx.EXPAND, 4)

        self.browse_button = wx.Button(self.relay_panel, label="Get a &list of publicized servers")
        self.browse_button.Bind(wx.EVT_BUTTON, self._browse_servers)
        relay.Add(self.browse_button, 0, wx.ALL, 4)

        relay_hint = wx.StaticText(
            self.relay_panel,
            label=(
                "Both players pick the same server and type the same room code. "
                "The first player to join is the host and moves first."
            ),
        )
        self.relay_hint = relay_hint
        relay_hint.Wrap(430)
        relay.Add(relay_hint, 0, wx.ALL, 4)

        self.relay_panel.SetSizer(relay)
        outer.Add(self.relay_panel, 0, wx.ALL | wx.EXPAND, 4)

        # --- Room code or passphrase -----------------------------------
        self.pass_label = field_label(
            panel, "Room code (both players type the same code):"
        )
        self.pass_field = wx.TextCtrl(panel, value=casual_code(), size=(320, -1))
        self.pass_field.SetMaxLength(128)
        self.pass_stack = stack(self.pass_label, self.pass_field)
        outer.Add(self.pass_stack, 0, wx.ALL | wx.EXPAND, 10)

        self.secret_row = wx.BoxSizer(wx.HORIZONTAL)
        self.copy_button = wx.Button(panel, label="&Copy code")
        self.copy_button.Bind(wx.EVT_BUTTON, self._copy_passphrase)
        self.secret_row.Add(self.copy_button, 0, wx.RIGHT, 8)
        self.new_button = wx.Button(panel, label="&New code")
        self.new_button.Bind(wx.EVT_BUTTON, self._new_passphrase)
        self.secret_row.Add(self.new_button, 0)
        outer.Add(self.secret_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.explain = wx.StaticText(panel, label="")
        self.explain.Wrap(430)
        outer.Add(self.explain, 0, wx.ALL, 10)

        finish_dialog(self, panel, outer, focus=self.security_choice)
        self._last_security = self.security_choice.GetSelection()
        self.connection_choice.Bind(wx.EVT_RADIOBOX, lambda e: self._sync())
        self.security_choice.Bind(wx.EVT_RADIOBOX, lambda e: self._sync())
        self.mode.Bind(wx.EVT_RADIOBOX, lambda e: self._sync())
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._sync()

    # ------------------------------------------------------------------
    @staticmethod
    def _address_help() -> str:
        found = local_addresses()
        lines = [
            "On the same network, give your opponent:",
            *(f"    {address}" for address in found),
            "",
            "Over the internet, they need your public address and a forwarded port.",
        ]
        return "\n".join(lines)

    def _sync(self) -> None:
        relay_mode = self.connection_choice.GetSelection() == 0
        self.p2p_panel.Enable(not relay_mode)
        self.relay_panel.Enable(relay_mode)
        hosting = self.mode.GetSelection() == 0
        self.address_field.Enable(not hosting)
        self.addresses.Enable(hosting)

        secure = self.security_choice.GetSelection() == 1
        if secure != self._last_security:
            self._last_security = secure
            self.pass_field.SetValue(generate_passphrase() if secure else casual_code())

        need_secret = secure or relay_mode
        self.pass_stack.Show(need_secret)
        self.secret_row.Show(need_secret)
        if need_secret:
            self.pass_label.SetLabel(
                "Shared passphrase (both players must type the same one):"
                if secure
                else "Room code (both players type the same code):"
            )
            shareable = relay_mode or hosting
            self.copy_button.Enable(shareable)
            self.new_button.Enable(shareable)
            self.copy_button.SetLabel("&Copy passphrase" if secure else "&Copy code")
            self.new_button.SetLabel("&New passphrase" if secure else "&New code")
            self.explain.SetLabel(
                "The passphrase both encrypts the match and proves who you are. "
                "Nobody can connect without it. Read it to your opponent over a "
                "channel you already trust."
                if secure
                else "No passphrase. Anyone who knows the room code can join, and "
                "the match is not encrypted. Fine for a quick game between "
                "people who already know each other."
            )
        else:
            self.explain.SetLabel(
                "No passphrase. Your opponent just dials your address. Anyone who "
                "can reach the port can join, and the match is not encrypted."
            )
        self.relay_hint.SetLabel(
            "Both players pick the same server and type the same "
            + ("passphrase." if secure else "room code.")
            + " The first player to join is the host and moves first."
        )
        self.explain.Wrap(430)
        self.relay_hint.Wrap(430)
        self.Layout()

    def _copy_passphrase(self, event: wx.CommandEvent) -> None:
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(self.pass_field.GetValue()))
                wx.TheClipboard.Flush()
            finally:
                wx.TheClipboard.Close()
            message(
                self,
                f"{'Passphrase' if self.secure else 'Room code'} copied to the clipboard.",
                "Copied",
            )

    def _new_passphrase(self, event: wx.CommandEvent) -> None:
        self.pass_field.SetValue(generate_passphrase() if self.secure else casual_code())
        self.pass_field.SetFocus()

    # ------------------------------------------------------------------
    def _browse_servers(self, event: wx.CommandEvent) -> None:
        """Fetch the publicized relay servers and offer a picker."""
        url = self.settings.relay_spy_url.strip()
        if not url:
            message(
                self,
                "No relay spy service is configured. Open Settings, go to the "
                "Online page, and set the relay spy service address. You can "
                "still type a relay server's name or address by hand.",
                "Relay spy",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        wx.BeginBusyCursor()
        try:
            servers = fetch_servers(url)
        except SpyError as exc:
            message(
                self,
                f"The relay spy service could not be reached:\n\n{exc}",
                "Relay spy",
                wx.OK | wx.ICON_WARNING,
            )
            return
        finally:
            wx.EndBusyCursor()

        if not servers:
            message(
                self,
                "The relay spy service reported no publicized servers. "
                "Try again later, or type a relay server's address by hand.",
                "Relay spy",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        dialog = ServerListDialog(self, servers)
        try:
            if dialog.ShowModal() == wx.ID_OK and dialog.selected is not None:
                self.relay_field.SetValue(dialog.selected.host)
                self.relay_port_field.SetValue(dialog.selected.port)
                if self._apply():
                    # The server was picked to play through, not just to fill
                    # the field: connect right away.
                    self.EndModal(wx.ID_OK)
        finally:
            dialog.Destroy()

    # ------------------------------------------------------------------
    def _apply(self) -> bool:
        """Collect and validate the fields, and remember them.

        Returns True when the dialog should close and the connection start;
        False (with a message) when something needs fixing first.
        """
        relay_mode = self.connection_choice.GetSelection() == 0
        self.connection = "relay" if relay_mode else "p2p"
        if relay_mode:
            self.host = self.relay_field.GetValue().strip()
            self.port = int(self.relay_port_field.GetValue())
        else:
            self.hosting = self.mode.GetSelection() == 0
            self.host = self.address_field.GetValue().strip()
            self.port = int(self.port_field.GetValue())
        self.secure = self.security_choice.GetSelection() == 1
        self.passphrase = self.pass_field.GetValue().strip()

        if self.secure:
            if len(self.passphrase) < MIN_PASSPHRASE_LENGTH:
                message(
                    self,
                    f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters. "
                    "Use the New passphrase button if you would rather not think of one.",
                    "Passphrase too short",
                    wx.OK | wx.ICON_WARNING,
                )
                self.pass_field.SetFocus()
                return False
        elif relay_mode:
            if not self.passphrase:
                message(
                    self,
                    "Type a room code, or use the New code button to get one. "
                    "Both players must type the same code.",
                    "Room code needed",
                    wx.OK | wx.ICON_WARNING,
                )
                self.pass_field.SetFocus()
                return False

        if not self.host:
            if relay_mode:
                message(
                    self,
                    "Type the address of the relay server you want to play through, "
                    "or get a list of publicized servers.",
                    "Server needed",
                    wx.OK | wx.ICON_WARNING,
                )
                self.relay_field.SetFocus()
            else:
                message(
                    self,
                    "Type the address of the player hosting the game.",
                    "Address needed",
                    wx.OK | wx.ICON_WARNING,
                )
                self.address_field.SetFocus()
            return False

        if relay_mode:
            self.settings.last_relay_host = self.host
            self.settings.last_relay_port = self.port
        else:
            self.settings.last_host = self.host
            self.settings.last_port = self.port
        return True

    def _on_ok(self, event: wx.CommandEvent) -> None:
        if self._apply():
            event.Skip()


class WaitingDialog(wx.Dialog):
    """Shown while a connection is being established. Cancel closes it."""

    def __init__(self, parent: wx.Window, text: str) -> None:
        super().__init__(parent, title="Connecting", style=wx.CAPTION | wx.SYSTEM_MENU)
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.label = wx.StaticText(panel, label=text)
        self.label.Wrap(400)
        sizer.Add(self.label, 0, wx.ALL, 14)

        cancel = wx.Button(panel, wx.ID_CANCEL, "&Cancel")
        sizer.Add(cancel, 0, wx.ALL | wx.ALIGN_CENTRE, 14)

        panel.SetSizer(sizer)
        sizer.Fit(panel)
        self.Fit()
        self.CentreOnParent()
        cancel.SetFocus()

    def set_text(self, text: str) -> None:
        self.label.SetLabel(text)
        self.Layout()


class ServerListDialog(wx.Dialog):
    """Pick a relay server from the publicized list.

    Choosing an entry connects to it right away: a click on an item, Enter on
    the focused item, or the OK button all end the dialog with that server
    selected, and the caller starts the match. Arrow keys just move the
    selection and update the details, so browsing the list never connects by
    accident.
    """

    def __init__(self, parent: wx.Window, servers) -> None:
        super().__init__(
            parent, title="Publicized relay servers", style=wx.DEFAULT_DIALOG_STYLE
        )
        self.servers = list(servers)
        self.selected = None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        list_label = field_label(panel, "Relay servers:")
        self.list = wx.ListBox(
            panel, choices=[self._entry(server) for server in self.servers], style=wx.LB_SINGLE
        )
        outer.Add(stack(list_label, self.list), 1, wx.ALL | wx.EXPAND, 10)

        detail_label = field_label(panel, "Details of the selected server:")
        self.detail = wx.TextCtrl(
            panel,
            value=self._detail(0),
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(320, 76),
        )
        outer.Add(stack(detail_label, self.detail), 0, wx.ALL | wx.EXPAND, 10)

        buttons = finish_dialog(self, panel, outer, focus=self.list)
        connect = buttons.GetAffirmativeButton()
        if connect is not None:
            connect.SetLabel("&Connect")
        self.list.Bind(wx.EVT_LISTBOX, self._on_select)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._activate())
        self.list.Bind(wx.EVT_LEFT_UP, self._on_click)
        self.list.Bind(wx.EVT_CHAR, self._on_char)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    @staticmethod
    def _entry(server) -> str:
        return f"{server.name} ({server.host}:{server.port})"

    def _detail(self, index: int) -> str:
        server = self.servers[index]
        parts = [server.name, f"{server.host}:{server.port}"]
        if server.note:
            parts.append(server.note)
        return "\n".join(parts)

    def _on_select(self, event: wx.CommandEvent) -> None:
        index = self.list.GetSelection()
        if index != wx.NOT_FOUND:
            self.detail.SetValue(self._detail(index))

    def _on_click(self, event: wx.MouseEvent) -> None:
        """A single click on an entry connects to it immediately.

        Handled on the mouse-up of the click rather than on the selection
        event, so arrow-key browsing of the list selects without connecting.
        """
        index = self.list.HitTest(event.GetPosition())
        if 0 <= index < len(self.servers):
            self.selected = self.servers[index]
            self.EndModal(wx.ID_OK)

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._activate()
        else:
            event.Skip()

    def _selected(self):
        index = self.list.GetSelection()
        if 0 <= index < len(self.servers):
            return self.servers[index]
        return None

    def _activate(self) -> None:
        server = self._selected()
        if server is not None:
            self.selected = server
            self.EndModal(wx.ID_OK)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        self._activate()
        event.Skip()
