"""Relay mode, end to end over the loopback interface.

The relay is a dumb byte-forwarder, so these tests prove three things: that
two players who share a passphrase land in the same room, that the first one
there is told they are the host, and that the TLS handshake and match traffic
still work end to end through the relay — never decrypted, never re-encrypted,
just forwarded.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from fusionfire.net.relay import (
    MAX_SEATS,
    OPTION_RINGSIDE,
    ROLE_FULL,
    ROLE_HOST,
    ROLE_JOINER,
    ROLE_SPECTATOR,
    SEAT_HEADER,
)
from fusionfire.net.session import (
    RelaySession,
    generate_passphrase,
    room_token,
)
from srv import RelayServer


def _open(port: int, token: bytes, options: int = 0) -> socket.socket:
    """Dial the relay the way a client does: the token, then the options byte.

    The opening gained that byte when rooms gained a ringside, and the tests
    that speak to the relay directly have to speak the same opening as the
    game does.
    """
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(token + bytes([options]))
    return sock


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Recorder:
    """Collects callbacks from a session so a test can wait on them."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.connected = threading.Event()
        self.disconnected = threading.Event()
        self.got_message = threading.Event()
        self.reason = ""
        #: Progress lines, and whether each arrived before the connection
        #: completed -- which is the whole point of them.
        self.status: list[str] = []
        self.status_before_connected: list[str] = []
        self.said_something = threading.Event()

    def bind(self, session_class, **kwargs):
        return session_class(
            on_message=self._message,
            on_connected=self.connected.set,
            on_disconnected=self._down,
            on_status=self._status,
            **kwargs,
        )

    def _status(self, text: str) -> None:
        self.status.append(text)
        if not self.connected.is_set():
            self.status_before_connected.append(text)
        self.said_something.set()

    def _message(self, message: dict) -> None:
        self.messages.append(message)
        self.got_message.set()

    def _down(self, reason: str) -> None:
        self.reason = reason
        self.disconnected.set()


@pytest.fixture
def relay():
    server = RelayServer("127.0.0.1", 0)
    server.start()
    assert server.wait_until_ready(10), "the relay never bound a port"
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def relay_port(relay):
    assert relay.bound_port is not None
    return relay.bound_port


@pytest.fixture
def relay_pair(relay_port):
    """Two RelaySessions through the relay, sharing a passphrase."""
    passphrase = generate_passphrase()
    first, second = Recorder(), Recorder()
    one = first.bind(RelaySession)
    two = second.bind(RelaySession)

    one.connect_relay("127.0.0.1", relay_port, passphrase)
    time.sleep(0.1)
    two.connect_relay("127.0.0.1", relay_port, passphrase)

    assert first.connected.wait(25), "the first player never connected"
    assert second.connected.wait(25), "the second player never connected"
    try:
        yield one, two, first, second
    finally:
        one.close()
        two.close()


# ----------------------------------------------------------------------
def test_matching_passphrases_pair_through_the_relay(relay_pair):
    one, two, first, second = relay_pair
    assert one.connected
    assert two.connected


def test_the_first_player_to_join_is_the_host(relay_pair):
    one, two, first, second = relay_pair
    assert one.is_host, "the first player to join was not made the host"
    assert not two.is_host, "the second player to join was made the host too"
    assert one.is_host != two.is_host


def test_host_and_joiner_send_receive_end_to_end(relay_pair):
    one, two, first, second = relay_pair
    assert one.send("hello", version=1, name="Ada Lovelace", gender="female")
    assert second.got_message.wait(10)
    assert second.messages[0]["name"] == "Ada Lovelace"

    assert two.send("strike", weapon="gun", outcome="hit", damage=12)
    assert first.got_message.wait(10)
    assert first.messages[0] == {
        "type": "strike", "weapon": "gun", "outcome": "hit", "damage": 12,
    }


def test_a_wrong_passphrase_lands_in_a_different_room(relay_port):
    """Two players with different passphrases never pair with each other.

    They derive different room tokens, so the relay puts them in separate
    rooms where each waits alone for an opponent who shares their phrase.
    """
    passphrase = generate_passphrase()
    stranger = generate_passphrase()
    first, second = Recorder(), Recorder()
    one = first.bind(RelaySession)
    two = second.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, passphrase)
        time.sleep(0.1)
        two.connect_relay("127.0.0.1", relay_port, stranger)

        assert not first.connected.wait(3), "the two players paired after all"
        assert not second.connected.wait(3)
    finally:
        one.close()
        two.close()


def test_many_matches_run_on_one_relay_at_the_same_time(relay_port):
    """Three matches in flight, each on its own key, none aware of the rest.

    The reported worry: a match already in progress meant the next player to
    dial the server was disconnected. Rooms are keyed and independent, so
    this drives three of them at once and checks every one still carries its
    own traffic afterwards.
    """
    tokens = [room_token(generate_passphrase()) for _ in range(3)]
    pairs = []
    try:
        for token in tokens:
            host = _open(relay_port, token)
            assert host.recv(1) == ROLE_HOST
            joiner = _open(relay_port, token)
            assert joiner.recv(1) == ROLE_JOINER
            pairs.append((host, joiner))

            # Opening this room must not have disturbed any already open.
            for index, (earlier_host, earlier_joiner) in enumerate(pairs):
                earlier_host.sendall(f"match-{index}".encode())
                earlier_joiner.settimeout(5)
                assert earlier_joiner.recv(64) == f"match-{index}".encode(), (
                    f"match {index} stopped forwarding when a later one opened"
                )

        # And a match ending frees only its own room.
        ending_host, ending_joiner = pairs.pop(1)
        ending_host.close()
        ending_joiner.close()
        time.sleep(0.3)
        for index, (host, joiner) in enumerate(pairs):
            host.sendall(b"still here")
            joiner.settimeout(5)
            assert joiner.recv(64) == b"still here", (
                "a surviving match was cut off when another one ended"
            )
    finally:
        for host, joiner in pairs:
            host.close()
            joiner.close()


def test_a_finished_room_is_replaced_rather_than_handed_out_spent(relay_port):
    """The next player to dial a key whose match just ended is a new host.

    A room is dead the instant its match ends, but it is only dropped from
    the table a moment later. Handing that room to the next arrival meant
    claiming a place in it failed and the player was closed on without even
    a role byte -- a disconnection with nothing said, purely for having
    arrived at the wrong instant.
    """
    for _ in range(10):
        _reopen_a_finished_room(relay_port)


def _reopen_a_finished_room(relay_port: int) -> None:
    token = room_token(generate_passphrase())

    host = _open(relay_port, token)
    assert host.recv(1) == ROLE_HOST
    joiner = _open(relay_port, token)
    assert joiner.recv(1) == ROLE_JOINER

    host.close()
    joiner.close()

    # Straight back in on the same key, with no pause: the point is that the
    # newcomer is served however far the old room's cleanup happens to have
    # got, including the window where it is dead but still in the table.
    again = _open(relay_port, token)
    try:
        again.settimeout(10)
        assert again.recv(1) == ROLE_HOST, (
            "the newcomer was not made the host of a fresh room"
        )
    finally:
        again.close()


# ----------------------------------------------------------------------
# What the player is told while they wait
#
# The reported problem: the dialog said "connecting" from the moment you
# pressed OK until your opponent turned up, which for a host is a wait on
# another human being. Players concluded their client or the server had
# hung. The relay assigns the role within milliseconds; from then on the
# wait has to be described as what it is.
# ----------------------------------------------------------------------
def test_the_host_is_told_it_is_waiting_on_a_person(relay_port):
    """And told it *before* an opponent shows up, not afterwards."""
    first = Recorder()
    one = first.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, generate_passphrase(), secure=False)

        assert first.said_something.wait(15), "the host was told nothing at all"
        assert not first.connected.is_set(), "there is no opponent yet to be connected to"

        said = " ".join(first.status_before_connected).lower()
        assert "host" in said, f"the host was not told its role: {first.status}"
        assert "waiting" in said, (
            f"the host was not told it is waiting rather than connecting: {first.status}"
        )
    finally:
        one.close()


def test_the_wait_a_host_is_told_about_has_an_end(relay_port):
    """"Waiting" with no limit is what reads as "hung". The relay does give
    up, so the player is told when."""
    first = Recorder()
    one = first.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, generate_passphrase(), secure=False)
        assert first.said_something.wait(15)

        said = " ".join(first.status_before_connected)
        assert "minutes" in said, f"the wait was left open-ended: {first.status}"
    finally:
        one.close()


def test_the_joiner_is_told_its_role_as_well(relay_port):
    passphrase = generate_passphrase()
    first, second = Recorder(), Recorder()
    one, two = first.bind(RelaySession), second.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, passphrase, secure=False)
        assert first.said_something.wait(15)
        two.connect_relay("127.0.0.1", relay_port, passphrase, secure=False)
        assert second.said_something.wait(15)

        assert "joiner" in " ".join(second.status).lower(), second.status
    finally:
        one.close()
        two.close()


def test_a_session_with_nowhere_to_report_still_works(relay_port):
    """on_status is optional, and every existing caller omits it."""
    passphrase = generate_passphrase()
    first, second = Recorder(), Recorder()
    one = RelaySession(
        on_message=lambda m: None,
        on_connected=first.connected.set,
        on_disconnected=lambda r: None,
    )
    two = second.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, passphrase, secure=False)
        time.sleep(0.2)
        two.connect_relay("127.0.0.1", relay_port, passphrase, secure=False)
        # Only the joiner is connected outright; a casual host waits for its
        # opponent's first frame, so there has to be one to wait for.
        assert second.connected.wait(25), "the joiner never connected"
        two.send("hello", version=1, name="Joiner", gender="male")
        assert first.connected.wait(25), "a session without a status callback never connected"
    finally:
        one.close()
        two.close()


# ----------------------------------------------------------------------
# The ringside
#
# A room can keep up to three seats. Whoever opens it decides whether it
# has any; anyone arriving to find the two places taken is sat down instead
# of being turned away. Seats are fed both fighters and heard by neither.
# ----------------------------------------------------------------------
def _seats_open(relay_port, token, count):
    """Take `count` seats in a room that already has its two fighters."""
    return [_open(relay_port, token) for _ in range(count)]


def test_a_room_has_no_ringside_unless_it_was_asked_for(relay_port):
    """The default is a fight nobody watches."""
    token = room_token(generate_passphrase())
    host = _open(relay_port, token, 0)
    joiner = _open(relay_port, token, 0)
    try:
        assert host.recv(1) == ROLE_HOST
        assert joiner.recv(1) == ROLE_JOINER

        # Asking on the way in is too late; the opener already answered.
        third = _open(relay_port, token, OPTION_RINGSIDE)
        try:
            assert third.recv(1) == ROLE_FULL
        finally:
            third.close()
    finally:
        host.close()
        joiner.close()


def test_a_ringside_seats_three_and_no_more(relay_port):
    token = room_token(generate_passphrase())
    host = _open(relay_port, token, OPTION_RINGSIDE)
    joiner = _open(relay_port, token)
    seats = []
    try:
        assert host.recv(1) == ROLE_HOST
        assert joiner.recv(1) == ROLE_JOINER

        for taken in range(MAX_SEATS):
            seat = _open(relay_port, token)
            seats.append(seat)
            assert seat.recv(1) == ROLE_SPECTATOR, f"seat {taken + 1} was refused"

        spare = _open(relay_port, token)
        try:
            assert spare.recv(1) == ROLE_FULL, "a fourth watcher got in"
        finally:
            spare.close()
    finally:
        for sock in [host, joiner, *seats]:
            sock.close()


def test_a_seat_is_told_which_fighter_moved(relay_port):
    """A player's socket carries one conversation; a seat's carries two.
    Without the tag they would arrive shuffled together with no way to tell
    whose move was whose."""
    token = room_token(generate_passphrase())
    host = _open(relay_port, token, OPTION_RINGSIDE)
    joiner = _open(relay_port, token)
    assert host.recv(1) == ROLE_HOST
    assert joiner.recv(1) == ROLE_JOINER
    seat = _open(relay_port, token)
    assert seat.recv(1) == ROLE_SPECTATOR
    try:
        host.sendall(b"from the host")
        time.sleep(0.3)
        joiner.sendall(b"from the joiner")

        header = struct.Struct(SEAT_HEADER)
        seat.settimeout(10)
        buf = bytearray()
        got = []
        while len(got) < 2:
            buf += seat.recv(4096)
            while len(buf) >= header.size:
                tag, length = header.unpack(buf[: header.size])
                if len(buf) < header.size + length:
                    break
                got.append((tag, bytes(buf[header.size : header.size + length])))
                del buf[: header.size + length]

        assert got == [(ROLE_HOST, b"from the host"), (ROLE_JOINER, b"from the joiner")]
    finally:
        for sock in (host, joiner, seat):
            sock.close()


def test_the_players_still_get_each_other_untagged(relay_port):
    """The seats are a copy. What the two fighters exchange is unchanged,
    because their splice is what a match actually runs on."""
    token = room_token(generate_passphrase())
    host = _open(relay_port, token, OPTION_RINGSIDE)
    joiner = _open(relay_port, token)
    assert host.recv(1) == ROLE_HOST
    assert joiner.recv(1) == ROLE_JOINER
    seat = _open(relay_port, token)
    assert seat.recv(1) == ROLE_SPECTATOR
    try:
        # Clear the seat notice the relay sends the fighters.
        host.settimeout(5)
        joiner.settimeout(5)
        host.recv(4096)
        joiner.recv(4096)

        host.sendall(b"exactly these bytes")
        assert joiner.recv(4096) == b"exactly these bytes"
    finally:
        for sock in (host, joiner, seat):
            sock.close()


def test_nothing_a_seat_sends_reaches_the_fight(relay_port):
    """The ringside is one way. A watcher cannot interrupt a match, whatever
    they are running."""
    token = room_token(generate_passphrase())
    host = _open(relay_port, token, OPTION_RINGSIDE)
    joiner = _open(relay_port, token)
    assert host.recv(1) == ROLE_HOST
    assert joiner.recv(1) == ROLE_JOINER
    seat = _open(relay_port, token)
    assert seat.recv(1) == ROLE_SPECTATOR
    try:
        host.settimeout(5)
        joiner.settimeout(5)
        host.recv(4096)  # the seat notice
        joiner.recv(4096)

        seat.sendall(b"let me in")
        time.sleep(0.4)

        for sock, who in ((host, "host"), (joiner, "joiner")):
            sock.settimeout(0.5)
            with pytest.raises((socket.timeout, TimeoutError)):
                sock.recv(4096)
    finally:
        for sock in (host, joiner, seat):
            sock.close()


def test_the_fighters_are_told_how_many_are_watching(relay_port):
    """They have no other way of knowing. A seat cannot speak to them, so
    the relay says so on its behalf -- the one frame it ever writes."""
    token = room_token(generate_passphrase())
    host = _open(relay_port, token, OPTION_RINGSIDE)
    joiner = _open(relay_port, token)
    assert host.recv(1) == ROLE_HOST
    assert joiner.recv(1) == ROLE_JOINER
    seats = []
    try:
        for _ in range(2):
            seat = _open(relay_port, token)
            assert seat.recv(1) == ROLE_SPECTATOR
            seats.append(seat)

        host.settimeout(10)
        counts = _seat_counts(host, 2)
        assert counts == [1, 2], counts

        seats.pop().close()
        assert _seat_counts(host, 1) == [0 + 1], "the count was not corrected"
    finally:
        for sock in [host, joiner, *seats]:
            sock.close()


def _seat_counts(sock, how_many):
    """Read `how_many` of the relay's ringside frames off a fighter's socket."""
    from fusionfire.net import protocol

    found, buf = [], bytearray()
    sock.settimeout(10)
    while len(found) < how_many:
        buf += sock.recv(4096)
        while len(buf) >= protocol.HEADER_SIZE:
            length = protocol.read_length(bytes(buf[: protocol.HEADER_SIZE]))
            if len(buf) < protocol.HEADER_SIZE + length:
                break
            body = bytes(buf[protocol.HEADER_SIZE : protocol.HEADER_SIZE + length])
            del buf[: protocol.HEADER_SIZE + length]
            message = protocol.decode(body)
            if message["type"] == "ringside":
                found.append(message["seats"])
    return found


def test_a_session_given_a_seat_says_so(relay_port):
    """And says which fighter each message came from, because at a ringside
    neither of them is "you"."""
    passphrase = generate_passphrase()
    first, second, watcher = Recorder(), Recorder(), Recorder()
    host = first.bind(RelaySession)
    joiner = second.bind(RelaySession)
    seat = watcher.bind(RelaySession)
    try:
        host.connect_relay("127.0.0.1", relay_port, passphrase, secure=False, ringside=True)
        time.sleep(0.3)
        joiner.connect_relay("127.0.0.1", relay_port, passphrase, secure=False)
        assert second.connected.wait(20)
        joiner.send("hello", version=1, name="Blue Screen", gender="male")
        assert first.connected.wait(20)

        seat.connect_relay("127.0.0.1", relay_port, passphrase, secure=False)
        assert watcher.connected.wait(20), "the seat never connected"
        assert seat.is_spectator, "the session did not know it had a seat"
        assert not host.is_spectator and not joiner.is_spectator

        host.send("strike", weapon="gun", outcome="hit", damage=12)
        joiner.send("strike", weapon="whip", outcome="miss", damage=0)
        deadline = time.monotonic() + 15
        while len(watcher.messages) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

        seen = [(m["source"], m["type"]) for m in watcher.messages]
        assert ("host", "strike") in seen, seen
        assert ("joiner", "strike") in seen, seen
    finally:
        for session in (host, joiner, seat):
            session.close()


def test_an_encrypted_fight_cannot_be_watched():
    """TLS is a conversation between two ends. There is no third place to
    stand, and pretending otherwise would mean handing the relay a key."""
    session = RelaySession(
        on_message=lambda m: None,
        on_connected=lambda: None,
        on_disconnected=lambda r: None,
    )
    with pytest.raises(ValueError):
        session.connect_relay("127.0.0.1", 6001, generate_passphrase(), ringside=True)


def test_a_third_player_is_refused_a_full_room(relay_port):
    passphrase = generate_passphrase()
    first, second, third = Recorder(), Recorder(), Recorder()
    one = first.bind(RelaySession)
    two = second.bind(RelaySession)
    three = third.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, passphrase)
        time.sleep(0.1)
        two.connect_relay("127.0.0.1", relay_port, passphrase)
        assert first.connected.wait(25)
        assert second.connected.wait(25)

        three.connect_relay("127.0.0.1", relay_port, passphrase)
        assert third.disconnected.wait(15), "the third player was not refused"
        assert not third.connected.is_set()
        assert "two players" in third.reason.lower()
    finally:
        one.close()
        two.close()
        three.close()


def test_a_host_waits_for_a_slow_opponent_to_launch(monkeypatch):
    """The reported bug: the host's TLS handshake timed out before the other
    player had launched the client. Waiting for the joiner is waiting on a
    human, so it is bounded by the relay's pairing window, not by the short
    crypto handshake timeout."""
    import fusionfire.net.session as session

    monkeypatch.setattr(session, "HANDSHAKE_TIMEOUT", 1.0)
    monkeypatch.setattr(session, "RELAY_HANDSHAKE_TIMEOUT", 10.0)

    server = RelayServer("127.0.0.1", 0)
    server.start()
    assert server.wait_until_ready(10), "the relay never bound a port"
    passphrase = generate_passphrase()

    host_recorder, joiner_recorder = Recorder(), Recorder()
    host = host_recorder.bind(RelaySession)
    joiner = joiner_recorder.bind(RelaySession)
    try:
        host.connect_relay("127.0.0.1", server.bound_port, passphrase)

        # Well past the crypto handshake timeout that used to kill the host
        # while it waited for its opponent to launch the game.
        time.sleep(2.5)
        assert not host_recorder.disconnected.is_set(), (
            "the host gave up on its opponent before they launched"
        )

        joiner.connect_relay("127.0.0.1", server.bound_port, passphrase)
        assert host_recorder.connected.wait(15), "the pair never connected"
        assert joiner_recorder.connected.wait(15), "the pair never connected"
    finally:
        host.close()
        joiner.close()
        server.stop()


# ----------------------------------------------------------------------
# Casual play: ``secure=False`` pairs by a public room code, and the relay
# treats the hashed code as just another opaque 16-byte token.
def test_casual_codes_map_to_a_sixteen_byte_room_token():
    import fusionfire.net.session as session

    code = session.casual_code()
    assert code
    token = session.casual_token(code)
    assert len(token) == 16
    assert token == session.casual_token(code), "the same code must give the same token"
    assert token != session.casual_token(code + "x"), "a different code must differ"
    with pytest.raises(ValueError, match="room code"):
        session.casual_token("")


def test_casual_play_pairs_by_a_public_room_code(relay_port):
    import fusionfire.net.session as session

    code = session.casual_code()
    first, second = Recorder(), Recorder()
    one = first.bind(RelaySession)
    two = second.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, code, secure=False)
        time.sleep(0.1)
        two.connect_relay("127.0.0.1", relay_port, code, secure=False)

        # The joiner is told it is connected at once; its hello is what
        # unblocks the host's waiting gate.
        assert second.connected.wait(15), "the joiner never connected"
        assert two.send("hello", version=1, name="Ada Lovelace", gender="female")
        assert first.connected.wait(15), "the host never heard its opponent arrive"
        assert first.messages[0]["name"] == "Ada Lovelace"
        assert one.is_host and not two.is_host
    finally:
        one.close()
        two.close()


def test_casual_play_never_pairs_different_codes(relay_port):
    """Different room codes are different rooms; each player waits alone."""
    import fusionfire.net.session as session

    first, second = Recorder(), Recorder()
    one = first.bind(RelaySession)
    two = second.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", relay_port, session.casual_code(), secure=False)
        time.sleep(0.1)
        two.connect_relay("127.0.0.1", relay_port, session.casual_code(), secure=False)

        assert not first.connected.wait(2.0)
        assert not second.connected.wait(2.0)
    finally:
        one.close()
        two.close()


def test_a_casual_host_waits_for_its_opponent_before_connecting(monkeypatch):
    """The relay tells the casual host it is the host the moment it dials in
    — possibly minutes before the opponent has even launched the game. The
    host must neither be told it is connected nor be declared dead by the
    idle timer until the opponent's first frame actually arrives."""
    import fusionfire.net.session as session

    monkeypatch.setattr(session, "IDLE_TIMEOUT", 1.5)

    server = RelayServer("127.0.0.1", 0)
    server.start()
    assert server.wait_until_ready(10), "the relay never bound a port"
    code = session.casual_code()

    host_rec, joiner_rec = Recorder(), Recorder()
    host = host_rec.bind(RelaySession)
    joiner = joiner_rec.bind(RelaySession)
    try:
        host.connect_relay("127.0.0.1", server.bound_port, code, secure=False)

        # Longer than the idle timeout, which would normally call the
        # session dead; the gated host must still be standing.
        time.sleep(2.5)
        assert not host_rec.disconnected.is_set(), (
            "the host gave up while waiting for its opponent to launch"
        )
        assert not host_rec.connected.is_set(), (
            "the host was told it was connected before its opponent arrived"
        )

        joiner.connect_relay("127.0.0.1", server.bound_port, code, secure=False)
        assert joiner_rec.connected.wait(15), "the joiner never connected"
        joiner.send("hello", version=1, name="Ada Lovelace", gender="female")
        assert host_rec.connected.wait(15), "the host never heard its opponent arrive"
        assert host_rec.messages[0]["name"] == "Ada Lovelace"
    finally:
        host.close()
        joiner.close()
        server.stop()


def test_an_idle_room_is_not_torn_down(monkeypatch):
    """A player thinking through a move must not get disconnected.

    The token read leaves a short socket timeout on the paired sockets, and
    a room that fell quiet for longer than that would be torn down between
    the clients' 30-second keepalives. The splice has to make the sockets
    blocking, or a thought is a disconnect.
    """
    monkeypatch.setattr("srv.TOKEN_TIMEOUT", 1.0)
    server = RelayServer("127.0.0.1", 0)
    server.start()
    assert server.wait_until_ready(10), "the relay never bound a port"
    passphrase = generate_passphrase()
    first, second = Recorder(), Recorder()
    one = first.bind(RelaySession)
    two = second.bind(RelaySession)
    try:
        one.connect_relay("127.0.0.1", server.bound_port, passphrase)
        time.sleep(0.1)
        two.connect_relay("127.0.0.1", server.bound_port, passphrase)

        assert first.connected.wait(25), "the first player never connected"
        assert second.connected.wait(25), "the second player never connected"

        # Hold still for well past the token timeout. The keepalive interval
        # is thirty seconds, so the pair is genuinely silent for this long.
        assert not first.disconnected.wait(3), "the idle room was torn down"
        assert not second.disconnected.wait(3), "the idle room was torn down"
    finally:
        one.close()
        two.close()
        server.stop()


# ----------------------------------------------------------------------
def test_room_tokens_match_for_the_same_passphrase():
    passphrase = generate_passphrase()
    assert room_token(passphrase) == room_token(passphrase)


def test_room_tokens_differ_between_passphrases():
    assert room_token(generate_passphrase()) != room_token(generate_passphrase())


def test_the_room_token_is_sixteen_bytes():
    assert len(room_token(generate_passphrase())) == 16


def test_a_blank_passphrase_is_refused():
    with pytest.raises(ValueError, match="required"):
        room_token("")


# ----------------------------------------------------------------------
# The standalone server must stay in lockstep with what the game expects.
def test_the_standalone_server_matches_the_games_wire_constants():
    import srv

    from fusionfire.net.relay import (
        MAX_ROOMS,
        RELAY_DEFAULT_PORT,
        ROLE_FULL,
        ROLE_HOST,
        ROLE_JOINER,
        ROOM_TOKEN_LENGTH,
        ROOM_WAIT_TIMEOUT,
        TOKEN_TIMEOUT,
    )

    assert srv.ROOM_TOKEN_LENGTH == ROOM_TOKEN_LENGTH
    assert srv.RELAY_DEFAULT_PORT == RELAY_DEFAULT_PORT
    assert srv.TOKEN_TIMEOUT == TOKEN_TIMEOUT
    assert srv.ROOM_WAIT_TIMEOUT == ROOM_WAIT_TIMEOUT
    assert srv.MAX_ROOMS == MAX_ROOMS
    assert (srv.ROLE_HOST, srv.ROLE_JOINER, srv.ROLE_FULL) == (
        ROLE_HOST, ROLE_JOINER, ROLE_FULL,
    )


# ----------------------------------------------------------------------
# Command line: srv.py <name> <port> [-P]
# ----------------------------------------------------------------------
def test_main_rejects_a_bad_port():
    from srv import main

    with pytest.raises(SystemExit):
        main(["relay.example.org", "70000"])


def test_main_rejects_a_name_that_is_not_an_address():
    from srv import main

    with pytest.raises(SystemExit):
        main(["not a host", "6001"])


def test_main_rejects_a_public_host_with_spaces():
    from srv import main

    with pytest.raises(SystemExit):
        main(["test", "6001", "-A", "not a host", "-P", "https://spy.example.org/servers"])


def test_main_publicizes_a_label_with_a_separate_dialable_host(monkeypatch):
    """srv.py test 7000 -A fusion.seedy.cc -P ... must advertise the label
    but point players at the dialable address."""
    from srv import RelayServer, main

    publicized = []

    class FakePublicizer:
        def __init__(self, **kwargs):
            publicized.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

    def fake_serve(self):
        raise KeyboardInterrupt

    monkeypatch.setattr("srv.Publicizer", FakePublicizer)
    monkeypatch.setattr("srv.spy_url_from_config", lambda: "https://spy.example.org/servers")
    monkeypatch.setattr(RelayServer, "serve_forever", fake_serve)

    assert main(["test", "7000", "-A", "fusion.seedy.cc", "-P"]) == 0
    assert publicized == [{
        "spy_url": "https://spy.example.org/servers",
        "name": "test",
        "host": "fusion.seedy.cc",
        "port": 7000,
    }]


def test_main_publicizes_and_serves(monkeypatch):
    """srv.py name port -P must announce the server and then serve."""
    from srv import RelayServer, main

    publicized = []

    class FakePublicizer:
        def __init__(self, **kwargs):
            publicized.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

    def fake_serve(self):
        raise KeyboardInterrupt  # stop the server loop as a signal to end

    monkeypatch.setattr("srv.Publicizer", FakePublicizer)
    monkeypatch.setattr(
        "srv.spy_url_from_config",
        lambda: "https://spy.example.org/servers",
    )
    monkeypatch.setattr(RelayServer, "serve_forever", fake_serve)

    assert main(["relay.example.org", "7000", "-P"]) == 0
    assert publicized == [{
        "spy_url": "https://spy.example.org/servers",
        "name": "relay.example.org",
        "host": "relay.example.org",
        "port": 7000,
    }]


def test_main_publicizes_to_a_url_given_directly(monkeypatch):
    """srv.py name port -P <url> must announce to that URL directly,
    without consulting the environment or the game's settings."""
    from srv import RelayServer, main

    publicized = []

    class FakePublicizer:
        def __init__(self, **kwargs):
            publicized.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

    def fake_serve(self):
        raise KeyboardInterrupt

    monkeypatch.setattr("srv.Publicizer", FakePublicizer)
    monkeypatch.setattr(
        "srv.spy_url_from_config",
        lambda: pytest.fail("the settings lookup must not run"),
    )
    monkeypatch.setattr(RelayServer, "serve_forever", fake_serve)

    assert main(
        ["relay.example.org", "7001", "-P", "https://spy.example.org/servers"]
    ) == 0
    assert publicized == [{
        "spy_url": "https://spy.example.org/servers",
        "name": "relay.example.org",
        "host": "relay.example.org",
        "port": 7001,
    }]


def test_publicizing_without_a_configured_spy_is_refused(monkeypatch):
    """-P with no spy URL must fail cleanly rather than serve unadvertised."""
    from srv import RelayServer, main

    monkeypatch.setattr("srv.spy_url_from_config", lambda: "")
    monkeypatch.setattr(
        RelayServer, "serve_forever", lambda self: pytest.fail("served anyway")
    )

    assert main(["relay.example.org", "6001", "-P"]) == 1


# ----------------------------------------------------------------------
# The wire protocol itself: a raw client should be answered with its role.
def test_raw_clients_get_host_then_joiner_then_full(relay_port):
    passphrase = generate_passphrase()
    token = room_token(passphrase)

    first = _open(relay_port, token)
    assert first.recv(1) == ROLE_HOST

    second = _open(relay_port, token)
    assert second.recv(1) == ROLE_JOINER

    third = _open(relay_port, token)
    assert third.recv(1) == ROLE_FULL

    for sock in (first, second, third):
        sock.close()


def test_bytes_flow_between_spliced_connections(relay_port):
    token = room_token(generate_passphrase())

    first = _open(relay_port, token)
    assert first.recv(1) == ROLE_HOST

    second = _open(relay_port, token)
    assert second.recv(1) == ROLE_JOINER

    second.sendall(b"x" * 5000)
    received = bytearray()
    while len(received) < 5000:
        chunk = first.recv(5000 - len(received))
        assert chunk, "the splice closed before forwarding the bytes"
        received += chunk
    assert bytes(received) == b"x" * 5000, "the splice did not forward the bytes"

    first.close()
    second.close()
