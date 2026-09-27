#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end test for the Guard Key Names mod inside TE1_Mod_Launcher.bat.

Builds a fake game folder with REAL game executables, drives the launcher
engine through install -> re-apply -> Steam-restore -> heal -> uninstall
and checks the executables byte for byte at every step.

Needs sample executables (the full two-level builds with the npc_rename
frame, ~8 MB each). Point TE1_SAMPLE_EXE / TE1_SAMPLE_EXE2 at them, or drop
them into one of the fallback locations below. Without samples the test
skips cleanly (exit code 0, prints SKIP) so it can stay in the repo.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "TE1_Mod_Launcher.bat"
BEGIN = "#<ENGINE>"
END = "#</ENGINE>"

ok = True


def check(label, cond, extra=""):
    global ok
    print("  %-4s %s%s" % ("OK" if cond else "FAIL", label,
                           ("  -- " + extra) if extra and not cond else ""))
    if not cond:
        ok = False


def unpack_engine(dst):
    with open(str(BAT), "r", encoding="ascii", newline="") as fh:
        lines = fh.read().split("\r\n")
    i = lines.index(BEGIN)
    j = lines.index(END)
    dst.write_text("\n".join(lines[i + 1:j]) + "\n", encoding="ascii")


def run(engine, game, *args):
    return subprocess.run([sys.executable, str(engine), "--game", str(game)]
                          + list(args),
                          capture_output=True, text=True, encoding="utf-8")


def find_samples():
    def fix(p):
        return Path(os.environ[p]) if os.environ.get(p) else None
    cands = [fix("TE1_SAMPLE_EXE"), fix("TE1_SAMPLE_EXE2")]
    cands += [Path("/tmp/te1_extract/part1/exe/game/TheEscapists_eur.exe.txt"),
              Path("/tmp/te1_extract/part1/exe/game/TheEscapists_rus.exe.txt"),
              Path("/tmp/te1_extract/part2/exe/game/TheEscapists_pol.exe.txt")]
    out = []
    for c in cands:
        if c and c.exists() and c not in out:
            out.append(c)
    return out


def make_game(work, samples, with_real=True):
    game = work / "game"
    if game.exists():
        shutil.rmtree(game)
    (game / "Data").mkdir(parents=True)
    # a real Data folder keeps the val.dat rebuild happy
    for f in sorted((ROOT / "data_samples").glob("*.dat")):
        (game / "Data" / f.name).write_bytes(f.read_bytes())
    # the small front end: never carries the mod, must be skipped gracefully
    frontend = Path("/tmp/te1_extract/part1/exe/game/TheEscapists.exe.txt")
    if frontend.exists():
        (game / "TheEscapists.exe").write_bytes(frontend.read_bytes())
    else:
        (game / "TheEscapists.exe").write_bytes(b"not a fusion exe\n")
    names = ["theescapists_eur.exe", "theescapists_rus.exe",
             "theescapists_pol.exe"]
    if with_real:
        for src, name in zip(samples, names):
            (game / name).write_bytes(src.read_bytes())
    return game


def main():
    samples = find_samples()
    if not samples:
        print("SKIP: no sample executables "
              "(set TE1_SAMPLE_EXE=/path/to/TheEscapists_eur.exe)")
        return 0
    print("samples:")
    for s in samples:
        print("  %s (%d bytes)" % (s, s.stat().st_size))

    work = Path(tempfile.mkdtemp(prefix="te1_gk_test_"))
    try:
        engine = work / "te1_engine.py"
        unpack_engine(engine)
        body = engine.read_text(encoding="ascii")
        check("engine is pure ASCII",
              all(ord(c) < 127 for c in body))
        check("guard_keys section present",
              '"guard_keys"' in body and "npc_rename" in body
              and "Guard Key Names" in body)

        print("1. install via --apply")
        game = make_game(work, samples)
        real_names = sorted(f.name for f in game.glob("theescapists_*.exe"))
        before = {f: f.read_bytes() for f in game.glob("*.exe")}
        state = game / "mods" / "mods.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"guard_keys": True}), encoding="utf-8")
        p = run(engine, game, "--apply")
        print("     " + "\n     ".join(p.stdout.strip().splitlines()))
        check("apply exited cleanly", p.returncode == 0, p.stderr[-400:])
        check("every real game exe got patched",
              all("patched" in p.stdout and n in p.stdout for n in real_names))
        check("front-end skipped with a reason",
              "TheEscapists.exe skipped" in p.stdout)
        for n in real_names:
            exe = game / n
            check("%s bytes changed" % n, exe.read_bytes() != before[exe])
            bak = game / "mods" / "original" / "exe" / n
            check("%s backup == pristine exe" % n,
                  bak.exists() and bak.read_bytes() == before[exe])
        check("front-end untouched",
              (game / "TheEscapists.exe").read_bytes()
              == before[game / "TheEscapists.exe"])

        print("2. cross-check with the standalone patcher")
        chk = ROOT / "mods" / "guard_keys" / "te1_guardkeys.py"
        for n in real_names[:1]:
            q = subprocess.run([sys.executable, str(chk), "check",
                                str(game / n)],
                               capture_output=True, text=True,
                               encoding="utf-8")
            check("standalone check sees the mod (rc=0)", q.returncode == 0,
                  q.stdout[-300:] + q.stderr[-300:])
            check("standalone check reports 'already installed'",
                  "УЖЕ УСТАНОВЛЕН" in q.stdout.encode("utf-8").decode("utf-8")
                  if q.returncode == 0 else False)

        print("3. second apply is idempotent")
        p2 = run(engine, game, "--apply")
        check("re-apply clean", p2.returncode == 0)
        check("idempotent: no double patch", "was already patched" in p2.stdout)

        print("4. 'Steam verify' wipes the exe -> launch heals it")
        victim = game / real_names[0]
        victim.write_bytes(before[victim])
        p3 = run(engine, game, "--apply")
        check("heal clean", p3.returncode == 0)
        check("exe re-patched after Steam restore",
              victim.read_bytes() != before[victim])
        check("backup still the pristine one",
              (game / "mods" / "original" / "exe" / real_names[0]).read_bytes()
              == before[victim])

        print("5. --status shows per-exe state")
        p4 = run(engine, game, "--status")
        modline = [l for l in p4.stdout.splitlines()
                   if l.startswith("Guard Key Names")]
        check("status lists the mod",
              bool(modline) and modline[0].rstrip().endswith("True"),
              p4.stdout)
        check("status shows exe state",
              "theescapists_eur.exe patched" in p4.stdout)

        print("6. game folder with only the front end -> honest failure")
        game2 = make_game(work / "bare", [], with_real=False)
        st2 = game2 / "mods" / "mods.json"
        st2.parent.mkdir(parents=True, exist_ok=True)
        st2.write_text(json.dumps({"guard_keys": True}), encoding="utf-8")
        p5 = run(engine, game2, "--apply")
        check("no real game -> apply fails loudly", p5.returncode != 0)
        check("reason is printed",
              "not a single executable" in p5.stdout)

        print("7. uninstall puts the pristine exes back")
        script = ("import sys; sys.path.insert(0, %r); "
                  "import te1_engine as E; "
                  "L = E.Launcher(%r); "
                  "r, pr = L.gk_restore_all(); "
                  "print('restored:', r, 'problems:', pr)"
                  % (str(engine.parent), str(game)))
        p6 = subprocess.run([sys.executable, "-c", script],
                            capture_output=True, text=True, encoding="utf-8")
        check("restore helper ran cleanly", p6.returncode == 0
              and "problems: []" in p6.stdout, p6.stdout + p6.stderr[-300:])
        for n in real_names:
            check("%s byte-identical again" % n,
                  (game / n).read_bytes() == before[game / n])
        check("backups consumed",
              not any((game / "mods" / "original" / "exe").glob("*")))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("")
    print("ALL GOOD" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
