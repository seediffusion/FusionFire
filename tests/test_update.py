"""The auto updater.

None of these touch the network or an installed copy of the game. Everything
the updater has to get right is a decision about a string, an archive or a
path, and those are exercised directly:

* which of two version tags is newer, including the ones that mean "do
  nothing" -- a wrong answer here overwrites a player's installation;
* what an archive is allowed to contain, because a zip is a list of names
  chosen by whoever built it and one of those names may be
  ``..\\..\\Windows\\System32``;
* that the swap helper waits for the game before touching anything, and asks
  for elevation exactly when the target cannot be written to.

The one part not covered here is the helper actually running, which needs a
real second process and a real installation to be worth anything.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from fusionfire import update
from fusionfire.update import UpdateError, is_newer


# ----------------------------------------------------------------------
# Version comparison
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "candidate, current, newer",
    [
        ("2521", "2520", True),
        ("2520", "2520", False),
        ("2519", "2520", False),
        # Tags are typed by hand and pick up decoration.
        ("v2521", "2520", True),
        ("v2520", "2520", False),
        ("2521-beta", "2520", True),
        # A dotted tag against a plain one: 2520.1 is a later 2520.
        ("2520.1", "2520", True),
        ("2520", "2520.1", False),
        ("2.5.21", "2.5.20", True),
    ],
)
def test_which_version_is_newer(candidate, current, newer):
    assert is_newer(candidate, current) is newer


@pytest.mark.parametrize("tag", ["", "latest", "release", "v", "  "])
def test_an_unreadable_tag_is_never_newer(tag):
    """An update overwrites what the player has. A tag nobody can read has to
    mean "leave it alone", never "install it and find out"."""
    assert is_newer(tag, "2520") is False


def test_a_readable_tag_against_an_unreadable_version_is_not_newer():
    assert is_newer("2521", "unknown") is False


# ----------------------------------------------------------------------
# Reading GitHub's answer
# ----------------------------------------------------------------------
class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _github(monkeypatch, payload) -> None:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(
        update.urllib.request, "urlopen", lambda request, timeout=None: _FakeResponse(body)
    )


def test_check_reads_the_tag_and_the_notes(monkeypatch):
    _github(
        monkeypatch,
        {
            "tag_name": "2600",
            "body": "  Fixed the thing.  ",
            "html_url": "https://github.com/seediffusion/FusionFire/releases/tag/2600",
        },
    )
    release = update.check()
    assert release.tag == "2600"
    assert release.newer is True
    assert release.notes == "Fixed the thing."
    assert release.url.startswith("https://github.com/")


def test_check_takes_the_download_address_from_the_release(monkeypatch):
    """Guessing the file name has been wrong once already: the releases ship
    ``fusion-fire.zip`` where the documented address says ``FusionFire.zip``,
    and an updater built on the guess 404s on every one of them."""
    _github(
        monkeypatch,
        {
            "tag_name": "2600",
            "assets": [
                {"name": "Fusion_Fire_Setup.exe", "browser_download_url": "https://github.com/x/setup.exe"},
                {
                    "name": "fusion-fire.zip",
                    "browser_download_url": (
                        "https://github.com/seediffusion/FusionFire/releases/"
                        "download/v2600/fusion-fire.zip"
                    ),
                },
            ],
        },
    )
    assert update.check().download_url.endswith("fusion-fire.zip")


def test_check_falls_back_to_the_documented_address(monkeypatch):
    """A release that lists no zip is not a reason to refuse to update."""
    _github(monkeypatch, {"tag_name": "2600", "assets": []})
    assert update.check().download_url == update.DOWNLOAD_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x/FusionFire.zip",          # not https
        "https://evil.example.org/FusionFire.zip",     # not GitHub
        "https://github.com.evil.org/FusionFire.zip",  # nor is this
    ],
)
def test_check_refuses_an_asset_address_it_did_not_ask_about(monkeypatch, url):
    """The reply decides where this process downloads an executable from, so
    "whatever the JSON said" is not good enough."""
    _github(
        monkeypatch,
        {"tag_name": "2600", "assets": [{"name": "FusionFire.zip", "browser_download_url": url}]},
    )
    assert update.check().download_url == update.DOWNLOAD_URL


def test_check_falls_back_to_the_release_name(monkeypatch):
    _github(monkeypatch, {"tag_name": None, "name": "2600"})
    assert update.check().tag == "2600"


def test_check_refuses_a_release_with_no_version_at_all(monkeypatch):
    _github(monkeypatch, {"body": "notes but no tag"})
    with pytest.raises(UpdateError):
        update.check()


def test_check_refuses_a_reply_that_is_not_json(monkeypatch):
    _github(monkeypatch, b"<html>rate limited</html>")
    with pytest.raises(UpdateError):
        update.check()


def test_check_refuses_a_non_https_page_url(monkeypatch):
    """A hostile or broken reply must not put a ``javascript:`` or ``file:``
    address anywhere the dialog might offer to open."""
    _github(monkeypatch, {"tag_name": "2600", "html_url": "javascript:alert(1)"})
    assert update.check().url == update.RELEASES_PAGE


# ----------------------------------------------------------------------
# Unpacking what was downloaded
# ----------------------------------------------------------------------
def _zip(tmp_path: Path, entries: dict[str, bytes], name: str = "release.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for entry, data in entries.items():
            archive.writestr(entry, data)
    return path


def test_staging_strips_the_archives_own_top_folder(tmp_path):
    """Releases are built as ``dist/FusionFire`` and zipped with that folder
    inside. Leaving it on would put a FusionFire folder *inside* the game
    instead of replacing it."""
    archive = _zip(
        tmp_path,
        {
            "FusionFire/FusionFire.exe": b"exe",
            "FusionFire/sounds/sfx/usergun.wav": b"wav",
        },
    )
    staged = update.stage(archive, tmp_path / "staging")

    assert (staged / "FusionFire.exe").read_bytes() == b"exe"
    assert (staged / "sounds" / "sfx" / "usergun.wav").read_bytes() == b"wav"
    assert not (staged / "FusionFire").exists()


def test_staging_keeps_a_flat_archive_flat(tmp_path):
    archive = _zip(tmp_path, {"FusionFire.exe": b"exe", "sounds/x.wav": b"wav"})
    staged = update.stage(archive, tmp_path / "staging")
    assert (staged / "FusionFire.exe").read_bytes() == b"exe"


def test_staging_clears_out_a_half_finished_previous_attempt(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "leftover.txt").write_text("from a run that was cancelled")

    archive = _zip(tmp_path, {"FusionFire.exe": b"exe"})
    staged = update.stage(archive, staging)

    assert not (staged / "leftover.txt").exists()


@pytest.mark.parametrize(
    "entry",
    [
        "../escaped.txt",
        "FusionFire/../../escaped.txt",
        "/etc/passwd",
        "C:/Windows/System32/evil.dll",
        "..\\escaped.txt",
    ],
)
def test_staging_refuses_an_archive_that_writes_outside_itself(tmp_path, entry):
    """The releases are our own, but that is a fact about today's release and
    not about the file that actually arrived, so the file is what is checked."""
    archive = _zip(tmp_path, {entry: b"nope"})
    with pytest.raises(UpdateError):
        update.stage(archive, tmp_path / "staging")
    assert not (tmp_path / "escaped.txt").exists()


def test_staging_refuses_an_empty_archive(tmp_path):
    archive = _zip(tmp_path, {})
    with pytest.raises(UpdateError):
        update.stage(archive, tmp_path / "staging")


def test_staging_refuses_something_that_is_not_a_zip(tmp_path):
    broken = tmp_path / "release.zip"
    broken.write_bytes(b"404: Not Found")
    with pytest.raises(UpdateError):
        update.stage(broken, tmp_path / "staging")


def test_staging_refuses_an_archive_that_expands_absurdly(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "MAX_EXTRACT_BYTES", 16)
    archive = _zip(tmp_path, {"big.bin": b"x" * 4096})
    with pytest.raises(UpdateError):
        update.stage(archive, tmp_path / "staging")


def test_a_failed_staging_leaves_nothing_behind(tmp_path):
    staging = tmp_path / "staging"
    archive = _zip(tmp_path, {"../escaped.txt": b"nope"})
    with pytest.raises(UpdateError):
        update.stage(archive, staging)
    assert not staging.exists()


# ----------------------------------------------------------------------
# Downloading
# ----------------------------------------------------------------------
class _SizedResponse(_FakeResponse):
    def __init__(self, body: bytes, total: int | None = None) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if total is None else total)}


def test_download_refuses_an_absurdly_large_release(tmp_path, monkeypatch):
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda request, timeout=None: _SizedResponse(b"x", total=update.MAX_DOWNLOAD_BYTES + 1),
    )
    with pytest.raises(UpdateError):
        update.download(tmp_path / "release.zip")
    assert not (tmp_path / "release.zip").exists()


def test_a_cancelled_download_deletes_its_half_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda request, timeout=None: _SizedResponse(b"x" * 200_000),
    )
    destination = tmp_path / "release.zip"
    with pytest.raises(update.UpdateCancelled):
        update.download(destination, progress=lambda done, total: False)
    assert not destination.exists(), "a cancelled download left a partial file"


# ----------------------------------------------------------------------
# The swap helper
# ----------------------------------------------------------------------
def _helper_text(tmp_path: Path, **kwargs) -> str:
    staging = tmp_path / "update"
    staging.mkdir(exist_ok=True)
    helper = update.write_helper(
        staging,
        tmp_path / "installed",
        pid=kwargs.pop("pid", 4321),
        image=kwargs.pop("image", "FusionFire.exe"),
        relaunch=kwargs.pop("relaunch", tmp_path / "installed" / "FusionFire.exe"),
        **kwargs,
    )
    return helper.read_text(encoding="utf-8")


def test_the_helper_waits_for_the_game_before_touching_anything(tmp_path):
    """Windows will not overwrite a running executable, and a helper that
    copied first and checked later would leave a half-replaced install."""
    body = _helper_text(tmp_path, pid=4321)
    wait_at = body.index("4321")
    copy_at = body.lower().index("robocopy" if os.name == "nt" else "cp -r")
    assert wait_at < copy_at, "the helper starts copying before the game has exited"


def test_the_helper_lives_outside_the_folder_it_deletes(tmp_path):
    """Its last act is to remove the staging folder, and a script cannot
    tidy away the directory it is running from."""
    staging = tmp_path / "update"
    staging.mkdir()
    helper = update.write_helper(
        staging, tmp_path / "installed", pid=1, image="x.exe", relaunch=None
    )
    assert helper.parent != staging
    assert helper.parent == staging.parent


def test_the_helper_restarts_the_game_it_just_replaced(tmp_path):
    body = _helper_text(tmp_path)
    assert "FusionFire.exe" in body


def test_a_helper_with_nothing_to_restart_does_not_try(tmp_path):
    body = _helper_text(tmp_path, relaunch=None)
    assert "start \"\" \"" not in body


@pytest.mark.skipif(os.name != "nt", reason="the batch helper is Windows-only")
def test_the_windows_helper_is_written_with_the_line_endings_cmd_needs(tmp_path):
    """A batch file with bare newlines runs -- right up until it meets a
    label or a parenthesised block, and then quietly does something else."""
    staging = tmp_path / "update"
    staging.mkdir()
    helper = update.write_helper(
        staging, tmp_path / "installed", pid=1, image="x.exe", relaunch=None
    )
    assert b"\r\n" in helper.read_bytes()
    assert b"\n" not in helper.read_bytes().replace(b"\r\n", b"")


@pytest.mark.skipif(os.name != "nt", reason="the batch helper is Windows-only")
def test_the_elevated_helper_hands_the_game_back_to_the_desktop(tmp_path):
    """Restarting straight from an elevated script would leave the player
    running an audio game as administrator for the rest of the session."""
    plain = _helper_text(tmp_path, elevated=False)
    elevated = _helper_text(tmp_path, elevated=True)
    assert "explorer.exe" not in plain
    assert "explorer.exe" in elevated


def test_the_helper_matches_the_process_by_name_not_by_a_translated_message(tmp_path):
    """tasklist's "no tasks are running" is localised. A helper that waits on
    that string works on an English Windows and corrupts a German one."""
    body = _helper_text(tmp_path, image="FusionFire.exe")
    assert "No tasks" not in body


@pytest.mark.skipif(os.name != "nt", reason="Windows process flags")
def test_the_helper_is_launched_with_a_console_it_can_use(monkeypatch, tmp_path):
    """The bug a unit test could not have found, and a rehearsal did.

    ``DETACHED_PROCESS`` hides the helper by giving it no console at all --
    and the helper's whole first act is to run ``tasklist`` and ``ping``.
    With no console those do nothing, cmd falls off the end of the script,
    and the update silently never happens. ``CREATE_NO_WINDOW`` hides it just
    as well and still gives it somewhere to run them.
    """
    import subprocess

    seen = {}

    class _Popen:
        def __init__(self, args, creationflags=0, **kwargs):
            seen["flags"] = creationflags

    monkeypatch.setattr(update.subprocess, "Popen", _Popen)
    update._launch_plain(tmp_path / "apply_update.cmd")

    assert seen["flags"] & subprocess.CREATE_NO_WINDOW
    assert not seen["flags"] & subprocess.DETACHED_PROCESS, (
        "the helper was launched with no console, so it cannot wait for the game"
    )


# ----------------------------------------------------------------------
# Deciding whether elevation is needed
# ----------------------------------------------------------------------
def test_a_writable_folder_is_reported_writable(tmp_path):
    assert update.writable(tmp_path) is True
    assert not list(tmp_path.iterdir()), "the write probe left a file behind"


def test_a_folder_that_cannot_be_created_is_not_writable(tmp_path):
    # A path *through* a regular file can never be made into a directory, on
    # any platform, which is a portable stand-in for a folder we lack rights
    # to -- and unlike chmod, it is one Windows honours.
    blocker = tmp_path / "afile"
    blocker.write_text("not a directory")
    assert update.writable(blocker / "inside") is False


def test_install_refuses_when_there_is_nothing_staged(tmp_path):
    empty = tmp_path / "staging"
    empty.mkdir()
    with pytest.raises(UpdateError):
        update.install(empty, tmp_path / "installed")


def test_install_launches_the_helper_without_elevation_when_it_can(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "FusionFire.exe").write_bytes(b"new")
    target = tmp_path / "installed"
    target.mkdir()

    launched: list[str] = []
    monkeypatch.setattr(update, "_launch_plain", lambda helper: launched.append("plain"))
    monkeypatch.setattr(update, "_launch_elevated", lambda helper: launched.append("elevated"))

    update.install(staging, target)
    assert launched == ["plain"]


def test_install_asks_for_elevation_when_the_game_is_somewhere_protected(
    tmp_path, monkeypatch
):
    """The Program Files case, which is where the installer puts it by
    default and therefore the case that has to work."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "FusionFire.exe").write_bytes(b"new")
    target = tmp_path / "installed"
    target.mkdir()

    launched: list[str] = []
    monkeypatch.setattr(update, "writable", lambda directory: False)
    monkeypatch.setattr(update, "_launch_plain", lambda helper: launched.append("plain"))
    monkeypatch.setattr(update, "_launch_elevated", lambda helper: launched.append("elevated"))
    monkeypatch.setattr(update.sys, "platform", "win32")

    update.install(staging, target)
    assert launched == ["elevated"], "a protected install directory did not ask for rights"


def test_a_refused_elevation_prompt_is_a_cancel_not_a_failure(tmp_path, monkeypatch):
    """Saying no to the UAC prompt is a decision, not a fault, and must not
    be reported to the player as one."""
    import ctypes

    class _Shell32:
        @staticmethod
        def ShellExecuteW(*args):
            return 5  # SE_ERR_ACCESSDENIED

    class _WinDLL:
        shell32 = _Shell32()

    monkeypatch.setattr(ctypes, "windll", _WinDLL(), raising=False)
    with pytest.raises(update.UpdateCancelled):
        update._launch_elevated(tmp_path / "apply_update.cmd")


def test_the_staging_folder_is_somewhere_the_player_can_write(monkeypatch, tmp_path):
    """Under the user's own data directory, never beside the game -- the
    whole point is that the game's folder may be read-only."""
    monkeypatch.setattr(update.paths, "data_dir", lambda: tmp_path)
    assert update.staging_dir().parent == tmp_path


def test_the_download_address_is_the_release_asset():
    assert update.DOWNLOAD_URL == (
        "https://github.com/seediffusion/FusionFire/releases/latest/download/FusionFire.zip"
    )
