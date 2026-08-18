"""Wire format and message schema.

Frames are a four-byte big-endian length followed by that many bytes of
UTF-8 JSON. Two rules make this safe to point at the internet:

* **The length is checked before the allocation.** A peer cannot make us
  reserve a gigabyte by claiming a gigabyte is coming.
* **Every field is validated on arrival**, against a table that says what
  type it is and what values are legal. Nothing is unpacked into an object
  graph, nothing is executed, and JSON is the only encoding — there is no
  ``pickle`` anywhere in this package.

Damage values are range-checked against the same constants the local engine
uses, so a peer running modified code still cannot report a 9,000-point
gunshot. That is the honest limit of a peer-to-peer design with no server:
the channel is authenticated and the numbers are bounded, but the peer still
rolls its own dice. It is a friendly game between two people who exchanged a
passphrase, and it is documented as such.
"""

from __future__ import annotations

import json
import struct
from typing import Any, Callable

from ..config import sanitise_name
from ..game import constants as K

PROTOCOL_VERSION = 1

#: Nothing legitimate approaches this. Chat is the only variable-length field
#: and it is capped at 300 characters.
MAX_FRAME_SIZE = 8 * 1024
MAX_CHAT_LENGTH = 300

_HEADER = struct.Struct("!I")
HEADER_SIZE = _HEADER.size


class ProtocolError(Exception):
    """A frame was malformed, oversized, or failed schema validation."""


# ----------------------------------------------------------------------
# Field validators
# ----------------------------------------------------------------------
def _string(max_length: int, *, allow_empty: bool = False) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if not isinstance(value, str):
            raise ProtocolError("Expected a string.")
        cleaned = "".join(
            ch for ch in value if ch == " " or (ch.isprintable() and not ch.isspace())
        ).strip()
        if len(cleaned) > max_length:
            cleaned = cleaned[:max_length]
        if not cleaned and not allow_empty:
            raise ProtocolError("Expected a non-empty string.")
        return cleaned

    return check


def _enum(*allowed: str) -> Callable[[Any], str]:
    permitted = frozenset(allowed)

    def check(value: Any) -> str:
        if not isinstance(value, str) or value not in permitted:
            raise ProtocolError(f"Expected one of {sorted(permitted)}.")
        return value

    return check


def _bounded_int(low: int, high: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError("Expected an integer.")
        if not low <= value <= high:
            raise ProtocolError(f"Value {value} outside {low}..{high}.")
        return value

    return check


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise ProtocolError("Expected a string name.")
    cleaned = sanitise_name(value)
    if not cleaned:
        raise ProtocolError("Empty name.")
    return cleaned


#: The widest damage any single message may claim, taken from the local rules.
_MAX_DAMAGE = max(
    K.GUN_DAMAGE[1], K.LASH_DAMAGE[1], K.BOMB_DAMAGE[1], K.POWER_WEAPON_DAMAGE[1]
)

#: ``type -> {field: validator}``. Unknown types and unknown fields are
#: rejected rather than ignored, so a protocol mismatch fails loudly.
SCHEMA: dict[str, dict[str, Callable[[Any], Any]]] = {
    "hello": {
        "version": _bounded_int(1, 99),
        "name": _name,
        "gender": _enum("male", "female"),
    },
    "ready": {},
    "strike": {
        "weapon": _enum("gun", "whip", "bomb"),
        "outcome": _enum("hit", "miss"),
        "damage": _bounded_int(0, _MAX_DAMAGE),
    },
    "heal": {"amount": _bounded_int(0, K.MAX_HEALTH)},
    "load": {},
    "comment": {"key": _enum("stuff", "arse", "beast")},
    "laugh": {},
    "chat": {"text": _string(MAX_CHAT_LENGTH)},
    "resign": {"reason": _string(120, allow_empty=True)},
    "ping": {"nonce": _bounded_int(0, 2**31 - 1)},
    "pong": {"nonce": _bounded_int(0, 2**31 - 1)},
}


# ----------------------------------------------------------------------
# Encode / decode
# ----------------------------------------------------------------------
def encode(kind: str, **fields: Any) -> bytes:
    """Build a frame. Raises if the message does not match the schema."""
    payload = validate(kind, fields)
    payload["type"] = kind
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_SIZE:
        raise ProtocolError(f"Frame too large to send: {len(body)} bytes.")
    return _HEADER.pack(len(body)) + body


def decode(body: bytes) -> dict[str, Any]:
    """Parse and validate one frame body. Returns the message dict."""
    if len(body) > MAX_FRAME_SIZE:
        raise ProtocolError("Frame exceeds the maximum size.")
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Undecodable frame: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProtocolError("Frame is not a JSON object.")

    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in SCHEMA:
        raise ProtocolError(f"Unknown message type: {kind!r}")

    fields = {key: value for key, value in raw.items() if key != "type"}
    message = validate(kind, fields)
    message["type"] = kind
    return message


def validate(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Check ``fields`` against the schema for ``kind``, returning clean values."""
    spec = SCHEMA.get(kind)
    if spec is None:
        raise ProtocolError(f"Unknown message type: {kind!r}")

    unexpected = set(fields) - set(spec)
    if unexpected:
        raise ProtocolError(f"Unexpected field(s): {sorted(unexpected)}")

    missing = set(spec) - set(fields)
    if missing:
        raise ProtocolError(f"Missing field(s): {sorted(missing)}")

    return {key: check(fields[key]) for key, check in spec.items()}


def read_length(header: bytes) -> int:
    """Unpack a frame header, rejecting absurd lengths before we allocate."""
    if len(header) != HEADER_SIZE:
        raise ProtocolError("Short frame header.")
    (length,) = _HEADER.unpack(header)
    if length == 0:
        raise ProtocolError("Zero-length frame.")
    if length > MAX_FRAME_SIZE:
        raise ProtocolError(f"Peer announced a {length}-byte frame; limit is {MAX_FRAME_SIZE}.")
    return length
