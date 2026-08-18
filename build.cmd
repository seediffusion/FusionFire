@echo off
rem Build a release of Fusion Fire: the portable folder, then the installer
rem that wraps it. Both land in dist\.
setlocal
cd /d "%~dp0"

echo === Building the game ===
uv run build.py --clean --upx C:\upx --no-verify
if errorlevel 1 goto :failed

echo.
echo === Compiling the installer ===
call :find_iscc
if not defined ISCC goto :no_iscc
rem /Qp keeps the progress line but drops the per-file listing, which for a
rem 250 MB payload is several thousand lines of console nobody reads.
rem Through call, so that an ISCC pointed at a .cmd wrapper comes back
rem here afterwards instead of ending the build script where it stands.
call "%ISCC%" /Qp "installer\Fusion_Fire_Setup.iss"
if errorlevel 1 goto :failed

echo.
echo Done. dist\FusionFire\ is the portable folder, and
echo "dist\Fusion Fire Setup.exe" installs it.
pause
exit /b 0


rem ----------------------------------------------------------------------
rem Inno Setup's command line compiler. Set ISCC in the environment
rem beforehand to point at an install none of these guesses find.
:find_iscc
if defined ISCC if exist "%ISCC%" exit /b
rem Cleared first: a stale ISCC pointing at nothing must not survive the
rem search and be handed to the shell as if it had been found.
set "ISCC="
for %%P in (ISCC.exe) do if not "%%~$PATH:P"=="" set "ISCC=%%~$PATH:P"
if defined ISCC exit /b
rem Inno Setup 6 installs per user by default. Older habits and 32-bit
rem installs on 64-bit Windows put it under one of the Program Files.
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" exit /b
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" exit /b
set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" exit /b
set "ISCC="
exit /b


:no_iscc
echo.
echo The game was built, but Inno Setup's compiler was not found, so
echo installer\Fusion_Fire_Setup.iss has not been compiled. dist\FusionFire\
echo is finished and works; only the installer is missing.
echo.
echo Install Inno Setup 6 from https://jrsoftware.org/isdl.php, or set ISCC
echo to the full path of ISCC.exe, then run this again.
pause
exit /b 1


:failed
echo.
echo Build failed.
pause
exit /b 1
