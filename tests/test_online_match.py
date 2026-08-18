"""Two players, two engines, one real socket.

This is the test that was missing, and its absence is exactly why online
play shipped broken. ``test_net.py`` proved the transport carried a frame;
``test_app_smoke.py`` proved a match worked offline. Nothing ever put the
two halves together, so the fact that combat actions were never *sent* went
unnoticed: the receiving code existed and was correct, and there was simply
nothing calling the sender.

The failure it produced is worth stating, because it is the one a player
reported. You fire; your own engine resolves the shot and hands the turn
over. The other end is told nothing, so it still believes the turn is yours.
Both engines then sit waiting for the other to move, and the match is
deadlocked with no error anywhere.

So these tests drive two real engines through a real loopback connection and
assert the two agree afterwards. Anything that fails to cross the wire shows
up as the two sides disagreeing about whose turn it is.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from fusionfire.game.constants import Phase, Side
from fusionfire.game.difficulty import INTERMEDIATE
from fusionfire.game.engine import Combatant, Engine
from fusionfire.net.session import HostSession, JoinSession, generate_passphrase


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Peer:
    """One player: an engine, a connection, and the glue the panel provides.

    Mirrors what ``GamePanel`` and ``AppContext`` do between them, without
    needing wx: send what our engine resolved, apply what theirs did.
    """

    def __init__(self, name: str, opponent_name: str) -> None:
        self.engine = Engine(
            Combatant(name=name),
            Combatant(name=opponent_name),
            INTERMEDIATE,
            online=True,
        )
        self.net = None
        self.received: list[dict] = []
        self.arrived = threading.Event()
        self.sent: list[tuple[str, dict]] = []

    def start(self, first: Side) -> None:
        self.engine.start(first=first)
        self.engine.begin_play()

    # -- sending, as the game panel does --------------------------------
    def send(self, kind: str, **fields) -> None:
        self.sent.append((kind, fields))
        if self.net is not None:
            self.net.send(kind, **fields)

    def fire(self) -> None:
        before = self.engine.opponent.health
        self.engine.player.gun_loaded = True
        events = self.engine.fire_gun(Side.PLAYER)
        self._report_strike(events, before)

    def whip(self) -> None:
        before = self.engine.opponent.health
        events = self.engine.crack_whip(Side.PLAYER)
        self._report_strike(events, before)

    def load(self) -> None:
        before = self.engine.player.gun_loaded
        self.engine.load_gun(Side.PLAYER)
        if self.engine.player.gun_loaded and not before:
            self.send("load")

    def heal(self) -> None:
        before = self.engine.player.health
        self.engine.restore_health(Side.PLAYER)
        gained = self.engine.player.health - before
        if gained > 0:
            self.send("heal", amount=int(gained))

    def _report_strike(self, events, health_before: int) -> None:
        from fusionfire.game.events import StrikeResolved

        for event in events:
            if isinstance(event, StrikeResolved) and event.attacker is Side.PLAYER:
                if event.weapon in ("gun", "whip", "bomb") and event.outcome in (
                    "hit", "miss",
                ):
                    self.send(
                        "strike",
                        weapon=event.weapon,
                        outcome=event.outcome,
                        damage=int(event.damage),
                    )

    # -- receiving, as AppContext._apply_remote_move does ---------------
    def apply(self, message: dict) -> None:
        from fusionfire.game.constants import Outcome, Weapon
        from fusionfire.game.engine import Strike
        from fusionfire.game.events import EventLog

        kind = message["type"]
        log = EventLog()
        if kind == "strike":
            strike = Strike(
                Side.OPPONENT,
                Weapon(message["weapon"]),
                Outcome(message["outcome"]),
                message["damage"],
            )
            self.engine._resolve(strike, log)
            self.engine._end_turn(log)
        elif kind == "heal":
            self.engine.opponent.heal(message["amount"])
            self.engine._end_turn(log)
        elif kind == "load":
            # Free action: must not end the turn here either.
            self.engine.opponent.gun_loaded = True

    def on_message(self, message: dict) -> None:
        self.received.append(message)
        self.apply(message)
        self.arrived.set()

    def wait(self, timeout: float = 10.0) -> bool:
        got = self.arrived.wait(timeout)
        self.arrived.clear()
        return got


@pytest.fixture
def table():
    """Two connected peers, ready to fight. Host moves first."""
    passphrase = generate_passphrase()
    port = _free_port()

    host = Peer("Ada Lovelace", "Alan Turing")
    join = Peer("Alan Turing", "Ada Lovelace")
    ready = threading.Event()

    host.net = HostSession(
        on_message=host.on_message,
        on_connected=ready.set,
        on_disconnected=lambda reason: None,
    )
    join.net = JoinSession(
        on_message=join.on_message,
        on_connected=lambda: None,
        on_disconnected=lambda reason: None,
    )

    host.net.listen(port=port, passphrase=passphrase)
    time.sleep(0.2)
    join.net.connect("127.0.0.1", port, passphrase)
    assert ready.wait(20), "the two never connected"

    # The host moves first, which is how the real game decides it.
    host.start(Side.PLAYER)
    join.start(Side.OPPONENT)
    try:
        yield host, join
    finally:
        host.net.close()
        join.net.close()


def _agree_on_turn(a: Peer, b: Peer) -> bool:
    """The two engines describe the same turn from opposite sides."""
    return a.engine.turn is not b.engine.turn


# ----------------------------------------------------------------------
def test_they_start_agreeing_about_whose_turn_it_is(table):
    host, join = table
    assert host.engine.turn is Side.PLAYER
    assert join.engine.turn is Side.OPPONENT
    assert _agree_on_turn(host, join)


def test_an_attack_reaches_the_other_player(table):
    """The reported bug: the shot landed locally and they heard nothing."""
    host, join = table
    before = join.engine.player.health

    host.fire()
    assert join.wait(), "the attack never arrived"

    assert join.received, "nothing was received"
    assert join.received[-1]["type"] == "strike"
    if join.received[-1]["outcome"] == "hit":
        assert join.engine.player.health < before


def test_the_turn_passes_to_the_other_player(table):
    """The second half of the bug: both sides waited for the other."""
    host, join = table

    host.fire()
    assert join.wait()

    assert host.engine.turn is Side.OPPONENT, "the attacker still has the turn"
    assert join.engine.turn is Side.PLAYER, "the turn never arrived"
    assert _agree_on_turn(host, join), "the two disagree about whose go it is"


def test_a_whole_exchange_stays_in_step(table):
    """Several turns each way. A desync compounds, so play it out."""
    host, join = table
    attacker, defender = host, join

    for _ in range(8):
        assert attacker.engine.turn is Side.PLAYER, "acting out of turn"
        attacker.fire()
        assert defender.wait(), "a move went missing"
        assert _agree_on_turn(host, join), "the two fell out of step"
        if host.engine.phase is not Phase.PLAYING:
            break
        attacker, defender = defender, attacker


def test_the_damage_matches_on_both_sides(table):
    host, join = table

    for _ in range(6):
        if host.engine.phase is not Phase.PLAYING:
            break
        if host.engine.turn is Side.PLAYER:
            host.whip()
            assert join.wait()
        else:
            join.whip()
            assert host.wait()

        # Each engine holds the same two healths, named from its own side.
        assert host.engine.player.health == join.engine.opponent.health
        assert host.engine.opponent.health == join.engine.player.health


def test_healing_crosses_and_ends_the_turn(table, monkeypatch):
    """The damage has to arrive the normal way first.

    ``heal`` carries how much was restored, not the resulting health, so it
    only keeps the two in step if they already agreed on the starting
    figure. Setting one side's health directly would manufacture the very
    desync the test is supposed to detect.
    """
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: hi)

    host, join = table

    host.fire()              # host attacks, turn passes
    assert join.wait()
    join.fire()              # join wounds the host, turn comes back
    assert host.wait()
    assert host.engine.player.health < 100
    assert host.engine.player.health == join.engine.opponent.health

    host.heal()
    assert join.wait(), "the heal never arrived"

    assert join.received[-1]["type"] == "heal"
    assert join.engine.opponent.health == host.engine.player.health
    assert _agree_on_turn(host, join)


def test_loading_crosses_without_ending_the_turn(table):
    """Loading is free on both sides. Ending the turn on only one of them
    is a desync that never recovers."""
    host, join = table
    host.engine.player.gun_loaded = False

    host.load()
    assert join.wait(), "the load never arrived"

    assert join.received[-1]["type"] == "load"
    assert join.engine.opponent.gun_loaded
    assert host.engine.turn is Side.PLAYER, "loading took the turn"
    assert join.engine.turn is Side.OPPONENT, "loading advanced the other engine"
    assert _agree_on_turn(host, join)


def test_a_refused_action_is_not_reported(table):
    """Telling the other player about something that did not happen desyncs
    them just as surely as failing to tell them about something that did."""
    host, join = table
    host.engine.player.gun_loaded = True

    host.load()  # already loaded; the engine refuses
    assert not host.sent, f"a refused load was still sent: {host.sent}"

    host.engine.player.health = 100
    host.heal()  # already at full health
    assert not any(kind == "heal" for kind, _ in host.sent)


def test_every_attack_is_reported(table):
    """A blanket check. Anything that ends a turn locally has to cross, or
    the two ends stop agreeing about whose go it is."""
    host, join = table

    for weapon in ("fire", "whip"):
        host.sent.clear()
        host.engine.turn = Side.PLAYER
        getattr(host, weapon)()
        kinds = [kind for kind, _ in host.sent]
        assert "strike" in kinds, f"{weapon} was not reported to the other player"


# ----------------------------------------------------------------------
# The ending
#
# Both engines reach it on their own, off the one strike that crosses the
# wire. That is the premise the user interface rests on: whatever either
# end announces about the finish afterwards is an echo of something the
# other already knows, and must not be mistaken for somebody walking out.
# ----------------------------------------------------------------------
def test_a_killing_blow_finishes_both_engines(table, monkeypatch):
    host, join = table
    monkeypatch.setattr("fusionfire.game.engine.rng.chance", lambda pct: True)
    monkeypatch.setattr("fusionfire.game.engine.rng.between", lambda lo, hi: hi)
    join.engine.player.health = 1
    host.engine.opponent.health = 1

    host.fire()
    assert join.wait(), "the killing blow never arrived"

    assert host.engine.phase is Phase.FINISHED, "the striker's match did not end"
    assert join.engine.phase is Phase.FINISHED, "the victim's match did not end"
    # Each describes the same result from its own side of the fight.
    assert host.engine.winner is Side.PLAYER
    assert join.engine.winner is Side.OPPONENT
