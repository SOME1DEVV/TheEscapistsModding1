#!/usr/bin/env python3
# -*- coding: ascii -*-
#
# te1_engine.py - mod engine for "The Escapists 1" (PC / Steam).
#
# This file is NOT run directly. It is embedded inside TE1_Mod_Launcher.bat
# and unpacked to mods\te1_engine.py at run time. It is deliberately
# pure ASCII: every Russian string it writes lives in the FIXES database
# (a JSON document stored with \uXXXX escapes), so the .bat file itself
# never needs a code page and can never be mangled by cmd.exe.
#
# No third-party modules: Python 3 standard library only.
#
# Commands (all of them are driven by the launcher menu):
#   --game DIR        game folder (folder that holds TheEscapists.exe)
#   --menu            interactive menu (default)
#   --apply           install enabled mods into Data\ and exit
#   --restore         put the original Data\*.dat files back and exit
#   --status          print what is installed and exit
#   --report          dry run of "Better Translate", print the diff, exit
#   --handover SEC    wait this long for the real game to appear after the
#                     front-end closed (default 8)
#   --exit-grace SEC  wait this long after the game closed before cleaning
#                     up (default 2)
#
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

ENGINE_VERSION = "1.1"
EXE_NAME = "TheEscapists.exe"

# Language files the two mods touch.
MODS = [
    ("randomizer", "Randomizer"),
    ("better_translate", "Better Translate"),
    ("guard_keys", "Guard Key Names"),
]

# ---------------------------------------------------------------- FIXES data
# Filled in by tools/build_launcher.py from launcher_src/fixes_rus.json.
_FIXES_JSON = r"""
<<<FIXES_JSON>>>
"""

FIXES = json.loads(_FIXES_JSON)


# ------------------------------------------------------------------ console
def _fix_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_fix_console()


def say(*a):
    print(*a, flush=True)


def rule(ch="-", n=66):
    say(ch * n)


class EndOfInput(Exception):
    """Raised when there is nobody left to answer the menus."""


def ask(prompt, default=None):
    try:
        s = input(prompt + (" [%s]: " % default if default is not None else ": ")).strip()
    except (EOFError, OSError, KeyboardInterrupt):
        raise EndOfInput()
    return s or (default if default is not None else "")


def pause(msg="Press Enter to continue..."):
    try:
        input("\n" + msg)
    except (EOFError, OSError, KeyboardInterrupt):
        pass


# ------------------------------------------------------------- .dat encoding
def looks_text(t):
    if not t:
        return False
    head = t[:4096]
    good = sum(1 for c in head if c.isprintable() or c in "\r\n\t")
    if good / len(head) < 0.85:
        return False
    return head.count("[") + head.count("=") >= 3 or len(t) < 64


def decode_dat(raw):
    """-> (text, encoding, bom) or (None, None, False) when unreadable."""
    if not raw:
        return "", "utf-8", False
    if raw[:2] == b"\xff\xfe":
        try:
            return raw.decode("utf-16-le")[1:], "utf-16-le", True
        except UnicodeDecodeError:
            return None, None, False
    if raw[:2] == b"\xfe\xff":
        try:
            return raw.decode("utf-16-be")[1:], "utf-16-be", True
        except UnicodeDecodeError:
            return None, None, False
    if raw[:3] == b"\xef\xbb\xbf":
        try:
            return raw.decode("utf-8")[1:], "utf-8", True
        except UnicodeDecodeError:
            return None, None, False
    for enc in ("utf-8", "cp1252", "cp1251", "latin-1"):
        try:
            t = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if looks_text(t):
            return t, enc, False
    return None, None, False


def encode_dat(text, enc, bom):
    if bom:
        text = "\ufeff" + text
    return text.encode(enc, "strict")


def atomic_write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class DatFile(object):
    """A Data\\*.dat file: text + encoding, loaded and saved as a whole."""

    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        self.text, self.enc, self.bom = decode_dat(self.raw)
        self.ok = self.text is not None

    def save(self):
        data = encode_dat(self.text, self.enc, self.bom)
        # round-trip guard: never write something we cannot read back
        back, enc2, bom2 = decode_dat(data)
        if back is None or back != self.text:
            raise ValueError("round-trip check failed for %s" % self.path.name)
        atomic_write(self.path, data)
        self.raw = data

    @property
    def size(self):
        return len(self.raw)


# ------------------------------------------------------------------ INI text
KV_RE = re.compile(r"^([0-9A-Za-z_]+)=(.*)$", re.S)
SEC_RE = re.compile(r"^\[(.*)\]$")


class Ini(object):
    """Line-preserving INI editor.

    The game files carry cosmetic blank lines, a warning header and (in
    data_*.dat) blank lines inside sections. Rebuilding the file from a
    dict would drop all of that, so we keep the original line list and
    only replace the parts that actually change.
    """

    def __init__(self, text):
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.split(self.nl)
        self.index = {}
        self.order = []
        self._parse()

    def _parse(self):
        self.index = {}
        self.order = []
        sec = None
        for i, line in enumerate(self.lines):
            m = SEC_RE.match(line.strip())
            if m:
                sec = m.group(1)
                continue
            if sec is None:
                continue
            m = KV_RE.match(line)
            if m and (sec, m.group(1)) not in self.index:
                self.index[(sec, m.group(1))] = i
                self.order.append((sec, m.group(1)))

    @property
    def text(self):
        return self.nl.join(self.lines)

    def sections(self):
        seen = []
        for sec, _key in self.order:
            if sec not in seen:
                seen.append(sec)
        return seen

    def keys(self, sec):
        return [k for s, k in self.order if s == sec]

    def get(self, sec, key, default=None):
        i = self.index.get((sec, key))
        if i is None:
            return default
        return KV_RE.match(self.lines[i]).group(2)

    def has(self, sec, key):
        return (sec, key) in self.index

    def set(self, sec, key, value):
        i = self.index.get((sec, key))
        if i is None:
            self.add(sec, key, value)
            return
        old = KV_RE.match(self.lines[i]).group(2)
        if old == value:
            return
        self.lines[i] = "%s=%s" % (key, value)

    def add(self, sec, key, value):
        """Insert a new key, keeping numeric keys in ascending order."""
        pos = self._insert_pos(sec, key)
        self.lines.insert(pos, "%s=%s" % (key, value))
        self._parse()

    def _insert_pos(self, sec, key):
        last = None
        best = None
        try:
            want = int(key)
        except ValueError:
            want = None
        for (s, k), i in sorted(self.index.items(), key=lambda kv: kv[1]):
            if s != sec:
                continue
            last = i
            if want is not None:
                try:
                    cur = int(k)
                except ValueError:
                    continue
                if cur < want and (best is None or cur > best[0]):
                    best = (cur, i)
        if best is not None:
            return best[1] + 1
        if last is not None:
            return last + 1
        # brand new section: append at the end of the file
        return len(self.lines)

    def rewrite_section(self, sec, pairs):
        """Replace every key line of `sec` with `pairs` (list of (k, v))."""
        idx = [i for (s, _k), i in self.index.items() if s == sec]
        if not idx:
            return
        first, last = min(idx), max(idx)
        new = ["%s=%s" % (k, v) for k, v in pairs]
        self.lines[first:last + 1] = new
        self._parse()


# ------------------------------------------------------------------ val.dat
LANGS = {"e": "eng", "f": "fre", "g": "ger", "s": "spa",
         "r": "rus", "p": "pol", "i": "ita"}
KINDS = ["data", "items", "speech"]


def md5_size(size):
    return hashlib.md5(("l0l_%d" % size).encode()).hexdigest()


def rebuild_val(data_dir):
    """val.dat only stores md5("l0l_" + file size) per file, so any edit
    that changes a file length has to be followed by this."""
    vpath = Path(data_dir) / "val.dat"
    if not vpath.exists():
        return 0
    txt = vpath.read_bytes().decode("utf-16-le", "replace")
    bom = txt.startswith("\ufeff")
    if bom:
        txt = txt[1:]
    changed = 0
    for letter, lang in LANGS.items():
        m = re.search(r"(^|\n)(%s=)([0-9a-f]{32})_([0-9a-f]{32})_([0-9a-f]{32})"
                      % letter, txt)
        if not m:
            continue
        toks = [m.group(3), m.group(4), m.group(5)]
        for i, kind in enumerate(KINDS):
            fp = Path(data_dir) / ("%s_%s.dat" % (kind, lang))
            if fp.exists():
                toks[i] = md5_size(fp.stat().st_size)
        txt = (txt[:m.start()] + m.group(1) + m.group(2) + "_".join(toks)
               + txt[m.end():])
        changed += 1
    vpath.write_bytes((("\ufeff" if bom else "") + txt).encode("utf-16-le"))
    return changed


# ------------------------------------------------------- generic RU clean-up
CYR_LOW = "\u0430\u0431\u0432\u0433\u0434\u0435\u0451\u0436\u0437\u0438\u0439\u043a\u043b\u043c\u043d\u043e\u043f\u0440\u0441\u0442\u0443\u0444\u0445\u0446\u0447\u0448\u0449\u044a\u044b\u044c\u044d\u044e\u044f"

# Latin letters that people (and machine translation) keep typing instead of
# the identical-looking Cyrillic ones.
HOMOGLYPHS = {
    "A": "\u0410", "B": "\u0412", "C": "\u0421", "E": "\u0415",
    "H": "\u041d", "K": "\u041a", "M": "\u041c", "O": "\u041e",
    "P": "\u0420", "T": "\u0422", "X": "\u0425", "Y": "\u0423",
    "a": "\u0430", "c": "\u0441", "e": "\u0435", "o": "\u043e",
    "p": "\u0440", "x": "\u0445", "y": "\u0443"
}

RE_SPACED_DOTS = re.compile(r"\.(\s+\.)+")
RE_SP_BEFORE_PUNCT = re.compile(r"[ \t]+([!,.;:?])")
RE_SP_BEFORE_HASH = re.compile(r"[ \t]+#")
RE_MULTISPACE = re.compile(r"[ \t]{2,}")


def is_cyr(c):
    return "\u0400" <= c <= "\u04ff"


def fix_homoglyphs(v):
    """Latin letter adjacent to a Cyrillic one -> Cyrillic.

    Only touches characters that sit right next to a Cyrillic letter, so
    Latin brand names (VIP, DVD, Worms, Jingle Cells) are left alone.
    """
    out = None
    for i, c in enumerate(v):
        if c not in HOMOGLYPHS:
            continue
        left = i > 0 and is_cyr(v[i - 1])
        right = i + 1 < len(v) and is_cyr(v[i + 1])
        if left or right:
            if out is None:
                out = list(v)
            out[i] = HOMOGLYPHS[c]
    return "".join(out) if out is not None else v


def normalize_ru(v, eng=None, full=True):
    v = v.replace("\u00a0", " ")
    if full:
        v = v.replace("\n", "#")           # stray LF used as a line break
        v = RE_SPACED_DOTS.sub("...", v)
        v = RE_SP_BEFORE_PUNCT.sub(r"\1", v)
        v = RE_SP_BEFORE_HASH.sub("#", v)
    v = RE_MULTISPACE.sub(" ", v)
    v = v.strip()
    v = fix_homoglyphs(v)
    if full and v and eng and eng[:1].isupper() and v[0] in CYR_LOW:
        v = v[0].upper() + v[1:]
    return v


# ------------------------------------------------------------- N@ line prefix
# Tutorial / popup strings start with "N@". The Russian files drifted away
# from the English ones (2@ where the original says 1@ and so on), which
# changes how the hint is displayed.
RE_PREFIX = re.compile(r"^(\d+)@")


def sync_prefix(value, eng):
    if not eng:
        return value
    me = RE_PREFIX.match(eng)
    if not me:
        return value
    mr = RE_PREFIX.match(value)
    if mr and mr.group(1) == me.group(1):
        return value
    body = value[mr.end():] if mr else value
    return me.group(1) + "@" + body


# ------------------------------------------------------- Guard Key Names mod
# This mod is unlike the two Data-file mods above: it patches the game
# executable itself. Inside the npc_rename frame (the "prison population"
# screen before the game starts), right before the start button jumps into
# the prison, five actions pin the officer names to the key each officer
# carries, so the journal / hover tooltip reads "Officer 1 - Cell Key",
# "Officer 2 - Utility Key" and so on instead of random names. The mapping
# is fixed in every prison by the game itself (frame "game", group
# assign_keys), so the mod works on every map. Full write-up:
# docs/GUARD_KEY_MOD.md. What follows is a self-contained reader/writer of
# the Clickteam Fusion 2.5 chunk stream (stdlib only): an exact port of
# toolkit/te_crypto.py, te1_icons.py, te1_frames.py and the structural
# parts of te1_events.py that mods/guard_keys/te1_guardkeys.py relies on.
GK_NAMES = ("Cell Key", "Utility Key", "Entrance Key", "Staff Key",
            "Work Key")
GK_FRAME = "npc_rename"
GK_JUMP_TARGET = 5                             # frame id the start click jumps to
GK_ACT = (36, 88)                              # "Named Variable Object": set string
GK_JUMP_PREFIX = bytes.fromhex("08001a00")     # param: u16 size=8, i16 code=0x1a
GK_MAGIC = 54


class GkSkip(Exception):
    """The exe cannot take this mod - report and leave the file alone."""


# -- the encryption (port of toolkit/te_crypto.py) --------------------------
def _gk_rotl1(x):
    return ((x << 7) | (x >> 1)) & 0xFF


def _gk_make_key(title, copyright_, project, magic_char=GK_MAGIC):
    blob = (title + copyright_ + project).encode("latin1", "replace")
    buf = bytearray(256)
    n = min(len(blob), 256)
    buf[:n] = blob[:n]
    for i in range(128, 256):
        buf[i] = 0
    v33 = 0
    while v33 < 256 and buf[v33]:
        v33 += 1
    v35 = magic_char & 0xFF
    v34 = magic_char & 0xFF
    if (v33 + 1) > 0:
        for i in range(v33 + 1):
            v34 = _gk_rotl1(v34)
            buf[i] ^= v34
            v35 = (v35 + buf[i] * ((v34 & 1) + 2)) & 0xFF
    buf[v33 + 1] = v35
    return bytes(buf)


def _gk_decode_table(key, magic_char=GK_MAGIC):
    buf = list(range(256))
    mc2 = magic_char & 0xFF
    mc3 = magic_char & 0xFF
    pos = 0
    v17 = 0
    v15 = True
    for i in range(256):
        mc3 = _gk_rotl1(mc3)
        if v15:
            mc2 = (mc2 + ((mc3 & 1) + 2) * key[pos]) & 0xFF
        temp = mc3 ^ key[pos]
        if mc3 == key[pos]:
            mc3 = _gk_rotl1(magic_char & 0xFF)
            pos = 0
            v15 = False
            temp = mc3 ^ key[0]
        v13 = buf[i]
        v17 = (v17 + ((temp + v13) & 0xFF)) & 0xFF
        buf[i] = buf[v17]
        pos += 1
        buf[v17] = v13
    return buf


class _GkCipher(object):
    def __init__(self, title, copyright_, project, magic_char=GK_MAGIC):
        self.table = _gk_decode_table(
            _gk_make_key(title, copyright_, project, magic_char), magic_char)
        self._ks = None

    def transform(self, data):
        n = len(data)
        if self._ks is None or len(self._ks) < n:
            b = list(self.table)
            i1 = i2 = 0
            out = bytearray()
            for _ in range(max(n, 4096)):
                i1 = (i1 + 1) & 0xFF
                v7 = b[i1]
                i2 = (i2 + v7) & 0xFF
                v9 = b[i2]
                b[i1] = v9
                b[i2] = v7
                out.append(b[(v7 + v9) & 0xFF])
            self._ks = bytes(out)
        return bytes(a ^ k for a, k in zip(data, self._ks[:n]))


# -- chunk streams (port of te1_icons.py / te1_frames.py) -------------------
def xf_find_stream(d):
    def walk(off):
        r = off
        while r + 8 <= len(d):
            cid, flag, size = struct.unpack_from("<hhi", d, r)
            if not (0 <= flag <= 3) or not (0 <= size < 20000000):
                return None
            r += 8 + size
            if cid == 32639:
                return r
        return None
    for off in range(0x1000, min(len(d), 0x500000)):
        cid, flag, size = struct.unpack_from("<hhi", d, off)
        if cid in (8738, 8739) and 0 <= flag <= 3 and 0 < size < 100000:
            if walk(off):
                return off
    raise RuntimeError("fusion chunk stream not found")


def xf_read_chunks(d, start):
    out = []
    r = start
    while r + 8 <= len(d):
        cid, flag, size = struct.unpack_from("<hhi", d, r)
        out.append((cid, flag, d[r + 8:r + 8 + size]))
        r += 8 + size
        if cid == 32639:
            break
    return out


def xf_dechunk(raw):
    out = []
    r = 0
    while r + 8 <= len(raw):
        cid, flag, size = struct.unpack_from("<hhi", raw, r)
        if size < 0 or r + 8 + size > len(raw):
            break
        out.append((cid, flag, raw[r + 8:r + 8 + size]))
        r += 8 + size
        if cid == 32639:
            break
    return out


def xf_zdec(raw):
    _ds, cs = struct.unpack_from("<II", raw, 0)
    return zlib.decompress(raw[8:8 + cs])


def xf_universal(b):
    try:
        return b.decode("utf-16-le")
    except Exception:
        return b.decode("latin1", "replace")


def xf_decode_sub(cid, flag, raw, cipher):
    if flag == 1:
        return xf_zdec(raw)
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


def xf_encode_sub3(cid, plain, cipher):
    comp = zlib.compress(plain, 9)
    body = bytearray(cipher.transform(struct.pack("<I", len(comp)) + comp))
    if cid & 1:
        body[0] ^= (cid & 0xFF) ^ (cid >> 8)
    return struct.pack("<I", len(plain)) + bytes(body)


def xf_parse(d):
    """-> (stream offset, chunk list, cipher or None) for any Fusion exe."""
    off = xf_find_stream(d)
    chunks = xf_read_chunks(d, off)
    by = {}
    for c in chunks:
        by.setdefault(c[0], []).append(c)
    try:
        name = xf_universal(xf_zdec(by[8740][0][2])).strip("\0")
        cop = xf_universal(xf_zdec(by[8763][0][2])).strip("\0")
        ed = xf_universal(xf_zdec(by[8750][0][2])).strip("\0")
    except (KeyError, IndexError, zlib.error, struct.error, UnicodeError):
        name = ""
    cipher = _GkCipher(name, cop, ed) if name else None
    return off, chunks, cipher


# -- frame events -----------------------------------------------------------
def gk_frame_events(chunks, cipher, frame_name):
    """-> (index of the frame chunk, its subchunks, its decrypted events)."""
    for i, (cid, _flag, data) in enumerate(chunks):
        if cid != 13107:
            continue
        subs = xf_dechunk(data)
        name = None
        events = None
        for scid, sflag, sraw in subs:
            if scid == 13109:
                try:
                    name = xf_decode_sub(scid, sflag, sraw, cipher)
                except Exception:
                    name = None
            elif scid == 13117:
                try:
                    events = xf_decode_sub(scid, sflag, sraw, cipher)
                except Exception:
                    events = None
        name = name.decode("utf-16-le", "replace").strip("\0") if name else ""
        if name == frame_name:
            if events is None:
                raise GkSkip("the events chunk could not be decoded")
            return i, subs, events
    raise GkSkip("frame '%s' not found (this build is not supported)"
                 % frame_name)


# -- structural walker: sizes only, no parameter decoding -------------------
def gk_walk_events(events):
    """Validate the whole events blob and return its group records.

    Conditions/actions are walked exactly the way the game's own parser
    walks them (fixed header + parameter records hopped by their size
    fields) - no parameter decoding is needed for this structural pass.
    The caller also gets the ERes/ERev tag offsets: both i32 values hold
    the byte size of the group area and grow by the same delta when a
    group grows.
    """
    if events[:4] != b"ER>>":
        raise GkSkip("events blob has no ER>> header")
    pos = 4 + 2 + 2 + 2 + 17 * 2                     # fixed-size header
    nq, = struct.unpack_from("<h", events, pos)
    pos += 2 + 4 * nq
    if pos > len(events):
        raise GkSkip("events header is truncated")
    info = {"eres_off": None, "erev_off": None, "groups": [], "consumed": None}
    while pos + 4 <= len(events):
        tag = events[pos:pos + 4]
        if tag == b"ERes":
            info["eres_off"] = pos
            info["eres_value"], = struct.unpack_from("<i", events, pos + 4)
            pos += 8
        elif tag == b"ERev":
            info["erev_off"] = pos
            size, = struct.unpack_from("<i", events, pos + 4)
            gend = pos + 8 + size
            if size < 0 or gend > len(events):
                raise GkSkip("group area size is broken")
            p = pos + 8
            while p < gend:
                g = _gk_group_record(events, p, gend)
                info["groups"].append(g)
                p = g["end"]
            if p != gend:
                raise GkSkip("group records overrun the group area")
            pos = gend
        elif tag == b"ERop":
            pos += 8
        elif tag == b"<<ER":
            pos += 4
            info["consumed"] = pos
            break
        else:
            raise GkSkip("unknown events tag %r" % tag)
    if info["consumed"] is None:
        raise GkSkip("events blob has no <<ER terminator")
    if info["consumed"] != len(events):
        raise GkSkip("events blob has trailing data")
    if info["eres_off"] is None or info["erev_off"] is None:
        raise GkSkip("events blob has no ERes/ERev headers")
    if info["eres_value"] != struct.unpack_from(
            "<i", events, info["erev_off"] + 4)[0]:
        raise GkSkip("ERes/ERev sizes disagree (unknown build)")
    return info


def _gk_param_end(buf, p, limit):
    """End of one parameter record, exactly like the game's parser: the
    i16 size field positions the cursor (start+size), a non-positive size
    leaves it right after the 4-byte size+code header."""
    if p + 4 > limit:
        raise GkSkip("parameter header outside its record")
    psz, = struct.unpack_from("<h", buf, p)
    h = p + psz if psz > 0 else p + 4
    if h < p + 4 or h > limit:
        raise GkSkip("parameter size is broken")
    return h


def _gk_group_records(events, p, count, header_len, limit):
    """Raw slices of <count> condition/action records: fixed-size header
    (16/14 bytes; the u8 at offset 12 is the parameter count in both) and
    <nparams> parameter records hopped by their own size fields."""
    raws = []
    for _ in range(count):
        if p + header_len > limit:
            raise GkSkip("record header outside its group")
        q = p + header_len
        for _ in range(events[p + 12]):
            q = _gk_param_end(events, q, limit)
        raws.append(events[p:q])
        p = q
    return raws, p


def _gk_group_record(events, start, limit):
    # group header: i16 size, u8 ncond, u8 nact, u16 flags, i16 line,
    # i32 isRestricted, i32 restrictCpt -> 16 bytes before the records
    if start + 16 > limit:
        raise GkSkip("group header outside the blob")
    size, = struct.unpack_from("<h", events, start)
    ncond = events[start + 2]
    nact = events[start + 3]
    flags, = struct.unpack_from("<H", events, start + 4)
    line, isr, rcp = struct.unpack_from("<hii", events, start + 6)
    # a negative size is the real size of everything after this field; a
    # positive one is meaningless and the records walk decides
    end = start - size if size < 0 else limit
    conds, p = _gk_group_records(events, start + 16, ncond, 16, end)
    acts, p = _gk_group_records(events, p, nact, 14, end)
    if size < 0 and p > end:
        raise GkSkip("group records overrun the group")
    padded = False
    if size < 0 and p < end:
        # padding the game's parser shrugs at: the group is fine as a raw
        # slice, but we could never rebuild it by hand - so it can never
        # be the target group (checked by the caller)
        padded = True
        conds = []
        acts = []
        p = end
    if size >= 0:
        end = p
    return dict(start=start, end=end, flags=flags, line=line, isr=isr,
                rcp=rcp, conds=conds, acts=acts, padded=padded)


def gk_act_params(raw):
    """Split an action record into its parameter records (by size only)."""
    if len(raw) < 14:
        raise GkSkip("action record truncated")
    p = 14
    out = []
    for _ in range(raw[12]):
        h = _gk_param_end(raw, p, len(raw))
        out.append(raw[p:h])
        p = h
    return out


def gk_iter_strings(param_raw):
    """All string literals hidden in a parameter record (otype=-1 num=3)."""
    out = []
    i = 0
    b = param_raw
    while True:
        i = b.find(b"\xff\xff\x03\x00", i)
        if i < 0 or i + 6 > len(b):
            break
        esize, = struct.unpack_from("<H", b, i + 4)
        if 6 <= esize <= 800 and i + esize <= len(b):
            wide = b[i + 6:i + esize]
            if wide.endswith(b"\0\0"):
                try:
                    s = wide[:-2].decode("utf-16-le")
                    if all(ch.isprintable() for ch in s):
                        out.append(s)
                except UnicodeDecodeError:
                    pass
            i += esize
        else:
            i += 1
    return out


def gk_str_literal_param(s):
    """code=0x2d parameter holding exactly one string literal."""
    wide = s.encode("utf-16-le") + b"\0\0"
    expr = (b"\xff\xff\x03\x00" + struct.pack("<H", 6 + len(wide)) + wide)
    payload = struct.pack("<H", 0) + expr + b"\0\0\0\0"
    return struct.pack("<HH", 4 + len(payload), 0x2D) + payload


def gk_act_set_var_string(var_name, value, obj_info):
    """Action 88 of the "Named Variable Object": act88(<name>, <string>)."""
    obj_type, num = GK_ACT
    params = gk_str_literal_param(var_name) + gk_str_literal_param(value)
    head = (struct.pack("<hhHh", obj_type, num, obj_info, 0)
            + struct.pack("<bb", 0, 0) + bytes([2, 0]))
    return struct.pack("<H", 2 + len(head) + len(params)) + head + params


def gk_group_bytes(flags, line, isr, rcp, conds, acts):
    body = (bytes([len(conds), len(acts)]) + struct.pack("<H", flags)
            + struct.pack("<h", line) + struct.pack("<ii", isr, rcp)
            + b"".join(conds) + b"".join(acts))
    return struct.pack("<h", -(2 + len(body))) + body


def gk_is_jump(rec):
    ot, num = struct.unpack_from("<hh", rec, 2)
    if ot != -3 or num != 2:
        return False
    for pr in gk_act_params(rec):
        if (len(pr) == 8 and pr.startswith(GK_JUMP_PREFIX)
                and struct.unpack_from("<i", pr, 4)[0] == GK_JUMP_TARGET):
            return True
    return False


def gk_find_start_group(groups):
    """The one group that sends the player into the prison (start click)."""
    hits = [g for g in groups if any(gk_is_jump(a) for a in g["acts"])]
    if len(hits) != 1:
        raise GkSkip("expected exactly one start group, found %d" % len(hits))
    return hits[0]


def gk_already_patched(groups):
    for g in groups:
        strs = []
        for a in g["acts"]:
            _ot, num = struct.unpack_from("<hh", a, 2)
            if num != GK_ACT[1]:
                continue
            for pr in gk_act_params(a):
                strs += gk_iter_strings(pr)
        if "GuardName_1" in strs and GK_NAMES[0] in strs:
            return True
    return False


def gk_selftest_obj_info(groups):
    """Find the Named Variable Object's obj_info by rebuilding one of the
    game's own act88 actions and requiring byte-for-byte equality. If the
    encoding ever changes this returns None and the exe is left alone."""
    last = None
    for g in groups:
        for a in g["acts"]:
            ot, num = struct.unpack_from("<hh", a, 2)
            if (ot, num) != GK_ACT:
                continue
            obj_info, = struct.unpack_from("<H", a, 6)
            last = obj_info
            prs = gk_act_params(a)
            if len(prs) != 2:
                continue
            s0 = gk_iter_strings(prs[0])
            s1 = gk_iter_strings(prs[1])
            if len(s0) != 1 or len(s1) != 1:
                continue
            if (len(prs[0]) != 16 + (len(s0[0]) + 1) * 2
                    or len(prs[1]) != 16 + (len(s1[0]) + 1) * 2):
                continue
            if gk_act_set_var_string(s0[0], s1[0], obj_info) == a:
                return obj_info
    return last


# -- patch + verify ----------------------------------------------------------
def gk_build_events(events, info):
    """-> the new events blob with five officer-name actions spliced into
    the start group. Raises GkSkip on anything unexpected."""
    groups = info["groups"]
    if gk_already_patched(groups):
        return None
    g = gk_find_start_group(groups)
    jump_raw = g["acts"][-1]
    if not any(pr.startswith(GK_JUMP_PREFIX) for pr in gk_act_params(jump_raw)):
        raise GkSkip("start group does not end with a frame jump")
    # our encoding must reproduce the untouched group byte-for-byte
    ident = gk_group_bytes(g["flags"], g["line"], g["isr"], g["rcp"],
                           g["conds"], g["acts"])
    if ident != events[g["start"]:g["end"]]:
        raise GkSkip("group layout does not round-trip")
    obj_info = gk_selftest_obj_info(groups)
    if obj_info is None:
        raise GkSkip("no 'Named Variable Object' donor found")
    acts = list(g["acts"][:-1])
    for i, label in enumerate(GK_NAMES, start=1):
        acts.append(gk_act_set_var_string("GuardName_%d" % i, label, obj_info))
    acts.append(jump_raw)
    new_group = gk_group_bytes(g["flags"], g["line"], g["isr"], g["rcp"],
                               g["conds"], acts)
    delta = len(new_group) - (g["end"] - g["start"])
    out = bytearray(events[:g["start"]] + new_group + events[g["end"]:])
    for off in (info["eres_off"], info["erev_off"]):
        v, = struct.unpack_from("<i", events, off + 4)
        struct.pack_into("<i", out, off + 4, v + delta)
    return bytes(out)


def gk_rebuild_exe(d, off, chunks, frame_idx, subs, new_events, cipher):
    out_subs = []
    for scid, sflag, sraw in subs:
        raw = (xf_encode_sub3(scid, new_events, cipher)
               if scid == 13117 else sraw)
        out_subs.append(struct.pack("<hhi", scid, sflag, len(raw)) + raw)
    frame_body = b"".join(out_subs)
    out = [d[:off]]
    for i, (cid, flag, data) in enumerate(chunks):
        if i == frame_idx:
            data = frame_body
        out.append(struct.pack("<hhi", cid, flag, len(data)) + data)
    return b"".join(out)


def gk_verify_exe(old_d, off, chunks, fi, subs, events, info, new_exe):
    """Deep-check a freshly-built exe before it may replace the original:
    every chunk except the npc_rename frame is byte-identical, the events
    walk cleanly to the terminator, the old groups did not move..."""
    off2, chunks2, cipher2 = xf_parse(new_exe)
    if off2 != off:
        raise GkSkip("chunk stream moved")
    if cipher2 is None or len(chunks2) != len(chunks):
        raise GkSkip("chunk table changed")
    for i, (c1, c2) in enumerate(zip(chunks, chunks2)):
        if i != fi and c1 != c2:
            raise GkSkip("chunk #%d (%d) changed" % (i, c1[0]))
    fi2, subs2, events2 = gk_frame_events(chunks2, cipher2, GK_FRAME)
    if fi2 != fi:
        raise GkSkip("the frame moved inside the stream")
    for s1, s2 in zip(subs, subs2):
        if s1 != s2 and s1[0] != 13117:
            raise GkSkip("subchunk %d changed" % s1[0])
    info2 = gk_walk_events(events2)
    if len(info2["groups"]) != len(info["groups"]):
        raise GkSkip("group count changed")
    # everything outside the spliced group must be untouched
    ho1 = events.rindex(b"ERev") + 8
    ho2 = events2.rindex(b"ERev") + 8
    g = gk_find_start_group(info["groups"])
    g2 = gk_find_start_group(info2["groups"])
    if (events[ho1:g["start"]] != events2[ho2:g2["start"]]
            or events[g["end"]:] != events2[g2["end"]:]
            or events[:info["eres_off"]] != events2[:info2["eres_off"]]):
        raise GkSkip("neighbouring groups changed")
    # the five names must be readable from the new start group
    found = {}
    for a in g2["acts"]:
        _ot, num = struct.unpack_from("<hh", a, 2)
        if num != GK_ACT[1]:
            continue
        prs = gk_act_params(a)
        if len(prs) == 2:
            s0 = gk_iter_strings(prs[0])
            s1 = gk_iter_strings(prs[1])
            if s0 and s0[0].startswith("GuardName_"):
                found[s0[0]] = s1[0] if s1 else None
    want = dict(("GuardName_%d" % i, GK_NAMES[i - 1]) for i in range(1, 6))
    if found != want:
        raise GkSkip("the officer names did not land as planned")
    return True


def gk_patch_exe(path, backup_dir):
    """Patch one game executable in place (backing it up first).

    -> (status, detail) with status in
    patched / already / skipped / failed
    """
    try:
        d = path.read_bytes()
        off, chunks, cipher = xf_parse(d)
    except Exception:
        return ("skipped", "not a Clickteam Fusion executable")
    if cipher is None:
        return ("skipped", "no product key inside (unsupported build)")
    try:
        fi, subs, events = gk_frame_events(chunks, cipher, GK_FRAME)
        info = gk_walk_events(events)
        if gk_already_patched(info["groups"]):
            return ("already", "the mod is already in this exe")
        new_events = gk_build_events(events, info)
        new_exe = gk_rebuild_exe(d, off, chunks, fi, subs, new_events, cipher)
        gk_verify_exe(d, off, chunks, fi, subs, events, info, new_exe)
    except GkSkip as e:
        return ("skipped", str(e))
    except Exception as e:
        return ("skipped", "self-check failed: %s" % e)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / path.name
        if not backup.exists():
            shutil.copy2(str(path), str(backup))
        tmp = path.with_name(path.name + ".te1-new")
        tmp.write_bytes(new_exe)
        os.replace(str(tmp), str(path))
    except OSError as e:
        return ("failed", str(e))
    return ("patched", "%d -> %d bytes" % (len(d), len(new_exe)))


def gk_exe_state(path):
    """patched / clean / unsupported - for --status and the test suite."""
    try:
        d = path.read_bytes()
        _off, chunks, cipher = xf_parse(d)
        if cipher is None:
            return "unsupported"
        _fi, _subs, events = gk_frame_events(chunks, cipher, GK_FRAME)
        info = gk_walk_events(events)
        return "patched" if gk_already_patched(info["groups"]) else "clean"
    except GkSkip:
        return "unsupported"
    except Exception:
        return "unreadable"


# ---------------------------------------------------------- watching the game
# Delays, in seconds. See Launcher.wait_for_game() for what they mean.
HANDOVER = 8.0     # launcher closed, waiting for the real game to appear
EXIT_GRACE = 2.0   # the real game closed - make sure it is really gone
POLL_IDLE = 1.5    # how often to look while the game is running
POLL_BUSY = 0.4    # how often to look while a delay is counting down

# TheEscapists.exe (2.4 MB) is a front end; the real game is
# TheEscapists_rus.exe / _eur.exe / _pol.exe (8 MB each). Anything that is
# game sized or carries the game's name counts, installers never do.
IS_WINDOWS = (os.name == "nt")
GAME_EXE_MIN_SIZE = 1024 * 1024
NON_GAME_EXE = re.compile(r"^(unins|uninst|setup|vcredist|dxsetup|dotnet|"
                          r"redist|install|crash|report|steam)", re.I)
# Always watched, even when the file is not in the folder we scanned.
KNOWN_GAME_EXES = ("theescapists.exe", "theescapists_rus.exe",
                   "theescapists_eur.exe", "theescapists_pol.exe",
                   "theescapists_ger.exe", "theescapists_fre.exe",
                   "theescapists_spa.exe", "theescapists_ita.exe")


def game_exe_names(game_dir):
    """Lower-case image names of everything in the folder that is a game."""
    names = set()
    try:
        root = Path(game_dir)
        for f in list(root.glob("*.exe")) + list(root.glob("*/*.exe")):
            if NON_GAME_EXE.match(f.name):
                continue
            if (f.name.lower().startswith("theescapists")
                    or f.stat().st_size >= GAME_EXE_MIN_SIZE):
                names.add(f.name.lower())
    except OSError:
        pass
    names.update(KNOWN_GAME_EXES)
    return names


def short_list(names, keep=3):
    ordered = sorted(names)
    head = ", ".join(ordered[:keep])
    if len(ordered) > keep:
        head += " (+%d more)" % (len(ordered) - keep)
    return head


def running_pids(names):
    """PIDs of running processes whose image name is in `names`.

    Windows only - everywhere else this returns nothing and the launcher
    falls back to the plain delays.
    """
    if not names or not IS_WINDOWS:
        return set()
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=10)
        text = out.stdout.decode("utf-8", "replace")
    except Exception:
        return set()
    pids = set()
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        if row[0].strip().lower() in names:
            try:
                pids.add(int(row[1].strip()))
            except ValueError:
                pass
    return pids


# ----------------------------------------------------------- Better Translate
def translate_items(ini_rus, ini_eng, log):
    """items_rus.dat: names, descriptions and recipes."""
    fx = FIXES.get("items", {})
    changed = 0
    for group, keys in (("name", ("Name",)), ("info", ("Info",)), ("craft", ("Craft",))):
        table = fx.get(group, {})
        for sid, value in table.items():
            for key in keys:
                if not ini_rus.has(sid, key):
                    continue
                if ini_rus.get(sid, key) != value:
                    ini_rus.set(sid, key, value)
                    changed += 1
    # generic tidy-up of the human readable fields
    for sec in ini_rus.sections():
        for key in ini_rus.keys(sec):
            if key not in ("Name", "Info", "Craft"):
                continue
            new = normalize_ru(ini_rus.get(sec, key), None, full=False)
            if new != ini_rus.get(sec, key):
                ini_rus.set(sec, key, new)
                changed += 1
    log.append("item names / descriptions: %d fix(es)" % changed)
    return changed


def translate_generic(ini_rus, ini_eng, table, log, label):
    """data_rus.dat / speech_rus.dat: the shared pass.

    1. exact replacements from the curated table
    2. lines the English file has but the Russian one never got
    3. generic clean-up (NBSP, spacing, homoglyphs, N@ prefix, capitalisation)
    """
    changed = 0
    added = 0

    # ---- 1. curated replacements (+ the "_fix" block for speech)
    for block in (table, table.get("_fix", {})):
        for sec, pairs in block.items():
            if sec.startswith("_"):
                continue
            for key, value in pairs.items():
                if not ini_rus.has(sec, key):
                    continue
                if ini_rus.get(sec, key) != value:
                    ini_rus.set(sec, key, value)
                    changed += 1

    # ---- 2. lines missing from the Russian file
    for sec in ini_eng.sections():
        if not ini_rus.has(sec, "Count"):
            continue
        try:
            count = int(ini_eng.get(sec, "Count", "0"))
        except ValueError:
            count = 0
        for i in range(1, count + 1):
            key = str(i)
            eng = ini_eng.get(sec, key)
            if eng is None or ini_rus.has(sec, key):
                continue
            rus = table.get(sec, {}).get(key)
            if rus is None:
                continue
            ini_rus.add(sec, key, rus)
            added += 1

    # ---- 3. generic clean-up
    for sec in ini_rus.sections():
        for key in ini_rus.keys(sec):
            if key == "Count":
                continue
            old = ini_rus.get(sec, key)
            eng = ini_eng.get(sec, key)
            if eng is not None and old == eng:
                continue                      # never translated - handled above
            new = sync_prefix(old, eng)
            new = normalize_ru(new, eng, full=True)
            if new != old:
                ini_rus.set(sec, key, new)
                changed += 1

    # ---- 4. keep Count in step with the lines that are really there
    for sec in ini_rus.sections():
        if not ini_rus.has(sec, "Count"):
            continue
        nums = sorted(int(k) for k in ini_rus.keys(sec) if k.isdigit())
        if nums and nums == list(range(1, len(nums) + 1)):
            want = str(len(nums))
            if ini_rus.get(sec, "Count") != want:
                ini_rus.set(sec, "Count", want)
                changed += 1

    log.append("%s: %d line(s) fixed, %d line(s) added" % (label, changed, added))
    return changed + added


def mod_better_translate(data_dir, log, dry=False):
    """Returns the number of changed lines, or None when it cannot run."""
    pairs = [("items", translate_items), ("data", None), ("speech", None)]
    total = 0
    for kind, fn in pairs:
        f_rus = Path(data_dir) / ("%s_rus.dat" % kind)
        f_eng = Path(data_dir) / ("%s_eng.dat" % kind)
        if not f_rus.exists():
            log.append("%s_rus.dat: not found - skipped" % kind)
            continue
        if not f_eng.exists():
            log.append("%s_eng.dat: reference missing - %s_rus.dat skipped"
                       % (kind, kind))
            continue
        rus = DatFile(f_rus)
        eng = DatFile(f_eng)
        if not rus.ok or not eng.ok:
            log.append("%s_rus.dat: unreadable (encrypted?) - skipped" % kind)
            continue
        ini_rus = Ini(rus.text)
        ini_eng = Ini(eng.text)
        if kind == "items":
            n = translate_items(ini_rus, ini_eng, log)
        else:
            n = translate_generic(ini_rus, ini_eng,
                                  FIXES.get(kind, {}), log, "%s_rus.dat" % kind)
        total += n
        if n and not dry:
            rus.text = ini_rus.text
            rus.save()
    return total


# ---------------------------------------------------------------- Randomizer
STAT_POOLS = {
    "Weapon": [0, 1, 1, 2, 2, 3, 3, 4, 5],
    "Digging": [0, 0, 1, 1, 2, 2, 3, 5],
    "Chipping": [0, 0, 1, 1, 2, 2, 3, 5],
    "Cutting": [0, 0, 1, 1, 2, 2, 3, 5],
    "Unscrewing": [0, 0, 1, 1, 2, 3],
    "HP": [0, 0, 0, 5, 10, 15, 25, 40],
    "FAT": [0, 0, 0, 5, 10, 20],
    "Gift": [0, 1, 1, 2, 2, 3, 5],
    "Decay": [0, 1, 2, 2, 5, 5, 10],
    "Buy": [0, 5, 10, 20, 30, 50, 75, 100],
}
STAT_KEYS = list(STAT_POOLS)
# Name, Craft, Info, Found, Desk, Outfit, NPC_carry and CamDis are never
# touched: they carry the readable text and the prison-editor data.


def randomize_items_text(text, seed, chaos=3):
    ini = Ini(text)
    rnd = random.Random(seed)
    touched = 0
    for sec in ini.sections():
        keys = ini.keys(sec)
        name = (ini.get(sec, "Name") or "").strip().lower()
        if name in ("", "empty", "none"):
            continue
        props = [(k, ini.get(sec, k)) for k in keys
                 if k not in STAT_KEYS and k != "Illegal"]
        stats = []
        for k in rnd.sample(STAT_KEYS, min(chaos + 1, len(STAT_KEYS))):
            v = rnd.choice(STAT_POOLS[k])
            if v:
                stats.append((k, str(v)))
        extra = []
        if chaos >= 2 and rnd.random() < 0.5:
            extra.append(("Illegal", "1" if rnd.random() < 0.7 else "0"))
        # Name first, then the rolled stats, then everything else
        head = [p for p in props if p[0] == "Name"]
        tail = [p for p in props if p[0] != "Name"]
        ini.rewrite_section(sec, head + stats + tail + extra)
        touched += 1
    return ini.text, touched


def mod_randomizer(data_dir, log, seed=None):
    if seed is None:
        seed = random.randrange(1, 2 ** 31)
    total = 0
    files = sorted(Path(data_dir).glob("items_*.dat"))
    if not files:
        log.append("no items_*.dat found - nothing to randomize")
        return 0
    for f in files:
        d = DatFile(f)
        if not d.ok:
            log.append("%s: unreadable (encrypted?) - skipped" % f.name)
            continue
        new_text, touched = randomize_items_text(d.text, seed)
        if new_text != d.text:
            d.text = new_text
            d.save()
        total += touched
        log.append("%s: %d item(s) re-rolled (seed %d)" % (f.name, touched, seed))
    return total


# --------------------------------------------------------------- mod plumbing
class Launcher(object):

    def __init__(self, game_dir):
        self.game = Path(game_dir)
        self.data = self.game / "Data"
        self.mods = self.game / "mods"
        self.orig = self.mods / "original"
        self.exe_orig = self.orig / "exe"
        self.state_path = self.mods / "mods.json"
        self.state = {"randomizer": False, "better_translate": False,
                      "guard_keys": False}
        self.handover = HANDOVER
        self.exit_grace = EXIT_GRACE

    # -- state ------------------------------------------------------------
    def load_state(self):
        try:
            self.state.update(json.loads(self.state_path.read_text("utf-8")))
        except Exception:
            pass
        return self.state

    def save_state(self):
        self.mods.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_path,
                     json.dumps(self.state, indent=2, sort_keys=True).encode("utf-8"))

    def enabled(self):
        return [m for m, _label in MODS if self.state.get(m)]

    # -- backups ----------------------------------------------------------
    def backup(self, names):
        self.orig.mkdir(parents=True, exist_ok=True)
        for name in names:
            src = self.data / name
            dst = self.orig / name
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)

    def restore(self, quiet=False):
        """Put the pristine Data\\*.dat files back.

        This is what makes the mods launcher-only: a game started straight
        from Steam always gets the untouched files.
        """
        n = 0
        if not self.orig.exists():
            return 0
        for src in sorted(self.orig.glob("*.dat")):
            dst = self.data / src.name
            try:
                shutil.copy2(src, dst)
                n += 1
            except OSError as e:
                say("    ! could not restore %s: %s" % (src.name, e))
        if n and not quiet:
            say("    restored %d original file(s)" % n)
        return n

    def writable(self):
        probe = self.data / ".te1_write_test"
        try:
            probe.write_bytes(b"x")
            probe.unlink()
            return True
        except OSError:
            return False

    # -- guard keys (exe patcher) ------------------------------------------
    # Unlike the Data mods, Guard Key Names rewrites the game executables
    # themselves (theescapists*.exe - the 8 MB real game, not the small
    # front end). Backups live in mods\original\exe.
    def gk_candidates(self):
        out = []
        try:
            for f in sorted(self.game.glob("*.exe")):
                if NON_GAME_EXE.match(f.name):
                    continue
                if f.name.lower().startswith("theescapists"):
                    out.append(f)
        except OSError:
            pass
        return out

    def gk_apply(self):
        """Patch every game executable that needs it. Idempotent: an exe
        that already holds the mod (from a previous launch or from the
        standalone patcher) is left alone - and never backed up over a
        clean copy."""
        log = []
        cands = self.gk_candidates()
        if not cands:
            log.append("Guard Key Names: no theescapists*.exe found here")
            return False, log
        n_ok = 0
        for f in cands:
            status, detail = gk_patch_exe(f, self.exe_orig)
            if status == "patched":
                n_ok += 1
                log.append("Guard Key Names: %s patched (%s)" % (f.name, detail))
            elif status == "already":
                n_ok += 1
                line = "Guard Key Names: %s was already patched" % f.name
                if not (self.exe_orig / f.name).exists():
                    line += " (no backup on file; Uninstall cannot revert it)"
                log.append(line)
            elif status == "skipped":
                log.append("Guard Key Names: %s skipped - %s" % (f.name, detail))
            else:
                log.append("Guard Key Names: %s FAILED - %s" % (f.name, detail))
        if not n_ok:
            log.append("Guard Key Names: not a single executable could take "
                       "the mod; the full Steam/GOG build of the game is "
                       "required (two-level build with the npc_rename frame)")
            return False, log
        return True, log

    def gk_restore_all(self):
        """Put the backed-up executables back. -> (restored, problems)"""
        restored = []
        problems = []
        if not self.exe_orig.exists():
            return restored, problems
        for bak in sorted(self.exe_orig.glob("*.exe")):
            dst = self.game / bak.name
            try:
                shutil.copy2(str(bak), str(dst))
                bak.unlink()
                restored.append(bak.name)
            except OSError as e:
                problems.append("%s: %s" % (bak.name, e))
        return restored, problems

    # -- applying ---------------------------------------------------------
    def files_to_touch(self):
        names = set()
        if self.state.get("better_translate"):
            for kind in ("items", "data", "speech"):
                names.add("%s_rus.dat" % kind)
        if self.state.get("randomizer"):
            for f in self.data.glob("items_*.dat"):
                names.add(f.name)
        names.add("val.dat")
        return sorted(names)

    def apply(self):
        log = []
        if not self.writable():
            say("")
            say("  ! No write access to the game folder:")
            say("      %s" % self.data)
            say("")
            say("    The game lives in Program Files. Close this window and start")
            say("    the launcher again - it will ask Windows for administrator")
            say("    rights. Alternatively right-click the .bat and pick")
            say("    \"Run as administrator\".")
            say("")
            return False, log
        self.restore(quiet=True)
        names = self.files_to_touch()
        self.backup(names)
        ok = True
        if self.state.get("better_translate"):
            try:
                n = mod_better_translate(self.data, log)
                if not n:
                    log.append("Better Translate: nothing left to fix")
            except Exception as e:
                log.append("Better Translate FAILED: %s" % e)
                ok = False
        if self.state.get("randomizer"):
            try:
                mod_randomizer(self.data, log)
            except Exception as e:
                log.append("Randomizer FAILED: %s" % e)
                ok = False
        try:
            rebuild_val(self.data)
            log.append("val.dat rebuilt for the new file sizes")
        except Exception as e:
            log.append("val.dat rebuild FAILED: %s" % e)
            ok = False
        # Guard Key Names lives in the exe, not in Data\, but re-checking it
        # on every launch is what heals the patch whenever Steam updates or
        # "verify integrity" puts a clean executable back.
        if self.state.get("guard_keys"):
            try:
                ok_gk, gk_log = self.gk_apply()
                log.extend(gk_log)
                ok = ok and ok_gk
            except Exception as e:
                log.append("Guard Key Names FAILED: %s" % e)
                ok = False
        return ok, log

    # -- launching --------------------------------------------------------
    def launch(self):
        exe = self.game / EXE_NAME
        if not exe.exists():
            say("  ! %s is gone - cannot start the game." % EXE_NAME)
            return False
        say("")
        say("  Starting %s ..." % EXE_NAME)
        say("  Watching: %s" % short_list(game_exe_names(self.game)))
        say("  (this window stays open and puts your files back when you quit)")
        say("")
        try:
            proc = subprocess.Popen([str(exe)], cwd=str(self.game))
        except Exception as e:
            say("  ! could not start the game: %s" % e)
            return False
        try:
            self.wait_for_game(proc)
        except KeyboardInterrupt:
            pass
        return True

    def wait_for_game(self, proc):
        """Wait until the player is really done.

        TheEscapists.exe is only a front end: it shows a "Play" button,
        closes itself and starts the actual game (TheEscapists_rus.exe /
        _eur.exe / _pol.exe, 8 MB each). Waiting on our own child is
        therefore not enough - it would hand the files back while the
        player is still looking at the menu.

        So we watch every game executable in the folder. Two different
        delays are used, because the two gaps are very different:

          handover  - launcher closed, real game has not shown up yet
          exit      - the game itself closed; make sure it is really gone

        If a game process turns up during either delay the wait restarts.
        """
        names = game_exe_names(self.game)
        child = proc.pid
        game_seen = False
        deadline = None
        said_wait = False
        while True:
            others = running_pids(names) - {child}
            if proc.poll() is None or others:
                # something is still up: launcher, game, or both
                if others and not game_seen:
                    game_seen = True
                    say("  The game is running.")
                deadline = None
                said_wait = False
                time.sleep(POLL_IDLE)
                continue
            # nothing of ours is running any more
            if deadline is None:
                window = self.exit_grace if game_seen else self.handover
                deadline = time.monotonic() + window
            if time.monotonic() >= deadline:
                if not game_seen:
                    say("  The game never started - restoring your files.")
                return
            if not game_seen and not said_wait:
                say("  Waiting for the game to start (%.0f seconds)..."
                    % self.handover)
                said_wait = True
            time.sleep(POLL_BUSY)


# ------------------------------------------------------------------- screens
def header(launcher):
    say("")
    rule("=")
    say("  THE ESCAPISTS 1 - MOD LAUNCHER   (engine v%s)" % ENGINE_VERSION)
    rule("=")
    say("  Game folder : %s" % launcher.game)
    say("  Mods folder : %s" % launcher.mods)
    installed = [label for m, label in MODS if launcher.state.get(m)]
    say("  Installed   : %s" % (", ".join(installed) if installed else "none"))
    rule("-")


def screen_main(launcher):
    while True:
        header(launcher)
        say("")
        say("   1. Launch Game With Mods")
        say("   2. Mod Workshop")
        say("   3. Exit")
        say("")
        c = ask("  Choose 1-3", "1")
        if c == "1":
            screen_launch(launcher)
        elif c == "2":
            screen_workshop(launcher)
        elif c == "3":
            return
        else:
            say("  -> please type 1, 2 or 3")


def screen_launch(launcher):
    if not launcher.enabled():
        say("")
        say("  No mods are installed yet - the game would start as usual.")
        if ask("  Open the Mod Workshop instead? (y/n)", "y").lower() != "y":
            return
        screen_workshop(launcher)
        return
    say("")
    rule("-")
    say("  APPLYING MODS")
    rule("-")
    ok, log = launcher.apply()
    for line in log:
        say("   " + line)
    if not ok:
        say("")
        say("  ! Something went wrong. Your original files are being restored.")
        launcher.restore()
        pause()
        return
    say("")
    say("  Mods are active. They will be removed when the game closes.")
    if not launcher.launch():
        launcher.restore()
        pause()
        return
    say("")
    rule("-")
    say("  Game closed - restoring your original files")
    rule("-")
    launcher.restore()
    say("")
    say("  Done. The game is unmodded again.")
    pause()


def screen_workshop(launcher):
    while True:
        header(launcher)
        say("")
        say("  MOD WORKSHOP")
        say("")
        for i, (key, label) in enumerate(MODS, start=1):
            mark = "INSTALLED" if launcher.state.get(key) else "not installed"
            say("   %d. %-18s [%s]" % (i, label, mark))
        say("   %d. Back" % (len(MODS) + 1))
        say("")
        c = ask("  Choose 1-%d" % (len(MODS) + 1), str(len(MODS) + 1))
        if c.isdigit() and 1 <= int(c) <= len(MODS):
            screen_mod(launcher, MODS[int(c) - 1])
        elif c == str(len(MODS) + 1):
            return
        else:
            say("  -> please type 1, 2 or 3")


def screen_mod(launcher, mod):
    key, label = mod
    while True:
        header(launcher)
        say("")
        say("  %s" % label.upper())
        rule("-")
        state = "INSTALLED" if launcher.state.get(key) else "NOT INSTALLED"
        say("  Status: %s" % state)
        say("")
        if key == "randomizer":
            say("  Shuffles the stats of every item (damage, digging, HP, price,")
            say("  gift value, decay and more). Names, descriptions and recipes")
            say("  stay readable. Rolled again on every single launch, so you")
            say("  never get the same prison twice.")
        elif key == "guard_keys":
            say("  Renames the five key-carrying officers after their key:")
            say("  the 1st always holds the Cell Key, the 4th the red Staff")
            say("  Key and so on - one glance at a guard or the journal tells")
            say("  you whom to knock out for which key. Nothing else changes.")
            say("")
            say("  Unlike the other mods this one patches the game executable")
            say("  (theescapists_*.exe, not the Data files). The original exe")
            say("  is backed up to mods\\original\\exe and Uninstall puts it")
            say("  back. The patch stays active even for a Steam start.")
        else:
            say("  Repairs the official Russian translation: fills in the lines")
            say("  that were never translated, fixes machine-translated item")
            say("  names, restores broken $variables, cleans up spacing and")
            say("  homoglyphs (Latin letters typed inside Russian words).")
            say("  The English files are used as the reference.")
        say("")
        say("   1. %s" % ("Uninstall" if launcher.state.get(key) else "Install"))
        say("   2. Back")
        say("")
        c = ask("  Choose 1-2", "2")
        if c == "1":
            if key == "guard_keys":
                screen_gk_toggle(launcher, key, label)
                return
            if launcher.state.get(key):
                launcher.state[key] = False
                launcher.save_state()
                launcher.restore()
                say("")
                say("  %s uninstalled. Original files restored." % label)
            else:
                launcher.state[key] = True
                launcher.save_state()
                say("")
                say("  %s installed." % label)
                say("  It will be applied when you launch the game from here.")
            pause()
            return
        if c == "2":
            return
        say("  -> please type 1 or 2")


def screen_gk_toggle(launcher, key, label):
    """Install/uninstall for Guard Key Names: the exe is patched right away,
    so the player immediately sees which executables took the mod."""
    if launcher.state.get(key):
        restored, problems = launcher.gk_restore_all()
        # regardless of restore problems, stop re-applying the patch
        launcher.state[key] = False
        launcher.save_state()
        say("")
        for name in restored:
            say("  %s: original exe restored." % name)
        if problems:
            for p in problems:
                say("  ! could not restore %s" % p)
            say("  All other changes were dropped; the game is still playable")
            say("  (the patched exe only renames officers).")
        elif not restored:
            say("  No backed-up exe found - the game is already stock.")
        say("")
        say("  %s uninstalled." % label)
    else:
        say("")
        say("  Patching the game executable(s)...")
        ok, log = launcher.gk_apply()
        for line in log:
            say("   " + line)
        if ok:
            launcher.state[key] = True
            launcher.save_state()
            say("")
            say("  %s installed." % label)
            say("  Launching from this launcher re-checks the patch, so a")
            say("  Steam update reinstalls the mod on the next start.")
        else:
            launcher.state[key] = False
            launcher.save_state()
            say("")
            say("  ! The mod was NOT installed: no supported game exe here.")
            say("    It needs the full build of The Escapists 1 (Steam/GOG).")
    pause()


# ---------------------------------------------------------------------- main
def find_game_dir(arg=None):
    """Folder that holds TheEscapists.exe, always as an absolute path
    (the engine later uses it as a working directory for the game)."""
    seen = []
    if arg:
        seen.append(Path(arg))
    seen.append(Path(os.getcwd()))
    seen.append(Path(sys.argv[0]).resolve().parent.parent)
    for p in seen:
        try:
            p = Path(os.path.abspath(str(p)))
        except OSError:
            continue
        if (p / EXE_NAME).exists() and (p / "Data").exists():
            return p
    return None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    game = None
    if "--game" in argv:
        i = argv.index("--game")
        if i + 1 < len(argv):
            game = argv[i + 1]
    gd = find_game_dir(game)
    if gd is None:
        say("")
        say("  ! %s not found." % EXE_NAME)
        say("    Put TE1_Mod_Launcher.bat in the game folder and run it there.")
        return 1
    if not (gd / "Data").exists():
        say("")
        say("  ! No Data subfolder next to %s." % EXE_NAME)
        return 1

    launcher = Launcher(gd)
    launcher.mods.mkdir(parents=True, exist_ok=True)
    launcher.load_state()

    def opt(name, default):
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv):
                try:
                    return float(argv[i + 1])
                except ValueError:
                    pass
        return default

    launcher.handover = opt("--handover", HANDOVER)
    launcher.exit_grace = opt("--exit-grace", EXIT_GRACE)

    # Safety net: if the previous run was killed before it could clean up
    # (closed console, crash, power cut), put the game files back first.
    launcher.restore(quiet=True)

    if "--status" in argv:
        say("game   : %s" % launcher.game)
        for key, label in MODS:
            say("%-20s %s" % (label, launcher.state.get(key)))
        if launcher.state.get("guard_keys"):
            for f in launcher.gk_candidates():
                say("%-20s %s" % ("  exe " + f.name, gk_exe_state(f)))
        return 0

    if "--restore" in argv:
        say("")
        n = launcher.restore()
        say("restored %d file(s)" % n)
        return 0

    if "--report" in argv:
        log = []
        say("")
        rule("-")
        say("  BETTER TRANSLATE - dry run")
        rule("-")
        n = mod_better_translate(launcher.data, log, dry=True)
        for line in log:
            say("   " + line)
        say("   total: %d change(s)" % n)
        say("")
        say("   Nothing was written - this was a dry run.")
        return 0

    if "--apply" in argv:
        ok, log = launcher.apply()
        for line in log:
            say("   " + line)
        return 0 if ok else 1

    screen_main(launcher)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EndOfInput:
        say("")
        sys.exit(0)
    except KeyboardInterrupt:
        say("")
        say("interrupted")
        sys.exit(1)
