"""Controller and gamepad support.

Acefire predated any of this, so the mapping is ours. It is built around the
fact that this is a blind-accessible game played entirely by ear: the pad
has to be usable without looking at it, so the bindings follow the physical
shape of a standard controller rather than an on-screen legend. The two
weapons sit under your index fingers on the triggers, the face buttons are
the four things you reach for between shots, the shoulders carry the bomb
and the power weapon, and the sticks and D-pad drive the bonus round.

The pad has two modes, because the same four buttons cannot mean "shoot"
and "choose this menu item" at once. During a match it produces combat
actions; while a menu or a dialog owns the screen it produces navigation
actions instead, and :class:`~fusionfire.ui.navigator.Navigator` turns those
into the keystrokes the interface already understands. The mode travels with
each dispatch rather than being read at the far end, so an action produced
in a menu can never arrive as a gunshot because the screen changed in
between.

SDL is driven on a polling thread with a dummy video driver — pygame is here
for its joystick layer only, never for a window. Every dispatch hops onto
the wx main thread through the callback, so nothing here touches a widget
directly.

Hot-plugging is handled: plug a pad in mid-match and it starts working,
unplug it and the game carries on from the keyboard.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from .actions import Action

log = logging.getLogger(__name__)

# Must precede the pygame import: we want SDL's joystick subsystem without a
# window, and SDL decides its video driver at import/init time.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame

    HAVE_PYGAME = True
except Exception as exc:  # pragma: no cover - optional dependency
    HAVE_PYGAME = False
    log.warning("pygame unavailable, gamepad support disabled: %s", exc)

# SDL's game controller layer, which is what makes a trigger findable. The
# raw joystick API reports axes in whatever order the driver felt like: the
# XInput driver puts the triggers at 2 and 5, the HID driver at 4 and 5, and
# neither says which is which. The controller layer answers that question
# from SDL's own device database, so a trigger is a trigger on every pad it
# recognises -- which is every pad Windows recognises. See _probe_triggers
# for what happens on the ones it does not.
sdl_controller = None
if HAVE_PYGAME:
    try:
        from pygame._sdl2 import controller as sdl_controller
    except Exception as exc:  # pragma: no cover - very old pygame
        log.warning("SDL game controller layer unavailable: %s", exc)


#: Button number -> action, on the standard controller layout: 0 to 3 are
#: the face buttons in the order A, B, X, Y, then the two shoulders, Back and
#: Start, and the two stick clicks. Every recognised pad is translated onto
#: these numbers by CONTROLLER_BUTTON_ORDER, so a binding means the same
#: physical button whatever is plugged in — and a settings file written on
#: one pad still makes sense on another.
DEFAULT_BUTTON_BINDINGS: dict[int, Action] = {
    0: Action.TAUNT,           # A
    1: Action.LAUGH,           # B
    2: Action.LOAD_GUN,        # X
    3: Action.RESTORE_HEALTH,  # Y
    4: Action.USE_BOMB,        # Left shoulder
    5: Action.POWER_WEAPON,     # Right shoulder
    6: Action.OPPONENT_STATUS,  # Back / View
    7: Action.PLAYER_STATUS,   # Start / Menu
    8: Action.LAUGH,           # Left stick click
    9: Action.AUDIO_COMMENT,   # Right stick click
    10: Action.REPEAT_LAST,
    11: Action.QUIT_MATCH,
}

#: SDL's game controller button -> the button number above. The two orders
#: are not the same one, and the raw order only matches this table on an
#: Xbox pad. That is what this closes: a PlayStation or Switch pad hands its
#: buttons over in a different order entirely, so the button printed "A" was
#: not the button the game thought it was. Reading through the mapping puts
#: every recognised pad on the layout the settings dialog describes.
#:
#: Empty without pygame, since the keys are its constants.
CONTROLLER_BUTTON_ORDER: dict[int, int] = {}
#: The D-pad, which a mapped pad reports as four buttons rather than a hat.
CONTROLLER_DPAD: dict[int, Action] = {}
if HAVE_PYGAME:
    CONTROLLER_BUTTON_ORDER = {
        pygame.CONTROLLER_BUTTON_A: 0,
        pygame.CONTROLLER_BUTTON_B: 1,
        pygame.CONTROLLER_BUTTON_X: 2,
        pygame.CONTROLLER_BUTTON_Y: 3,
        pygame.CONTROLLER_BUTTON_LEFTSHOULDER: 4,
        pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: 5,
        pygame.CONTROLLER_BUTTON_BACK: 6,
        pygame.CONTROLLER_BUTTON_START: 7,
        pygame.CONTROLLER_BUTTON_LEFTSTICK: 8,
        pygame.CONTROLLER_BUTTON_RIGHTSTICK: 9,
        # Windows opens the Game Bar on this one, so it is a long shot, but
        # it is the button that sat at 10 on the raw layout.
        pygame.CONTROLLER_BUTTON_GUIDE: 10,
    }
    CONTROLLER_DPAD = {
        pygame.CONTROLLER_BUTTON_DPAD_LEFT: Action.MOVE_LEFT,
        pygame.CONTROLLER_BUTTON_DPAD_RIGHT: Action.MOVE_RIGHT,
        pygame.CONTROLLER_BUTTON_DPAD_UP: Action.MOVE_UP,
        pygame.CONTROLLER_BUTTON_DPAD_DOWN: Action.MOVE_DOWN,
    }

LEFT_TRIGGER = "lt"
RIGHT_TRIGGER = "rt"

#: The two weapons live on the triggers. They are the two things you do most
#: and the only two that are aimed, so they get the controls that are already
#: shaped like a trigger.
DEFAULT_TRIGGER_BINDINGS: dict[str, Action] = {
    LEFT_TRIGGER: Action.CRACK_WHIP,
    RIGHT_TRIGGER: Action.FIRE_GUN,
}

#: How the settings dialog names them.
TRIGGER_LABELS: dict[str, str] = {
    LEFT_TRIGGER: "left trigger",
    RIGHT_TRIGGER: "right trigger",
}

#: What the same buttons mean while a menu or dialog is up. A is the
#: affirmative and B backs out, which is the convention every console player
#: already has in their hands; Back and Start double them so a pad with worn
#: face buttons is still usable. Everything else is deliberately absent —
#: pressing the bomb button at a menu should do nothing at all.
MENU_BUTTON_BINDINGS: dict[int, Action] = {
    0: Action.CONFIRM,          # A
    1: Action.CANCEL,           # B
    4: Action.FOCUS_PREVIOUS,   # Left shoulder
    5: Action.FOCUS_NEXT,       # Right shoulder
    6: Action.CANCEL,           # Back / View
    7: Action.CONFIRM,          # Start / Menu
}

#: How hard and how long to buzz when an attack lands on the player, by
#: weapon, as (heavy motor, light motor, milliseconds). The whip is a flick:
#: short, and mostly the light motor. Being shot has to be unmistakably worse
#: than being lashed by feel alone, so it runs both motors for more than
#: twice as long, and the two weapons that can take a third of you in one go
#: are heavier still.
RUMBLE_PATTERNS: dict[str, tuple[float, float, int]] = {
    "whip": (0.18, 0.45, 160),
    "gun": (0.75, 0.90, 380),
    "bomb": (1.00, 0.85, 550),
    "power_weapon": (1.00, 1.00, 700),
}

POLL_INTERVAL = 0.02          # 50 Hz; comfortably below human reaction time
DEVICE_SCAN_INTERVAL = 2.0    # how often to look for newly plugged pads
AXIS_REPEAT_DELAY = 0.35      # hold a stick to repeat, at this cadence

#: A trigger counts as pulled past the first, and released below the second.
#: The gap is hysteresis: a finger resting exactly on a single threshold
#: would otherwise fire the gun over and over at fifty times a second.
TRIGGER_PRESS = 0.55
TRIGGER_RELEASE = 0.35

#: Full scale of an SDL axis, for turning one into -1.0 .. 1.0.
AXIS_SCALE = 32767.0


class _Pad:
    """One open device: the joystick, and the controller view of it."""

    __slots__ = ("index", "joystick", "controller", "trigger_axes", "probed")

    def __init__(self, index: int, joystick, controller) -> None:
        self.index = index
        self.joystick = joystick
        self.controller = controller
        #: Raw axis numbers for (left, right), worked out by _probe_triggers
        #: when there is no controller mapping to ask. None means unknown or
        #: undiscoverable.
        self.trigger_axes: tuple[int, int] | None = None
        self.probed = False

    @property
    def name(self) -> str:
        try:
            return self.joystick.get_name()
        except Exception:  # pragma: no cover - device vanished mid-question
            return "gamepad"


class GamepadManager:
    """Polls attached pads and turns their input into :class:`Action` values."""

    def __init__(
        self, settings, dispatch: Callable[[Action, bool], None]
    ) -> None:
        self.settings = settings
        self._dispatch = dispatch
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pads: dict[int, _Pad] = {}
        self._button_state: dict[tuple[int, int | str], bool] = {}
        self._trigger_state: dict[tuple[int, str], bool] = {}
        self._axis_latch: dict[tuple[int, str], float] = {}
        self._hat_state: dict[tuple[int, int], tuple[int, int]] = {}
        self._names: list[str] = []
        #: True while a menu or dialog owns the screen; see the module docstring.
        self._navigation = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return HAVE_PYGAME

    @property
    def device_names(self) -> list[str]:
        with self._lock:
            return list(self._names)

    @property
    def navigation(self) -> bool:
        return self._navigation

    def set_navigation(self, navigating: bool) -> None:
        """Switch between menu navigation and combat.

        Called whenever what is on screen changes. The button state is left
        alone: a button held across the change has already been counted, and
        clearing it here would let one press fire twice.
        """
        self._navigation = bool(navigating)

    def start(self) -> bool:
        """Begin polling. Returns False if unavailable or already running."""
        if not HAVE_PYGAME or not self.settings.gamepad_enabled:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        try:
            pygame.display.init()
            pygame.joystick.init()
        except Exception as exc:
            log.warning("Could not initialise SDL joystick support: %s", exc)
            return False
        if sdl_controller is not None:
            try:
                sdl_controller.init()
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("Could not initialise SDL game controllers: %s", exc)

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._teardown()

    def _teardown(self) -> None:
        with self._lock:
            for pad in self._pads.values():
                self._close(pad)
            self._pads.clear()
            self._names.clear()
        if HAVE_PYGAME:
            try:
                if sdl_controller is not None:
                    sdl_controller.quit()
                pygame.joystick.quit()
                pygame.display.quit()
            except Exception:
                pass

    @staticmethod
    def _close(pad: _Pad) -> None:
        """Let go of one device, silencing it on the way out.

        A pad still buzzing when the game closed would otherwise carry on
        for the rest of the rumble's duration with nothing left holding it
        that could stop it.
        """
        for handle in (pad.controller, pad.joystick):
            if handle is None:
                continue
            try:
                handle.stop_rumble()
            except Exception:
                pass
            try:
                handle.quit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------
    def _override(self, key: str) -> Action | None:
        """The player's own binding for one control, if they set one."""
        value = self.settings.gamepad_bindings.get(key)
        if not value:
            return None
        try:
            return Action(value)
        except ValueError:
            log.debug("Unknown bound action %r for control %s", value, key)
            return None

    def _action_for_button(self, button: int) -> Action | None:
        """User binding wins; otherwise fall back to the standard layout.

        In navigation mode neither applies: the menu bindings are fixed, so
        that rebinding A to "throw a bomb" cannot leave a player unable to
        answer a dialog.
        """
        if self._navigation:
            return MENU_BUTTON_BINDINGS.get(button)
        return self._override(str(button)) or DEFAULT_BUTTON_BINDINGS.get(button)

    def _action_for_trigger(self, slot: str) -> Action | None:
        if self._navigation:
            return None
        return self._override(slot) or DEFAULT_TRIGGER_BINDINGS.get(slot)

    def bind(self, control: int | str, action: Action | None) -> None:
        """Assign one button or trigger. ``control`` is an index or "lt"/"rt"."""
        key = control if isinstance(control, str) else str(int(control))
        if action is None:
            self.settings.gamepad_bindings.pop(key, None)
        else:
            self.settings.gamepad_bindings[key] = action.value

    def reset_bindings(self) -> None:
        self.settings.gamepad_bindings.clear()

    # ------------------------------------------------------------------
    # Vibration
    # ------------------------------------------------------------------
    def rumble(self, low: float, high: float, duration_ms: int) -> bool:
        """Buzz every attached pad. False if nothing actually buzzed.

        Called from the wx thread while the poll thread is reading the same
        devices. SDL takes its own lock per joystick, so the only thing
        needed here is a consistent snapshot of which pads exist.
        """
        if not HAVE_PYGAME or not self.settings.gamepad_vibration:
            return False
        with self._lock:
            handles = [(pad.index, pad.joystick) for pad in self._pads.values()]

        buzzed = False
        for index, joystick in handles:
            try:
                buzzed = bool(joystick.rumble(low, high, duration_ms)) or buzzed
            except Exception as exc:
                log.debug("Gamepad %s cannot rumble: %s", index, exc)
        return buzzed

    def rumble_for(self, weapon: str) -> bool:
        """Buzz with the pattern for an attack that landed on the player."""
        pattern = RUMBLE_PATTERNS.get(weapon)
        if pattern is None:
            return False
        return self.rumble(*pattern)

    def stop_rumble(self) -> None:
        if not HAVE_PYGAME:
            return
        with self._lock:
            handles = [pad.joystick for pad in self._pads.values()]
        for joystick in handles:
            try:
                joystick.stop_rumble()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _run(self) -> None:
        last_scan = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now - last_scan >= DEVICE_SCAN_INTERVAL:
                    self._rescan()
                    last_scan = now
                pygame.event.pump()
                self._poll(now)
            except Exception as exc:  # a dying device must not kill the thread
                log.debug("Gamepad poll error: %s", exc)
                self._forget_all()
            self._stop.wait(POLL_INTERVAL)

    def _rescan(self) -> None:
        """Open pads that appeared, drop ones that went away."""
        try:
            count = pygame.joystick.get_count()
        except Exception:
            return

        with self._lock:
            present = set(range(count))
            for index in list(self._pads):
                if index not in present:
                    self._close(self._pads.pop(index))
                    log.info("Gamepad %s disconnected.", index)

            for index in present:
                if index in self._pads:
                    continue
                try:
                    joystick = pygame.joystick.Joystick(index)
                    joystick.init()
                except Exception as exc:
                    log.debug("Could not open joystick %s: %s", index, exc)
                    continue
                pad = _Pad(index, joystick, self._open_controller(index))
                self._pads[index] = pad
                log.info(
                    "Gamepad %s connected: %s (%s)",
                    index,
                    pad.name,
                    "standard layout" if pad.controller is not None else "unmapped",
                )

            self._names = [pad.name for pad in self._pads.values()]

    @staticmethod
    def _open_controller(index: int):
        """The game controller view of a joystick, or None if SDL has no map."""
        if sdl_controller is None:
            return None
        try:
            if not sdl_controller.is_controller(index):
                return None
            return sdl_controller.Controller(index)
        except Exception as exc:
            log.debug("No controller mapping for joystick %s: %s", index, exc)
            return None

    def _forget_all(self) -> None:
        with self._lock:
            self._pads.clear()
            self._names.clear()
        self._button_state.clear()
        self._trigger_state.clear()
        self._axis_latch.clear()
        self._hat_state.clear()

    def _poll(self, now: float) -> None:
        with self._lock:
            pads = list(self._pads.values())

        for pad in pads:
            try:
                if pad.controller is not None:
                    # The mapping covers the D-pad too, so the hat is not
                    # read as well -- both at once would move twice per press.
                    self._poll_mapped_buttons(pad)
                else:
                    self._poll_buttons(pad)
                    self._poll_hats(pad)
                self._poll_triggers(pad)
                self._poll_stick(pad, now)
            except Exception as exc:
                log.debug("Gamepad %s read failed: %s", pad.index, exc)

    def _edge(self, key, pressed: bool) -> bool:
        """True on the frame a control goes down. Holding it does nothing."""
        was = self._button_state.get(key, False)
        self._button_state[key] = pressed
        return pressed and not was

    def _poll_buttons(self, pad: _Pad) -> None:
        """Raw button order, for a pad SDL has no mapping for."""
        for button in range(pad.joystick.get_numbuttons()):
            if self._edge((pad.index, button), bool(pad.joystick.get_button(button))):
                action = self._action_for_button(button)
                if action is not None:
                    self._emit(action)

    def _poll_mapped_buttons(self, pad: _Pad) -> None:
        """Buttons by what they are labelled rather than by what order they
        arrive in. See :data:`CONTROLLER_BUTTON_ORDER`."""
        for sdl_button, button in CONTROLLER_BUTTON_ORDER.items():
            pressed = bool(pad.controller.get_button(sdl_button))
            if self._edge((pad.index, button), pressed):
                action = self._action_for_button(button)
                if action is not None:
                    self._emit(action)

        for sdl_button, action in CONTROLLER_DPAD.items():
            pressed = bool(pad.controller.get_button(sdl_button))
            # Its own key space: a D-pad direction and a face button must not
            # share an entry in the state table.
            if self._edge((pad.index, f"dpad{sdl_button}"), pressed):
                self._emit(action)

    def _poll_triggers(self, pad: _Pad) -> None:
        """Fire once per pull, not once per poll while it is held down."""
        values = self._trigger_values(pad)
        if values is None:
            return
        for slot, value in zip((LEFT_TRIGGER, RIGHT_TRIGGER), values):
            key = (pad.index, slot)
            was = self._trigger_state.get(key, False)
            if was:
                if value < TRIGGER_RELEASE:
                    self._trigger_state[key] = False
                continue
            if value < TRIGGER_PRESS:
                continue
            self._trigger_state[key] = True
            action = self._action_for_trigger(slot)
            if action is not None:
                self._emit(action)

    def _trigger_values(self, pad: _Pad) -> tuple[float, float] | None:
        """Both triggers as 0.0 (released) to 1.0 (pulled), or None if unknown.

        A trigger reads -1.0 at rest through the raw joystick API and 0.0
        through the controller one, so both conventions are folded onto the
        same scale here and the thresholds above only have to know one.
        """
        if pad.controller is not None:
            try:
                return (
                    max(0.0, pad.controller.get_axis(
                        pygame.CONTROLLER_AXIS_TRIGGERLEFT) / AXIS_SCALE),
                    max(0.0, pad.controller.get_axis(
                        pygame.CONTROLLER_AXIS_TRIGGERRIGHT) / AXIS_SCALE),
                )
            except Exception as exc:  # pragma: no cover - device went away
                log.debug("Gamepad %s trigger read failed: %s", pad.index, exc)
                return None

        if not pad.probed:
            self._probe_triggers(pad)
        if pad.trigger_axes is None:
            return None
        left, right = pad.trigger_axes
        return (
            (pad.joystick.get_axis(left) + 1.0) / 2.0,
            (pad.joystick.get_axis(right) + 1.0) / 2.0,
        )

    @staticmethod
    def _probe_triggers(pad: _Pad) -> None:
        """Find the trigger axes on a pad SDL has no mapping for.

        The one signature left without a mapping is the resting position: a
        trigger sits at -1.0 and a thumbstick at 0.0. Anything other than
        exactly two candidates would be guesswork, and the failure it invites
        is firing the gun by leaning on a thumbstick, so the triggers go
        unbound instead and the face buttons carry the match.

        Run on the first poll rather than at open, because SDL has no axis
        values to report until the event queue has been pumped once.
        """
        pad.probed = True
        try:
            resting = [
                axis
                for axis in range(pad.joystick.get_numaxes())
                if pad.joystick.get_axis(axis) <= -0.8
            ]
        except Exception:  # pragma: no cover - device went away
            return
        if len(resting) == 2:
            pad.trigger_axes = (resting[0], resting[1])
            log.info("Gamepad %s: triggers on axes %s and %s.", pad.index, *resting)
        else:
            log.info(
                "Gamepad %s: could not identify the triggers, so they are "
                "unbound. The face buttons still work.",
                pad.index,
            )

    def _poll_hats(self, pad: _Pad) -> None:
        """The D-pad on an unmapped pad, where it arrives as a hat switch."""
        for hat in range(pad.joystick.get_numhats()):
            key = (pad.index, hat)
            value = pad.joystick.get_hat(hat)
            was = self._hat_state.get(key, (0, 0))
            self._hat_state[key] = value
            if value == was:
                continue
            x, y = value
            if x < 0:
                self._emit(Action.MOVE_LEFT)
            elif x > 0:
                self._emit(Action.MOVE_RIGHT)
            # SDL's hat is positive upwards, unlike its sticks.
            if y > 0:
                self._emit(Action.MOVE_UP)
            elif y < 0:
                self._emit(Action.MOVE_DOWN)

    def _poll_stick(self, pad: _Pad, now: float) -> None:
        """The left stick moves too, with hold-to-repeat on both axes."""
        position = self._stick_position(pad)
        if position is None:
            return
        deadzone = self.settings.gamepad_deadzone
        x, y = position

        for slot, value, low, high in (
            ("x", x, Action.MOVE_LEFT, Action.MOVE_RIGHT),
            ("y", y, Action.MOVE_UP, Action.MOVE_DOWN),
        ):
            key = (pad.index, slot)
            if abs(value) < deadzone:
                self._axis_latch.pop(key, None)
                continue
            next_fire = self._axis_latch.get(key)
            if next_fire is not None and now < next_fire:
                continue
            self._axis_latch[key] = now + AXIS_REPEAT_DELAY
            self._emit(low if value < 0 else high)

    @staticmethod
    def _stick_position(pad: _Pad) -> tuple[float, float] | None:
        """The left stick as (x, y), where up and left are negative."""
        if pad.controller is not None:
            try:
                return (
                    pad.controller.get_axis(pygame.CONTROLLER_AXIS_LEFTX) / AXIS_SCALE,
                    pad.controller.get_axis(pygame.CONTROLLER_AXIS_LEFTY) / AXIS_SCALE,
                )
            except Exception:  # pragma: no cover - device went away
                return None
        try:
            axes = pad.joystick.get_numaxes()
            if axes < 1:
                return None
            return (
                pad.joystick.get_axis(0),
                pad.joystick.get_axis(1) if axes > 1 else 0.0,
            )
        except Exception:  # pragma: no cover - device went away
            return None

    def _emit(self, action: Action) -> None:
        # The mode is captured here rather than read by the far end: the
        # dispatch crosses onto the wx thread, and by the time it lands the
        # screen may have changed underneath it.
        navigation = self._navigation
        try:
            self._dispatch(action, navigation)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Gamepad dispatch failed for %s: %s", action, exc)
