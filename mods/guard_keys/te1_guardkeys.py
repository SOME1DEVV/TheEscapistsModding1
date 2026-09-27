#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
te1_guardkeys.py — мод «Guard Key Names» для The Escapists 1 (PC, Clickteam Fusion 2.5).

Что делает мод:
    Офицеры в тюрьме получают имена по ключу, который они носят, — вместо
    случайных имён из стандартного пула. Теперь не нужно запоминать/выбивать
    ключи вслепую: достаточно открыть журнал или посмотреть на офицера.

        1-й офицер  ->  Officer Cell Key     (жёлтая дверь,  предмет ID 0)
        2-й офицер  ->  Officer Utility Key  (оранжевая дверь, предмет ID 7)
        3-й офицер  ->  Officer Entrance Key (фиолетовая дверь, предмет ID 6)
        4-й офицер  ->  Officer Staff Key    (красная дверь,  предмет ID 1)
        5-й офицер  ->  Officer Work Key     (зелёная дверь,  предмет ID 43)

    Привязка взята из байткода игры: фрейм `game`, группа assign_keys (#5363)
    раздаёт NPC_1..5_Inv_1 = 0_100 / 7_100 / 6_100 / 1_100 / 43_100
    в КАЖДОЙ тюрьме одинаково, поэтому мод работает на всех картах
    (включая DLC: irongate, escapeteam, DTAF, SS, CCL — там та же группа).

Как работает патч:
    Имена офицеров генерируются фреймом `npc_rename` (экран «население тюрьмы»
    перед стартом игры): cop_loop заполняет переменные GuardName_1..N случайными
    именами. При клике «start» (единственный переход во фрейм игры, группа с
    действием storyboard:Jump to frame) мод дописывает ПЯТЬ действий
    `act88("GuardName_N", <имя ключа>)` прямо перед переходом — имена фиксируются,
    уезжают в save.dat и переживают сохранения/загрузки.

    Патчер вставляет действия внутрь существующей группы событий, пересчитывает
    все заголовки размеров (группа/ERes/ERev/чанк/PE-поток) и пересобирает exe.
    Вся остальная логика игры не изменяется (проверку см. вывод патчера).

Использование:
    python te1_guardkeys.py check   путь/к/TheEscapists.exe
    python te1_guardkeys.py patch   путь/к/TheEscapists.exe -o TheEscapists_guardkeys.exe
        [--names "Cell Key,Utility Key,Entrance Key,Staff Key,Work Key"]
        [--frame npc_rename]

    Затем переименовать результат в TheEscapists.exe в папке игры (сделав бэкап!).

Поддерживаются сборки: european/steam (eur), rus, pol — у всех одинаковый
    набор фреймов. Старые сборки с одним фреймом не поддерживаются (проверка
    `check` честно скажет об этом).

Без внешних зависимостей (Python 3.8+). Инструменты взяты из TE1Modding
(toolkit/te_crypto.py, te1_icons.py; tools/te1_frames.py, te1_events.py).
"""
from __future__ import annotations
import argparse, os, struct, sys, zlib

# ---------------------------------------------------------------- зависимости TE1Modding
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_HERE, "..", "..", "tools"),      # репозиторий TheEscapistsModding1
              os.path.join(_HERE, "..", ".."),               # на случай плоской раскладки
              os.path.join(_HERE, "..", "..", "toolkit")):   # оригинальный TE1Modding
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)

from te_crypto import Cipher                                    # noqa: E402
from te1_icons import find_stream, read_chunks, zdec, universal  # noqa: E402
from te1_frames import dechunk, decode_sub                      # noqa: E402
import te1_events as TE                                         # noqa: E402

# ---------------------------------------------------------------- константы мода
# Порядок офицеров -> ключ (из игры, фрейм game, assign_keys):
#   NPC_1 -> предмет 0  "Cell Key"     (жёлтые двери)
#   NPC_2 -> предмет 7  "Utility Key"  (оранжевые двери)
#   NPC_3 -> предмет 6  "Entrance Key" (фиолетовые двери)
#   NPC_4 -> предмет 1  "Staff Key"    (красные двери)
#   NPC_5 -> предмет 43 "Work Key"     (зелёные двери)
KEY_TABLE = [
    (1, 0,  "Cell Key",     "yellow / жёлтая"),
    (2, 7,  "Utility Key",  "orange / оранжевая"),
    (3, 6,  "Entrance Key", "purple / фиолетовая"),
    (4, 1,  "Staff Key",    "red / красная"),
    (5, 43, "Work Key",     "green / зелёная"),
]
DEFAULT_NAMES = [row[2] for row in KEY_TABLE]

JUMP_PARAM_PREFIX = bytes.fromhex("08001a00")     # param: size=8, code=0x1a, i32=<frame>
ACT_HEADER = (36, 88)                              # obj_type=36 (extension), action 88 (Set String)
MOD_MARKER = b"G\x00u\x00a\x00r\x00d\x00N\x00a\x00m\x00e\x00_\x001\x00"


# ---------------------------------------------------------------- низкоуровневые утилиты
def w16(v): return struct.pack("<h", v)
def W16(v): return struct.pack("<H", v)
def w32(v): return struct.pack("<i", v)


def str_literal_param(s: str) -> bytes:
    """Параметр code=0x2d (строковое выражение), содержащий один строковый литерал."""
    wide = s.encode("utf-16-le") + b"\0\0"
    expr = b"\xff\xff\x03\x00" + W16(6 + len(wide)) + wide          # otype=-1 num=3, esize, UTF-16+\0
    payload = W16(0) + expr + b"\x00\x00\x00\x00"                   # cmp=0, выражение, терминатор списка
    return W16(4 + len(payload)) + W16(0x2D) + payload


def act_set_var_string(var_name: str, value: str, obj_info: int) -> bytes:
    """Действие extension-объекта «Named variable object»: act88(<имя>, <строка>)."""
    obj_type, num = ACT_HEADER
    params = str_literal_param(var_name) + str_literal_param(value)
    head = (w16(obj_type) + w16(num) + W16(obj_info) + w16(0) +
            struct.pack("<bb", 0, 0) + bytes([2, 0]))
    return W16(2 + len(head) + len(params)) + head + params


def iter_strings(param_raw: bytes):
    """Вытащить все строковые литералы (otype=-1 num=3, esize=полный размер записи
    с заголовком 6 байт) из raw-байтов параметра — для проверок."""
    out, b, i = [], param_raw, 0
    while True:
        i = b.find(b"\xff\xff\x03\x00", i)
        if i < 0 or i + 6 > len(b):
            break
        esize = struct.unpack_from("<H", b, i + 4)[0]
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


# ---------------------------------------------------------------- доступ к exe/чанкам
def load_exe(exe_path: str):
    d = open(exe_path, "rb").read()
    stream_off = find_stream(d)
    chunks = read_chunks(d, stream_off)
    by = {}
    for c in chunks:
        by.setdefault(c[0], []).append(c)
    try:
        name = universal(zdec(by[8740][0][2])).strip("\0")
        cop = universal(zdec(by[8763][0][2])).strip("\0")
        ed = universal(zdec(by[8750][0][2])).strip("\0")
    except (KeyError, IndexError, zlib.error, UnicodeDecodeError):
        name, cop, ed = "", "", ""
    cipher = Cipher(name, cop, ed) if name else None
    return d, stream_off, chunks, cipher, name


def decode_frame_events(chunks, cipher, frame_name):
    """→ (индекс фрейма в потоке чанков, список подчанков, расшифрованные события)"""
    for i, (cid, flag, data) in enumerate(chunks):
        if cid != 13107:
            continue
        subs = dechunk(data)
        plain = {}
        for scid, sflag, sraw in subs:
            try:
                plain[scid] = decode_sub(scid, sflag, sraw, cipher)
            except Exception:
                plain[scid] = None
        nm = plain.get(13109)
        nm = nm.decode("utf-16-le", "replace").strip("\0") if nm else "?"
        if nm == frame_name:
            raw13117 = dict((s[0], s[2]) for s in subs)[13117]
            body = bytearray(raw13117[4:])
            if 13117 & 1:
                body[0] ^= (13117 & 0xFF) ^ (13117 >> 8)
            t = cipher.transform(bytes(body))
            cs, = struct.unpack_from("<I", t, 0)
            events = zlib.decompress(t[4:4 + cs])
            return i, subs, events
    raise SystemExit(f"фрейм {frame_name!r} не найден (сборка не поддерживается)")


def encode_sub3(cid: int, plain: bytes, cipher: Cipher) -> bytes:
    compressed = zlib.compress(plain, 9)
    t = w32(len(compressed)) + compressed
    body = bytearray(cipher.transform(t))
    if cid & 1:
        body[0] ^= (cid & 0xFF) ^ (cid >> 8)
    return w32(len(plain)) + bytes(body)


# ---------------------------------------------------------------- работа с событиями
def group_bytes(flags: int, line: int, is_restr: int, restr_cpt: int,
                conds: list, acts: list) -> bytes:
    body = (bytes([len(conds), len(acts)]) + struct.pack("<H", flags) + w16(line) +
            w32(is_restr) + w32(restr_cpt) + b"".join(conds) + b"".join(acts))
    return w16(-(2 + len(body))) + body


def find_start_group(events: bytes):
    """Группа с действием storyboard:Jump to frame (отправляет игрока в тюрьму).
    Во фрейме npc_rename такая группа ровно одна; проверяем это."""
    info, groups = TE.parse_events(events)
    hits = []
    for g in groups:
        for a in g.actions:
            if a.obj_type == -3 and a.num == 2 and len(a.params) == 1:
                p = a.params[0].raw
                # переход во фрейм с индексом 5 = "load" (стартует игру); назад = другой индекс
                if p.startswith(JUMP_PARAM_PREFIX) and struct.unpack_from("<i", p, 4)[0] == 5:
                    hits.append(g)
    if len(hits) != 1:
        raise SystemExit(f"ожидалась ровно одна группа с переходом в игру, найдено {len(hits)}")
    g = hits[0]
    jump_val = None
    for a in g.actions:
        for p in a.params:
            if p.raw.startswith(JUMP_PARAM_PREFIX):
                jump_val = struct.unpack_from("<i", p.raw, 4)[0]
    return g, jump_val, groups


def already_patched(events: bytes) -> bool:
    """Мод считается установленным, если в какой-то группе есть act88
    со статическими строками "GuardName_1" и "Cell Key" (наш паттерн)."""
    info, groups = TE.parse_events(events)
    for g in groups:
        strs = []
        for a in g.actions:
            if a.num == ACT_HEADER[1]:
                for p in a.params:
                    strs += iter_strings(p.raw)
        if "GuardName_1" in strs and any(n in strs for n in DEFAULT_NAMES[:1]):
            return True
    return False


def splice_group(events: bytes, g, new_group_raw: bytes) -> bytes:
    """Заменить группу g новой (другой длины), поправив ERes/ERev."""
    erev_off = events.rindex(b"ERev", 0, g.start)
    delta = len(new_group_raw) - (g.end - g.start)
    out = bytearray(events[:g.start] + new_group_raw + events[g.end:])
    struct.pack_into("<i", out, erev_off + 4,
                     struct.unpack_from("<i", events, erev_off + 4)[0] + delta)
    eres_off = events.rindex(b"ERes", 0, erev_off)
    struct.pack_into("<i", out, eres_off + 4,
                     struct.unpack_from("<i", events, eres_off + 4)[0] + delta)
    return bytes(out)


def rebuild_exe(d, stream_off, chunks, frame_idx, subs, new_events, cipher):
    out_subs = []
    for scid, sflag, sraw in subs:
        if scid == 13117:
            raw = encode_sub3(scid, new_events, cipher)
            out_subs.append(struct.pack("<hhi", scid, sflag, len(raw)) + raw)
        else:
            out_subs.append(struct.pack("<hhi", scid, sflag, len(sraw)) + sraw)
    frame_body = b"".join(out_subs)
    out = [d[:stream_off]]
    for i, (cid, flag, data) in enumerate(chunks):
        if i == frame_idx:
            data = frame_body
        out.append(struct.pack("<hhi", cid, flag, len(data)) + data)
    return b"".join(out)


# ---------------------------------------------------------------- самопроверка кодировки
def selftest_act88(groups) -> int:
    """Собираем act88(name, value) нашими руками и требуем байт-в-байт совпадения
    с хотя бы одним оригинальным act88 из той же сборки игры (чистые литералы).
    Возвращаем obj_info объекта «Named variable object»."""
    last_obj_info = None
    for g in groups:
        for a in g.actions:
            if (a.obj_type, a.num) != ACT_HEADER or len(a.params) != 2:
                continue
            last_obj_info = a.obj_info
            s0 = iter_strings(a.params[0].raw)
            s1 = iter_strings(a.params[1].raw)
            if len(s0) != 1 or len(s1) != 1:
                continue
            # чистые литералы: длина параметра = 16 + (len+1)*2
            if (len(a.params[0].raw) != 16 + (len(s0[0]) + 1) * 2 or
                    len(a.params[1].raw) != 16 + (len(s1[0]) + 1) * 2):
                continue
            mine = act_set_var_string(s0[0], s1[0], a.obj_info)
            if mine == a.raw:
                print(f"самопроверка act88: совпадение с игровым донором "
                      f"(группа #{g.index}, «{s0[0]}»=«{s1[0]}») ✓")
                return a.obj_info
    return last_obj_info


# ---------------------------------------------------------------- команды
def cmd_check(args):
    d, so, chunks, cipher, gname = load_exe(args.exe)
    print(f"файл: {args.exe} ({len(d)} байт)")
    print(f"продукт: {gname or 'не опознан'}")
    if cipher is None:
        print("!! не удалось собрать ключ шифрования — сборка не поддерживается")
        return 2
    fi, subs, events = decode_frame_events(chunks, cipher, args.frame)
    info, groups = TE.parse_events(events)
    print(f"фрейм: {args.frame} — событий {len(events)} байт, групп {len(groups)}, "
          f"потреблено {info['consumed']}/{info['total']}")
    print("\nпривязка ключей (одинакова во всех тюрьмах, фрейм game, groupe assign_keys):")
    for n, item, label, color in KEY_TABLE:
        print(f"  офицер #{n}: item {item:>2} = {label:<13} ({color} дверь)")
    try:
        g, jump, _ = find_start_group(events)
        print(f"\nгруппа «start»: #{g.index} (офс {g.start}, действий {len(g.actions)}), "
              f"переход в кадр {jump}")
    except SystemExit as e:
        print(f"\n!! {e}")
        return 3
    patched = already_patched(events)
    print(f"мод Guard Key Names: {'УЖЕ УСТАНОВЛЕН ✓' if patched else 'не установлен'}")
    return 0


def cmd_patch(args):
    names = DEFAULT_NAMES
    if args.names:
        names = [s.strip() for s in args.names.split(",")]
        if len(names) != 5 or any(not n for n in names):
            raise SystemExit("--names требует ровно 5 непустых имён через запятую")
        over = [n for n in names if len(n) > 12]
        if over:
            print(f"предупреждение: имена длиннее 12 символов могут не влезть в UI: {over}")

    d, so, chunks, cipher, gname = load_exe(args.exe)
    if cipher is None:
        raise SystemExit("не удалось собрать ключ шифрования — сборка не поддерживается")
    print(f"exe: {args.exe} ({len(d)} байт, «{gname}»)")

    fi, subs, events = decode_frame_events(chunks, cipher, args.frame)
    g, jump, groups = find_start_group(events)
    print(f"группа «start» в {args.frame}: #{g.index}, переход в кадр {jump}, "
          f"условий {len(g.conditions)}, действий {len(g.actions)}")

    if already_patched(events):
        print("мод уже установлен в этом exe — патчить не нужно")
        return 4

    # самопроверка: собранная нами act88 должна байт-в-байт совпасть с игровым донором
    obj_info = selftest_act88(groups)
    if obj_info is None:
        raise SystemExit("не найден объект «Named variable object» для привязки")
    probe = act_set_var_string("GuardName_probe", "probe", obj_info)
    if struct.unpack_from("<H", probe, 0)[0] != len(probe):
        raise SystemExit("внутренняя ошибка: неверный размер действия")

    # новая группа = старые условия + [действия: всё кроме перехода] + 5 имён + переход
    jump_raw = g.actions[-1].raw
    if not any(p.startswith(JUMP_PARAM_PREFIX) for p in
               [pp.raw for pp in g.actions[-1].params]):
        raise SystemExit("последнее действие группы — не переход кадра, прерываюсь")
    new_acts = [a.raw for a in g.actions[:-1]]
    for idx, label in enumerate(names, start=1):
        new_acts.append(act_set_var_string(f"GuardName_{idx}", label, obj_info))
    new_acts.append(jump_raw)

    new_group = group_bytes(g.flags, g.line, g.is_restricted, g.restrict_cpt,
                            [c.raw for c in g.conditions], new_acts)
    # контроль: если не добавлять действия, группа должна собраться байт-в-байт как была
    orig_rebuilt = group_bytes(g.flags, g.line, g.is_restricted, g.restrict_cpt,
                               [c.raw for c in g.conditions], [a.raw for a in g.actions])
    if orig_rebuilt != events[g.start:g.end]:
        raise SystemExit("group() не воспроизводит исходную группу — сборка не поддерживается")

    new_events = splice_group(events, g, new_group)
    out = rebuild_exe(d, so, chunks, fi, subs, new_events, cipher)
    open(args.out, "wb").write(out)
    print(f"\nзаписано: {args.out} ({len(out)} байт, {len(out) - len(d):+d})")

    # ---- глубокая проверка результата ----
    d2, so2, chunks2, cipher2, _ = load_exe(args.out)
    fi2, subs2, events2 = decode_frame_events(chunks2, cipher2, args.frame)
    info2, groups2 = TE.parse_events(events2)
    ok = True

    # 1) события парсятся целиком
    if info2["consumed"] != info2["total"]:
        ok = False; print("!! события не потреблены полностью")
    # 2) число групп не изменилось
    if len(groups2) != len(groups):
        ok = False; print(f"!! число групп изменилось: {len(groups)} -> {len(groups2)}")
    # 3) целевые действия на месте
    g2, jump2, _ = find_start_group(events2)
    found = {}
    for a in g2.actions:
        if a.num == ACT_HEADER[1] and len(a.params) == 2:
            s0 = iter_strings(a.params[0].raw)
            s1 = iter_strings(a.params[1].raw)
            if s0 and s0[0].startswith("GuardName_"):
                found[s0[0]] = s1[0] if s1 else None
    expect = {f"GuardName_{i}": names[i - 1] for i in range(1, 6)}
    if found != expect:
        ok = False; print(f"!! имена не совпали: {found} vs {expect}")
    # 4) все остальные группы побайтово идентичны
    if events2 == events:
        ok = False  # файл обязан измениться
    # всё до целевой группы идентично (кроме полей размера ERes/ERev сдвига)
    ho1 = events.rindex(b"ERev", 0, g.start) + 8
    ho2 = events2.rindex(b"ERev", 0, g2.start) + 8
    head_same = events[ho1:g.start] == events2[ho2:g2.start]
    tail_same = events[g.end:] == events2[g2.end:]
    if not (head_same and tail_same):
        ok = False; print("!! затронуты соседние группы")
    # 5) весь остальной exe побайтово идентичен, кроме целевого фрейма
    for i, (c1, c2) in enumerate(zip(chunks, chunks2)):
        if i == fi:
            continue
        if c1 != c2:
            ok = False; print(f"!! изменён чанк #{i} (id={c1[0]})")
    # 6) «игровой» фрейм с логикой не тронут: внутри целевого — только чанк событий
    for (scid, sf, sraw), (scid2, sf2, sraw2) in zip(subs, subs2):
        if (scid, sf, sraw) != (scid2, sf2, sraw2) and scid != 13117:
            ok = False; print(f"!! изменён подчанк {scid}")

    print("\nпроверка результата:")
    print(f"  группа «start» теперь с {len(g2.actions)} действиями (было {len(g.actions)})")
    print(f"  все остальные группы и фреймы байт-в-байт идентичны: {'да' if ok else 'НЕТ'}")
    for n, (_, item, label, color) in zip(range(1, 6), KEY_TABLE):
        print(f"  офицер #{n} -> Officer {names[n-1]}")
    if not ok:
        print("\n!! проверка НЕ пройдена, файл лучше не использовать")
        return 1
    print("\nготово ✓  (скопируй файл поверх TheEscapists.exe в папке игры, сделав бэкап)")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Guard Key Names mod: офицеры названы по цвету ключа (The Escapists 1)")
    ap.add_argument("cmd", choices=["check", "patch"])
    ap.add_argument("exe", help="путь к TheEscapists.exe (или его дампу .exe.txt)")
    ap.add_argument("-o", "--out", help="куда записать пропатченный exe (для patch)")
    ap.add_argument("--frame", default="npc_rename", help="фрейм генерации имён (по умолчанию npc_rename)")
    ap.add_argument("--names", help="свои 5 имён через запятую (по умолчанию имена ключей)")
    args = ap.parse_args()
    if args.cmd == "check":
        return cmd_check(args)
    if not args.out:
        ap.error("для patch нужен -o/--out")
    return cmd_patch(args)


if __name__ == "__main__":
    sys.exit(main())
