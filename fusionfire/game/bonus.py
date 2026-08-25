"""The item bonus — thirteen notes, a few seconds, no take-backs.

The opponent hides items inside an octave of thirteen notes. You arrow left
and right and press space to mark as many as you like before the horn goes.
Some notes help you, some help the other side, and some do nothing at all.

Each note's payload is rolled fresh when the round spawns, so learning "note
seven is always a bomb" is not a strategy. What you mark is a bet on how many
of thirteen unknowns you fancy.

How long the round lasts is the player's to choose, up to
:data:`~fusionfire.game.constants.MAX_BONUS_SECONDS`. Ten is the default: the
original's three is not long enough to hear thirteen notes through once, and
half a minute turns the same round into a deliberate choice. Both ends of
that are worth having, so both are reachable.

Online, both players get a round at the same moment and each picks from
their own thirteen. The host says how long it runs, and each side reports
what its notes were worth as plain numbers -- see
:func:`snapshot` and :func:`deltas`, which is how a set of effect functions
becomes something that can cross a wire and be bounded on arrival.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .. import rng
from . import constants as K
from .difficulty import Difficulty


@dataclass(frozen=True)
class Effect:
    """One note's payload."""

    description: str
    apply_fn: Callable[..., None] = field(repr=False)

    def apply(self, player, opponent, difficulty: Difficulty) -> None:
        self.apply_fn(player, opponent, difficulty)


def _player_health(amount: int) -> Effect:
    word = "gain" if amount > 0 else "lose"
    return Effect(
        f"You {word} {abs(amount)} health",
        lambda p, o, d: setattr(p, "health", p.health + amount),
    )


def _opponent_health(amount: int, foe: str) -> Effect:
    word = "gains" if amount > 0 else "loses"
    return Effect(
        f"{foe} {word} {abs(amount)} health",
        lambda p, o, d: setattr(o, "health", o.health + amount),
    )


def _player_points(amount: int) -> Effect:
    word = "gain" if amount > 0 else "lose"
    return Effect(
        f"You {word} {abs(amount)} points",
        lambda p, o, d: setattr(p, "points", max(0, p.points + amount)),
    )


def _opponent_points(amount: int, foe: str) -> Effect:
    word = "gains" if amount > 0 else "loses"
    return Effect(
        f"{foe} {word} {abs(amount)} points",
        lambda p, o, d: setattr(o, "points", max(0, o.points + amount)),
    )


def _player_bullets(amount: int) -> Effect:
    word = "gain" if amount > 0 else "lose"

    def apply(p, o, d):
        if p.unlimited_bullets:
            return
        p.bullets = max(0, p.bullets + amount)

    return Effect(f"You {word} {abs(amount)} bullets", apply)


def _player_restores(amount: int) -> Effect:
    word = "gain" if amount > 0 else "lose"

    def apply(p, o, d):
        if p.unlimited_restores:
            return
        p.restores = max(0, p.restores + amount)

    return Effect(f"You {word} {abs(amount)} health restores", apply)


def _player_bombs(amount: int) -> Effect:
    def apply(p, o, d):
        if amount > 0 and not d.player_gets_bombs:
            return
        p.bombs = max(0, p.bombs + amount)

    word = "gain" if amount > 0 else "lose"
    return Effect(f"You {word} {abs(amount)} bombs", apply)


def _opponent_bombs(amount: int, foe: str) -> Effect:
    def apply(p, o, d):
        if amount > 0 and not d.ai_gets_bombs:
            return
        o.bombs = max(0, o.bombs + amount)

    word = "gains" if amount > 0 else "loses"
    return Effect(f"{foe} {word} {abs(amount)} bombs", apply)


NOTHING = Effect("Nothing at all", lambda p, o, d: None)


#: What the other side is called in a note's description. Offline it is the
#: machine; online it is a person with a name, and calling them "the machine"
#: in the results made half the round read as though it had been played
#: against something else.
DEFAULT_FOE = "The machine"


def _weighted_pool(foe: str) -> list[tuple[int, Callable[[], Effect]]]:
    """(weight, factory) pairs. Weights are relative, not percentages."""
    return [
        (14, lambda: _player_health(rng.between(5, 25))),
        (10, lambda: _player_health(-rng.between(5, 20))),
        (9, lambda: _opponent_health(-rng.between(5, 25), foe)),
        (7, lambda: _opponent_health(rng.between(5, 15), foe)),
        (10, lambda: _player_points(rng.between(1, 5))),
        (7, lambda: _player_points(-rng.between(1, 4))),
        (6, lambda: _opponent_points(-rng.between(1, 4), foe)),
        (5, lambda: _opponent_points(rng.between(1, 3), foe)),
        (11, lambda: _player_bullets(rng.between(1, 6))),
        (6, lambda: _player_bullets(-rng.between(1, 3))),
        (9, lambda: _player_restores(rng.between(1, 3))),
        (5, lambda: _player_restores(-1)),
        (8, lambda: _player_bombs(1)),
        (6, lambda: _opponent_bombs(1, foe)),
        (12, lambda: NOTHING),
    ]


#: The numbers a bonus round can move. Snapshotting these before and after
#: is how a set of effect functions becomes a set of deltas the wire can
#: carry -- the effects themselves are closures, and nothing is gained by
#: trying to describe one to another machine.
TRACKED = ("health", "points", "bullets", "restores", "bombs")


def snapshot(combatant) -> dict[str, int]:
    return {field: int(getattr(combatant, field)) for field in TRACKED}


def deltas(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {field: after[field] - before[field] for field in TRACKED}


@dataclass
class BonusResult:
    marked: list[int]
    effects: list[Effect]
    summary: str


class BonusRound:
    """State for one bonus. The UI drives it; the rules live here."""

    def __init__(self, difficulty: Difficulty, foe: str = DEFAULT_FOE) -> None:
        self.difficulty = difficulty
        self.foe = foe or DEFAULT_FOE
        self.notes: list[Effect] = self._roll_notes(self.foe)
        self.cursor = 0
        self.marked: set[int] = set()
        self.finished = False

    @staticmethod
    def _roll_notes(foe: str = DEFAULT_FOE) -> list[Effect]:
        pool = _weighted_pool(foe)
        weights = [w for w, _ in pool]
        total = sum(weights)
        notes = []
        for _ in range(K.BONUS_NOTE_COUNT):
            roll = rng.between(1, total)
            running = 0
            for weight, factory in pool:
                running += weight
                if roll <= running:
                    notes.append(factory())
                    break
            else:  # pragma: no cover - arithmetic guarantees we break above
                notes.append(NOTHING)
        return notes

    # ------------------------------------------------------------------
    def move(self, delta: int) -> int:
        """Move the cursor, clamped to the octave. Returns the new index."""
        self.cursor = max(0, min(K.BONUS_NOTE_COUNT - 1, self.cursor + delta))
        return self.cursor

    def toggle(self) -> bool:
        """Mark or unmark the note under the cursor. Returns True if marked."""
        if self.cursor in self.marked:
            self.marked.discard(self.cursor)
            return False
        self.marked.add(self.cursor)
        return True

    @property
    def note_sound(self) -> str:
        return f"note{self.cursor + 1}"

    def describe_cursor(self) -> str:
        state = "marked" if self.cursor in self.marked else "unmarked"
        return f"Note {self.cursor + 1} of {K.BONUS_NOTE_COUNT}, {state}."

    # ------------------------------------------------------------------
    def finish(self) -> BonusResult:
        """Time's up. Collect the payloads of everything marked."""
        self.finished = True
        marked = sorted(self.marked)
        effects = [self.notes[i] for i in marked]

        if not marked:
            summary = f"You marked nothing. {self.foe} keeps the lot."
        else:
            lines = [f"Note {i + 1}: {self.notes[i].description}." for i in marked]
            summary = f"You marked {len(marked)} note{'s' if len(marked) != 1 else ''}. " + " ".join(
                lines
            )
        return BonusResult(marked=marked, effects=effects, summary=summary)
