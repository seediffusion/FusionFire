"""Relay spy: the game's side of finding publicized relay servers.

The spy *service* itself is a standalone server — ``spy.py`` at the repository
root, which a server operator can run with a plain Python install and no game
code. This module is what the game needs of a spy: fetch and validate the list
of publicized relay servers, so the Online dialog can offer them to a player.

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
time. See :mod:`fusionfire.net.relay`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..config import _as_host, _as_int, sanitise_name

#: How long one fetch may take before it is abandoned.
FETCH_TIMEOUT = 10.0
#: The most servers a spy is allowed to report. Cap against a bloated or
#: hostile reply rather than a realistic public list.
MAX_SERVERS = 200
#: Room a spy reply or announcement body may occupy.
MAX_PUBLICATION_BODY = 8 * 1024

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


def fetch_servers(spy_url: str, timeout: float = FETCH_TIMEOUT) -> list[PublicizedServer]:
    """Download and validate the list of publicized relay servers.

    Accepts either a bare JSON array of servers or ``{"servers": [...]}``.
    Malformed entries are dropped, not fatal; a reply that is neither shape
    is an error.
    """
    if not isinstance(spy_url, str) or not spy_url.strip():
        raise SpyError("A relay spy service address is required.")
    parsed = urllib.parse.urlparse(spy_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SpyError("The relay spy service address must be an http or https URL.")

    request = urllib.request.Request(spy_url.strip(), headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_PUBLICATION_BODY * 8)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SpyError(f"Could not fetch the server list: {exc}") from exc

    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpyError(f"The server list was not valid JSON: {exc}") from exc

    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("servers")
    else:
        entries = None
    if not isinstance(entries, list):
        raise SpyError("The server list was neither a list nor an object with a 'servers' list.")

    servers = [
        entry for entry in (_server_from(entry) for entry in entries) if entry is not None
    ]
    return servers[:MAX_SERVERS]
