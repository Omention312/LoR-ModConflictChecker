@echo off
rem ---------------------------------------------------------------
rem  Launcher for the Library of Ruina Mod Conflict Checker.
rem  ASCII-only on purpose: cmd.exe mis-parses UTF-8 batch files
rem  that contain non-ASCII characters.
rem  The install path may contain spaces, so the interpreter is
rem  stored and invoked as a quoted executable plus separate args.
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYEXE="
set "PYARG="

if exist "%~dp0runtime\pythonw.exe" (
  set "PYEXE=%~dp0runtime\pythonw.exe"
) else (
  where py >nul 2>nul && set "PYEXE=py" && set "PYARG=-3w"
)
if not defined PYEXE (
  where pythonw >nul 2>nul && set "PYEXE=pythonw"
)
if not defined PYEXE (
  where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE goto nopython

start "" "%PYEXE%" %PYARG% "%~dp0LoRModChecker.pyw"
exit /b 0

:nopython
echo.
echo   [ERROR] Python runtime not found.
echo.
echo   Expected bundled runtime:
echo       %~dp0runtime\pythonw.exe
echo.
echo   Fix (choose one):
echo     1. Restore the "runtime" folder next to this file.
echo     2. Install Python 3.9+ from https://www.python.org/downloads/
echo        and tick "Add python.exe to PATH".
echo.
echo   See README.md for details.
echo.
pause
exit /b 1
