"""The six opponents.

Straight from the original's documentation, with the vague parts pinned to
numbers. "Computer will use its restores more often" becomes a probability;
"the computer becomes harder to hit, and you are easier to hit" becomes a
pair of accuracy modifiers. The ordering and the flavour of each tier is
preserved — Coward really is a pushover, Impossible really does deny you
bombs and cheats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Sentinel for the Coward tier's endless supplies.
UNLIMITED = -1


@dataclass(frozen=True)
class Difficulty:
    key: str
    label: str
    description: str

    #: Starting ammunition and health restores for the player.
    player_bullets: int
    player_restores: int

    #: Probability (0-100) the opponent spends a restore when hurt, and the
    #: health fraction below which it starts considering one at all.
    ai_restore_chance: float
    ai_restore_threshold: float

    #: Multipliers on the base hit probability.
    ai_accuracy: float = 1.0
    player_accuracy: float = 1.0

    #: Whether either side can pick bombs up in the item bonus.
    ai_gets_bombs: bool = True
    player_gets_bombs: bool = True

    #: Impossible disables the cheat prompt entirely.
    cheats_allowed: bool = True

    #: How eagerly the opponent throws a bomb it is holding, per turn.
    ai_bomb_chance: float = 18.0

    #: Restores the opponent starts with.
    ai_restores: int = 5

    order: int = 0

    @property
    def unlimited_bullets(self) -> bool:
        return self.player_bullets == UNLIMITED

    @property
    def unlimited_restores(self) -> bool:
        return self.player_restores == UNLIMITED


DIFFICULTIES: dict[str, Difficulty] = {}


def _add(d: Difficulty) -> Difficulty:
    DIFFICULTIES[d.key] = d
    return d


COWARD = _add(
    Difficulty(
        key="coward",
        label="Coward",
        description=(
            "Endless bullets and health restores for you. The machine never "
            "heals itself. It is, frankly, embarrassing for it."
        ),
        player_bullets=UNLIMITED,
        player_restores=UNLIMITED,
        ai_restore_chance=0.0,
        ai_restore_threshold=0.0,
        ai_restores=0,
        ai_accuracy=0.80,
        player_accuracy=1.15,
        ai_bomb_chance=8.0,
        order=0,
    )
)

BEGINNER = _add(
    Difficulty(
        key="beginner",
        label="Beginner",
        description=(
            "Ten bullets and ten restores. The machine heals occasionally and "
            "never picks up bombs."
        ),
        player_bullets=10,
        player_restores=10,
        ai_restore_chance=20.0,
        ai_restore_threshold=0.45,
        ai_restores=3,
        ai_accuracy=0.90,
        player_accuracy=1.08,
        ai_gets_bombs=False,
        ai_bomb_chance=0.0,
        order=1,
    )
)

INTERMEDIATE = _add(
    Difficulty(
        key="intermediate",
        label="Intermediate",
        description=(
            "Ten bullets and ten restores. The machine heals more readily and "
            "starts collecting bombs."
        ),
        player_bullets=10,
        player_restores=10,
        ai_restore_chance=40.0,
        ai_restore_threshold=0.55,
        ai_restores=5,
        order=2,
    )
)

ADVANCED = _add(
    Difficulty(
        key="advanced",
        label="Advanced",
        description="The machine heals often and throws bombs without hesitation.",
        player_bullets=10,
        player_restores=8,
        ai_restore_chance=60.0,
        ai_restore_threshold=0.65,
        ai_restores=6,
        ai_accuracy=1.08,
        player_accuracy=0.96,
        ai_bomb_chance=25.0,
        order=3,
    )
)

EXPERT = _add(
    Difficulty(
        key="expert",
        label="Expert",
        description=(
            "Five bullets and five restores. The machine is harder to hit, you "
            "are easier to hit, and it heals constantly."
        ),
        player_bullets=5,
        player_restores=5,
        ai_restore_chance=75.0,
        ai_restore_threshold=0.70,
        ai_restores=8,
        ai_accuracy=1.20,
        player_accuracy=0.82,
        ai_bomb_chance=32.0,
        order=4,
    )
)

IMPOSSIBLE = _add(
    Difficulty(
        key="impossible",
        label="Impossible",
        description=(
            "No bombs for you. No cheats at all. The machine heals every time "
            "it drops below three quarters health. Good luck."
        ),
        player_bullets=5,
        player_restores=4,
        ai_restore_chance=100.0,
        ai_restore_threshold=0.75,
        ai_restores=12,
        ai_accuracy=1.32,
        player_accuracy=0.74,
        player_gets_bombs=False,
        cheats_allowed=False,
        ai_bomb_chance=40.0,
        order=5,
    )
)


def ordered() -> list[Difficulty]:
    return sorted(DIFFICULTIES.values(), key=lambda d: d.order)


def get(key: str) -> Difficulty:
    return DIFFICULTIES.get(key, INTERMEDIATE)
