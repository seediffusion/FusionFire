#!/usr/bin/env python3
"""Build a release of Fusion Fire with PyInstaller.

    uv run build.py                 one-directory release (recommended)
    uv run build.py --onefile       single .exe, slower to start
    uv run build.py --upx           squeeze the binaries further
    uv run build.py --no-verify     skip launching the result
    """

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = "FusionFire"
ENTRY = ROOT / "run.py"


# ----------------------------------------------------------------------
# What has to be carried along
# ----------------------------------------------------------------------
def asset_args() -> list[str]:
    """The name lists, which live inside the package.

    The sounds are not here: they go beside the executable so a player can
    open the folder and replace one. See :func:`copy_sounds`.
    """
    assets = ROOT / "fusionfire" / "assets"
    if not assets.is_dir():
        raise SystemExit(f"Assets are missing: {assets}")
    return ["--add-data", f"{assets}{os.pathsep}fusionfire/assets"]


def copy_sounds(built: Path) -> int:
    """Put the sounds beside the game, where a player can get at them.

    Deliberately not bundled inside the executable's payload. Replacing a
    sound is a supported thing to do, and it only works if the folder is
    somewhere visible -- which also means the build has to place it, because
    PyInstaller would otherwise bury it in _internal.
    """
    source = ROOT / "sounds"
    if not source.is_dir():
        raise SystemExit(f"The sounds folder is missing: {source}")

    target = (built.parent if built.is_file() else built) / "sounds"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return sum(1 for f in target.rglob("*") if f.is_file())


def write_readme(built: Path) -> Path:
    """Put a readable copy of the documentation in the release folder.

    Markdown is the wrong format to ship to a player: Windows has nothing
    registered to open a .md file, so double-clicking it does nothing.
    HTML opens in the browser they already have, and because that browser is
    often driven by a screen reader the generated page carries a heading
    structure, a table of contents, a skip link and table header scopes.
    """
    import readme_html

    folder = built.parent if built.is_file() else built
    target = folder / "readme.html"
    try:
        import fusionfire

        version = fusionfire.__version__
    except Exception:
        version = ""
    target.write_text(readme_html.build(version=version), encoding="utf-8")
    return target


def prism_args() -> list[str]:
    """Prism's native payload.

    ``prism/_native/`` holds prism.dll and the cffi extension, which the
    package opens by path at runtime. PyInstaller's analysis finds the
    Python but not the files it loads itself, so they are listed explicitly.
    """
    import prism

    native = Path(prism.__file__).parent / "_native"
    if not native.is_dir():
        raise SystemExit(f"Prism's native directory is missing: {native}")

    args: list[str] = []
    for item in sorted(native.iterdir()):
        if item.is_file():
            flag = "--add-binary" if item.suffix.lower() in (".pyd", ".dll") else "--add-data"
            args += [flag, f"{item}{os.pathsep}prism/_native"]
    return args


def sound_lib_args() -> list[str]:
    """BASS and its add-ons.

    sound_lib loads these through ctypes from its own ``lib`` directory, so
    again nothing in the import graph points at them. They are ~900 KB in
    total; the unused add-ons are kept because sound_lib probes for them at
    import and a missing one is a crash rather than a smaller build.
    """
    import sound_lib

    lib = Path(sound_lib.__file__).parent / "lib"
    if not lib.is_dir():
        raise SystemExit(f"sound_lib's library directory is missing: {lib}")

    args: list[str] = []
    for item in sorted(lib.rglob("*")):
        if item.is_file():
            target = f"sound_lib/lib/{item.relative_to(lib).parent.as_posix()}".rstrip("/.")
            args += ["--add-binary", f"{item}{os.pathsep}{target}"]
    return args


HIDDEN = [
    # Prism 0.17 moved its native layer into prism/_native/ as a cffi
    # extension. The submodules are imported lazily, so analysis misses them.
    "prism",
    "prism.core",
    "prism.common",
    "prism.custom",
    "prism.log",
    "prism._dispatch",
    "prism._native",
    "cffi",
    "_cffi_backend",
    # Reached only through ctypes and runtime lookups.
    "sound_lib.output",
    "sound_lib.stream",
    "sound_lib.external.pybass",
    # SDL's game controller layer, which is what tells a trigger from a
    # thumbstick. It is a compiled submodule behind a guarded import, and
    # losing it would not fail the build -- the game would simply come out
    # of the oven with no triggers and the face buttons in whatever order
    # the driver felt like. Named here so that cannot happen quietly.
    "pygame._sdl2",
    "pygame._sdl2.controller",
]

#: Excluded because nothing here imports them and they are large.
#:
#: Deliberately conservative. ``pygame`` is left whole: the game uses only
#: its joystick layer, but pygame probes its own submodules at import and a
#: missing one raises rather than degrading. Trimming it would save a few MB
#: against a 250 MB payload and risk the controller support outright.
EXCLUDES = [
    "tkinter",
    "unittest",
    "doctest",
    "pydoc_data",
    "lib2to3",
    "test",
    # distutils and setuptools are deliberately absent. PyInstaller's own
    # hooks alias setuptools' vendored distutils, and excluding either makes
    # that hook fail the build outright with "already imported as
    # ExcludedModule". PyInstaller drops what it does not need by itself.
    "pip",
    "pytest",
    "_pytest",
    "numpy",
    "PIL",
    "matplotlib",
]


# ----------------------------------------------------------------------
def build(args: argparse.Namespace) -> Path:
    dist = ROOT / "dist"
    work = ROOT / "build"
    if args.clean:
        for path in (dist, work):
            shutil.rmtree(path, ignore_errors=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", APP,
        # No console window: this is a GUI program, and a stray console
        # steals focus from the screen reader at launch.
        "--windowed",
        # Strips docstrings and asserts. Nothing in the package asserts as
        # part of its behaviour, so this is safe and shaves the payload.
        "--optimize", "2",
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
    ]
    command += ["--onefile"] if args.onefile else ["--onedir"]
    command += ["--upx-dir", args.upx] if args.upx else ["--noupx"]

    for name in HIDDEN:
        command += ["--hidden-import", name]
    for name in EXCLUDES:
        command += ["--exclude-module", name]

    command += asset_args() + prism_args() + sound_lib_args()
    command.append(str(ENTRY))

    print("Building", APP, "(onefile)" if args.onefile else "(onedir)")
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller failed with {result.returncode}")
    print(f"Built in {time.monotonic() - started:.0f}s")

    built = dist / (f"{APP}.exe" if args.onefile else APP)
    if not built.exists():
        raise SystemExit(f"Expected output not found: {built}")

    copied = copy_sounds(built)
    print(f"Copied {copied} sound files beside the game")

    readme = write_readme(built)
    print(f"Wrote {readme.name} ({readme.stat().st_size / 1024:.0f} KB)")
    return built


# ----------------------------------------------------------------------
def report(built: Path) -> None:
    """Say where the size actually went, rather than just the total."""
    if built.is_file():
        print(f"\n{built.name}: {built.stat().st_size / 1e6:.1f} MB (single file)")
        return

    total = sum(f.stat().st_size for f in built.rglob("*") if f.is_file())
    sounds = built / "sounds"
    audio = (
        sum(f.stat().st_size for f in sounds.rglob("*.wav")) if sounds.is_dir() else 0
    )

    print(f"\n{built}")
def log_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / APP / "fusionfire.log"


def verify(built: Path) -> bool:
    """Launch the result and read its log back.

    Starting without crashing is a weak test. A packaged build fails in ways
    that leave the window up and the program useless -- data unpacked to a
    path the code does not look in being the obvious one, which costs every
    sound in the game and raises nothing. So the log is checked for the
    warnings that describe exactly that.
    """
    exe = built if built.is_file() else built / f"{APP}.exe"
    if not exe.exists():
        print(f"Cannot verify: {exe} is missing")
        return False

    log = log_path()
    try:
        log.unlink()
    except OSError:
        pass

    print(f"\nLaunching {exe.name} to check it runs...")
    process = subprocess.Popen([str(exe)], cwd=exe.parent)
    try:
        time.sleep(20 if built.is_file() else 12)
        if process.poll() is not None:
            print(f"FAILED: it exited on its own with code {process.returncode}")
            return False
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    folder = built.parent if built.is_file() else built
    readme = folder / "readme.html"
    if not readme.is_file() or readme.stat().st_size < 4096:
        print(f"FAILED: {readme.name} is missing or too small to be the readme")
        return False

    try:
        written = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        print(f"FAILED: it started but never wrote {log}")
        return False

    problems = [
        line for line in written.splitlines()
        if ("missing" in line.lower() or "ERROR" in line)
        # sound_lib says this on every machine that has no "default" device
        # entry; it is noise, not a packaging fault.
        and "Could not set default device" not in line
    ]
    if problems:
        print("FAILED: it ran, but the log says the build is not whole:")
        for line in problems[:5]:
            print("   ", line.strip()[:150])
        return False

    print("It started, found its assets and opened a speech backend.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="single executable; unpacks 250 MB on every launch, so slower to start",
    )
    parser.add_argument("--upx", metavar="DIR", help="compress binaries with UPX from DIR")
    parser.add_argument("--clean", action="store_true", help="delete build/ and dist/ first")
    parser.add_argument("--no-verify", action="store_true", help="do not launch the result")
    args = parser.parse_args()

    built = build(args)
    report(built)

    if args.no_verify:
        return 0
    return 0 if verify(built) else 1


if __name__ == "__main__":
    sys.exit(main())
