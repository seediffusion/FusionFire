"""The hardening.

Each test here corresponds to something the original did loosely: paths
assembled from user-supplied names, settings that were trusted on load,
network frames parsed without validation, cheat codes matched by prefix.
"""

from __future__ import annotations

import json

import pytest

from fusionfire import paths
from fusionfire.config import SETTINGS_VERSION, Settings, Stats, sanitise_name
from fusionfire.game import cheats
from fusionfire.game.difficulty import IMPOSSIBLE, INTERMEDIATE
from fusionfire.game.engine import Combatant
from fusionfire.net import protocol
from fusionfire.net.protocol import ProtocolError
from fusionfire.net.session import derive_psk, generate_passphrase


# ----------------------------------------------------------------------
# Path containment
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "attempt",
    [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "sub/../../escape.wav",
        "/absolute/path.wav",
        "\\\\server\\share\\file.wav",
    ],
)
def test_traversal_attempts_are_refused(tmp_path, attempt):
    with pytest.raises(paths.PathEscapeError):
        paths.contained(tmp_path, attempt)


def test_ordinary_relative_paths_are_allowed(tmp_path):
    resolved = paths.contained(tmp_path, "sfx/usergun.wav")
    assert resolved == (tmp_path / "sfx" / "usergun.wav").resolve()


# ----------------------------------------------------------------------
# One sounds folder, and nothing escapes it
# ----------------------------------------------------------------------
def test_the_sounds_folder_sits_with_the_game():
    from fusionfire import paths as p

    root = p.sounds_dir()
    assert root.is_dir(), f"no sounds folder at {root}"
    assert p.data_dir() not in root.parents, (
        "sounds belong with the game, not buried in the user's data folder"
    )


def test_a_replaced_file_is_simply_the_sound(tmp_path, monkeypatch):
    """Replacing a sound means replacing the file. No shadowing, no second
    folder, nothing hidden underneath."""
    from fusionfire import assets

    monkeypatch.setattr("fusionfire.paths.game_dir", lambda: tmp_path)
    target = tmp_path / "sounds" / "sfx"
    target.mkdir(parents=True)
    replacement = target / "usergun.wav"
    replacement.write_bytes(b"RIFF")

    assert assets.CATALOGUE["usergun"].resolve() == replacement.resolve()


def test_every_catalogued_path_stays_inside_the_sounds_folder(tmp_path, monkeypatch):
    """The check that makes replacing sounds safe."""
    from fusionfire import assets

    monkeypatch.setattr("fusionfire.paths.game_dir", lambda: tmp_path)
    root = tmp_path / "sounds"
    root.mkdir()
    for ref in assets.CATALOGUE.values():
        resolved = paths.contained(root, ref.relpath)
        assert root.resolve() in resolved.parents


# ----------------------------------------------------------------------
# Settings validation
# ----------------------------------------------------------------------
def test_out_of_range_settings_are_clamped_on_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "sound_volume": 1e9,
                "music_volume": -50,
                "output_device_name": 12345,  # not even a string
                "last_port": 70000,
                "gamepad_deadzone": 40.0,
                "difficulty": "godmode",
                "player_gender": "unspecified",
            }
        ),
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert settings.sound_volume == 1.0
    assert settings.music_volume == 0.0
    assert settings.output_device_name == ""
    assert settings.last_port == 6000
    assert settings.gamepad_deadzone <= 0.95
    assert settings.difficulty == "intermediate"
    assert settings.player_gender == "male"


def test_unknown_settings_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"__class__": "evil", "save": "clobbered", "sound_volume": 0.5}),
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert settings.sound_volume == 0.5
    assert callable(settings.save), "a stray key must not overwrite a method"


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("this is not json {{{", encoding="utf-8")
    settings = Settings.load(path)
    assert settings.difficulty == "intermediate"


def test_settings_file_that_is_a_list_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert Settings.load(path).sound_volume == 0.85


def test_saving_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.save(path)
    settings.save(path)
    assert path.is_file()
    assert list(tmp_path.glob(".tmp-*")) == []
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == SETTINGS_VERSION


def test_negative_statistics_are_floored_at_zero(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text(json.dumps({"games_won": -5, "shots_fired": 10}), encoding="utf-8")
    stats = Stats.load(path)
    assert stats.games_won == 0
    assert stats.shots_fired == 10


def test_birthday_keeps_only_month_and_day():
    settings = Settings(birthday="1985-03-14")
    settings.normalise()
    assert settings.birthday == "", "a full date must not be accepted"

    settings = Settings(birthday="03-14")
    settings.normalise()
    assert settings.birthday == "03-14"


# ----------------------------------------------------------------------
# Name sanitising
# ----------------------------------------------------------------------
def test_control_characters_are_stripped_from_names():
    assert sanitise_name("Ada\x00\x07Lovelace") == "AdaLovelace"
    assert "\n" not in sanitise_name("line\nbreak")


def test_bidi_overrides_are_stripped_from_names():
    assert sanitise_name("Ada‮ecivreS") == "AdaecivreS"


def test_names_are_length_capped():
    assert len(sanitise_name("A" * 500)) == 40


def test_whitespace_is_collapsed():
    assert sanitise_name("  Ada   Lovelace  ") == "Ada Lovelace"


# ----------------------------------------------------------------------
# Protocol validation
# ----------------------------------------------------------------------
def test_a_valid_strike_round_trips():
    frame = protocol.encode("strike", weapon="gun", outcome="hit", damage=12)
    body = frame[protocol.HEADER_SIZE:]
    assert protocol.decode(body) == {
        "type": "strike", "weapon": "gun", "outcome": "hit", "damage": 12,
    }


def test_damage_beyond_the_rules_is_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(
            json.dumps({"type": "strike", "weapon": "gun", "outcome": "hit",
                        "damage": 99999}).encode()
        )


def test_negative_damage_is_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(
            json.dumps({"type": "strike", "weapon": "gun", "outcome": "hit",
                        "damage": -40}).encode()
        )


def test_unknown_message_types_are_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(json.dumps({"type": "exec", "code": "rm -rf /"}).encode())


def test_unexpected_fields_are_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(
            json.dumps({"type": "load", "surprise": "payload"}).encode()
        )


def test_missing_fields_are_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(json.dumps({"type": "strike", "weapon": "gun"}).encode())


def test_oversized_length_header_is_refused_before_allocation():
    header = (2**31).to_bytes(4, "big")
    with pytest.raises(ProtocolError):
        protocol.read_length(header)


def test_zero_length_frame_is_refused():
    with pytest.raises(ProtocolError):
        protocol.read_length((0).to_bytes(4, "big"))


def test_non_object_frames_are_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(b'"just a string"')
    with pytest.raises(ProtocolError):
        protocol.decode(b"[1,2,3]")


def test_malformed_json_is_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode(b"{not json")


def test_chat_is_length_capped():
    long_text = "x" * 5000
    frame = protocol.encode("chat", text=long_text)
    decoded = protocol.decode(frame[protocol.HEADER_SIZE:])
    assert len(decoded["text"]) <= protocol.MAX_CHAT_LENGTH


def test_chat_strips_control_characters():
    decoded = protocol.decode(
        json.dumps({"type": "chat", "text": "hello\x00\x1bworld"}).encode()
    )
    assert decoded["text"] == "helloworld"


def test_hello_rejects_an_unknown_gender():
    with pytest.raises(ProtocolError):
        protocol.decode(
            json.dumps({"type": "hello", "version": 1, "name": "A B",
                        "gender": "../../etc"}).encode()
        )


def test_booleans_are_not_accepted_as_integers():
    with pytest.raises(ProtocolError):
        protocol.decode(
            json.dumps({"type": "heal", "amount": True}).encode()
        )


# ----------------------------------------------------------------------
# Key derivation
# ----------------------------------------------------------------------
def test_psk_derivation_is_deterministic_and_full_length():
    key = derive_psk("correct horse battery staple")
    assert key == derive_psk("correct horse battery staple")
    assert len(key) == 32


def test_different_passphrases_derive_different_keys():
    assert derive_psk("passphrase one") != derive_psk("passphrase two")


def test_empty_passphrase_is_refused():
    with pytest.raises(ValueError):
        derive_psk("")


def test_generated_passphrases_are_long_and_unique():
    first, second = generate_passphrase(), generate_passphrase()
    assert first != second
    assert len(first) >= 20


# ----------------------------------------------------------------------
# Cheat parsing
# ----------------------------------------------------------------------
def test_a_valid_cheat_parses():
    assert cheats.parse("15 bullets") == ("bullets", 15)


@pytest.mark.parametrize(
    "attempt",
    [
        "__import__('os').system('calc')",
        "15 bullets; rm -rf /",
        "bullets",
        "15",
        "-5 bullets",
        "15 nonexistent",
        "99999999999999999999 bullets",
        "15 bullets\n15 bombs",
        "",
    ],
)
def test_malformed_cheats_are_refused(attempt):
    assert cheats.parse(attempt) is None


def test_cheat_quantities_are_clamped_to_the_table_maximum():
    player = Combatant(name="P", bullets=0)
    opponent = Combatant(name="C")
    result = cheats.apply("999999 bullets", player, opponent, INTERMEDIATE)
    assert result.ok
    assert player.bullets == cheats.CHEATS["bullets"].max_quantity


def test_cheats_are_refused_on_impossible():
    player, opponent = Combatant(name="P"), Combatant(name="C")
    before = player.bullets
    result = cheats.apply("10 bullets", player, opponent, IMPOSSIBLE)
    assert not result.ok
    assert player.bullets == before, "the cheat must not have run"


@pytest.mark.parametrize(
    "code",
    ["10 bullets", "50 health", "60 machinedamage", "500 points", "5 bombs"],
)
def test_no_cheat_runs_in_an_online_match(code):
    """Not one of them, whatever the difficulty happens to allow.

    Cheating a person rather than a machine is the reason. That they would
    not even work is the other one: every cheat writes to this end's engine
    and nothing goes on the wire, so the opponent's copy of the match would
    never hear about the health gained or the damage dealt and the two would
    stop agreeing about the fight.
    """
    player = Combatant(name="P", health=50, bullets=0, restores=0, bombs=0, points=0)
    opponent = Combatant(name="O", health=80)
    before = (player.health, player.bullets, player.restores, player.bombs,
              player.points, opponent.health)

    result = cheats.apply(code, player, opponent, INTERMEDIATE, online=True)

    assert not result.ok
    assert "online" in result.message.lower(), result.message
    assert (player.health, player.bullets, player.restores, player.bombs,
            player.points, opponent.health) == before, "a cheat ran online"


def test_cheats_still_run_offline():
    """The guard must refuse online matches, not every match."""
    player = Combatant(name="P", bullets=0)
    result = cheats.apply("10 bullets", player, Combatant(name="C"), INTERMEDIATE)
    assert result.ok
    assert player.bullets == 10


def test_the_unlock_file_says_cheats_are_off_online(tmp_path, monkeypatch):
    """The codes are handed over in a file, so the file has to carry the
    rule -- otherwise it is only discoverable by pressing C and being told."""
    monkeypatch.setattr(paths, "cheats_file", lambda: tmp_path / "cheats.txt")
    cheats.write_cheat_file()
    assert "online" in (tmp_path / "cheats.txt").read_text(encoding="utf-8").lower()


def test_health_cheat_cannot_exceed_the_maximum():
    player = Combatant(name="P", health=90)
    cheats.apply("100 health", player, Combatant(name="C"), INTERMEDIATE)
    assert player.health == 100


def test_overlong_cheat_input_is_refused():
    assert cheats.parse("1 " + "a" * 500) is None


# ----------------------------------------------------------------------
# Cheat unlocking
# ----------------------------------------------------------------------
@pytest.fixture
def clean_data_dir(tmp_path, monkeypatch):
    """Point the data directory at a throwaway folder.

    Without this the unlock tests would read whatever cheats.txt happens to
    exist on the developer's own machine.
    """
    monkeypatch.setattr("fusionfire.paths.data_dir", lambda: tmp_path)
    return tmp_path


def test_cheats_are_locked_with_no_file_and_no_score(clean_data_dir):
    assert not cheats.already_unlocked()
    assert not cheats.unlocked(0)
    assert not cheats.unlocked(29)


def test_thirty_points_earns_the_unlock(clean_data_dir):
    assert cheats.earned(30)
    assert cheats.unlocked(30)


def test_an_existing_cheats_file_unlocks_regardless_of_score(clean_data_dir):
    """The 30 points buy the codes permanently, not for a single match."""
    (clean_data_dir / "cheats.txt").write_text("earned earlier", encoding="utf-8")
    assert cheats.already_unlocked()
    assert cheats.unlocked(0), "a previous session's unlock must still count"


def test_writing_the_cheat_file_makes_it_unlock_next_time(clean_data_dir):
    assert not cheats.unlocked(0)
    path = cheats.write_cheat_file()
    assert path
    assert cheats.unlocked(0)


def test_the_cheat_file_lists_every_code(clean_data_dir):
    cheats.write_cheat_file()
    written = (clean_data_dir / "cheats.txt").read_text(encoding="utf-8")
    for name in cheats.CHEATS:
        assert name in written
