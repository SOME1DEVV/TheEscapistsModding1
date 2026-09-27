#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
te1_events.py — парсер байткода событий Clickteam Fusion 2.5 (runtime, build 288).

Порт формата из CTFAK2.0 (Events.cs + параметры/выражения), проверен на The Escapists.
Формат чанка Frame Events (13117):

  "ER>>" i16 maxObjects i16 maxObjectInfo i16 players i16[17] condCounts
         i16 qualifierCount (u16 objectInfo + i16 type)*qualifierCount
  "ERes" i32 (число групп)
  "ERev" i32 size | группы до конца блока
  ("ERop" i32 flags)  — может отсутствовать
  "<<ER"

Группа:  i16 size(ОТРИЦАТЕЛЬНЫЙ, размер всего после этого поля)
         u8 ncond u8 nact u16 flags i16 line i32 isRestricted i32 restrictCpt
         условия[ncond] действия[nact]
Условие: u16 size i16 objType i16 num u16 objInfo i16 objInfoList
         i8 flags i8 otherFlags u8 nparams u8 defType i16 identifier, параметры
Действие: u16 size i16 objType i16 num u16 objInfo i16 objInfoList
         i8 flags i8 otherFlags u8 nparams u8 defType, параметры
Параметр: i16 size i16 code, загрузчик по code, затем seek на начало+size

Использование:
  python3 te1_events.py dump exe/TheEscapists_eur.exe.txt --frame game -o work/game_events.txt
  python3 te1_events.py dump work/game_events.bin --names work/objects.json -o work/game_events.txt
  python3 te1_events.py grep exe/TheEscapists_eur.exe.txt --frame game --object 501
"""
from __future__ import annotations
import argparse, json, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "toolkit"))


# --------------------------------------------------------------------------- ридер
class R:
    __slots__ = ("b", "p")

    def __init__(self, b: bytes, p: int = 0):
        self.b = b
        self.p = p

    def u8(self):  v = self.b[self.p]; self.p += 1; return v
    def i8(self):  v = struct.unpack_from("<b", self.b, self.p)[0]; self.p += 1; return v
    def u16(self): v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v
    def i16(self): v = struct.unpack_from("<h", self.b, self.p)[0]; self.p += 2; return v
    def i32(self): v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v
    def u32(self): v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v
    def f32(self): v = struct.unpack_from("<f", self.b, self.p)[0]; self.p += 4; return v
    def f64(self): v = struct.unpack_from("<d", self.b, self.p)[0]; self.p += 8; return v
    def raw(self, n): v = self.b[self.p:self.p + n]; self.p += n; return v

    def ascii0(self):
        e = self.b.index(0, self.p)
        s = self.b[self.p:e].decode("latin1", "replace")
        self.p = e + 1
        return s

    def wide0(self):
        out = []
        while self.p + 2 <= len(self.b):
            c = self.u16()
            if c == 0:
                break
            out.append(chr(c))
        return "".join(out)


# --------------------------------------------------------------------------- словари
OBJ_TYPES = {-7: "player", -6: "keyboard", -5: "create", -4: "timer", -3: "game",
             -2: "speaker", -1: "system", 0: "quick_backdrop", 1: "backdrop",
             2: "active", 3: "text", 4: "question", 5: "score", 6: "lives",
             7: "counter", 8: "rtf", 9: "subapp", 32: "extension"}

# системные условия (objType=-1) — уверенная часть
COND_SYS = {-1: "начало фрейма", -2: "начало уровня", -8: "сравнить глоб.переменную",
            -24: "alterable-флаг ВЫКЛ", -25: "alterable-флаг ВКЛ",
            -26: "группа активна?", -27: "сравнить alterable value",
            -43: "сравнить глоб.строку", -41: "несколько переменных (упаковано)"}
ACT_SYS = {1: "создать объект?", 2: "позиция", 3: "установить значение",
           4: "прибавить к значению", 5: "вычесть из значения", 43: "установить глоб.строку",
           27: "set global value(вар.)", 35: "add global value(вар.)", 31: "sub global value(вар.)"}

# сравнения в ExpressionParameter
CMP = {0: "?0", 1: ">=", 2: "<=", 3: "<", 4: ">", 5: "=="}


# --------------------------------------------------------------------------- параметры
class Param:
    __slots__ = ("code", "size", "data", "raw")

    def __init__(self, code, size, data):
        self.code, self.size, self.data = code, size, data
        self.raw = None  # сырые байты записи (size+code+loader) — для клонирования


def parse_parameter(r: R):
    start = r.p
    size = r.i16()
    code = r.i16()
    # грузим «сырые» данные загрузчика; seek как в CTFAK
    loader_start = r.p
    p = Param(code, size, None)
    try:
        d = _load_param(code, r)
    except Exception as e:
        d = {"kind": "PARSE_ERROR", "err": str(e)}
    p.data = d
    if size > 0:
        r.p = start + size
    else:
        r.p = loader_start
    if r.p < loader_start:
        r.p = loader_start
    p.raw = r.b[start:r.p]
    return p


def _load_param(code, r: R):
    if code == 1:  # ParamObject
        return {"kind": "object", "obj_info_list": r.i16(), "obj_info": r.u16(), "obj_type": r.i16()}
    if code in (2, 42):  # Time
        return {"kind": "time", "timer": r.i32(), "loops": r.i32(), "cmp": r.i16()}
    if code in (3, 4, 10, 11, 12, 17, 26, 31, 43, 57, 58, 60, 61):
        return {"kind": "short", "value": r.i16()}
    if code in (5, 25, 29, 34, 48, 56, 67, 70):
        return {"kind": "int", "value": r.i32()}
    if code in (6, 7, 35, 36):  # Sample
        return {"kind": "sample", "handle": r.i16(), "flags": r.u16(), "name": r.wide0()}
    if code in (9, 21):  # Create
        pos = _load_position(r)
        return {"kind": "create", "position": pos, "obj_instance": r.u16(), "obj_info": r.u16()}
    if code == 13:  # Every
        return {"kind": "every", "delay": r.i32(), "counter": r.i32()}
    if code in (14, 44):
        return {"kind": "key", "key": r.u16()}
    if code in (15, 22, 23, 27, 28, 45, 46, 52, 53, 54, 59, 62):
        return {"kind": "expr", "cmp": r.i16(), "items": _load_expressions(r)}
    if code == 16:
        return {"kind": "position", "pos": _load_position(r)}
    if code == 18:  # Shoot
        pos = _load_position(r)
        r.u16(); oi = r.u16(); r.i32()
        return {"kind": "shoot", "pos": pos, "obj_instance": None, "obj_info": oi, "speed": r.i16()}
    if code == 19:
        return {"kind": "zone", "x1": r.i16(), "y1": r.i16(), "x2": r.i16(), "y2": r.i16()}
    if code == 24:
        return {"kind": "colour", "rgba": r.raw(4).hex()}
    if code == 40:
        return {"kind": "filename", "value": r.wide0()}
    if code == 50:
        return {"kind": "alterable", "index": r.i16()}
    if code == 32:
        return {"kind": "click", "button": r.u8(), "dbl": r.u8()}
    if code == 33:
        return {"kind": "program", "flags": r.i16(), "filename": r.ascii0(), "cmd": r.ascii0()}
    if code == 55:  # Extension
        size = r.i16(); etype = r.i16(); ecode = r.i16()
        return {"kind": "ext", "ext_type": etype, "ext_code": ecode, "data": r.raw(max(size, 0)).hex()}
    if code == 38:  # Group
        flags = r.u16(); gid = r.u16(); name = r.wide0()
        rest = 190 - len(name) * 2
        r.raw(max(rest, 0))
        return {"kind": "group", "flags": flags, "id": gid, "name": name}
    if code == 39:
        return {"kind": "group_ptr", "pointer": r.i32(), "id": r.i16()}
    if code == 49:
        return {"kind": "global_value", "index": r.i16()}
    if code in (41, 64):
        return {"kind": "string", "value": r.ascii0()}
    if code in (47, 51):
        return {"kind": "two_shorts", "a": r.i16(), "b": r.i16()}
    if code == 68:  # MultipleVariables
        flags = r.i32(); masks = r.i32(); values_flags = r.i32()
        n = 0; mask = 1
        while n < 4 and (flags & mask):
            n += 1; mask <<= 4
        vals = []
        for _ in range(n):
            is_double = (flags & 4) != 0
            idx = r.i32(); op = r.i32()
            if is_double:
                val = r.f64()
            else:
                val = r.i32(); r.i32()
            vals.append({"index": idx, "op": op, "value": val, "double": is_double})
        return {"kind": "multivar", "flags": flags, "values": vals}
    if code == 69:  # ChildEvent
        cnt = r.i32()
        ois = [r.i16() for _ in range(cnt * 2)]
        r.i32()
        return {"kind": "child", "count": cnt, "ois": ois}
    return {"kind": f"unknown_{code}", "raw": r.raw(0) and ""}


def _load_position(r: R):
    return {"parent_obj_info": r.u16(), "flags": r.i16(), "x": r.i16(), "y": r.i16(),
            "slope": r.i16(), "angle": r.i16(), "dir": r.i32(),
            "parent_type": r.i16(), "parent_list": r.i16(), "layer": r.i16()}


def _load_expressions(r: R):
    items = []
    while True:
        start = r.p
        otype = r.i16()
        num = r.i16()
        if otype == 0 and num == 0:
            break
        size = r.i16()
        it = {"obj_type": otype, "num": num}
        try:
            if otype == -1:  # system
                if num == 0:
                    it["value"] = r.i32()
                elif num == 3:
                    it["string"] = r.wide0()
                elif num == 23:
                    it["value"] = r.f64(); it["f"] = r.f32()
                elif num in (24, 50):
                    r.i32(); it["global_index"] = r.i32()
                elif otype >= 2 or otype == -7:
                    it["obj_info"] = r.u16(); it["obj_info_list"] = r.i16()
                    if num in (16, 19):
                        it["ext_val"] = r.i16()
                    else:
                        r.i32()
            elif otype >= 2 or otype == -7:
                it["obj_info"] = r.u16(); it["obj_info_list"] = r.i16()
                if num in (16, 19):
                    it["ext_val"] = r.i16()
            # seek на конец выражения
            if size > 0:
                r.p = start + 6 + size - 6 + 6  # start+objType+num+size... фактически start+size
                r.p = start + size if size > start else start
                r.p = start + size
        except Exception as e:
            it["err"] = str(e)
        items.append(it)
    return items


# --------------------------------------------------------------------------- события
def _fix_condition(num, obj_type):
    if num in (-42, -43) and obj_type != -1:
        num = -27
    if obj_type == -1 and num in (-28, -29, -30, -31, -32, -33):
        num = -8
    return num


def _fix_action(num, obj_type):
    if obj_type == -1:
        if num in (27, 28, 29, 30):
            num = 3
        if num in (35, 32, 33, 34):
            num = 4
        if num in (31, 36, 37, 38):
            num = 5
    return num


class Cond:
    __slots__ = ("obj_type", "num", "obj_info", "obj_info_list", "flags", "other",
                 "params", "def_type", "identifier", "raw")


class Act:
    __slots__ = ("obj_type", "num", "obj_info", "obj_info_list", "flags", "other",
                 "params", "def_type", "raw")


def parse_condition(r: R):
    c = Cond()
    start = r.p
    r.u16()  # size
    c.obj_type = r.i16()
    c.num = r.i16()
    c.obj_info = r.u16()
    c.obj_info_list = r.i16()
    c.flags = r.i8()
    c.other = r.i8()
    npar = r.u8()
    c.def_type = r.u8()
    c.identifier = r.i16()
    c.num = _fix_condition(c.num, c.obj_type)
    c.params = [parse_parameter(r) for _ in range(npar)]
    c.raw = r.b[start:r.p]
    return c


def parse_action(r: R):
    a = Act()
    start = r.p
    r.u16()  # size
    a.obj_type = r.i16()
    a.num = r.i16()
    a.obj_info = r.u16()
    a.obj_info_list = r.i16()
    a.flags = r.i8()
    a.other = r.i8()
    npar = r.u8()
    a.def_type = r.u8()
    a.num = _fix_action(a.num, a.obj_type)
    a.params = [parse_parameter(r) for _ in range(npar)]
    a.raw = r.b[start:r.p]
    return a


class Group:
    __slots__ = ("index", "flags", "line", "is_restricted", "restrict_cpt",
                 "conditions", "actions", "start", "end")


def parse_events(data: bytes, verbose=False):
    r = R(data)
    magic = r.raw(4)
    if magic != b"ER>>":
        raise SystemExit(f"ожидается 'ER>>', получено {magic!r}")
    max_objects = r.i16(); max_obj_info = r.i16(); players = r.i16()
    cond_counts = [r.i16() for _ in range(17)]
    qual_count = r.i16()
    quals = [{"obj_info": r.u16(), "type": r.i16()} for _ in range(qual_count)]
    info = dict(max_objects=max_objects, max_obj_info=max_obj_info, players=players,
                cond_counts=cond_counts, qualifiers=quals)
    groups = []
    end_pos = len(data)
    while r.p + 4 <= end_pos:
        tag = r.raw(4)
        if tag == b"ERes":
            info["eres_value"] = r.i32()
        elif tag == b"ERev":
            size = r.i32()
            gend = r.p + size
            while r.p < gend:
                g = _parse_group(r, len(groups))
                groups.append(g)
                if r.p > gend:
                    if verbose:
                        print(f"!! группа {g.index} перекрыла конец блока ({r.p} > {gend})", file=sys.stderr)
                    break
        elif tag == b"ERop":
            info["options"] = r.i32()
        elif tag == b"<<ER":
            break
        else:
            if verbose:
                print(f"!! неизвестный тег {tag!r} на {r.p-4}", file=sys.stderr)
            break
    info["consumed"] = r.p
    info["total"] = len(data)
    return info, groups


def _parse_group(r: R, index: int) -> Group:
    g = Group()
    g.index = index
    start = r.p
    g.start = start
    size = r.i16()
    ncond = r.u8()
    nact = r.u8()
    g.flags = r.u16()
    g.line = r.i16()
    g.is_restricted = r.i32()
    g.restrict_cpt = r.i32()
    g.conditions = [parse_condition(r) for _ in range(ncond)]
    g.actions = [parse_action(r) for _ in range(nact)]
    g.end = start - size if size < 0 else r.p
    if g.end != r.p:
        # мягкая коррекция: группы сливаются только при ошибке парсинга
        if verbose_mode:
            print(f"!! группа {index}: ожидался конец {g.end}, фактически {r.p} (сдвиг {r.p-g.end})",
                  file=sys.stderr)
        r.p = g.end
    return g


verbose_mode = False


# --------------------------------------------------------------------------- рендер
def obj_name(objects, oi):
    if oi in objects:
        o = objects[oi]
        return f"{o['name']}[{oi}]"
    return f"?[{oi}]"


def render_param(p: Param, objects, ext_names=None) -> str:
    d = p.data or {}
    k = d.get("kind", "?")
    if k == "object":
        return obj_name(objects, d["obj_info"])
    if k == "short":
        return str(d["value"])
    if k == "int":
        return str(d["value"])
    if k == "time":
        return f"time={d['timer']}мс loops={d['loops']} cmp={d['cmp']}"
    if k == "every":
        return f"каждые {d['delay']}мс"
    if k == "key":
        return f"клавиша {d['key']}"
    if k == "click":
        return f"клик btn={d['button']} dbl={d['dbl']}"
    if k == "zone":
        return f"зона ({d['x1']},{d['y1']})-({d['x2']},{d['y2']})"
    if k == "colour":
        return f"цвет #{d['rgba']}"
    if k == "string":
        return f'"{d["value"]}"'
    if k == "filename":
        return f'"{d["value"]}"'
    if k == "sample":
        return f"звук '{d['name']}'"
    if k == "create":
        pos = d["position"]
        return f"создать {obj_name(objects, d['obj_info'])} на ({pos['x']},{pos['y']})"
    if k == "position":
        pos = d["pos"]
        par = obj_name(objects, pos["parent_obj_info"]) if pos["parent_obj_info"] else ""
        return f"позиция({par} {pos['x']},{pos['y']} слой={pos['layer']})"
    if k == "shoot":
        return f"выстрел {obj_name(objects, d['obj_info'])} скорость={d['speed']}"
    if k == "alterable":
        return f"alterable[{d['index']}]"
    if k == "global_value":
        return f"gv[{d['index']}]"
    if k == "group":
        return f"группа '{d['name']}'(id={d['id']})"
    if k == "group_ptr":
        return f"группа→{d['id']}"
    if k == "two_shorts":
        return f"{d['a']},{d['b']}"
    if k == "expr":
        items = render_exprs(d.get("items", []), objects)
        return f"{items} {CMP.get(d['cmp'], '?') + str(d['cmp'])}".strip()
    if k == "multivar":
        vs = "; ".join(f"av[{v['index']}]{CMP.get(v['op'],v['op'])}{v['value']}" for v in d["values"])
        return f"мульти: {vs}"
    if k == "child":
        return f"child({d['count']}): {d['ois']}"
    if k == "ext":
        return f"ext[type={d['ext_type']} code={d['ext_code']}]"
    if k == "time_" :
        return "time"
    if k.startswith("unknown"):
        return k
    return k


def render_exprs(items, objects) -> str:
    out = []
    for it in items:
        t = it.get("obj_type")
        if t == -1:
            if it["num"] == 0:
                out.append(str(it.get("value")))
            elif it["num"] == 3:
                out.append(f'"{it.get("string")}"')
            elif it["num"] == 23:
                out.append(str(it.get("value")))
            elif it["num"] in (24, 50):
                out.append(f"gv[{it.get('global_index')}]")
            else:
                out.append(f"sys{it['num']}")
        elif t == -7:
            out.append(f"player{it['num']}")
        elif t is not None and t >= 2:
            nm = obj_name(objects, it.get("obj_info", -1))
            out.append(f"{nm}:{it['num']}")
        else:
            out.append(f"e{t}/{it['num']}")
    return " ".join(out)


def render_cond(c: Cond, objects) -> str:
    if c.obj_type == -1:
        base = COND_SYS.get(c.num, f"sys_cond{c.num}")
    else:
        nm = obj_name(objects, c.obj_info) if c.obj_info else OBJ_TYPES.get(c.obj_type, str(c.obj_type))
        base = f"{nm}:cond{c.num}"
    ps = ", ".join(render_param(p, objects) for p in c.params)
    return f"{base}({ps})" if ps else base


def render_act(a: Act, objects) -> str:
    if a.obj_type == -1:
        base = ACT_SYS.get(a.num, f"sys_act{a.num}")
    else:
        nm = obj_name(objects, a.obj_info) if a.obj_info else OBJ_TYPES.get(a.obj_type, str(a.obj_type))
        base = f"{nm}:act{a.num}"
    ps = ", ".join(render_param(p, objects) for p in a.params)
    return f"{base}({ps})" if ps else base


def render_group(g: Group, objects) -> str:
    lines = [f"━━ группа #{g.index} (офс {g.start}, line={g.line}, flags={g.flags:#06x})"]
    for c in g.conditions:
        lines.append(f"  ЕСЛИ  {render_cond(c, objects)}")
    for a in g.actions:
        lines.append(f"  ТО    {render_act(a, objects)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI
def load_objects_from_exe(exe_path):
    from te1_icons import parse_objects, read_chunks, find_stream, zdec, universal
    from te_crypto import Cipher
    d = open(exe_path, "rb").read()
    chunks = read_chunks(d, find_stream(d))
    by = {}
    for c in chunks:
        by.setdefault(c[0], []).append(c)
    name = universal(zdec(by[8740][0][2])).strip("\0")
    cop = universal(zdec(by[8763][0][2])).strip("\0")
    ed = universal(zdec(by[8750][0][2])).strip("\0")
    cipher = Cipher(name, cop, ed)
    objs = parse_objects(chunks, cipher)  # берёт сам чанк FrameItems из списка
    return {o["handle"]: o for o in objs if o}


def get_frame_events(exe_path, frame):
    from te1_frames import load_exe, find_frame
    chunks, cipher = load_exe(exe_path)
    fr = find_frame(chunks, cipher, frame)
    return fr["plain"][13117]


def main():
    global verbose_mode
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["dump", "grep", "info"])
    ap.add_argument("src", help="exe (.txt) или уже снятый дамп событий .bin")
    ap.add_argument("--frame", default="game")
    ap.add_argument("--object", type=int, action="append", help="фильтр: хендл объекта")
    ap.add_argument("--text", help="подстрока в имени объекта для фильтра")
    ap.add_argument("--names", help="json со словарём хендл→имя (иначе из exe)")
    ap.add_argument("-o", "--out")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    verbose_mode = a.verbose

    objects = {}
    if a.src.lower().endswith(".exe.txt") or a.src.lower().endswith(".exe"):
        events = get_frame_events(a.src, a.frame)
        objects = load_objects_from_exe(a.src)
    else:
        events = open(a.src, "rb").read()
        if a.names:
            raw = json.load(open(a.names))
            objects = {int(k): v for k, v in raw.items()}
    info, groups = parse_events(events, verbose=a.verbose)
    print(f"объектов в карте: {len(objects)}; групп событий: {len(groups)}; "
          f"потреблено {info['consumed']}/{info['total']} байт", file=sys.stderr)

    if a.cmd == "info":
        print(json.dumps({k: v for k, v in info.items() if k != "qualifiers"},
                         ensure_ascii=False, indent=1))
        print("квалификаторы:", len(info["qualifiers"]))
        return

    selected = groups
    if a.object or a.text:
        def match(g):
            for c in g.conditions + g.actions:
                oi = getattr(c, "obj_info", 0)
                if a.object and oi in a.object:
                    return True
                if a.text:
                    nm = objects.get(oi, {}).get("name", "") if isinstance(objects.get(oi), dict) else ""
                    if a.text.lower() in str(nm).lower():
                        return True
            return False
        selected = [g for g in groups if match(g)]

    text = "\n\n".join(render_group(g, objects) for g in selected)
    header = (f"# события фрейма {a.frame}: групп всего {len(groups)}, выбрано {len(selected)}\n"
              f"# объектов в карте: {len(objects)}\n\n")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(header + text + "\n")
        print("->", a.out, file=sys.stderr)
    else:
        print(header + text)


if __name__ == "__main__":
    main()
