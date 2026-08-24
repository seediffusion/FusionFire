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
