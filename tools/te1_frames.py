#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
te1_frames.py — обход потока чанков TheEscapists.exe: фреймы, имена, события.

Команды:
  list    — перечислить фреймы: имя, handle, размеры подчанков (в т.ч. Frame Events)
  events  — расшифровать Frame Events (13117) указанного фрейма в файл
            python te1_frames.py events exe/TheEscapists_eur.exe.txt --frame game -o work/game_events.bin
  raw     — снять дамп любого подчанка фрейма
"""
from __future__ import annotations
import argparse, json, os, struct, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "toolkit"))
from te_crypto import Cipher  # noqa: E402
from te1_icons import find_stream, read_chunks, zdec, universal  # noqa: E402

# идентификаторы подчанков фрейма (Clickteam Fusion 2.5 / PAMU)
CH_END = 32639
CH_FRAME = 13107


def dechunk(raw: bytes):
    """Разбирает вложенный список чанков (id, flag, data)... до END."""
    out = []
    r = 0
    while r + 8 <= len(raw):
        cid, flag, size = struct.unpack_from("<hhi", raw, r)
        if size < 0 or r + 8 + size > len(raw):
            break
        out.append((cid, flag, raw[r + 8:r + 8 + size]))
        r += 8 + size
        if cid == CH_END:
            break
    return out


def decode_sub(cid: int, flag: int, raw: bytes, cipher: Cipher) -> bytes:
    """Расшифровка/распаковка подчанка — как в te1_icons.parse_objects."""
    if flag == 1:
        return zdec(raw)
    if flag == 3:
        body = bytearray(raw[4:])
        if cid & 1:
            body[0] ^= (cid & 0xFF) ^ (cid >> 8)
        t = cipher.transform(bytes(body))
        cs, = struct.unpack_from("<I", t, 0)
        return zlib.decompress(t[4:4 + cs])
    if flag == 2:
        return cipher.transform(raw)
    return raw


def load_exe(path: str):
    d = open(path, "rb").read()
    start = find_stream(d)
    chunks = read_chunks(d, start)
    by = {}
    for c in chunks:
        by.setdefault(c[0], []).append(c)
    name = universal(zdec(by[8740][0][2])).strip("\0")
    cop = universal(zdec(by[8763][0][2])).strip("\0")
    ed = universal(zdec(by[8750][0][2])).strip("\0")
    cipher = Cipher(name, cop, ed)
    return chunks, cipher


def iter_frames(chunks, cipher):
    """yield dict(name, handle, subs=[(cid,flag,plain_bytes)]) для каждого фрейма."""
    for cid, flag, data in chunks:
        if cid != CH_FRAME:
            continue
        subs = []
        plain = {}
        for scid, sflag, sraw in dechunk(data):
            subs.append((scid, sflag, len(sraw)))
            try:
                plain[scid] = decode_sub(scid, sflag, sraw, cipher)
            except Exception:
                plain[scid] = None
        header = plain.get(13108)
        handle = None
        if header:
            handle, = struct.unpack_from("<i", header, 0)
        namet = plain.get(13109)
        name = namet.decode("utf-16-le", "replace").strip("\0") if namet else "?"
        yield dict(handle=handle, name=name, subs=subs, plain=plain)


def cmd_list(args):
    chunks, cipher = load_exe(args.exe)
    print(f"{'hnd':>4}  {'имя':<28} {'events':>10}  подчанки")
    for fr in iter_frames(chunks, cipher):
        ev = fr["plain"].get(13117)
        subs = " ".join(f"{cid}/{flag}:{sz}" for cid, flag, sz in fr["subs"])
        print(f"{str(fr['handle']):>4}  {fr['name']:<28} "
              f"{len(ev) if ev else '—':>10}  {subs}")


def find_frame(chunks, cipher, key):
    for fr in iter_frames(chunks, cipher):
        if (str(key).isdigit() and fr["handle"] == int(key)) or fr["name"] == key:
            return fr
    raise SystemExit(f"фрейм {key!r} не найден")


def cmd_events(args):
    chunks, cipher = load_exe(args.exe)
    fr = find_frame(chunks, cipher, args.frame)
    data = fr["plain"].get(13117)
    if data is None:
        raise SystemExit("во фрейме нет расшифрованного chunk 13117 (Frame Events)")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "wb").write(data)
    print(f"фрейм: {fr['name']} (handle {fr['handle']})")
    print(f"Frame Events: {len(data)} байт -> {args.out}")


def cmd_raw(args):
    chunks, cipher = load_exe(args.exe)
    fr = find_frame(chunks, cipher, args.frame)
    cid = int(args.chunk)
    data = fr["plain"].get(cid)
    if data is None:
        raise SystemExit(f"подчанк {cid} отсутствует/не расшифровался")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "wb").write(data)
    print(f"chunk {cid}: {len(data)} байт -> {args.out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "events", "raw"])
    ap.add_argument("exe")
    ap.add_argument("--frame", help="имя или handle фрейма")
    ap.add_argument("--chunk", help="id подчанка для raw")
    ap.add_argument("-o", "--out", default="work/events.bin")
    a = ap.parse_args()
    if a.cmd == "list":
        cmd_list(a)
    elif a.cmd == "events":
        cmd_events(a)
    else:
        cmd_raw(a)


if __name__ == "__main__":
    main()
