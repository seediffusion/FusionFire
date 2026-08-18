"""End-to-end smoke test through the real UI.

Builds the actual :class:`AppContext`, opens the real frame, and drives a
match with the same ``handle_action`` calls the keyboard and gamepad use.
This is the test that catches wiring mistakes the pure-rules tests cannot:
a panel that never subscribes to the presenter, a menu entry pointing at a
method that no longer exists, an audio call made before the device is open.

Settings and statistics are redirected into a temporary directory so a test
run never touches the player's real files.
"""

from __future__ import annotations

import ctypes
import gc
import sys

import pytest

wx = pytest.importorskip("wx")

from fusionfire.assets import GROUPS  # noqa: E402
from fusionfire.game.constants import Phase, Side  # noqa: E402
from fusionfire.input.actions import Action  # noqa: E402


@pytest.fixture
def app_ctx(tmp_path, monkeypatch):
    """A booted application pointed at a throwaway data directory."""
    monkeypatch.setattr("fusionfire.paths.data_dir", lambda: tmp_path)

    from fusionfire.app import AppContext

    app = wx.App(redirect=False)
    ctx = AppContext()
    ctx.settings.player_name = "Ada Lovelace"
    ctx.settings.player_gender = "female"
    ctx.settings.confirm_exit = False
    ctx.settings.gamepad_enabled = False  # no SDL polling thread in tests
    ctx.start()
    # Boot leaves the front menu behind the launch jingle; tests want the
    # menu, so skip past it. The hold-back itself is tested separately.
    ctx.skip_intro_music()
    try:
        yield ctx
    finally:
        ctx.shutdown()
        if ctx.frame:
            ctx.frame.Destroy()
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()


@pytest.fixture
def match(app_ctx):
    """A running offline match, with the panel on screen."""
    from fusionfire.game.difficulty import get

    app_ctx._launch_match(get("intermediate"))
    panel = app_ctx.frame.content
    # begin() schedules _begin_play on a wx timer sized to
    # computerstart.wav; no MainLoop runs here, so drive it directly.
    panel._cancel_intro_timer()
    panel._begin_play()
    return app_ctx, panel


# ----------------------------------------------------------------------
def test_the_application_boots(app_ctx):
    from fusionfire.ui.main_frame import MenuPanel

    assert app_ctx.frame is not None
    assert isinstance(app_ctx.frame.content, MenuPanel)


def test_audio_and_speech_come_up(app_ctx):
    assert app_ctx.audio.available, "no audio device opened"
    assert app_ctx.speech.available, "no speech backend opened"


def _boot_ctx(tmp_path, monkeypatch, **overrides):
    """A freshly booted AppContext with a clean slate.

    Standalone rather than the app_ctx fixture because the fixture boots
    once for the whole session's needs, while these tests must control what
    happens during boot itself. ``greetings.for_today`` is pinned to None so
    a test run on Christmas day still exercises the intro branch.
    """
    monkeypatch.setattr("fusionfire.paths.data_dir", lambda: tmp_path)
    monkeypatch.setattr("fusionfire.app.greetings.for_today", lambda birthday: None)

    from fusionfire.app import AppContext

    app = wx.App(redirect=False)
    ctx = AppContext()
    for key, value in overrides.items():
        setattr(ctx.settings, key, value)
    ctx.start()
    return app, ctx


def _tear_down(app, ctx) -> None:
    ctx.shutdown()
    if ctx.frame:
        ctx.frame.Destroy()
    wx.CallAfter(app.ExitMainLoop)
    app.MainLoop()


def test_launch_plays_the_intro_music_when_enabled(tmp_path, monkeypatch):
    """With the setting on (the default), the launch jingle replaces the
    spoken ready message, and the front menu waits for it."""
    app, ctx = _boot_ctx(tmp_path, monkeypatch)
    try:
        assert ctx.audio.playing_music("intro"), "the launch jingle should be playing"
        assert ctx.frame.content is None, "the menu must wait for the jingle"
        assert ctx.presenter.history == [], "the ready message must not be spoken"
    finally:
        _tear_down(app, ctx)


def test_the_launch_jingle_ignores_the_in_match_music_toggle(tmp_path, monkeypatch):
    """Reported bug: with the score's music toggle off, the jingle was
    skipped and the ready message spoke instead. The jingle is an event
    piece, not background score, so the music toggle must not silence it."""
    app, ctx = _boot_ctx(tmp_path, monkeypatch, music_enabled=False)
    try:
        assert ctx.audio.playing_music("intro"), (
            "the jingle must play even when in-match music is off"
        )
        assert ctx.presenter.history == [], "the ready message must not be spoken"
    finally:
        _tear_down(app, ctx)


def test_launch_speaks_ready_when_the_intro_music_is_off(tmp_path, monkeypatch):
    app, ctx = _boot_ctx(tmp_path, monkeypatch, play_intro_music=False)
    try:
        assert not ctx.audio.playing_music("intro")
        assert ctx.audio.current_music is None
        assert any("ready" in line for line in ctx.presenter.history), (
            "turning the intro off must bring the spoken ready message back"
        )
    finally:
        _tear_down(app, ctx)


def test_every_menu_entry_resolves_to_a_handler(app_ctx, monkeypatch):
    """Each entry must reach a real method. The handlers that open modal
    dialogs are stubbed, since a modal would block the test forever."""
    from fusionfire.ui.main_frame import MENU_ITEMS

    called = []
    for name in ("start_offline", "start_online", "show_settings"):
        monkeypatch.setattr(app_ctx, name, lambda n=name: called.append(n))
    monkeypatch.setattr(app_ctx.frame, "show_help", lambda: called.append("help"))
    monkeypatch.setattr(app_ctx.frame, "show_about", lambda: called.append("about"))
    monkeypatch.setattr(app_ctx.frame, "Close", lambda: called.append("exit"))

    for _label, key in MENU_ITEMS:
        app_ctx.menu_choice(key)

    # Every entry but "stats", which renders a panel in place rather than
    # delegating, must have reached its handler exactly once.
    assert called == ["start_offline", "start_online", "show_settings",
                      "help", "about", "exit"]
    from fusionfire.ui.main_frame import StatsPanel

    assert isinstance(app_ctx.frame.content, StatsPanel)


@pytest.mark.parametrize(
    "builder",
    [
        "setup", "opponent", "comment", "comment_online", "online", "settings",
    ],
)
def test_every_dialog_constructs_and_lays_out(app_ctx, builder):
    """Constructing a dialog exercises its sizers.

    wx asserts in a debug build when a sizer is handed windows belonging to
    another parent, which is exactly what happens if the standard button row
    is added to an inner panel's sizer. Building each dialog here catches
    that without anyone having to open it by hand.
    """
    from fusionfire.ui.comment_dialog import CommentDialog
    from fusionfire.ui.online_dialog import OnlineDialog
    from fusionfire.ui.settings_dialog import SettingsDialog
    from fusionfire.ui.setup_dialog import OpponentDialog, SetupDialog

    frame = app_ctx.frame
    builders = {
        "setup": lambda: SetupDialog(frame, app_ctx.settings),
        "opponent": lambda: OpponentDialog(frame, app_ctx.settings),
        "comment": lambda: CommentDialog(frame, allow_chat=False),
        "comment_online": lambda: CommentDialog(frame, allow_chat=True),
        "online": lambda: OnlineDialog(frame, app_ctx.settings),
        "settings": lambda: SettingsDialog(frame, app_ctx),
    }

    dialog = builders[builder]()
    try:
        assert dialog.GetSizer() is not None
        width, height = dialog.GetSize()
        assert width > 0 and height > 0
        # The OK button must exist and belong to the dialog, not a child panel.
        ok_button = dialog.FindWindowById(wx.ID_OK)
        assert ok_button is not None
        assert ok_button.GetParent() is dialog
    finally:
        dialog.Destroy()


# ----------------------------------------------------------------------
# Label association.
#
# A control's accessible name on Windows is not its wx name -- it is the
# static text immediately before it in native z-order, and z-order follows
# creation order. Build the label after the control and every field in the
# dialog reads with the *previous* field's label, which is exactly what a
# screen reader user reported. Sizer position hides it completely: the
# dialog looks right and reads wrong, so only creation order can be tested.


def _preceding_sibling(control: wx.Window) -> wx.Window | None:
    """The sibling created immediately before ``control``.

    ``GetChildren`` is in creation order, which is the z-order a screen
    reader walks backwards from the control to find its label.
    """
    siblings = list(control.GetParent().GetChildren())
    return siblings[siblings.index(control) - 1] if siblings.index(control) > 0 else None


def _assert_labelled(control: wx.Window, expected: str, where: str) -> None:
    found = _preceding_sibling(control)
    assert isinstance(found, wx.StaticText), (
        f"{where}: nothing but a {type(found).__name__} precedes this control, "
        f"so a screen reader has no label to read for {expected!r}"
    )
    assert found.GetLabel() == expected, (
        f"{where}: the control is preceded by {found.GetLabel()!r}, not "
        f"{expected!r} -- the labels are out of step by a field"
    )


# Every labelled field of the settings dialog, page by page.
SETTINGS_FIELDS = [
    ("device", "Output device:"),
    ("sound_slider", "Sound volume (Home and End in game):"),
    ("music_slider", "Music volume (Page Up and Page Down in game):"),
    ("backend", "Speech output:"),    ("difficulty", "Default difficulty:"),
    ("name_field", "Your name:"),
    ("gender", "Your character's voice:"),
    ("birthday", "Birthday as month and day, for example 03-14 (optional):"),
    ("deadzone", "Stick dead zone, percent:"),
    ("binding_list", "Button assignments:"),
    ("spy_url", "Relay spy service (web address):"),
]


def test_every_settings_field_is_preceded_by_its_own_label(app_ctx):
    """The reported bug: tabbing the settings dialog read each field's label
    against the next control. Assert the real association for all four
    notebook pages."""
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        for attribute, expected in SETTINGS_FIELDS:
            _assert_labelled(
                getattr(dialog, attribute), expected, f"SettingsDialog.{attribute}"
            )
    finally:
        dialog.Destroy()


def _encrypted_online_dialog(frame, settings):
    """An OnlineDialog switched to the encrypted (passphrase) mode."""
    from fusionfire.ui.online_dialog import OnlineDialog

    dialog = OnlineDialog(frame, settings)
    dialog.security_choice.SetSelection(1)
    dialog._sync()
    return dialog


def test_labels_line_up_in_the_other_dialogs(app_ctx):
    """The same helper is used everywhere, so check everywhere."""
    from fusionfire.ui.comment_dialog import CommentDialog
    from fusionfire.ui.online_dialog import OnlineDialog
    from fusionfire.ui.setup_dialog import OpponentDialog, SetupDialog

    frame = app_ctx.frame
    cases = [
        (
            lambda: SetupDialog(frame, app_ctx.settings),
            [("name_field", "Your name:"), ("gender_choice", "Your character's voice:")],
        ),
        (
            lambda: OpponentDialog(frame, app_ctx.settings),
            [("list", "Difficulty:"), ("description", "What this opponent does:")],
        ),
        (
            lambda: CommentDialog(frame, allow_chat=True),
            [
                ("list", "Comment:"),
                ("chat", "Or type a message (up to 300 characters):"),
            ],
        ),
        (
            # The default online mode is casual: a public room code.
            lambda: OnlineDialog(frame, app_ctx.settings),
            [
                ("address_field", "Opponent's address (name or IP):"),
                ("port_field", "Port:"),
                ("addresses", "Give your opponent one of these addresses:"),
                ("relay_field", "Relay server (name or IP):"),
                ("relay_port_field", "Port:"),
                ("pass_field", "Room code (both players type the same code):"),
            ],
        ),
        (
            lambda: _encrypted_online_dialog(frame, app_ctx.settings),
            [
                ("pass_field", "Shared passphrase (both players must type the same one):"),
            ],
        ),
    ]

    for build, fields in cases:
        dialog = build()
        try:
            for attribute, expected in fields:
                _assert_labelled(
                    getattr(dialog, attribute),
                    expected,
                    f"{type(dialog).__name__}.{attribute}",
                )
        finally:
            dialog.Destroy()


def _fake_end_modal(dialog, monkeypatch):
    """Return a recorder that stands in for dialog.EndModal, so tests can
    call the handlers that close a modal without opening a modal loop."""
    ended = []
    monkeypatch.setattr(dialog, "EndModal", lambda code: ended.append(code))
    return ended


def test_picking_a_server_from_the_list_connects_immediately(app_ctx, monkeypatch):
    """Choosing an entry in the publicized-server list must close the online
    dialog with that server, so the match starts without another OK press."""
    from fusionfire.net.spy import PublicizedServer
    from fusionfire.ui.online_dialog import OnlineDialog

    server = PublicizedServer(name="test", host="fusion.seedy.cc", port=6001)

    class FakeListDialog:
        selected = server

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            pass

    dialog = OnlineDialog(app_ctx.frame, app_ctx.settings)
    try:
        dialog.settings.relay_spy_url = "https://spy.example.org/servers"
        monkeypatch.setattr("fusionfire.ui.online_dialog.fetch_servers", lambda url: [server])
        monkeypatch.setattr(
            "fusionfire.ui.online_dialog.ServerListDialog",
            lambda parent, servers: FakeListDialog(),
        )
        ended = _fake_end_modal(dialog, monkeypatch)

        dialog._browse_servers(None)

        assert dialog.relay_field.GetValue() == "fusion.seedy.cc"
        assert dialog.relay_port_field.GetValue() == 6001
        assert dialog.host == "fusion.seedy.cc"
        assert dialog.connection == "relay"
        assert ended == [wx.ID_OK], "picking a server must close the dialog and connect"
    finally:
        dialog.Destroy()


def test_activating_a_server_in_the_list_connects(app_ctx, monkeypatch):
    from fusionfire.net.spy import PublicizedServer
    from fusionfire.ui.online_dialog import ServerListDialog

    server = PublicizedServer(name="test", host="fusion.seedy.cc", port=6001)
    dialog = ServerListDialog(app_ctx.frame, [server])
    try:
        ended = _fake_end_modal(dialog, monkeypatch)
        dialog.list.SetSelection(0)
        dialog._activate()
        assert dialog.selected == server
        assert ended == [wx.ID_OK]
    finally:
        dialog.Destroy()


def test_clicking_a_server_in_the_list_connects(app_ctx, monkeypatch):
    from fusionfire.net.spy import PublicizedServer
    from fusionfire.ui.online_dialog import ServerListDialog

    server = PublicizedServer(name="test", host="fusion.seedy.cc", port=6001)
    dialog = ServerListDialog(app_ctx.frame, [server])
    try:
        ended = _fake_end_modal(dialog, monkeypatch)
        monkeypatch.setattr(dialog.list, "HitTest", lambda point: 0)
        dialog._on_click(wx.MouseEvent(wx.wxEVT_LEFT_UP))
        assert dialog.selected == server
        assert ended == [wx.ID_OK]
    finally:
        dialog.Destroy()


def test_enter_in_the_list_connects_but_other_keys_only_navigate(app_ctx, monkeypatch):
    from fusionfire.net.spy import PublicizedServer
    from fusionfire.ui.online_dialog import ServerListDialog

    server = PublicizedServer(name="test", host="fusion.seedy.cc", port=6001)
    dialog = ServerListDialog(app_ctx.frame, [server])
    try:
        ended = _fake_end_modal(dialog, monkeypatch)
        dialog.list.SetSelection(0)

        enter = wx.KeyEvent(wx.wxEVT_CHAR)
        enter.SetKeyCode(wx.WXK_RETURN)
        dialog._on_char(enter)
        assert dialog.selected == server
        assert ended == [wx.ID_OK]

        # A plain navigation key must not connect.
        arrow = wx.KeyEvent(wx.wxEVT_CHAR)
        arrow.SetKeyCode(wx.WXK_DOWN)
        skipped = []
        monkeypatch.setattr(arrow, "Skip", lambda: skipped.append(True))
        dialog._on_char(arrow)
        assert ended == [wx.ID_OK], "navigating the list must not connect"
        assert skipped == [True], "unhandled keys must be passed on"
    finally:
        dialog.Destroy()


def test_labels_line_up_in_the_frame_panels(app_ctx):
    from fusionfire.ui.main_frame import MenuPanel, StatsPanel

    menu = app_ctx.frame.content
    assert isinstance(menu, MenuPanel)
    _assert_labelled(menu.list, "Main menu:", "MenuPanel.list")

    app_ctx.menu_choice("stats")
    stats = app_ctx.frame.content
    assert isinstance(stats, StatsPanel)
    _assert_labelled(stats.report, "Your record:", "StatsPanel.report")


def test_the_first_enter_skips_the_intro_and_the_second_picks(tmp_path, monkeypatch):
    """The launch jingle holds the menu back, and is skippable with a single
    Enter. That Enter must not also pick a menu item -- the next one does."""
    from fusionfire.ui.main_frame import MenuPanel

    app, ctx = _boot_ctx(tmp_path, monkeypatch)
    try:
        assert ctx.frame.content is None, "the menu must wait for the jingle"
        assert ctx.audio.playing_music("intro")

        chosen = []
        monkeypatch.setattr(ctx, "menu_choice", lambda key: chosen.append(key))

        first = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        first.SetKeyCode(wx.WXK_RETURN)
        ctx.frame._on_intro_key(first)
        assert not ctx.audio.playing_music("intro"), "Enter must stop the jingle"
        assert ctx.audio.current_music is None
        assert chosen == [], "the skip keypress must not pick a menu item"

        menu = ctx.frame.content
        assert isinstance(menu, MenuPanel), "skipping must bring the menu up"
        # _on_key only acts when the list has keyboard focus, which wx does
        # not hand out reliably in a headless test.
        monkeypatch.setattr(wx.Window, "FindFocus", lambda: menu.list)
        second = wx.KeyEvent(wx.wxEVT_CHAR)
        second.SetKeyCode(wx.WXK_RETURN)
        menu._on_key(second)
        assert chosen == ["offline"], "the next Enter picks the highlighted item"
    finally:
        _tear_down(app, ctx)


def test_the_menu_comes_up_when_the_jingle_plays_out(tmp_path, monkeypatch):
    """If nobody presses Enter, the menu appears once the track ends."""
    from fusionfire.ui.main_frame import MenuPanel

    app, ctx = _boot_ctx(tmp_path, monkeypatch)
    try:
        assert ctx.frame.content is None
        assert ctx.audio.playing_music("intro")
        # The wx.CallLater that triggers this never fires without a
        # MainLoop, so drive the natural-end path it would have invoked.
        ctx._intro_timer.Stop()
        ctx._intro_played_out()
        assert isinstance(ctx.frame.content, MenuPanel), (
            "a played-out jingle must bring the menu up"
        )
        assert ctx.presenter.history == [], "the ready message must not be spoken"
    finally:
        _tear_down(app, ctx)


def test_a_label_built_after_its_control_is_refused(app_ctx):
    """The old shape -- control first, label second -- must not assemble.

    This is the bug in miniature. Without this guard the ordering rule lives
    only in a docstring, and the next field added to a dialog reintroduces
    the off-by-one silently.
    """
    from fusionfire.ui.widgets import field_label, stack

    host = wx.Panel(app_ctx.frame)
    try:
        control = wx.TextCtrl(host)
        late = field_label(host, "Your name:")  # created after the control
        with pytest.raises(ValueError, match="created after the control"):
            stack(late, control)

        # The right way round assembles fine.
        early = field_label(host, "Your name:")
        assert stack(early, wx.TextCtrl(host)) is not None
    finally:
        host.Destroy()


# ----------------------------------------------------------------------
# Accessible names, read back from Windows rather than from wx.
#
# Creation order, above, decides only what the platform can *infer*. That is
# enough for an edit, a combo box or a list box, whose oleacc proxies look
# backwards through the z-order for a static text. A native trackbar does no
# such lookup: left alone it answers the name query with its own position, so
# all three settings sliders announced as a bare number -- "95, slider", with
# nothing to say which volume it was. That is the unlabelled-sliders report.
#
# Checking this against wx's own GetAccessible() would be our code agreeing
# with itself: it hands back the object we stored, whatever Windows does with
# it. These go through oleacc's AccessibleObjectFromWindow and IAccessible,
# the same path NVDA and JAWS take, so a failure here is evidence about what
# is really announced.

_OBJID_CLIENT = 0xFFFFFFFC
_S_OK = 0
_VT_I4 = 3

# Vtable slots. IUnknown holds 0-2 and IDispatch 3-6, then IAccessible's own.
_SLOT_RELEASE = 2
_SLOT_CHILD_COUNT = 8
_SLOT_NAME = 10
_SLOT_VALUE = 11
_SLOT_ROLE = 13

ROLE_SYSTEM_LIST = 0x21
ROLE_SYSTEM_LISTITEM = 0x22
ROLE_SYSTEM_TEXT = 0x2A
ROLE_SYSTEM_COMBOBOX = 0x2E
ROLE_SYSTEM_SLIDER = 0x33
ROLE_SYSTEM_SPINBUTTON = 0x34


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class _VARIANT_U(ctypes.Union):
    # The 16-byte pad fixes the union at its real width; without it ctypes
    # would lay out a VARIANT too small and the callee would write past it.
    _fields_ = [("lVal", ctypes.c_long), ("bstrVal", ctypes.c_void_p),
                ("_pad", ctypes.c_byte * 16)]


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("_r1", ctypes.c_ushort),
                ("_r2", ctypes.c_ushort), ("_r3", ctypes.c_ushort),
                ("u", _VARIANT_U)]


_IID_IAccessible = _GUID(
    0x618736E0, 0x3C3D, 0x11CF,
    (ctypes.c_ubyte * 8)(0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71),
)

# 24 bytes is the 64-bit layout these prototypes are written against.
_MSAA = sys.platform == "win32" and ctypes.sizeof(_VARIANT) == 24
needs_msaa = pytest.mark.skipif(_MSAA is False, reason="MSAA is Windows-only")


def _child_id(index: int) -> _VARIANT:
    """CHILDID_SELF is 0; a control's own children are numbered from 1."""
    variant = _VARIANT()
    variant.vt = _VT_I4
    variant.u.lVal = index
    return variant


def _method(pointer, slot, prototype):
    vtable = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    return prototype(vtable[slot])


def _bstr_property(pointer, slot, index=0):
    prototype = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, _VARIANT, ctypes.POINTER(ctypes.c_void_p)
    )
    out = ctypes.c_void_p()
    if _method(pointer, slot, prototype)(pointer, _child_id(index), ctypes.byref(out)):
        return None
    if not out:
        return None
    try:
        return ctypes.cast(out, ctypes.c_wchar_p).value
    finally:
        ctypes.windll.oleaut32.SysFreeString(out)


def _int_property(pointer, slot, index=0):
    prototype = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, _VARIANT, ctypes.POINTER(_VARIANT)
    )
    out = _VARIANT()
    if _method(pointer, slot, prototype)(pointer, _child_id(index), ctypes.byref(out)):
        return None
    return out.u.lVal if out.vt == _VT_I4 else None


def announced(control: wx.Window) -> dict:
    """Everything Windows would tell a screen reader about ``control``.

    Snapshotted in one call so no test has to hold a COM pointer.
    """
    pointer = ctypes.c_void_p()
    result = ctypes.windll.oleacc.AccessibleObjectFromWindow(
        ctypes.c_void_p(control.GetHandle()), ctypes.c_ulong(_OBJID_CLIENT),
        ctypes.byref(_IID_IAccessible), ctypes.byref(pointer),
    )
    assert result == _S_OK and pointer, (
        f"Windows exposes no accessible object at all for "
        f"{type(control).__name__} {control.GetName()!r}"
    )
    try:
        count = ctypes.c_long()
        _method(
            pointer, _SLOT_CHILD_COUNT,
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_long)),
        )(pointer, ctypes.byref(count))
        return {
            "name": _bstr_property(pointer, _SLOT_NAME),
            "value": _bstr_property(pointer, _SLOT_VALUE),
            "role": _int_property(pointer, _SLOT_ROLE),
            "children": count.value,
            "child_names": [
                _bstr_property(pointer, _SLOT_NAME, i)
                for i in range(1, count.value + 1)
            ],
            "child_roles": [
                _int_property(pointer, _SLOT_ROLE, i)
                for i in range(1, count.value + 1)
            ],
        }
    finally:
        _method(
            pointer, _SLOT_RELEASE,
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p),
        )(pointer)


SETTINGS_SLIDERS = [
    ("sound_slider", "Sound volume (Home and End in game):"),
    ("music_slider", "Music volume (Page Up and Page Down in game):"),
    ("deadzone", "Stick dead zone, percent:"),
]


@needs_msaa
def test_the_settings_sliders_announce_their_label(app_ctx):
    """The reported bug, at the layer the screen reader reads.

    Asserting the name merely exists would not catch this: an unfixed slider
    reports a name of "95". It has to be the label.
    """
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        for attribute, expected in SETTINGS_SLIDERS:
            slider = getattr(dialog, attribute)
            spoken = announced(slider)["name"]
            assert spoken == expected, (
                f"SettingsDialog.{attribute}: Windows announces {spoken!r}, not "
                f"{expected!r}. A slider that reports a bare number is the "
                "unlabelled-slider bug."
            )
            # And the words announced are the words on the screen.
            assert spoken == _preceding_sibling(slider).GetLabelText()
    finally:
        dialog.Destroy()


@needs_msaa
def test_a_named_slider_still_announces_its_role_and_position(app_ctx):
    """Naming the slider must not cost it anything else.

    Only GetName is overridden; role, state and value still come from wx and
    the native control. A slider that announced its name but no longer its
    percentage would be a worse bug than the one being fixed.
    """
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        slider = dialog.sound_slider
        slider.SetValue(37)
        spoken = announced(slider)
        assert spoken["role"] == ROLE_SYSTEM_SLIDER, (
            f"role came back as {spoken['role']}, so it no longer reads as a slider"
        )
        assert spoken["value"] == "37", f"position announced as {spoken['value']!r}"
        assert spoken["name"] == "Sound volume (Home and End in game):"

        # The position is still read live, not frozen at the moment we named it.
        slider.SetValue(88)
        assert announced(slider)["value"] == "88"
    finally:
        dialog.Destroy()


@needs_msaa
def test_naming_a_list_box_does_not_rename_its_rows(app_ctx):
    """A list box is asked for its rows' names through the very same GetName
    that serves the field label, with the row number as the child id.
    Answering the label there renames every entry of the main menu, which
    would be a far worse regression than the bug being fixed."""
    menu = app_ctx.frame.content
    rows = [menu.list.GetString(i) for i in range(menu.list.GetCount())]

    spoken = announced(menu.list)
    assert spoken["name"] == "Main menu:"
    assert spoken["role"] == ROLE_SYSTEM_LIST
    assert spoken["children"] == len(rows)
    assert spoken["child_names"] == rows, (
        "the rows of the main menu no longer announce their own text"
    )
    assert spoken["child_roles"] == [ROLE_SYSTEM_LISTITEM] * len(rows)


@needs_msaa
def test_naming_a_slider_does_not_rename_its_parts(app_ctx):
    """The same trap, on the control we actually do name.

    A trackbar exposes three children -- the two page areas and the thumb --
    and is asked for their names through the very same GetName, with the
    child number as the id. Answering the field label there would rename all
    three. The names themselves are supplied by Windows and translated, so
    what is asserted is that they are still Windows' and not ours.
    """
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        spoken = announced(dialog.sound_slider)
        assert spoken["children"] == 3, "the slider no longer exposes its parts"
        assert spoken["name"] not in spoken["child_names"], (
            f"the slider's parts are announcing the field label "
            f"({spoken['child_names']}) -- GetName is answering for child ids "
            "it should be deferring on"
        )
    finally:
        dialog.Destroy()


@needs_msaa
def test_a_named_choice_still_announces_its_selection(app_ctx):
    """A combo box announces the chosen entry as its *value*, so that is what
    naming it must not disturb. (Its MSAA children are the text portion, the
    Open button and the drop-down, not the entries -- the row-naming trap
    above is a list box concern.)"""
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        choice = dialog.difficulty
        spoken = announced(choice)
        assert spoken["name"] == "Default difficulty:"
        assert spoken["role"] == ROLE_SYSTEM_COMBOBOX
        assert spoken["value"] == choice.GetStringSelection()

        choice.SetSelection(0)
        assert announced(choice)["value"] == choice.GetStringSelection()
    finally:
        dialog.Destroy()


def _labelled_controls(window: wx.Window) -> list[wx.Window]:
    """Every field ``stack`` has built, anywhere under ``window``.

    Found by the label ``stack`` records on each control, not by whether an
    accessible was attached, so a field the platform names for itself is
    checked just as strictly as one we had to name.
    """
    found = []
    pending = list(window.GetChildren())
    while pending:
        child = pending.pop()
        pending.extend(child.GetChildren())
        if getattr(child, "_field_label", None) is not None:
            found.append(child)
    return found


@needs_msaa
def test_every_labelled_control_announces_the_text_beside_it(app_ctx):
    """stack() names every control it builds, not only the ones known to
    need it. Whatever the control type, what Windows announces has to be the
    words the sighted player reads above it.

    This walks the real dialogs rather than a list of field names, so a field
    added later is covered without anyone remembering to add it here.
    """
    from fusionfire.ui.comment_dialog import CommentDialog
    from fusionfire.ui.online_dialog import OnlineDialog
    from fusionfire.ui.settings_dialog import SettingsDialog
    from fusionfire.ui.setup_dialog import OpponentDialog, SetupDialog

    frame = app_ctx.frame
    builders = [
        lambda: SettingsDialog(frame, app_ctx),
        lambda: SetupDialog(frame, app_ctx.settings),
        lambda: OpponentDialog(frame, app_ctx.settings),
        lambda: CommentDialog(frame, allow_chat=True),
        lambda: OnlineDialog(frame, app_ctx.settings),
    ]

    seen_types: set[str] = set()
    checked = 0
    for build in builders:
        dialog = build()
        try:
            for control in _labelled_controls(dialog):
                label = _preceding_sibling(control)
                assert isinstance(label, wx.StaticText), (
                    f"{type(dialog).__name__}.{control.GetName()!r} is named "
                    "after something that is not a static text"
                )
                spoken = announced(control)["name"]
                assert spoken == label.GetLabelText(), (
                    f"{type(dialog).__name__}: {type(control).__name__} "
                    f"announces {spoken!r} but the label beside it reads "
                    f"{label.GetLabelText()!r}"
                )
                seen_types.add(type(control).__name__)
                checked += 1
        finally:
            dialog.Destroy()

    assert checked >= 16, f"only {checked} labelled controls found; stack() is not naming them"
    # The fix is not slider-specific, and the coverage here should show it.
    assert {"Slider", "Choice", "TextCtrl", "ListBox", "SpinCtrl"} <= seen_types, (
        f"control types actually exercised: {sorted(seen_types)}"
    )


@needs_msaa
def test_the_statistics_box_still_announces_its_contents(app_ctx):
    """Why the naming is not applied to every control type indiscriminately.

    Attaching a wx.Accessible replaces the whole native accessible object,
    and wx's substitute is not always as good. On the rich edit behind
    review_box() it is markedly worse: the statistics box drops from role
    "text" with its contents readable as a value to role "client" with no
    value at all. It already announces its label without help, so it is left
    alone. If this test fails, that rule has been widened too far.
    """
    app_ctx.menu_choice("stats")
    stats = app_ctx.frame.content

    spoken = announced(stats.report)
    assert spoken["name"] == "Your record:"
    assert spoken["role"] == ROLE_SYSTEM_TEXT, (
        f"the statistics box now reports role {spoken['role']}, not text"
    )
    assert spoken["value"], "the statistics text is no longer exposed at all"
    assert spoken["value"].splitlines()[0] == stats.report.GetValue().splitlines()[0]


@needs_msaa
def test_the_accessible_name_survives_a_garbage_collection(app_ctx):
    """The object answering the name query is created inside stack() and
    would be collected the moment that call returned if nothing held it."""
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        gc.collect()
        assert announced(dialog.deadzone)["name"] == "Stick dead zone, percent:"
    finally:
        dialog.Destroy()


def test_only_the_name_is_overridden(app_ctx):
    """Role, state, value and the rest must fall through to wx's defaults.

    ``wx.ACC_NOT_IMPLEMENTED`` is what tells wx to let the native control
    answer. Overriding any of these by accident is how a slider loses its
    percentage, so assert the base behaviour is still in place.
    """
    from fusionfire.ui.widgets import _NamedByItsLabel

    host = wx.Panel(app_ctx.frame)
    try:
        label = wx.StaticText(host, label="Sound volume:")
        accessible = _NamedByItsLabel(label)

        assert accessible.GetName(wx.ACC_SELF) == (wx.ACC_OK, "Sound volume:")
        assert accessible.GetName(1) == (wx.ACC_NOT_IMPLEMENTED, "")

        # Structurally: GetName is the only part of the interface we touch.
        overridden = {
            attribute for attribute in vars(_NamedByItsLabel)
            if not attribute.startswith("_") and hasattr(wx.Accessible, attribute)
        }
        assert overridden == {"GetName"}, (
            f"the accessible also overrides {sorted(overridden - {'GetName'})}, "
            "which stops the native control answering for itself"
        )

        # And behaviourally, for the queries that carry a slider's percentage.
        # Only those returning a number, a string or a rect are called here:
        # wx leaves the out-parameter uninitialised when the default returns
        # NOT_IMPLEMENTED, so GetChild, GetFocus, GetParent and GetSelections
        # can hand back stack garbage in place of an object and take the
        # interpreter down with them.
        for query in ("GetRole", "GetState", "GetValue", "GetDescription",
                      "GetDefaultAction", "GetKeyboardShortcut", "GetHelpText",
                      "GetLocation"):
            answer = getattr(accessible, query)(wx.ACC_SELF)
            assert answer[0] == wx.ACC_NOT_IMPLEMENTED, (
                f"{query} no longer defers to wx, so the native control's "
                f"own answer is being suppressed: {answer!r}"
            )
        assert accessible.GetChildCount()[0] == wx.ACC_NOT_IMPLEMENTED
    finally:
        host.Destroy()


def test_starting_a_match_swaps_in_the_game_panel(match):
    ctx, panel = match
    from fusionfire.ui.game_panel import GamePanel

    assert isinstance(panel, GamePanel)
    assert ctx.engine is not None
    assert ctx.engine.phase is Phase.PLAYING


def test_combat_actions_reach_the_engine(match):
    ctx, panel = match
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.LOAD_GUN)
    assert ctx.engine.player.gun_loaded

    ctx.engine.turn = Side.PLAYER
    before = ctx.engine.player.bullets
    panel.handle_action(Action.FIRE_GUN)
    assert ctx.engine.player.bullets == before - 1


def test_free_actions_do_not_end_the_turn(match):
    ctx, panel = match
    ctx.engine.turn = Side.PLAYER
    for action in (
        Action.PLAYER_STATUS,
        Action.OPPONENT_STATUS,
        Action.REPEAT_LAST,
        Action.LAUGH,
        Action.TAUNT,
    ):
        panel.handle_action(action)
    assert ctx.engine.turn is Side.PLAYER


def test_the_taunt_button_says_something_rude_without_asking_which(match):
    """The comment dialog is the considered version: it lists all three. A
    taunt is one button and an immediate insult, which is what makes it
    usable in the beat between your turn ending and theirs starting."""
    from fusionfire.assets import COMMENTS

    ctx, panel = match
    played = []
    original = ctx.audio.play

    def spy(name, **kwargs):
        played.append(name)
        return original(name, **kwargs)

    ctx.audio.play = spy
    try:
        panel.handle_action(Action.TAUNT)
    finally:
        ctx.audio.play = original

    assert played, "the taunt button made no sound at all"
    assert played[0] in {f"comment_{key}" for key in COMMENTS}


def test_volume_keys_move_the_buses(match):
    ctx, panel = match
    sound_before = ctx.audio.sound_volume
    music_before = ctx.audio.music_volume
    panel.handle_action(Action.SOUND_UP)
    panel.handle_action(Action.MUSIC_DOWN)
    assert ctx.audio.sound_volume > sound_before
    assert ctx.audio.music_volume < music_before


def test_toggling_music_off_and_on_brings_the_score_back(match):
    ctx, panel = match
    assert ctx.settings.music_enabled

    panel.handle_action(Action.TOGGLE_MUSIC)
    assert not ctx.settings.music_enabled
    assert ctx.audio.current_music is None

    panel.handle_action(Action.TOGGLE_MUSIC)
    assert ctx.settings.music_enabled
    assert ctx.audio.current_music == "level1", "the score must actually restart"


def test_the_transcript_records_what_was_announced(match):
    ctx, panel = match
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.PLAYER_STATUS)
    assert ctx.presenter.history
    assert panel.transcript.GetValue().strip()


def test_gamepad_dispatch_reaches_the_panel(match, monkeypatch):
    # Forced to land, because a miss scores nothing and the point is the
    # proxy for the action having arrived at all.
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx, _panel = match
    ctx.engine.turn = Side.PLAYER
    before = ctx.engine.player.points
    ctx._dispatch_action(Action.CRACK_WHIP)
    assert ctx.engine.player.points == before + 1


def test_a_navigation_action_never_reaches_the_match(match, monkeypatch):
    """The mode travels with the dispatch. One produced while a menu was up
    must not land as a gunshot because the screen changed on the way."""
    ctx, panel = match
    ctx.engine.turn = Side.PLAYER
    acted = []
    monkeypatch.setattr(panel, "handle_action", acted.append)
    monkeypatch.setattr(ctx.navigator, "dispatch", lambda action: True)

    ctx._dispatch_action(Action.CONFIRM, navigation=True)
    assert acted == [], "a menu keystroke reached the match panel"


# ----------------------------------------------------------------------
# Which of the two things the pad is doing
#
# The same four face buttons fight during a match and answer dialogs
# everywhere else, so the only thing keeping A from firing a gun at the main
# menu is the mode following the screen. It is set in one place --
# swap_content -- for exactly that reason.
# ----------------------------------------------------------------------
def test_the_pad_navigates_the_menu_and_fights_in_a_match(app_ctx):
    from fusionfire.game.difficulty import get

    assert app_ctx.gamepad.navigation, "the front menu is not a fight"

    app_ctx._launch_match(get("intermediate"))
    assert not app_ctx.gamepad.navigation, "the match panel wants its weapons"

    app_ctx.leave_match()
    assert app_ctx.gamepad.navigation, "back at the menu, back to navigating"


def test_a_dialog_takes_the_pad_off_the_match(match):
    """Answering the comment dialog with the pad used to fire the gun behind
    it, because the buttons still went to the match."""
    ctx, panel = match
    assert not ctx.gamepad.navigation

    with ctx.modal_input(object()):
        assert ctx.gamepad.navigation, "a dialog is navigated, not fought"
    assert not ctx.gamepad.navigation, "the match must get the pad back"


def test_a_dialog_opened_from_a_dialog_hands_the_pad_back_to_the_right_one(match):
    ctx, _panel = match
    outer, inner = object(), object()

    with ctx.modal_input(outer):
        with ctx.modal_input(inner):
            assert ctx._modal_input_target is inner
        assert ctx._modal_input_target is outer
    assert ctx._modal_input_target is None


def test_the_bonus_round_keeps_the_match_bindings(match):
    """It is a dialog, but the D-pad has to keep moving between notes rather
    than trying to move a focus ring around a window with one control."""
    from fusionfire.ui.bonus_dialog import BonusDialog

    ctx, panel = match
    dialog = BonusDialog(panel, ctx.engine.difficulty, ctx.audio, ctx.speech)
    try:
        ctx.set_modal_input_target(dialog)
        assert not ctx.gamepad.navigation
    finally:
        ctx.set_modal_input_target(None)
        dialog._finish()
        dialog.Destroy()


def test_every_navigation_action_stands_for_a_key():
    """A navigation action with no keystroke behind it is a control that
    silently does nothing."""
    from fusionfire.input.gamepad import MENU_BUTTON_BINDINGS
    from fusionfire.ui.navigator import NAVIGATION_KEYS

    produced = set(MENU_BUTTON_BINDINGS.values()) | {
        Action.MOVE_LEFT, Action.MOVE_RIGHT, Action.MOVE_UP, Action.MOVE_DOWN,
    }
    assert produced <= set(NAVIGATION_KEYS)


def test_nothing_is_typed_while_the_game_is_in_the_background(app_ctx, monkeypatch):
    """The keystrokes are injected at the system level, so a thumb on the
    stick after alt-tabbing away would send Escape into somebody else's
    window."""
    monkeypatch.setattr(wx, "GetActiveWindow", lambda: None)
    assert app_ctx.navigator.dispatch(Action.CANCEL) is False


def test_an_action_with_no_keystroke_is_declined(app_ctx):
    assert app_ctx.navigator.dispatch(Action.FIRE_GUN) is False


# ----------------------------------------------------------------------
# Vibration
# ----------------------------------------------------------------------
def _rumbles(ctx, monkeypatch) -> list[tuple[float, float, int]]:
    """Record what the pad was asked to do, with no pad attached."""
    recorded: list[tuple[float, float, int]] = []
    monkeypatch.setattr(
        ctx.gamepad, "rumble", lambda low, high, ms: recorded.append((low, high, ms))
    )
    return recorded


def _strike(weapon: str, victim: Side, outcome: str = "hit"):
    from fusionfire.game.events import StrikeResolved

    return StrikeResolved(
        attacker=victim.other, weapon=weapon, outcome=outcome, damage=7, victim=victim
    )


def test_being_shot_is_felt_harder_than_being_lashed(match, monkeypatch):
    ctx, panel = match
    recorded = _rumbles(ctx, monkeypatch)

    panel._on_event(_strike("whip", Side.PLAYER))
    panel._on_event(_strike("gun", Side.PLAYER))

    assert len(recorded) == 2, "both hits should have been felt"
    whip, gun = recorded
    assert gun > whip, "a gunshot must be the heavier of the two"


def test_only_what_lands_on_you_buzzes(match, monkeypatch):
    """Feeling your own hits land would make the two indistinguishable,
    which is the one thing the buzz is for."""
    ctx, panel = match
    recorded = _rumbles(ctx, monkeypatch)

    panel._on_event(_strike("gun", Side.OPPONENT))
    panel._on_event(_strike("whip", Side.PLAYER, outcome="miss"))

    assert recorded == []


def test_the_cheat_prompt_applies_a_valid_code(match):
    ctx, panel = match
    panel.cheat.open()
    for char in "25 bullets":
        panel.cheat.type_char(char)
    result = panel.cheat.submit(
        ctx.engine.player, ctx.engine.opponent, ctx.engine.difficulty
    )
    assert result.ok
    assert ctx.engine.player.bullets >= 25


def test_a_bonus_result_folds_into_the_match(match):
    from fusionfire.game.bonus import BonusRound

    ctx, _panel = match
    bonus = BonusRound(ctx.engine.difficulty)
    bonus.toggle()
    ctx.presenter.render(ctx.engine.apply_bonus(bonus.finish()))
    assert ctx.engine.rounds_since_bonus == 0


def _land_the_killing_blow(ctx, panel, monkeypatch):
    """Finish the match deterministically.

    A lash misses about a fifth of the time, so leaving the roll to chance
    would make anything that depends on the match ending intermittently
    wrong.
    """
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: hi)
    ctx.engine.turn = Side.PLAYER
    ctx.engine.opponent.health = 1
    panel.handle_action(Action.CRACK_WHIP)


def test_a_match_can_be_played_to_a_finish(match, monkeypatch):
    ctx, panel = match
    _land_the_killing_blow(ctx, panel, monkeypatch)
    assert ctx.engine.phase is Phase.FINISHED
    assert ctx.engine.winner is Side.PLAYER


def test_results_are_written_to_the_temporary_stats_file(match, tmp_path, monkeypatch):
    ctx, panel = match
    _land_the_killing_blow(ctx, panel, monkeypatch)
    assert ctx.stats.games_played == 1
    assert ctx.stats.games_won == 1
    assert (tmp_path / "stats.json").is_file()


def _game_over_delay(ctx, panel, monkeypatch, winner):
    """The delay the panel chooses before offering a rematch, in ms."""
    from fusionfire.game.events import GameOver

    scheduled = []
    monkeypatch.setattr(
        "wx.CallLater", lambda ms, fn, *a, **k: (scheduled.append(ms), _Dummy(ms))[1]
    )
    panel._on_game_over(GameOver(winner, "reason"))
    return scheduled[0] if scheduled else None


def test_the_game_over_dialog_waits_for_the_win_music(match, monkeypatch):
    """The 'You win' dialog must not speak over win.wav or computerdie.wav."""
    ctx, panel = match
    expected = max(
        ctx.audio.length_of("computerdie"), ctx.audio.length_of("win")
    )
    assert expected > 5

    delay = _game_over_delay(ctx, panel, monkeypatch, Side.PLAYER)

    assert delay is not None, "no rematch was scheduled"
    assert delay >= int(expected * 1000), (
        f"the dialog was scheduled after {delay}ms, before the "
        f"{expected:.2f}s of closing sounds finished"
    )


def test_the_game_over_dialog_waits_for_the_die_music(match, monkeypatch):
    """The 'You lose' dialog must not speak over die.wav or userdie.wav."""
    ctx, panel = match
    expected = max(
        ctx.audio.length_of("userdie"), ctx.audio.length_of("die")
    )
    assert expected > 5

    delay = _game_over_delay(ctx, panel, monkeypatch, Side.OPPONENT)

    assert delay is not None, "no rematch was scheduled"
    assert delay >= int(expected * 1000), (
        f"the dialog was scheduled after {delay}ms, before the "
        f"{expected:.2f}s of closing sounds finished"
    )


def test_an_online_loss_waits_for_the_online_die_music(match, monkeypatch):
    """Online matches play dieo.wav and userdieo.wav rather than the offline
    names, so the wait has to follow the same suffix."""
    ctx, panel = match
    expected = max(
        ctx.audio.length_of("userdieo"), ctx.audio.length_of("dieo")
    )
    assert expected > 5

    ctx.engine.online = True
    delay = _game_over_delay(ctx, panel, monkeypatch, Side.OPPONENT)

    assert delay is not None, "no rematch was scheduled"
    assert delay >= int(expected * 1000), (
        f"an online loss was scheduled after {delay}ms, before the "
        f"{expected:.2f}s of closing sounds finished"
    )


def test_the_cheat_unlock_announcement_fires_only_on_the_first_earn(
    match, monkeypatch
):
    """The unlock line belongs to the moment the player crosses the
    threshold, not to every win afterwards. Regression: a player who had
    already earned the codes heard it announced at the end of every match."""
    from fusionfire.game.events import GameOver

    ctx, panel = match
    ctx.engine.player.points = 30  # the threshold, in a single match

    panel._on_game_over(GameOver(Side.PLAYER, "reason"))
    assert any(
        "Cheat codes unlocked" in line for line in ctx.presenter.history
    ), "the first earn must announce the unlock"

    # The file now exists; a second win must not announce it again.
    ctx.presenter.history.clear()
    panel._on_game_over(GameOver(Side.PLAYER, "reason"))
    assert not any(
        "Cheat codes unlocked" in line for line in ctx.presenter.history
    ), "a later win must not re-announce an already-earned unlock"


def test_the_cheat_unlock_announcement_needs_the_threshold(match, monkeypatch):
    """Crossing the threshold is what earns the codes; a win short of it
    must not announce or write the unlock."""
    from fusionfire.game.events import GameOver

    ctx, panel = match
    ctx.engine.player.points = 29
    panel._on_game_over(GameOver(Side.PLAYER, "reason"))
    assert not any(
        "Cheat codes unlocked" in line for line in ctx.presenter.history
    )
    from fusionfire import paths

    assert not paths.cheats_file().exists(), "the file must not be written"


def test_statistics_tally_hits_misses_and_damage(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: 5)

    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.gun_loaded = True
    panel.handle_action(Action.FIRE_GUN)

    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.CRACK_WHIP)

    assert ctx.stats.shots_fired == 1
    assert ctx.stats.shots_hit == 1
    assert ctx.stats.lashes == 1
    assert ctx.stats.lashes_hit == 1
    assert ctx.stats.damage_dealt == 10
    assert ctx.stats.damage_taken == 0


def test_a_miss_counts_the_attempt_but_not_the_hit(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: False)
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.CRACK_WHIP)
    assert ctx.stats.lashes == 1
    assert ctx.stats.lashes_hit == 0
    assert ctx.stats.damage_dealt == 0


def test_damage_taken_is_recorded_separately(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: 7)
    ctx.engine.turn = Side.OPPONENT
    ctx.presenter.render(ctx.engine.crack_whip(Side.OPPONENT))
    assert ctx.stats.damage_taken == 7
    assert ctx.stats.damage_dealt == 0
    assert ctx.stats.lashes == 0, "the opponent's lashes are not yours"


def test_switching_statistics_off_freezes_the_tally(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: 5)

    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.CRACK_WHIP)
    banked = ctx.stats.lashes
    assert banked == 1

    panel.handle_action(Action.TOGGLE_STATS)
    assert not ctx.settings.stats_enabled

    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.CRACK_WHIP)
    assert ctx.stats.lashes == banked, "what was banked before must survive untouched"


# ----------------------------------------------------------------------
# Keys that wx would otherwise claim.
#
# Enter, Escape and the arrows are navigation keys. Bound with EVT_KEY_DOWN
# they never arrive: the frame's default-button handling takes Enter, dialog
# navigation takes the arrows, and the multiline transcript takes both. Every
# one of these was a bug a player hit. The bindings must stay on
# EVT_CHAR_HOOK, which runs before any of that.
#
# These assert the wiring; real OS-level key delivery through
# wx.UIActionSimulator was verified separately, since it needs a foreground
# window and a running MainLoop that a test runner does not provide.


def test_the_game_panel_listens_on_char_hook(match, monkeypatch):
    """A KEY_DOWN binding here would silently lose Enter and the arrows."""
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx, panel = match
    ctx.engine.turn = Side.PLAYER
    before = ctx.engine.player.points

    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(ord("2"))
    panel.GetEventHandler().ProcessEvent(event)

    assert ctx.engine.player.points == before + 1, (
        "the panel did not act on a CHAR_HOOK key"
    )


def test_enter_reaches_the_game_panel_through_char_hook(match):
    ctx, panel = match
    ctx.settings.music_enabled = True

    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    panel.GetEventHandler().ProcessEvent(event)

    assert not ctx.settings.music_enabled, "Enter never reached the music toggle"


def test_the_menu_activates_on_enter_through_char_hook(app_ctx, monkeypatch):
    """Regression: Enter did nothing and the Go button was the only way in."""
    menu = app_ctx.frame.content
    chosen = []
    monkeypatch.setattr(app_ctx, "menu_choice", chosen.append)

    # The launch jingle owns the first keypress; stop it so this Enter
    # actually picks an item. The skip itself has its own test.
    app_ctx.skip_intro_music()

    menu.list.SetSelection(2)
    menu.list.SetFocus()
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    menu.GetEventHandler().ProcessEvent(event)

    assert chosen == ["stats"]


def test_the_bonus_round_moves_on_arrow_keys(match):
    """Regression: left and right did nothing, because dialog navigation
    consumed them before the KEY_DOWN binding ran."""
    from fusionfire.ui.bonus_dialog import BonusDialog

    ctx, panel = match
    dialog = BonusDialog(panel, ctx.engine.difficulty, ctx.audio, ctx.speech)
    try:
        start = dialog.round.cursor
        for code in (wx.WXK_RIGHT, wx.WXK_RIGHT):
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.SetKeyCode(code)
            dialog.GetEventHandler().ProcessEvent(event)
        assert dialog.round.cursor == start + 2

        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_SPACE)
        dialog.GetEventHandler().ProcessEvent(event)
        assert dialog.round.marked == {start + 2}

        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_LEFT)
        dialog.GetEventHandler().ProcessEvent(event)
        assert dialog.round.cursor == start + 1
    finally:
        dialog._ticker.Stop()
        dialog.Destroy()


def test_the_bonus_results_wait_for_the_horn(match, monkeypatch):
    """The results of a bonus round arrive after itemtimeout.wav finishes,
    not after an arbitrary delay."""
    from fusionfire.ui.bonus_dialog import BonusDialog

    ctx, panel = match
    dialog = BonusDialog(panel, ctx.engine.difficulty, ctx.audio, ctx.speech)
    scheduled = []
    monkeypatch.setattr(
        "wx.CallLater", lambda ms, fn, *a, **k: (scheduled.append(ms), _Dummy(ms))[1]
    )
    try:
        dialog._finish()
        horn = ctx.audio.length_of("itemtimeout")
        assert horn > 1, f"itemtimeout.wav measured {horn}s"
        assert scheduled and scheduled[0] >= int(horn * 1000), (
            f"the results were scheduled after {scheduled[0] if scheduled else 0}ms, "
            f"before the {horn:.2f}s horn finished"
        )
    finally:
        dialog._ticker.Stop()
        dialog.Destroy()


def test_the_machine_cannot_act_while_the_bonus_is_open(match, monkeypatch):
    """Regression: a machine turn falling due during the bonus round must
    wait rather than attack over the notes."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    scheduled = []
    monkeypatch.setattr(
        "wx.CallLater", lambda ms, fn, *a, **k: (scheduled.append(ms), _Dummy(ms))[1]
    )

    ctx.engine.turn = Side.OPPONENT
    panel._bonus_open = True
    before = ctx.engine.player.health
    panel._opponent_move()

    assert ctx.engine.player.health == before, "the machine attacked mid-bonus"
    assert ctx.engine.turn is Side.OPPONENT, "the machine's turn was consumed"
    assert scheduled, "the machine's move was not put back"


def test_opening_the_bonus_defers_the_machine_turn(match, monkeypatch):
    """If the player acted in the beat before a bonus opened, the machine's
    turn is due while the notes are on screen; it must wait for them."""
    ctx, panel = match
    ctx.engine.turn = Side.OPPONENT
    panel._schedule_opponent()
    assert panel._ai_timer is not None

    class FakeBonus:
        def __init__(self, parent, difficulty, audio, speech):
            self.result = None

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            pass

    monkeypatch.setattr("fusionfire.ui.game_panel.BonusDialog", FakeBonus)

    panel._open_bonus()

    assert ctx.engine.phase is Phase.PLAYING
    assert ctx.engine.turn is Side.OPPONENT, "the machine's turn was swallowed"
    assert panel._ai_timer is not None, "the machine was never put back"
    assert not panel._bonus_open

    panel._cancel_ai_timer()


def test_the_results_dialog_blocks_the_next_turn(match, monkeypatch):
    """The bonus results are shown in a dialog, and the machine's move only
    starts after the player dismisses it."""
    from fusionfire.game.bonus import BonusRound

    ctx, panel = match
    ctx.engine.turn = Side.OPPONENT

    class FakeBonus:
        def __init__(self, parent, difficulty, audio, speech):
            self.round = BonusRound(difficulty)

        def ShowModal(self):
            self.result = self.round.finish()
            return wx.ID_OK

        def Destroy(self):
            pass

    monkeypatch.setattr("fusionfire.ui.game_panel.BonusDialog", FakeBonus)
    calls = []

    def fake_message(parent, text, caption="", style=wx.OK):
        calls.append(("message", text))

    def fake_schedule_opponent():
        calls.append(("schedule", None))

    monkeypatch.setattr("fusionfire.ui.game_panel.message", fake_message)
    monkeypatch.setattr(panel, "_schedule_opponent", fake_schedule_opponent)

    panel._open_bonus()

    assert [kind for kind, _ in calls] == ["message", "schedule"], (
        "the machine's turn started before the results dialog was dismissed"
    )
    assert calls[0][1] == "You marked nothing. The machine keeps its items."


# ----------------------------------------------------------------------
# PowerWeapon timing
# ----------------------------------------------------------------------
def test_the_power_weapon_waits_for_the_drumroll(match, monkeypatch):
    """Pressing 6 starts the drumroll; the shot lands when it finishes."""
    from fusionfire.game.constants import PowerWeaponState

    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.engine.power_weapon.begin()
    ctx.engine.power_weapon.state = PowerWeaponState.READY
    ctx.engine.turn = Side.PLAYER

    panel.handle_action(Action.POWER_WEAPON)

    assert ctx.audio.current_music == "dr", "the drumroll should be playing"
    assert ctx.engine.opponent.health == 100, "nothing may land during the drumroll"
    assert panel._power_weapon_timer is not None, "no resolution was scheduled"

    panel._resolve_power_weapon()
    assert ctx.engine.opponent.health < 100
    assert ctx.engine.turn is Side.OPPONENT


def test_nothing_else_can_be_done_mid_drumroll(match, monkeypatch):
    from fusionfire.game.constants import PowerWeaponState

    # Every attack lands, so an unchanged score can only mean the whip was
    # refused rather than that it happened to miss.
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx, panel = match
    ctx.engine.power_weapon.begin()
    ctx.engine.power_weapon.state = PowerWeaponState.READY
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.POWER_WEAPON)

    before = ctx.engine.player.points
    panel.handle_action(Action.CRACK_WHIP)
    assert ctx.engine.player.points == before, "a second attack slipped through"

    panel._cancel_power_weapon_timer()


def test_the_drumroll_plays_even_with_music_switched_off(match):
    """The reported bug. Enter silences the *background score*; the drumroll
    is part of the shot, not accompaniment. The original was explicit that
    its music toggle 'does not affect win/lose/exit/end/logo/loading music'.
    """
    from fusionfire.game.constants import PowerWeaponState

    ctx, panel = match
    ctx.settings.music_enabled = False
    ctx.audio.stop_music(fade=0)

    ctx.engine.power_weapon.begin()
    ctx.engine.power_weapon.state = PowerWeaponState.READY
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.POWER_WEAPON)

    assert ctx.audio.current_music == "dr", (
        "the drumroll was silenced along with the background score"
    )
    panel._cancel_power_weapon_timer()


def test_the_background_score_still_obeys_the_music_toggle(match):
    from fusionfire.audio import NULL_HANDLE

    ctx, _panel = match
    ctx.settings.music_enabled = False
    assert ctx.audio.play_music("level1") is NULL_HANDLE
    assert ctx.audio.current_music is None


def test_event_music_ignores_the_music_toggle(app_ctx):
    """Win, lose, exit and the drumroll are events, not accompaniment."""
    app_ctx.settings.music_enabled = False
    for name in ("dr", "win", "die", "end", "exit"):
        app_ctx.audio.play_music(name, looping=False)
        assert app_ctx.audio.current_music == name, f"{name} was suppressed"
        app_ctx.audio.stop_music(fade=0)


# ----------------------------------------------------------------------
# Keeping the effects off the spoken status line.
#
# The report: "speech fires immediately after the player attacks, so it is
# drowned out by the attack sound unless your volumes are just right."
#
# The line is the game state and the sounds are flavour, so the line has to
# win. The awkward part is that only one side of the collision can be
# measured -- the sounds, exactly, from the files; the speech not at all,
# because the screen reader will not say how fast it talks or when it has
# stopped. Everything below is therefore checked in the sound domain, and
# these tests exist as much to stop a speech-duration guess creeping in as
# to check the behaviour.


class _SpeechLog:
    """Stands in for Speech, recording which channel each line reached."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.brailled: list[str] = []

    def speak(self, text, interrupt=True):
        self.spoken.append(text)

    def braille(self, text):
        self.brailled.append(text)

    def report(self, text, interrupt=True):
        self.spoken.append(text)
        self.brailled.append(text)

    def stop(self):
        pass


def _listen(ctx) -> _SpeechLog:
    heard = _SpeechLog()
    ctx.presenter.speech = heard
    return heard


def _take_a_shot(ctx, panel) -> None:
    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.gun_loaded = True
    panel.handle_action(Action.FIRE_GUN)


def _one_shots(ctx):
    """Live effect handles that are not the looping ambience beds."""
    ambience = list(ctx.audio._ambience.values())
    return [h for h in ctx.audio._live if h._bus == "sfx" and h not in ambience]


class _Dummy:
    """Stands in for a wx.CallLater when the test is capturing timers.

    It has to answer ``GetInterval`` like the real thing. The presenter uses
    that to tell the game how long a line still has to wait, and a double
    that cannot answer makes the caller think nothing is pending — which
    quietly reproduces the very bug these tests exist to catch.
    """

    def __init__(self, ms: int) -> None:
        self._ms = ms

    def GetInterval(self):
        return self._ms

    def IsRunning(self):
        return True

    def Stop(self):
        pass


def _line_timer(scheduled: list[int]) -> float:
    """The status line's own wait, in seconds.

    The first timer of the batch: the presenter schedules the line before
    the subscribers run, and the opponent's timer -- deliberately longer,
    since it waits for the line -- comes after.
    """
    assert scheduled, "nothing was scheduled"
    return scheduled[0] / 1000.0


def _capture_timers(monkeypatch) -> list[int]:
    """Record scheduled delays in ms instead of actually scheduling them."""
    scheduled: list[int] = []
    monkeypatch.setattr(
        "wx.CallLater", lambda ms, fn, *a, **k: (scheduled.append(ms), _Dummy(ms))[1]
    )
    return scheduled


# --- what the wait is built from --------------------------------------
def test_the_wait_covers_the_whole_sound_not_just_its_loud_part(app_ctx):
    """The correction. Waiting only until a sound stopped being loud put the
    line in the quiet tail of a scream, which still lands it mid-cry."""
    length = app_ctx.audio.length_of("usergun")
    assert length > 1.5, f"usergun.wav measured {length}s"
    assert not hasattr(app_ctx.audio, "masking_time"), (
        "the envelope measurement is gone; the wait is the whole sound now"
    )


def test_the_scream_outlasts_the_gun_that_caused_it(app_ctx):
    """Why waiting on the attack alone was not enough."""
    gun = app_ctx.audio.length_of("usergun")
    longer = [n for n in GROUPS["userhit"] if app_ctx.audio.length_of(n) > gun]
    assert longer, "no scream outlasts the gunshot; the premise has changed"


# --- what the player actually gets ------------------------------------
def test_the_status_line_waits_for_the_gunshot_and_the_scream(match, monkeypatch):
    """The reported bug: speech arrived mid-cry."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    longest = max(
        (n for n in GROUPS["userhit"]),
        key=lambda n: ctx.audio.length_of(n),
    )
    monkeypatch.setattr(
        "fusionfire.presenter.rng.choice", lambda members: longest
    )
    scheduled = _capture_timers(monkeypatch)
    heard = _listen(ctx)

    _take_a_shot(ctx, panel)

    assert not heard.spoken, "the line was spoken over the shot"
    assert ctx.presenter.pending_text, "no line is waiting"
    gun = ctx.audio.length_of("usergun")
    wait = _line_timer(scheduled)
    assert wait > gun, (
        f"waited {wait:.2f}s, which is inside the {gun:.2f}s gunshot alone -- "
        "the scream was not counted"
    )
    scream = ctx.audio.length_of(longest)
    assert wait == pytest.approx(scream, abs=0.05), (
        f"waited {wait:.2f}s; the whole {scream:.2f}s scream and nothing more"
    )


def test_a_miss_waits_only_for_the_attack(match, monkeypatch):
    """No scream plays on a miss, so nothing should be waited on for one."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: False)
    scheduled = _capture_timers(monkeypatch)

    _take_a_shot(ctx, panel)

    gun = ctx.audio.length_of("usergun")
    wait = _line_timer(scheduled)
    assert wait == pytest.approx(gun, abs=0.05), (
        f"a miss waited {wait:.2f}s; only the {gun:.2f}s gunshot plays"
    )


def test_screams_switched_off_shorten_the_wait(match, monkeypatch):
    """Nothing is played, so nothing is waited on."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.settings.screams_enabled = False
    scheduled = _capture_timers(monkeypatch)

    _take_a_shot(ctx, panel)

    gun = ctx.audio.length_of("usergun")
    wait = _line_timer(scheduled)
    assert wait == pytest.approx(gun, abs=0.05), (
        f"waited {wait:.2f}s with screams off; only the gunshot plays"
    )


def test_braille_and_the_transcript_never_wait(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.presenter.history.clear()
    heard = _listen(ctx)

    _take_a_shot(ctx, panel)

    assert ctx.presenter.history, "the transcript waited"
    assert heard.brailled, "braille waited"
    assert not heard.spoken, "speech did not wait"


def test_the_held_line_is_spoken_when_the_timer_fires(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    heard = _listen(ctx)

    _take_a_shot(ctx, panel)
    text = ctx.presenter.pending_text
    ctx.presenter.flush_pending()

    assert heard.spoken and heard.spoken[-1] == text
    assert ctx.presenter.pending_text is None


def test_a_long_sound_cannot_turn_into_a_long_silence(app_ctx, monkeypatch):
    """The power weapon impact runs a minute. Waiting it out would leave the
    player sitting in silence wondering what happened."""
    from fusionfire.config import SOUND_WAIT_CEILING

    scheduled = _capture_timers(monkeypatch)

    assert app_ctx.audio.length_of("userweaponhit") > 30
    app_ctx.presenter.announce("It landed.", after="userweaponhit")

    wait = _line_timer(scheduled)
    assert wait == pytest.approx(SOUND_WAIT_CEILING, abs=0.05)


def test_non_attack_lines_get_no_gap(app_ctx, monkeypatch):
    """Every line waits for its covering sound, and nothing else on top."""
    scheduled = _capture_timers(monkeypatch)
    app_ctx.presenter.announce("Out of bullets.", after="error")

    sound = app_ctx.audio.length_of("error")
    assert sound > 0
    assert _line_timer(scheduled) == pytest.approx(sound, abs=0.05), (
        "a line picked up a gap on top of its sound"
    )


def test_a_player_attack_line_waits_for_its_sound(match, monkeypatch):
    """The player's own attack outcomes line up with the machine's: the
    outcome waits for its sound, with no gap on top."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: False)
    scheduled = _capture_timers(monkeypatch)

    _take_a_shot(ctx, panel)

    assert ctx.presenter.pending_text, "the player's line was not held"
    gun = ctx.audio.length_of("usergun")
    assert _line_timer(scheduled) == pytest.approx(gun, abs=0.05), (
        "the player's attack waited longer than its sound"
    )


def test_a_machine_attack_line_gets_no_gap(match, monkeypatch):
    """The opponent's attack outcomes wait for their sounds and nothing else."""
    ctx, _panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: False)
    scheduled = _capture_timers(monkeypatch)
    ctx.engine.turn = Side.OPPONENT

    ctx.presenter.render(ctx.engine.crack_whip(Side.OPPONENT))

    assert ctx.presenter.pending_text, "the machine's line was not held"
    whip = ctx.audio.length_of("computerwhip")
    assert _line_timer(scheduled) == pytest.approx(whip, abs=0.05), (
        "the machine's attack waited longer than its sound"
    )


# --- rapid play --------------------------------------------------------
def test_firing_again_drops_the_line_that_was_still_waiting(match, monkeypatch):
    """A backlog of stale lines is worse than losing one."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)

    _take_a_shot(ctx, panel)
    first = ctx.presenter.pending_text
    _take_a_shot(ctx, panel)
    second = ctx.presenter.pending_text

    assert first and second
    assert ctx.presenter.pending_text == second, "a stale line survived"


def test_leaving_the_match_cancels_a_line_that_never_arrived(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)

    _take_a_shot(ctx, panel)
    assert ctx.presenter.pending_text

    ctx.leave_match()
    assert ctx.presenter.pending_text is None, (
        "a status line from a finished match arrived in the menu"
    )


def test_asking_for_a_repeat_says_it_now_rather_than_twice(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    _take_a_shot(ctx, panel)
    heard = _listen(ctx)

    ctx.presenter.repeat_last()

    assert len(heard.spoken) == 1, "the repeat and the held line both arrived"
    assert ctx.presenter.pending_text is None


# --- the duck ----------------------------------------------------------
def test_the_duck_spares_the_looping_beds(match):
    """Only relevant where the ceiling cut a wait short, but it must still
    never touch the idle hum -- a duck is never lifted."""
    ctx, _panel = match
    hum = ctx.audio._ambience.get("machine")
    assert hum is not None
    before = hum._stream.volume

    ctx.presenter._clear_the_way()

    assert hum._stream.volume == pytest.approx(before, abs=0.01)


def test_a_braille_only_player_keeps_their_effects(match, monkeypatch):
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.settings.speak_status = False
    ctx.settings.braille_status = True

    _take_a_shot(ctx, panel)

    assert ctx.presenter.pending_text is None, "nothing should be held for speech"
    assert all(h._duck == 1.0 for h in _one_shots(ctx)), (
        "effects were ducked for speech that is switched off"
    )


# --- pacing: the line has to actually be heard -------------------------
#
# Holding a line back only helps if it gets said. The opponent used to move
# on its own 0.7-1.8s timer while a line was due in five seconds, and
# anything it did cancelled that line. Measured over a real match: nought
# out of seventy-four status lines reached the player. A silent game, which
# is exactly what "the game is stuck" sounds like from the outside.


def test_the_opponent_waits_for_your_line_before_moving(match, monkeypatch):
    """The regression. The machine must not talk over your own outcome."""
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    scheduled = _capture_timers(monkeypatch)

    _take_a_shot(ctx, panel)

    assert ctx.presenter.pending_text, "no line is waiting"
    line_wait = _line_timer(scheduled)
    # The opponent's timer is the one scheduled after the line's.
    assert len(scheduled) >= 2, "the opponent was never scheduled"
    ai_wait = scheduled[-1] / 1000.0
    assert ai_wait > line_wait, (
        f"the machine moves at {ai_wait:.2f}s but your line is not spoken "
        f"until {line_wait:.2f}s, so it would cancel it"
    )


def test_the_line_is_scheduled_before_the_turn_handover_is_announced(match, monkeypatch):
    """Ordering, which is what made the first attempt at this fail.

    The panel paces the opponent from ``seconds_until_spoken``. If the line
    is scheduled after the subscribers run, that reads zero and the opponent
    goes in front of it.
    """
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)

    seen: list[float] = []

    from fusionfire.game.events import TurnChanged

    def watch(event):
        if isinstance(event, TurnChanged):
            seen.append(ctx.presenter.seconds_until_spoken())

    ctx.presenter.subscribe(watch)
    try:
        _take_a_shot(ctx, panel)
    finally:
        ctx.presenter.unsubscribe(watch)

    assert seen, "no turn handover was announced"
    assert seen[-1] > 0, (
        "the turn handover was announced before the line was scheduled, so "
        "anything pacing itself against the line sees nothing to wait for"
    )


def test_seconds_until_spoken_is_zero_when_nothing_waits(app_ctx):
    app_ctx.presenter.cancel_pending()
    assert app_ctx.presenter.seconds_until_spoken() == 0.0


def test_seconds_until_spoken_counts_down(app_ctx, monkeypatch):
    app_ctx.presenter.announce("Held line.", after="usergun")

    first = app_ctx.presenter.seconds_until_spoken()
    assert first > 0

    # Pretend a second has gone by.
    timer = app_ctx.presenter._pending_timer
    timer._ff_started -= 1.0
    assert app_ctx.presenter.seconds_until_spoken() == pytest.approx(first - 1.0, abs=0.1)

    app_ctx.presenter.cancel_pending()


def test_most_lines_survive_a_run_of_turns(match, monkeypatch):
    """An end-to-end check on the thing that was at zero.

    Plays several turns the way somebody listening would -- act, wait for the
    line, act again -- and asserts the lines actually get spoken rather than
    being cancelled by whatever happens next.
    """
    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)

    spoken: list[str] = []
    monkeypatch.setattr(ctx.speech, "speak",
                        lambda text, interrupt=True: spoken.append(text))

    for _ in range(6):
        ctx.engine.turn = Side.PLAYER
        ctx.engine.player.gun_loaded = True
        ctx.engine.opponent.health = 100
        panel.handle_action(Action.FIRE_GUN)
        # The listening player waits for the line before doing anything else.
        ctx.presenter.flush_pending()
        panel._cancel_ai_timer()

    assert len(spoken) == 6, f"only {len(spoken)} of 6 lines were spoken"


def test_turning_music_off_mid_drumroll_leaves_it_alone(app_ctx):
    app_ctx.settings.music_enabled = True
    app_ctx.audio.play_music("dr", looping=False)
    assert app_ctx.audio.current_music == "dr"

    app_ctx.audio.toggle_music()
    assert not app_ctx.settings.music_enabled
    assert app_ctx.audio.current_music == "dr", (
        "toggling music off cut the shot the player was in the middle of"
    )


def test_the_score_returns_after_the_drumroll(match, monkeypatch):
    """Firing takes the music slot; something has to give it back."""
    from fusionfire.game.constants import PowerWeaponState

    ctx, panel = match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: 5)

    ctx.engine.power_weapon.begin()
    ctx.engine.power_weapon.state = PowerWeaponState.READY
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.POWER_WEAPON)
    assert ctx.audio.current_music == "dr"

    panel._resolve_power_weapon()
    assert ctx.audio.current_music == "level1", (
        "the score never came back after the drumroll"
    )


def test_the_drumroll_delay_comes_from_the_file(app_ctx):
    length = app_ctx.audio.length_of("dr")
    assert length > 20, f"dr.wav measured {length}s"


# ----------------------------------------------------------------------
# Exit music
# ----------------------------------------------------------------------
def test_the_exit_music_plays_and_reports_its_length(app_ctx):
    """Regression: shutdown() freed the audio device immediately after
    starting the exit piece, so it was never heard."""
    duration = app_ctx.begin_exit()
    assert duration > 5, f"expected the exit piece, measured {duration}s"
    assert app_ctx.audio.available, "the device must still be open to play it"


def test_closing_defers_until_the_exit_music_finishes(app_ctx, monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "wx.CallLater", lambda ms, fn, *a, **k: scheduled.append(ms)
    )
    frame = app_ctx.frame

    event = wx.CloseEvent(wx.wxEVT_CLOSE_WINDOW)
    event.SetCanVeto(True)
    frame._on_close(event)

    assert frame._closing, "the frame should be waiting for the music"
    assert event.GetVeto(), "the close must be deferred, not taken"
    assert scheduled and scheduled[0] > 5000, f"scheduled {scheduled}"


# ----------------------------------------------------------------------
# Output device selection.
#
# The "Test this device" button reported as doing nothing was really doing
# something worse: two compounding off-by-ones meant every selection opened
# the device *before* the one picked. On this machine that routed the test
# sound into a virtual audio cable, so it played perfectly and was inaudible.
# Device identity is a name now, never a list position.


def test_the_device_list_is_not_empty(app_ctx):
    names = app_ctx.audio.device_names()
    assert names, "no output devices enumerated"
    assert all(isinstance(n, str) and n for n in names)


def test_no_silent_sink_is_offered(app_ctx):
    """BASS device 0 is 'No sound'. Offering it in an audio game is a trap."""
    assert not any(n.strip().lower() == "no sound" for n in app_ctx.audio.device_names())


def test_every_listed_device_resolves_to_its_own_id(app_ctx):
    """The regression: positions and BASS ids are not the same numbering."""
    pairs = app_ctx.audio.enumerate_devices()
    for name, device_id in pairs:
        assert app_ctx.audio.device_id_for(name) == device_id, (
            f"{name!r} resolved to the wrong device id"
        )
    ids = [device_id for _name, device_id in pairs]
    assert len(set(ids)) == len(ids), "two devices share an id"


def test_an_unknown_device_falls_back_to_the_default(app_ctx):
    """A headset unplugged since last launch must not silently hand play to
    whatever now sits at that position."""
    assert app_ctx.audio.device_id_for("A Device That Does Not Exist") == -1


def _bass_open_device() -> int:
    """The BASS device actually open right now.

    Asking the engine what device it is on would only report what we asked
    for; the original bug was that the request and the result differed. This
    reads it back from BASS itself.
    """
    from sound_lib.external.pybass import BASS_GetDevice

    return BASS_GetDevice()


def test_the_test_button_opens_the_device_that_was_picked(app_ctx):
    from fusionfire.ui.settings_dialog import SettingsDialog

    expected_ids = dict(app_ctx.audio.enumerate_devices())
    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        for position, name in enumerate(dialog._device_names):
            dialog.device.SetSelection(position)
            assert dialog._selected_device() == name

            dialog._test_audio(wx.CommandEvent(wx.wxEVT_BUTTON))

            # Verified against BASS, not against our own record of the choice.
            assert _bass_open_device() == expected_ids[name], (
                f"picked {name!r} (BASS {expected_ids[name]}) but BASS has "
                f"device {_bass_open_device()} open"
            )
            assert app_ctx.audio.current_device_name() == name
    finally:
        dialog.Destroy()


def test_the_test_button_actually_produces_a_sound(app_ctx):
    """'Does nothing' was the report; assert something audible happens."""
    from fusionfire.audio import NULL_HANDLE
    from fusionfire.ui.settings_dialog import SettingsDialog

    played = []
    original = app_ctx.audio.play

    def spy(name, **kwargs):
        played.append(name)
        return original(name, **kwargs)

    # Stop the launch jingle first: the test button switches the device, and
    # a device switch resumes whatever music was playing, which would put the
    # jingle in the recording and muddy exactly what the button played.
    app_ctx.skip_intro_music()
    app_ctx.audio.play = spy
    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        dialog.device.SetSelection(0)
        dialog._test_audio(wx.CommandEvent(wx.wxEVT_BUTTON))
    finally:
        dialog.Destroy()
        app_ctx.audio.play = original

    assert played == ["usergun"], f"the test button played {played}"
    assert original("usergun") is not NULL_HANDLE


def test_the_speech_test_button_speaks_even_with_status_speech_off(app_ctx):
    """Regression: the button reported as doing nothing was routing through
    report(), which is silent when "Speak status messages" is off -- the very
    player most likely to need to hear whether a backend works."""
    from fusionfire.ui.settings_dialog import SettingsDialog

    app_ctx.settings.speak_status = False
    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    said = []
    original = app_ctx.speech.speak

    def spy(text, interrupt=True):
        said.append(text)

    app_ctx.speech.speak = spy
    try:
        dialog.speak_status.SetValue(False)
        dialog._test_speech(wx.CommandEvent(wx.wxEVT_BUTTON))
    finally:
        app_ctx.speech.speak = original
        dialog.Destroy()

    assert said, "the test button did not speak a word"
    assert "speech test" in said[0]
    assert "Speaking through" in said[0]


def test_the_pitch_slider_defaults_to_zero_for_one_core_when_unset(app_ctx):
    """Regression: OneCore's floor is normal pitch, and 0% is that floor. The
    generic middle would send 0.755 to Prism, so merely opening and OK'ing the
    dialog would raise the player's voice."""
    from fusionfire.ui.settings_dialog import SettingsDialog

    app_ctx.settings.speech_pitch = -1.0  # never set
    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        tokens = [token for token, _ in dialog._backends]
        if "one_core" not in tokens:
            pytest.skip("OneCore not available on this machine")
        dialog.backend.SetSelection(tokens.index("one_core"))
        dialog._sync_voice_controls()
        assert dialog.pitch_slider.GetValue() == 0
    finally:
        dialog.Destroy()


def test_the_pitch_slider_default_stays_middle_for_sapi_when_unset(app_ctx):
    """The zero default is specific to OneCore: SAPI's middle is normal."""
    from fusionfire.ui.settings_dialog import SettingsDialog

    app_ctx.settings.speech_pitch = -1.0
    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        tokens = [token for token, _ in dialog._backends]
        if "sapi" not in tokens:
            pytest.skip("SAPI not available on this machine")
        dialog.backend.SetSelection(tokens.index("sapi"))
        dialog._sync_voice_controls()
        assert dialog.pitch_slider.GetValue() == 50
    finally:
        dialog.Destroy()


def test_cancelling_settings_undoes_a_device_switch(app_ctx):
    """Testing has to open a device to make a noise through it, so Cancel has
    live state to undo."""
    from fusionfire.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_ctx.frame, app_ctx)
    try:
        before = dialog._device_on_open
        if len(dialog._device_names) < 2:
            pytest.skip("only one output device on this machine")
        other = next(n for n in dialog._device_names if n != before)
        dialog.device.SetSelection(dialog._device_names.index(other))
        dialog._test_audio(wx.CommandEvent(wx.wxEVT_BUTTON))
        assert app_ctx.audio.current_device_name() == other

        dialog._on_cancel(wx.CommandEvent(wx.wxEVT_BUTTON))
        assert app_ctx.audio.current_device_name() == before
    finally:
        dialog.Destroy()


def test_the_device_survives_a_save_and_reload(app_ctx, tmp_path):
    """Stored by name, so it still means the same hardware next launch."""
    from fusionfire.config import Settings

    names = app_ctx.audio.device_names()
    app_ctx.settings.output_device_name = names[-1]
    app_ctx.settings.save(tmp_path / "settings.json")

    reloaded = Settings.load(tmp_path / "settings.json")
    assert reloaded.output_device_name == names[-1]


def test_the_intro_music_setting_survives_a_save_and_reload(app_ctx, tmp_path):
    from fusionfire.config import Settings

    assert app_ctx.settings.play_intro_music, "the launch jingle is on by default"
    app_ctx.settings.play_intro_music = False
    app_ctx.settings.save(tmp_path / "settings.json")

    reloaded = Settings.load(tmp_path / "settings.json")
    assert reloaded.play_intro_music is False


def test_leaving_a_match_returns_to_the_menu(match):
    from fusionfire.ui.main_frame import MenuPanel

    ctx, _panel = match
    ctx.leave_match()
    assert isinstance(ctx.frame.content, MenuPanel)
    assert ctx.engine is None


# ----------------------------------------------------------------------
# Online: the real panel's sending half
#
# test_online_match.py drives two engines over a real socket, but it
# reimplements the panel's glue to do it. These use the actual GamePanel,
# because the bug that shipped was precisely that the glue was missing --
# the receiving code was correct and nothing ever called the sender.
# ----------------------------------------------------------------------
class _FakeNet:
    """Records what the panel tries to send."""

    def __init__(self):
        self.sent = []
        self.closed = []

    def send(self, kind, **fields):
        self.sent.append((kind, fields))
        return True

    def close(self, reason="You left the game."):
        self.closed.append(reason)


@pytest.fixture
def online_match(app_ctx, monkeypatch):
    """A match wired up as an online one, with the wire replaced by a spy."""
    from fusionfire.game.difficulty import get
    from fusionfire.game.engine import Combatant, Engine
    from fusionfire.ui.game_panel import GamePanel

    net = _FakeNet()
    engine = Engine(
        Combatant(name="Ada Lovelace"),
        Combatant(name="Alan Turing"),
        get("intermediate"),
        online=True,
    )
    app_ctx.engine = engine
    panel = GamePanel(app_ctx.frame, app_ctx, engine, net=net)
    app_ctx.frame.swap_content(panel)
    panel._cancel_intro_timer()
    engine.start(first=Side.PLAYER)
    engine.begin_play()
    return app_ctx, panel, net


def test_firing_online_tells_the_other_player(online_match, monkeypatch):
    """The reported bug: the shot landed and the opponent heard nothing."""
    ctx, panel, net = online_match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.gun_loaded = True

    panel.handle_action(Action.FIRE_GUN)

    strikes = [fields for kind, fields in net.sent if kind == "strike"]
    assert strikes, f"nothing was sent; the panel sent {net.sent}"
    assert strikes[-1]["weapon"] == "gun"
    assert strikes[-1]["outcome"] in ("hit", "miss")


def test_lashing_online_tells_the_other_player(online_match, monkeypatch):
    ctx, panel, net = online_match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.engine.turn = Side.PLAYER

    panel.handle_action(Action.CRACK_WHIP)

    assert any(kind == "strike" and f["weapon"] == "whip" for kind, f in net.sent)


def test_healing_online_tells_the_other_player(online_match, monkeypatch):
    ctx, panel, net = online_match
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: 20)
    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.health = 50

    panel.handle_action(Action.RESTORE_HEALTH)

    heals = [f for kind, f in net.sent if kind == "heal"]
    assert heals, f"the heal was not reported; sent {net.sent}"
    assert heals[-1]["amount"] == 20


def test_loading_online_tells_the_other_player(online_match):
    ctx, panel, net = online_match
    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.gun_loaded = False

    panel.handle_action(Action.LOAD_GUN)

    assert any(kind == "load" for kind, _f in net.sent)


def test_a_refused_load_is_not_reported(online_match):
    """Reporting something that did not happen desyncs the two ends just as
    surely as failing to report something that did."""
    ctx, panel, net = online_match
    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.gun_loaded = True

    panel.handle_action(Action.LOAD_GUN)

    assert not any(kind == "load" for kind, _f in net.sent)


def test_a_refused_heal_is_not_reported(online_match):
    ctx, panel, net = online_match
    ctx.engine.turn = Side.PLAYER
    ctx.engine.player.health = 100

    panel.handle_action(Action.RESTORE_HEALTH)

    assert not any(kind == "heal" for kind, _f in net.sent)


def test_the_opponents_own_moves_are_not_echoed_back(online_match, monkeypatch):
    """Applying what they sent must not send it straight back at them."""
    ctx, panel, net = online_match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.engine.turn = Side.OPPONENT

    ctx._apply_remote_move(
        {"type": "strike", "weapon": "gun", "outcome": "hit", "damage": 10}
    )

    assert not net.sent, f"their move was echoed back: {net.sent}"


def test_an_offline_match_sends_nothing(match, monkeypatch):
    """The same code path runs offline, where there is no wire at all."""
    ctx, panel = match
    assert panel.net is None
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    ctx.engine.turn = Side.PLAYER
    panel.handle_action(Action.CRACK_WHIP)  # must not raise


def test_every_turn_ending_action_reports_itself(online_match, monkeypatch):
    """A blanket check. Anything that ends the turn locally has to cross the
    wire, or the two engines stop agreeing about whose go it is -- which is
    a deadlock with nothing logged anywhere."""
    ctx, panel, net = online_match
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)

    for action, expected in (
        (Action.FIRE_GUN, "strike"),
        (Action.CRACK_WHIP, "strike"),
        (Action.USE_BOMB, "strike"),
    ):
        net.sent.clear()
        ctx.engine.turn = Side.PLAYER
        ctx.engine.player.gun_loaded = True
        ctx.engine.player.bombs = 1
        before = ctx.engine.turn

        panel.handle_action(action)

        if ctx.engine.turn is not before:  # it really did take the turn
            assert any(kind == expected for kind, _f in net.sent), (
                f"{action.value} ended the turn without telling the other player"
            )


def test_a_long_online_wait_is_explained(online_match, monkeypatch):
    """The deadlock the player hit was completely silent. Even with the
    cause fixed, a future desync should say something rather than leaving
    the game apparently stopped."""
    import time as _time

    ctx, panel, _net = online_match
    said = []
    monkeypatch.setattr(ctx.presenter, "report",
                        lambda text, interrupt=True: said.append(text))

    ctx.engine.turn = Side.OPPONENT
    panel._waiting_since = _time.monotonic() - (panel.WAIT_WARNING + 1)
    panel._warned_waiting = False

    panel._check_still_waiting()

    assert said, "a stalled online match said nothing at all"
    assert "Alan Turing" in said[0]


def test_a_normal_wait_says_nothing(online_match, monkeypatch):
    import time as _time

    ctx, panel, _net = online_match
    said = []
    monkeypatch.setattr(ctx.presenter, "report",
                        lambda text, interrupt=True: said.append(text))

    ctx.engine.turn = Side.OPPONENT
    panel._waiting_since = _time.monotonic() - 2.0
    panel._check_still_waiting()

    assert not said, "it complained about an ordinary pause"


def test_it_only_says_it_once(online_match, monkeypatch):
    import time as _time

    ctx, panel, _net = online_match
    said = []
    monkeypatch.setattr(ctx.presenter, "report",
                        lambda text, interrupt=True: said.append(text))

    ctx.engine.turn = Side.OPPONENT
    panel._waiting_since = _time.monotonic() - (panel.WAIT_WARNING + 1)
    panel._warned_waiting = False
    for _ in range(5):
        panel._check_still_waiting()

    assert len(said) == 1, f"it nagged {len(said)} times"


def test_an_offline_match_never_warns_about_waiting(match):
    """Offline the machine takes its own turn; there is nobody to wait for."""
    import time as _time

    ctx, panel = match
    panel._waiting_since = _time.monotonic() - 600
    panel._warned_waiting = False
    panel._check_still_waiting()
    assert not panel._warned_waiting


# ----------------------------------------------------------------------
# Online: the ending
#
# Both engines reach the game over on their own, off the same strike, and
# both then say so down the wire. Treating the other end's announcement as
# the opponent walking out dropped both players at the main menu the instant
# an online match finished -- and the result dialog, parented to the panel
# that had just been destroyed, never appeared. Reported as: offline you are
# told you won, online you are simply at the menu.
# ----------------------------------------------------------------------
def _finish_online(ctx, panel, net, monkeypatch):
    """Play the online match to a genuine finish, wire and all.

    Hands back the ending and the callback the panel scheduled behind the
    win music, so a test can reach the dialog without waiting the eight
    seconds the player does.
    """
    from fusionfire.game.events import GameOver

    ctx.net = net
    endings, scheduled = [], []

    def watch(event):
        if isinstance(event, GameOver):
            endings.append(event)

    ctx.presenter.subscribe(watch)
    # The presenter schedules through this as well, so the rematch call is
    # picked out by name rather than by being the only one.
    monkeypatch.setattr(
        "wx.CallLater",
        lambda ms, fn, *a, **k: (scheduled.append((fn, a)), _Dummy(ms))[1],
    )
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    try:
        ctx.engine.opponent.health = 1
        ctx.engine.turn = Side.PLAYER
        panel.handle_action(Action.CRACK_WHIP)
    finally:
        ctx.presenter.unsubscribe(watch)

    assert ctx.engine.phase is Phase.FINISHED, "the match did not finish"
    offers = [
        (fn, args) for fn, args in scheduled
        if getattr(fn, "__name__", "") == "_offer_rematch"
    ]
    assert offers, "no result dialog was scheduled"
    return endings[0], offers[0]


def test_an_online_ending_is_not_a_resignation(online_match, monkeypatch):
    """The reported bug. Our own engine has already finished, so the peer
    saying so is an echo, not somebody walking out."""
    ctx, panel, net = online_match
    _finish_online(ctx, panel, net, monkeypatch)

    ctx._on_net_message({"type": "resign", "reason": "Match finished."})

    assert ctx.frame.content is panel, "the match screen was torn down"
    assert ctx.engine is not None, "the match was abandoned by its own ending"


def test_the_peer_hanging_up_afterwards_leaves_the_ending_alone(
    online_match, monkeypatch
):
    """Whichever player closes their dialog first hangs up, and the other is
    usually still reading theirs."""
    ctx, panel, net = online_match
    _finish_online(ctx, panel, net, monkeypatch)

    reported = []
    monkeypatch.setattr(
        ctx.presenter, "report", lambda text, *a, **k: reported.append(text)
    )

    ctx._on_net_disconnected("The other player disconnected.")

    assert ctx.frame.content is panel, "the match screen was torn down"
    assert reported == [], f"spoke over the ending: {reported}"
    assert ctx.net is None, "the dead session was kept"


def test_the_result_dialog_appears_online_too(online_match, monkeypatch):
    """What was actually asked for: online tells you that you won, the same
    way offline does."""
    ctx, panel, net = online_match
    ending, (offer, args) = _finish_online(ctx, panel, net, monkeypatch)

    shown = []
    monkeypatch.setattr(
        "fusionfire.ui.game_panel.message",
        lambda parent, text, caption, style=wx.OK: (
            shown.append((text, caption)), wx.ID_OK
        )[1],
    )
    # Everything the other end has to say arrives first, as it does in a
    # real match: the dialog is held back behind the win music.
    ctx._on_net_message({"type": "resign", "reason": "Match finished."})
    ctx._on_net_disconnected("The other player disconnected.")
    offer(*args)

    assert len(shown) == 1, f"the result dialog did not appear: {shown}"
    text, caption = shown[0]
    assert caption == "You win"
    assert ending.reason in text


def test_a_real_resignation_still_ends_the_match(online_match):
    """The safety net stays: a peer that walks out mid-match, or one whose
    engine never reached the ending, is still acted on."""
    from fusionfire.ui.main_frame import MenuPanel

    ctx, _panel, net = online_match
    ctx.net = net

    ctx._on_net_message({"type": "resign", "reason": "Alan left."})

    assert isinstance(ctx.frame.content, MenuPanel)
    assert ctx.engine is None


def test_a_connection_lost_mid_match_still_ends_it(online_match):
    from fusionfire.ui.main_frame import MenuPanel

    ctx, _panel, net = online_match
    ctx.net = net

    ctx._on_net_disconnected("The other player disconnected.")

    assert isinstance(ctx.frame.content, MenuPanel)


def test_leaving_does_not_announce_your_own_departure(online_match, monkeypatch):
    """close() hands the reason we sent the other player straight back to us
    through the same callback a real disconnection uses. Spoken here, it
    tells the person who just left that their opponent left."""
    ctx, _panel, net = online_match
    ctx.net = net

    reported = []
    monkeypatch.setattr(
        ctx.presenter, "report", lambda text, *a, **k: reported.append(text)
    )

    ctx.leave_match()
    ctx._on_net_disconnected("Your opponent left the game.")

    assert net.closed == ["Your opponent left the game."], "the peer was not told"
    assert reported == [], f"told the player they themselves had left: {reported}"


def test_a_chat_message_names_who_said_it(online_match, monkeypatch):
    """Reported: the receiver heard "Opponent says", never the name they
    were given at the start of the match."""
    ctx, _panel, _net = online_match

    reported = []
    monkeypatch.setattr(
        ctx.presenter, "report", lambda text, *a, **k: reported.append(text)
    )

    ctx._on_net_message({"type": "chat", "text": "Nice shot."})

    assert reported == ["Alan Turing says: Nice shot."]


def test_a_chat_arriving_before_the_match_still_says_something(app_ctx, monkeypatch):
    """There is no opponent to name until their hello has been read. No
    ordinary match produces this, but silence would be worse than a
    placeholder if one ever did."""
    reported = []
    monkeypatch.setattr(
        app_ctx.presenter, "report", lambda text, *a, **k: reported.append(text)
    )

    app_ctx._on_net_message({"type": "chat", "text": "Hello?"})

    assert reported == ["Your opponent says: Hello?"]
