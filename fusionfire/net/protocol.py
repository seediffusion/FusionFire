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


class _Optional:
    """Wraps a validator for a field a peer is allowed to leave out.

    Absent and ``null`` mean the same thing here -- not given -- so a peer
    with nothing to say about a field can say so either way, and a build from
    before the field existed is not a protocol violation. Everything else
    stays as strict as it was: an optional field that *is* present is checked
    by exactly the validator it wraps.
    """

    __slots__ = ("check",)

    def __init__(self, check: Callable[[Any], Any]) -> None:
        self.check = check


def _optional(check: Callable[[Any], Any]) -> _Optional:
    return _Optional(check)


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise ProtocolError("Expected a string name.")
    cleaned = sanitise_name(value)
    if not cleaned:
        raise ProtocolError("Empty name.")
    return cleaned


#: What one bonus round can be worth, taken from the local rules: thirteen
#: notes, and the largest swing any single note carries. A peer running
#: modified code cannot report a bonus worth more than the game can produce.
_MAX_BONUS_SWING = K.BONUS_NOTE_COUNT * 25
_MAX_BONUS_COUNT = K.BONUS_NOTE_COUNT * 10
#: The widest damage any single message may claim, taken from the local
#: rules. The bomb's share is a percentage, so its worst case is that
#: percentage of a target still on full health.
_MAX_DAMAGE = max(
    K.GUN_DAMAGE[1],
    K.LASH_DAMAGE[1],
    K.BOMB_DAMAGE_PERCENT[1] * K.MAX_HEALTH // 100,
    K.POWER_WEAPON_DAMAGE[1],
)

#: ``type -> {field: validator}``. Unknown types and unknown fields are
#: rejected rather than ignored, so a protocol mismatch fails loudly. A
#: validator wrapped in :func:`_optional` marks a field a peer may omit.
SCHEMA: dict[str, dict[str, Callable[[Any], Any] | _Optional]] = {
    "hello": {
        "version": _bounded_int(1, 99),
        "name": _name,
        "gender": _enum("male", "female"),
        # The supplies both players fight with. Optional, because only the
        # host's numbers count and only the host knows it is the host --
        # under the relay that is decided by arrival order, after the
        # dialog has been filled in. Both ends send theirs; the joiner's
        # are ignored. An opponent too old to send either falls back to
        # K.DEFAULT_ONLINE_SUPPLY, which is what both would have picked.
        "bullets": _optional(_bounded_int(0, K.MAX_ONLINE_SUPPLY)),
        "restores": _optional(_bounded_int(0, K.MAX_ONLINE_SUPPLY)),
    },
    "ready": {},
    # The host opens a bonus round on both ends at once and says how long it
    # runs, so the two players are answering the same question for the same
    # length of time.
    "bonus_start": {"seconds": _bounded_int(1, K.MAX_BONUS_SECONDS)},
    # And what one player's notes turned out to be worth. Deltas rather than
    # effects, because the effects are functions and the wire carries
    # numbers -- numbers that can be bounded before anything is applied.
    # ``foe_`` is what the sender's notes did to the receiver.
    "bonus": {
        "health": _bounded_int(-_MAX_BONUS_SWING, _MAX_BONUS_SWING),
        "points": _bounded_int(-_MAX_BONUS_COUNT, _MAX_BONUS_COUNT),
        "bullets": _bounded_int(-_MAX_BONUS_COUNT, _MAX_BONUS_COUNT),
        "restores": _bounded_int(-_MAX_BONUS_COUNT, _MAX_BONUS_COUNT),
        "bombs": _bounded_int(-_MAX_BONUS_COUNT, _MAX_BONUS_COUNT),
        "foe_health": _bounded_int(-_MAX_BONUS_SWING, _MAX_BONUS_SWING),
        "foe_points": _bounded_int(-_MAX_BONUS_COUNT, _MAX_BONUS_COUNT),
        "foe_bombs": _bounded_int(-_MAX_BONUS_COUNT, _MAX_BONUS_COUNT),
    },
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

    missing = {
        key for key, check in spec.items()
        if key not in fields and not isinstance(check, _Optional)
    }
    if missing:
        raise ProtocolError(f"Missing field(s): {sorted(missing)}")

    clean: dict[str, Any] = {}
    for key, check in spec.items():
        if isinstance(check, _Optional):
            # Omitted and null are the same answer, and both drop the field
            # rather than putting a None into the message for every caller
            # downstream to test for.
            value = fields.get(key)
            if value is not None:
                clean[key] = check.check(value)
        else:
            clean[key] = check(fields[key])
    return clean


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
