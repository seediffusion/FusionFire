#!/usr/bin/env python3
"""Run the reference relay spy service: ``python spy.py <port>``.

This script is deliberately standalone. It imports no game code and needs no
game dependencies, so it runs on any machine with a plain Python 3.13+ — a
server operator does not need to copy the game to run it.

A relay spy service is a tiny HTTP service that holds a list of public relay
servers. The game fetches that list so a player can pick a server to connect
to without being told an address; relay operators post their server to it
(``srv.py ... -P``) so it shows up there.

Nothing about the list is trusted, because the game's side of the fetch is
the only side that cannot be assumed to behave:

* every field is validated on arrival, so a broken or hostile spy cannot slip
  a path, a control character or an absurd length into a field;
* hosts are limited to the characters an address can contain;
* ports must be in range;
* the number of entries is capped;
* the fetch is bounded by a timeout.

The security story is unchanged either way. A relay sees only the TLS
ciphertext flowing between two players, so even a spy service that points a
player at a hostile relay cannot read a match — it can only waste someone's
time. See ``srv.py``.

Usage
-----
::

    python spy.py 6002

The port is optional and defaults to 6002; the service binds all interfaces.
It stores nothing on disk; entries are held in memory and expire after two
hours. Relay operators re-publicize every few minutes (``srv.py ... -P``) and
a server that stops announcing falls out of the list on its own.

To serve the list over HTTPS instead of plain HTTP — so that neither the
addresses nor anything else leaks on the wire, and so players can trust the
list comes from you — give ``--ssl-cert`` and ``--ssl-key`` a PEM
certificate and its private key (a Let's Encrypt pair, for example)::

    python spy.py 6002 --ssl-cert fullchain.pem --ssl-key privkey.pem

The game and ``srv.py -P`` then talk to it over ``https://``. Both arguments
are required together; without them the service serves plain HTTP.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

SPY_DEFAULT_PORT = 6002

#: How long one announcement may take.
FETCH_TIMEOUT = 10.0
#: The most servers a spy is allowed to report. Cap against a bloated or
#: hostile reply rather than a realistic public list.
MAX_SERVERS = 200
#: A publicized entry disappears from the reference spy after this long if
#: its operator stops re-announcing it.
PUBLICATION_TTL = 2 * 60 * 60
#: Room a POST body may occupy.
MAX_PUBLICATION_BODY = 8 * 1024
#: The longest a server label or note may grow to.
MAX_NAME_LENGTH = 40

#: Human-sayable label used when a spy returns no name for a server.
UNNAMED = "Publicized relay server"


class SpyError(Exception):
    """A spy service was unreachable or answered with something invalid."""


@dataclass(frozen=True)
class PublicizedServer:
    """One entry in a spy service's list."""

    name: str
    host: str
    port: int
    note: str = ""


# ----------------------------------------------------------------------
# Validation, the same rules the game applies to its own settings.
# ----------------------------------------------------------------------
def _as_int(value: object, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_host(value: object) -> str:
    if not isinstance(value, str):
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-[]")
    return "".join(c for c in value.strip() if c in allowed)[:255]


def sanitise_name(value: object) -> str:
    """Reduce a name to printable characters and a sane length."""
    if not isinstance(value, str):
        return ""
    cleaned = []
    for ch in value:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            continue
        # Bidi/format controls — invisible in a name, confusing in output.
        if 0x200B <= code <= 0x200F or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069:
            continue
        cleaned.append(ch)
    return " ".join("".join(cleaned).split())[:MAX_NAME_LENGTH]


def _server_from(entry: object) -> PublicizedServer | None:
    """Build one server from a raw JSON entry, or reject it."""
    if not isinstance(entry, dict):
        return None
    name = sanitise_name(entry.get("name", ""))
    host = _as_host(entry.get("host", ""))
    port = _as_int(entry.get("port", 0), 0)
    note = entry.get("note", "")
    if not isinstance(note, str):
        note = ""
    note = "".join(
        ch for ch in note if ch == " " or (ch.isprintable() and not ch.isspace())
    ).strip()[:200]
    if not host or not 1 <= port <= 65535:
        return None
    return PublicizedServer(name=name or UNNAMED, host=host, port=port, note=note)


# ----------------------------------------------------------------------
# The in-memory list shared by every handler thread.
# ----------------------------------------------------------------------
class _SpyState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        #: keyed by (host, port); value is (entry, expires_at).
        self.servers: dict[tuple[str, int], tuple[PublicizedServer, float]] = {}

    def register(self, entry: PublicizedServer) -> None:
        with self.lock:
            self.servers[(entry.host, entry.port)] = (
                entry, time.monotonic() + PUBLICATION_TTL,
            )

    def remove(self, host: str, port: int) -> None:
        with self.lock:
            self.servers.pop((host, port), None)

    def list(self) -> list[PublicizedServer]:
        now = time.monotonic()
        with self.lock:
            alive = {
                key: (entry, expires)
                for key, (entry, expires) in self.servers.items()
                if expires > now
            }
            self.servers = alive
            return [entry for entry, _ in sorted(alive.values(), key=lambda pair: pair[0].name)]


_STATE = _SpyState()


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FusionFireRelaySpy/1.0"

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if urllib.parse.urlparse(self.path).path.rstrip("/") in ("", "/servers"):
            self._send_json(200, {"servers": [asdict(s) for s in _STATE.list()]})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        if urllib.parse.urlparse(self.path).path.rstrip("/") not in ("", "/servers"):
            self._send_json(404, {"error": "not found"})
            return
        length = self.headers.get("Content-Length")
        try:
            length = min(int(length or 0), MAX_PUBLICATION_BODY)
        except ValueError:
            self._send_json(400, {"error": "no content length"})
            return
        body = self.rfile.read(length)
        try:
            raw = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return
        entry = _server_from(raw)
        if entry is None:
            self._send_json(400, {"error": "a name, host and valid port are required"})
            return
        _STATE.register(entry)
        log.info("Registered relay server %s:%s (%s).", entry.host, entry.port, entry.name)
        self._send_json(200, {"ok": True})

    def do_DELETE(self) -> None:  # noqa: N802 - http.server naming
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        host = _as_host(query.get("host", [""])[0])
        port = _as_int(query.get("port", ["0"])[0], 0)
        if host and 1 <= port <= 65535:
            _STATE.remove(host, port)
        self._send_json(200, {"ok": True})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.info("%s - %s", self.address_string(), format % args)


class _TLSServer(http.server.ThreadingHTTPServer):
    """A ThreadingHTTPServer that wraps every accepted socket in TLS.

    The TLS handshake runs up front, so a client that cannot complete it —
    a plain HTTP probe, say — fails before it reaches the handler at all.
    """

    def __init__(self, server_address, handler_class, ssl_context: ssl.SSLContext) -> None:
        self._ssl_context = ssl_context
        super().__init__(server_address, handler_class)

    def get_request(self):
        sock, address = super().get_request()
        try:
            return self._ssl_context.wrap_socket(sock, server_side=True), address
        except Exception:
            sock.close()
            raise

    def handle_error(self, request, client_address):
        # A plaintext probe fails the TLS handshake before it reaches the
        # handler; log it quietly instead of dumping a traceback.
        log.debug("TLS handshake failed from %s.", client_address[0])


class SpyServer:
    """The reference relay spy service, as a reusable class for tests.

    Serve HTTPS by passing a PEM ``cert_file`` and its ``key_file``; without
    both, the service serves plain HTTP.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = SPY_DEFAULT_PORT,
        *,
        cert_file: str = "",
        key_file: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.cert_file = cert_file
        self.key_file = key_file
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int | None:
        if self._httpd is None:
            return None
        return self._httpd.server_address[1]

    @property
    def scheme(self) -> str:
        return "https" if self.cert_file and self.key_file else "http"

    def start(self) -> None:
        if self.cert_file and self.key_file:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert_file, self.key_file)
            self._httpd = _TLSServer((self.host, self.port), _Handler, context)
        else:
            self._httpd = http.server.ThreadingHTTPServer((self.host, self.port), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="relay-spy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.bound_port}/servers"


# ----------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spy.py",
        description=(
            "The reference Fusion Fire relay spy service: publishes the list "
            "of publicized relay servers that the game's Online dialog offers."
        ),
    )
    parser.add_argument(
        "port",
        type=int,
        nargs="?",
        default=SPY_DEFAULT_PORT,
        help=f"the port to listen on (default: {SPY_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--ssl-cert",
        metavar="CERT",
        help="serve the list over HTTPS with this PEM certificate; requires --ssl-key",
    )
    parser.add_argument(
        "--ssl-key",
        metavar="KEY",
        help="the private key for --ssl-cert (PEM)",
    )
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if bool(args.ssl_cert) != bool(args.ssl_key):
        parser.error("--ssl-cert and --ssl-key must be given together")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = SpyServer(
        "0.0.0.0",
        args.port,
        cert_file=args.ssl_cert or "",
        key_file=args.ssl_key or "",
    )
    try:
        server.start()
    except (OSError, ssl.SSLError) as exc:
        log.error("Could not start the relay spy%s: %s", f" ({server.scheme})" if server.scheme == "https" else "", exc)
        return 1
    log.info("Relay spy listening on %s://0.0.0.0:%s.", server.scheme, server.bound_port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
