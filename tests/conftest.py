"""What the suite is not allowed to do to the machine it runs on.

Both of these were reported by someone running the tests on the desktop they
were using at the time, rather than in CI, which is where a suite of desktop
tests is actually run most of the time.

**It must not talk.** Booting an :class:`~fusionfire.app.AppContext` opens
whatever screen reader is running, and the suite boots roughly two hundred of
them. Each one then speaks match commentary, menu labels and every refusal
through it, and each one opens and closes its own connection on the way past.
That is not a load any screen reader is built for; the report was NVDA
needing a restart afterwards, which is the game's own accessibility layer
being turned on its user.

Speech is therefore withheld — using the game's own silent path rather than a
stand-in, so the class the tests exercise is still the real one. A
:class:`~fusionfire.speech.Speech` with no backend behind it answers its whole
surface and says nothing, because that is what the game does on a machine
with no speech library at all.

**It must not shout either.** The same fixtures open a real audio device and
play real gunshots, for a minute and a half. The buses are silenced rather
than the device: every handle is still created, started and tracked, so
nothing a test can see about the audio engine changes. They simply start at
zero.

**And it must not take the machine over.** Real windows, real sockets, real
audio, ninety seconds of them. Below normal priority costs a couple of
seconds on an idle machine and gives the desktop back on a busy one.

Both silences are lifted by a marker, for the handful of tests that are about
the speech layer or the volume controls themselves.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

#: Windows process priority. Not "idle", which would starve the suite behind
#: anything at all; just below whatever the player is doing.
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "speaks_aloud: needs a real speech backend, so leave this one audible",
    )
    config.addinivalue_line(
        "markers",
        "plays_aloud: reads the audio engine's own volumes, so leave them alone",
    )
    _step_out_of_the_way()


def _step_out_of_the_way() -> None:
    """Ask Windows to schedule the run behind whatever else is going on."""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        # Declared, not assumed. A handle is a pointer, and ctypes defaults
        # every return value to a 32-bit int -- which truncates it on a
        # 64-bit build, and the call then fails and returns zero without
        # raising. That is exactly what it did until a test looked.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        kernel32.SetPriorityClass(
            kernel32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS
        )
    except Exception:  # pragma: no cover - a nicety, never a requirement
        pass


def _say_nothing(self, *args, **kwargs) -> None:
    """Stand-in for every method that would reach a screen reader."""


@pytest.fixture(autouse=True)
def _hold_the_tongue(request, monkeypatch):
    """Give the application a speech layer that is real, and silent."""
    if "speaks_aloud" in request.keywords:
        return

    from fusionfire import speech as speech_module
    from fusionfire.speech_ao2 import AO2Speech

    # The three that reach a screen reader, and the one that interrupts it.
    # Everything else is left exactly as it is, backend and all: the layer
    # still opens, still reports what it can do, and still gets torn down the
    # way the game tears it down. Only the words are dropped.
    #
    # Withholding the backend instead was tried and is worse than it looks --
    # opening it is also what initialises COM for the process, and a suite
    # that skips that dies later in somebody else's COM call.
    for owner in (speech_module.Speech, AO2Speech):
        for method in ("speak", "braille", "report", "stop"):
            monkeypatch.setattr(owner, method, _say_nothing, raising=False)


@pytest.fixture(autouse=True)
def _turn_it_down(request, monkeypatch):
    """Play everything the game would play, at nothing."""
    if "plays_aloud" in request.keywords:
        return

    from fusionfire.audio import AudioEngine

    # The buses, not the settings and not the device. Handles are still
    # created, started, tracked and asked whether they are playing, so what
    # a test can observe is unchanged.
    monkeypatch.setattr(AudioEngine, "sound_volume", property(lambda self: 0.0))
    monkeypatch.setattr(AudioEngine, "music_volume", property(lambda self: 0.0))
