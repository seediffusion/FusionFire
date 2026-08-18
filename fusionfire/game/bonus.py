"""The item bonus — thirteen notes, three seconds, no take-backs.

The opponent hides items inside an octave of thirteen notes. You arrow left
and right and press space to mark as many as you like before the horn goes.
Some notes help you, some help the machine, and some do nothing at all.

Each note's payload is rolled fresh when the round spawns, so learning "note
seven is always a bomb" is not a strategy. What you mark is a bet on how many
of thirteen unknowns you fancy.
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


def _opponent_health(amount: int) -> Effect:
    word = "gains" if amount > 0 else "loses"
    return Effect(
        f"The machine {word} {abs(amount)} health",
        lambda p, o, d: setattr(o, "health", o.health + amount),
    )


def _player_points(amount: int) -> Effect:
    word = "gain" if amount > 0 else "lose"
    return Effect(
        f"You {word} {abs(amount)} points",
        lambda p, o, d: setattr(p, "points", max(0, p.points + amount)),
    )


def _opponent_points(amount: int) -> Effect:
    word = "gains" if amount > 0 else "loses"
    return Effect(
        f"The machine {word} {abs(amount)} points",
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


def _opponent_bombs(amount: int) -> Effect:
    def apply(p, o, d):
        if amount > 0 and not d.ai_gets_bombs:
            return
        o.bombs = max(0, o.bombs + amount)

    word = "gains" if amount > 0 else "loses"
    return Effect(f"The machine {word} {abs(amount)} bombs", apply)


NOTHING = Effect("Nothing at all", lambda p, o, d: None)


def _weighted_pool() -> list[tuple[int, Callable[[], Effect]]]:
    """(weight, factory) pairs. Weights are relative, not percentages."""
    return [
        (14, lambda: _player_health(rng.between(5, 25))),
        (10, lambda: _player_health(-rng.between(5, 20))),
        (9, lambda: _opponent_health(-rng.between(5, 25))),
        (7, lambda: _opponent_health(rng.between(5, 15))),
        (10, lambda: _player_points(rng.between(1, 5))),
        (7, lambda: _player_points(-rng.between(1, 4))),
        (6, lambda: _opponent_points(-rng.between(1, 4))),
        (5, lambda: _opponent_points(rng.between(1, 3))),
        (11, lambda: _player_bullets(rng.between(1, 6))),
        (6, lambda: _player_bullets(-rng.between(1, 3))),
        (9, lambda: _player_restores(rng.between(1, 3))),
        (5, lambda: _player_restores(-1)),
        (8, lambda: _player_bombs(1)),
        (6, lambda: _opponent_bombs(1)),
        (12, lambda: NOTHING),
    ]


@dataclass
class BonusResult:
    marked: list[int]
    effects: list[Effect]
    summary: str


class BonusRound:
    """State for one bonus. The UI drives it; the rules live here."""

    def __init__(self, difficulty: Difficulty) -> None:
        self.difficulty = difficulty
        self.notes: list[Effect] = self._roll_notes()
        self.cursor = 0
        self.marked: set[int] = set()
        self.finished = False

    @staticmethod
    def _roll_notes() -> list[Effect]:
        pool = _weighted_pool()
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
            summary = "You marked nothing. The machine keeps its items."
        else:
            lines = [f"Note {i + 1}: {self.notes[i].description}." for i in marked]
            summary = f"You marked {len(marked)} note{'s' if len(marked) != 1 else ''}. " + " ".join(
                lines
            )
        return BonusResult(marked=marked, effects=effects, summary=summary)
