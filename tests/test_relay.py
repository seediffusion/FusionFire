"""Relay mode, end to end over the loopback interface.

The relay is a dumb byte-forwarder, so these tests prove three things: that
two players who share a passphrase land in the same room, that the first one
there is told they are the host, and that the TLS handshake and match traffic
still work end to end through the relay — never decrypted, never re-encrypted,
just forwarded.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from fusionfire.net.relay import ROLE_FULL, ROLE_HOST, ROLE_JOINER
from fusionfire.net.session import (
    RelaySession,
    generate_passphrase,
    room_token,
)
from srv import RelayServer


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

    def bind(self, session_class, **kwargs):
        return session_class(
            on_message=self._message,
            on_connected=self.connected.set,
            on_disconnected=self._down,
            **kwargs,
        )

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
            host = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
            host.sendall(token)
            assert host.recv(1) == ROLE_HOST
            joiner = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
            joiner.sendall(token)
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

    host = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    host.sendall(token)
    assert host.recv(1) == ROLE_HOST
    joiner = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    joiner.sendall(token)
    assert joiner.recv(1) == ROLE_JOINER

    host.close()
    joiner.close()

    # Straight back in on the same key, with no pause: the point is that the
    # newcomer is served however far the old room's cleanup happens to have
    # got, including the window where it is dead but still in the table.
    again = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    try:
        again.sendall(token)
        again.settimeout(10)
        assert again.recv(1) == ROLE_HOST, (
            "the newcomer was not made the host of a fresh room"
        )
    finally:
        again.close()


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

    first = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    first.sendall(token)
    assert first.recv(1) == ROLE_HOST

    second = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    second.sendall(token)
    assert second.recv(1) == ROLE_JOINER

    third = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    third.sendall(token)
    assert third.recv(1) == ROLE_FULL

    for sock in (first, second, third):
        sock.close()


def test_bytes_flow_between_spliced_connections(relay_port):
    token = room_token(generate_passphrase())

    first = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    first.sendall(token)
    assert first.recv(1) == ROLE_HOST

    second = socket.create_connection(("127.0.0.1", relay_port), timeout=10)
    second.sendall(token)
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
