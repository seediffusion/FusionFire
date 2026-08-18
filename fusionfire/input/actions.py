"""The verbs of the game.

Keyboard and gamepad are two ways of naming the same small set of actions.
Putting that set in one enum means a new input device needs a mapping table
and nothing else — the game panel handles :class:`Action`, never key codes
or button numbers.
"""

from __future__ import annotations

from enum import Enum


class Action(str, Enum):
    # Combat
    FIRE_GUN = "fire_gun"
    CRACK_WHIP = "crack_whip"
    LOAD_GUN = "load_gun"
    RESTORE_HEALTH = "restore_health"
    USE_BOMB = "use_bomb"
    POWER_WEAPON = "power_weapon"

    # Information
    PLAYER_STATUS = "player_status"
    OPPONENT_STATUS = "opponent_status"
    REPEAT_LAST = "repeat_last"

    # Social
    AUDIO_COMMENT = "audio_comment"
    LAUGH = "laugh"
    TAUNT = "taunt"

    # Bonus round
    MOVE_LEFT = "move_left"
    MOVE_RIGHT = "move_right"
    MARK_NOTE = "mark_note"

    # Interface navigation. These never reach the match: the pad produces
    # them only while a menu or a dialog owns the screen.
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    FOCUS_NEXT = "focus_next"
    FOCUS_PREVIOUS = "focus_previous"
    CANCEL = "cancel"

    # Mixing
    SOUND_UP = "sound_up"
    SOUND_DOWN = "sound_down"
    MUSIC_UP = "music_up"
    MUSIC_DOWN = "music_down"
    TOGGLE_MUSIC = "toggle_music"
    TOGGLE_SCREAMS = "toggle_screams"

    # Session
    TOGGLE_STATS = "toggle_stats"
    CHEAT_PROMPT = "cheat_prompt"
    QUIT_MATCH = "quit_match"
    CONFIRM = "confirm"


#: Human-readable labels, used by the settings dialog and the help text.
ACTION_LABELS: dict[Action, str] = {
    Action.FIRE_GUN: "Fire gun",
    Action.CRACK_WHIP: "Crack whip",
    Action.LOAD_GUN: "Load gun",
    Action.RESTORE_HEALTH: "Restore health",
    Action.USE_BOMB: "Activate a bomb",
    Action.POWER_WEAPON: "Fire power weapon",
    Action.PLAYER_STATUS: "Check your status",
    Action.OPPONENT_STATUS: "Check opponent status",
    Action.REPEAT_LAST: "Repeat last message",
    Action.AUDIO_COMMENT: "Play an audio comment",
    Action.LAUGH: "Laugh at your opponent",
    Action.TAUNT: "Taunt your opponent",
    Action.MOVE_LEFT: "Move left",
    Action.MOVE_RIGHT: "Move right",
    Action.MARK_NOTE: "Mark or unmark a note",
    Action.MOVE_UP: "Move up",
    Action.MOVE_DOWN: "Move down",
    Action.FOCUS_NEXT: "Next control",
    Action.FOCUS_PREVIOUS: "Previous control",
    Action.CANCEL: "Cancel",
    Action.SOUND_UP: "Sound volume up",
    Action.SOUND_DOWN: "Sound volume down",
    Action.MUSIC_UP: "Music volume up",
    Action.MUSIC_DOWN: "Music volume down",
    Action.TOGGLE_MUSIC: "Turn background music on or off",
    Action.TOGGLE_SCREAMS: "Turn reaction screams on or off",
    Action.TOGGLE_STATS: "Turn statistics recording on or off",
    Action.CHEAT_PROMPT: "Open the cheat prompt",
    Action.QUIT_MATCH: "Leave the match",
    Action.CONFIRM: "Confirm",
}


#: Actions the gamepad can usefully bind, in the order the settings dialog
#: presents them. Volume and stats stay on the keyboard — a pad has enough to
#: do with combat.
BINDABLE: tuple[Action, ...] = (
    Action.FIRE_GUN,
    Action.CRACK_WHIP,
    Action.LOAD_GUN,
    Action.RESTORE_HEALTH,
    Action.USE_BOMB,
    Action.POWER_WEAPON,
    Action.PLAYER_STATUS,
    Action.OPPONENT_STATUS,
    Action.AUDIO_COMMENT,
    Action.LAUGH,
    Action.TAUNT,
    Action.MARK_NOTE,
    Action.QUIT_MATCH,
)
