"""Keyboard bindings.

The number-row layout is inherited wholesale from the original, because
players who know Acefire already have it in their fingers: 1 shoots, 2
lashes, 3 loads, 4 and 5 read out the two status lines, 6 is the
power_weapon, 7 heals, 8 talks and 9 bombs. Home/End and Page Up/Down keep
the two volume buses, Enter toggles the score, space silences the screams,
Backspace toggles stats recording.

The additions are a laugh key, a repeat key (audio games need one — miss a
status line and it is gone) and F1 for the full hotkey list.
"""

from __future__ import annotations

import wx

from .actions import Action

#: Key code -> action, for keys with no modifier.
KEYMAP: dict[int, Action] = {
    ord("1"): Action.FIRE_GUN,
    wx.WXK_NUMPAD1: Action.FIRE_GUN,
    ord("2"): Action.CRACK_WHIP,
    wx.WXK_NUMPAD2: Action.CRACK_WHIP,
    ord("3"): Action.LOAD_GUN,
    wx.WXK_NUMPAD3: Action.LOAD_GUN,
    ord("4"): Action.PLAYER_STATUS,
    wx.WXK_NUMPAD4: Action.PLAYER_STATUS,
    ord("5"): Action.OPPONENT_STATUS,
    wx.WXK_NUMPAD5: Action.OPPONENT_STATUS,
    ord("6"): Action.POWER_WEAPON,
    wx.WXK_NUMPAD6: Action.POWER_WEAPON,
    ord("7"): Action.RESTORE_HEALTH,
    wx.WXK_NUMPAD7: Action.RESTORE_HEALTH,
    ord("8"): Action.AUDIO_COMMENT,
    wx.WXK_NUMPAD8: Action.AUDIO_COMMENT,
    ord("9"): Action.USE_BOMB,
    wx.WXK_NUMPAD9: Action.USE_BOMB,
    ord("L"): Action.LAUGH,
    ord("R"): Action.REPEAT_LAST,
    ord("C"): Action.CHEAT_PROMPT,
    wx.WXK_LEFT: Action.MOVE_LEFT,
    wx.WXK_RIGHT: Action.MOVE_RIGHT,
    wx.WXK_SPACE: Action.MARK_NOTE,
    wx.WXK_RETURN: Action.TOGGLE_MUSIC,
    wx.WXK_NUMPAD_ENTER: Action.TOGGLE_MUSIC,
    wx.WXK_HOME: Action.SOUND_UP,
    wx.WXK_END: Action.SOUND_DOWN,
    wx.WXK_PAGEUP: Action.MUSIC_UP,
    wx.WXK_PAGEDOWN: Action.MUSIC_DOWN,
    wx.WXK_BACK: Action.TOGGLE_STATS,
    wx.WXK_ESCAPE: Action.QUIT_MATCH,
}


def action_for(event: wx.KeyEvent) -> Action | None:
    """Resolve a key event, ignoring anything with Ctrl or Alt held.

    Modified keys belong to the menu bar and the screen reader; swallowing
    them here would break both.
    """
    if event.ControlDown() or event.AltDown() or event.MetaDown():
        return None
    return KEYMAP.get(event.GetKeyCode())


#: Rendered by the in-game help (F1) and the README.
HOTKEY_HELP: tuple[tuple[str, str], ...] = (
    ("1", "Fire gun"),
    ("2", "Crack whip"),
    ("3", "Load gun"),
    ("4", "Check your status"),
    ("5", "Check opponent status"),
    ("6", "Fire the power weapon (offline) or send a message (online)"),
    ("7", "Restore health"),
    ("8", "Play an audio comment"),
    ("9", "Activate a bomb"),
    ("L", "Laugh at your opponent"),
    ("R", "Repeat the last message"),
    ("C", "Open the cheat prompt"),
    ("Left and Right", "Move between notes in the bonus round"),
    ("Space", "Mark a note in the bonus, or silence screams during a match"),
    ("Enter", "Turn the background score on or off"),
    ("Home and End", "Sound volume up and down"),
    ("Page Up and Page Down", "Music volume up and down"),
    ("Backspace", "Turn statistics recording on or off"),
    ("Escape", "Leave the match"),
    ("F1", "This list"),
)


def help_text() -> str:
    width = max(len(key) for key, _ in HOTKEY_HELP)
    return "\n".join(f"{key.ljust(width)}   {label}" for key, label in HOTKEY_HELP)
