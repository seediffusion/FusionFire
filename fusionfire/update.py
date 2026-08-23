"""Checking for a new release, and installing one.

The game ships as a zip and as an installer whose default target is
``C:\\Program Files\\Fusion Fire`` — a directory the player running the game
cannot write to. An updater that only works when the game was unzipped
somewhere friendly is an updater that does not work for most of the people
who have it installed, so the awkward case is the one this module is built
around rather than the one it apologises for.

The flow is three steps, and only the middle one happens inside the running
game:

1. **Check.** Ask GitHub for the latest release tag and compare it with
   :data:`fusionfire.__version__`. One HTTPS GET, no authentication, no
   identifying information beyond what any HTTP client sends.
2. **Download and stage.** Fetch ``FusionFire.zip`` and unpack it into a
   staging folder under the *user's* data directory, which is writable by
   definition — the game already keeps its settings there. Nothing in the
   installed copy has been touched at this point, so a failure or a cancel
   costs nothing but the download.
3. **Swap.** Write a small helper script, launch it, and quit. The helper
   waits for this process to exit (it cannot replace a running .exe), copies
   the staged files over the installed ones, deletes the staging folder and
   starts the game again.

Step 3 is a separate process for one unavoidable reason and one useful one.
Unavoidable: Windows will not let a running executable be overwritten, so
*something* has to outlive the game. Useful: a separate process can be
launched elevated. When the game's folder is not writable the helper is
started through ``ShellExecuteW`` with the ``runas`` verb, which is the one
and only point in the whole update where Windows asks the player for
anything — and it is asked once, after they have already said yes to
updating, rather than at launch.

Everything here is deliberately testable without a network or an
installation: the version comparison, the archive validation, the staging
and the helper script are all plain functions over paths and strings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from . import paths

log = logging.getLogger(__name__)

#: The repository the releases come from.
REPOSITORY = "seediffusion/FusionFire"

#: Where the latest release's tag is read from. GitHub's REST API needs no
#: authentication for a public repository's releases.
RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"

#: The build a release is expected to attach, used when the release itself
#: does not say. ``/releases/latest/download/<name>`` is GitHub's own stable
#: address for an asset, so this needs no tag in it -- but it does need the
#: asset to be called exactly this, and releases have shipped it as
#: ``fusion-fire.zip`` too. Which is why the address is normally taken from
#: the release rather than assumed; see :func:`check`.
DOWNLOAD_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/FusionFire.zip"

#: The release page, for a player who would rather do it by hand.
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"

#: How long the version check may take. Short: it runs at startup, and a
#: slow or unreachable GitHub must never hold the menu back.
CHECK_TIMEOUT = 10.0
#: How long the download may stall before it is abandoned.
DOWNLOAD_TIMEOUT = 60.0

#: Refuse an absurd download rather than filling the player's disk with it.
#: The onedir build is tens of megabytes; this is room to grow into.
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024
#: And a bound on what those bytes may expand into, so a zip bomb cannot
#: turn a 90 MB download into a full disk.
MAX_EXTRACT_BYTES = 1200 * 1024 * 1024

#: Read in chunks so progress can be reported and a cancel can be noticed.
_CHUNK = 64 * 1024

#: The executable the helper restarts, and the name that identifies a
#: staged archive as the right one.
EXECUTABLE_NAME = "FusionFire.exe"

#: Hosts a release asset may be downloaded from. GitHub serves them from
#: ``github.com`` and redirects to its own object store; anything else in an
#: asset list is a reply telling this process to fetch an executable from
#: somewhere we did not ask about.
_ASSET_HOSTS = frozenset({"github.com", "www.github.com", "objects.githubusercontent.com"})


class UpdateError(Exception):
    """The update could not be checked, downloaded, staged or installed."""


class UpdateCancelled(UpdateError):
    """The player stopped it — a cancel, or a refused elevation prompt."""


@dataclass(frozen=True)
class Release:
    """What the check found."""

    tag: str
    #: True when :data:`tag` is newer than the running build.
    newer: bool
    #: The release's own notes, trimmed. Empty when it published none.
    notes: str = ""
    url: str = RELEASES_PAGE
    #: Where the build actually lives, taken from the release's own asset
    #: list. Falls back to :data:`DOWNLOAD_URL` when the release lists no
    #: zip -- a guess is better than refusing to update, but only just, which
    #: is why it is the fallback rather than the rule.
    download_url: str = DOWNLOAD_URL


# ----------------------------------------------------------------------
# Comparing versions
# ----------------------------------------------------------------------
def _parts(version: str) -> tuple[int, ...]:
    """The numeric run of a version string, as a tuple.

    Fusion Fire numbers its builds with a bare integer (``2520``), but tags
    are written by hand and pick up prefixes and dots: ``v2520``, ``2.5.20``,
    ``2520-beta``. Pulling the digit groups out and comparing those handles
    every shape the tag has taken without teaching this a version grammar it
    would only get wrong.
    """
    return tuple(int(group) for group in re.findall(r"\d+", version))


def is_newer(candidate: str, current: str = __version__) -> bool:
    """True when ``candidate`` is a later release than ``current``.

    Anything that cannot be read as a number at all is *not* newer. An
    update is a thing that overwrites the player's installation, so an
    unreadable tag has to mean "do nothing", never "install it and see".
    """
    theirs, ours = _parts(candidate), _parts(current)
    if not theirs or not ours:
        return False
    # Compare on equal footing: 2520 against 2520.1 is older, not equal.
    width = max(len(theirs), len(ours))
    theirs += (0,) * (width - len(theirs))
    ours += (0,) * (width - len(ours))
    return theirs > ours


# ----------------------------------------------------------------------
# Asking GitHub what the latest release is
# ----------------------------------------------------------------------
def _asset_url(raw: dict) -> str:
    """The download address of the release's build, from its own asset list.

    Guessing the file name has already been wrong once: the releases carry
    ``fusion-fire.zip`` where the documented address says ``FusionFire.zip``,
    and an updater built on the guess would have 404'd on every one of them.
    Reading the name off the release costs nothing -- the reply is already
    parsed for the tag -- and it keeps working whichever way the asset is
    named next.

    Only an ``https`` address on GitHub's own hosts is accepted. The reply
    decides where this process downloads an executable from, so "whatever
    the JSON said" is not good enough.
    """
    assets = raw.get("assets")
    if not isinstance(assets, list):
        return DOWNLOAD_URL
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not name.lower().endswith(".zip"):
            continue
        if not isinstance(url, str):
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if url.startswith("https://") and host in _ASSET_HOSTS:
            return url
    return DOWNLOAD_URL


def _clean_tag(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value.strip() if ch.isprintable() and not ch.isspace())
    return cleaned[:64]


def check(timeout: float = CHECK_TIMEOUT, url: str = RELEASE_API_URL) -> Release:
    """Ask for the latest release. Raises :class:`UpdateError` if it cannot.

    The caller decides what a failure means. At startup it means nothing at
    all — a player without a working connection is not told their game could
    not phone home — while a check they asked for says so plainly.
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"FusionFire/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(256 * 1024)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise UpdateError(f"Could not reach GitHub to check for updates: {exc}") from exc

    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"GitHub's reply was not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise UpdateError("GitHub's reply was not a release object.")

    tag = _clean_tag(raw.get("tag_name")) or _clean_tag(raw.get("name"))
    if not tag:
        raise UpdateError("That release has no version tag, so there is nothing to compare.")

    notes = raw.get("body")
    notes = notes.strip()[:4000] if isinstance(notes, str) else ""
    page = raw.get("html_url")
    page = page if isinstance(page, str) and page.startswith("https://") else RELEASES_PAGE
    return Release(
        tag=tag,
        newer=is_newer(tag),
        notes=notes,
        url=page,
        download_url=_asset_url(raw),
    )


# ----------------------------------------------------------------------
# Downloading and unpacking
# ----------------------------------------------------------------------
def staging_dir() -> Path:
    """Where a new version is unpacked before anything is replaced.

    Under the user's own data directory rather than beside the game: that is
    the one place known to be writable, which is exactly the property the
    game's own folder cannot be relied on for.
    """
    return paths.data_dir() / "update"


def download(
    destination: Path,
    url: str = DOWNLOAD_URL,
    *,
    timeout: float = DOWNLOAD_TIMEOUT,
    progress: Callable[[int, int], bool] | None = None,
) -> Path:
    """Fetch the release archive to ``destination``.

    ``progress`` is called with ``(bytes so far, total or 0)`` and returns
    False to cancel, which is how the dialog's Cancel button stops a download
    that has already started.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": f"FusionFire/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise UpdateError(f"That download is {total // (1024 * 1024)} MB. Too large.")
            done = 0
            with destination.open("wb") as out:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_DOWNLOAD_BYTES:
                        raise UpdateError("The download outgrew its size limit.")
                    out.write(chunk)
                    if progress is not None and not progress(done, total):
                        raise UpdateCancelled("Cancelled.")
    except UpdateError:
        destination.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError(f"The download failed: {exc}") from exc
    return destination


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The entries of ``archive``, checked before any of them is written.

    A zip is a list of names chosen by whoever built it, and a name is
    allowed to be ``..\\..\\Windows\\System32\\anything`` or an absolute path.
    Extracting one of those writes outside the folder that was asked for.
    These releases are our own, but "it is our own archive" is a property of
    today's release, not of the file that actually arrives, so the check is
    on the file.
    """
    members: list[zipfile.ZipInfo] = []
    expanded = 0
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue  # directories are created as their files need them
        if name.startswith("/") or ":" in name.split("/")[0]:
            raise UpdateError(f"The archive holds an absolute path: {info.filename!r}")
        if any(part in ("..", "") for part in name.split("/")):
            raise UpdateError(f"The archive tries to escape its folder: {info.filename!r}")
        expanded += info.file_size
        if expanded > MAX_EXTRACT_BYTES:
            raise UpdateError("The archive expands to far more than a release should.")
        members.append(info)
    if not members:
        raise UpdateError("The downloaded archive is empty.")
    return members


def _common_root(names: list[str]) -> str:
    """The single top-level folder every entry sits in, if there is one.

    Releases are built as ``dist/FusionFire/`` and zipped, so the archive
    usually carries that folder inside it. Stripping it is the difference
    between replacing the game and creating a ``FusionFire`` folder inside
    the game.
    """
    roots = {name.split("/")[0] for name in names}
    if len(roots) != 1:
        return ""
    root = roots.pop()
    # Only a real containing folder, never a lone file at the top level.
    return root if all("/" in name for name in names) else ""


def stage(archive_path: Path, into: Path | None = None) -> Path:
    """Unpack a downloaded archive, ready to be swapped in. Returns the folder.

    The staging folder is emptied first, so a previous attempt that was
    abandoned half way cannot contribute files to this one.
    """
    into = staging_dir() if into is None else into
    if into.exists():
        shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive)
            names = [info.filename.replace("\\", "/") for info in members]
            root = _common_root(names)
            for info, name in zip(members, names):
                relative = name[len(root) + 1:] if root else name
                target = into.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, _CHUNK)
    except UpdateError:
        # A rejected archive is still a failed staging: leave nothing behind
        # for the next attempt to inherit half of.
        shutil.rmtree(into, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(into, ignore_errors=True)
        raise UpdateError(f"The downloaded archive could not be unpacked: {exc}") from exc

    if not any(into.iterdir()):
        shutil.rmtree(into, ignore_errors=True)
        raise UpdateError("The downloaded archive unpacked to nothing.")
    return into


# ----------------------------------------------------------------------
# Swapping the staged copy in
# ----------------------------------------------------------------------
def writable(directory: Path) -> bool:
    """Whether this process can actually write into ``directory``.

    Asked by trying, not by reading permissions: on Windows the answer
    depends on the ACL, the integrity level, UAC virtualisation and whether
    the folder is under Program Files, and every attempt to work it out from
    the outside gets one of those wrong.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = tempfile.NamedTemporaryFile(dir=directory, prefix=".ff-write-", delete=True)
    except OSError:
        return False
    probe.close()
    return True


def _windows_script(
    staging: Path,
    target: Path,
    pid: int,
    image: str,
    relaunch: Path | None,
    elevated: bool,
) -> str:
    """The batch file that outlives the game and does the swap.

    The process is waited on by PID *and* image name, and the wait ends when
    the image name stops appearing in tasklist's output. Looking for the name
    rather than for "No tasks are running" matters: that message is
    translated, and a helper that only works on an English Windows is a
    helper that corrupts the installation on a German one.

    When the swap needed administrator rights the game is restarted through
    Explorer, which runs at the desktop's own integrity level. Starting it
    directly from an elevated script would hand the player a game running as
    administrator for the rest of the session, which nothing about an audio
    game justifies.
    """
    if relaunch is None:
        start = "rem nothing to restart"
    elif elevated:
        start = f'start "" explorer.exe "{relaunch}"'
    else:
        start = f'start "" "{relaunch}"'
    return f"""@echo off
rem Fusion Fire updater. Written by the game, run once, deletes itself.
rem Waits for the game to exit -- Windows will not overwrite a running exe --
rem then copies the staged build over the installed one and starts it again.
setlocal
set "STAGING={staging}"
set "TARGET={target}"
set /a TRIES=0

:wait
tasklist /FI "PID eq {pid}" /FI "IMAGENAME eq {image}" /NH 2>nul | find /I "{image}" >nul
if errorlevel 1 goto swap
set /a TRIES+=1
if %TRIES% GEQ 150 (
    echo Fusion Fire is still running, so it cannot be updated.
    echo Close it and run the update again. Nothing has been changed.
    pause
    goto done
)
ping -n 2 127.0.0.1 >nul
goto wait

:swap
robocopy "%STAGING%" "%TARGET%" /E /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS >nul
rem Robocopy uses exit codes 0-7 for success; 8 and above are real failures.
if errorlevel 8 (
    echo Fusion Fire could not be updated. The game has been left as it was.
    pause
    goto done
)
{start}

:done
rmdir /s /q "%STAGING%" 2>nul
(goto) 2>nul & del "%~f0"
"""


def _posix_script(
    staging: Path,
    target: Path,
    pid: int,
    image: str,
    relaunch: Path | None,
    elevated: bool,
) -> str:
    """The same job for a source checkout on macOS or Linux."""
    start = f'"{relaunch}" &' if relaunch is not None else ": # nothing to restart"
    return f"""#!/bin/sh
# Fusion Fire updater. Written by the game, run once, deletes itself.
while kill -0 {pid} 2>/dev/null; do sleep 1; done
cp -R "{staging}/." "{target}/" || {{
    echo "Fusion Fire could not be updated. The game has been left as it was."
    exit 1
}}
{start}
rm -rf "{staging}"
rm -f "$0"
"""


def write_helper(
    staging: Path,
    target: Path,
    *,
    pid: int,
    image: str,
    relaunch: Path | None,
    elevated: bool = False,
) -> Path:
    """Write the swap helper and return its path.

    It lives in the staging folder's parent rather than in the staging folder
    itself, because the helper deletes the staging folder as its last act and
    a script cannot tidy away the directory it is running from.
    """
    windows = sys.platform == "win32"
    build = _windows_script if windows else _posix_script
    body = build(staging, target, pid, image, relaunch, elevated)
    helper = staging.parent / ("apply_update.cmd" if windows else "apply_update.sh")
    # CRLF for cmd.exe: a batch file with bare newlines runs, until it meets
    # a label or a parenthesised block, and then does something else instead.
    helper.write_text(
        body,
        encoding="ascii" if windows else "utf-8",
        newline="\r\n" if windows else "\n",
    )
    if not windows:
        helper.chmod(0o755)
    return helper


def _launch_elevated(helper: Path) -> None:
    """Run the helper as administrator, via the UAC prompt.

    The only moment in the update that asks the player for anything, and it
    comes after they have already agreed to update — never at launch, and
    never for a player whose game is somewhere they can write to.
    """
    import ctypes

    SW_HIDE = 0
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "cmd.exe", f'/c "{helper}"', None, SW_HIDE
    )
    # ShellExecuteW returns a fake HINSTANCE: anything over 32 is success.
    if result == 5:  # SE_ERR_ACCESSDENIED — the prompt was refused
        raise UpdateCancelled("Permission refused. Nothing has been changed.")
    if result <= 32:
        raise UpdateError(f"Could not start the updater as administrator (error {result}).")


def _launch_plain(helper: Path) -> None:
    """Start the helper so it outlives this process, and out of sight.

    ``CREATE_NO_WINDOW``, not ``DETACHED_PROCESS``. Both hide the helper, but
    detached means *no console at all*, and the helper's whole first act is
    to run console programs: ``tasklist`` to watch for the game exiting and
    ``ping`` to wait between looks. Without a console those do nothing, cmd
    falls off the end of the script, and the update silently never happens --
    which is exactly what it did. A hidden console of its own gives the
    helper somewhere to run them and still shows the player nothing.

    ``CREATE_NEW_PROCESS_GROUP`` keeps a Ctrl+C aimed at the game from
    reaching the process that is meant to survive it.
    """
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            ["cmd.exe", "/c", str(helper)],
            creationflags=flags,
            close_fds=True,
        )
    else:
        subprocess.Popen(["/bin/sh", str(helper)], start_new_session=True, close_fds=True)


def install(staging: Path, target: Path | None = None, *, relaunch: bool = True) -> Path:
    """Start the swap and return the helper that will carry it out.

    This returns while the helper is still waiting for the game to exit — it
    cannot do otherwise, since what it is waiting for is *this* process. The
    caller's next act must be to quit.
    """
    target = paths.game_dir() if target is None else target
    if not staging.exists() or not any(staging.iterdir()):
        raise UpdateError("There is nothing staged to install.")

    executable = Path(sys.executable) if getattr(sys, "frozen", False) else None
    if relaunch and executable is None:
        candidate = target / EXECUTABLE_NAME
        executable = candidate if candidate.exists() else None

    needs_elevation = not writable(target)
    if needs_elevation and sys.platform != "win32":
        raise UpdateError(
            f"Cannot write to {target}. Move the game somewhere you own, or "
            "update it by hand."
        )

    helper = write_helper(
        staging,
        target,
        pid=os.getpid(),
        image=Path(sys.executable).name,
        relaunch=executable if relaunch else None,
        elevated=needs_elevation,
    )
    if needs_elevation:
        _launch_elevated(helper)
    else:
        _launch_plain(helper)
    return helper
