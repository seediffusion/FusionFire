"""What the engine tells the outside world.

The rules never play a sound or speak a line themselves; they return events
and let the presentation layer decide how to render them. That is what keeps
:mod:`fusionfire.game` importable — and testable — without an audio device,
and it is what lets the same engine drive both the local game and the online
one, where the same event has to be rendered on two machines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .constants import Side


@dataclass(frozen=True)
class Say:
    """Status output. Goes to speech and braille."""

    text: str
    interrupt: bool = True
    #: The sound this line arrives alongside, if any.
    #:
    #: Naming it lets the presenter hold the *spoken* half back until that
    #: sound has stopped covering it. The rules layer only names the sound;
    #: it has no idea how long anything takes, and measuring it needs an
    #: audio device the engine deliberately does not have.
    #:
    #: A group name works too, and is treated as its longest member.
    after: str | None = None


@dataclass(frozen=True)
class PlaySound:
    """A one-shot effect, named either directly or by group."""

    name: str | None = None
    group: str | None = None
    volume: float = 1.0
    pan: float = 0.0
    delay: float = 0.0
    #: Set for reaction screams, which the player can silence with space.
    scream: bool = False


@dataclass(frozen=True)
class PlayMusic:
    name: str
    looping: bool = True


@dataclass(frozen=True)
class StopMusic:
    fade: float = 0.8


@dataclass(frozen=True)
class StartAmbience:
    key: str
    name: str
    volume: float = 0.55


@dataclass(frozen=True)
class StopAmbience:
    key: str
    fade: float = 1.5


@dataclass(frozen=True)
class TurnChanged:
    side: Side


@dataclass(frozen=True)
class StatsChanged:
    """Health, points or inventory moved; refresh any visible readout."""


@dataclass(frozen=True)
class StrikeResolved:
    """One attack has been fully resolved.

    Carries everything the statistics tally needs, so recording does not mean
    reaching into engine internals from the application layer.
    """

    attacker: Side
    weapon: str
    outcome: str
    damage: int
    victim: Side


@dataclass(frozen=True)
class BonusStarted:
    pass


@dataclass(frozen=True)
class BonusFinished:
    summary: str


@dataclass(frozen=True)
class GameOver:
    winner: Side
    reason: str


@dataclass(frozen=True)
class CheatUnlocked:
    path: str


Event = Union[
    Say,
    PlaySound,
    PlayMusic,
    StopMusic,
    StartAmbience,
    StopAmbience,
    TurnChanged,
    StatsChanged,
    StrikeResolved,
    BonusStarted,
    BonusFinished,
    GameOver,
    CheatUnlocked,
]


@dataclass
class EventLog:
    """Small accumulator so engine methods can append without plumbing lists."""

    events: list[Event] = field(default_factory=list)

    def add(self, *events: Event) -> None:
        self.events.extend(events)

    def say(
        self,
        text: str,
        interrupt: bool = True,
        after: str | None = None,
    ) -> None:
        self.events.append(Say(text, interrupt, after))

    def sound(self, name: str, **kwargs) -> None:
        self.events.append(PlaySound(name=name, **kwargs))

    def group(self, group: str, **kwargs) -> None:
        self.events.append(PlaySound(group=group, **kwargs))

    def drain(self) -> list[Event]:
        out, self.events = self.events, []
        return out
