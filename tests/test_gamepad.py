"""The pad, driven without a pad.

Everything below polls a stand-in joystick rather than a real one. The point
is not to test SDL — it is to pin down the two things that are ours and that
a plugged-in controller would only reveal by misbehaving in front of a
player: which control produces which action, and the fact that the same
button means one thing in a match and something else entirely at a menu.
"""

from __future__ import annotations

import pytest

from fusionfire.config import Settings
from fusionfire.input.actions import Action
from fusionfire.input.gamepad import (
    DEFAULT_BUTTON_BINDINGS,
    DEFAULT_TRIGGER_BINDINGS,
    LEFT_TRIGGER,
    RIGHT_TRIGGER,
    RUMBLE_PATTERNS,
    GamepadManager,
    _Pad,
)


class FakeJoystick:
    """A pad with six axes, twelve buttons and one hat, all under our thumb.

    The axes rest the way a real one does: the sticks at zero, and the two
    triggers at -1 until something pulls them.
    """

    def __init__(self, *, buttons: int = 12, axes=(0.0, 0.0, -1.0, 0.0, 0.0, -1.0)):
        self.buttons = [False] * buttons
        self.axes = list(axes)
        self.hat = (0, 0)
        self.rumbles: list[tuple[float, float, int]] = []
        self.stopped = 0

    # The joystick surface pygame offers, and no more of it than is used.
    def get_numbuttons(self) -> int:
        return len(self.buttons)

    def get_button(self, index: int) -> bool:
        return self.buttons[index]

    def get_numaxes(self) -> int:
        return len(self.axes)

    def get_axis(self, index: int) -> float:
        return self.axes[index]

    def get_numhats(self) -> int:
        return 1

    def get_hat(self, index: int):
        return self.hat

    def get_name(self) -> str:
        return "Fake Pad"

    def rumble(self, low: float, high: float, duration: int) -> bool:
        self.rumbles.append((low, high, duration))
        return True

    def stop_rumble(self) -> None:
        self.stopped += 1

    def quit(self) -> None:
        pass


class FakeController:
    """The SDL game controller view of a pad, which answers by label.

    Its buttons and axes are numbered by SDL's own enums rather than by the
    order the device happens to send them in — which is the whole reason the
    game asks it anything.
    """

    def __init__(self) -> None:
        self.buttons: dict[int, bool] = {}
        self.axes: dict[int, int] = {}

    def get_button(self, button: int) -> bool:
        return self.buttons.get(button, False)

    def get_axis(self, axis: int) -> int:
        return self.axes.get(axis, 0)

    def stop_rumble(self) -> None:
        pass

    def quit(self) -> None:
        pass


@pytest.fixture
def pad():
    """A manager with one fake pad attached, and a record of what it emits."""
    settings = Settings()
    emitted: list[tuple[Action, bool]] = []
    manager = GamepadManager(
        settings, lambda action, navigation: emitted.append((action, navigation))
    )
    joystick = FakeJoystick()
    device = _Pad(0, joystick, None)
    manager._pads[0] = device
    # A match, not a menu, unless a test says otherwise.
    manager.set_navigation(False)
    # One poll with the pad at rest, which is what happens the moment a pad
    # is plugged in and is when the trigger axes are picked out.
    manager._poll_triggers(device)
    assert device.trigger_axes == (2, 5)
    return manager, device, joystick, emitted


def _actions(emitted) -> list[Action]:
    return [action for action, _navigation in emitted]


def _press(manager, device, joystick, button: int) -> None:
    joystick.buttons[button] = True
    manager._poll_buttons(device)
    joystick.buttons[button] = False
    manager._poll_buttons(device)


def _pull(manager, device, joystick, axis: int, amount: float = 1.0) -> None:
    joystick.axes[axis] = amount
    manager._poll_triggers(device)


# ----------------------------------------------------------------------
# The match layout
# ----------------------------------------------------------------------
def test_the_triggers_are_the_two_weapons(pad):
    """Left lashes, right shoots -- the two things you do most, on the two
    controls already shaped like a trigger."""
    manager, device, joystick, emitted = pad

    _pull(manager, device, joystick, 2)
    _pull(manager, device, joystick, 5)

    assert _actions(emitted) == [Action.CRACK_WHIP, Action.FIRE_GUN]


def test_a_held_trigger_fires_once(pad):
    """A turn-based game: holding the trigger down is not automatic fire."""
    manager, device, joystick, emitted = pad

    for _ in range(5):
        _pull(manager, device, joystick, 5)
    assert _actions(emitted) == [Action.FIRE_GUN]

    # Let it back out past the release point, and it will fire again.
    joystick.axes[5] = -1.0
    manager._poll_triggers(device)
    _pull(manager, device, joystick, 5)
    assert _actions(emitted) == [Action.FIRE_GUN, Action.FIRE_GUN]


def test_a_trigger_resting_on_the_threshold_does_not_chatter(pad):
    """Hysteresis. A finger held at exactly the press point would otherwise
    fire fifty times a second."""
    manager, device, joystick, emitted = pad

    _pull(manager, device, joystick, 5, 0.60)
    for amount in (0.54, 0.58, 0.52, 0.56):
        _pull(manager, device, joystick, 5, amount)

    assert _actions(emitted) == [Action.FIRE_GUN]


def test_the_face_buttons_are_the_four_things_you_reach_for(pad):
    manager, device, joystick, emitted = pad

    for button in (0, 1, 2, 3):
        _press(manager, device, joystick, button)

    assert _actions(emitted) == [
        Action.TAUNT,
        Action.LAUGH,
        Action.LOAD_GUN,
        Action.RESTORE_HEALTH,
    ]


def test_the_shoulders_and_the_rest_kept_their_places(pad):
    """The remap moved the four face buttons and the triggers. Everything
    else was already in the player's fingers and stays where it was."""
    assert DEFAULT_BUTTON_BINDINGS[4] is Action.USE_BOMB
    assert DEFAULT_BUTTON_BINDINGS[5] is Action.POWER_WEAPON
    assert DEFAULT_BUTTON_BINDINGS[6] is Action.OPPONENT_STATUS
    assert DEFAULT_BUTTON_BINDINGS[7] is Action.PLAYER_STATUS
    assert DEFAULT_BUTTON_BINDINGS[8] is Action.LAUGH
    assert DEFAULT_BUTTON_BINDINGS[9] is Action.AUDIO_COMMENT
    assert DEFAULT_BUTTON_BINDINGS[10] is Action.REPEAT_LAST
    assert DEFAULT_BUTTON_BINDINGS[11] is Action.QUIT_MATCH


def test_a_binding_the_player_set_wins(pad):
    manager, device, joystick, emitted = pad
    manager.bind(0, Action.USE_BOMB)
    manager.bind(RIGHT_TRIGGER, Action.CRACK_WHIP)

    _press(manager, device, joystick, 0)
    _pull(manager, device, joystick, 5)

    assert _actions(emitted) == [Action.USE_BOMB, Action.CRACK_WHIP]
    manager.reset_bindings()
    assert manager.settings.gamepad_bindings == {}


# ----------------------------------------------------------------------
# Menus
# ----------------------------------------------------------------------
def test_at_a_menu_the_same_buttons_navigate(pad):
    """A is the affirmative and B backs out, which is what a console player
    already has in their hands."""
    manager, device, joystick, emitted = pad
    manager.set_navigation(True)

    for button in (0, 1, 4, 5):
        _press(manager, device, joystick, button)

    assert _actions(emitted) == [
        Action.CONFIRM,
        Action.CANCEL,
        Action.FOCUS_PREVIOUS,
        Action.FOCUS_NEXT,
    ]
    assert all(navigation for _action, navigation in emitted)


def test_no_weapon_can_be_fired_from_a_menu(pad):
    manager, device, joystick, emitted = pad
    manager.set_navigation(True)

    _pull(manager, device, joystick, 2)
    _pull(manager, device, joystick, 5)
    for button in (2, 3, 9, 11):  # load, heal, comment, quit
        _press(manager, device, joystick, button)

    assert emitted == []


def test_a_rebound_button_still_answers_a_dialog(pad):
    """Menu bindings are deliberately not rebindable. Rebinding A to a bomb
    must not leave a player with no way to answer a yes/no box."""
    manager, device, joystick, emitted = pad
    manager.bind(0, Action.USE_BOMB)
    manager.set_navigation(True)

    _press(manager, device, joystick, 0)
    assert _actions(emitted) == [Action.CONFIRM]


def test_the_mode_travels_with_the_action(pad):
    """The dispatch crosses onto the wx thread, where the screen may already
    have changed. Reading the mode at the far end would let a menu press
    arrive as a gunshot."""
    manager, device, joystick, emitted = pad
    manager.set_navigation(True)
    _press(manager, device, joystick, 0)
    manager.set_navigation(False)
    _press(manager, device, joystick, 0)

    assert emitted == [(Action.CONFIRM, True), (Action.TAUNT, False)]


# ----------------------------------------------------------------------
# Sticks and the D-pad
# ----------------------------------------------------------------------
def test_the_d_pad_moves_in_all_four_directions(pad):
    manager, device, joystick, emitted = pad

    for value in ((-1, 0), (1, 0), (0, 1), (0, -1)):
        joystick.hat = value
        manager._poll_hats(device)

    assert _actions(emitted) == [
        Action.MOVE_LEFT,
        Action.MOVE_RIGHT,
        Action.MOVE_UP,
        Action.MOVE_DOWN,
    ]


def test_the_stick_repeats_while_it_is_held(pad):
    manager, device, joystick, emitted = pad
    joystick.axes[1] = -1.0  # SDL is negative upwards

    manager._poll_stick(device, now=0.0)
    manager._poll_stick(device, now=0.1)   # too soon to repeat
    manager._poll_stick(device, now=10.0)

    assert _actions(emitted) == [Action.MOVE_UP, Action.MOVE_UP]


def test_a_stick_inside_the_dead_zone_says_nothing(pad):
    manager, device, joystick, emitted = pad
    joystick.axes[0] = manager.settings.gamepad_deadzone - 0.01

    manager._poll_stick(device, now=0.0)

    assert emitted == []


# ----------------------------------------------------------------------
# Pads SDL has no mapping for
# ----------------------------------------------------------------------
def test_an_unrecognisable_pad_leaves_its_triggers_unbound(pad):
    """Without a mapping the only signature left is the resting position, and
    anything but exactly two candidates is guesswork. Guessing wrong here
    fires the gun off a thumbstick, so it does not guess."""
    manager, _device, _joystick, emitted = pad
    joystick = FakeJoystick(axes=(0.0, 0.0, 0.0, 0.0))  # four sticks, no triggers
    device = _Pad(1, joystick, None)

    manager._poll_triggers(device)   # at rest: nothing identifiable
    joystick.axes[3] = 1.0
    manager._poll_triggers(device)

    assert emitted == []
    assert device.probed and device.trigger_axes is None


# ----------------------------------------------------------------------
# Vibration
# ----------------------------------------------------------------------
def test_a_lash_is_felt_more_lightly_than_a_gunshot(pad):
    manager, _device, joystick, _emitted = pad

    manager.rumble_for("whip")
    manager.rumble_for("gun")

    whip, gun = joystick.rumbles
    assert whip[0] < gun[0] and whip[1] < gun[1], "the whip should be softer"
    assert whip[2] < gun[2], "the whip should be shorter"
    assert whip == RUMBLE_PATTERNS["whip"] and gun == RUMBLE_PATTERNS["gun"]


def test_vibration_can_be_switched_off(pad):
    manager, _device, joystick, _emitted = pad
    manager.settings.gamepad_vibration = False

    assert manager.rumble_for("gun") is False
    assert joystick.rumbles == []


def test_an_unknown_weapon_does_not_buzz(pad):
    manager, _device, joystick, _emitted = pad
    assert manager.rumble_for("harsh language") is False
    assert joystick.rumbles == []


def test_letting_go_of_a_pad_stops_it_buzzing(pad):
    """A pad still running a rumble when the match ended would keep going
    with nothing left holding it that could stop it."""
    _manager, device, joystick, _emitted = pad
    GamepadManager._close(device)
    assert joystick.stopped == 1


# ----------------------------------------------------------------------
# Pads SDL does have a mapping for
# ----------------------------------------------------------------------
@pytest.fixture
def mapped(pad):
    """The same manager, with a pad SDL recognises attached at index 1."""
    manager, _device, _joystick, emitted = pad
    controller = FakeController()
    device = _Pad(1, FakeJoystick(), controller)
    return manager, device, controller, emitted


def test_a_recognised_pad_is_read_by_label_not_by_order(mapped):
    """A PlayStation pad hands its buttons over in a different order from an
    Xbox one. Read raw, the button printed "A" was not the button the game
    thought it was; read through the mapping, it is."""
    import pygame

    manager, device, controller, emitted = mapped

    for sdl_button in (
        pygame.CONTROLLER_BUTTON_A,
        pygame.CONTROLLER_BUTTON_X,
        pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
        pygame.CONTROLLER_BUTTON_START,
    ):
        controller.buttons = {sdl_button: True}
        manager._poll_mapped_buttons(device)
        controller.buttons = {}
        manager._poll_mapped_buttons(device)

    assert _actions(emitted) == [
        Action.TAUNT,
        Action.LOAD_GUN,
        Action.USE_BOMB,
        Action.PLAYER_STATUS,
    ]


def test_a_recognised_pad_reads_its_triggers_from_the_mapping(mapped):
    import pygame

    manager, device, controller, emitted = mapped
    controller.axes = {pygame.CONTROLLER_AXIS_TRIGGERLEFT: 32767}
    manager._poll_triggers(device)

    assert _actions(emitted) == [Action.CRACK_WHIP]
    assert not device.probed, "a mapped pad has nothing to guess at"


def test_the_d_pad_of_a_recognised_pad_moves_once_per_press(mapped):
    """It arrives as buttons there and as a hat on the joystick behind it.
    Reading both would move two notes for one press."""
    import pygame

    manager, device, controller, emitted = mapped
    # The same press, reported by both halves of the device at once, which
    # is what a real pad does. Through the whole poll, because that is where
    # the choice between the two is made.
    manager._pads = {1: device}
    controller.buttons = {pygame.CONTROLLER_BUTTON_DPAD_LEFT: True}
    device.joystick.hat = (-1, 0)
    manager._poll(0.0)

    assert _actions(emitted) == [Action.MOVE_LEFT]


# ----------------------------------------------------------------------
# Bindings as data
# ----------------------------------------------------------------------
def test_every_bound_action_is_one_the_game_answers_to():
    from fusionfire.input.actions import ACTION_LABELS
    from fusionfire.input.gamepad import MENU_BUTTON_BINDINGS

    for table in (DEFAULT_BUTTON_BINDINGS, DEFAULT_TRIGGER_BINDINGS, MENU_BUTTON_BINDINGS):
        for action in table.values():
            assert action in ACTION_LABELS, f"{action} has no label"


def test_the_two_weapons_are_on_the_triggers():
    assert DEFAULT_TRIGGER_BINDINGS[LEFT_TRIGGER] is Action.CRACK_WHIP
    assert DEFAULT_TRIGGER_BINDINGS[RIGHT_TRIGGER] is Action.FIRE_GUN
    assert Action.FIRE_GUN not in DEFAULT_BUTTON_BINDINGS.values()
    assert Action.CRACK_WHIP not in DEFAULT_BUTTON_BINDINGS.values()
