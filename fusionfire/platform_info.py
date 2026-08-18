"""What this Windows can do.

Two features depend on it: the speech layer (Prism needs Windows 10 or
later; older systems get accessible_output2) and dark mode (Windows 10
onwards has a system setting to follow; earlier ones do not, so the player
sets it by hand).

Kept in one place so both ask the same question and get the same answer.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

#: Windows 10's major version. 8.1 reports 6.3, 7 reports 6.1.
_WINDOWS_10 = 10


def is_windows() -> bool:
    return sys.platform == "win32"


def windows_version() -> tuple[int, int, int]:
    """``(major, minor, build)``. All zeros anywhere but Windows."""
    if not is_windows():
        return (0, 0, 0)
    try:
        info = sys.getwindowsversion()
        return (info.major, info.minor, info.build)
    except Exception:  # pragma: no cover - defensive
        return (0, 0, 0)


def is_windows_10_or_later() -> bool:
    """Whether this is Windows 10 or 11.

    Note that a program without the right manifest is told it is 6.2 by
    compatibility shims. This one is run from Python or a PyInstaller build,
    both of which are manifested for 10, so the answer is honest here.
    """
    return is_windows() and windows_version()[0] >= _WINDOWS_10


def describe() -> str:
    """A short human name for the running system, for logs and the About box."""
    if not is_windows():
        return sys.platform
    major, minor, build = windows_version()
    if major >= 10:
        # Windows 11 kept the major version and moved the build number.
        return "Windows 11" if build >= 22000 else "Windows 10"
    if (major, minor) == (6, 3):
        return "Windows 8.1"
    if (major, minor) == (6, 2):
        return "Windows 8"
    if (major, minor) == (6, 1):
        return "Windows 7"
    return f"Windows {major}.{minor}"


def follows_system_theme() -> bool:
    """Whether the OS has a light/dark setting worth following.

    Windows 10 introduced it. On 8.1 and 7 there is nothing to follow, so
    the game offers the choice itself instead.
    """
    return is_windows_10_or_later()
