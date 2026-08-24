"""What the suite is not allowed to do to the machine it runs on.

All of it was reported by someone running the tests on the desktop they were
using at the time, rather than in CI, which is where a suite of desktop tests
gets run most of the time and where none of this shows up.

**It must not talk.** Booting an :class:`~fusionfire.app.AppContext` opens
whatever screen reader is running, and the suite boots roughly two hundred of
them. Each then speaks match commentary, menu labels and every refusal
through it. That is not a load any screen reader is built for; the report was
NVDA needing a restart afterwards, which is the game's own accessibility
layer turned on its user. The four methods that reach a screen reader are
stubbed, and nothing else about the speech layer is touched.

**It must not shout either.** The same fixtures open a real audio device and
play real gunshots, for a minute and a half. The buses are silenced rather
than the device: every handle is still created, started and tracked, so
nothing a test can see about the audio engine changes. They simply start at
zero.

**It must not keep taking the foreground.** Starting the application shows
its main window, and the suite starts one for almost every application test.
Each took the foreground, so a run was a couple of hundred focus changes
through whatever the player was doing -- announced one after another by the
screen reader that was trying to read something else. The window is still
built, and still answers MSAA, which is what the accessibility tests actually
ask of it; it is simply never shown, and never focused into.

**And it must not take the machine over.** Real windows, real sockets, real
audio, a minute and a half of them. Below normal priority costs a couple of
seconds on an idle machine and gives the desktop back on a busy one.

Each silence is lifted for the tests that are about the thing being silenced:
a marker for speech and for the volume controls, and
``FUSION_FIRE_TEST_WINDOWS=1`` to watch a run happen.
"""

from __future__ import annotations

import ctypes
import os
import sys

import pytest

#: Windows process priority. Not "idle", which would starve the suite behind
#: anything at all; just below whatever the player is doing.
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

#: Every wx class that defines a focus-taking method of its own, and the
#: original it defines. wx.Panel shadows wx.Window's, so patching the base
#: class alone leaves the one call that matters -- the panel swap that puts
#: focus on the front menu -- going straight through.
_FOCUS_METHODS: dict[tuple[type, str], object] = {}
try:  # pragma: no cover - import-time plumbing
    import wx as _wx

    for _cls in (_wx.Window, _wx.Panel):
        for _name in ("SetFocus", "SetFocusIgnoringChildren"):
            if _name in _cls.__dict__:
                _FOCUS_METHODS[(_cls, _name)] = _cls.__dict__[_name]
except ImportError:  # pragma: no cover - wx is optional for some tests
    pass


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


def _stay_hidden(self, show: bool = True) -> bool:
    """Stand-in for Frame.Show that never puts anything on screen."""
    return False


def _focus_without_barging(original):
    """Wrap a focus-taking method so it cannot drag a hidden window into view.

    Hiding the frame is not enough on its own. Windows treats focusing a
    control as a request to activate the window holding it, so the very next
    thing the game does -- putting focus on the front menu, which it does
    deliberately and rightly -- hauled the invisible window to the front
    anyway. It reached the foreground without ever having been shown.

    Focus inside a window nobody can see means nothing, so it is not placed.
    Anywhere else, including a window a test does show, this is the original.
    """

    def guarded(self, *args, **kwargs):
        import wx

        top = wx.GetTopLevelParent(self)
        if top is not None and not top.IsShown():
            return None
        return original(self, *args, **kwargs)

    return guarded


@pytest.fixture(autouse=True)
def _keep_your_windows_to_yourself(monkeypatch):
    """Build the game's window, and never show it.

    ``AppContext.start`` shows the main frame, and the suite starts one for
    almost every application test. Each of those takes the foreground, so a
    run is a couple of hundred focus changes through whatever the player was
    doing -- which a screen reader announces, one after another, for a minute
    and a half.

    Hiding it costs nothing. The window is still created, so it still has a
    real handle and MSAA still answers for every control in it, which is what
    the accessibility tests actually ask about; visibility is not part of the
    question. It is the only top-level window the game ever shows, and no
    dialog in the suite is opened modally, so with this in place a run puts
    nothing on screen at all.

    Set ``FUSION_FIRE_TEST_WINDOWS=1`` to watch a run instead.
    """
    if os.environ.get("FUSION_FIRE_TEST_WINDOWS", "").strip():
        return

    from fusionfire.ui.main_frame import MainFrame

    monkeypatch.setattr(MainFrame, "Show", _stay_hidden, raising=False)
    for (owner, name), original in _FOCUS_METHODS.items():
        monkeypatch.setattr(owner, name, _focus_without_barging(original))
