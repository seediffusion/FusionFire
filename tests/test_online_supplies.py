"""Host-decided match supplies for online play.

The opponent used to start every online match with UNLIMITED bullets and
restores, so the other player could never run them dry. The host now picks
the amounts, carries them in its hello handshake, and the same finite pool
is applied to both sides. These tests check the protocol field, the engine
wiring, and that a host cannot quietly give itself more than its opponent.
"""

from __future__ import annotations

import pytest

from fusionfire.game.constants import Side
from fusionfire.game.difficulty import INTERMEDIATE
from fusionfire.game.engine import Combatant, Engine
from fusionfire.net.protocol import ProtocolError, decode, encode, validate


# ---------------------------------------------------------------------
# Protocol schema
def test_hello_accepts_optional_supplies():
    frame = encode(
        "hello",
        version=1,
        name="Ada",
        gender="female",
        bullets=7,
        restores=3,
    )
    # ``decode`` works on the frame body (the 4-byte length header is
    # stripped by the reader before it is handed the body).
    message = decode(frame[4:])
    assert message["bullets"] == 7
    assert message["restores"] == 3


def test_hello_without_supplies_is_still_valid():
    """The legacy/casual hello omits the fields; the field must be optional."""
    frame = encode("hello", version=1, name="Ada", gender="female")
    message = decode(frame[4:])
    assert "bullets" not in message
    assert "restores" not in message

    # A peer can also send null explicitly and get the same result: the
    # field is treated as absent and dropped, just like when it is omitted.
    message2 = validate(
        "hello", {"version": 1, "name": "Ada", "gender": "female", "bullets": None}
    )
    assert "bullets" not in message2


def test_hello_rejects_out_of_range_supplies():
    with pytest.raises(ProtocolError):
        encode("hello", version=1, name="Ada", gender="female", bullets=1000)
    with pytest.raises(ProtocolError):
        encode("hello", version=1, name="Ada", gender="female", restores=-1)


# ---------------------------------------------------------------------
# Engine wiring
def _online_engine(bullets: int = 10, restores: int = 10) -> Engine:
    return Engine(
        Combatant("Host", bullets=bullets, restores=restores),
        Combatant("Join"),
        INTERMEDIATE,
        online=True,
    )


def test_online_engine_starts_both_sides_finite():
    """No side gets UNLIMITED supplies in an online match."""
    engine = _online_engine(8, 4)
    assert engine.player.bullets == 8
    assert engine.player.restores == 4
    assert engine.opponent.bullets == 8
    assert engine.opponent.restores == 4
    assert not engine.player.unlimited_bullets
    assert not engine.opponent.unlimited_bullets
    assert not engine.player.unlimited_restores
    assert not engine.opponent.unlimited_restores


def test_apply_match_settings_overrides_both_sides():
    engine = _online_engine(10, 10)
    engine.apply_match_settings(5, 2)
    assert engine.player.bullets == 5
    assert engine.opponent.bullets == 5
    assert engine.player.restores == 2
    assert engine.opponent.restores == 2


def test_apply_match_settings_is_ignored_offline():
    """Offline matches keep their difficulty-driven supplies untouched."""
    engine = Engine(Combatant("You"), Combatant("Coward"), "coward")
    assert engine.opponent.unlimited_bullets
    engine.apply_match_settings(5, 5)
    # Offline, the call is a no-op: the opponent stays unlimited and the
    # player keeps the difficulty's (Coward's) starting stock, untouched by
    # the ignored online-only settings.
    assert engine.opponent.unlimited_bullets
    assert engine.player.unlimited_bullets


def test_apply_match_settings_cannot_be_cheated():
    """The protocol bounds the values, so a modified client cannot hand out
    a thousand bullets — but even at the engine level, negative or absurd
    numbers are not silently turned into unlimited."""
    engine = _online_engine(10, 10)
    engine.apply_match_settings(0, 0)
    assert engine.player.bullets == 0
    assert engine.opponent.bullets == 0
    # Zero is a legitimate, finite choice; it is not the UNLIMITED sentinel.
    assert not engine.player.unlimited_bullets


def test_online_finite_bullets_are_actually_spent():
    """The whole point: when both sides are finite, firing runs the ammo
    down for both, so neither side can shoot forever."""
    engine = _online_engine(2, 1)
    engine.start()
    engine.begin_play()
    engine.turn = Side.PLAYER

    engine.player.gun_loaded = True
    engine.fire_gun(Side.PLAYER)
    assert engine.player.bullets == 1

    engine.opponent.gun_loaded = True
    engine.fire_gun(Side.OPPONENT)
    assert engine.opponent.bullets == 1

    # Out of ammo is reported as such rather than granting free shots.
    engine.player.gun_loaded = True
    engine.player.bullets = 0
    engine.turn = Side.PLAYER
    log = engine.fire_gun(Side.PLAYER)
    text = " ".join(getattr(ev, "text", "") for ev in log)
    assert "out of bullets" in text.lower()
