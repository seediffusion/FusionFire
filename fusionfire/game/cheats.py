"""Cheat codes.

The original's prompt was invisible on purpose — a small bang told you that
you were in it, and typing echoed each character back through recorded
speech. That is preserved. What is not preserved is the parsing: codes here
resolve against a fixed table of named handlers, and the quantity is an
integer run through a per-cheat clamp.

There is no path from this prompt to arbitrary behaviour. A code either
matches a table entry or it plays the error sound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .. import paths
from . import constants as K

#: ``quantity name`` — nothing else is accepted.
_CHEAT_PATTERN = re.compile(r"^\s*(\d{1,6})\s+([a-z][a-z0-9]{1,31})\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Cheat:
    name: str
    description: str
    max_quantity: int
    apply_fn: Callable[[object, object, int], str]


def _give_bullets(player, opponent, n: int) -> str:
    if player.unlimited_bullets:
        return "You already have unlimited bullets."
    player.bullets += n
    return f"{n} bullets added. You now have {player.bullets}."


def _give_restores(player, opponent, n: int) -> str:
    if player.unlimited_restores:
        return "You already have unlimited health restores."
    player.restores += n
    return f"{n} health restores added. You now have {player.restores}."


def _give_bombs(player, opponent, n: int) -> str:
    player.bombs += n
    return f"{n} bombs added. You now have {player.bombs}."


def _give_health(player, opponent, n: int) -> str:
    gained = min(n, K.MAX_HEALTH - player.health)
    player.health = min(K.MAX_HEALTH, player.health + n)
    return f"{gained} health restored. You are on {player.health}."


def _give_points(player, opponent, n: int) -> str:
    player.points += n
    return f"{n} points added. You now have {player.points}."


def _drain_opponent(player, opponent, n: int) -> str:
    opponent.health -= n
    return f"The machine loses {n} health. It is on {max(0, opponent.health)}."


CHEATS: dict[str, Cheat] = {
    c.name: c
    for c in (
        Cheat("bullets", "Add bullets to your gun belt.", 500, _give_bullets),
        Cheat("restores", "Add health restores.", 100, _give_restores),
        Cheat("bombs", "Add bombs.", 50, _give_bombs),
        Cheat("health", "Restore your own health.", K.MAX_HEALTH, _give_health),
        Cheat("points", "Add points to your score.", 1000, _give_points),
        Cheat("machinedamage", "Take health off the machine.", 60, _drain_opponent),
    )
}


@dataclass(frozen=True)
class CheatResult:
    ok: bool
    message: str


def parse(text: str) -> tuple[str, int] | None:
    """Split ``"15 bullets"`` into ``("bullets", 15)``, or return None."""
    if not isinstance(text, str) or len(text) > K.MAX_CHEAT_INPUT:
        return None
    match = _CHEAT_PATTERN.match(text)
    if match is None:
        return None
    quantity_text, name = match.group(1), match.group(2).lower()
    if name not in CHEATS:
        return None
    return name, int(quantity_text)


def apply(text: str, player, opponent, difficulty) -> CheatResult:
    """Validate and run a cheat against the live match."""
    if not difficulty.cheats_allowed:
        return CheatResult(False, "Cheats are disabled on this difficulty.")

    parsed = parse(text)
    if parsed is None:
        return CheatResult(False, "That is not a cheat.")

    name, quantity = parsed
    cheat = CHEATS[name]
    quantity = max(1, min(quantity, cheat.max_quantity))
    return CheatResult(True, cheat.apply_fn(player, opponent, quantity))


def earned(points: int) -> bool:
    """Whether this score is enough to earn the unlock in the first place."""
    return points >= K.CHEAT_UNLOCK_POINTS


def already_unlocked() -> bool:
    """Whether the unlock file exists from a previous session.

    The 30 points buy the codes permanently, not for one match. Once the
    file is there the prompt stays available, so a player who earned it last
    week does not have to earn it again tonight.
    """
    try:
        return paths.cheats_file().is_file()
    except OSError:  # pragma: no cover - unreadable data directory
        return False


def unlocked(points: int = 0) -> bool:
    """Whether the cheat prompt should open right now."""
    return already_unlocked() or earned(points)


def write_cheat_file() -> str:
    """Write the unlock file the player earns at 30 points."""
    path = paths.cheats_file()
    lines = [
        "Fusion Fire cheat codes",
        "=========================",
        "",
        f"Earned at {K.CHEAT_UNLOCK_POINTS} points, unlocked "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.",
        "",
        "Press C during a match to open the cheat prompt. There is no window:",
        "a short bang tells you the prompt is open, and each character you type",
        "is spoken back. Enter submits, escape backs out.",
        "",
        "Type a quantity, a space, then the code. For example: 15 bullets",
        "",
    ]
    width = max(len(c.name) for c in CHEATS.values())
    for cheat in sorted(CHEATS.values(), key=lambda c: c.name):
        lines.append(f"  {cheat.name.ljust(width)}  {cheat.description} (max {cheat.max_quantity})")
    lines += [
        "",
        "Cheats do not work on Impossible.",
        "",
    ]
    text = "\n".join(lines)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return ""
    return str(path)
