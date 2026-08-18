"""Voice, rate and pitch.

A screen reader owns its own voice: NVDA reports it can set neither voice,
rate nor pitch, because the user configured those in NVDA and the game has
no business overriding them. The platform voices are the opposite -- OneCore
and SAPI expose all three, and somebody playing without a screen reader has
nowhere else to set them. So the controls follow the backend.

Two things here were measured against the live backends rather than assumed,
and both would fail silently if they regressed:

* Prism takes 0.0 to 1.0 and raises for anything outside it, while the
  settings and the sliders are percentages. The conversion is easy to lose,
  and losing it does nothing visible -- the exception is swallowed and the
  voice simply never changes.
* OneCore jumps to maximum pitch for anything at or below 50%,
  so the slider maps onto the half above that.
"""

from __future__ import annotations

import pytest

from fusionfire.config import Settings
from fusionfire.speech import DEFAULT_PITCH_RANGE, Speech


# ----------------------------------------------------------------------
# The percent to unit conversion
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "percent,expected",
    [(0, 0.0), (50, 0.5), (100, 1.0), (250, 1.0), (-30, 0.0)],
)
def test_percentages_become_the_unit_range_prism_wants(percent, expected):
    assert Speech._as_unit(percent) == pytest.approx(expected)


def test_nothing_escapes_the_range_prism_accepts():
    """Prism raises PrismRangeError outside 0..1, and the raise is swallowed,
    so an unconverted percentage would quietly do nothing at all."""
    for percent in range(-100, 301, 7):
        assert 0.0 <= Speech._as_unit(percent) <= 1.0


# ----------------------------------------------------------------------
# Settings validation
# ----------------------------------------------------------------------
def test_the_defaults_leave_every_voice_setting_alone():
    settings = Settings()
    settings.normalise()
    assert settings.speech_voice == ""
    assert settings.speech_rate == -1.0
    assert settings.speech_pitch == -1.0
    assert settings.speech_volume == -1.0


@pytest.mark.parametrize("field", ["speech_rate", "speech_pitch", "speech_volume"])
@pytest.mark.parametrize(
    "written,expected",
    [(0, 0.0), (50, 50.0), (100, 100.0), (400, 100.0), (-1, -1.0), (-99, -1.0)],
)
def test_voice_numbers_are_clamped_or_left_alone(field, written, expected):
    settings = Settings()
    setattr(settings, field, written)
    settings.normalise()
    assert getattr(settings, field) == expected


@pytest.mark.parametrize("field", ["speech_rate", "speech_pitch", "speech_volume"])
@pytest.mark.parametrize("bogus", [None, "fast", [50], {}])
def test_nonsense_voice_numbers_fall_back_to_leaving_it_alone(field, bogus):
    settings = Settings()
    setattr(settings, field, bogus)
    settings.normalise()
    assert getattr(settings, field) == -1.0


def test_the_voice_is_remembered_by_name_not_by_number():
    """Installing or removing a voice renumbers them; ending up on a
    different voice silently is worse than falling back to the default."""
    settings = Settings()
    settings.speech_voice = "  Microsoft Hazel  "
    settings.normalise()
    assert settings.speech_voice == "Microsoft Hazel"


def test_an_absurd_voice_name_is_trimmed():
    settings = Settings()
    settings.speech_voice = "x" * 500
    settings.normalise()
    assert len(settings.speech_voice) <= 128


def test_the_voice_settings_survive_a_save_and_reload(tmp_path):
    settings = Settings()
    settings.speech_voice = "Microsoft David"
    settings.speech_rate = 70.0
    settings.speech_pitch = 60.0
    settings.save(tmp_path / "settings.json")

    reloaded = Settings.load(tmp_path / "settings.json")
    assert reloaded.speech_voice == "Microsoft David"
    assert reloaded.speech_rate == 70.0
    assert reloaded.speech_pitch == 60.0


# ----------------------------------------------------------------------
# The measured OneCore pitch mapping
#
# Established by synthesising to memory and measuring the fundamental
# frequency, which is the only way to see what a pitch value really does.
# Reading it back out of Prism reports what it stored, not what the
# synthesiser did -- and believing the read-back is how this was got wrong
# twice.
#
# OneCore on a 157 Hz male voice, sent straight to Prism:
#
#     0.00 - 0.50  ->  302 Hz   every value, the maximum
#     0.51         ->  162 Hz   normal
#     1.00         ->  302 Hz   the maximum
#
# The bottom half does not do nothing; it jumps to maximum pitch. Only
# 0.51 upward is monotonic, so the slider maps onto that. SAPI measured
# monotonic over the full range and keeps it.
# ----------------------------------------------------------------------
def test_onecore_maps_onto_the_half_that_behaves(speech):
    assert speech.pitch_range("one_core") == (0.51, 1.0)


@pytest.mark.parametrize("spelling", ["one_core", "onecore", "ONE_CORE", "OneCore"])
def test_every_spelling_of_the_name_finds_the_range(speech, spelling):
    """The token the dropdown carries is the BackendId name lowered. An
    earlier table keyed on a different spelling clamped nothing at all, and
    the test that was supposed to catch it used the same wrong spelling."""
    assert speech.pitch_range(spelling) == (0.51, 1.0)


def test_the_dropdown_tokens_all_resolve(speech):
    for token, _label in speech.list_backends():
        low, high = speech.pitch_range(token)
        assert 0.0 <= low < high <= 1.0, f"{token!r} resolved to {(low, high)}"


def test_a_well_behaved_backend_keeps_the_whole_range(speech):
    assert speech.pitch_range("sapi") == DEFAULT_PITCH_RANGE
    assert speech.pitch_range("nvda") == DEFAULT_PITCH_RANGE
    assert not speech.raises_pitch_only("sapi")


def test_auto_resolves_to_whatever_is_really_running(speech):
    """'auto' is the default, and when no screen reader is running it lands
    on OneCore -- the case that matters most."""
    live = speech.backend_name.lower().replace("_", "")
    expected = (0.51, 1.0) if "onecore" in live else DEFAULT_PITCH_RANGE
    assert speech.pitch_range("auto") == expected


def test_the_slider_ends_map_to_the_ends_of_the_range(speech):
    """0% must reach the bottom of what the backend does and 100% the top,
    or the control does not span what it claims to."""
    low, high = speech.pitch_range()
    assert speech._to_backend("pitch", 0) == pytest.approx(low)
    assert speech._to_backend("pitch", 100) == pytest.approx(high)


def test_the_mapping_is_monotonic(speech):
    values = [speech._to_backend("pitch", p) for p in range(0, 101, 5)]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_the_mapping_round_trips(speech):
    for percent in (0, 25, 50, 75, 100):
        sent = speech._to_backend("pitch", percent)
        assert speech._from_backend("pitch", sent) == pytest.approx(percent, abs=0.5)


def test_rate_and_volume_are_not_remapped(speech):
    """Only pitch has a backend quirk; the others are the plain unit range."""
    for what in ("rate", "volume"):
        assert speech._to_backend(what, 0) == 0.0
        assert speech._to_backend(what, 50) == pytest.approx(0.5)
        assert speech._to_backend(what, 100) == 1.0


def test_no_slider_position_reaches_the_broken_region(speech):
    """The whole point. On OneCore anything at or below 0.50 produces
    maximum pitch, so the slider must never send one."""
    low, _high = speech.pitch_range()
    if low == 0.0:
        pytest.skip("this backend has no broken region")
    for percent in range(0, 101):
        assert speech._to_backend("pitch", percent) >= low


# ----------------------------------------------------------------------
# Against whatever backend is really here
# ----------------------------------------------------------------------
@pytest.fixture
def speech():
    engine = Speech(Settings())
    if not engine.available:
        pytest.skip("no speech backend on this machine")
    yield engine
    engine.shutdown()


def test_a_backend_reports_what_it_can_do(speech):
    for what in ("voice", "rate", "pitch"):
        assert isinstance(speech.supports(what), bool)


def test_describing_a_backend_never_raises(speech):
    for token in ("auto", "onecore", "sapi", "nvda", "not_a_backend"):
        offers = speech.describe(token)
        assert set(offers) == {"voices", "voice", "rate", "pitch"}
        assert isinstance(offers["voices"], list)


def test_selecting_a_voice_that_is_not_installed_is_refused(speech):
    assert not speech.set_voice("Definitely Not An Installed Voice")


def test_an_empty_voice_name_changes_nothing(speech):
    assert not speech.set_voice("")


def test_a_screen_reader_is_left_to_its_own_settings():
    """The important half. NVDA users set rate and voice in NVDA."""
    settings = Settings()
    settings.speech_backend = "nvda"
    engine = Speech(settings)
    try:
        if engine.backend_name != "NVDA":
            pytest.skip("NVDA is not running")
        assert not engine.supports("voice")
        assert not engine.supports("rate")
        assert not engine.supports("pitch")
        assert engine.voice_names() == []
    finally:
        engine.shutdown()
