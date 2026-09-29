"""Parser for Godot's text formats: .tscn, .tres and project.godot (ConfigFile).

All three share one layout: `[section attr=value ...]` headers followed by
`key = value` lines, where a value may span several lines (arrays, dicts,
multi-line strings). We only need structure, not full Variant decoding, so
values are kept as raw strings and decoded on demand with the helpers below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Section:
    tag: str
    attrs: dict[str, str]
    props: dict[str, str] = field(default_factory=dict)
    line: int = 0


def _scan_depth(text: str, depth: int, in_str: bool) -> tuple[int, bool]:
    """Advance bracket depth / string state across *text*."""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in "[{(":
                depth += 1
            elif c in "]})":
                depth -= 1
            elif c == ";" and depth == 0:
                break  # comment (project.godot)
        i += 1
    return depth, in_str


def split_top(text: str, sep: str = " ") -> list[str]:
    """Split *text* on *sep* only at bracket depth 0 and outside strings."""
    out, buf = [], []
    depth, in_str, i = 0, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            buf.append(c)
            if c == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            buf.append(c)
        elif c in "[{(":
            depth += 1
            buf.append(c)
        elif c in "]})":
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            if buf:
                out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if buf:
        out.append("".join(buf))
    return [s for s in (x.strip() for x in out) if s]


def _parse_header(line: str) -> tuple[str, dict[str, str]]:
    inner = line.strip()[1:-1].strip()
    parts = split_top(inner, " ")
    tag = parts[0] if parts else ""
    attrs: dict[str, str] = {}
    pending_key = None
    for p in parts[1:]:
        # tolerate `key= value` (Godot writes `binds= [7]`)
        if pending_key is not None:
            attrs[pending_key] = p
            pending_key = None
            continue
        if "=" in p:
            k, _, v = p.partition("=")
            if v == "":
                pending_key = k.strip()
            else:
                attrs[k.strip()] = v.strip()
    return tag, attrs


def parse(text: str) -> list[Section]:
    """Parse Godot ConfigFile-style text into sections. Top-level keys (before
    the first header) go into a section with tag ''."""
    sections: list[Section] = [Section("", {}, {}, 0)]
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        lineno = i + 1
        i += 1
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and raw[:1] == "[":
            # header may itself span lines if an attr contains a multi-line array
            header = stripped
            depth, in_str = _scan_depth(header, 0, False)
            while (depth > 0 or in_str) and i < len(lines):
                header += " " + lines[i].strip()
                i += 1
                depth, in_str = _scan_depth(header, 0, False)
            tag, attrs = _parse_header(header)
            sections.append(Section(tag, attrs, {}, lineno))
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        depth, in_str = _scan_depth(value, 0, False)
        while (depth > 0 or in_str) and i < len(lines):
            value += "\n" + lines[i]
            i += 1
            depth, in_str = _scan_depth(value, 0, False)
        sections[-1].props[key] = value
    return sections


# ---------------------------------------------------------------- value helpers

_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def unquote(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if v.startswith("&"):
        v = v[1:]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def strings_in(v: str | None) -> list[str]:
    if not v:
        return []
    return [m.group(1) for m in _STR_RE.finditer(v)]


_RES_CALL_RE = re.compile(r'(ExtResource|SubResource)\(\s*"?([^")]+)"?\s*\)')


def resource_ref(v: str | None) -> tuple[str, str] | None:
    """`ExtResource("3_ab")` -> ("ExtResource", "3_ab")."""
    if not v:
        return None
    m = _RES_CALL_RE.fullmatch(v.strip())
    return (m.group(1), m.group(2)) if m else None


def all_ext_refs(v: str | None) -> list[str]:
    if not v:
        return []
    return [m.group(2) for m in _RES_CALL_RE.finditer(v) if m.group(1) == "ExtResource"]


def is_true(v: str | None) -> bool:
    return (v or "").strip() == "true"
