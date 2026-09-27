#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self test for TE1_Mod_Launcher.bat / the embedded mod engine.

Builds a throwaway game folder out of data_samples/, unpacks the engine
from the .bat exactly the way PowerShell does, runs both mods, and checks
the result. Run:  python3 tools/test_launcher.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work" / "selftest"
ENGINE = WORK / "te1_engine.py"
BAT = ROOT / "TE1_Mod_Launcher.bat"
SAMPLES = ROOT / "data_samples"

BEGIN = "#<ENGINE>"
END = "#</ENGINE>"

ok = True


def check(label, cond, extra=""):
    global ok
    print("  %-4s %s%s" % ("OK" if cond else "FAIL", label,
                           ("  -- " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


def unpack_engine():
    """Same job as the PowerShell one-liner in the .bat."""
    with open(str(BAT), "r", encoding="ascii", newline="") as fh:
        lines = fh.read().split("\r\n")
    i = lines.index(BEGIN)
    j = lines.index(END)
    ENGINE.parent.mkdir(parents=True, exist_ok=True)
    ENGINE.write_text("\n".join(lines[i + 1:j]) + "\n", encoding="ascii")


def make_game():
    game = WORK / "game"
    data = game / "Data"
    if data.exists():
        import shutil
        shutil.rmtree(data)
    data.mkdir(parents=True)
    for f in sorted(SAMPLES.glob("*.dat")):
        (data / f.name).write_bytes(f.read_bytes())
    (game / "TheEscapists.exe").write_bytes(b"not a real exe\n")
    return game


def run(game, *args):
    p = subprocess.run([sys.executable, str(ENGINE), "--game", str(game)] + list(args),
                       capture_output=True, text=True, encoding="utf-8")
    return p


def main():
    if WORK.exists():
        import shutil
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    print("1. unpack the engine out of the .bat")
    unpack_engine()
    check("engine extracted", ENGINE.exists())
    check("engine is pure ASCII",
          all(ord(c) < 127 for c in ENGINE.read_text(encoding="ascii")))
    body = ENGINE.read_text(encoding="ascii")
    check("FIXES database present", '"items"' in body and '"speech"' in body)
    check("FIXES are \\u escaped, not raw Cyrillic", "\\u041a" in body)

    print("2. build a fake game folder from data_samples")
    game = make_game()
    data = game / "Data"
    before = {f.name: f.read_bytes() for f in data.glob("*.dat")}
    check("game folder ready", (game / "TheEscapists.exe").exists())

    print("3. install both mods")
    state = game / "mods" / "mods.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"randomizer": True, "better_translate": True}),
                     encoding="utf-8")
    p = run(game, "--apply")
    print("     " + "\n     ".join(p.stdout.strip().splitlines()))
    check("apply exited cleanly", p.returncode == 0, p.stderr[-400:])

    print("4. backups and results")
    orig = game / "mods" / "original"
    check("originals backed up", (orig / "items_rus.dat").exists())
    check("backup == pre-mod bytes",
          (orig / "items_rus.dat").read_bytes() == before["items_rus.dat"])
    after = {f.name: f.read_bytes() for f in data.glob("*.dat")}
    check("items_rus.dat changed", after["items_rus.dat"] != before["items_rus.dat"])
    check("data_rus.dat changed", after["data_rus.dat"] != before["data_rus.dat"])
    check("speech_rus.dat changed", after["speech_rus.dat"] != before["speech_rus.dat"])
    check("val.dat rebuilt", after["val.dat"] != before["val.dat"])
    check("data_eng.dat untouched",
          after["data_eng.dat"] == before["data_eng.dat"])

    print("5. Better Translate: spot checks")
    ir = (data / "items_rus.dat").read_bytes().decode("utf-16")
    dr = (data / "data_rus.dat").read_bytes().decode("utf-16")
    sr = (data / "speech_rus.dat").read_bytes().decode("utf-16")
    check("no untranslated English item descriptions",
          "Unlocks yellow doors" not in ir)
    check("no untranslated English item names", "Dirty Tux Outfit" not in ir)
    check("machine word order fixed",
          "Пластиковый ключ рабочего" in ir and "Пластиковый работа ключевая" not in ir)
    check("File -> Напильник everywhere",
          "Напильник" in ir and ir.count("Файл") == 0)
    check("no stray English 'Rope' in recipes", ", Rope" not in ir)
    check("no NBSP left", "\u00a0" not in dr and "\u00a0" not in sr)
    import re as _re
    check("no stray LF inside values",
          not _re.search(r"(?<!\r)\n", dr) and not _re.search(r"(?<!\r)\n", sr))
    check("homoglyph fixed (К тому же)", "К тому же.." in dr)
    check("homoglyph fixed (продажу)", "на продажу." in dr)
    check("$combat restored in the Mac tutorial", "$combat" in dr and "$бой" not in dr)
    check("tutorial prefix synced", "1@Подойди к своему столу" in dr)
    check("UI labels capitalised", "\r\n2=Новая игра\r\n" in dr)
    check("credits translated", "Mouldy Toof Studios" not in dr.split("[Misc]")[1][:4000]
          or "Разработано Mouldy Toof Studios" in dr)
    check("Store -> Магазин", "54=Магазин" in dr)
    check("MedStaff refilled", "наш последний пациент умер" in sr)
    check("MedStaff Count updated", "[MedStaff]\r\nCount=24" in sr)
    check("OnDesk refilled", "[OnDesk]\r\nCount=3" in sr)
    check("Sheets refilled", "[Sheets]\r\nCount=10" in sr)
    check("no trailing spaces left in speech lines",
          not any(l.endswith(" ") for l in sr.split("\r\n") if "=" in l))

    print("6. Randomizer")
    p2 = run(game, "--apply")
    a = (data / "items_rus.dat").read_bytes()
    check("second apply produced a different roll", a != after["items_rus.dat"])
    check("names survived the randomizer",
          "Ключ от камеры" in a.decode("utf-16"))
    check("craft recipes survived",
          "80_Легкая лопата, Лист металла, Клейкая лента" in a.decode("utf-16"))

    print("7. restore")
    p3 = run(game, "--restore")
    check("restore exited cleanly", p3.returncode == 0)
    restored = {f.name: f.read_bytes() for f in data.glob("*.dat")}
    diff = [n for n in before if before[n] != restored.get(n)]
    check("every .dat is byte-identical again", not diff, str(diff))

    print("8. the front-end hands the game over to another exe")
    # TheEscapists.exe is only a launcher: it shows "Play", closes itself and
    # starts TheEscapists_rus.exe. wait_for_game() has to survive that gap
    # and must not hand the files back while the real game is running.
    import importlib.util

    spec = importlib.util.spec_from_file_location("te1_engine", str(ENGINE))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)

    class FakeProc(object):
        def __init__(self):
            self.pid = 4242
            self.alive = True

        def poll(self):
            return None if self.alive else 0

    def script(steps):
        """steps: list of (seconds_from_start, set_of_game_pids)."""
        t0 = [None]

        def fake_running_pids(names):
            now = time.monotonic()
            if t0[0] is None:
                t0[0] = now
            pids = set()
            for at, value in steps:
                if now - t0[0] >= at:
                    pids = value
            return pids

        return fake_running_pids

    real_running_pids = eng.running_pids      # restored again in step 9

    # a) launcher closes, real game appears 1.5 s later, runs 2 s, then quits
    eng.POLL_IDLE = 0.2
    eng.POLL_BUSY = 0.1
    launcher = eng.Launcher(game)
    launcher.handover = 8.0
    launcher.exit_grace = 2.0
    eng.running_pids = script([(0.0, set()), (1.5, {9001}), (3.5, set())])
    proc = FakeProc()
    proc.alive = False                # the front-end already closed
    t = time.monotonic()
    launcher.wait_for_game(proc)
    elapsed = time.monotonic() - t
    check("waited for the real game, not for the launcher",
          4.5 <= elapsed <= 7.5, "elapsed %.1fs" % elapsed)

    # b) the game never shows up at all -> give up after the handover delay
    launcher.handover = 1.0
    eng.running_pids = script([(0.0, set())])
    proc = FakeProc()
    proc.alive = False
    t = time.monotonic()
    launcher.wait_for_game(proc)
    elapsed = time.monotonic() - t
    check("gives up when the game never starts",
          0.5 <= elapsed <= 2.5, "elapsed %.1fs" % elapsed)

    # c) the launcher stays open the whole time -> we keep waiting on it
    launcher.handover = 1.0
    eng.running_pids = script([(0.0, set())])
    proc = FakeProc()
    proc.alive = True
    threading.Timer(1.0, lambda: setattr(proc, "alive", False)).start()
    t = time.monotonic()
    launcher.wait_for_game(proc)
    elapsed = time.monotonic() - t
    check("waits while the front-end window is open",
          1.5 <= elapsed <= 3.5, "elapsed %.1fs" % elapsed)

    print("9. real tasklist parsing (fake tasklist on PATH)")
    # Exercise the actual running_pids()/csv code path, not a stub.
    fake_bin = WORK / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    tasklist = fake_bin / "tasklist"
    state = WORK / "tasklist.csv"
    tasklist.write_text('#!/bin/sh\ncat "%s"\n' % state, encoding="ascii")
    tasklist.chmod(0o755)
    os.environ["PATH"] = str(fake_bin) + os.pathsep + os.environ["PATH"]

    def write_procs(rows):
        lines = []
        for image, pid in rows:
            lines.append('"%s","%d","Console","1","12 345 K"' % (image, pid))
        state.write_text("\n".join(lines) + "\n", encoding="ascii")

    write_procs([("System Idle Process", 0), ("cmd.exe", 55),
                 ("TheEscapists.exe", 4242), ("TheEscapists_rus.exe", 9001)])
    eng.IS_WINDOWS = True
    eng.running_pids = real_running_pids
    check("finds the game processes in tasklist output",
          eng.running_pids({"theescapists.exe", "theescapists_rus.exe"})
          == {4242, 9001})
    write_procs([("System Idle Process", 0)])
    check("ignores unrelated processes",
          eng.running_pids({"theescapists.exe"}) == set())
    write_procs([("TheEscapists_rus.exe", 7)])
    check("survives a single-column / odd row",
          eng.running_pids({"theescapists_rus.exe"}) == {7})
    state.write_text("", encoding="ascii")
    check("empty tasklist output is not a crash",
          eng.running_pids({"theescapists.exe"}) == set())

    # end-to-end: front-end exits, real game shows up 1.5 s later, runs 2 s
    # the Russian release ships the front end plus one 8 MB exe per language
    for name in ("TheEscapists_rus.exe", "TheEscapists_eur.exe"):
        (game / name).write_bytes(b"stub")
    write_procs([("TheEscapists.exe", 4242)])
    script_rows = [(0.0, [("TheEscapists.exe", 4242)]),
                   (1.5, [("TheEscapists_rus.exe", 9001)]),
                   (3.5, [("System Idle Process", 0)])]
    t0 = [None]

    def tick():
        now = time.monotonic()
        if t0[0] is None:
            t0[0] = now
        rows = script_rows[0][1]
        for at, value in script_rows:
            if now - t0[0] >= at:
                rows = value
        write_procs(rows)

    stop = threading.Event()

    def loop():
        while not stop.is_set():
            tick()
            time.sleep(0.05)

    thr = threading.Thread(target=loop)
    launcher2 = eng.Launcher(game)
    launcher2.handover = 8.0
    launcher2.exit_grace = 2.0
    eng.POLL_IDLE = 0.2
    eng.POLL_BUSY = 0.1
    proc2 = FakeProc()
    proc2.alive = False
    thr.start()
    t = time.monotonic()
    launcher2.wait_for_game(proc2)
    elapsed = time.monotonic() - t
    stop.set()
    thr.join()
    check("end-to-end handover waits for TheEscapists_rus.exe",
          4.5 <= elapsed <= 7.5, "elapsed %.1fs" % elapsed)
    eng.IS_WINDOWS = False

    print("10. dry-run report on a clean tree")
    p4 = run(game, "--report")
    check("report exited cleanly", p4.returncode == 0, p4.stderr[-400:])
    print("     " + "\n     ".join(p4.stdout.strip().splitlines()))

    print()
    print("ALL GOOD" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
