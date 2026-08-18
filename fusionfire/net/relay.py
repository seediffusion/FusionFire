"""The relay wire protocol, as the game must agree on it.

The relay itself is a standalone server — ``srv.py`` at the repository root,
which a server operator can run with a plain Python install and no game code.
This module exists so the *game* and the standalone server cannot silently
disagree about the protocol between them. Every constant here is duplicated
by ``srv.py``, and a test pins the two copies together; change either and the
test says so.

The protocol is a dumb byte-forwarder:

Two players dial the same relay server. Each connection opens with a sixteen
byte *room token* — derived from the shared passphrase (or, in casual play,
from a public room code; see :mod:`fusionfire.net.session`), so the players
who land in the same room are the players who share the same key — and once
two are in a room the relay connects their streams byte for byte and gets out
of the way. That is all it does.

The TLS 1.3 handshake runs between the two *players*, through the relay, so
the relay only ever sees ciphertext. It has exactly the same end-to-end
passphrase encryption and mutual authentication as direct peer to peer; the
difference is that nobody needs to forward a port or know a public address.
The relay is trusted for availability, never for privacy: it can drop a match
it does not like, but it cannot read one and cannot join it without the
passphrase. It never needs to know which mode a room is in, because in both
cases it only ever sees the opaque token.

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
"""

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
