"""Tunable game numbers, gathered in one place.

Where the original documentation gave a range it is reproduced exactly
(gunshots 5-15, lashes 0-8, bombs 15-50%). Where it only gave a direction
("takes approximately two minutes to charge") the number here is the reading
that matches the recovered audio — the power_weapon ambience bed is six
minutes long, which comfortably covers a two-minute charge plus a
three-minute firing window.
"""

from __future__ import annotations

from enum import Enum

MAX_HEALTH = 100
START_HEALTH = 100

# --- Damage ranges ------------------------------------------------------
#: Absolute health, both of them: this many points off whatever is left.
GUN_DAMAGE = (5, 15)
LASH_DAMAGE = (1, 8)  # a 0 roll is a miss and handled separately
#: *Percent* of the target's remaining health, which is what the original
#: documented and what the readme promises: thirty off someone on sixty, five
#: off someone on ten. Named for its units, because it sat here as
#: ``BOMB_DAMAGE`` beside two absolute ranges and was read out by the same
#: line of code that read those -- which made every bomb a flat fifteen to
#: fifty and let one take a player from single figures to minus forty.
BOMB_DAMAGE_PERCENT = (15, 50)
#: Absolute again. The power weapon is the one swing that is meant to finish
#: a fight, and a percentage can only ever take a fraction of what is left.
POWER_WEAPON_DAMAGE = (15, 50)

# --- Base hit probabilities (percent), before difficulty modifiers ------
GUN_HIT_CHANCE = 68.0
LASH_HIT_CHANCE = 78.0
BOMB_HIT_CHANCE = 80.0
POWER_WEAPON_HIT_CHANCE = 65.0
#: Of the power_weapon shots that do not hit, this share backfires onto the
#: firer rather than simply missing. The readme's three outcomes are miss,
#: hit, and a defence so strong the attack rebounds.
POWER_WEAPON_BACKFIRE_SHARE = 45.0

# --- Healing ------------------------------------------------------------
RESTORE_AMOUNT = (20, 35)

# --- PowerWeapon timing (seconds) --------------------------------------
POWER_WEAPON_CHARGE_TIME = 120.0
POWER_WEAPON_WINDOW = 180.0

# --- Music thresholds (fraction of max health) -------------------------
MUSIC_LEVEL2_AT = 0.30
MUSIC_LEVEL3_AT = 0.15
#: Spoken/played low-health warnings, fired once each as you cross them.
WARN_AT = (0.30, 0.15)

# --- Bonus round --------------------------------------------------------
BONUS_NOTE_COUNT = 13
#: How long a bonus round lasts, unless the player says otherwise. The
#: original gave you three, which is not enough time to hear thirteen notes
#: through once, let alone decide anything about them.
DEFAULT_BONUS_SECONDS = 10
#: What the original gave you, and what this used to default to. Kept so a
#: settings file still holding it can be told apart from one where somebody
#: chose it on purpose.
ORIGINAL_BONUS_SECONDS = 3
#: The longest a bonus round may be set to. Long enough to hear all thirteen
#: notes twice over, which turns the round from a scramble into a choice --
#: some players want that and some want the scramble, so it is a setting.
MAX_BONUS_SECONDS = 30
#: How often the clock is checked. Fine enough that the round ends when it
#: says it will, coarse enough not to wake the interface sixty times a second.
BONUS_TICK = 0.5
#: Chance per completed round that the opponent spawns an item bonus.
BONUS_SPAWN_CHANCE = 12.0
#: Rounds that must pass after one bonus before another can spawn.
BONUS_COOLDOWN_ROUNDS = 4

# --- Cheats -------------------------------------------------------------
CHEAT_UNLOCK_POINTS = 30
MAX_CHEAT_INPUT = 64

# --- Online play --------------------------------------------------------
#: Bullets and health restores each player starts an online match with when
#: nobody says otherwise -- an opponent running a build from before the host
#: decided the supplies, or one that leaves the fields out.
DEFAULT_ONLINE_SUPPLY = 10
#: The most of either a host may hand out. Bounded because the number travels
#: on the wire: a modified client must not be able to deal itself a thousand
#: bullets by editing one field of its hello.
MAX_ONLINE_SUPPLY = 99

# --- Miscellaneous ------------------------------------------------------
AI_THINK_DELAY = (0.7, 1.8)
VOLUME_STEP = 0.05


class Weapon(str, Enum):
    GUN = "gun"
    WHIP = "whip"
    BOMB = "bomb"
    POWER_WEAPON = "power_weapon"


class Outcome(str, Enum):
    HIT = "hit"
    MISS = "miss"
    BACKFIRE = "backfire"
    BLOCKED = "blocked"


class Side(str, Enum):
    PLAYER = "player"
    OPPONENT = "opponent"

    @property
    def other(self) -> "Side":
        return Side.OPPONENT if self is Side.PLAYER else Side.PLAYER


class Phase(str, Enum):
    SETUP = "setup"
    PLAYING = "playing"
    BONUS = "bonus"
    FINISHED = "finished"


class PowerWeaponState(str, Enum):
    UNUSED = "unused"       # player declined it
    CHARGING = "charging"
    READY = "ready"
    SPENT = "spent"         # fired
    EXPIRED = "expired"     # window closed without firing
