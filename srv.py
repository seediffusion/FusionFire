#!/usr/bin/env python3
"""Run a Fusion Fire relay server: ``python srv.py <name> <port> [-P]``.

This script is deliberately standalone. It is the server side of the game's
relay mode and nothing else: it imports no game code and needs no game
dependencies, so it runs on any machine with a plain Python 3.13+ — a server
operator does not need to copy the game to run it.

Two players dial the same relay server. Each connection opens with a sixteen
byte *room token* — derived from the shared passphrase, so the players who
land in the same room are the players who share one — and once two are in a
room the relay connects their streams byte for byte and gets out of the way.
That is all it does.

The TLS 1.3 handshake runs between the two *players*, through the relay, so
the relay only ever sees ciphertext. It has exactly the same end-to-end
passphrase encryption and mutual authentication as direct peer to peer; the
difference is that nobody needs to forward a port or know a public address.
The relay is trusted for availability, never for privacy: it can drop a match
it does not like, but it cannot read one and cannot join it without the
passphrase.

The first player to join a room is the host. They move first, and their
session completes the server side of the handshake; the second player is the
joiner. This is decided by arrival order alone, which is the whole point of
the mode — neither player needs to have been told an address to listen on.

Wire protocol (before the splice, all plain bytes):
    ``client -> relay`` 16 bytes of room token
    ``relay -> client`` one byte: ``H`` host, ``J`` joiner, ``F`` room full

Everything after that byte is forwarded unchanged. The relay has nothing to
inspect: there is no schema, no framing, no size check, because the bytes are
the other player's problem, and the game's own frame checks protect them.

Usage
-----
::

    python srv.py relay.example.org 6001

``relay.example.org`` is the address players dial, so give it one they can
reach. Publicize the server to a relay spy service so players can find it::

    python srv.py relay.example.org 6001 -P

``-P`` announces the server to a relay spy service. The address of the
service is taken from the ``FUSION_FIRE_SPY_URL`` environment variable, or
failing that the address set in the game itself (Settings, Online page),
which is stored in the game's settings file. On a machine that has neither —
say, a server that only ever runs this script — give the address straight to
``-P``::

    python srv.py relay.example.org 6001 -P https://spy.example.org/servers

The spy list shows ``<name>`` as the label and advertises it as the address
players dial. If you want a label that is not itself dialable — a name like
``test`` instead of an address — give the dialable address separately with
``-A``/``--public-host``::

    python srv.py test 6001 -A fusion.seedy.cc -P https://spy.example.org/servers

Players who pick the entry from the list are then pointed at
``fusion.seedy.cc:6001`` instead of ``test:6001``.

The reference spy service is the equally standalone ``spy.py``; see its
docstring. A relay that stops being announced falls out of a spy's list on
its own, so publicizing is how a server advertises that it is alive.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

#: The room token is 128 bits of scrypt output. Long enough that guessing one
#: is as hard as guessing the passphrase it came from, which is the correct
#: bar: a relay that sees the token learns nothing it could not learn from the
#: TLS handshake transcript it also sees.
ROOM_TOKEN_LENGTH = 16
RELAY_DEFAULT_PORT = 6001

#: How long a client has to send its room token before being dropped.
TOKEN_TIMEOUT = 20.0
#: How long the first player of a room waits for an opponent.
ROOM_WAIT_TIMEOUT = 600.0
#: A room full of strangers takes at most this many bytes to reject.
ROLE_HOST = b"H"
ROLE_JOINER = b"J"
ROLE_FULL = b"F"

#: Bound on simultaneously open rooms, so a flood of distinct tokens cannot
#: grow the table forever. Each room is one match, so this is plenty.
MAX_ROOMS = 2048

#: How long one announcement to the spy service may take.
FETCH_TIMEOUT = 10.0
#: How often a publicized server re-announces itself, so it falls out of the
#: spy's list only after genuinely going quiet.
PUBLICATION_INTERVAL = 300.0


class _Room:
    """The two sockets of one match, waiting to be spliced together."""

    def __init__(self, token: bytes) -> None:
        self.token = token
        self.lock = threading.Lock()
        self.host: socket.socket | None = None
        self.joiner: socket.socket | None = None
        self.host_ready = threading.Event()
        self.spliced = False
        self.alive = True

    def register(self, conn: socket.socket) -> bytes:
        """Claim a socket. Returns the role byte, or ``ROLE_FULL``."""
        with self.lock:
            if not self.alive:
                return b""
            if self.host is None:
                self.host = conn
                return ROLE_HOST
            if self.joiner is None:
                self.joiner = conn
                self.host_ready.set()
                return ROLE_JOINER
            return ROLE_FULL

    def abandon(self, conn: socket.socket) -> bool:
        """Drop a host that is still waiting. True if the room is now dead."""
        with self.lock:
            if self.alive and self.host is conn and self.joiner is None:
                self.alive = False
                self.host = None
                return True
            return False

    def mark_done(self) -> bool:
        """Close both sockets. True if this is the call that ends the room."""
        with self.lock:
            if not self.alive:
                return False
            self.alive = False
            sockets = (self.host, self.joiner)
            self.host = self.joiner = None
        for sock in sockets:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        return True


class RelayServer:
    """Pairs two players per room and splices their connections.

    Run with :meth:`serve_forever` on the main thread, or :meth:`start` to
    serve in a daemon thread. :meth:`stop` tears the listener down and closes
    every open room.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = RELAY_DEFAULT_PORT,
        *,
        room_wait: float = ROOM_WAIT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.room_wait = room_wait
        self._rooms: dict[bytes, _Room] = {}
        self._rooms_lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    @property
    def bound_port(self) -> int | None:
        """The port actually bound, which differs from ``port`` if it was 0."""
        if self._listener is None:
            return None
        return self._listener.getsockname()[1]

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)

    def start(self) -> None:
        """Serve in a background daemon thread."""
        self._thread = threading.Thread(
            target=self.serve_forever, name="relay-server", daemon=True
        )
        self._thread.start()

    def serve_forever(self) -> None:
        """Bind, then accept and pair clients until :meth:`stop` is called."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(16)
        listener.settimeout(1.0)
        self._listener = listener
        self._ready.set()
        log.info("Relay server listening on %s:%s.", self.host, self.bound_port)

        try:
            while not self._stop.is_set():
                try:
                    conn, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self._stop.is_set():
                        log.exception("Accept failed.")
                    break
                thread = threading.Thread(
                    target=self._handle,
                    args=(conn, address),
                    name="relay-client",
                    daemon=True,
                )
                thread.start()
        finally:
            self._cleanup_all()

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        self._cleanup_all()

    # ------------------------------------------------------------------
    # Per-connection handling
    # ------------------------------------------------------------------
    def _handle(self, conn: socket.socket, address: tuple) -> None:
        room: _Room | None = None
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            token = self._read_token(conn)
            if token is None:
                log.info("Client %s left before its room token.", address[0])
                conn.close()
                return
            room = self._room_for(token)
            if room is None:
                conn.close()
                return

            role = room.register(conn)
            if role in (b"", ROLE_FULL):
                try:
                    if role == ROLE_FULL:
                        conn.sendall(ROLE_FULL)
                except OSError:
                    pass
                conn.close()
                room.abandon(conn)
                self._forget(room)
                return

            try:
                conn.sendall(role)
            except OSError:
                conn.close()
                room.abandon(conn)
                self._forget(room)
                return

            if role == ROLE_HOST:
                if not self._wait_for_joiner(room, conn):
                    return  # timed out or the socket died; room cleaned up
            # The joiner completes the room. Whichever side runs this first
            # starts the pumps; _splice is guarded so only one pair starts.
            self._splice(room)
        except OSError as exc:
            log.debug("Relay client error: %s", exc)
            try:
                conn.close()
            except OSError:
                pass
            if room is not None:
                room.abandon(conn)
                self._forget(room)

    def _read_token(self, conn: socket.socket) -> bytes | None:
        conn.settimeout(TOKEN_TIMEOUT)
        buf = bytearray()
        while len(buf) < ROOM_TOKEN_LENGTH:
            chunk = conn.recv(ROOM_TOKEN_LENGTH - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _wait_for_joiner(self, room: _Room, conn: socket.socket) -> bool:
        """Wait for the second player, watching for the host going away.

        Returns False once the room has been cleaned up. The host socket is
        polled for EOF so a host that vanishes does not leave a dead room
        waiting out the whole timeout for its opponent to wander in.
        """
        deadline = time.monotonic() + self.room_wait
        conn.setblocking(False)
        try:
            while not room.host_ready.wait(1.0):
                if time.monotonic() > deadline:
                    room.abandon(conn)
                    conn.close()
                    self._forget(room)
                    return False
                if self._peer_gone(conn):
                    room.abandon(conn)
                    conn.close()
                    self._forget(room)
                    return False
        finally:
            conn.setblocking(True)
        return True

    @staticmethod
    def _peer_gone(conn: socket.socket) -> bool:
        try:
            return conn.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return True

    def _splice(self, room: _Room) -> None:
        """Start the two pump threads that forward bytes between the players."""
        with room.lock:
            if room.spliced or not room.alive:
                return
            room.spliced = True
            host, joiner = room.host, room.joiner
        if host is None or joiner is None:
            return
        # The token read left the 20-second TOKEN_TIMEOUT on these sockets.
        # A room that goes quiet while a player thinks would then be torn
        # down between keepalives; from here the pumps block and the
        # players' own keepalive protocol decides liveness.
        for sock in (host, joiner):
            try:
                sock.settimeout(None)
            except OSError:
                pass
        for src, dst in ((host, joiner), (joiner, host)):
            thread = threading.Thread(
                target=self._pump, args=(src, dst, room), name="relay-pump", daemon=True
            )
            thread.start()

    def _pump(self, src: socket.socket, dst: socket.socket, room: _Room) -> None:
        try:
            while True:
                chunk = src.recv(4096)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            room.mark_done()
            self._forget(room)

    # ------------------------------------------------------------------
    # Room table
    # ------------------------------------------------------------------
    def _room_for(self, token: bytes) -> _Room | None:
        with self._rooms_lock:
            room = self._rooms.get(token)
            if room is None:
                if len(self._rooms) >= MAX_ROOMS:
                    log.warning("Relay room table is full; refusing a new room.")
                    return None
                room = _Room(token)
                self._rooms[token] = room
            return room

    def _forget(self, room: _Room) -> None:
        with self._rooms_lock:
            if self._rooms.get(room.token) is room and not room.alive:
                del self._rooms[room.token]

    def _cleanup_all(self) -> None:
        with self._rooms_lock:
            rooms = list(self._rooms.values())
            self._rooms.clear()
        for room in rooms:
            room.mark_done()


# ----------------------------------------------------------------------
# Publicizing to a relay spy service
# ----------------------------------------------------------------------
def config_file() -> Path:
    """Where the game keeps its settings file, mirroring the game itself.

    ``srv.py -P`` reads the relay spy address from here when the
    ``FUSION_FIRE_SPY_URL`` environment variable is not set, so a server
    machine that has never run the game can still be configured, and one that
    has can reuse what the player already set under Settings, Online page.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "FusionFire" / "settings.json"


def spy_url_from_config() -> str:
    """The spy service an operator wants to publicize to.

    ``FUSION_FIRE_SPY_URL`` wins, so a server machine that has never run the
    game can still be told where its spy lives; otherwise the address set in
    the game itself (Settings, Online page) is used. Empty means neither is
    configured.
    """
    env = os.environ.get("FUSION_FIRE_SPY_URL", "").strip()
    if env:
        return env
    try:
        raw = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    url = raw.get("relay_spy_url", "")
    return url.strip() if isinstance(url, str) else ""


class Publicizer:
    """Announces one relay server to a spy service, on a schedule.

    The announcement is repeated every ``interval`` seconds so the spy can
    expire entries whose operator has gone away. A failure to publicize is
    logged and retried; it must not take the relay down with it.
    """

    def __init__(
        self,
        spy_url: str,
        *,
        name: str,
        host: str,
        port: int,
        note: str = "",
        interval: float = PUBLICATION_INTERVAL,
    ) -> None:
        self.spy_url = spy_url
        self.name = name
        self.host = host
        self.port = port
        self.note = note
        self.interval = max(10.0, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="relay-publicizer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        self.announce()
        while not self._stop.wait(self.interval):
            self.announce()

    def announce(self) -> None:
        payload = json.dumps(
            {"name": self.name, "host": self.host, "port": self.port, "note": self.note}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.spy_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT):
                pass
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("Could not publicize to %s: %s", self.spy_url, exc)


# ----------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="srv.py",
        description=(
            "A Fusion Fire relay server. Pairs two players who share a "
            "passphrase and forwards their encrypted traffic byte for byte. "
            "Players dial <name>, so give it an address they can reach."
        ),
    )
    parser.add_argument(
        "name",
        help="the address players dial, e.g. relay.example.org or a public IP",
    )
    parser.add_argument("port", type=int, help="the port to listen on")
    parser.add_argument(
        "-A",
        "--public-host",
        metavar="ADDRESS",
        help=(
            "the address players dial, as shown in the spy list. Defaults to "
            "<name>, so a name that is just a label (e.g. 'test') needs this "
            "to point players at the address they can actually reach"
        ),
    )
    parser.add_argument(
        "-P",
        "--publicize",
        nargs="?",
        const="",
        default=None,
        metavar="SPY_URL",
        help=(
            "announce this server to a relay spy service so players can find "
            "it. SPY_URL, when given, is the address to announce to; "
            "otherwise the FUSION_FIRE_SPY_URL environment variable or the "
            "relay spy address set in the game's settings is used"
        ),
    )
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    name = args.name.strip()
    if not name or any(ch.isspace() for ch in name):
        parser.error("name must be an address players can dial, without spaces")
    public_host = args.public_host.strip() if args.public_host else ""
    if public_host and any(ch.isspace() for ch in public_host):
        parser.error("--public-host must be an address players can dial, without spaces")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    server = RelayServer("0.0.0.0", args.port)
    publicizer = None
    if args.publicize is not None:
        spy_url = args.publicize.strip() if args.publicize else spy_url_from_config()
        if not spy_url:
            log.error(
                "No relay spy service is configured. Give one to -P "
                "(e.g. srv.py <name> <port> -P https://spy.example.org/servers), "
                "or set the FUSION_FIRE_SPY_URL environment variable. "
                "(The address set in the game's own settings, under "
                "Settings > Online, is used when neither is present.)"
            )
            return 1
        dial = public_host or name
        publicizer = Publicizer(
            spy_url=spy_url,
            name=name,
            host=dial,
            port=args.port,
        )
        publicizer.start()
        log.info("Publicizing %s:%s to %s.", dial, args.port, spy_url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        server.stop()
        if publicizer is not None:
            publicizer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
