"""Calendar greetings, and the machine's excuses for not playing.

Two bits of the original's character live here. First, the game wishes you a
happy Christmas, Easter or birthday when it knows about them. Second, the
opponent behaves "very much like a human" — it can refuse a match, and it is
more likely to do so at mealtimes and bedtime, because it wants to eat or
sleep.

Only the month and day of a birthday are ever stored, and even that is
optional. There is no reason for a game to hold a full date of birth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .. import rng


def easter(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return date(year, month, day + 1)


@dataclass(frozen=True)
class Greeting:
    text: str
    music: str | None = None


def for_today(birthday: str = "", *, today: date | None = None) -> Greeting | None:
    """Return the greeting due today, if any. ``birthday`` is ``"MM-DD"``."""
    today = today or date.today()

    if birthday:
        try:
            month, day = (int(part) for part in birthday.split("-"))
        except ValueError:
            month = day = 0
        if (month, day) == (today.month, today.day):
            return Greeting("Happy birthday! Let us celebrate by shooting a computer.", "birthday")

    if (today.month, today.day) in ((12, 24), (12, 25), (12, 26)):
        return Greeting("Merry Christmas from Fusion Fire.", "christmas")

    easter_sunday = easter(today.year)
    if abs((today - easter_sunday).days) <= 1:
        return Greeting("Happy Easter. Mind the chocolate, you will need both hands.")

    if (today.month, today.day) == (1, 1):
        return Greeting("Happy new year. Same machine, new grudge.")

    return None


# ----------------------------------------------------------------------
# The machine's excuses
# ----------------------------------------------------------------------
MEALTIME_HOURS = {7, 8, 12, 13, 18, 19}
BEDTIME_HOURS = {23, 0, 1, 2, 3, 4, 5}

_MEAL_EXCUSES = [
    "Not now, I am eating.",
    "It is dinner time. Come back when I have finished.",
    "I am halfway through a meal and you want a fight? No.",
]
_SLEEP_EXCUSES = [
    "Do you know what time it is? I am asleep.",
    "It is the middle of the night. Go to bed.",
    "I have been powered down for hours. Try again in the morning.",
]
_GENERAL_EXCUSES = [
    "I do not feel like it.",
    "I am busy defragmenting. Ask me later.",
    "No. I am installing updates. Do not turn me off.",
    "I have a headache. A processor headache.",
    "Not today. Nothing personal.",
]


def refusal_chance(*, now: datetime | None = None) -> float:
    """Percentage chance the machine turns down a match right now."""
    now = now or datetime.now()
    if now.hour in BEDTIME_HOURS:
        return 30.0
    if now.hour in MEALTIME_HOURS:
        return 20.0
    return 5.0


def maybe_refuse(*, now: datetime | None = None) -> str | None:
    """Roll for a refusal. Returns the excuse, or None to proceed."""
    now = now or datetime.now()
    if not rng.chance(refusal_chance(now=now)):
        return None
    if now.hour in BEDTIME_HOURS:
        return rng.choice(_SLEEP_EXCUSES)
    if now.hour in MEALTIME_HOURS:
        return rng.choice(_MEAL_EXCUSES)
    return rng.choice(_GENERAL_EXCUSES)
