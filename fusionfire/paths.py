"""Filesystem locations, and the guards that keep us inside them.

Everything the game reads from disk resolves through here. The originals
kept sounds beside the executable and let any file in that folder override a
built-in one by name; that is a directory the user can write to, so the
override path is also an attack path. We keep the same *feature* — modding is
half the fun of an audio game — but every resolved path is checked against its
allowed root before it is opened.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "FusionFire"
APP_AUTHOR = "Seediffusion"


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def package_root() -> Path:
    """Directory holding the package's own files, frozen or not.

    PyInstaller unpacks ``--add-data`` into ``sys._MEIPASS`` preserving the
    destination layout it was given, and the build asks for
    ``fusionfire/assets`` so the frozen tree mirrors the source one. The
    ``fusionfire`` component matters: without it the assets resolve one
    directory too high and every single sound goes missing, which the game
    survives by playing nothing at all.
    """
    if _frozen():
        return Path(sys._MEIPASS) / "fusionfire"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def game_dir() -> Path:
    """The game's own folder — beside the executable, or the source checkout.

    Distinct from :func:`package_root`, which for a frozen build points at
    the temporary unpack directory rather than anywhere the player can see.
    """
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return package_root() / "assets"


def sounds_dir() -> Path:
    """The one folder the game's sounds live in.

    There used to be two: the ones shipped inside the program, and a separate
    folder to drop replacements into. That is a worse deal than it sounds --
    you had to know the layout to shadow a file correctly, and the sounds you
    were replacing were somewhere you could not see. Now there is a single
    ``sounds`` folder beside the game, and changing a sound means replacing
    the file. Nothing is hidden and nothing shadows anything.
    """
    return game_dir() / "sounds"


def data_dir() -> Path:
    """Per-user writable directory for config, stats and cheat unlocks."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return data_dir() / "fusionfire.log"


def config_file() -> Path:
    return data_dir() / "settings.json"


def stats_file() -> Path:
    return data_dir() / "stats.json"


def cheats_file() -> Path:
    return data_dir() / "cheats.txt"


class PathEscapeError(ValueError):
    """A resolved path pointed outside the root it was supposed to stay in."""


def contained(root: Path, *parts: str) -> Path:
    """Join ``parts`` onto ``root`` and prove the result stays under ``root``.

    Rejects absolute components, ``..`` traversal, symlinks that leave the
    tree, and — on Windows — the reserved device names (``CON``, ``NUL``,
    ``COM1``…) that still resolve to devices no matter which folder you ask
    for them in.
    """
    root = root.resolve()
    for part in parts:
        if not part:
            raise PathEscapeError("Empty path component.")
        if os.path.isabs(part) or part.startswith(("/", "\\")):
            raise PathEscapeError(f"Absolute path component: {part!r}")
        if any(sep in part for sep in (os.sep, os.altsep) if sep):
            # Sub-paths are legitimate (``sfx/usergun.wav``); check each piece.
            pieces = part.replace("\\", "/").split("/")
        else:
            pieces = [part]
        for piece in pieces:
            if piece in ("..", "."):
                raise PathEscapeError(f"Traversal component: {part!r}")
            if _is_reserved_device(piece):
                raise PathEscapeError(f"Reserved device name: {piece!r}")

    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - platform dependent
        raise PathEscapeError(f"Cannot resolve {candidate}: {exc}") from exc

    if resolved != root and root not in resolved.parents:
        raise PathEscapeError(f"{resolved} escapes {root}")
    return resolved


_WINDOWS_DEVICES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)


def _is_reserved_device(name: str) -> bool:
    if sys.platform != "win32":
        return False
    return name.split(".")[0].strip().lower() in _WINDOWS_DEVICES
