"""Script extraction for GDScript (.gd) and C# (.cs).

Regex + indentation based rather than a full parser: fast, no native deps, and
precise enough for the facts nodemap needs (declarations, node paths, signal
wiring, resource loads, input actions, groups). Every fact carries its line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScriptModel:
    res: str
    lang: str  # "gdscript" | "csharp"
    lines: int = 0
    class_name: str = ""
    class_line: int = 0
    extends: str = ""  # identifier, "res://..." or "" (implicit RefCounted)
    extends_line: int = 0
    is_tool: bool = False
    doc: str = ""
    funcs: dict[str, dict] = field(default_factory=dict)  # name -> {line, params, static}
    signals: dict[str, dict] = field(default_factory=dict)  # name -> {line, params}
    exports: list[dict] = field(default_factory=list)  # {name, type, line}
    var_types: dict[str, tuple[str, str]] = field(default_factory=dict)  # var -> ("class"|"path", value)
    node_refs: list[dict] = field(default_factory=list)  # {path, optional, line, func}
    calls: list[dict] = field(default_factory=list)  # {recv, name, line, func, lits}
    connects: list[dict] = field(default_factory=list)  # {recv, signal, handler, hrecv, lambda, line, func}
    emits: list[dict] = field(default_factory=list)  # {recv, signal, line, func}
    res_refs: list[dict] = field(default_factory=list)  # {path, kind, line, func}
    actions: list[dict] = field(default_factory=list)  # {name, line, func}
    action_decls: list[dict] = field(default_factory=list)  # {name, line}
    action_registrars: list[tuple[str, int]] = field(default_factory=list)  # (func, param idx)
    groups: list[dict] = field(default_factory=list)  # {name, op, line}
    idents: dict[str, int] = field(default_factory=dict)  # identifier -> first line
    legacy: list[dict] = field(default_factory=list)  # {line, msg}
    builds_nodes: bool = False  # adds children in code (weakens node-path checks)
    runtime_names: set[str] = field(default_factory=set)  # `node.name = "X"` in code
    local_names: set[str] = field(default_factory=set)  # every var / parameter name


# ---------------------------------------------------------------- shared utils

def _strip_comment(line: str, marker: str = "#") -> str:
    in_str = None
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif line.startswith(marker, i):
            return line[:i]
        i += 1
    return line


_STRING_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')


def _blank_strings(code: str) -> str:
    return _STRING_RE.sub(lambda m: '""', code)


def _depth(code: str) -> int:
    d = 0
    for c in _blank_strings(code):
        if c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
    return d


def _logical_lines(text: str, comment: str) -> list[tuple[int, int, str]]:
    """Yield (lineno, indent, code) with comments stripped and bracketed /
    backslash-continued lines joined."""
    out = []
    raw = text.splitlines()
    i = 0
    while i < len(raw):
        start = i
        line = raw[i]
        code = _strip_comment(line, comment).rstrip()
        i += 1
        if not code.strip():
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        indent = len(line[:indent].replace("\t", "    "))
        while (code.endswith("\\") or _depth(code) > 0) and i < len(raw):
            code = code.rstrip("\\") + " " + _strip_comment(raw[i], comment).strip()
            i += 1
        out.append((start + 1, indent, code.strip()))
    return out


def _args(code: str, open_idx: int) -> list[str]:
    """Split the argument list starting at code[open_idx] == '('."""
    depth, buf, out, in_str = 0, [], [], None
    for c in code[open_idx:]:
        if in_str:
            buf.append(c)
            if c == in_str:
                in_str = None
            continue
        if c in "\"'":
            in_str = c
            buf.append(c)
        elif c in "([{":
            depth += 1
            if depth > 1:
                buf.append(c)
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                out.append("".join(buf).strip())
                return [a for a in out if a]
            buf.append(c)
        elif c == "," and depth == 1:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    out.append("".join(buf).strip())
    return [a for a in out if a]


_LIT_RE = re.compile(r'^[&^]?"((?:[^"\\]|\\.)*)"$|^[&^]?\'((?:[^\'\\]|\\.)*)\'$')


def _lit(arg: str) -> str | None:
    m = _LIT_RE.match(arg.strip())
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


# ---------------------------------------------------------------- GDScript

_G_FUNC = re.compile(r"^(static\s+)?func\s+([A-Za-z_]\w*)\s*\(([^)]*)\)")
_G_CLASS = re.compile(r"^class\s+([A-Za-z_]\w*)")
_G_SIGNAL = re.compile(r"^signal\s+([A-Za-z_]\w*)\s*(\(([^)]*)\))?")
_G_CLASS_NAME = re.compile(r"^class_name\s+([A-Za-z_]\w*)(?:\s+extends\s+(\S+))?")
_G_EXTENDS = re.compile(r"^extends\s+(\"[^\"]+\"|'[^']+'|[A-Za-z_][\w.]*)")
_G_EXPORT = re.compile(r"@export\w*(?:\([^)]*\))?\s+(?:@\w+\s+)*var\s+([A-Za-z_]\w*)\s*(?::\s*([\w.\[\]]+))?")
_G_VAR_TYPE = re.compile(r"\bvar\s+([A-Za-z_]\w*)\s*:\s*([A-Za-z_][\w.]*)")
_G_VAR_NEW = re.compile(r"\bvar\s+([A-Za-z_]\w*)\s*:?=\s*([A-Z]\w*)\.new\s*\(")
_G_VAR_AS = re.compile(r"\bvar\s+([A-Za-z_]\w*)\s*:?=.*\bas\s+([A-Z]\w*)\s*$")
_G_VAR_PATH = re.compile(r"\bvar\s+([A-Za-z_]\w*)\s*(?::\s*[\w.]+)?\s*:?=\s*(\$\"[^\"]+\"|\$[\w/%.]*\w|%[A-Za-z_]\w*|get_node(?:_or_null)?\(\s*[\^&]?\"[^\"]+\"\s*\))")
_G_RUNTIME_NAME = re.compile(r"\bname\s*=\s*[&]?\"([^\"/]+)\"|\bset_name\s*\(\s*[&]?\"([^\"/]+)\"")
_G_VAR_NAME = re.compile(r"\b(?:var|const|for)\s+([A-Za-z_]\w*)")
_G_PARAM_TYPE = re.compile(r"([A-Za-z_]\w*)\s*:\s*([A-Z]\w*)")
_G_DOLLAR = re.compile(r"\$(\"[^\"]+\"|'[^']+'|[A-Za-z_%][\w/%]*(?:\.\.[\w/%]*)*)")
_G_UNIQUE = re.compile(r"(?:(?<=[\s(=,\[:!])|^)%([A-Za-z_]\w*)")
_G_GETNODE = re.compile(r"\b(get_node|get_node_or_null|has_node|find_child)\s*\(\s*(?:NodePath\(\s*)?[\^&]?\"([^\"]+)\"")
_G_CALL = re.compile(r"(?:(\$\"[^\"]*\"|\$[\w/%]+|%\w+|[A-Za-z_]\w*)\s*\.\s*)?([A-Za-z_]\w*)\s*\(")
_CHAIN = r"(?:\$\"[^\"]*\"|\$[\w/%]+|%\w+|[A-Za-z_]\w*)(?:\.[A-Za-z_]\w*)*"
_G_CONNECT = re.compile(r"(" + _CHAIN + r")\.connect\s*\(")
_G_CONNECT_STR = re.compile(r"(?:(" + _CHAIN + r")\.)?connect\s*\(\s*[&]?[\"']([A-Za-z_]\w*)[\"']")
_G_EMIT = re.compile(r"(" + _CHAIN + r")\.emit\s*\(")
_G_EMIT_STR = re.compile(r"(?:(" + _CHAIN + r")\.)?emit_signal\s*\(\s*[&]?[\"']([A-Za-z_]\w*)[\"']")
_G_RES = re.compile(r"(?:\b(preload|load|load_threaded_request|change_scene_to_file|ResourceLoader\.load)\s*\(\s*)?[\"']((?:res|uid)://[^\"']*)[\"']")
_ACTION_FUNCS = ("is_action_pressed|is_action_just_pressed|is_action_released|is_action_just_released|"
                 "get_action_strength|get_action_raw_strength|is_action|action_press|action_release|"
                 "get_axis|get_vector")
_G_ACTION = re.compile(r"\b(" + _ACTION_FUNCS + r")\s*\(")
_G_ADD_ACTION = re.compile(r"\bInputMap\s*\.\s*add_action\s*\(")
_GROUP_FUNCS = ("add_to_group|remove_from_group|is_in_group|get_nodes_in_group|get_first_node_in_group|"
                "call_group|call_group_flags|set_group|notify_group|has_group|get_node_count_in_group")
_G_GROUP = re.compile(r"\b(" + _GROUP_FUNCS + r")\s*\(")
_IDENT = re.compile(r"\b[A-Za-z_]\w*\b")

_GD_LEGACY = [
    (re.compile(r"^export\b"), "Godot 3 `export var` - use `@export var` in Godot 4"),
    (re.compile(r"^onready\s+var\b"), "Godot 3 `onready var` - use `@onready var` in Godot 4"),
    (re.compile(r"^tool\s*$"), "Godot 3 `tool` - use `@tool` in Godot 4"),
    (re.compile(r"\byield\s*\("), "Godot 3 `yield()` - use `await signal` in Godot 4"),
    (re.compile(r"\bsetget\b"), "Godot 3 `setget` - use `var x: set = _set_x, get = _get_x` in Godot 4"),
    (re.compile(r"\.instance\s*\(\s*\)"), "Godot 3 `PackedScene.instance()` - use `instantiate()` in Godot 4"),
    (re.compile(r"\bchange_scene\s*\("), "Godot 3 `change_scene()` - use `change_scene_to_file()` in Godot 4"),
    (re.compile(r"^(?:remote|remotesync|master|puppet|puppetsync|mastersync)\s+func\b"),
     "Godot 3 RPC keyword - use the `@rpc` annotation in Godot 4"),
    (re.compile(r"\bfuncref\s*\("), "Godot 3 `funcref()` - use `Callable` in Godot 4"),
    (re.compile(r"\brand_range\s*\("), "Godot 3 `rand_range()` - use `randf_range()` in Godot 4"),
    (re.compile(r"\b(?:to_json|parse_json)\s*\("), "Godot 3 JSON helpers - use `JSON.stringify()/JSON.parse_string()` in Godot 4"),
    (re.compile(r"\b(?:str2var|var2str|bytes2var|var2bytes)\s*\("), "Godot 3 conversion helper - renamed `str_to_var`/`var_to_str`/... in Godot 4"),
    (re.compile(r"\bPool(?:String|Int|Real|Byte|Color|Vector2|Vector3)Array\b"), "Godot 3 `Pool*Array` - use `Packed*Array` in Godot 4"),
    (re.compile(r"\bOS\.get_ticks_(?:msec|usec)\b"), "Godot 3 `OS.get_ticks_*` - use `Time.get_ticks_*` in Godot 4"),
    (re.compile(r"\bmove_and_slide\s*\(\s*[^)\s]"), "Godot 3 `move_and_slide(velocity, ...)` - Godot 4 takes no args; set `velocity` then call `move_and_slide()`"),
    (re.compile(r"\b(?:KinematicBody2D|KinematicBody|Spatial|Position2D|Position3D|YSort|Navigation2D|VisibilityNotifier2D|Particles2D|ARVROrigin|ARVRCamera)\b"),
     "Godot 3 class name - renamed in Godot 4 (e.g. KinematicBody2D->CharacterBody2D, Spatial->Node3D, Position2D->Marker2D)"),
]
_GD_LEGACY_ANY = re.compile("|".join("(?:%s)" % rx.pattern for rx, _ in _GD_LEGACY))
_GD_CONNECT3 = re.compile(r"\bconnect\s*\(\s*[\"'][A-Za-z_]\w*[\"']\s*,\s*self\s*,\s*[\"']")
_GD_EXTENDS_REFERENCE = re.compile(r"^extends\s+Reference\b")


def _split_chain(chain: str) -> tuple[str | None, str]:
    """"GameState.state_changed" -> ("GameState", "state_changed")."""
    if chain.startswith("$") or chain.startswith("%"):
        # "$Button.pressed" -> ("$Button", "pressed")
        head, _, rest = chain.partition(".")
        if not rest:
            return None, head
        parts = rest.split(".")
        return ".".join([head] + parts[:-1]), parts[-1]
    parts = chain.split(".")
    if len(parts) == 1:
        return None, parts[0]
    recv = ".".join(parts[:-1])
    if recv == "self":
        recv = None
    return recv, parts[-1]


def _handler(arg: str) -> tuple[str | None, str | None, bool]:
    """Parse a connect() callable argument -> (receiver, method, is_lambda)."""
    a = arg.strip()
    if a.startswith("func"):
        return None, None, True
    m = re.match(r"Callable\s*\(\s*([\w.]+)\s*,\s*[&]?[\"'](\w+)[\"']", a)
    if m:
        recv = None if m.group(1) == "self" else m.group(1)
        return recv, m.group(2), False
    m = re.match(r"([A-Za-z_][\w.]*)", a)
    if not m:
        return None, None, False
    chain = m.group(1)
    for suffix in (".bind", ".bindv", ".unbind", ".call_deferred"):
        if chain.endswith(suffix):
            chain = chain[: -len(suffix)]
    recv, name = _split_chain(chain)
    return recv, name, False


def _prev_char(code: str, idx: int) -> str:
    j = idx - 1
    while j >= 0 and code[j] == " ":
        j -= 1
    return code[j] if j >= 0 else ""


def parse_gdscript(fp: Path, res: str) -> ScriptModel:
    text = fp.read_text(encoding="utf-8", errors="replace")
    m = ScriptModel(res, "gdscript", lines=text.count("\n") + 1)
    docs = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("##"):
            docs.append(s[2:].strip())
        elif s and not s.startswith("#") and not s.startswith("@"):
            if docs and (s.startswith("extends") or s.startswith("class_name")):
                continue
            break
    m.doc = " ".join(d for d in docs if d)[:400]

    scope: list[tuple[int, str, str]] = []  # (indent, kind, name)
    for lineno, indent, code in _logical_lines(text, "#"):
        while scope and indent <= scope[-1][0]:
            scope.pop()
        classes = [n for _, k, n in scope if k == "class"]
        funcs = [n for _, k, n in scope if k == "func"]
        cur = funcs[-1] if funcs else ""
        if classes and cur:
            cur = ".".join(classes) + "." + cur
        top = not scope
        blank = _blank_strings(code)
        defined_here = None

        # --- declarations
        if top:
            mm = _G_CLASS_NAME.match(code)
            if mm:
                m.class_name, m.class_line = mm.group(1), lineno
                if mm.group(2):
                    m.extends, m.extends_line = mm.group(2).strip("\"'"), lineno
            mm = _G_EXTENDS.match(code)
            if mm and not m.extends:
                m.extends, m.extends_line = mm.group(1).strip("\"'"), lineno
            if code.startswith("@tool"):
                m.is_tool = True
            mm = _G_SIGNAL.match(code)
            if mm:
                m.signals[mm.group(1)] = {"line": lineno, "params": (mm.group(3) or "").strip()}
            mm = _G_EXPORT.search(code)
            if mm:
                m.exports.append({"name": mm.group(1), "type": mm.group(2) or "", "line": lineno})
        mm = _G_CLASS.match(code)
        if mm:
            scope.append((indent, "class", mm.group(1)))
            continue
        mm = _G_FUNC.match(code)
        if mm:
            name = mm.group(2)
            qual = ".".join(classes + [name]) if classes else name
            params = [p.split(":")[0].split("=")[0].strip() for p in mm.group(3).split(",") if p.strip()]
            m.local_names.update(params)
            m.funcs.setdefault(qual, {"line": lineno, "params": params, "static": bool(mm.group(1))})
            for pm in _G_PARAM_TYPE.finditer(mm.group(3)):
                m.var_types.setdefault(pm.group(1), ("class", pm.group(2)))
            scope.append((indent, "func", name))
            cur = qual
            defined_here = name
            # fall through: one-line funcs (`func f(): return $A`) still get scanned

        # --- typed variables (used to resolve `x.signal.connect` / `x.method()`)
        mm = _G_VAR_PATH.search(code)
        if mm:
            m.var_types[mm.group(1)] = ("path", mm.group(2))
        else:
            for rx in (_G_VAR_NEW, _G_VAR_AS, _G_VAR_TYPE):
                mm = rx.search(code)
                if mm and mm.group(2)[0].isupper():
                    m.var_types.setdefault(mm.group(1), ("class", mm.group(2)))
                    break

        # --- node paths
        dollars = [mm.group(1) for mm in _G_DOLLAR.finditer(blank) if not mm.group(1).startswith(("\"", "'"))]
        dollars += [mm.group(1) for mm in re.finditer(r"\$\"([^\"]+)\"|\$'([^']+)'", code) if mm.group(1)]
        for p in dollars:
            if p.startswith("%"):
                m.node_refs.append({"path": p, "optional": False, "line": lineno, "func": cur})
            else:
                m.node_refs.append({"path": p.rstrip("."), "optional": False, "line": lineno, "func": cur})
        for mm in _G_UNIQUE.finditer(blank):
            m.node_refs.append({"path": "%" + mm.group(1), "optional": False, "line": lineno, "func": cur})
        for mm in _G_GETNODE.finditer(code):
            if _prev_char(code, mm.start()) == "." and not code[:mm.start()].rstrip().endswith("self."):
                continue  # `other.get_node(...)` is relative to another node
            m.node_refs.append({"path": mm.group(2), "optional": mm.group(1) != "get_node",
                                "line": lineno, "func": cur})
        if "add_child(" in code or "add_sibling(" in code:
            m.builds_nodes = True
        if "name" in code:
            for mm in _G_RUNTIME_NAME.finditer(code):
                m.runtime_names.add(mm.group(1) or mm.group(2))
        for mm in _G_VAR_NAME.finditer(blank):
            m.local_names.add(mm.group(1))

        # --- signals: connect / emit
        for mm in (_G_CONNECT.finditer(code) if "connect" in code else ()):
            chain = mm.group(1)
            if chain.endswith(".connect"):
                continue
            recv, sig = _split_chain(chain)
            if _prev_char(code, mm.start()) in (".", ")"):
                recv = "?"
            args = _args(code, mm.end() - 1)
            if args and _lit(args[0]) is not None:
                continue  # `obj.connect("sig", ...)` handled below
            if len(args) >= 2 and not re.match(r"^(?:Object\.)?CONNECT_|^\d|^[A-Z_]+$|.*\|", args[1].strip()):
                continue  # `obj.connect(signal_name_var, callable)`: signal unknown statically
            hrecv, hname, lam = _handler(args[0]) if args else (None, None, False)
            m.connects.append({"recv": recv, "signal": sig, "handler": hname, "hrecv": hrecv,
                               "lambda": lam, "line": lineno, "func": cur})
        for mm in (_G_CONNECT_STR.finditer(code) if "connect" in code else ()):
            recv = mm.group(1)
            if recv == "self":
                recv = None
            args = _args(code, code.index("(", mm.start()))
            hrecv, hname, lam = (None, None, False)
            if len(args) >= 3 and args[1].strip() in ("self",) and _lit(args[2]):
                hname = _lit(args[2])  # Godot 3 form
            elif len(args) >= 2:
                hrecv, hname, lam = _handler(args[1])
            m.connects.append({"recv": recv, "signal": mm.group(2), "handler": hname, "hrecv": hrecv,
                               "lambda": lam, "line": lineno, "func": cur})
        for mm in (_G_EMIT.finditer(code) if ".emit" in code else ()):
            recv, sig = _split_chain(mm.group(1))
            if _prev_char(code, mm.start()) in (".", ")"):
                recv = "?"
            m.emits.append({"recv": recv, "signal": sig, "line": lineno, "func": cur})
        for mm in (_G_EMIT_STR.finditer(code) if "emit_signal" in code else ()):
            recv = mm.group(1)
            m.emits.append({"recv": None if recv == "self" else recv, "signal": mm.group(2),
                            "line": lineno, "func": cur})

        # --- calls
        for mm in _G_CALL.finditer(blank):
            name = mm.group(2)
            if name in ("func", "if", "elif", "while", "for", "match", "return", "not", "and", "or",
                        "connect", "emit", "emit_signal", "preload", "load", "await"):
                continue
            recv = mm.group(1)
            if name == defined_here and recv is None:
                continue  # the `func name(` declaration itself
            if recv is None and _prev_char(blank, mm.start()) in (".", ")", "]"):
                continue  # chained call on an expression we cannot type
            lits = []
            if any(q in code for q in ("\"", "'")):
                # map back to the original line to recover literal args
                oi = code.find(name + "(", 0)
                while oi != -1 and oi < len(code):
                    if oi == 0 or not (code[oi - 1].isalnum() or code[oi - 1] == "_"):
                        break
                    oi = code.find(name + "(", oi + 1)
                if oi != -1:
                    for i, a in enumerate(_args(code, oi + len(name))):
                        v = _lit(a)
                        if v is not None:
                            lits.append((i, v))
            m.calls.append({"recv": None if recv == "self" else recv, "name": name,
                            "line": lineno, "func": cur, "lits": lits})

        # --- resources
        for mm in (_G_RES.finditer(code) if "://" in code else ()):
            kind = (mm.group(1) or "references").replace("ResourceLoader.load", "load")
            if kind in ("load_threaded_request", "change_scene_to_file"):
                kind = "load"
            m.res_refs.append({"path": mm.group(2), "kind": kind, "line": lineno, "func": cur})

        # --- input actions
        for mm in (_G_ACTION.finditer(code) if ("action" in code or "get_axis" in code or "get_vector" in code) else ()):
            for a in _args(code, mm.end() - 1):
                v = _lit(a)
                if v is not None:
                    m.actions.append({"name": v, "line": lineno, "func": cur})
        for mm in _G_ADD_ACTION.finditer(code):
            args = _args(code, mm.end() - 1)
            if not args:
                continue
            v = _lit(args[0])
            if v is not None:
                m.action_decls.append({"name": v, "line": lineno})
            elif cur:
                params = m.funcs.get(cur, {}).get("params", [])
                if args[0] in params:
                    m.action_registrars.append((cur, params.index(args[0])))

        # --- groups
        for mm in (_G_GROUP.finditer(code) if "group" in code else ()):
            args = _args(code, mm.end() - 1)
            op = "add" if mm.group(1) in ("add_to_group",) else "remove" if mm.group(1) == "remove_from_group" else "query"
            for a in args[:2]:
                v = _lit(a)
                if v is not None:
                    m.groups.append({"name": v, "op": op, "line": lineno})
                    break

        # --- identifiers (autoload / class_name usage is resolved later)
        for ident in set(_IDENT.findall(blank)) - m.idents.keys():
            m.idents[ident] = lineno

        # --- Godot 3 leftovers
        for rx, msg in (_GD_LEGACY if _GD_LEGACY_ANY.search(code) else ()):
            if rx.search(blank if not rx.pattern.startswith("^") else code):
                if "class name" in msg and not (code.startswith("extends") or ".new(" in blank or ":" in blank):
                    continue
                m.legacy.append({"line": lineno, "msg": msg})
        if _GD_CONNECT3.search(code):
            m.legacy.append({"line": lineno, "msg": "Godot 3 `connect(\"signal\", self, \"method\")` - use `signal.connect(method)` in Godot 4"})
        if _GD_EXTENDS_REFERENCE.match(code):
            m.legacy.append({"line": lineno, "msg": "Godot 3 `Reference` - use `RefCounted` in Godot 4"})
    return m


# ---------------------------------------------------------------- C#

_CS_CLASS = re.compile(r"\b(?:public\s+|internal\s+|sealed\s+|abstract\s+|partial\s+|static\s+)*class\s+([A-Za-z_]\w*)(?:\s*<[^>]*>)?\s*(?::\s*([A-Za-z_][\w.]*))?")
_CS_SIGNAL = re.compile(r"\[Signal\]\s*(?:public\s+)?delegate\s+\w+\s+([A-Za-z_]\w*?)EventHandler\s*\(([^)]*)\)")
_CS_EXPORT = re.compile(r"\[Export[^\]]*\]\s*(?:public\s+|private\s+|protected\s+|internal\s+)*([\w<>\[\],.?]+)\s+([A-Za-z_]\w*)")
_CS_METHOD = re.compile(r"^(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|override|virtual|async|sealed|new|partial|extern|unsafe)\s+)+[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\(([^)]*)")
_CS_GETNODE = re.compile(r"\b(GetNode|GetNodeOrNull|HasNode|FindChild)\s*(?:<[^>]*>)?\s*\(\s*\"([^\"]+)\"")
_CS_EMIT = re.compile(r"\bEmitSignal\s*\(\s*(?:SignalName\.(\w+)|nameof\s*\(\s*(\w+)\s*\)|\"(\w+)\")")
_CS_EVENT = re.compile(r"(?:\b([A-Za-z_][\w.]*)\.)?([A-Z]\w*)\s*\+=\s*([A-Za-z_][\w.]*)")
_CS_CONNECT = re.compile(r"(?:\b([A-Za-z_][\w.]*)\.)?Connect\s*\(\s*(?:[\w.]*SignalName\.(\w+)|\"(\w+)\")\s*,\s*(?:new\s+Callable\s*\(\s*(\w+)\s*,\s*(?:MethodName\.(\w+)|nameof\s*\(\s*(\w+)\s*\)|\"(\w+)\")|Callable\.From\s*\(\s*(\w+)?)")
_CS_RES = re.compile(r"(?:\b(GD\.Load|ResourceLoader\.Load|GD\.Preload|ChangeSceneToFile)\s*(?:<[^>]*>)?\s*\(\s*)?\"((?:res|uid)://[^\"]*)\"")
_CS_ACTION = re.compile(r"\b(IsActionPressed|IsActionJustPressed|IsActionReleased|IsActionJustReleased|GetActionStrength|GetActionRawStrength|IsAction|ActionPress|ActionRelease|GetAxis|GetVector)\s*\(")
_CS_ADD_ACTION = re.compile(r"\bInputMap\s*\.\s*AddAction\s*\(")
_CS_GROUP = re.compile(r"\b(AddToGroup|RemoveFromGroup|IsInGroup|GetNodesInGroup|GetFirstNodeInGroup|CallGroup)\s*\(")
_CS_CALL = re.compile(r"(?:\b([A-Za-z_]\w*)\s*\.\s*)?([A-Z]\w*)\s*\(")


def parse_csharp(fp: Path, res: str) -> ScriptModel:
    text = fp.read_text(encoding="utf-8", errors="replace")
    m = ScriptModel(res, "csharp", lines=text.count("\n") + 1)
    # strip block comments, keep line count
    text_nc = re.sub(r"/\*.*?\*/", lambda x: "\n" * x.group(0).count("\n"), text, flags=re.S)
    stem = fp.stem
    brace = 0
    method_stack: list[list] = []  # [brace level at declaration, name, body entered]
    pending_attr = ""  # `[Signal]` / `[Export]` written on the line above the member
    for lineno, raw in enumerate(text_nc.splitlines(), 1):
        code = _strip_comment(raw, "//").strip()
        if not code:
            continue
        if re.fullmatch(r"(\[[^\]]*\]\s*)+", code):
            pending_attr += code
            continue
        if pending_attr:
            code = pending_attr + " " + code
            pending_attr = ""
        blank = _blank_strings(code)
        while method_stack and method_stack[-1][2] and brace <= method_stack[-1][0]:
            method_stack.pop()
        cur = method_stack[-1][1] if method_stack else ""
        mm = _CS_CLASS.search(blank)
        if mm and not m.class_name:
            # Godot binds a C# script to the class named like the file
            if mm.group(1) == stem or not m.class_name:
                m.class_name, m.class_line = mm.group(1), lineno
                m.extends, m.extends_line = (mm.group(2) or "").split(".")[-1], lineno
        if "[GlobalClass]" in code:
            m.idents.setdefault("__global_class__", lineno)
        mm = _CS_SIGNAL.search(code)
        if mm:
            m.signals[mm.group(1)] = {"line": lineno, "params": mm.group(2).strip()}
        mm = _CS_EXPORT.search(code)
        if mm:
            m.exports.append({"name": mm.group(2), "type": mm.group(1), "line": lineno})
        mm = _CS_METHOD.match(blank)
        if mm and mm.group(1) not in ("if", "while", "for", "switch", "return", "new"):
            name = mm.group(1)
            params = [p.strip().split(" ")[-1] for p in mm.group(2).split(",") if p.strip()]
            m.funcs.setdefault(name, {"line": lineno, "params": params, "static": " static " in f" {blank} "})
            for pm in re.finditer(r"\b([A-Z]\w*)\s+([a-z_]\w*)", mm.group(2)):
                m.var_types.setdefault(pm.group(2), ("class", pm.group(1)))
            if not blank.rstrip().endswith(";"):  # expression-bodied members have no block
                method_stack.append([brace, name, False])
            cur = name
        for vm in re.finditer(r"\b([A-Z]\w*)\s+(_?[a-z]\w*)\s*(?:=|;)", blank):
            m.var_types.setdefault(vm.group(2), ("class", vm.group(1)))
        for mm in _CS_GETNODE.finditer(code):
            if _prev_char(code, mm.start()) == "." and not code[:mm.start()].rstrip().endswith("this."):
                continue
            m.node_refs.append({"path": mm.group(2), "optional": mm.group(1) != "GetNode",
                                "line": lineno, "func": cur})
            vm = re.search(r"\b(_?\w+)\s*=\s*GetNode", code)
            if vm:
                m.var_types[vm.group(1)] = ("path", mm.group(2))
        if "AddChild(" in code:
            m.builds_nodes = True
        for mm in re.finditer(r"\bName\s*=\s*\"([^\"/]+)\"", code):
            m.runtime_names.add(mm.group(1))
        for mm in re.finditer(r"\b(?:var|[A-Z][\w<>]*)\s+([a-z_]\w*)\s*=", blank):
            m.local_names.add(mm.group(1))
        for mm in _CS_EMIT.finditer(code):
            m.emits.append({"recv": None, "signal": mm.group(1) or mm.group(2) or mm.group(3),
                            "line": lineno, "func": cur})
        for mm in _CS_EVENT.finditer(blank):
            recv, sig, handler = mm.group(1), mm.group(2), mm.group(3)
            hparts = handler.split(".")
            m.connects.append({"recv": None if recv == "this" else recv, "signal": sig,
                               "handler": hparts[-1], "hrecv": ".".join(hparts[:-1]) or None,
                               "lambda": False, "line": lineno, "func": cur})
        if "=>" in blank and "+=" in blank:
            for mm in re.finditer(r"(?:\b([A-Za-z_][\w.]*)\.)?([A-Z]\w*)\s*\+=\s*(?:\([^)]*\)|\w+)\s*=>", blank):
                m.connects.append({"recv": mm.group(1), "signal": mm.group(2), "handler": None,
                                   "hrecv": None, "lambda": True, "line": lineno, "func": cur})
        for mm in _CS_CONNECT.finditer(code):
            sig = mm.group(2) or mm.group(3)
            handler = mm.group(5) or mm.group(6) or mm.group(7) or mm.group(8)
            m.connects.append({"recv": mm.group(1), "signal": sig, "handler": handler,
                               "hrecv": None if mm.group(4) in (None, "this") else mm.group(4),
                               "lambda": handler is None, "line": lineno, "func": cur})
        declared = mm_decl.group(1) if (mm_decl := _CS_METHOD.match(blank)) else None
        for mm in _CS_CALL.finditer(blank):
            if mm.group(2) in ("GetNode", "EmitSignal", "Connect") or (mm.group(2) == declared and not mm.group(1)):
                continue
            recv = mm.group(1)
            m.calls.append({"recv": None if recv == "this" else recv, "name": mm.group(2),
                            "line": lineno, "func": cur, "lits": []})
        for mm in _CS_RES.finditer(code):
            kind = {"GD.Load": "load", "ResourceLoader.Load": "load", "GD.Preload": "preload",
                    "ChangeSceneToFile": "load"}.get(mm.group(1) or "", "references")
            m.res_refs.append({"path": mm.group(2), "kind": kind, "line": lineno, "func": cur})
        for mm in _CS_ACTION.finditer(code):
            for a in _args(code, mm.end() - 1):
                v = _lit(a)
                if v is not None:
                    m.actions.append({"name": v, "line": lineno, "func": cur})
        for mm in _CS_ADD_ACTION.finditer(code):
            args = _args(code, mm.end() - 1)
            v = _lit(args[0]) if args else None
            if v is not None:
                m.action_decls.append({"name": v, "line": lineno})
            elif args and cur and args[0] in m.funcs.get(cur, {}).get("params", []):
                m.action_registrars.append((cur, m.funcs[cur]["params"].index(args[0])))
        for mm in _CS_GROUP.finditer(code):
            args = _args(code, mm.end() - 1)
            v = _lit(args[0]) if args else None
            if v is not None:
                op = "add" if mm.group(1) == "AddToGroup" else "remove" if mm.group(1) == "RemoveFromGroup" else "query"
                m.groups.append({"name": v, "op": op, "line": lineno})
        for ident in set(_IDENT.findall(blank)) - m.idents.keys():
            m.idents[ident] = lineno
        brace += blank.count("{") - blank.count("}")
        for entry in method_stack:
            if brace > entry[0]:
                entry[2] = True
    if not m.class_name:
        m.class_name = stem
    return m
