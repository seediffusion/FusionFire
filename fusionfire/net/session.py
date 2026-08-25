"""Authenticated, encrypted peer-to-peer sessions.

Threat model
------------
Two people who know each other want to fight over the internet. They can
exchange a passphrase out of band (a phone call, a chat window). Everyone
else — including whoever runs the network in between — is untrusted.

What this gives them:

* **Confidentiality and integrity** via TLS 1.3.
* **Mutual authentication** via a pre-shared key. Neither side needs a
  certificate, a CA, or a domain name, which matters because the original's
  players connect by bare IP address. Someone who does not know the
  passphrase cannot complete the handshake, so the "whoever connects first is
  your opponent" problem is gone.
* **Resistance to passphrase grinding.** The PSK is derived with scrypt, so
  each guess costs an attacker real work rather than a hash.
* **Bounded resource use.** Frame sizes are capped, the listener accepts
  exactly one peer and then closes, reads have timeouts, and a peer that
  floods is disconnected.

What it does not give them: protection from an opponent running modified
code. The peer rolls its own dice, and all we can do is bound the numbers it
reports (see :mod:`fusionfire.net.protocol`). A dedicated server would fix
that and is out of scope for a two-player audio game.

Casual play is the other face of the same sessions: with ``secure=False`` the
TLS layer is skipped entirely and the match runs over a plain socket. The
relay then pairs by a *public* room code (see :func:`casual_token`) instead
of a secret token. There is no confidentiality and no authentication — anyone
who types the code in first is your opponent — which is exactly the point for
two people who just want to play without a secret. Framing, keepalives, and
rate limits are unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import socket
import ssl
import struct
import threading
import time
from hashlib import scrypt
from typing import Callable

from . import protocol
from .protocol import ProtocolError
from .relay import (
    OPTION_RINGSIDE,
    ROLE_FULL,
    ROLE_HOST,
    ROLE_JOINER,
    ROLE_SPECTATOR,
    ROOM_TOKEN_LENGTH,
    ROOM_WAIT_TIMEOUT,
    SEAT_HEADER,
)

#: A seat's stream is chunks tagged with the player they came from; see
#: :data:`fusionfire.net.relay.SEAT_HEADER`.
_SEAT_HEADER = struct.Struct(SEAT_HEADER)

log = logging.getLogger(__name__)

DEFAULT_PORT = 6000
RELAY_PORT = 6001
HANDSHAKE_TIMEOUT = 20.0
CONNECT_TIMEOUT = 15.0

#: How long a relay session may sit in its TLS handshake.
#:
#: The relay assigns the host role the moment the first player arrives, and
#: the host then starts its server-side handshake immediately -- but the
#: joiner may still be launching the client. Waiting for their ClientHello is
#: waiting on a human, not on a handful of crypto round trips, so this has to
#: be the relay's own pairing window rather than HANDSHAKE_TIMEOUT. When the
#: relay gives up on the room it closes the host's socket and the handshake
#: fails on its own.
RELAY_HANDSHAKE_TIMEOUT = ROOM_WAIT_TIMEOUT

#: How long a blocking read waits before coming up for air. Short, because
#: this is also how long ``close()`` can take to stop the reader thread — a
#: socket timeout measured in minutes would leave a thread wedged in ``recv``
#: long after the player has returned to the menu.
READ_POLL = 1.0
#: Silence beyond this means the peer is gone, whatever the socket thinks.
IDLE_TIMEOUT = 150.0
KEEPALIVE_INTERVAL = 30.0

#: Token bucket: sustained rate and burst allowance, in messages.
RATE_LIMIT_PER_SECOND = 20.0
RATE_LIMIT_BURST = 40.0

#: Domain-separation salt for the passphrase KDF. Fixed so both ends derive
#: the same key; the work factor, not the salt, is what defends the
#: passphrase.
_KDF_SALT = b"fusion-fire/psk/v1"
_KDF_N = 2**15
_KDF_R = 8
_KDF_P = 1
#: 128 * N * r comes to 32 MiB here, which is exactly OpenSSL's default
#: ``maxmem``; ask for headroom or the derivation is refused outright.
_KDF_MAXMEM = 64 * 1024 * 1024
_PSK_LENGTH = 32

#: TLS 1.3 PSK identity. Carries no secret; it just labels the key.
_PSK_IDENTITY = "fusion-fire"

#: Domain-separation salt for the room token the relay pairs players by.
#: Different salt, same work factor: a relay that sees the token gains
#: nothing over the on-path observer who already sees the handshake.
_RELAY_SALT = b"fusion-fire/relay-room/v1"

MIN_PASSPHRASE_LENGTH = 12


def generate_passphrase() -> str:
    """A high-entropy passphrase for the host to read out to their opponent."""
    return secrets.token_urlsafe(18)


def derive_psk(passphrase: str) -> bytes:
    """Stretch a human-typed passphrase into a 32-byte pre-shared key."""
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("A passphrase is required.")
    return scrypt(
        passphrase.encode("utf-8"),
        salt=_KDF_SALT,
        n=_KDF_N,
        r=_KDF_R,
        p=_KDF_P,
        maxmem=_KDF_MAXMEM,
        dklen=_PSK_LENGTH,
    )


def room_token(passphrase: str) -> bytes:
    """The bytes a player sends a relay to join their passphrase's room.

    Two players who type the same passphrase derive the same token and land
    in the same room; anyone else derives a different one and is paired
    nowhere near them. It is 128 bits of scrypt output, so guessing it costs
    exactly as much as guessing the passphrase it came from.
    """
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("A passphrase is required.")
    return scrypt(
        passphrase.encode("utf-8"),
        salt=_RELAY_SALT,
        n=_KDF_N,
        r=_KDF_R,
        p=_KDF_P,
        maxmem=_KDF_MAXMEM,
        dklen=ROOM_TOKEN_LENGTH,
    )


def casual_code() -> str:
    """A room code for casual play. Not a secret, just an identifier.

    Anyone who knows it can join the room, so it only needs to be
    distinctive enough that two games in flight do not collide. The host
    reads it out (or pastes it) and the opponent types the same one back.
    """
    return secrets.token_urlsafe(6)


def casual_token(code: str) -> bytes:
    """The 16-byte room key for a casual room, where the code is public.

    Hashing maps any code onto the same fixed length the relay expects
    without pretending the code is a secret: two players who type the same
    code derive the same token and land in the same room, and anyone else —
    including an eavesdropper — can do the same. That is the definition of
    casual play.
    """
    if not isinstance(code, str) or not code:
        raise ValueError("A room code is required.")
    return hashlib.sha256(code.encode("utf-8")).digest()[:ROOM_TOKEN_LENGTH]


def _base_context(server: bool) -> ssl.SSLContext:
    context = ssl.SSLContext(
        ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT
    )
    # PSK cipher suites carry the authentication; certificate checking is not
    # merely unnecessary here, it is inapplicable — there are no certificates.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context


def server_context(passphrase: str) -> ssl.SSLContext:
    psk = derive_psk(passphrase)
    context = _base_context(server=True)
    context.set_psk_server_callback(lambda identity: psk, _PSK_IDENTITY)
    return context


def client_context(passphrase: str) -> ssl.SSLContext:
    psk = derive_psk(passphrase)
    context = _base_context(server=False)
    context.set_psk_client_callback(lambda hint: (_PSK_IDENTITY, psk))
    return context


class _RateLimiter:
    """Token bucket. Returns False once a peer is talking too fast."""

    def __init__(self, rate: float, burst: float) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = burst
        self._last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class NetSession:
    """A live connection to the other player.

    Callbacks fire on the reader thread. The application layer is expected to
    hop them onto the UI thread — :class:`fusionfire.app.Presenter` does this
    with ``wx.CallAfter``.
    """

    def __init__(
        self,
        *,
        on_message: Callable[[dict], None],
        on_connected: Callable[[], None],
        on_disconnected: Callable[[str], None],
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_status = on_status

        self._sock: ssl.SSLSocket | None = None
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed_reason: str | None = None
        self._limiter = _RateLimiter(RATE_LIMIT_PER_SECOND, RATE_LIMIT_BURST)
        self._last_keepalive = 0.0
        self._last_heard = 0.0
        #: Whether this session is the host. True for :class:`HostSession`,
        #: decided by arrival order at the relay for :class:`RelaySession`.
        self._is_host = False
        #: True for a relay session that arrived to find the room already
        #: had its two fighters and was given a seat instead.
        self._is_spectator = False
        #: Bytes received but not yet formed into a complete frame.
        self._rxbuf = bytearray()
        #: Hold back ``on_connected`` until the opponent's first frame
        #: arrives. Set for a casual relay host, whose role byte arrives the
        #: moment it dials in -- possibly long before its opponent has even
        #: launched the game.
        self._peer_gate = False
        self._waiting_for_peer = False

    # ------------------------------------------------------------------
    def _status(self, text: str) -> None:
        """Say what this connection is waiting on, while it waits.

        The other callbacks are events and fire once each. Between them a
        session can sit for minutes with nothing to show -- a relay host is
        told it is the host the instant it dials in, and then waits on
        another person to launch their game. With nothing reporting that,
        the player is left watching one unchanging "connecting" line and
        reasonably concludes their client or the server has hung.

        Optional, so a caller with nowhere to put the news is not obliged to
        invent somewhere.
        """
        if self._on_status is None:
            return
        try:
            self._on_status(text)
        except Exception:
            log.exception("Status callback failed.")

    # ------------------------------------------------------------------
    @property
    def is_host(self) -> bool:
        """The host moves first, and only one end may be the host."""
        return self._is_host

    @property
    def is_spectator(self) -> bool:
        """Watching rather than fighting. Nothing this end sends is carried."""
        return self._is_spectator
    @property
    def connected(self) -> bool:
        return self._sock is not None and not self._stop.is_set()

    @property
    def peer_address(self) -> str:
        try:
            if self._sock is not None:
                host, port = self._sock.getpeername()[:2]
                return f"{host}:{port}"
        except Exception:
            pass
        return "unknown"

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def send(self, kind: str, **fields) -> bool:
        """Encode and transmit a message. False if the link is gone."""
        try:
            frame = protocol.encode(kind, **fields)
        except ProtocolError as exc:
            log.error("Refusing to send malformed %s: %s", kind, exc)
            return False

        # Tear-down happens outside the lock. ``_shutdown`` closes the socket
        # (which needs this same lock) and then invokes the disconnect
        # callback, so doing either from inside the ``with`` would deadlock
        # the moment a send failed — precisely when the connection has just
        # dropped and the player most needs to be told.
        failure: Exception | None = None
        with self._send_lock:
            sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall(frame)
            except (OSError, ssl.SSLError) as exc:
                failure = exc

        if failure is not None:
            self._shutdown(f"Connection lost while sending: {failure}")
            return False
        return True

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------
    def _begin_reading(self) -> None:
        self._thread = threading.Thread(target=self._read_loop, name="net-reader", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        self._last_heard = time.monotonic()
        self._last_keepalive = self._last_heard
        # A casual relay host is told it is the host the moment it dials in,
        # which may be minutes before its opponent has even launched the
        # game. Wait for the opponent's first frame before announcing the
        # connection, so the player is not told they are connected while
        # their opponent is still sitting on the menu.
        self._waiting_for_peer = self._peer_gate
        if not self._waiting_for_peer:
            try:
                self._on_connected()
            except Exception:
                log.exception("on_connected callback failed.")

        while not self._stop.is_set():
            try:
                body = self._next_frame()
                if body is None:
                    self._shutdown(
                        "Your opponent left before the match started."
                        if self._waiting_for_peer
                        else "The other player disconnected."
                    )
                    return
                self._last_heard = time.monotonic()
            except socket.timeout:
                # A poll expiry, not a disconnection. Loop back round so the
                # stop flag gets a look-in, then decide whether the quiet has
                # gone on long enough to call it.
                if not self._check_liveness():
                    return
                continue
            except ProtocolError as exc:
                self._shutdown(f"Protocol violation: {exc}")
                return
            except (OSError, ssl.SSLError) as exc:
                self._shutdown(f"Connection lost: {exc}")
                return

            if not self._limiter.allow():
                self._shutdown("The other player is flooding the connection.")
                return

            try:
                message = protocol.decode(body)
            except ProtocolError as exc:
                log.warning("Dropping invalid frame: %s", exc)
                continue

            if message["type"] == "ping":
                self.send("pong", nonce=message["nonce"])
                continue
            if message["type"] == "pong":
                continue

            if self._waiting_for_peer:
                self._waiting_for_peer = False
                try:
                    self._on_connected()
                except Exception:
                    log.exception("on_connected callback failed.")

            try:
                self._on_message(message)
            except Exception:
                log.exception("Message handler failed for %s.", message.get("type"))

    def _next_frame(self) -> bytes | None:
        """Return one complete frame body, or None at a clean end of stream.

        Bytes accumulate in :attr:`_rxbuf` and are only consumed once a whole
        frame has arrived. That is what makes the one-second read poll safe:
        a timeout can land in the middle of an incoming frame and nothing is
        lost, because the partial frame is still sitting in the buffer when
        we come back round. Consuming the header eagerly would desynchronise
        the stream the first time a large frame straddled a poll boundary.
        """
        while True:
            if len(self._rxbuf) >= protocol.HEADER_SIZE:
                length = protocol.read_length(bytes(self._rxbuf[: protocol.HEADER_SIZE]))
                if len(self._rxbuf) >= protocol.HEADER_SIZE + length:
                    del self._rxbuf[: protocol.HEADER_SIZE]
                    body = bytes(self._rxbuf[:length])
                    del self._rxbuf[:length]
                    return body

            chunk = self._sock.recv(4096)  # type: ignore[union-attr]
            if not chunk:
                return None
            self._rxbuf += chunk

    def _check_liveness(self) -> bool:
        """Ping an idle link, and give up on one that has gone silent.

        Returns False once the session has been shut down, so the reader
        loop knows to stop.
        """
        if self._stop.is_set():
            return False
        if self._waiting_for_peer:
            # The opponent has not arrived yet. The relay holds the room open
            # longer than the idle timeout, so neither ping nor give up here.
            return True
        now = time.monotonic()
        if now - self._last_heard > IDLE_TIMEOUT:
            self._shutdown("The other player stopped responding.")
            return False
        if now - self._last_keepalive >= KEEPALIVE_INTERVAL:
            self._last_keepalive = now
            if not self.send("ping", nonce=secrets.randbelow(2**31)):
                return False
        return True

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def _shutdown(self, reason: str) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._closed_reason = reason
        self._close_sockets()
        try:
            self._on_disconnected(reason)
        except Exception:
            log.exception("on_disconnected callback failed.")

    def _close_sockets(self) -> None:
        with self._send_lock:
            sock, self._sock = self._sock, None
        for candidate in (sock, self._listener):
            if candidate is None:
                continue
            try:
                candidate.close()
            except Exception:
                pass
        self._listener = None

    def close(self, reason: str = "You left the game.") -> None:
        if self._sock is not None and not self._stop.is_set():
            self.send("resign", reason=reason[:120])
        self._shutdown(reason)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)


class HostSession(NetSession):
    """Waits for one opponent, then serves the match."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._is_host = True

    def listen(
        self,
        port: int = DEFAULT_PORT,
        passphrase: str = "",
        *,
        secure: bool = True,
        host: str = "",
    ) -> None:
        """Bind and accept in the background. Raises on a bad bind.

        With ``secure=False`` the match runs over a plain socket with no
        passphrase — casual play, for two people who just want to fight.

        ``host`` is the local address to listen on. Empty means every one of
        them, which is the right default and the one to keep: a player should
        not have to work out which of their network cards their opponent will
        arrive on, and picking the wrong one is a match that never connects
        for a reason nothing on screen explains. It is offered because a
        machine with a VPN up has a real reason to say "the LAN card, not
        that one".
        """
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        if secure and len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise ValueError(
                f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters."
            )

        context = server_context(passphrase) if secure else None
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host or "0.0.0.0", port))
            listener.listen(1)
        except OSError:
            listener.close()
            raise
        listener.settimeout(1.0)
        self._listener = listener

        thread = threading.Thread(
            target=self._accept_loop,
            args=(context, secure),
            name="net-accept",
            daemon=True,
        )
        thread.start()

    def _accept_loop(self, context: ssl.SSLContext | None, secure: bool) -> None:
        deadline = time.monotonic() + 600.0  # stop waiting after ten minutes
        while not self._stop.is_set():
            if time.monotonic() > deadline:
                self._shutdown("Nobody connected. Waiting timed out.")
                return
            try:
                raw, address = self._listener.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._shutdown(f"Stopped listening: {exc}")
                return

            if secure:
                self._status("Someone connected. Checking the passphrase...")
            # One opponent only. Close the door behind them so a second
            # connection cannot queue up behind the first.
            try:
                self._listener.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._listener = None

            if not secure:
                # Casual play: the joiner is on the other end of this socket
                # already, so there is nothing more to wait for.
                raw.settimeout(READ_POLL)
                self._sock = raw
                log.info("Opponent connected from %s.", address[0])
                self._begin_reading()
                return

            raw.settimeout(HANDSHAKE_TIMEOUT)
            try:
                tls = context.wrap_socket(raw, server_side=True)  # type: ignore[union-attr]
            except (ssl.SSLError, OSError) as exc:
                raw.close()
                # A failed PSK handshake is the expected outcome for someone
                # who does not have the passphrase. Say so plainly to the
                # player and keep the detail for the log.
                log.warning("Handshake from %s failed: %s", address[0], exc)
                self._shutdown(
                    f"A connection from {address[0]} failed to authenticate. "
                    "Check that you both typed the same passphrase."
                )
                return

            tls.settimeout(READ_POLL)
            self._sock = tls
            log.info("Opponent connected from %s.", address[0])
            self._begin_reading()
            return


class JoinSession(NetSession):
    """Connects out to a host."""

    def connect(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        passphrase: str = "",
        *,
        secure: bool = True,
    ) -> None:
        """Dial the host in the background."""
        if not host:
            raise ValueError("A host address is required.")
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        if secure and len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise ValueError(
                f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters."
            )

        context = client_context(passphrase) if secure else None
        thread = threading.Thread(
            target=self._connect_once,
            args=(host, port, context, secure),
            name="net-connect",
            daemon=True,
        )
        thread.start()

    def _connect_once(
        self, host: str, port: int, context: ssl.SSLContext | None, secure: bool
    ) -> None:
        try:
            raw = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            self._shutdown(f"Could not reach {host} on port {port}: {exc}")
            return

        # Only worth saying when something follows it. Casual play is
        # connected the instant the socket is, and the announcement would be
        # spoken over by the one that says so.
        if secure:
            self._status("Reached them. Checking the passphrase...")
        if not secure:
            # Casual play: the host accepted us, so the match is on.
            raw.settimeout(READ_POLL)
            self._sock = raw
            log.info("Connected to %s:%s.", host, port)
            self._begin_reading()
            return

        raw.settimeout(HANDSHAKE_TIMEOUT)
        try:
            tls = context.wrap_socket(raw, server_hostname=None)
        except (ssl.SSLError, OSError) as exc:
            raw.close()
            self._shutdown(
                "Could not authenticate with the host. Check that you both "
                f"typed the same passphrase. ({exc})"
            )
            return

        tls.settimeout(READ_POLL)
        self._sock = tls
        log.info("Connected to %s:%s.", host, port)
        self._begin_reading()


class RelaySession(NetSession):
    """Joins a relay server, which assigns the host and joiner roles.

    Both players dial the same relay and send it a room token derived from
    their shared passphrase (see :func:`room_token`). The first player in a
    room is told they are the host, the second that they are the joiner, and
    the relay then pipes the two streams together byte for byte. The TLS
    handshake still runs end to end between the two players, so the relay —
    like any on-path observer — can see only ciphertext.

    With ``secure=False`` the room token comes from a *public* room code
    instead (see :func:`casual_token`) and there is no TLS at all: the relay
    splices the plain sockets. The relay itself never knows which mode a
    room is in, because it only ever sees an opaque 16-byte token.

    One relay carries as many matches at once as it has rooms, and the rooms
    do not touch: a match in progress is unaffected by anyone else arriving,
    starting, or finishing. The single thing a room cannot do is hold three
    people, so a third player dialling in on a key that already has two is
    told to pick another one -- and only that player is told anything.

    From this class outward the session is exactly a
    :class:`HostSession` or :class:`JoinSession`; which one depends on the
    role byte, which is why ``is_host`` is set here rather than by the
    caller.
    """

    def connect_relay(
        self,
        host: str,
        port: int,
        passphrase: str,
        *,
        secure: bool = True,
        ringside: bool = False,
    ) -> None:
        """Dial the relay in the background and take whatever role it gives.

        ``secure=False`` is casual play: ``passphrase`` then holds a *public*
        room code (see :func:`casual_token`) and the match runs over a plain
        socket.

        ``ringside`` asks the relay to keep seats for onlookers. Only the
        first arrival's answer counts, because by the time anyone else turns
        up the question has been settled. It is refused outright alongside
        ``secure``: a seat cannot watch an encrypted match, since TLS is a
        conversation between two ends and there is no third place to stand.
        """
        if ringside and secure:
            raise ValueError("An encrypted match cannot be watched.")
        if not host:
            raise ValueError("A relay server address is required.")
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        if secure and len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise ValueError(
                f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters."
            )

        thread = threading.Thread(
            target=self._relay_once,
            args=(host, port, passphrase, secure, ringside),
            name="net-relay",
            daemon=True,
        )
        thread.start()

    def _relay_once(
        self, host: str, port: int, passphrase: str, secure: bool, ringside: bool = False
    ) -> None:
        try:
            raw = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            self._shutdown(f"Could not reach the relay server {host} on port {port}: {exc}")
            return
        try:
            raw.settimeout(HANDSHAKE_TIMEOUT)
            try:
                raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            token = room_token(passphrase) if secure else casual_token(passphrase)
            options = OPTION_RINGSIDE if ringside else 0
            raw.sendall(token + bytes([options]))
            role = self._read_role(raw)
        except (socket.timeout, OSError) as exc:
            raw.close()
            self._shutdown(
                f"The relay server {host} did not pair you with an opponent: {exc}"
            )
            return

        if role == ROLE_FULL:
            raw.close()
            key = "passphrase" if secure else "room code"
            # The server is not busy and the match is not over: one specific
            # room is occupied, and every other room on the same server is
            # still free. Saying so is the difference between "try again
            # later" and "change one field and go", which is why the old
            # wording -- which read like the server was full -- is gone.
            self._shutdown(
                f"Two players are already using that {key}. Pick a different "
                f"one; the server is fine, that room is busy."
            )
            return
        if role == ROLE_SPECTATOR:
            # The room already had its two fighters and kept a seat for us.
            raw.settimeout(READ_POLL)
            self._is_spectator = True
            self._sock = raw
            self._status("You have a ringside seat. Waiting for the fight.")
            log.info("Relay %s:%s gave us a ringside seat.", host, port)
            self._begin_watching()
            return

        if role not in (ROLE_HOST, ROLE_JOINER):
            raw.close()
            self._shutdown(
                f"The relay server {host} answered unexpectedly. Try another server."
            )
            return

        self._is_host = role == ROLE_HOST
        # The point at which the old dialog stopped telling the truth. From
        # here the relay has done its part and the wait is on a person, so
        # the wait is named and bounded rather than left looking like a
        # connection that never completes.
        if self._is_host:
            minutes = int(ROOM_WAIT_TIMEOUT // 60)
            self._status(
                "Connected as the host. Waiting for your opponent to join, "
                f"for up to {minutes} minutes."
            )
        else:
            self._status("Connected as the joiner. Starting the match...")

        if not secure:
            # Casual play: the relay has already paired the room, so the
            # socket is live. The host's role byte may have arrived long
            # before its opponent launched the game, so hold back
            # "connected" until the opponent's first frame turns up.
            raw.settimeout(READ_POLL)
            self._sock = raw
            self._peer_gate = self._is_host
            log.info(
                "Relay %s:%s assigned role %s.",
                host, port, "host" if self._is_host else "joiner",
            )
            self._begin_reading()
            return

        # The host starts its server-side handshake the moment it is told it
        # is the host, while the joiner may still be launching the client --
        # so the wait is for a person, and is bounded by the relay's pairing
        # window, not by the crypto handshake timeout that governs the quick
        # token exchange above.
        raw.settimeout(RELAY_HANDSHAKE_TIMEOUT)
        try:
            if self._is_host:
                tls = server_context(passphrase).wrap_socket(raw, server_side=True)
            else:
                tls = client_context(passphrase).wrap_socket(raw, server_hostname=None)
        except ssl.SSLError:
            raw.close()
            self._shutdown(
                "Could not authenticate with your opponent. Check that you both "
                "typed the same passphrase."
            )
            return
        except OSError as exc:
            raw.close()
            self._shutdown(f"Connection lost while authenticating with your opponent: {exc}")
            return

        tls.settimeout(READ_POLL)
        self._sock = tls
        log.info(
            "Relay %s:%s assigned role %s.",
            host, port, "host" if self._is_host else "joiner",
        )
        self._begin_reading()

    def _begin_watching(self) -> None:
        thread = threading.Thread(target=self._watch_loop, name="net-ringside", daemon=True)
        self._thread = thread
        thread.start()

    def _watch_loop(self) -> None:
        """Unpick a seat's stream into the two fights it is carrying.

        A player's socket carries one conversation. A seat's carries two,
        shuffled together in whatever order the relay happened to read them,
        so each chunk arrives tagged with the player it came from. Sorting
        them into a buffer each and framing those buffers separately is what
        turns the pile back into two people taking turns.

        Every message goes out with a ``source`` of ``host`` or ``joiner``,
        because for a watcher that is the whole point: neither of them is
        "you", and a strike with nobody attached to it is not a strike.
        """
        try:
            self._on_connected()
        except Exception:
            log.exception("on_connected callback failed.")

        header, buffers = bytearray(), {ROLE_HOST: bytearray(), ROLE_JOINER: bytearray()}
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(8192)  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError as exc:
                self._shutdown(f"The ringside connection dropped: {exc}")
                return
            if not chunk:
                self._shutdown("The fight is over and the ringside has emptied.")
                return

            header += chunk
            while True:
                if len(header) < _SEAT_HEADER.size:
                    break
                tag, length = _SEAT_HEADER.unpack(header[: _SEAT_HEADER.size])
                if length > protocol.MAX_FRAME_SIZE * 4:
                    self._shutdown("The relay sent a ringside chunk of an absurd size.")
                    return
                if len(header) < _SEAT_HEADER.size + length:
                    break
                body = bytes(header[_SEAT_HEADER.size : _SEAT_HEADER.size + length])
                del header[: _SEAT_HEADER.size + length]
                if tag in buffers:
                    buffers[tag] += body
                    self._deliver(tag, buffers[tag])

    def _deliver(self, tag: bytes, buffer: bytearray) -> None:
        """Pull whole game frames out of one player's buffer and hand them on."""
        source = "host" if tag == ROLE_HOST else "joiner"
        while len(buffer) >= protocol.HEADER_SIZE:
            try:
                length = protocol.read_length(bytes(buffer[: protocol.HEADER_SIZE]))
            except ProtocolError as exc:
                self._shutdown(f"The fight sent something unreadable: {exc}")
                return
            if len(buffer) < protocol.HEADER_SIZE + length:
                return
            body = bytes(buffer[protocol.HEADER_SIZE : protocol.HEADER_SIZE + length])
            del buffer[: protocol.HEADER_SIZE + length]
            try:
                message = protocol.decode(body)
            except ProtocolError as exc:
                # One unreadable message from a fighter is not worth
                # emptying the seat over; skip it and keep watching.
                log.warning("Ignoring a ringside message: %s", exc)
                continue
            if message["type"] in ("ping", "pong"):
                continue
            message["source"] = source
            try:
                self._on_message(message)
            except Exception:
                log.exception("Ringside handler failed for %s.", message.get("type"))

    @staticmethod
    def _read_role(sock: socket.socket) -> bytes:
        data = sock.recv(1)
        return data or b""


# ----------------------------------------------------------------------
# Address discovery
# ----------------------------------------------------------------------
def local_addresses() -> list[str]:
    """Best-effort list of this machine's LAN addresses, for the host to read out.

    Deliberately local-only. The original fetched the player's external IP
    from a third-party web service on startup; that is an unnecessary
    outbound call that tells someone else you are playing, so it is gone. A
    player on the open internet can get their external address from any
    source they already trust.
    """
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No packets are sent; this just asks the routing table which
            # interface would be used to reach the outside world.
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass

    return found or ["127.0.0.1"]
