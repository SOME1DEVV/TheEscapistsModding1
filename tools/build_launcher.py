#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble the single-file launcher TE1_Mod_Launcher.bat.

Sources (readable, UTF-8, may contain Cyrillic):

    launcher_src/te1_engine.py     - the mod engine, pure ASCII
    launcher_src/fixes_rus.json    - the Better Translate database

Output (pure ASCII, CRLF, self contained):

    TE1_Mod_Launcher.bat           - batch part + embedded engine

Why the escaping is needed
--------------------------
The .bat carries the engine as a payload between the markers #<ENGINE> and
#</ENGINE>. cmd.exe never reads that part (the batch code exits first), it
is unpacked by PowerShell, which reads the file byte for byte. Keeping the
whole payload ASCII means the file survives being copied, re-saved, zipped
and diffed on any Windows box with any code page - no BOM, no OEM/ANSI
guessing, no mojibake. Russian text therefore lives in JSON written with
\\uXXXX escapes (json.dumps(ensure_ascii=True)), which json.loads turns
back into proper Cyrillic at run time.

Usage:
    python3 tools/build_launcher.py              # build + report
    python3 tools/build_launcher.py --check      # fail if the .bat is stale
    python3 tools/build_launcher.py --engine-out work/te1_engine.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "launcher_src"
ENGINE_SRC = SRC / "te1_engine.py"
FIXES_SRC = SRC / "fixes_rus.json"
BAT_OUT = ROOT / "TE1_Mod_Launcher.bat"

BEGIN = "#<ENGINE>"
END = "#</ENGINE>"
PLACEHOLDER = "<<<FIXES_JSON>>>"

# ------------------------------------------------------------------ batch part
BATCH_HEAD = r'''@echo off
rem ======================================================================
rem   THE ESCAPISTS 1 - MOD LAUNCHER                       (single file)
rem ----------------------------------------------------------------------
rem   Copy this .bat into the game folder - the one that holds
rem   TheEscapists.exe and the Data subfolder - and double-click it.
rem
rem   What it does:
rem     * checks that TheEscapists.exe really is next to it
rem     * creates the "mods" folder
rem     * lets you install / uninstall mods
rem     * applies the mods, starts the game, and puts your original files
rem       back the moment you quit - so a game started from Steam is
rem       always completely unmodded (one exception: Guard Key Names
rem       lives inside the game exe itself, its mod page has the details)
rem
rem   Needs Python 3 (https://www.python.org/downloads/ - tick
rem   "Add Python to PATH" while installing). Everything else, including
rem   the mod engine, is packed into this very file: the engine is
rem   unpacked to mods\te1_engine.py each time it runs.
rem ======================================================================
setlocal EnableExtensions
title The Escapists 1 - Mod Launcher

set "TE1_GAME=%~dp0"
set "TE1_BAT=%~f0"
set "TE1_MODS=%~dp0mods"
set "TE1_ENGINE=%TE1_MODS%\te1_engine.py"

echo.
echo   ============================================================
echo     THE ESCAPISTS 1 - MOD LAUNCHER
echo   ============================================================
echo.

rem ---- 1. we have to sit right next to the game ------------------------
if not exist "%TE1_GAME%TheEscapists.exe" goto no_exe
if not exist "%TE1_GAME%Data" goto no_data

rem ---- 2. the mods folder ----------------------------------------------
if not exist "%TE1_MODS%" mkdir "%TE1_MODS%" >nul 2>&1
if not exist "%TE1_MODS%" goto need_admin

rem ---- 3. can we actually write there? ---------------------------------
> "%TE1_MODS%\.writetest" echo te1 2>nul
if not exist "%TE1_MODS%\.writetest" goto need_admin
del "%TE1_MODS%\.writetest" >nul 2>&1

rem ---- 4. Python -------------------------------------------------------
set "TE1_PY="
py -c "import sys" >nul 2>&1
if not errorlevel 1 goto py_py
python -c "import sys" >nul 2>&1
if not errorlevel 1 goto py_python
python3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto py_python3
goto no_python
:py_py
set "TE1_PY=py"
goto have_python
:py_python
set "TE1_PY=python"
goto have_python
:py_python3
set "TE1_PY=python3"
goto have_python
:have_python

rem ---- 5. unpack the engine that is stored inside this file ------------
echo   Unpacking the mod engine...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$l=[IO.File]::ReadAllLines($env:TE1_BAT);$i=[Array]::IndexOf($l,'#<ENGINE>');$j=[Array]::IndexOf($l,'#</ENGINE>');if($i -lt 0 -or $j -le $i){exit 3};$w=[IO.File]::CreateText($env:TE1_ENGINE);for($k=$i+1;$k -lt $j;$k++){$w.WriteLine($l[$k])};$w.Close();exit 0"
if errorlevel 1 goto no_unpack
if not exist "%TE1_ENGINE%" goto no_unpack

rem ---- 6. hand over to the engine --------------------------------------
echo.
"%TE1_PY%" "%TE1_ENGINE%" --game "%TE1_GAME%."
if not errorlevel 1 exit /b 0
echo.
echo   The launcher stopped with an error.
pause
exit /b 1

rem ----------------------------------------------------------------------
:no_exe
echo   ERROR: TheEscapists.exe was not found next to this file.
echo.
echo   Copy TE1_Mod_Launcher.bat into the game folder - the folder that
echo   contains TheEscapists.exe and the Data subfolder - and run it there.
echo.
echo   This folder is: %TE1_GAME%
echo.
pause
exit /b 1

:no_data
echo   ERROR: no Data subfolder next to TheEscapists.exe.
echo.
echo   The launcher is in the wrong place, or the game installation is
echo   incomplete. Run Steam -^> The Escapists -^> Properties -^> Local
echo   Files -^> "Verify integrity of game files".
echo.
pause
exit /b 1

:need_admin
echo   ERROR: this folder cannot be written to.
echo.
echo   The game sits in a protected folder (usually Program Files), so
echo   Windows has to be asked for administrator rights. Confirm the next
echo   prompt, or right-click this .bat and choose "Run as administrator".
echo.
pause
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
exit /b 1

:no_python
echo   ERROR: Python 3 was not found.
echo.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add Python to PATH" during the installation. Then run this .bat
echo   again.
echo.
pause
exit /b 1

:no_unpack
echo   ERROR: the mod engine could not be unpacked from this .bat.
echo.
echo   PowerShell is required to unpack it. If this keeps happening,
echo   download TE1_Mod_Launcher.bat again - the file may be truncated.
echo.
pause
exit /b 1

'''

BATCH_TAIL = r'''
rem ----------------------------------------------------------------------
rem   Everything below this line is the mod engine. cmd.exe never gets
rem   here (the batch part exits above); PowerShell copies the block
rem   between the two markers into mods\te1_engine.py.
rem ----------------------------------------------------------------------
'''


# ------------------------------------------------------------------- builder
def build_engine_text():
    engine = ENGINE_SRC.read_text(encoding="ascii")
    fixes = json.loads(FIXES_SRC.read_text(encoding="utf-8"))
    payload = json.dumps(fixes, ensure_ascii=True, indent=1, sort_keys=False)
    if PLACEHOLDER not in engine:
        raise SystemExit("engine source is missing the %s marker" % PLACEHOLDER)
    text = engine.replace(PLACEHOLDER, "\n" + payload + "\n")
    _assert_ascii(text, "engine")
    for line in text.split("\n"):
        if line.strip() in (BEGIN, END):
            raise SystemExit("engine contains a payload marker line: %r" % line)
    compile(text, "te1_engine.py", "exec")     # syntax check
    return text


def build_bat_text():
    engine = build_engine_text()
    lines = []
    lines.extend(BATCH_HEAD.replace("\r\n", "\n").split("\n"))
    lines.extend(BATCH_TAIL.replace("\r\n", "\n").split("\n"))
    lines.append(BEGIN)
    lines.extend(engine.split("\n"))
    lines.append(END)
    while lines and lines[-1] == "":
        lines.pop()
    text = "\r\n".join(lines) + "\r\n"
    _assert_ascii(text, "TE1_Mod_Launcher.bat")
    return text


def _assert_ascii(text, what):
    bad = [(i, l) for i, l in enumerate(text.split("\n"), 1) if any(ord(c) > 126 for c in l)]
    if bad:
        for i, l in bad[:5]:
            sys.stderr.write("  line %d: %r\n" % (i, l[:90]))
        raise SystemExit("%s is not pure ASCII (%d line(s))" % (what, len(bad)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the checked-in .bat is out of date")
    ap.add_argument("--engine-out", metavar="PATH",
                    help="also write the assembled engine (for testing)")
    args = ap.parse_args()

    text = build_bat_text()
    if args.engine_out:
        out = Path(args.engine_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out), "w", encoding="ascii", newline="\n") as fh:
            fh.write(build_engine_text())
        print("engine -> %s" % args.engine_out)

    if args.check:
        old = ""
        if BAT_OUT.exists():
            with open(str(BAT_OUT), "r", encoding="ascii", newline="") as fh:
                old = fh.read()
        if old != text:
            sys.stderr.write("TE1_Mod_Launcher.bat is out of date - "
                             "run: python3 tools/build_launcher.py\n")
            return 1
        print("TE1_Mod_Launcher.bat is up to date")
        return 0

    with open(str(BAT_OUT), "w", encoding="ascii", newline="") as fh:
        fh.write(text)
    n_lines = text.count("\r\n")
    print("wrote %s (%d lines, %d bytes, ASCII + CRLF)"
          % (BAT_OUT.name, n_lines, len(text.encode("ascii"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
