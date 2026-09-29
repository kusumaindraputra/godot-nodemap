"""In-memory graph container shared by build, analysis, export and query."""
from __future__ import annotations

import json
from pathlib import Path

CONFIDENCE = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._ekeys: set[tuple] = set()
        self.meta: dict = {}
        self.issues: list[dict] = []
        self.communities: dict[int, list[str]] = {}
        self.community_labels: dict[int, str] = {}

    # ------------------------------------------------------------ building
    def node(self, nid: str, kind: str, label: str, **attrs) -> str:
        n = self.nodes.get(nid)
        if n is None:
            n = {"id": nid, "kind": kind, "label": label}
            self.nodes[nid] = n
        for k, v in attrs.items():
            if v not in (None, "", [], {}) and k not in n:
                n[k] = v
        return nid

    def edge(self, source: str, target: str, relation: str, confidence: str = "EXTRACTED",
             file: str | None = None, line: int | None = None, **attrs) -> None:
        if source == target and relation not in ("calls",):
            return
        key = (source, target, relation, attrs.get("signal"))
        if key in self._ekeys:
            return
        self._ekeys.add(key)
        e = {"source": source, "target": target, "relation": relation, "confidence": confidence}
        if file:
            e["file"] = file
        if line:
            e["line"] = line
        for k, v in attrs.items():
            if v not in (None, "", False):
                e[k] = v
        self.edges.append(e)

    def issue(self, severity: str, code: str, file: str, line: int | None, message: str,
              hint: str = "", node: str | None = None) -> None:
        i = {"severity": severity, "code": code, "file": file, "line": line or 0, "message": message}
        if hint:
            i["hint"] = hint
        if node:
            i["node"] = node
        self.issues.append(i)

    # ------------------------------------------------------------ views
    def adjacency(self) -> dict[str, list[tuple[str, dict, str]]]:
        """node -> [(neighbour, edge, 'out'|'in')]"""
        adj: dict[str, list] = {n: [] for n in self.nodes}
        for e in self.edges:
            if e["source"] in adj and e["target"] in adj:
                adj[e["source"]].append((e["target"], e, "out"))
                adj[e["target"]].append((e["source"], e, "in"))
        return adj

    def community_of(self) -> dict[str, int]:
        return {n: cid for cid, ns in self.communities.items() for n in ns}

    # ------------------------------------------------------------ io
    def to_dict(self) -> dict:
        cof = self.community_of()
        nodes = []
        for n in self.nodes.values():
            d = dict(n)
            if n["id"] in cof:
                d["community"] = cof[n["id"]]
            nodes.append(d)
        return {
            "meta": self.meta,
            "nodes": nodes,
            "edges": self.edges,
            "communities": {str(k): {"label": self.community_labels.get(k, ""), "nodes": v}
                            for k, v in self.communities.items()},
            "issues": self.issues,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Graph":
        data = json.loads(path.read_text(encoding="utf-8"))
        g = cls()
        g.meta = data.get("meta", {})
        for n in data.get("nodes", []):
            n = dict(n)
            n.pop("community", None)
            g.nodes[n["id"]] = n
        g.edges = data.get("edges", [])
        g.issues = data.get("issues", [])
        for k, v in data.get("communities", {}).items():
            g.communities[int(k)] = v["nodes"]
            g.community_labels[int(k)] = v.get("label", "")
        return g
