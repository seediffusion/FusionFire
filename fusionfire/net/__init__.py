"""Online play.

The original spoke plaintext over a raw TCP socket on port 6000, with no
authentication of any kind: whoever connected first was your opponent, and
anyone on the path could read or rewrite the match. This package replaces
that with an authenticated, encrypted channel and a validated message
schema. See :mod:`fusionfire.net.session` for the threat model.
"""

from .protocol import ProtocolError, decode, encode
from .session import HostSession, JoinSession, NetSession, RelaySession

__all__ = [
    "ProtocolError",
    "decode",
    "encode",
    "HostSession",
    "JoinSession",
    "NetSession",
    "RelaySession",
]
