"""The guard rails in ``conftest.py``, checked.

They are the sort of thing that gets quietly removed during a refactor and
is not missed until someone runs the suite on the machine they were using at
the time. Which is exactly how they came to be written: the suite booted two
hundred applications, spoke every line of every match through whatever screen
reader was running, and played the gunshots out loud while it did it. NVDA
needed restarting afterwards.

So the silences are asserted rather than assumed. A run that has lost one of
them fails here instead of in somebody's ears.
"""

from __future__ import annotations

import sys

import pytest

from fusionfire.audio import AudioEngine
from fusionfire.config import Settings
from fusionfire.speech import Speech
from fusionfire.speech_ao2 import AO2Speech


@pytest.mark.parametrize("owner", [Speech, AO2Speech])
@pytest.mark.parametrize("method", ["speak", "braille", "report", "stop"])
def test_nothing_reaches_a_screen_reader(owner, method):
    """Every route out of the speech layer is stopped, on both backends."""
    call = getattr(owner, method, None)
    assert call is not None, f"{owner.__name__} lost its {method}"
    assert call.__name__ == "_say_nothing", (
        f"{owner.__name__}.{method} would reach a real screen reader"
    )


def test_a_marked_test_gets_its_voice_back(pytestconfig):
    """The lift has to work, or the tests that are *about* speech would be
    testing the stand-in."""
    assert pytestconfig.getini("markers") is not None
    # test_voice_settings.py and test_platform.py carry the marker at module
    # level; this checks the mechanism they rely on is registered.
    names = " ".join(pytestconfig.getini("markers"))
    assert "speaks_aloud" in names
    assert "plays_aloud" in names


class _JustSettings:
    """Enough of an AudioEngine for its volume properties to be asked."""

    settings = Settings()


def test_the_buses_are_turned_down():
    assert AudioEngine.sound_volume.fget(_JustSettings()) == 0.0
    assert AudioEngine.music_volume.fget(_JustSettings()) == 0.0


@pytest.mark.plays_aloud
def test_the_marker_gives_the_volume_back():
    """And proves the muting is the fixture's doing rather than the machine
    simply having no sound card."""
    assert AudioEngine.sound_volume.fget(_JustSettings()) == Settings().sound_volume


def test_the_game_window_is_never_shown():
    """The other half of staying out of the way. Two hundred windows taking
    the foreground is two hundred focus changes read out over whatever the
    player was actually doing."""
    from fusionfire.ui.main_frame import MainFrame

    assert MainFrame.Show.__name__ == "_stay_hidden", (
        "the suite would put its window in front of whatever you are using"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows foreground window")
def test_a_run_does_not_take_the_foreground(tmp_path, monkeypatch):
    """Asked of Windows rather than of wx: whatever is in front when the
    application starts has to still be in front afterwards.

    Boots its own application rather than borrowing the shared fixture,
    because the thing under test is what starting one does to the desktop.
    """
    import ctypes

    wx = pytest.importorskip("wx")
    monkeypatch.setattr("fusionfire.paths.data_dir", lambda: tmp_path)

    from fusionfire.app import AppContext

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    before = user32.GetForegroundWindow()

    app = wx.App(redirect=False)
    ctx = AppContext()
    ctx.settings.gamepad_enabled = False
    ctx.settings.check_for_updates = False
    ctx.settings.play_intro_music = False
    try:
        ctx.start()
        assert user32.GetForegroundWindow() == before, (
            "starting the game pulled the foreground away from what was in front"
        )
        assert not ctx.frame.IsShown(), "the game's window was put on screen"
    finally:
        ctx.shutdown()
        if ctx.frame:
            ctx.frame.Destroy()
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows scheduling")
def test_the_run_stays_out_of_the_way():
    """Below normal, so the desktop is still usable while this is running."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel32.GetPriorityClass.restype = ctypes.c_uint
    priority = kernel32.GetPriorityClass(kernel32.GetCurrentProcess())
    assert priority == 0x00004000, f"priority class is {priority:#x}, not below normal"
