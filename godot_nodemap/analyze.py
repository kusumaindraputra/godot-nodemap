"""Graph analysis: communities, god nodes, surprising connections, signal bus,
suggested questions. Pure functions over a built Graph."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath

import networkx as nx

from .graph import Graph

# Structural edges say "X is part of Y"; they are useful for navigation but
# would make every file a god node, so analysis weighs them down.
STRUCTURAL = {"defines", "declares", "contains", "has_child"}
FILE_KINDS = {"script", "scene", "resource"}


def to_networkx(g: Graph, directed: bool = False):
    G = nx.DiGraph() if directed else nx.Graph()
    for nid, n in g.nodes.items():
        G.add_node(nid, **{k: v for k, v in n.items() if isinstance(v, (str, int, float, bool))})
    for e in g.edges:
        if e["source"] in g.nodes and e["target"] in g.nodes:
            w = 0.5 if e["relation"] in STRUCTURAL else 1.0
            if G.has_edge(e["source"], e["target"]):
                G[e["source"]][e["target"]]["weight"] += w
            else:
                G.add_edge(e["source"], e["target"], weight=w, relation=e["relation"])
    return G


def owner_file(g: Graph, nid: str) -> str:
    n = g.nodes.get(nid, {})
    return n.get("script") or n.get("scene") or n.get("file") or nid


def cluster(g: Graph, seed: int = 42) -> None:
    """Louvain communities over a file-level graph (members inherit their
    file's community), labelled by dominant folder + hub."""
    # collapse functions / signals / scene nodes into their file so communities
    # describe subsystems rather than single scripts
    F = nx.Graph()
    member_of: dict[str, str] = {}
    for nid, n in g.nodes.items():
        f = owner_file(g, nid) if n["kind"] in ("function", "signal", "node") else nid
        member_of[nid] = f
        F.add_node(f)
    for e in g.edges:
        a, b = member_of.get(e["source"]), member_of.get(e["target"])
        if not a or not b or a == b:
            continue
        w = 0.3 if e["relation"] in STRUCTURAL else 1.0
        if F.has_edge(a, b):
            F[a][b]["weight"] += w
        else:
            F.add_edge(a, b, weight=w)
    # hubs that everybody touches (autoloads, engine-wide enums) would glue
    # every community together; damp them
    for a, b, d in F.edges(data=True):
        d["weight"] /= max(1.0, (F.degree(a) * F.degree(b)) ** 0.25 / 2)
    if F.number_of_edges() == 0:
        comms = [set(F.nodes)]
    else:
        comms = nx.community.louvain_communities(F, weight="weight", seed=seed, resolution=1.0)
    comms = sorted(comms, key=len, reverse=True)
    file_comm: dict[str, int] = {}
    for i, c in enumerate(comms):
        for f in c:
            file_comm[f] = i
    out: dict[int, list[str]] = defaultdict(list)
    for nid in g.nodes:
        out[file_comm.get(member_of[nid], 0)].append(nid)
    g.communities = dict(out)
    g.community_labels = {cid: _label(g, members, F) for cid, members in g.communities.items()}


def is_test(path: str) -> bool:
    p = path.replace("res://", "/").lower()
    name = p.rsplit("/", 1)[-1]
    return ("/tests/" in p or "/test/" in p or name.endswith(("_test.gd", "test.cs", "tests.cs"))
            or name.startswith("test_"))


def _label(g: Graph, members: list[str], F) -> str:
    files = [m for m in members if g.nodes[m]["kind"] in FILE_KINDS | {"autoload"}]
    game = [f for f in files if not is_test(g.nodes[f].get("file", f))]
    files = game or files
    dirs = Counter()
    for f in files:
        p = PurePosixPath(g.nodes[f].get("file", f).replace("res://", ""))
        parts = p.parts[:-1]
        if parts:
            dirs["/".join(parts[:2])] += 1
    hub = max(files or members, key=lambda f: F.degree(f) if f in F else 0)
    hub_label = g.nodes[hub]["label"]
    if dirs:
        d, _ = dirs.most_common(1)[0]
        return f"{d} ({hub_label})"
    return hub_label


def degree_table(g: Graph) -> Counter:
    """Cross-file degree: how many distinct other files touch this node."""
    touch: dict[str, set] = defaultdict(set)
    for e in g.edges:
        if e["relation"] in STRUCTURAL:
            continue
        s, t = e["source"], e["target"]
        fs, ft = owner_file(g, s), owner_file(g, t)
        if fs != ft:
            touch[t].add(fs)
            touch[s].add(ft)
    return Counter({n: len(v) for n, v in touch.items() if n in g.nodes})


def god_nodes(g: Graph, top: int = 12) -> list[tuple[str, int]]:
    deg = degree_table(g)
    ranked = [(n, d) for n, d in deg.most_common()
              if g.nodes[n]["kind"] in ("script", "scene", "autoload", "signal", "function", "resource")]
    return ranked[:top]


def surprising_connections(g: Graph, top: int = 10) -> list[dict]:
    """Edges linking communities that rarely talk to each other, favouring
    non-structural, inferred and cross-folder links."""
    cof = g.community_of()
    pair_count: Counter = Counter()
    cross = []
    for e in g.edges:
        if e["relation"] in STRUCTURAL:
            continue
        a, b = cof.get(e["source"]), cof.get(e["target"])
        if a is None or b is None or a == b:
            continue
        pair_count[frozenset((a, b))] += 1
        cross.append(e)
    scored = []
    for e in cross:
        a, b = cof[e["source"]], cof[e["target"]]
        s = 1.0 / pair_count[frozenset((a, b))]
        if e["confidence"] != "EXTRACTED":
            s *= 1.5
        if e["relation"] in ("emits", "connected_to", "node_path", "calls"):
            s *= 1.3
        fa, fb = owner_file(g, e["source"]), owner_file(g, e["target"])
        if fa.split("/")[2:3] != fb.split("/")[2:3]:
            s *= 1.2
        scored.append((s, e))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for s, e in scored:
        key = frozenset((cof[e["source"]], cof[e["target"]]))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
        if len(out) >= top:
            break
    return out


def signal_bus(g: Graph) -> list[dict]:
    """For every project signal: who declares, emits and listens."""
    emits: dict[str, list[str]] = defaultdict(list)
    listens: dict[str, list[str]] = defaultdict(list)
    for e in g.edges:
        if e["relation"] == "emits" and e["target"] in g.nodes:
            emits[e["target"]].append(e["source"])
        elif e["relation"] == "connected_to" and "::sig:" in e["source"]:
            listens[e["source"]].append(e["target"])
    out = []
    for nid, n in g.nodes.items():
        if n["kind"] != "signal":
            continue
        out.append({"id": nid, "name": n["label"], "script": n.get("script", ""),
                    "params": n.get("params", ""),
                    "emitters": sorted(set(emits.get(nid, []))),
                    "listeners": sorted(set(listens.get(nid, [])))})
    out.sort(key=lambda s: -(len(s["emitters"]) + len(s["listeners"])))
    return out


def suggest_questions(g: Graph, gods: list[tuple[str, int]], bus: list[dict]) -> list[str]:
    qs = []
    for s in bus[:3]:
        qs.append(f"What happens when `{s['name']}` is emitted? (nodemap signal {s['name']})")
    for nid, _ in gods[:3]:
        n = g.nodes[nid]
        qs.append(f"Why is `{n['label']}` so central, and what breaks if it changes? (nodemap explain \"{n['label']}\")")
    main = g.meta.get("main_scene")
    if main:
        qs.append(f"How does the game boot from {main}? (nodemap tree {PurePosixPath(main).name})")
    labels = list(g.community_labels.values())
    if len(labels) >= 2:
        qs.append(f"How does `{labels[0]}` talk to `{labels[1]}`? (nodemap path ...)")
    return qs


def stats(g: Graph) -> dict:
    kinds = Counter(n["kind"] for n in g.nodes.values())
    rels = Counter(e["relation"] for e in g.edges)
    conf = Counter(e["confidence"] for e in g.edges)
    sev = Counter(i["severity"] for i in g.issues)
    return {"nodes": len(g.nodes), "edges": len(g.edges), "kinds": dict(kinds),
            "relations": dict(rels), "confidence": dict(conf), "issues": dict(sev),
            "communities": len(g.communities)}
