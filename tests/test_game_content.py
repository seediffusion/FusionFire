"""Bonus round, names, greetings and the sound catalogue."""

from __future__ import annotations

from datetime import date

import pytest

from fusionfire import assets
from fusionfire.game import constants as K
from fusionfire.game import greetings, names
from fusionfire.game.bonus import BonusRound
from fusionfire.game.difficulty import COWARD, IMPOSSIBLE, INTERMEDIATE
from fusionfire.game.engine import Combatant


# ----------------------------------------------------------------------
# Bonus round
# ----------------------------------------------------------------------
def test_bonus_deals_one_payload_per_note():
    assert len(BonusRound(INTERMEDIATE).notes) == K.BONUS_NOTE_COUNT


def test_cursor_cannot_leave_the_octave():
    bonus = BonusRound(INTERMEDIATE)
    for _ in range(50):
        bonus.move(-1)
    assert bonus.cursor == 0
    for _ in range(50):
        bonus.move(1)
    assert bonus.cursor == K.BONUS_NOTE_COUNT - 1


def test_marking_a_note_twice_unmarks_it():
    bonus = BonusRound(INTERMEDIATE)
    assert bonus.toggle() is True
    assert bonus.toggle() is False
    assert bonus.marked == set()


def test_note_sound_tracks_the_cursor():
    bonus = BonusRound(INTERMEDIATE)
    assert bonus.note_sound == "note1"
    bonus.move(4)
    assert bonus.note_sound == "note5"


def test_only_marked_notes_produce_effects():
    bonus = BonusRound(INTERMEDIATE)
    bonus.cursor = 2
    bonus.toggle()
    bonus.cursor = 7
    bonus.toggle()
    result = bonus.finish()
    assert result.marked == [2, 7]
    assert len(result.effects) == 2


def test_marking_nothing_yields_nothing():
    result = BonusRound(INTERMEDIATE).finish()
    assert result.effects == []
    assert "nothing" in result.summary.lower()


def test_bomb_payloads_are_withheld_where_the_difficulty_forbids_them():
    """Impossible denies the player bombs; a bomb note must be inert there."""
    from fusionfire.game.bonus import _player_bombs

    player, opponent = Combatant(name="P"), Combatant(name="C")
    _player_bombs(1).apply(player, opponent, IMPOSSIBLE)
    assert player.bombs == 0

    _player_bombs(1).apply(player, opponent, INTERMEDIATE)
    assert player.bombs == 1


def test_bullet_payloads_do_not_disturb_unlimited_ammunition():
    from fusionfire.game.bonus import _player_bullets

    player = Combatant(name="P", bullets=-1)  # the unlimited sentinel
    _player_bullets(5).apply(player, Combatant(name="C"), COWARD)
    assert player.unlimited_bullets


def test_point_payloads_never_drive_a_score_negative():
    from fusionfire.game.bonus import _player_points

    player = Combatant(name="P", points=1)
    _player_points(-10).apply(player, Combatant(name="C"), INTERMEDIATE)
    assert player.points == 0


# ----------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------
def test_a_full_name_is_kept_as_typed():
    choice = names.decide("Ada Lovelace", "female")
    assert choice.kind == "full"
    assert choice.name == "Ada Lovelace"
    assert choice.gender == "female"


def test_a_single_word_earns_a_taunt_and_a_new_name():
    choice = names.decide("Ada", "female")
    assert choice.kind == "partial"
    assert choice.taunt
    assert choice.name != "Ada"
    assert len(choice.name.split()) == 2


def test_an_empty_name_is_randomised_quietly():
    choice = names.decide("", "male")
    assert choice.kind == "random"
    assert choice.taunt == ""
    assert len(choice.name.split()) == 2


def test_the_name_lists_actually_loaded():
    assert len(names.given_names("male")) > 200
    assert len(names.given_names("female")) > 200
    assert len(names.surnames()) > 400


def test_there_are_plenty_of_combinations():
    assert names.combinations("male") > 100_000
    assert names.combinations("female") > 100_000


def test_random_names_pick_a_gender_when_none_is_given():
    _, gender = names.random_name(None)
    assert gender in ("male", "female")


# ----------------------------------------------------------------------
# Calendar
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "year,expected",
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
    ],
)
def test_easter_dates_are_correct(year, expected):
    assert greetings.easter(year) == expected


def test_christmas_greeting_fires_on_christmas_day():
    greeting = greetings.for_today(today=date(2026, 12, 25))
    assert greeting is not None
    assert greeting.music == "christmas"


def test_birthday_greeting_fires_on_the_right_day():
    greeting = greetings.for_today("03-14", today=date(2026, 3, 14))
    assert greeting is not None
    assert greeting.music == "birthday"


def test_no_birthday_greeting_on_other_days():
    greeting = greetings.for_today("03-14", today=date(2026, 3, 15))
    assert greeting is None or greeting.music != "birthday"


def test_an_ordinary_day_gets_no_greeting():
    assert greetings.for_today(today=date(2026, 9, 9)) is None


def test_the_machine_is_grumpier_at_bedtime():
    from datetime import datetime

    night = greetings.refusal_chance(now=datetime(2026, 5, 1, 2, 0))
    afternoon = greetings.refusal_chance(now=datetime(2026, 5, 1, 15, 0))
    assert night > afternoon


# ----------------------------------------------------------------------
# Sound catalogue
# ----------------------------------------------------------------------
def test_every_catalogued_sound_exists_on_disk():
    missing = assets.verify()
    assert missing == [], f"missing sound files: {missing}"


def test_the_catalogue_covers_the_documented_sound_set():
    for name in (
        "usergun", "userwhip", "userload", "userrestore", "userdie",
        "computergun", "computerwhip", "computerload", "computerdie",
        "userweaponrun", "userweaponready", "userweaponattack",
        "userweaponhit", "userweaponpowerdown",
        "enterch", "exitch", "select", "error", "type",
        "itemclick", "itemclock", "itemtimeout",
    ):
        assert name in assets.CATALOGUE, f"{name} is missing from the catalogue"


def test_all_thirteen_notes_and_thirty_three_hits_are_present():
    assert len(assets.GROUPS["note"]) == 13
    assert len(assets.GROUPS["userhit"]) == 33
    assert len(assets.GROUPS["computerhitm"]) == 8
    assert len(assets.GROUPS["computerhitf"]) == 4


def test_reaction_group_follows_the_characters_gender():
    assert assets.hit_group("female") == "computerhitf"
    assert assets.hit_group("male") == "computerhitm"
    assert assets.laugh_group("female") == "laugh_f"


def test_typed_characters_map_to_recorded_speech():
    assert assets.speech_sound("a") == "say_a"
    assert assets.speech_sound("Z") == "say_z"
    assert assets.speech_sound("7") == "say_7"
    assert assets.speech_sound("!") is None
    assert assets.speech_sound(" ") is None


def test_an_unknown_sound_is_not_in_the_catalogue():
    """There is one sounds folder and one lookup now, so a name that is not
    catalogued simply has no path -- there is no second place to search."""
    assert "not_a_real_sound" not in assets.CATALOGUE
    assert "../../evil" not in assets.CATALOGUE
