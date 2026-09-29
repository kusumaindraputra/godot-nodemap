"""Markdown extraction: design docs, ADRs, GDDs, READMEs.

A doc becomes one node; what it talks about becomes `mentions` edges. We only
collect *candidate* references here (paths and identifiers with their line and
section); build.py keeps the ones that match something real in the project and
reports the rest as stale when they clearly were meant to point at code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# the block `nodemap claude/agents install` writes into CLAUDE.md / AGENTS.md
_OWN_BLOCK = re.compile(r"<!-- nodemap:start -->.*?<!-- nodemap:end -->", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK_TARGET = re.compile(r"\]\(([^)\s]+)")
GODOT_FILE_EXT = ("gd", "cs", "tscn", "tres", "scn", "res", "gdshader", "godot")
_PATH = re.compile(r"(?:res://)?(?<![\w/.-])(?:[\w.-]+/)*[\w.-]+\.(?:" + "|".join(GODOT_FILE_EXT) + r")\b")
_RES_PATH = re.compile(r"res://[\w./-]*[\w/]")
# `Events.player_died.emit()`, `GameState.add_score()`: the trailing part tells us
# the member must be a signal or function (not a const / enum / property)
_QUALIFIED = re.compile(r"\b([A-Z]\w*)\.([a-z_]\w*)(\s*\(|\.(?:emit|connect)\b)?")
_CALL = re.compile(r"\b([a-z_]\w*)\(\)")
_IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
# CamelCase words in prose: "PlayerController", "HUD", "GameState" (not "The")
_CAMEL = re.compile(r"\b(?:[A-Z][a-z0-9]+[A-Z]\w*|[A-Z]{2,}[a-z]\w*|[A-Z]{2,})\b")


@dataclass
class DocModel:
    res: str
    title: str = ""
    lines: int = 0
    sections: list[tuple[int, str, int]] = field(default_factory=list)  # (level, title, line)
    # candidate references: {kind: path|qualified|call|code|prose, text, member, line, section}
    refs: list[dict] = field(default_factory=list)


def parse_markdown(fp: Path, res: str) -> DocModel:
    text = fp.read_text(encoding="utf-8", errors="replace")
    # blank out our own install block but keep line numbers
    text = _OWN_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    d = DocModel(res, lines=text.count("\n") + 1)
    section = ""
    in_fence = False
    seen: set[tuple] = set()

    def add(kind: str, txt: str, line: int, member: str = "", callish: bool = False) -> None:
        key = (kind, txt, member, section)
        if key in seen:
            return
        seen.add(key)
        d.refs.append({"kind": kind, "text": txt, "member": member, "callish": callish,
                       "line": line, "section": section})

    for lineno, raw in enumerate(text.splitlines(), 1):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if not in_fence:
            h = _HEADING.match(raw)
            if h:
                title = _CODE_SPAN.sub(lambda m: m.group(2), h.group(2)).strip()
                d.sections.append((len(h.group(1)), title, lineno))
                section = title
                if not d.title and len(h.group(1)) == 1:
                    d.title = title
        # paths (prose, links and code alike)
        for m in _RES_PATH.finditer(raw):
            add("path", m.group(0), lineno)
        for m in _PATH.finditer(raw):
            if not m.group(0).startswith("res://") and "://" not in raw[max(0, m.start() - 8):m.start()]:
                add("path", m.group(0), lineno)
        for m in _LINK_TARGET.finditer(raw):
            tgt = m.group(1).split("#")[0]
            if tgt.endswith(tuple("." + e for e in GODOT_FILE_EXT + ("md",))) and "://" not in tgt:
                add("path", tgt, lineno)
        # code: fenced blocks and `inline spans` may name anything (signals, actions, functions)
        code_parts = [raw] if in_fence else [m.group(2) for m in _CODE_SPAN.finditer(raw)]
        for code in code_parts:
            for m in _QUALIFIED.finditer(code):
                add("qualified", m.group(1), lineno, m.group(2), bool(m.group(3)))
            for m in _CALL.finditer(code):
                add("call", m.group(1), lineno)
            for ident in dict.fromkeys(_IDENT.findall(code)):
                add("code", ident, lineno)
        # prose: only CamelCase names (class / autoload / scene names); plain words are too noisy
        if not in_fence:
            prose = _CODE_SPAN.sub(" ", raw)
            for m in _QUALIFIED.finditer(prose):
                add("qualified", m.group(1), lineno, m.group(2), bool(m.group(3)))
            for ident in dict.fromkeys(_CAMEL.findall(prose)):
                add("prose", ident, lineno)
    if not d.title:
        d.title = fp.stem
    return d
