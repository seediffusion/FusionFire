"""Randomness.

The original shipped a hand-rolled linear congruential generator seeded from
the system clock, which is why two copies launched together could be nudged
into the same "random" sequence. Everything here draws from the OS CSPRNG via
``secrets``/``SystemRandom`` — for combat rolls that is merely correct, and
for the online handshake nonces it is load-bearing.
"""

from __future__ import annotations

import random
import secrets
from typing import Sequence, TypeVar

T = TypeVar("T")

_rng = random.SystemRandom()


def between(low: int, high: int) -> int:
    """Inclusive integer in ``[low, high]``."""
    if low > high:
        raise ValueError(f"Empty range: {low}..{high}")
    return _rng.randint(low, high)


def chance(percent: float) -> bool:
    """True with probability ``percent`` (0-100)."""
    return _rng.random() * 100.0 < percent


def choice(items: Sequence[T]) -> T:
    if not items:
        raise ValueError("Cannot choose from an empty sequence.")
    return _rng.choice(items)


def sample(items: Sequence[T], count: int) -> list[T]:
    count = max(0, min(count, len(items)))
    return _rng.sample(list(items), count)


def shuffled(items: Sequence[T]) -> list[T]:
    out = list(items)
    _rng.shuffle(out)
    return out


def uniform(low: float, high: float) -> float:
    return _rng.uniform(low, high)


def token(nbytes: int = 32) -> str:
    """URL-safe random token, for session ids and default passphrases."""
    return secrets.token_urlsafe(nbytes)
