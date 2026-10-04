@echo off
rem ---------------------------------------------------------------
rem  Command line scan for the Library of Ruina Mod Conflict Checker.
rem  ASCII-only on purpose: cmd.exe mis-parses UTF-8 batch files
rem  that contain non-ASCII characters.
rem  The install path may contain spaces, so the interpreter is
rem  stored and invoked as a quoted executable plus separate args.
rem
rem  Extra options are forwarded to lorcheck_cli.py, e.g.
rem      --game DIR --workshop DIR --log FILE --out DIR --top N --json
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYEXE="
set "PYARG="

if exist "%~dp0runtime\python.exe" (
  set "PYEXE=%~dp0runtime\python.exe"
) else (
  where py >nul 2>nul && set "PYEXE=py" && set "PYARG=-3"
)
if not defined PYEXE (
  where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE goto nopython

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%PYEXE%" %PYARG% "%~dp0lorcheck_cli.py" %*
echo.
pause
exit /b 0

:nopython
echo.
echo   [ERROR] Python runtime not found and the "runtime" folder is missing.
echo   Restore runtime\python.exe or install Python 3.9+.
echo   See README.md for details.
echo.
pause
exit /b 1
