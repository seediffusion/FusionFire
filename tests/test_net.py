"""Online sessions, end to end over the loopback interface.

These start real listeners on real ports and complete real TLS handshakes.
They are slower than the rest of the suite (scrypt is deliberately expensive)
but they are the only way to prove the thing that matters most: that a peer
without the passphrase cannot get in.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from fusionfire.net.session import (
    HostSession,
    JoinSession,
    generate_passphrase,
    local_addresses,
)


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
def pair():
    """A connected host/join pair sharing a passphrase. Torn down after."""
    passphrase = generate_passphrase()
    port = _free_port()
    host_rec, join_rec = Recorder(), Recorder()
    host = host_rec.bind(HostSession)
    join = join_rec.bind(JoinSession)

    host.listen(port=port, passphrase=passphrase)
    time.sleep(0.2)
    join.connect("127.0.0.1", port, passphrase)

    assert host_rec.connected.wait(20), "the host never saw a connection"
    assert join_rec.connected.wait(20), "the joiner never connected"
    try:
        yield host, join, host_rec, join_rec
    finally:
        host.close()
        join.close()


def test_matching_passphrases_connect(pair):
    host, join, _, _ = pair
    assert host.connected
    assert join.connected


def test_a_strike_crosses_the_wire_intact(pair):
    host, join, host_rec, _ = pair
    assert join.send("strike", weapon="gun", outcome="hit", damage=12)
    assert host_rec.got_message.wait(10)
    assert host_rec.messages[0] == {
        "type": "strike", "weapon": "gun", "outcome": "hit", "damage": 12,
    }


def test_messages_flow_in_both_directions(pair):
    host, join, host_rec, join_rec = pair
    join.send("hello", version=1, name="Ada Lovelace", gender="female")
    assert host_rec.got_message.wait(10)
    host.send("hello", version=1, name="Alan Turing", gender="male")
    assert join_rec.got_message.wait(10)
    assert join_rec.messages[0]["name"] == "Alan Turing"


def test_a_malformed_message_is_never_sent(pair):
    host, join, host_rec, _ = pair
    # 9,000 damage is outside every weapon's range, so encoding refuses it and
    # the peer never sees a frame at all.
    assert join.send("strike", weapon="gun", outcome="hit", damage=9000) is False
    assert not host_rec.got_message.wait(1.0)


def test_resigning_reaches_the_other_side(pair):
    host, join, host_rec, _ = pair
    join.send("resign", reason="Had enough.")
    assert host_rec.got_message.wait(10)
    assert host_rec.messages[0]["reason"] == "Had enough."


def test_a_wrong_passphrase_cannot_connect():
    port = _free_port()
    host_rec, join_rec = Recorder(), Recorder()
    host = host_rec.bind(HostSession)
    join = join_rec.bind(JoinSession)
    try:
        host.listen(port=port, passphrase="the-real-passphrase-here")
        time.sleep(0.2)
        join.connect("127.0.0.1", port, "a-different-passphrase")

        assert join_rec.disconnected.wait(25), "the joiner should have been rejected"
        assert not join_rec.connected.is_set()
        assert not host_rec.connected.is_set()
        assert "passphrase" in join_rec.reason.lower()
    finally:
        host.close()
        join.close()


def test_sending_on_a_dead_socket_does_not_deadlock(pair):
    """Regression: the failure path of ``send`` used to call ``_shutdown``
    while still holding the send lock, which ``_close_sockets`` then tried to
    take again. A dropped connection froze the whole game."""
    host, join, _, join_rec = pair

    # Kill the socket underneath the sender without going through close(),
    # so the next sendall raises exactly as it would on a real drop.
    join._sock.detach()

    finished = threading.Event()

    def attempt():
        join.send("laugh")
        finished.set()

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    assert finished.wait(10), "send() deadlocked on a failed transmission"
    assert join_rec.disconnected.is_set()


def test_a_short_passphrase_is_refused_before_binding():
    with pytest.raises(ValueError, match="at least"):
        HostSession(
            on_message=lambda m: None,
            on_connected=lambda: None,
            on_disconnected=lambda r: None,
        ).listen(port=_free_port(), passphrase="short")


def test_an_out_of_range_port_is_refused():
    with pytest.raises(ValueError, match="between"):
        JoinSession(
            on_message=lambda m: None,
            on_connected=lambda: None,
            on_disconnected=lambda r: None,
        ).connect("127.0.0.1", 99999, generate_passphrase())


def test_joining_with_no_host_address_is_refused():
    with pytest.raises(ValueError, match="host address"):
        JoinSession(
            on_message=lambda m: None,
            on_connected=lambda: None,
            on_disconnected=lambda r: None,
        ).connect("", 6000, generate_passphrase())


def test_local_addresses_are_reported_without_calling_out():
    found = local_addresses()
    assert found
    assert all(isinstance(address, str) for address in found)


# ----------------------------------------------------------------------
# Casual play: the same sessions with ``secure=False``, plain TCP and no
# passphrase.
def test_casual_play_connects_without_a_passphrase():
    """The default online flow: no shared secret, no TLS, plain sockets."""
    port = _free_port()
    host_rec, join_rec = Recorder(), Recorder()
    host = host_rec.bind(HostSession)
    join = join_rec.bind(JoinSession)
    try:
        host.listen(port=port, secure=False)
        time.sleep(0.2)
        join.connect("127.0.0.1", port, secure=False)

        assert host_rec.connected.wait(10), "the host never saw a connection"
        assert join_rec.connected.wait(10), "the joiner never connected"
        assert join.send("strike", weapon="gun", outcome="hit", damage=12)
        assert host_rec.got_message.wait(10)
        assert host_rec.messages[0]["damage"] == 12
    finally:
        host.close()
        join.close()


def test_casual_play_needs_no_passphrase_at_all():
    """With ``secure=False`` an empty passphrase is fine; it is ignored."""
    port = _free_port()
    host_rec, join_rec = Recorder(), Recorder()
    host = host_rec.bind(HostSession)
    join = join_rec.bind(JoinSession)
    try:
        host.listen(port=port, secure=False)
        time.sleep(0.2)
        join.connect("127.0.0.1", port, secure=False)

        assert host_rec.connected.wait(10)
        assert join_rec.connected.wait(10)
    finally:
        host.close()
        join.close()
