"""Combat rules.

These lean on the fact that the engine is pure: no audio device, no screen
reader, no window. Where a test needs a specific dice roll it patches
:mod:`fusionfire.rng` rather than looping until the desired outcome shows up.
"""

from __future__ import annotations

import pytest

from fusionfire.game import constants as K
from fusionfire.game.constants import Outcome, Phase, Side, PowerWeaponState, Weapon
from fusionfire.game.difficulty import COWARD, IMPOSSIBLE, INTERMEDIATE, get
from fusionfire.game.engine import Combatant, Engine, Strike
from fusionfire.game.events import (
    GameOver,
    PlayMusic,
    PlaySound,
    StartAmbience,
    TurnChanged,
)


@pytest.fixture
def engine():
    player = Combatant(name="Ada Lovelace", gender="female")
    opponent = Combatant(name="Blue Screen")
    eng = Engine(player, opponent, INTERMEDIATE)
    eng.start(use_power_weapon=False, first=Side.PLAYER)
    # The opening plays computerstart before anyone may act; tests that
    # are about combat want the match already running.
    eng.begin_play()
    return eng


def _always_hit(monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)


def _always_miss(monkeypatch):
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: False)


def _fixed_roll(monkeypatch, value: int):
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: value)


# ----------------------------------------------------------------------
# Turn order
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# The opening
#
# computerstart.wav is the opponent machine booting, and it gets its ten
# seconds to itself. Dropping the score, the idle hum and the first turn on
# top of it turned a deliberate opening into a pile-up.
# ----------------------------------------------------------------------
def test_start_plays_the_boot_sound_and_nothing_else():
    eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE)
    events = eng.start(use_power_weapon=True, first=Side.PLAYER)

    sounds = [e.name for e in events if isinstance(e, PlaySound)]
    assert sounds == ["computerstart"]
    assert not any(isinstance(e, PlayMusic) for e in events), "music must wait"
    assert not any(isinstance(e, StartAmbience) for e in events), "the hum must wait"
    assert not any(isinstance(e, TurnChanged) for e in events), "the turn must wait"
    assert eng.phase is Phase.SETUP


def test_nobody_can_act_during_the_opening():
    eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE)
    eng.start(first=Side.PLAYER)
    before = eng.opponent.health

    eng.crack_whip(Side.PLAYER)
    assert eng.opponent.health == before, "the player acted over the intro"
    assert eng.opponent_move() == [], "the machine acted over the intro"


def test_begin_play_starts_the_score_the_hum_and_the_first_turn():
    eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE)
    eng.start(use_power_weapon=True, first=Side.PLAYER)
    events = eng.begin_play()

    assert eng.phase is Phase.PLAYING
    assert [e.name for e in events if isinstance(e, PlayMusic)] == ["level1"]
    ambience = {e.key for e in events if isinstance(e, StartAmbience)}
    assert ambience == {"machine", "power_weapon"}
    assert any(isinstance(e, TurnChanged) for e in events)
    assert eng.power_weapon.state is PowerWeaponState.CHARGING


def test_begin_play_is_idempotent():
    eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE)
    eng.start(first=Side.PLAYER)
    eng.begin_play()
    assert eng.begin_play() == [], "a second call must not restart the match"


def test_the_first_mover_chosen_at_start_is_the_one_that_plays():
    for side in (Side.PLAYER, Side.OPPONENT):
        eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE)
        eng.start(first=side)
        eng.begin_play()
        assert eng.turn is side


def test_start_sets_playing_phase_and_first_mover(engine):
    assert engine.phase is Phase.PLAYING
    assert engine.turn is Side.PLAYER


def test_attack_hands_the_turn_over(engine):
    engine.crack_whip(Side.PLAYER)
    assert engine.turn is Side.OPPONENT


def test_acting_out_of_turn_is_refused(engine):
    engine.turn = Side.OPPONENT
    before = engine.opponent.health
    engine.crack_whip(Side.PLAYER)
    assert engine.opponent.health == before
    assert engine.turn is Side.OPPONENT


def test_dry_fire_does_not_cost_the_turn(engine):
    assert not engine.player.gun_loaded
    engine.fire_gun(Side.PLAYER)
    assert engine.turn is Side.PLAYER, "an empty chamber should not end your go"


def test_loading_does_not_cost_the_turn(engine):
    """Reloading is the setup for a move, not a move."""
    engine.load_gun(Side.PLAYER)
    assert engine.player.gun_loaded
    assert engine.turn is Side.PLAYER


def test_you_can_load_and_then_fire_in_the_same_turn(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.load_gun(Side.PLAYER)
    before = engine.opponent.health
    engine.fire_gun(Side.PLAYER)
    assert engine.opponent.health < before
    assert engine.turn is Side.OPPONENT, "firing still ends the turn"


def test_loading_a_loaded_gun_is_refused_without_costing_the_turn(engine):
    engine.player.gun_loaded = True
    engine.load_gun(Side.PLAYER)
    assert engine.turn is Side.PLAYER


def test_the_opponent_reloads_and_still_attacks(engine, monkeypatch):
    """The machine must not lose its go to a reload either, or the player
    would get a free hit every time its gun ran dry."""
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    engine.turn = Side.OPPONENT
    engine.opponent.gun_loaded = False
    before = engine.player.health

    engine.opponent_move()

    assert engine.player.health < before, "the machine reloaded but never attacked"
    assert engine.turn is Side.PLAYER


# ----------------------------------------------------------------------
# Damage
# ----------------------------------------------------------------------
def test_gun_damage_stays_in_the_documented_range(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.player.gun_loaded = True
    before = engine.opponent.health
    engine.fire_gun(Side.PLAYER)
    dealt = before - engine.opponent.health
    assert K.GUN_DAMAGE[0] <= dealt <= K.GUN_DAMAGE[1]


def test_lash_damage_stays_in_the_documented_range(engine, monkeypatch):
    _always_hit(monkeypatch)
    before = engine.opponent.health
    engine.crack_whip(Side.PLAYER)
    dealt = before - engine.opponent.health
    assert K.LASH_DAMAGE[0] <= dealt <= K.LASH_DAMAGE[1]


def test_a_miss_deals_nothing(engine, monkeypatch):
    _always_miss(monkeypatch)
    before = engine.opponent.health
    engine.crack_whip(Side.PLAYER)
    assert engine.opponent.health == before


def test_a_landed_strike_scores_a_point(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.crack_whip(Side.PLAYER)
    assert engine.player.points == 1


def test_a_miss_scores_nothing(engine, monkeypatch):
    """Swinging at the air is not an achievement. Paying a point for one
    made the score a count of how long the match ran rather than of how well
    it was fought."""
    _always_miss(monkeypatch)
    engine.crack_whip(Side.PLAYER)
    assert engine.player.points == 0


# ----------------------------------------------------------------------
# Ammunition and healing
# ----------------------------------------------------------------------
def test_firing_spends_a_bullet_and_empties_the_chamber(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.player.gun_loaded = True
    before = engine.player.bullets
    engine.fire_gun(Side.PLAYER)
    assert engine.player.bullets == before - 1
    assert not engine.player.gun_loaded


def test_coward_grants_unlimited_supplies():
    eng = Engine(Combatant(name="P"), Combatant(name="C"), COWARD)
    assert eng.player.unlimited_bullets
    assert eng.player.unlimited_restores
    eng.player.spend_bullet()
    assert eng.player.unlimited_bullets, "unlimited must not decrement"


def test_restore_is_capped_at_full_health(engine, monkeypatch):
    _fixed_roll(monkeypatch, 35)
    engine.player.health = 90
    engine.restore_health(Side.PLAYER)
    assert engine.player.health == K.MAX_HEALTH


def test_restore_at_full_health_is_refused_without_costing_the_turn(engine):
    engine.player.health = K.MAX_HEALTH
    before = engine.player.restores
    engine.restore_health(Side.PLAYER)
    assert engine.player.restores == before
    assert engine.turn is Side.PLAYER


# ----------------------------------------------------------------------
# Winning and losing
# ----------------------------------------------------------------------
def test_reducing_the_opponent_to_zero_wins(engine, monkeypatch):
    _always_hit(monkeypatch)
    _fixed_roll(monkeypatch, 8)  # pin the roll; a 1 or 2 would leave them standing
    engine.opponent.health = 3
    events = engine.crack_whip(Side.PLAYER)
    assert engine.phase is Phase.FINISHED
    assert engine.winner is Side.PLAYER
    assert any(isinstance(e, GameOver) and e.winner is Side.PLAYER for e in events)


def test_negative_health_still_loses_the_match(engine, monkeypatch):
    """The readme is explicit: -13 health means the last striker wins."""
    _always_hit(monkeypatch)
    _fixed_roll(monkeypatch, 8)
    engine.turn = Side.OPPONENT
    engine.player.health = 5
    engine.crack_whip(Side.OPPONENT)
    assert engine.player.health < 0
    assert engine.winner is Side.OPPONENT


def test_no_turn_change_event_after_the_match_ends(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.opponent.health = 1
    events = engine.crack_whip(Side.PLAYER)
    assert not any(isinstance(e, TurnChanged) for e in events)


# ----------------------------------------------------------------------
# PowerWeapon
# ----------------------------------------------------------------------
def test_power_weapon_charges_then_becomes_ready():
    weapon = Engine(Combatant(name="P"), Combatant(name="C")).power_weapon
    weapon.begin(now=0.0)
    assert weapon.state is PowerWeaponState.CHARGING
    assert weapon.tick(now=K.POWER_WEAPON_CHARGE_TIME - 1) is None
    assert weapon.tick(now=K.POWER_WEAPON_CHARGE_TIME + 1) is PowerWeaponState.READY
    assert weapon.usable


def test_power_weapon_expires_after_its_window():
    weapon = Engine(Combatant(name="P"), Combatant(name="C")).power_weapon
    weapon.begin(now=0.0)
    weapon.tick(now=K.POWER_WEAPON_CHARGE_TIME + 1)
    past = K.POWER_WEAPON_CHARGE_TIME + K.POWER_WEAPON_WINDOW + 1
    assert weapon.tick(now=past) is PowerWeaponState.EXPIRED
    assert not weapon.usable


def test_firing_a_charging_power_weapon_is_refused(engine):
    engine.power_weapon.begin()
    before = engine.opponent.health
    engine.fire_power_weapon()
    assert engine.opponent.health == before
    assert engine.power_weapon.state is PowerWeaponState.CHARGING


def test_power_weapon_can_only_be_fired_once(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.power_weapon.begin()
    engine.power_weapon.state = PowerWeaponState.READY
    engine.fire_power_weapon()
    assert engine.power_weapon.state is PowerWeaponState.SPENT

    engine.phase = Phase.PLAYING
    engine.turn = Side.PLAYER
    before = engine.opponent.health
    engine.fire_power_weapon()
    assert engine.opponent.health == before


def test_launching_the_power_weapon_does_not_resolve_it_yet(engine):
    """The outcome is rolled when the drumroll ends, not when 6 is pressed,
    so the whole length of dr.wav is real suspense."""
    engine.power_weapon.begin()
    engine.power_weapon.state = PowerWeaponState.READY

    events, launched = engine.launch_power_weapon()
    assert launched
    assert [e.name for e in events if isinstance(e, PlayMusic)] == ["dr"]
    assert engine.opponent.health == K.START_HEALTH, "nothing may land yet"
    assert engine.turn is Side.PLAYER, "the turn ends when the shot lands"
    assert engine.power_weapon.state is PowerWeaponState.SPENT


def test_the_status_readout_is_just_the_status(engine):
    """Pressing 4 must report the player's own state. Regression: a second
    line about the power weapon's charge rode along on the status, and the
    two back-to-back utterances left only the power weapon line heard --
    until the weapon had been fired, when it vanished on its own."""
    from fusionfire.game.events import Say

    engine.power_weapon.begin()  # charging, the reported symptom

    lines = [e for e in engine.player_status() if isinstance(e, Say)]
    assert len(lines) == 1, f"status must be a single line, got {lines}"
    assert "health" in lines[0].text and "points" in lines[0].text
    assert "Power weapon" not in lines[0].text
    assert lines[0].interrupt is True, "the status must come through first"

    # Fired, spent, disabled -- still just the status.
    engine.power_weapon.state = PowerWeaponState.SPENT
    lines = [e for e in engine.player_status() if isinstance(e, Say)]
    assert len(lines) == 1


def test_resolving_the_power_weapon_lands_the_shot_and_ends_the_turn(engine, monkeypatch):
    _always_hit(monkeypatch)
    engine.power_weapon.begin()
    engine.power_weapon.state = PowerWeaponState.READY
    engine.launch_power_weapon()

    engine.resolve_power_weapon()
    assert engine.opponent.health < K.START_HEALTH
    assert engine.turn is Side.OPPONENT


def test_a_refused_launch_reports_no_launch(engine):
    engine.power_weapon.begin()  # still charging
    events, launched = engine.launch_power_weapon()
    assert not launched
    assert not any(isinstance(e, PlayMusic) for e in events)


def test_resolving_after_the_match_ended_does_nothing(engine):
    engine.power_weapon.begin()
    engine.power_weapon.state = PowerWeaponState.READY
    engine.launch_power_weapon()
    engine.phase = Phase.FINISHED

    before = engine.opponent.health
    assert engine.resolve_power_weapon() == []
    assert engine.opponent.health == before


def test_power_weapon_backfire_damages_the_firer(engine):
    strike = Strike(
        Side.PLAYER, Weapon.POWER_WEAPON, Outcome.BACKFIRE, damage=30, victim=Side.PLAYER
    )
    before = engine.player.health
    from fusionfire.game.events import EventLog

    engine._resolve(strike, EventLog())
    assert engine.player.health == before - 30
    assert engine.opponent.health == K.START_HEALTH


def test_power_weapon_damage_range_matches_the_documentation(engine):
    for _ in range(200):
        strike = engine._roll_power_weapon()
        if strike.outcome is not Outcome.MISS:
            assert K.POWER_WEAPON_DAMAGE[0] <= strike.damage <= K.POWER_WEAPON_DAMAGE[1]


# ----------------------------------------------------------------------
# Music
# ----------------------------------------------------------------------
def test_music_follows_the_players_health(engine):
    engine.player.health = 25  # below the 30% threshold
    events = engine._music_for_health()
    assert [e.name for e in events if isinstance(e, PlayMusic)] == ["level2"]

    engine.player.health = 10  # below 15%
    events = engine._music_for_health()
    assert [e.name for e in events if isinstance(e, PlayMusic)] == ["level3"]


def test_music_does_not_restart_while_the_level_is_unchanged(engine):
    engine.player.health = 25
    engine._music_for_health()
    assert engine._music_for_health() == []


def test_music_can_be_forced_back_on_after_being_silenced(engine):
    """Regression: the level cache used to make Enter a one-way switch.

    Turning the score off and on again returned no event, because the health
    band had not moved, so the music stayed silent for the rest of the match.
    """
    engine.player.health = 25
    engine._music_for_health()
    assert engine._music_for_health() == [], "unchanged level should stay quiet"

    events = engine.resume_music()
    assert [e.name for e in events if isinstance(e, PlayMusic)] == ["level2"]


def test_online_matches_use_the_online_score():
    eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE, online=True)
    events = eng._music_for_health()
    assert [e.name for e in events if isinstance(e, PlayMusic)] == ["level1o"]


# ----------------------------------------------------------------------
# Difficulty
# ----------------------------------------------------------------------
def test_impossible_denies_the_player_bombs_and_cheats():
    assert not IMPOSSIBLE.player_gets_bombs
    assert not IMPOSSIBLE.cheats_allowed


def test_unknown_difficulty_falls_back_to_intermediate():
    assert get("nonsense") is INTERMEDIATE


def test_opponent_heals_when_hurt_on_impossible(monkeypatch):
    eng = Engine(Combatant(name="P"), Combatant(name="C"), IMPOSSIBLE)
    eng.start(first=Side.OPPONENT)
    eng.begin_play()
    eng.opponent.health = 40
    eng.opponent.bombs = 0
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: pct >= 100.0)
    before = eng.opponent.health
    eng.opponent_move()
    assert eng.opponent.health > before


# ----------------------------------------------------------------------
# Bonus hand-off
# ----------------------------------------------------------------------
def test_bonus_cannot_spawn_during_its_cooldown(engine):
    engine.rounds_since_bonus = 0
    assert not engine.should_spawn_bonus()


def test_an_online_match_can_spawn_a_bonus(monkeypatch):
    """It used to refuse outright. Both players get one now, so the rule the
    engine holds is the same as offline -- who is allowed to roll is the
    panel's business, because only it knows which end is the host."""
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    eng = Engine(Combatant(name="P"), Combatant(name="C"), INTERMEDIATE, online=True)
    eng.start()
    eng.begin_play()
    eng.rounds_since_bonus = 99
    assert eng.should_spawn_bonus()


def _online_pair():
    """Two engines of the same match, each seeing it from its own end."""
    host = Engine(
        Combatant(name="Host", bullets=10, restores=10),
        Combatant(name="Joiner", bullets=10, restores=10),
        INTERMEDIATE,
        online=True,
    )
    joiner = Engine(
        Combatant(name="Joiner", bullets=10, restores=10),
        Combatant(name="Host", bullets=10, restores=10),
        INTERMEDIATE,
        online=True,
    )
    for eng, first in ((host, Side.PLAYER), (joiner, Side.OPPONENT)):
        eng.start(first=first)
        eng.begin_play()
    return host, joiner


def _state(engine):
    p, o = engine.player, engine.opponent
    return (
        p.health, p.points, p.bullets, p.restores, p.bombs,
        o.health, o.points, o.bullets, o.restores, o.bombs,
    )


def test_a_peer_bonus_lands_on_the_right_side_of_the_match():
    """Their notes moved them; their notes also moved us. Getting the two
    the wrong way round would heal whoever was supposed to be hurt."""
    host, joiner = _online_pair()
    joiner.player.health = 60
    joiner.opponent.health = 80

    joiner.apply_peer_bonus(
        {"health": 15, "points": 2, "bullets": -1, "restores": 0, "bombs": 1,
         "foe_health": -10, "foe_points": 0, "foe_bombs": 0}
    )

    # "health" is what the sender gained, and the sender is our opponent.
    assert joiner.opponent.health == 95
    assert joiner.opponent.points == 2
    assert joiner.opponent.bullets == 9
    assert joiner.opponent.bombs == 1
    # "foe_" is what the sender's notes did to us.
    assert joiner.player.health == 50


def test_two_engines_still_agree_after_a_bonus_each():
    """The whole point of exchanging the numbers. Each side plays its own
    thirteen notes; afterwards each side's view has to be the other's,
    mirrored."""
    from fusionfire.game.bonus import BonusRound, deltas, snapshot

    host, joiner = _online_pair()

    def play(engine, marks):
        round_ = BonusRound(engine.difficulty, engine.opponent.name)
        for index in marks:
            round_.cursor = index
            round_.toggle()
        before_me = snapshot(engine.player)
        before_them = snapshot(engine.opponent)
        engine.apply_bonus(round_.finish())
        mine = deltas(before_me, snapshot(engine.player))
        theirs = deltas(before_them, snapshot(engine.opponent))
        return {
            "health": mine["health"], "points": mine["points"],
            "bullets": mine["bullets"], "restores": mine["restores"],
            "bombs": mine["bombs"], "foe_health": theirs["health"],
            "foe_points": theirs["points"], "foe_bombs": theirs["bombs"],
        }

    from_host = play(host, [0, 3, 7])
    from_joiner = play(joiner, [1, 5])
    joiner.apply_peer_bonus(from_host)
    host.apply_peer_bonus(from_joiner)

    mine = _state(host)
    theirs = _state(joiner)
    assert mine == theirs[5:] + theirs[:5], (
        f"the two ends disagree after a bonus round: {mine} against {theirs}"
    )


def test_a_bonus_does_not_move_the_turn():
    """Which is what makes it safe online: it is a parenthesis in the match,
    not a move, so neither end can come out of it thinking it is their go."""
    from fusionfire.game.bonus import BonusRound

    host, _joiner = _online_pair()
    host.turn = Side.OPPONENT
    host.apply_bonus(BonusRound(host.difficulty).finish())
    assert host.turn is Side.OPPONENT

    host.apply_peer_bonus(
        {"health": 5, "points": 0, "bullets": 0, "restores": 0, "bombs": 0,
         "foe_health": 0, "foe_points": 0, "foe_bombs": 0}
    )
    assert host.turn is Side.OPPONENT


def test_a_peer_bonus_is_described_from_this_end():
    """A summary written where the notes were picked would arrive in the
    wrong person: their "you gain 12 health" reaches a player who gained
    nothing. So the words are built from the numbers on the reading side."""
    host, _joiner = _online_pair()
    events = host.apply_peer_bonus(
        {"health": 12, "points": 0, "bullets": 0, "restores": 0, "bombs": 1,
         "foe_health": -9, "foe_points": 0, "foe_bombs": 0}
    )
    from fusionfire.game.events import BonusFinished

    said = [e.summary for e in events if isinstance(e, BonusFinished)]
    assert said, "the peer's bonus was applied silently"
    text = said[0]
    assert "Joiner gains 12 health" in text, text
    assert "you lose 9 health" in text, text
    assert "gains 1 bomb;" in text or "gains 1 bomb" in text, text
    assert "1 bombs" not in text, f"pluralised a single bomb: {text}"


def test_a_peer_who_marked_nothing_says_so():
    host, _joiner = _online_pair()
    from fusionfire.game.events import BonusFinished

    events = host.apply_peer_bonus(
        {"health": 0, "points": 0, "bullets": 0, "restores": 0, "bombs": 0,
         "foe_health": 0, "foe_points": 0, "foe_bombs": 0}
    )
    said = [e.summary for e in events if isinstance(e, BonusFinished)]
    assert said == ["Joiner marked nothing."], said

def test_loading_the_gun_emits_no_speech(engine):
    from fusionfire.game.events import Say

    events = engine.load_gun(Side.PLAYER)
    assert not [e for e in events if isinstance(e, Say)]


def test_launching_the_power_weapon_emits_no_speech(engine):
    from fusionfire.game.events import Say

    engine.power_weapon.begin()
    engine.power_weapon.state = PowerWeaponState.READY
    events, launched = engine.launch_power_weapon()
    assert launched
    assert not [e for e in events if isinstance(e, Say)]


# ----------------------------------------------------------------------
# Grammar
#
# The two halves of a status line have to agree. "You shoot and missed"
# put a present-tense verb beside a past-tense one, and "Blue Screen lashes
# and hit" dropped the agreement the first verb had established. These lines
# are the game's primary output; they should read like English.
# ----------------------------------------------------------------------
def _resolved_line(engine, side, weapon, outcome, damage=12):
    from fusionfire.game.events import EventLog, Say

    engine.player.health = engine.opponent.health = 100
    log = EventLog()
    engine._resolve(Strike(side, weapon, outcome, damage), log)
    return next(e.text for e in log.events if isinstance(e, Say) and e.text)


@pytest.mark.parametrize(
    "weapon,expected",
    [
        (Weapon.GUN, "You shoot and miss."),
        (Weapon.WHIP, "You lash and miss."),
        (Weapon.BOMB, "You throw a bomb and miss."),
        (Weapon.POWER_WEAPON, "You fire the power weapon and miss."),
    ],
)
def test_your_misses_read_in_the_first_person(engine, weapon, expected):
    assert _resolved_line(engine, Side.PLAYER, weapon, Outcome.MISS) == expected


@pytest.mark.parametrize(
    "weapon,opening",
    [
        (Weapon.GUN, "Blue Screen shoots and misses."),
        (Weapon.WHIP, "Blue Screen lashes and misses."),
        (Weapon.BOMB, "Blue Screen throws a bomb and misses."),
    ],
)
def test_the_machines_misses_agree_with_the_machine(engine, weapon, opening):
    assert _resolved_line(engine, Side.OPPONENT, weapon, Outcome.MISS) == opening


def test_your_hits_read_in_the_first_person(engine):
    line = _resolved_line(engine, Side.PLAYER, Weapon.GUN, Outcome.HIT)
    assert line.startswith("You shoot and hit for 12.")


def test_the_machines_hits_agree_with_the_machine(engine):
    line = _resolved_line(engine, Side.OPPONENT, Weapon.WHIP, Outcome.HIT)
    assert line.startswith("Blue Screen lashes and hits for 12.")


def test_no_status_line_mixes_its_tenses(engine):
    """A blanket check, so a new weapon cannot reintroduce the mismatch."""
    for side in (Side.PLAYER, Side.OPPONENT):
        for weapon in Weapon:
            for outcome in (Outcome.MISS, Outcome.HIT):
                line = _resolved_line(engine, side, weapon, outcome)
                assert "and missed" not in line, f"past tense in {line!r}"
                assert " and hit for" not in line or side is Side.PLAYER, (
                    f"the machine should hit*s*: {line!r}"
                )
                if side is Side.OPPONENT:
                    assert "misses" in line or "hits for" in line, (
                        f"no third-person agreement in {line!r}"
                    )


# ----------------------------------------------------------------------
# The bonus round suspends the match
# ----------------------------------------------------------------------
def test_nobody_can_attack_during_a_bonus_round(engine):
    """The window is modal, but wx keeps pumping timers behind it."""
    assert engine.enter_bonus()
    assert engine.phase is Phase.BONUS

    before = (engine.player.health, engine.opponent.health)
    engine.crack_whip(Side.PLAYER)
    engine.turn = Side.OPPONENT
    engine.crack_whip(Side.OPPONENT)
    assert engine.opponent_move() == []
    assert (engine.player.health, engine.opponent.health) == before


def test_the_power_weapon_cannot_be_fired_during_a_bonus(engine):
    engine.power_weapon.begin()
    engine.power_weapon.state = PowerWeaponState.READY
    engine.enter_bonus()

    _events, launched = engine.launch_power_weapon()
    assert not launched
    assert engine.resolve_power_weapon() == []


def test_leaving_the_bonus_resumes_the_match(engine):
    engine.enter_bonus()
    engine.leave_bonus()
    assert engine.phase is Phase.PLAYING
    engine.crack_whip(Side.PLAYER)
    assert engine.turn is Side.OPPONENT


def test_a_bonus_cannot_start_outside_a_running_match(engine):
    engine.phase = Phase.FINISHED
    assert not engine.enter_bonus()
    assert engine.phase is Phase.FINISHED


def test_a_bonus_that_ends_the_match_stays_finished(engine):
    """A payload can take the last of someone's health."""
    from fusionfire.game.bonus import BonusResult, Effect

    engine.enter_bonus()
    engine.leave_bonus()
    engine.opponent.health = 5
    lethal = Effect("The machine loses 40 health",
                    lambda p, o, d: setattr(o, "health", o.health - 40))
    engine.apply_bonus(BonusResult(marked=[0], effects=[lethal], summary="done"))
    assert engine.phase is Phase.FINISHED
