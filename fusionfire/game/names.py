"""Character names.

The original's naming flow had personality and it is worth keeping: type a
full name and it is yours; type one word and the machine mocks you before
picking a name for you; press enter on an empty box and it picks one
quietly. Names then get used throughout the match instead of "user" and
"computer".

The original shipped 4,650 male, 7,055 female and 1,276 surnames from a
public word list that is not part of the recovered data. The lists here are
smaller — roughly 350 given names per gender and 600 surnames — which still
gives over 200,000 combinations per gender, so repeats stay rare.
"""

from __future__ import annotations

import functools
import logging

from .. import paths, rng
from ..config import sanitise_name

log = logging.getLogger(__name__)

TAUNTS = [
    "One word? That is not a name, that is a username. Allow me.",
    "Half a name for half an effort. I will finish the job.",
    "A single word. How mysterious. How useless. Here, have this instead.",
    "I asked for a name and got a fragment. Try mine on for size.",
    "You could not be bothered, so neither will I. Congratulations, you are now:",
    "That is a first name at best. Ordinarily I would insist, but I have a better one.",
]

MACHINE_NAMES = [
    "Blue Screen",
    "The Beige Tower",
    "Kernel Panic",
    "Ctrl Alt Defeat",
    "Segmentation Fault",
    "The Dial-Up Modem",
    "Stack Overflow",
    "Fatal Exception",
    "General Protection Fault",
    "Insufficient Memory",
    "The Update Daemon",
    "Not Responding",
    "Abort Retry Fail",
    "Read Error On Drive C",
    "The Spinning Wheel",
    "Disk Full",
]


@functools.lru_cache(maxsize=4)
def _load(stem: str) -> tuple[str, ...]:
    """Read one name list. Falls back to a stub if the file is unreadable."""
    try:
        path = paths.contained(paths.assets_dir(), "names", f"{stem}.txt")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, paths.PathEscapeError) as exc:
        log.error("Could not load name list %r: %s", stem, exc)
        return ("Alex", "Sam", "Jordan")

    names = tuple(
        cleaned
        for line in lines
        if (cleaned := sanitise_name(line.strip())) and not line.lstrip().startswith("#")
    )
    return names or ("Alex", "Sam", "Jordan")


def given_names(gender: str) -> tuple[str, ...]:
    return _load("female" if gender == "female" else "male")


def surnames() -> tuple[str, ...]:
    return _load("surnames")


def random_name(gender: str | None = None) -> tuple[str, str]:
    """Return ``(full name, gender)``, picking a gender if none was given."""
    if gender not in ("male", "female"):
        gender = rng.choice(["male", "female"])
    return f"{rng.choice(given_names(gender))} {rng.choice(surnames())}", gender


def random_machine_name() -> str:
    return rng.choice(MACHINE_NAMES)


def random_taunt() -> str:
    return rng.choice(TAUNTS)


def combinations(gender: str) -> int:
    return len(given_names(gender)) * len(surnames())


class NameChoice:
    """What the player typed, and what the game decided to do about it.

    ``full`` means they gave a first and last name and keep it. ``partial``
    means one word — the machine taunts and overrides. ``random`` means they
    submitted nothing and get a quiet random name.
    """

    __slots__ = ("name", "gender", "kind", "taunt")

    def __init__(self, name: str, gender: str, kind: str, taunt: str = "") -> None:
        self.name = name
        self.gender = gender
        self.kind = kind
        self.taunt = taunt

    @property
    def was_randomised(self) -> bool:
        return self.kind in ("partial", "random")

    def announcement(self) -> str:
        if self.kind == "full":
            return f"You are {self.name}."
        gendered = "a woman" if self.gender == "female" else "a man"
        prefix = f"{self.taunt} " if self.taunt else ""
        return f"{prefix}You are {self.name}, {gendered}."


def decide(typed: str, gender: str = "male") -> NameChoice:
    """Apply the original's naming rules to whatever was typed."""
    cleaned = sanitise_name(typed)
    if not cleaned:
        name, chosen = random_name(None)
        return NameChoice(name, chosen, "random")

    if len(cleaned.split()) < 2:
        name, chosen = random_name(None)
        return NameChoice(name, chosen, "partial", random_taunt())

    if gender not in ("male", "female"):
        gender = "male"
    return NameChoice(cleaned, gender, "full")
