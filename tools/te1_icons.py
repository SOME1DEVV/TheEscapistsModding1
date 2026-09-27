#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
te1_icons.py — сопоставление «ID предмета → кадр/хендл иконки» в The Escapists 1.

Раньше это было НЕРАСШИФРОВАННЫМ местом (см. MODDING_THE_ESCAPISTS.md §4:
«пока не полезешь в exe, любой новый ID будет без своей картинки»).
Теперь чанки с анимациями объектов (flag=3) расшифрованы (te_crypto.py),
и объект `Items` даёт точную карту: ID предмета = порядковый слот анимации.

Структура объекта `Items` (handle 184): 16 «анимаций», из них непустые —
0,1,2,5,6,7,8,9,10 (3,4 вырезаны разработчиком). Каждая анимация — до 32
«направлений», каждое направление = 1 кадр = хендл картинки из образа в exe.
Порядковая нумерация непустых слотов подряд даёт ID предмета (0..278) + 2
запасных слота (279,280).

Использование:
    python te1_icons.py game.exe --dump-dir <путь к dump_eur> --out item_icon_map.json
    python te1_icons.py game.exe --id 25 --dump-dir <dump_eur>      # иконка предмета 25
"""
from __future__ import annotations
import argparse, struct, zlib, json, csv, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from te_crypto import Cipher

STREAM_OFF = 4012920  # для TheEscapists_eur.exe; для других сборок искаться заново


def find_stream(d: bytes) -> int:
    def walk(off, maxn=6000):
        r = off; n = 0
        while r + 8 <= len(d):
            cid, flag, size = struct.unpack_from("<hhi", d, r)
            if not (0 <= flag <= 3) or not (0 <= size < 20_000_000):
                return None
            r += 8 + size; n += 1
            if cid == 32639:
                return r
        return None
    for off in range(0x1000, min(len(d), 0x500000)):
        cid, flag, size = struct.unpack_from("<hhi", d, off)
        if cid in (8738, 8739) and 0 <= flag <= 3 and 0 < size < 100000:
            if walk(off):
                return off
    raise RuntimeError("поток чанков не найден")


def read_chunks(d, start):
    out = []; r = start
    while r + 8 <= len(d):
        cid, flag, size = struct.unpack_from("<hhi", d, r)
        out.append((cid, flag, d[r + 8:r + 8 + size])); r += 8 + size
        if cid == 32639:
            break
    return out


def zdec(raw):
    ds, cs = struct.unpack_from("<II", raw, 0)
    return zlib.decompress(raw[8:8 + cs])


def u16(data):
    return struct.unpack("<h", data)[0]


def universal(b):
    try:
        return b.decode("utf-16-le")
    except Exception:
        return b.decode("latin1", "replace")


def parse_objects(chunks, cipher):
    by = {}
    for c in chunks:
        by.setdefault(c[0], []).append(c)
    fi = by[8745][0][2]
    cnt, = struct.unpack_from("<i", fi, 0)
    pos = 4
    objs = []
    for n in range(cnt):
        subs = {}
        while pos + 8 <= len(fi):
            cid, flag, size = struct.unpack_from("<hhi", fi, pos)
            if cid == 32639:
                pos += 8; break
            raw = fi[pos + 8:pos + 8 + size]; pos += 8 + size
            if flag == 1:
                data = zdec(raw)
            elif flag == 3:
                body = bytearray(raw[4:])
                if cid & 1:
                    body[0] ^= (cid & 0xFF) ^ (cid >> 8)
                t = cipher.transform(bytes(body))
                cs, = struct.unpack_from("<I", t, 0)
                data = zlib.decompress(t[4:4 + cs])
            elif flag == 2:
                data = cipher.transform(raw)
            else:
                data = raw
            subs[cid] = data
        if 17476 not in subs:
            objs.append(None); continue
        handle, otype, oflags = struct.unpack_from("<hhh", subs[17476], 0)
        name = universal(subs[17477]) if 17477 in subs else ""
        oc = None
        # ObjectCommon c анимациями есть только у type>=2 (Active и т.п.).
        if otype >= 2 and 17478 in subs:
            try:
                oc = parse_common(subs[17478])
            except Exception:
                oc = None
        objs.append(dict(handle=handle, type=otype, name=name.strip("\0"), oc=oc))
    return objs


def parse_common(buf):
    r = [0]
    def i16(o): v, = struct.unpack_from("<h", buf, o); return v
    def i32(o): v, = struct.unpack_from("<i", buf, o); return v
    def u16f(o): v, = struct.unpack_from("<H", buf, o); return v
    size = i32(0)
    anim_off = i16(4); mov_off = i16(6)
    base = anim_off
    asize, acount = struct.unpack_from("<hh", buf, base)
    offs = struct.unpack_from(f"<{acount}h", buf, base + 4)
    anims = {}
    for i, o in enumerate(offs):
        if o == 0:
            continue
        dbase = base + o
        doffs = struct.unpack_from("<32h", buf, dbase)
        dirs = {}
        for di, do in enumerate(doffs):
            if do == 0:
                continue
            b2 = dbase + do
            mins, maxs, rep, back, fc = struct.unpack_from("<bbhhH", buf, b2)
            if not (0 < fc <= 2000) or b2 + 8 + fc * 2 > len(buf):
                continue
            frames = list(struct.unpack_from(f"<{fc}H", buf, b2 + 8))
            dirs[di] = frames
        if dirs:
            anims[i] = dirs
    return dict(anims=anims)


def build_map(objs):
    # берём объект 'Items' с наибольшим числом кадров
    best = None
    for o in objs:
        if o and o['name'] == 'Items' and o['oc']:
            tot = sum(len(f) for dirs in o['oc']['anims'].values() for f in dirs.values())
            if best is None or tot > best[1]:
                best = (o, tot)
    if best is None:
        raise RuntimeError("объект Items не найден")
    o, _ = best
    seq = []
    a = o['oc']['anims']
    for ai in sorted(a):
        for di in sorted(a[ai]):
            seq.append((ai, di, a[ai][di][0]))
    return seq, o['handle']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("--dump-dir", help="папка с images/ и images.csv (из dump_fusion_images.py)")
    ap.add_argument("--out", default="item_icon_map.json")
    ap.add_argument("--id", type=int, help="показать иконку конкретного предмета")
    args = ap.parse_args()

    d = open(args.exe, "rb").read()
    start = find_stream(d)
    chunks = read_chunks(d, start)
    by = {}
    for c in chunks:
        by.setdefault(c[0], []).append(c)
    name = universal(zdec(by[8740][0][2])).strip("\0")
    cop = universal(zdec(by[8763][0][2])).strip("\0")
    ed = universal(zdec(by[8750][0][2])).strip("\0")
    ci = Cipher(name, cop, ed)
    objs = parse_objects(chunks, ci)
    seq, handle = build_map(objs)

    byh = {}
    if args.dump_dir and os.path.exists(os.path.join(args.dump_dir, "images.csv")):
        byh = {int(r["handle"]): r for r in csv.DictReader(open(os.path.join(args.dump_dir, "images.csv")))}

    if args.id is not None:
        if args.id < len(seq):
            ai, di, h = seq[args.id]
            r = byh.get(h)
            print(f"ID {args.id}: handle={h} anim={ai} dir={di} png={r['file'] if r else '?'}")
        else:
            print(f"ID {args.id} вне диапазона (есть {len(seq)} слотов)")
        return

    mapping = {}
    for gid, (ai, di, h) in enumerate(seq):
        r = byh.get(h)
        mapping[gid] = dict(anim=ai, dir=di, handle=h,
                            png=(os.path.join("images", r["file"]) if r else None))
    json.dump(mapping, open(args.out, "w"), indent=1)
    print(f"объект Items handle={handle}, слотов={len(seq)}, карта -> {args.out}")


if __name__ == "__main__":
    main()
