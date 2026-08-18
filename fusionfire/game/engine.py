"""The combat rules.

This is the whole game, minus presentation. It is deterministic given a
fixed RNG, holds no references to wx or the audio layer, and reports what
happened by returning events.

The turn model follows the original: you act, then the opponent acts.
Attacking, loading, restoring, bombing and firing the power_weapon all consume
your turn. Checking status, playing an audio comment and laughing do not —
they were free actions in Acefire and taking a turn away for them would
change the game's feel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .. import rng
from ..assets import hit_group
from . import constants as K
from .constants import Outcome, Phase, Side, PowerWeaponState, Weapon
from .difficulty import UNLIMITED, Difficulty, get as get_difficulty
from .events import (
    BonusFinished,
    Event,
    EventLog,
    GameOver,
    PlayMusic,
    StartAmbience,
    StatsChanged,
    StopAmbience,
    StopMusic,
    StrikeResolved,
    TurnChanged,
)


#: The sound each attack makes, so an outcome line can name the thing that
#: would otherwise talk over it. The rules already choose these sounds; all
#: that is added here is saying so out loud, which is what lets the
#: presenter hold the line until the weapon has stopped covering it.
#:
#: A power weapon hit resolves against its impact rather than its launch —
#: the launch happened twenty-one seconds of drumroll ago.
ATTACK_SOUNDS: dict[tuple[Side, Weapon], str] = {
    (Side.PLAYER, Weapon.GUN): "usergun",
    (Side.PLAYER, Weapon.WHIP): "userwhip",
    (Side.PLAYER, Weapon.BOMB): "userbomb",
    (Side.PLAYER, Weapon.POWER_WEAPON): "userweaponhit",
    (Side.OPPONENT, Weapon.GUN): "computergun",
    (Side.OPPONENT, Weapon.WHIP): "computerwhip",
    (Side.OPPONENT, Weapon.BOMB): "computerbomb",
    (Side.OPPONENT, Weapon.POWER_WEAPON): "userweaponhit",
}


@dataclass
class Combatant:
    """One side of the fight."""

    name: str
    gender: str = "male"
    health: int = K.START_HEALTH
    points: int = 0
    bullets: int = 10
    restores: int = 10
    bombs: int = 0
    gun_loaded: bool = False

    #: Warning thresholds already announced, so each fires once.
    warned: set[float] = field(default_factory=set)

    @property
    def alive(self) -> bool:
        return self.health > 0

    @property
    def health_fraction(self) -> float:
        return max(0.0, self.health / K.MAX_HEALTH)

    @property
    def unlimited_bullets(self) -> bool:
        return self.bullets == UNLIMITED

    @property
    def unlimited_restores(self) -> bool:
        return self.restores == UNLIMITED

    def spend_bullet(self) -> None:
        if not self.unlimited_bullets:
            self.bullets = max(0, self.bullets - 1)

    def spend_restore(self) -> None:
        if not self.unlimited_restores:
            self.restores = max(0, self.restores - 1)

    def take(self, damage: int) -> int:
        """Apply damage and return the amount actually applied."""
        self.health -= damage
        return damage

    def heal(self, amount: int) -> int:
        before = self.health
        self.health = min(K.MAX_HEALTH, self.health + amount)
        return self.health - before

    def describe(self) -> str:
        bullets = "unlimited" if self.unlimited_bullets else str(self.bullets)
        restores = "unlimited" if self.unlimited_restores else str(self.restores)
        return (
            f"{self.name}: {max(0, self.health)} health, {self.points} points, "
            f"{bullets} bullets, {restores} restores, {self.bombs} bombs, "
            f"gun {'loaded' if self.gun_loaded else 'empty'}."
        )


@dataclass
class PowerWeapon:
    """The player's one big swing.

    Charges for two minutes, stays usable for three, then powers down for
    good. It hits, misses, or is deflected so hard it lands on you.
    """

    enabled: bool = False
    state: PowerWeaponState = PowerWeaponState.UNUSED
    started_at: float = 0.0
    ready_at: float = 0.0
    expires_at: float = 0.0

    def begin(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.enabled = True
        self.state = PowerWeaponState.CHARGING
        self.started_at = now
        self.ready_at = now + K.POWER_WEAPON_CHARGE_TIME
        self.expires_at = self.ready_at + K.POWER_WEAPON_WINDOW

    def tick(self, now: float | None = None) -> PowerWeaponState | None:
        """Advance the clock. Returns the new state if it just changed."""
        if self.state not in (PowerWeaponState.CHARGING, PowerWeaponState.READY):
            return None
        now = time.monotonic() if now is None else now
        if self.state is PowerWeaponState.CHARGING and now >= self.ready_at:
            self.state = PowerWeaponState.READY
            return self.state
        if self.state is PowerWeaponState.READY and now >= self.expires_at:
            self.state = PowerWeaponState.EXPIRED
            return self.state
        return None

    @property
    def usable(self) -> bool:
        return self.state is PowerWeaponState.READY

    def seconds_remaining(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if self.state is PowerWeaponState.CHARGING:
            return max(0.0, self.ready_at - now)
        if self.state is PowerWeaponState.READY:
            return max(0.0, self.expires_at - now)
        return 0.0


@dataclass
class Strike:
    """The outcome of one attack, in a form both the log and the wire accept."""

    attacker: Side
    weapon: Weapon
    outcome: Outcome
    damage: int = 0
    #: Set when the damage landed on the attacker instead.
    victim: Side | None = None


class Engine:
    """Owns the match state and applies the rules to it."""

    def __init__(
        self,
        player: Combatant,
        opponent: Combatant,
        difficulty: Difficulty | str = "intermediate",
        *,
        online: bool = False,
    ) -> None:
        self.player = player
        self.opponent = opponent
        self.difficulty = (
            difficulty if isinstance(difficulty, Difficulty) else get_difficulty(difficulty)
        )
        self.online = online
        self.phase = Phase.SETUP
        self.turn: Side = Side.PLAYER
        self.power_weapon = PowerWeapon()
        self.round = 0
        self.rounds_since_bonus = 0
        self.winner: Side | None = None
        self.last_striker: Side | None = None
        self._music_level: str | None = None
        #: Decided in ``start`` but not applied until ``begin_play``, so the
        #: opening sound has the stage to itself.
        self._opening_turn: Side = Side.PLAYER
        self._opening_power_weapon = False

        self._apply_difficulty()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _apply_difficulty(self) -> None:
        d = self.difficulty
        self.player.bullets = d.player_bullets
        self.player.restores = d.player_restores
        self.opponent.bullets = UNLIMITED
        self.opponent.restores = d.ai_restores

    def start(self, *, use_power_weapon: bool = False, first: Side | None = None) -> list[Event]:
        """Boot the opponent machine. Nothing else happens until it finishes.

        ``computerstart`` is the machine spinning up, and it wants its ten
        seconds: dropping the score and the idle hum on top of it turns a
        deliberate opening into a pile-up. The caller plays this, waits for
        the sound to end, then calls :meth:`begin_play`.

        The phase stays ``SETUP`` throughout, so neither the player nor the
        opponent can act over the intro.
        """
        log = EventLog()
        self.phase = Phase.SETUP
        self._opening_turn = (
            first if first is not None else rng.choice([Side.PLAYER, Side.OPPONENT])
        )
        self._opening_power_weapon = use_power_weapon and not self.online

        log.sound("computerstart")
        log.say(f"{self.player.name} versus {self.opponent.name}.")
        return log.drain()

    def begin_play(self) -> list[Event]:
        """Start the match proper, once the machine has finished booting."""
        log = EventLog()
        if self.phase is not Phase.SETUP:
            return log.drain()

        self.phase = Phase.PLAYING
        self.turn = self._opening_turn

        # The machine hums for the rest of the match.
        log.add(StartAmbience("machine", "computerrun", volume=0.32))

        if self._opening_power_weapon:
            self.power_weapon.begin()
            log.say("Power weapon powering up. It will take about two minutes to charge.")
            log.add(StartAmbience("power_weapon", "userweaponrun", volume=0.5))

        log.say(
            "You go first."
            if self.turn is Side.PLAYER
            else f"{self.opponent.name} goes first."
        )
        log.add(*self._music_for_health(), TurnChanged(self.turn), StatsChanged())
        return log.drain()

    # ------------------------------------------------------------------
    # Player actions
    # ------------------------------------------------------------------
    def fire_gun(self, side: Side = Side.PLAYER) -> list[Event]:
        log = EventLog()
        actor, target = self._sides(side)
        if not self._can_act(side, log):
            return log.drain()

        if not actor.gun_loaded:
            log.say("Your gun is not loaded. Press 3 to load it." if side is Side.PLAYER
                    else f"{actor.name} pulls the trigger on an empty chamber.",
                    after="error")
            log.sound("error")
            if side is Side.PLAYER:
                return log.drain()  # a dry click does not cost you the turn
            return self._end_turn(log)

        if not actor.unlimited_bullets and actor.bullets <= 0:
            log.say("Out of bullets.", after="error")
            log.sound("error")
            if side is Side.PLAYER:
                return log.drain()
            return self._end_turn(log)

        actor.spend_bullet()
        actor.gun_loaded = False
        log.sound("usergun" if side is Side.PLAYER else "computergun")
        strike = self._roll_attack(side, Weapon.GUN)
        self._resolve(strike, log)
        return self._end_turn(log)

    def crack_whip(self, side: Side = Side.PLAYER) -> list[Event]:
        log = EventLog()
        if not self._can_act(side, log):
            return log.drain()
        log.sound("userwhip" if side is Side.PLAYER else "computerwhip")
        strike = self._roll_attack(side, Weapon.WHIP)
        self._resolve(strike, log)
        return self._end_turn(log)

    def load_gun(self, side: Side = Side.PLAYER) -> list[Event]:
        log = EventLog()
        actor, _ = self._sides(side)
        if not self._can_act(side, log):
            return log.drain()

        if actor.gun_loaded:
            log.say("Your gun is already loaded." if side is Side.PLAYER else "",
                    after="error")
            log.sound("error")
        elif not actor.unlimited_bullets and actor.bullets <= 0:
            log.say("No bullets left to load." if side is Side.PLAYER else "",
                    after="error")
            log.sound("error")
        else:
            actor.gun_loaded = True
            log.sound("userload" if side is Side.PLAYER else "computerload")

        # Loading is a free action. Reloading is not a move, it is the setup
        # for one, and spending a turn on it meant a reload handed the
        # opponent a free hit every single time.
        log.add(StatsChanged())
        return log.drain()

    def restore_health(self, side: Side = Side.PLAYER) -> list[Event]:
        log = EventLog()
        actor, _ = self._sides(side)
        if not self._can_act(side, log):
            return log.drain()

        if not actor.unlimited_restores and actor.restores <= 0:
            log.say("No health restores left.", after="error")
            log.sound("error")
            if side is Side.PLAYER:
                return log.drain()
            return self._end_turn(log)

        if actor.health >= K.MAX_HEALTH:
            log.say("Already at full health.", after="error")
            log.sound("error")
            if side is Side.PLAYER:
                return log.drain()
            return self._end_turn(log)

        actor.spend_restore()
        gained = actor.heal(rng.between(*K.RESTORE_AMOUNT))
        # Crossing back above a warning threshold re-arms it.
        actor.warned = {t for t in actor.warned if actor.health_fraction <= t}

        if side is Side.PLAYER:
            log.sound("userrestore")
            log.say(
                f"Restored {gained} health. You are on {actor.health}.",
                after="userrestore",
            )
        else:
            log.group("computerrestore")
            # A group, so which of the four plays is not decided yet; the
            # presenter takes the longest of them.
            log.say(f"{actor.name} restores {gained} health.", after="computerrestore")

        log.add(*self._music_for_health(), StatsChanged())
        return self._end_turn(log)

    def use_bomb(self, side: Side = Side.PLAYER) -> list[Event]:
        log = EventLog()
        actor, _ = self._sides(side)
        if not self._can_act(side, log):
            return log.drain()

        if actor.bombs <= 0:
            log.say("You have no bombs." if side is Side.PLAYER else "", after="error")
            log.sound("error")
            if side is Side.PLAYER:
                return log.drain()
            return self._end_turn(log)

        actor.bombs -= 1
        log.sound("userbomb" if side is Side.PLAYER else "computerbomb")
        strike = self._roll_attack(side, Weapon.BOMB)
        self._resolve(strike, log)
        log.add(StatsChanged())
        return self._end_turn(log)

    def launch_power_weapon(self) -> tuple[list[Event], bool]:
        """Begin the firing sequence.

        Returns ``(events, launched)``. When ``launched`` is true the caller
        must call :meth:`resolve_power_weapon` once the drumroll has finished
        — the outcome is not rolled until then, so the whole length of
        ``dr.wav`` is genuine suspense rather than a result already decided
        and waiting behind the music.
        """
        log = EventLog()
        if not self._can_act(Side.PLAYER, log):
            return log.drain(), False

        weapon = self.power_weapon
        if not weapon.enabled:
            log.say("You chose not to bring the power weapon to this fight.",
                    after="error")
            log.sound("error")
            return log.drain(), False
        if weapon.state is PowerWeaponState.CHARGING:
            left = int(weapon.seconds_remaining())
            log.say(f"Still charging. About {left} seconds to go.", after="error")
            log.sound("error")
            return log.drain(), False
        if weapon.state is PowerWeaponState.SPENT:
            log.say("The power weapon has already been fired this session.",
                    after="error")
            log.sound("error")
            return log.drain(), False
        if weapon.state is PowerWeaponState.EXPIRED:
            log.say("The power weapon has powered down. It cannot be used again.",
                    after="error")
            log.sound("error")
            return log.drain(), False

        weapon.state = PowerWeaponState.SPENT
        log.add(StopAmbience("power_weapon", fade=0.5), PlayMusic("dr", looping=False))
        log.sound("userweaponattack")
        return log.drain(), True

    def resolve_power_weapon(self) -> list[Event]:
        """Roll the outcome and apply it. Called after the drumroll ends."""
        log = EventLog()
        if self.phase is not Phase.PLAYING:
            return log.drain()
        strike = self._roll_power_weapon()
        self._resolve(strike, log)
        events = self._end_turn(log)

        # The drumroll took the music slot. If the match is still running,
        # put the score back; ``_music_for_health`` alone would return
        # nothing here, because the health band has not necessarily moved.
        if self.phase is Phase.PLAYING:
            events += self.resume_music()
        return events

    def fire_power_weapon(self) -> list[Event]:
        """Launch and resolve in one go, with no drumroll in between.

        Only for callers that cannot schedule the delayed half, such as
        tests. The game panel uses the two-phase form.
        """
        events, launched = self.launch_power_weapon()
        if not launched:
            return events
        return events + self.resolve_power_weapon()

    # ------------------------------------------------------------------
    # Free actions — these do not consume a turn
    # ------------------------------------------------------------------
    def player_status(self) -> list[Event]:
        """Your own readout — nothing else. The power weapon's charge is its
        own concern and is spoken when you press 6 to fire it; tacking it
        onto the status line let it crowd the status out, so it does not."""
        log = EventLog()
        log.say(self.player.describe())
        return log.drain()

    def opponent_status(self) -> list[Event]:
        log = EventLog()
        log.say(self.opponent.describe())
        return log.drain()

    def laugh(self, side: Side = Side.PLAYER) -> list[Event]:
        log = EventLog()
        actor, _ = self._sides(side)
        from ..assets import laugh_group

        log.group(laugh_group(actor.gender), pan=-0.25 if side is Side.PLAYER else 0.25)
        return log.drain()

    def play_comment(self, key: str, side: Side = Side.PLAYER) -> list[Event]:
        from ..assets import COMMENTS

        log = EventLog()
        if key not in COMMENTS:
            log.sound("error")
            return log.drain()
        log.sound(f"comment_{key}", pan=-0.25 if side is Side.PLAYER else 0.25)
        return log.drain()

    # ------------------------------------------------------------------
    # Opponent turn
    # ------------------------------------------------------------------
    def opponent_move(self) -> list[Event]:
        """Pick and perform the machine's action."""
        if self.phase is not Phase.PLAYING or self.turn is not Side.OPPONENT:
            return []

        d = self.difficulty
        me = self.opponent
        hurt = me.health_fraction < d.ai_restore_threshold

        if hurt and me.restores > 0 and rng.chance(d.ai_restore_chance):
            return self.restore_health(Side.OPPONENT)

        if me.bombs > 0 and rng.chance(d.ai_bomb_chance):
            return self.use_bomb(Side.OPPONENT)

        # Loading no longer costs a turn, so the machine reloads and then
        # still attacks in the same move — otherwise the player would get a
        # free hit every time the machine ran its gun dry.
        events: list[Event] = []
        if not me.gun_loaded and (me.unlimited_bullets or me.bullets > 0):
            if rng.chance(55.0):
                events += self.load_gun(Side.OPPONENT)

        if me.gun_loaded and rng.chance(70.0):
            return events + self.fire_gun(Side.OPPONENT)
        return events + self.crack_whip(Side.OPPONENT)

    def opponent_delay(self) -> float:
        """How long to wait before the machine moves, so it feels human."""
        return rng.uniform(*K.AI_THINK_DELAY)

    # ------------------------------------------------------------------
    # Timed updates
    # ------------------------------------------------------------------
    def tick(self) -> list[Event]:
        """Called on a timer. Advances the power weapon clock."""
        log = EventLog()
        changed = self.power_weapon.tick()
        if changed is PowerWeaponState.READY:
            log.sound("userweaponready")
            log.say(
                "Power weapon ready. Press 6 to fire. You have three minutes.",
                after="userweaponready",
            )
        elif changed is PowerWeaponState.EXPIRED:
            log.sound("userweaponpowerdown")
            log.say(
                "The power weapon has powered down. It is gone for this session.",
                after="userweaponpowerdown",
            )
            log.add(StopAmbience("power_weapon"))
        return log.drain()

    # ------------------------------------------------------------------
    # Combat maths
    # ------------------------------------------------------------------
    def _roll_attack(self, side: Side, weapon: Weapon) -> Strike:
        d = self.difficulty
        accuracy = d.player_accuracy if side is Side.PLAYER else d.ai_accuracy

        if weapon is Weapon.GUN:
            base, damage_range = K.GUN_HIT_CHANCE, K.GUN_DAMAGE
        elif weapon is Weapon.WHIP:
            base, damage_range = K.LASH_HIT_CHANCE, K.LASH_DAMAGE
        else:
            base, damage_range = K.BOMB_HIT_CHANCE, K.BOMB_DAMAGE

        if not rng.chance(min(97.0, base * accuracy)):
            return Strike(side, weapon, Outcome.MISS)
        return Strike(side, weapon, Outcome.HIT, rng.between(*damage_range))

    def _roll_power_weapon(self) -> Strike:
        if rng.chance(K.POWER_WEAPON_HIT_CHANCE):
            return Strike(
                Side.PLAYER,
                Weapon.POWER_WEAPON,
                Outcome.HIT,
                rng.between(*K.POWER_WEAPON_DAMAGE),
                victim=Side.OPPONENT,
            )
        if rng.chance(K.POWER_WEAPON_BACKFIRE_SHARE):
            return Strike(
                Side.PLAYER,
                Weapon.POWER_WEAPON,
                Outcome.BACKFIRE,
                rng.between(*K.POWER_WEAPON_DAMAGE),
                victim=Side.PLAYER,
            )
        return Strike(Side.PLAYER, Weapon.POWER_WEAPON, Outcome.MISS)

    def _resolve(self, strike: Strike, log: EventLog, *, delay: float = 0.0) -> None:
        """Apply a strike's consequences and narrate them."""
        attacker, defender = self._sides(strike.attacker)
        weapon_sound = ATTACK_SOUNDS.get((strike.attacker, strike.weapon))

        # Only a landed strike scores. Swinging at the air is not an
        # achievement, and paying a point for one made the score a count of
        # how long the match ran rather than of how well it was fought — a
        # player who missed every shot still finished level with one who hit
        # every one. A backfire scores nothing either: the blast landed, but
        # not on the person it was aimed at.
        if strike.outcome is Outcome.HIT:
            attacker.points += 1

        if strike.outcome is Outcome.MISS:
            log.say(
                f"{self._verb(strike)} and "
                f"{self._agrees(strike.attacker, 'miss', 'misses')}.",
                after=weapon_sound,
            )
            log.add(
                StrikeResolved(
                    attacker=strike.attacker,
                    weapon=strike.weapon.value,
                    outcome=strike.outcome.value,
                    damage=0,
                    victim=strike.attacker.other,
                ),
                StatsChanged(),
            )
            return

        victim_side = strike.victim or strike.attacker.other
        victim = self.player if victim_side is Side.PLAYER else self.opponent
        applied = victim.take(strike.damage)

        if strike.outcome is Outcome.BACKFIRE:
            log.sound("userweaponhit", volume=0.9, delay=delay)
            log.say(
                f"{self.opponent.name}'s defence holds and the blast rebounds! "
                f"You take {applied} damage. You are on {max(0, self.player.health)}.",
                after=weapon_sound,
            )
        else:
            if strike.weapon is Weapon.POWER_WEAPON:
                log.sound("userweaponhit", volume=0.9, delay=delay)
            log.say(
                f"{self._verb(strike)} and "
                f"{self._agrees(strike.attacker, 'hit', 'hits')} for {applied}. "
                f"{victim.name} is on {max(0, victim.health)}.",
                after=weapon_sound,
            )

        # Reaction scream from whoever was hit.
        if victim_side is Side.OPPONENT:
            log.group("userhit", scream=True, pan=0.3)
        else:
            log.group(hit_group(self.player.gender), scream=True, pan=-0.3)

        self.last_striker = strike.attacker if strike.outcome is not Outcome.BACKFIRE else victim_side
        log.add(
            StrikeResolved(
                attacker=strike.attacker,
                weapon=strike.weapon.value,
                outcome=strike.outcome.value,
                damage=applied,
                victim=victim_side,
            ),
            *self._warn_low(victim, victim_side),
            *self._music_for_health(),
            StatsChanged(),
        )

    def _verb(self, strike: Strike) -> str:
        """The subject and its attack verb: "You shoot", "Blue Screen lashes"."""
        attacker, _ = self._sides(strike.attacker)
        who = "You" if strike.attacker is Side.PLAYER else attacker.name
        verbs = {
            Weapon.GUN: "shoot" if strike.attacker is Side.PLAYER else "shoots",
            Weapon.WHIP: "lash" if strike.attacker is Side.PLAYER else "lashes",
            Weapon.BOMB: "throw a bomb" if strike.attacker is Side.PLAYER else "throws a bomb",
            Weapon.POWER_WEAPON: "fire the power weapon"
            if strike.attacker is Side.PLAYER
            else "fires the power weapon",
        }
        return f"{who} {verbs[strike.weapon]}"

    @staticmethod
    def _agrees(side: Side, singular: str, third_person: str) -> str:
        """Pick the form that agrees with the subject.

        The two halves of these lines have to match: "You shoot and missed"
        put a present-tense verb next to a past-tense one, and "Blue Screen
        lashes and hit" dropped the agreement the first verb had already
        established. Both halves are present tense now, and both agree.
        """
        return singular if side is Side.PLAYER else third_person

    def _warn_low(self, victim: Combatant, side: Side) -> list[Event]:
        """Low-health cues, once per threshold crossed."""
        out: list[Event] = []
        if side is not Side.OPPONENT:
            return out
        from .events import PlaySound

        fraction = victim.health_fraction
        for threshold, sound in ((0.30, "computerhealth30"), (0.15, "computerhealth15")):
            if fraction <= threshold and threshold not in victim.warned and victim.alive:
                victim.warned.add(threshold)
                out.append(PlaySound(name=sound, volume=0.8))
        return out

    def _music_level_for_health(self) -> str:
        fraction = self.player.health_fraction
        if fraction > K.MUSIC_LEVEL2_AT:
            level = "level1"
        elif fraction > K.MUSIC_LEVEL3_AT:
            level = "level2"
        else:
            level = "level3"
        return level + "o" if self.online else level

    def _music_for_health(self) -> list[Event]:
        """Score follows the player's health, as the original's level1/2/3 did.

        Returns nothing while the level is unchanged, so a run of hits does
        not keep restarting the same track from the top.
        """
        level = self._music_level_for_health()
        if level == self._music_level:
            return []
        self._music_level = level
        return [PlayMusic(level)]

    def resume_music(self) -> list[Event]:
        """Force the score back on after the player silenced it.

        The level cache exists to stop the track restarting on every hit, but
        it also means the ordinary path returns nothing when the level has
        not moved — which would leave the score off for good once someone
        toggled it. This clears the cache so the right track starts again.
        """
        self._music_level = None
        return self._music_for_health()

    # ------------------------------------------------------------------
    # Turn plumbing
    # ------------------------------------------------------------------
    def _sides(self, side: Side) -> tuple[Combatant, Combatant]:
        if side is Side.PLAYER:
            return self.player, self.opponent
        return self.opponent, self.player

    def _can_act(self, side: Side, log: EventLog) -> bool:
        if self.phase is not Phase.PLAYING:
            log.sound("error")
            return False
        if self.turn is not side:
            if side is Side.PLAYER:
                log.say("Not your turn.", after="error")
                log.sound("error")
            return False
        return True

    def _end_turn(self, log: EventLog) -> list[Event]:
        """Check for a death, then hand over. Returns the drained events."""
        if not self.player.alive or not self.opponent.alive:
            return self._finish(log)

        self.turn = self.turn.other
        if self.turn is Side.PLAYER:
            self.round += 1
            self.rounds_since_bonus += 1
        log.add(TurnChanged(self.turn))
        return log.drain()

    def _finish(self, log: EventLog) -> list[Event]:
        """Whoever struck last wins — including when the last strike was a
        power_weapon backfire onto the firer, which is how the original's
        'the person who made the last strike wins' rule cuts."""
        self.phase = Phase.FINISHED
        dead_player = not self.player.alive
        self.winner = Side.OPPONENT if dead_player else Side.PLAYER

        log.add(StopAmbience("power_weapon", fade=0.6), StopAmbience("machine", fade=1.2))
        suffix = "o" if self.online else ""

        if self.winner is Side.PLAYER:
            log.sound("computerdie")
            log.add(PlayMusic(f"win{suffix}", looping=False))
            log.say(
                f"{self.opponent.name} is down on {self.opponent.health}. You win! "
                f"Final score: you {self.player.points}, "
                f"{self.opponent.name} {self.opponent.points}.",
                after="computerdie",
            )
            reason = f"{self.opponent.name} reached {self.opponent.health} health."
        else:
            log.sound("userdie")
            log.add(PlayMusic(f"die{suffix}", looping=False))
            log.say(
                f"You are down on {self.player.health}. {self.opponent.name} wins. "
                f"Final score: you {self.player.points}, "
                f"{self.opponent.name} {self.opponent.points}.",
                after="userdie",
            )
            reason = f"You reached {self.player.health} health."

        log.add(GameOver(self.winner, reason), StatsChanged())
        return log.drain()

    def abandon(self) -> list[Event]:
        """Leave the match early — quitting to the menu, or a lost connection."""
        log = EventLog()
        self.phase = Phase.FINISHED
        log.add(
            StopAmbience("power_weapon", fade=0.4),
            StopAmbience("machine", fade=0.4),
            StopMusic(fade=0.6),
        )
        if self.power_weapon.state in (PowerWeaponState.CHARGING, PowerWeaponState.READY):
            log.sound("userweaponpowerdown")
        return log.drain()

    # ------------------------------------------------------------------
    # Bonus round hand-off
    # ------------------------------------------------------------------
    def should_spawn_bonus(self) -> bool:
        if self.phase is not Phase.PLAYING or self.online:
            return False
        if self.rounds_since_bonus < K.BONUS_COOLDOWN_ROUNDS:
            return False
        return rng.chance(K.BONUS_SPAWN_CHANCE)

    def enter_bonus(self) -> bool:
        """Suspend the match for a bonus round. False if it cannot start.

        The phase change is what stops anyone attacking. The bonus window is
        modal, but wx keeps pumping timers behind it, so the opponent's move
        would otherwise land in the middle of the round -- and the player
        could fire through a gamepad binding the dialog does not claim.
        """
        if self.phase is not Phase.PLAYING:
            return False
        self.phase = Phase.BONUS
        return True

    def leave_bonus(self) -> None:
        """Resume the match. A bonus that ended the match stays finished."""
        if self.phase is Phase.BONUS:
            self.phase = Phase.PLAYING

    def apply_bonus(self, result) -> list[Event]:
        """Fold a :class:`~fusionfire.game.bonus.BonusResult` into the match."""
        log = EventLog()
        self.rounds_since_bonus = 0
        for effect in result.effects:
            effect.apply(self.player, self.opponent, self.difficulty)
        self.player.health = min(K.MAX_HEALTH, self.player.health)
        self.opponent.health = min(K.MAX_HEALTH, self.opponent.health)
        log.add(BonusFinished(result.summary))
        log.add(*self._music_for_health(), StatsChanged())

        if not self.player.alive or not self.opponent.alive:
            self.last_striker = Side.OPPONENT if not self.player.alive else Side.PLAYER
            return self._finish(log)
        return log.drain()
