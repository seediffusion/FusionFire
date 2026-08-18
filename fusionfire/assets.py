"""The sound catalogue.

Every sound the game can play is named here, once, and looked up by that
name. Two things fall out of doing it this way:

* The rest of the code never builds a path from a string it was handed, so
  there is no place for a traversal bug to live.
* ``verify()`` can walk the whole catalogue at startup and tell the player
  exactly which files are missing, instead of the game dying mid-battle.

Sound origins (all recovered bit-exact from the original ``.xsi`` archives):
``sfx/`` game sounds, ``music/`` the score, ``voice/`` the audio comments and
laughs, ``speech/`` Philip Bennefall's public-domain alphabet and numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SoundRef:
    """A logical sound name bound to a file under the sounds root."""

    name: str
    relpath: str
    music: bool = False

    def resolve(self) -> Path:
        return paths.contained(paths.sounds_dir(), self.relpath)


def _numbered(prefix: str, folder: str, first: int, last: int) -> dict[str, SoundRef]:
    return {
        f"{prefix}{i}": SoundRef(f"{prefix}{i}", f"{folder}/{prefix}{i}.wav")
        for i in range(first, last + 1)
    }


def _sfx(*names: str) -> dict[str, SoundRef]:
    return {n: SoundRef(n, f"sfx/{n}.wav") for n in names}


def _music(*names: str) -> dict[str, SoundRef]:
    return {n: SoundRef(n, f"music/{n}.wav", music=True) for n in names}


CATALOGUE: dict[str, SoundRef] = {}

# --- Weapons and combat -------------------------------------------------
CATALOGUE.update(
    _sfx(
        "usergun",
        "userload",
        "userwhip",
        "userrestore",
        "userbomb",
        "userdie",
        "computergun",
        "computerload",
        "computerwhip",
        "computerbomb",
        "computerdie",
    )
)

# Reaction sounds. 33 for the opponent taking a hit from you; the opponent's
# hits on you are split by the character's gender.
CATALOGUE.update(_numbered("userhit", "sfx", 1, 33))
CATALOGUE.update(_numbered("computerhitm", "sfx", 1, 8))
CATALOGUE.update(_numbered("computerhitf", "sfx", 1, 4))
CATALOGUE.update(_numbered("computerrestore", "sfx", 1, 4))

# --- PowerWeapon --------------------------------------------------------
CATALOGUE.update(
    _sfx(
        "userweaponrun",  # six-minute ambience bed, looped while charging
        "userweaponready",
        "userweaponattack",
        "userweaponhit",
        "userweaponpowerdown",
        # The opponent's equivalent machine, undocumented in the original
        # readme but present in the sound set.
        "computerstart",
        "computerrun",
        "computerstop",
    )
)

# --- Warnings -----------------------------------------------------------
CATALOGUE.update(_sfx("computerhealth30", "computerhealth15"))

# --- Bonus round --------------------------------------------------------
CATALOGUE.update(_sfx("itemclick", "itemclock", "itemtimeout"))
CATALOGUE.update(_numbered("note", "sfx", 1, 13))

# --- Cheat prompt and interface ----------------------------------------
CATALOGUE.update(_sfx("enterch", "exitch", "select", "error", "type", "loading"))

# --- Music --------------------------------------------------------------
CATALOGUE.update(
    _music(
        "intro",  # the launch jingle; the first Enter on the menu skips it
        "loading",
        "connecting",
        "level1",
        "level2",
        "level3",
        "level1o",
        "level2o",
        "level3o",
        "win",
        "wino",
        "die",
        "dieo",
        "end",
        "exit",
        "dr",  # drumroll under a power_weapon attack
        "birthday",
        "christmas",
    )
)

# --- Voice --------------------------------------------------------------
COMMENTS: dict[str, tuple[str, str]] = {
    "stuff": ("Stuff you!", "voice/comment/stuff.wav"),
    "arse": ("Kiss my fairy rotten arse!", "voice/comment/arse.wav"),
    "beast": ("Ooh you little beast!", "voice/comment/beast.wav"),
}
for _key, (_label, _rel) in COMMENTS.items():
    CATALOGUE[f"comment_{_key}"] = SoundRef(f"comment_{_key}", _rel)

LAUGH_COUNTS = {"m": 3, "f": 5}
for _sex, _count in LAUGH_COUNTS.items():
    for _i in range(1, _count + 1):
        _n = f"laugh_{_sex}{_i}"
        CATALOGUE[_n] = SoundRef(_n, f"voice/laugh/{_sex}/{_i}.wav")

# --- Speech samples (used for the cheat prompt's character echo) --------
for _ch in "abcdefghijklmnopqrstuvwxyz":
    CATALOGUE[f"say_{_ch}"] = SoundRef(f"say_{_ch}", f"speech/alphabet/{_ch}.wav")
for _d in "0123456789":
    CATALOGUE[f"say_{_d}"] = SoundRef(f"say_{_d}", f"speech/numbers/{_d}.wav")


# ----------------------------------------------------------------------
# Named groups, so callers ask for "a hit sound" not "userhit17"
# ----------------------------------------------------------------------
GROUPS: dict[str, list[str]] = {
    "userhit": [f"userhit{i}" for i in range(1, 34)],
    "computerhitm": [f"computerhitm{i}" for i in range(1, 9)],
    "computerhitf": [f"computerhitf{i}" for i in range(1, 5)],
    "computerrestore": [f"computerrestore{i}" for i in range(1, 5)],
    "note": [f"note{i}" for i in range(1, 14)],
    "laugh_m": [f"laugh_m{i}" for i in range(1, 4)],
    "laugh_f": [f"laugh_f{i}" for i in range(1, 6)],
}

NOTE_COUNT = 13


def hit_group(gender: str) -> str:
    """Which reaction set voices a character of this gender."""
    return "computerhitf" if gender == "female" else "computerhitm"


def laugh_group(gender: str) -> str:
    return "laugh_f" if gender == "female" else "laugh_m"


def speech_sound(char: str) -> str | None:
    """Map a typed character to its recorded name, for the cheat prompt."""
    lowered = char.lower()
    if lowered.isalpha() and lowered in "abcdefghijklmnopqrstuvwxyz":
        return f"say_{lowered}"
    if lowered.isdigit() and lowered in "0123456789":
        return f"say_{lowered}"
    return None


# ----------------------------------------------------------------------
# Startup verification
# ----------------------------------------------------------------------
def verify() -> list[str]:
    """Return the names of catalogue entries whose files are missing."""
    missing = []
    for ref in CATALOGUE.values():
        try:
            if not ref.resolve().is_file():
                missing.append(ref.name)
        except paths.PathEscapeError:  # pragma: no cover - catalogue is static
            missing.append(ref.name)
    if missing:
        log.warning("%d sound(s) missing: %s", len(missing), ", ".join(sorted(missing)[:12]))
    return missing
