"""Structural extraction for .tscn scenes and .tres resources."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import godot_text as gt


@dataclass
class ExtRes:
    id: str
    type: str
    path: str
    uid: str
    line: int


@dataclass
class SceneNode:
    path: str  # "." for root, "A/B" otherwise
    name: str
    type: str  # "" for instanced nodes without an explicit type
    parent: str | None
    line: int
    script_ext: str | None = None  # ExtResource id
    instance_ext: str | None = None
    groups: list[str] = field(default_factory=list)
    unique: bool = False
    ext_refs: list[str] = field(default_factory=list)  # ExtResource ids used by props


@dataclass
class Connection:
    signal: str
    source: str  # node path
    target: str
    method: str
    line: int
    flags: str = ""


@dataclass
class SceneModel:
    res: str
    uid: str
    ext: dict[str, ExtRes]
    nodes: dict[str, SceneNode]
    connections: list[Connection]
    editable: list[str]
    sub_scripts: list[tuple[str, int]]  # (ext id, line) of sub_resource scripts
    format: str = ""

    @property
    def root(self) -> SceneNode | None:
        return self.nodes.get(".")


@dataclass
class ResourceModel:
    res: str
    uid: str
    type: str
    script_class: str
    ext: dict[str, ExtRes]
    script_ext: str | None
    format: str = ""


def _ext_map(sections) -> dict[str, ExtRes]:
    ext = {}
    for s in sections:
        if s.tag == "ext_resource":
            eid = gt.unquote(s.attrs.get("id")) or ""
            ext[eid] = ExtRes(eid, gt.unquote(s.attrs.get("type")) or "",
                              gt.unquote(s.attrs.get("path")) or "",
                              gt.unquote(s.attrs.get("uid")) or "", s.line)
    return ext


def _join(parent: str, name: str) -> str:
    return name if parent in (".", "") else f"{parent}/{name}"


def parse_scene(fp: Path, res: str) -> SceneModel:
    text = fp.read_text(encoding="utf-8", errors="replace")
    sections = gt.parse(text)
    head = next((s for s in sections if s.tag == "gd_scene"), None)
    uid = gt.unquote(head.attrs.get("uid")) if head else ""
    fmt = gt.unquote(head.attrs.get("format")) if head else ""
    ext = _ext_map(sections)
    nodes: dict[str, SceneNode] = {}
    conns: list[Connection] = []
    editable: list[str] = []
    sub_scripts: list[tuple[str, int]] = []
    for s in sections:
        if s.tag == "node":
            name = gt.unquote(s.attrs.get("name")) or ""
            parent = gt.unquote(s.attrs.get("parent"))
            path = "." if parent is None else _join(parent, name)
            n = SceneNode(path, name, gt.unquote(s.attrs.get("type")) or "", parent, s.line)
            inst = gt.resource_ref(s.attrs.get("instance"))
            if inst and inst[0] == "ExtResource":
                n.instance_ext = inst[1]
            n.groups = gt.strings_in(s.attrs.get("groups"))
            sc = gt.resource_ref(s.props.get("script"))
            if sc and sc[0] == "ExtResource":
                n.script_ext = sc[1]
            n.unique = gt.is_true(s.props.get("unique_name_in_owner"))
            for k, v in s.props.items():
                if k == "script":
                    continue
                n.ext_refs.extend(gt.all_ext_refs(v))
            nodes[path] = n
        elif s.tag == "connection":
            conns.append(Connection(
                gt.unquote(s.attrs.get("signal")) or "", gt.unquote(s.attrs.get("from")) or "",
                gt.unquote(s.attrs.get("to")) or "", gt.unquote(s.attrs.get("method")) or "",
                s.line, s.attrs.get("flags", "")))
        elif s.tag == "editable":
            editable.append(gt.unquote(s.attrs.get("path")) or "")
        elif s.tag == "sub_resource":
            sc = gt.resource_ref(s.props.get("script"))
            if sc and sc[0] == "ExtResource":
                sub_scripts.append((sc[1], s.line))
    return SceneModel(res, uid or "", ext, nodes, conns, editable, sub_scripts, fmt or "")


def parse_resource(fp: Path, res: str) -> ResourceModel:
    text = fp.read_text(encoding="utf-8", errors="replace")
    sections = gt.parse(text)
    head = next((s for s in sections if s.tag == "gd_resource"), None)
    attrs = head.attrs if head else {}
    ext = _ext_map(sections)
    script_ext = None
    for s in sections:
        if s.tag == "resource":
            sc = gt.resource_ref(s.props.get("script"))
            if sc and sc[0] == "ExtResource":
                script_ext = sc[1]
    return ResourceModel(res, gt.unquote(attrs.get("uid")) or "", gt.unquote(attrs.get("type")) or "",
                         gt.unquote(attrs.get("script_class")) or "", ext, script_ext,
                         gt.unquote(attrs.get("format")) or "")
