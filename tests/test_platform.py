"""Older Windows, and the theme.

Two features change shape with the Windows version, and both fail in the
same quiet way if they get it wrong: a speech library that will not load
leaves the game silent, and an appearance call that is accepted but ignored
leaves a white window on a dark desktop. Neither raises.
"""

from __future__ import annotations

import pytest

from fusionfire import platform_info
from fusionfire.config import Settings
from fusionfire.ui_theme_values import THEME_MODES


# ----------------------------------------------------------------------
# Version detection
# ----------------------------------------------------------------------
def test_this_machine_is_identified():
    described = platform_info.describe()
    assert described
    if platform_info.is_windows():
        assert described.startswith("Windows")


@pytest.mark.parametrize(
    "version,expected",
    [
        ((10, 0, 26200), "Windows 11"),
        ((10, 0, 22000), "Windows 11"),
        ((10, 0, 19045), "Windows 10"),
        ((10, 0, 10240), "Windows 10"),
        ((6, 3, 9600), "Windows 8.1"),
        ((6, 2, 9200), "Windows 8"),
        ((6, 1, 7601), "Windows 7"),
    ],
)
def test_each_windows_is_named_correctly(monkeypatch, version, expected):
    monkeypatch.setattr(platform_info, "is_windows", lambda: True)
    monkeypatch.setattr(platform_info, "windows_version", lambda: version)
    assert platform_info.describe() == expected


@pytest.mark.parametrize(
    "version,modern",
    [((10, 0, 26200), True), ((10, 0, 10240), True),
     ((6, 3, 9600), False), ((6, 1, 7601), False)],
)
def test_only_windows_10_and_later_count_as_modern(monkeypatch, version, modern):
    monkeypatch.setattr(platform_info, "is_windows", lambda: True)
    monkeypatch.setattr(platform_info, "windows_version", lambda: version)
    assert platform_info.is_windows_10_or_later() is modern
    assert platform_info.follows_system_theme() is modern


# ----------------------------------------------------------------------
# The speech fallback
# ----------------------------------------------------------------------
def test_the_fallback_speech_layer_answers_everything_the_game_asks(monkeypatch):
    """The game holds one object either way, so the fallback has to answer
    the whole surface -- including the parts it cannot do."""
    from fusionfire.speech_ao2 import AO2Speech

    speech = AO2Speech(Settings())
    for method in (
        "available", "backend_name", "list_backends", "supports", "voice_names",
        "set_voice", "current_value", "pitch_range", "raises_pitch_only",
        "describe", "apply_settings", "reload", "speak", "braille", "report",
        "stop", "shutdown",
    ):
        assert hasattr(speech, method), f"the fallback is missing {method}"


def test_the_fallback_offers_no_voice_controls():
    """accessible_output2 cannot set a voice, rate or pitch. Saying so is
    what stops the settings dialog offering controls that do nothing."""
    from fusionfire.speech_ao2 import AO2Speech

    speech = AO2Speech(Settings())
    for what in ("voice", "rate", "pitch"):
        assert not speech.supports(what)
    assert speech.voice_names() == []
    assert not speech.set_voice("Anything At All")
    assert speech.current_value("rate") == -1.0

    offers = speech.describe("auto")
    assert offers == {"voices": [], "voice": False, "rate": False, "pitch": False}


def test_the_fallback_offers_one_backend_choice():
    from fusionfire.speech_ao2 import AO2Speech

    backends = AO2Speech(Settings()).list_backends()
    assert len(backends) == 1
    assert backends[0][0] == "auto"


def test_windows_10_gets_prism(monkeypatch):
    from fusionfire import speech as speech_module

    if not speech_module.HAVE_PRISM:
        pytest.skip("Prism is not installed")
    monkeypatch.setattr(platform_info, "is_windows_10_or_later", lambda: True)
    engine = speech_module.open_speech(Settings())
    try:
        assert isinstance(engine, speech_module.Speech)
    finally:
        engine.shutdown()


def test_older_windows_gets_accessible_output2(monkeypatch):
    """The point of the exercise: Prism's native half is built against WinRT
    and drives voices that do not exist before Windows 10."""
    from fusionfire import speech as speech_module
    from fusionfire.speech_ao2 import HAVE_AO2, AO2Speech

    if not HAVE_AO2:
        pytest.skip("accessible_output2 is not installed")
    monkeypatch.setattr(platform_info, "is_windows_10_or_later", lambda: False)

    engine = speech_module.open_speech(Settings())
    try:
        assert isinstance(engine, AO2Speech)
    finally:
        engine.shutdown()


def test_the_fallback_never_raises_on_output():
    """Silence is survivable; an exception during a match is not."""
    from fusionfire.speech_ao2 import AO2Speech

    speech = AO2Speech(Settings())
    speech.speak("A line.")
    speech.braille("A line.")
    speech.report("A line.")
    speech.stop()
    speech.apply_settings()
    speech.shutdown()
    speech.speak("After shutdown.")


# ----------------------------------------------------------------------
# The theme setting
# ----------------------------------------------------------------------
def test_the_theme_setting_defaults_to_following_the_system():
    settings = Settings()
    settings.normalise()
    assert settings.theme == "system"


@pytest.mark.parametrize("bogus", [None, "", "midnight", 7, ["dark"]])
def test_a_nonsense_theme_falls_back(bogus):
    settings = Settings()
    settings.theme = bogus
    settings.normalise()
    assert settings.theme in THEME_MODES


def test_the_theme_survives_a_save_and_reload(tmp_path):
    settings = Settings()
    settings.theme = "dark"
    settings.save(tmp_path / "settings.json")
    assert Settings.load(tmp_path / "settings.json").theme == "dark"


def test_config_validates_the_theme_without_importing_wx():
    """``config`` has to stay importable with no display, so the list of
    legal theme names lives away from the module that imports wx."""
    import fusionfire.ui_theme_values as values

    source = __import__("pathlib").Path(values.__file__).read_text(encoding="utf-8")
    assert "import wx" not in source


def test_older_windows_is_not_offered_a_setting_it_has_not_got(monkeypatch):
    from fusionfire.ui import theme

    monkeypatch.setattr(platform_info, "follows_system_theme", lambda: False)
    offered = [key for key, _label in theme.available_modes()]
    assert "system" not in offered, (
        "Windows 8.1 and 7 have no light/dark setting to follow, so offering "
        "to follow it would be a control that does nothing"
    )
    assert offered == ["light", "dark"]
    assert theme.default_mode() == "light"
    assert theme.normalise("system") == "light"


def test_modern_windows_is_offered_all_three(monkeypatch):
    from fusionfire.ui import theme

    monkeypatch.setattr(platform_info, "follows_system_theme", lambda: True)
    offered = [key for key, _label in theme.available_modes()]
    assert offered == ["system", "light", "dark"]


def test_an_explicit_choice_ignores_the_system(monkeypatch):
    from fusionfire.ui import theme

    monkeypatch.setattr(theme, "os_is_dark", lambda: True)
    assert theme.wants_dark("light") is False
    monkeypatch.setattr(theme, "os_is_dark", lambda: False)
    assert theme.wants_dark("dark") is True


def test_following_the_system_tracks_the_system(monkeypatch):
    from fusionfire.ui import theme

    monkeypatch.setattr(platform_info, "follows_system_theme", lambda: True)
    monkeypatch.setattr(theme, "os_is_dark", lambda: True)
    assert theme.wants_dark("system") is True
    monkeypatch.setattr(theme, "os_is_dark", lambda: False)
    assert theme.wants_dark("system") is False


def test_the_dark_palette_is_actually_dark_and_readable():
    from fusionfire.ui import theme

    def luma(colour):
        return 0.299 * colour.Red() + 0.587 * colour.Green() + 0.114 * colour.Blue()

    assert luma(theme.DARK["window"]) < 60
    assert luma(theme.DARK["text"]) > 200
    assert luma(theme.LIGHT["window"]) > 200
    assert luma(theme.LIGHT["text"]) < 60
    # A field has to be distinguishable from the window behind it.
    assert theme.DARK["field"] != theme.DARK["window"]
