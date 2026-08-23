"""Settings and statistics persistence.

Two rules here, both reactions to how the original stored state:

* **Nothing is executed.** Settings are JSON, read with ``json.load`` and
  passed through a typed schema. No ``eval``, no ``pickle``, no INI parser
  that happens to accept code.
* **Nothing is trusted.** Every value is clamped or rejected on load, so a
  hand-edited settings file can make the game boring but not broken — a
  volume of ``1e9`` or a device index of ``-40`` cannot reach the audio layer.

Writes are atomic (temp file + ``os.replace``) so a crash or a full disk
leaves the previous good settings in place rather than a truncated file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger(__name__)

SETTINGS_VERSION = 2

#: The relay spy service the game asks for a list of relay servers, unless
#: the player points it somewhere else. Having one out of the box is the
#: difference between "Play online" working on a first run and the player
#: having to be told an address before they can find a single server.
#:
#: Still a plain setting, not a hard-coded address: clearing the field turns
#: the lookup off, and typing another one moves it. Nothing is contacted
#: until the player asks for the server list.
DEFAULT_RELAY_SPY_URL = "https://fusion.seedy.cc:6363"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ----------------------------------------------------------------------
# How long a sound may hold a line back before the game gives up waiting.
#:
#: Normal combat tops out around 4.7s (a bomb, or the longest scream), so
#: this only bites on the outliers -- the minute-long power weapon impact,
#: the 28-second death sound -- where waiting for the end would leave the
#: player sitting in silence wondering what happened. When it bites, the
#: sound is ducked under the line instead.
SOUND_WAIT_CEILING = 6.0

#: What is still sounding gets turned down to this when a line has to start
#: over it. About 10 dB; WCAG 2.2 SC 1.4.7 asks for 20 dB behind speech, but
#: that only applies when the line is genuinely competing, which after the
#: ceiling is the only time it happens.
DUCK_LEVEL = 0.30


@dataclass
class Settings:
    """Everything the player can change, with safe defaults."""

    version: int = SETTINGS_VERSION

    # Audio
    sound_volume: float = 0.85
    music_volume: float = 0.60
    #: Output device remembered by name, because BASS device numbers shift
    #: when hardware is plugged in or disabled. Empty means system default.
    output_device_name: str = ""
    music_enabled: bool = True
    screams_enabled: bool = True

    # Speech
    speech_backend: str = "auto"
    #: Voice by name, not index: installing or removing a voice renumbers
    #: them, and ending up on a different one silently is worse than falling
    #: back to the default. Empty means the backend's own choice.
    speech_voice: str = ""
    #: Rate, pitch and volume as percentages. -1 leaves the voice's own
    #: setting alone, which is what a screen reader always wants -- the user
    #: configured it there. Prism itself takes 0.0 to 1.0; the conversion
    #: happens in fusionfire.speech.
    speech_rate: float = -1.0
    speech_pitch: float = -1.0
    speech_volume: float = -1.0
    speak_status: bool = True
    braille_status: bool = True

    # Player
    player_name: str = ""
    player_gender: str = "male"
    birthday: str = ""  # ISO "MM-DD"; empty disables the birthday greeting

    # Gameplay
    difficulty: str = "intermediate"
    stats_enabled: bool = True
    use_power_weapon: bool = True
    confirm_exit: bool = True
    #: Play the launch jingle instead of the spoken ready message. The first
    #: Enter on the front menu skips it.
    play_intro_music: bool = True

    # Appearance
    #: "system", "light" or "dark". "system" follows Windows on 10 and 11;
    #: there is nothing to follow on 8.1 and 7, where it means light.
    theme: str = "system"

    # Input
    gamepad_enabled: bool = True
    #: Buzz the pad when an attack lands on the player. Its own switch
    #: because vibration is not universally wanted -- it is felt rather than
    #: heard, and some players find it painful or simply distracting.
    gamepad_vibration: bool = True
    gamepad_deadzone: float = 0.55
    #: Control -> action, overriding the standard layout. Keys are button
    #: numbers as strings, plus "lt" and "rt" for the two triggers.
    gamepad_bindings: dict[str, str] = field(default_factory=dict)

    # Online
    last_host: str = ""
    last_port: int = 6000
    last_relay_host: str = ""
    last_relay_port: int = 6001
    #: A relay spy service that lists publicized relay servers. Empty disables
    #: the "get a list of publicized servers" button in the online dialog.
    relay_spy_url: str = DEFAULT_RELAY_SPY_URL
    #: Bullets and health restores each player starts an online match with.
    #: Both players fill these in and only the host's are used, because who
    #: hosts a relayed match is decided by arrival order, after the dialog
    #: has been answered. See :meth:`fusionfire.game.engine.Engine.apply_match_settings`.
    online_bullets: int = 10
    online_restores: int = 10

    # Updates
    #: Ask GitHub at startup whether a newer release exists. On by default,
    #: because an audio game that quietly goes stale is one whose players
    #: never hear about the fix they reported. It is a switch and not a
    #: fixed behaviour because it is the one thing the game does over the
    #: network without being asked, and some players will not want it --
    #: turning it off leaves Check for updates on the Help menu, which asks
    #: only when the player does.
    check_for_updates: bool = True

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def normalise(self) -> None:
        """Coerce every field into its legal range, in place."""
        from .game.difficulty import DIFFICULTIES

        self.sound_volume = _clamp(_as_float(self.sound_volume, 0.85), 0.0, 1.0)
        self.music_volume = _clamp(_as_float(self.music_volume, 0.60), 0.0, 1.0)
        if not isinstance(self.output_device_name, str):
            self.output_device_name = ""
        self.output_device_name = self.output_device_name.strip()[:128]

        self.music_enabled = bool(self.music_enabled)
        self.screams_enabled = bool(self.screams_enabled)
        self.speak_status = bool(self.speak_status)
        self.braille_status = bool(self.braille_status)
        self.stats_enabled = bool(self.stats_enabled)
        self.use_power_weapon = bool(self.use_power_weapon)
        self.confirm_exit = bool(self.confirm_exit)
        self.play_intro_music = bool(self.play_intro_music)
        self.gamepad_enabled = bool(self.gamepad_enabled)
        self.gamepad_vibration = bool(self.gamepad_vibration)
        self.check_for_updates = bool(self.check_for_updates)

        # Through _as_token first: a hand-edited file can hold a list or a
        # dict here, and an unhashable value would take the membership test
        # out with a TypeError rather than falling back.
        self.speech_backend = _as_token(self.speech_backend, "auto", 32)
        for field_name in ("speech_rate", "speech_pitch", "speech_volume"):
            value = _as_float(getattr(self, field_name), -1.0)
            # Anything negative means "leave it alone"; normalise the whole
            # negative range onto the single sentinel.
            setattr(self, field_name, -1.0 if value < 0 else _clamp(value, 0.0, 100.0))
        if not isinstance(self.speech_voice, str):
            self.speech_voice = ""
        self.speech_voice = self.speech_voice.strip()[:128]

        self.player_name = sanitise_name(self.player_name)
        if self.player_gender not in ("male", "female"):
            self.player_gender = "male"
        self.birthday = _as_birthday(self.birthday)

        if self.difficulty not in DIFFICULTIES:
            self.difficulty = "intermediate"

        from .ui_theme_values import THEME_MODES

        if not isinstance(self.theme, str) or self.theme not in THEME_MODES:
            self.theme = "system"

        self.gamepad_deadzone = _clamp(_as_float(self.gamepad_deadzone, 0.55), 0.05, 0.95)
        if not isinstance(self.gamepad_bindings, dict):
            self.gamepad_bindings = {}
        else:
            self.gamepad_bindings = {
                str(k)[:32]: str(v)[:32]
                for k, v in list(self.gamepad_bindings.items())[:64]
            }

        self.last_host = _as_host(self.last_host)
        self.last_port = _as_int(self.last_port, 6000)
        if not 1 <= self.last_port <= 65535:
            self.last_port = 6000

        self.last_relay_host = _as_host(self.last_relay_host)
        self.last_relay_port = _as_int(self.last_relay_port, 6001)
        if not 1 <= self.last_relay_port <= 65535:
            self.last_relay_port = 6001
        self.relay_spy_url = _as_url(self.relay_spy_url)

        from .game.constants import MAX_ONLINE_SUPPLY

        self.online_bullets = int(
            _clamp(_as_int(self.online_bullets, 10), 0, MAX_ONLINE_SUPPLY)
        )
        self.online_restores = int(
            _clamp(_as_int(self.online_restores, 10), 0, MAX_ONLINE_SUPPLY)
        )

        self.version = SETTINGS_VERSION

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.config_file()
        settings = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            settings.normalise()
            return settings
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Settings unreadable (%s); falling back to defaults.", exc)
            settings.normalise()
            return settings

        if not isinstance(raw, dict):
            log.warning("Settings file is not an object; using defaults.")
            settings.normalise()
            return settings

        known = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key in known:
                setattr(settings, key, value)
            else:
                log.debug("Ignoring unknown setting %r.", key)
        settings._migrate(_as_int(raw.get("version"), 0))
        settings.normalise()
        return settings

    def _migrate(self, from_version: int) -> None:
        """Bring a settings file written by an older build up to date.

        Keyed on the version the file was *written* with, not on whether a
        value happens to look unset, so a player who deliberately turned
        something off does not have it turned back on at every launch.
        """
        if from_version < 2 and not self.relay_spy_url:
            # Version 1 had no default spy service, so every file from it
            # carries an empty address that predates there being one to
            # carry. Fill it in once; clearing it afterwards sticks.
            self.relay_spy_url = DEFAULT_RELAY_SPY_URL

    def save(self, path: Path | None = None) -> None:
        path = path or paths.config_file()
        self.normalise()
        write_json_atomic(path, asdict(self))

    # Convenience so callers can treat it like the dict-ish config in
    # Teepee/PowerCom without giving up dataclass typing.
    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in {f.name for f in fields(self)}:
            raise KeyError(key)
        setattr(self, key, value)


# ----------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------
@dataclass
class Stats:
    """Lifetime tallies. Recording pauses while ``stats_enabled`` is off, and
    whatever was banked before the pause survives untouched — same contract
    the original documented for the backspace key."""

    games_played: int = 0
    games_won: int = 0
    games_lost: int = 0
    shots_fired: int = 0
    shots_hit: int = 0
    lashes: int = 0
    lashes_hit: int = 0
    bombs_used: int = 0
    power_weapons_fired: int = 0
    power_weapon_backfires: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    best_points: int = 0
    total_points: int = 0

    @classmethod
    def load(cls, path: Path | None = None) -> "Stats":
        path = path or paths.stats_file()
        stats = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return stats
        if not isinstance(raw, dict):
            return stats
        known = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key in known:
                setattr(stats, key, max(0, _as_int(value, 0)))
        return stats

    def save(self, path: Path | None = None) -> None:
        write_json_atomic(path or paths.stats_file(), asdict(self))

    @property
    def shot_accuracy(self) -> float:
        return 100.0 * self.shots_hit / self.shots_fired if self.shots_fired else 0.0

    @property
    def lash_accuracy(self) -> float:
        return 100.0 * self.lashes_hit / self.lashes if self.lashes else 0.0


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Serialise ``payload`` to ``path`` without ever leaving a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        log.error("Could not write %s: %s", path, exc)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


MAX_NAME_LENGTH = 40


def sanitise_name(value: Any) -> str:
    """Reduce a player-supplied name to printable characters.

    Names travel over the wire to the other player in online mode and land in
    speech and braille output, so control characters, bidi overrides and
    absurd lengths all get stripped here rather than at each use site.
    """
    if not isinstance(value, str):
        return ""
    cleaned = []
    for ch in value:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            continue
        # Bidi/format controls — invisible in a name field, confusing in output.
        if 0x200B <= code <= 0x200F or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069:
            continue
        cleaned.append(ch)
    return " ".join("".join(cleaned).split())[:MAX_NAME_LENGTH]


def _as_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def _as_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_token(value: Any, default: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    return "".join(c for c in value.strip() if c.isalnum() or c in "-_")[:limit] or default


def _as_host(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-[]")
    return "".join(c for c in value.strip() if c in allowed)[:255]


def _as_url(value: Any) -> str:
    """A printable http/https address, for the relay spy service.

    Deliberately loose: the scheme is checked when the address is used, not
    stored. Control characters, quotes and angle brackets cannot survive a
    settings file edit, which is all this guards against.
    """
    if not isinstance(value, str):
        return ""
    cleaned = "".join(
        ch for ch in value.strip() if ch.isprintable() and ch not in "\"'<>\\"
    )
    return cleaned[:512]


def _as_birthday(value: Any) -> str:
    """Accept ``MM-DD``; reject anything else. Year is deliberately not stored."""
    if not isinstance(value, str) or not value.strip():
        return ""
    parts = value.strip().split("-")
    if len(parts) != 2:
        return ""
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{month:02d}-{day:02d}"
