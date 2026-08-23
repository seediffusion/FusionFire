"""Keeping sound effects off the spoken status line.

The complaint these cover: the gunshot, the reaction scream and the line
saying what happened all started at the same instant, so whether you could
make out "you hit for 12" depended on how your volumes happened to be set.

What makes this awkward to fix is an asymmetry. Sound durations are exact,
straight from the files. Speech durations are unknowable: the live NVDA
backend reports ``supports_is_speaking``, ``supports_set_rate`` and
``supports_get_rate`` all false, so the game cannot be told when a line
finishes, cannot read the rate to estimate it, and cannot raise the speech
to compete. Every number in the fix is therefore a sound measurement, and
these tests exist largely to keep it that way.

A first attempt waited only until a sound stopped being *loud*, on a -20 dB
envelope measurement. Playing it revealed the flaw the measurement could
not: the screams are human voices, and starting a line in the quiet tail of
a cry still lands the speech mid-cry. The line now waits for the sound to
finish outright — for the player's attacks and the machine's alike.

The device-level and presenter-level halves live in ``test_app_smoke.py``,
where a real audio engine and a real settings dialog are already booted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fusionfire.assets import CATALOGUE, GROUPS
from fusionfire.game import engine as engine_module
from fusionfire.game.constants import Side, Weapon
from fusionfire.game.difficulty import INTERMEDIATE
from fusionfire.game.engine import ATTACK_SOUNDS, Combatant, Engine
from fusionfire.game.events import PlaySound, Say


@pytest.fixture
def engine():
    player = Combatant(name="Ada Lovelace", gender="female")
    opponent = Combatant(name="Blue Screen")
    eng = Engine(player, opponent, INTERMEDIATE)
    eng.start(use_power_weapon=False, first=Side.PLAYER)
    eng.begin_play()
    return eng


def _lines(events) -> list[Say]:
    return [e for e in events if isinstance(e, Say) and e.text]


def _sounds(events) -> list[str]:
    """The names and groups of every effect an action asked to be played."""
    return [
        e.name or e.group for e in events if isinstance(e, PlaySound)
    ]


# ----------------------------------------------------------------------
# What the rules say about timing
#
# The engine has no audio device and cannot measure anything. All it does
# is name the sound a line arrives alongside; turning that name into a
# number is the presenter's job. These check the naming.
# ----------------------------------------------------------------------
def test_a_shot_names_the_gun_that_would_cover_its_outcome(engine, monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    engine.player.gun_loaded = True

    lines = _lines(engine.fire_gun(Side.PLAYER))

    assert lines, "firing said nothing"
    assert lines[0].after == "usergun"


def test_a_miss_is_held_back_just_as_a_hit_is(engine, monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: False)
    engine.player.gun_loaded = True

    lines = _lines(engine.fire_gun(Side.PLAYER))

    assert lines[0].after == "usergun", (
        "a miss is covered by the same gunshot a hit is"
    )


def test_the_lash_names_the_whip(engine, monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    assert _lines(engine.crack_whip(Side.PLAYER))[0].after == "userwhip"


def test_the_machine_s_attacks_name_the_machine_s_weapons(engine, monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    engine.turn = Side.OPPONENT
    assert _lines(engine.crack_whip(Side.OPPONENT))[0].after == "computerwhip"


def test_a_refusal_says_why_and_makes_no_noise_about_it(engine):
    """A blocked action gets the reason, and nothing else.

    The buzz used to fire alongside it -- the spoken line even waited out
    ``error.wav`` so it would not be talked over. But every refusal is
    already a sentence saying exactly what is wrong, and hearing half a
    second of buzzer before each one, on a keystroke as ordinary as loading
    an already-loaded gun, was noise on top of an answer. The sentence
    stayed; the buzz went, and with it the delay that only existed to make
    room for it.
    """
    engine.player.gun_loaded = False
    lines = _lines(engine.fire_gun(Side.PLAYER))

    assert lines[0].text.startswith("Your gun is not loaded")
    assert lines[0].after is None, "the refusal is still waiting on a sound"
    assert not _sounds(engine.fire_gun(Side.PLAYER)), "a refusal played a sound"


def test_no_blocked_action_reaches_for_the_buzzer(engine):
    """Every refusal in the rules, not just the one that was reported."""
    engine.player.gun_loaded = True
    engine.player.bullets = 0
    engine.player.restores = 0
    engine.player.bombs = 0
    engine.player.health = 100

    refusals = [
        engine.load_gun(Side.PLAYER),        # already loaded
        engine.restore_health(Side.PLAYER),  # none left, and on full health
        engine.use_bomb(Side.PLAYER),        # none held
        engine.launch_power_weapon()[0],     # never brought to the fight
    ]
    engine.turn = Side.OPPONENT
    refusals.append(engine.fire_gun(Side.PLAYER))  # not your turn

    for events in refusals:
        assert not _sounds(events), f"a refusal played {_sounds(events)}"
        for line in _lines(events):
            assert line.after is None, f"{line.text!r} is waiting on a sound"


def test_the_opponent_s_restore_names_the_group_not_a_member(engine, monkeypatch):
    """Which of the four plays is not decided yet, so the group is named.

    The presenter resolves it, and waits on the member it actually started
    rather than on the longest one in the group.
    """
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    engine.turn = Side.OPPONENT
    engine.opponent.health = 20

    lines = _lines(engine.restore_health(Side.OPPONENT))

    assert lines[0].after == "computerrestore"
    assert "computerrestore" in GROUPS


def test_the_result_of_the_match_waits_for_the_death_sound(engine, monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: hi)
    engine.opponent.health = 1

    lines = _lines(engine.crack_whip(Side.PLAYER))

    assert lines[-1].after == "computerdie", (
        "the line announcing the win is the one line nobody should miss"
    )


def test_a_free_action_is_never_held_back(engine):
    """Nothing is playing when you ask for your own status, so nothing waits."""
    for line in _lines(engine.player_status()) + _lines(engine.opponent_status()):
        assert line.after is None


# ----------------------------------------------------------------------
# Structural
# ----------------------------------------------------------------------
def test_every_sound_a_status_line_waits_for_actually_exists():
    """A misspelt name here would fail silently and forever.

    ``length_of`` answers 0 for a sound it cannot find, which means the line
    simply stops waiting -- the original bug back, with nothing raised and
    nothing logged at a level anyone reads. So the names are checked against
    the catalogue rather than trusted.
    """
    from fusionfire import app as app_module

    named: set[str] = set(ATTACK_SOUNDS.values())
    for module in (engine_module, app_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        named |= set(re.findall(r'after="([^"]+)"', source))

    assert named, "no status line names a sound; the wiring has gone"
    unknown = sorted(n for n in named if n not in CATALOGUE and n not in GROUPS)
    assert unknown == [], f"status lines wait for sounds that do not exist: {unknown}"


def test_every_weapon_has_a_sound_to_wait_for():
    """A weapon missing from the table announces itself over its own noise."""
    for side in (Side.PLAYER, Side.OPPONENT):
        for weapon in Weapon:
            assert (side, weapon) in ATTACK_SOUNDS, f"{side} {weapon} has no sound"

