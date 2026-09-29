"""MCP stdio server (JSON-RPC 2.0, newline-delimited) exposing the graph.

Dependency-free on purpose: the protocol surface nodemap needs (initialize,
tools/list, tools/call, ping) is small, and avoiding the `mcp` package keeps
`pipx install godot-nodemap` light. The graph is reloaded when graph.json
changes on disk, so `nodemap update` / the edit hook are picked up live.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__, analyze, check, query
from .graph import Graph

PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "query_graph",
     "description": "Answer a question about the Godot project from the knowledge graph: finds the best-matching scripts/scenes/signals/autoloads and returns their neighbourhood (calls, signal wiring, node paths, preloads) with file:line. Use this before grepping.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string"}, "budget": {"type": "integer", "default": 2000},
         "dfs": {"type": "boolean", "default": False}}, "required": ["question"]}},
    {"name": "get_node",
     "description": "Explain one entity (script, scene, scene node, function, signal, autoload, input action, group): its attributes, every incoming/outgoing relation and known issues.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "shortest_path",
     "description": "Shortest chain of relations connecting two entities (e.g. how the player script reaches the HUD).",
     "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}},
                     "required": ["source", "target"]}},
    {"name": "get_scene_tree",
     "description": "The node tree of a .tscn scene: node names, types, attached scripts, instanced sub-scenes, groups, unique (%) names and signal connections. Use it before writing $NodePath / get_node() code.",
     "inputSchema": {"type": "object", "properties": {"scene": {"type": "string"},
                                                      "expand": {"type": "integer", "default": 0}},
                     "required": ["scene"]}},
    {"name": "find_signal",
     "description": "Where a signal is declared, which functions emit it and which handlers are connected to it (from code and .tscn files).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "check_project",
     "description": "Godot-aware lint: broken node paths, connections to missing methods, undeclared input actions, missing/misspelled res:// paths, invalid uids, Godot 3 syntax. Optionally limited to some files.",
     "inputSchema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}},
         "min_severity": {"type": "string", "enum": ["error", "warning", "info"], "default": "warning"}}}},
    {"name": "project_info",
     "description": "Project overview: main scene, autoloads, input actions, groups, named layers, god nodes, communities.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "graph_stats",
     "description": "Node/edge counts by kind, relation and confidence.",
     "inputSchema": {"type": "object", "properties": {}}},
]


class Server:
    def __init__(self, graph_path: Path):
        self.graph_path = graph_path
        self._g: Graph | None = None
        self._mtime = 0.0

    @property
    def g(self) -> Graph:
        mt = self.graph_path.stat().st_mtime
        if self._g is None or mt != self._mtime:
            self._g = Graph.load(self.graph_path)
            self._mtime = mt
        return self._g

    def call(self, name: str, a: dict) -> str:
        g = self.g
        if name == "query_graph":
            return query.query(g, a["question"], int(a.get("budget", 2000)), bool(a.get("dfs", False)))
        if name == "get_node":
            return query.explain(g, a["name"])
        if name == "shortest_path":
            return query.shortest_path(g, a["source"], a["target"])
        if name == "get_scene_tree":
            return query.scene_tree(g, a["scene"], int(a.get("expand", 0)))
        if name == "find_signal":
            return query.signal(g, a["name"])
        if name == "check_project":
            return check.format_text(check.filter_issues(g, a.get("files"), a.get("min_severity", "warning")))
        if name == "project_info":
            return project_info(g)
        if name == "graph_stats":
            return json.dumps(analyze.stats(g), indent=1)
        raise ValueError(f"unknown tool {name}")

    def handle(self, msg: dict) -> dict | None:
        mid = msg.get("id")
        method = msg.get("method")
        if mid is None:
            return None  # notification
        try:
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "godot-nodemap", "version": __version__}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params", {})
                try:
                    text = self.call(p.get("name", ""), p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}


def project_info(g: Graph) -> str:
    m = g.meta
    out = [f"project: {m.get('project')} ({', '.join(m.get('engine_features', []))})",
           f"main scene: {m.get('main_scene')}",
           "autoloads: " + ", ".join(f"{k}={v}" for k, v in m.get("autoloads", {}).items()),
           "input actions: " + ", ".join(m.get("input_actions", [])),
           "groups: " + ", ".join(sorted(n["label"] for n in g.nodes.values() if n["kind"] == "group")),
           "layers: " + ", ".join(f"{k}={v}" for k, v in m.get("layer_names", {}).items()),
           "god nodes: " + ", ".join(f"{query._short(g, n)} ({d})" for n, d in analyze.god_nodes(g, 10)),
           "communities:"]
    for cid, label in sorted(g.community_labels.items(), key=lambda kv: -len(g.communities.get(kv[0], []))):
        out.append(f"  C{cid}: {label} ({len(g.communities.get(cid, []))} nodes)")
    return "\n".join(out)


def serve(graph_path: Path) -> None:
    srv = Server(graph_path)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        replies = [r for r in (srv.handle(x) for x in msgs) if r is not None]
        for r in replies:
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
        sys.stdout.flush()
