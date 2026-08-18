"""Input sources. Keyboard and gamepad both resolve to the same actions."""

from .actions import Action, ACTION_LABELS
from .gamepad import GamepadManager

__all__ = ["Action", "ACTION_LABELS", "GamepadManager"]
