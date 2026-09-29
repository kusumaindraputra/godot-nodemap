"""Text answers over the graph: query / path / explain / tree / signal.

Output is compact plain text meant to be pasted into an LLM context: one line
per node or edge, always with file:line so the agent can jump to the source.
"""
from __future__ import annotations

import re
from collections import deque
from pathlib import PurePosixPath

from .analyze import STRUCTURAL, degree_table
from .graph import Graph

_STOP = {"the", "a", "an", "is", "are", "was", "how", "what", "why", "where", "when", "who", "which",
         "does", "do", "did", "to", "of", "in", "on", "for", "and", "or", "with", "from", "by", "it",
         "this", "that", "be", "can", "i", "we", "my", "our", "about", "into", "get", "gets", "use",
         "used", "uses", "work", "works", "happen", "happens", "there", "show", "me", "all", "yang",
         "dan", "di", "ke", "dari", "apa", "bagaimana", "kenapa", "mana", "ini", "itu", "cara"}


def _tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t and t not in _STOP]


def _variants(tok: str) -> set[str]:
    """Cheap stemming: 'dies'/'died'/'dying' all meet at 'die'."""
    v = {tok}
    if len(tok) > 3:
        for suf, rep in (("ies", "y"), ("ied", "y"), ("ying", "ie"), ("ing", ""), ("ing", "e"), ("es", ""),
                         ("ed", ""), ("ed", "e"), ("s", ""), ("d", "")):
            if tok.endswith(suf):
                v.add(tok[: -len(suf)] + rep)
    return v


def _match(qtoks: list[str], hay: set[str]) -> int:
    hv = set()
    for h in hay:
        hv |= _variants(h)
    return sum(1 for q in qtoks if _variants(q) & hv)


# relations worth showing first when exploring a neighbourhood
_REL_PRIORITY = {"emits": 0, "connected_to": 0, "calls": 1, "node_path": 1, "uses_autoload": 2, "extends": 2,
                 "has_script": 2, "instances": 2, "inherits_scene": 2, "overrides": 3, "uses_action": 3,
                 "in_group": 3, "adds_to_group": 3, "queries_group": 3, "autoloads": 3, "uses_class": 4,
                 "loads": 5, "preloads": 5, "references": 6, "uses": 6}


def _is_test(g: Graph, nid: str) -> bool:
    from .analyze import is_test
    n = g.nodes.get(nid, {})
    return is_test(n.get("file", nid))


def fmt_loc(n: dict | None = None, file: str | None = None, line: int | None = None) -> str:
    f = file or (n or {}).get("file", "")
    ln = line if line is not None else (n or {}).get("line")
    f = f.replace("res://", "")
    return f"{f}:{ln}" if f and ln else f


def fmt_node(g: Graph, nid: str) -> str:
    n = g.nodes.get(nid)
    if not n:
        return nid
    extra = ""
    if n["kind"] in ("function", "signal"):
        owner = g.nodes.get(n.get("script", ""), {}).get("label", "")
        extra = f" in {owner}" if owner else ""
    elif n["kind"] == "node":
        extra = f" in {PurePosixPath(n.get('scene', '')).name}"
    loc = fmt_loc(n)
    return f"[{n['kind']}] {n['label']}{extra}" + (f" ({loc})" if loc else "")


def fmt_edge(g: Graph, e: dict) -> str:
    rel = e["relation"]
    if e.get("signal"):
        rel += f"({e['signal']})"
    conf = "" if e["confidence"] == "EXTRACTED" else f" [{e['confidence']}]"
    at = fmt_loc(file=e.get("file"), line=e.get("line"))
    return f"{_short(g, e['source'])} --{rel}--> {_short(g, e['target'])}{conf}  @{at}"


def _short(g: Graph, nid: str) -> str:
    n = g.nodes.get(nid)
    if not n:
        return nid
    if n["kind"] in ("function", "signal"):
        owner = g.nodes.get(n.get("script", ""), {}).get("label", "?")
        return f"{owner}.{n['label']}" if n["kind"] == "function" else f"{owner}::{n['label']}"
    if n["kind"] == "node":
        return f"{PurePosixPath(n.get('scene', '')).name}:{n.get('path')}"
    return n["label"]


def find(g: Graph, term: str, limit: int = 8, kinds: set[str] | None = None) -> list[str]:
    """Best-matching node ids for a free-text term."""
    t = term.strip()
    if t in g.nodes:
        return [t]
    for pre in ("res://", "autoload:", "action:", "group:"):
        if pre + t in g.nodes:
            return [pre + t]
    toks = _tokens(t)
    low = t.lower()
    deg = _deg_cache(g)
    scored = []
    for nid, n in g.nodes.items():
        if kinds and n["kind"] not in kinds:
            continue
        label = n["label"].lower()
        name = label.split(" (")[0].rstrip("()")
        s = 0.0
        if name == low or label == low or PurePosixPath(n.get("file", "")).stem.lower() == low \
                or (n.get("class_name", "").lower() == low):
            s += 10
        elif low and low in label:
            s += 4
        hay = set(_tokens(n["label"] + " " + PurePosixPath(n.get("file", "")).stem + " " + n.get("class_name", "")))
        hit = _match(toks, hay)
        if not hit and s == 0:
            continue
        s += hit * 2 + (hit / max(len(hay), 1))
        if n["kind"] in ("script", "scene", "autoload", "signal"):
            s += 0.5
        if _is_test(g, nid):
            s -= 1.5
        s += min(deg.get(nid, 0), 50) / 100
        scored.append((s, nid))
    scored.sort(key=lambda x: -x[0])
    return [nid for _, nid in scored[:limit]]


_DEG: dict[int, dict] = {}


def _deg_cache(g: Graph) -> dict:
    key = id(g)
    if key not in _DEG:
        _DEG.clear()
        _DEG[key] = degree_table(g)
    return _DEG[key]


def query(g: Graph, question: str, budget: int = 2000, dfs: bool = False, depth: int = 2) -> str:
    seeds = find(g, question, limit=5)
    if not seeds:
        return f"No node matches '{question}'. Try `nodemap explain <name>` with a script, scene, signal or autoload name."
    adj = g.adjacency()
    lines = [f"# nodemap query: {question}", "", "Seeds:"]
    lines += [f"- {fmt_node(g, s)}" for s in seeds]
    lines.append("")
    lines.append("Edges:")
    used = sum(len(x) for x in lines) // 4
    seen_n = set(seeds)
    seen_e = set()
    frontier = deque((s, 0) for s in seeds)
    while frontier:
        nid, d = frontier.pop() if dfs else frontier.popleft()
        nbrs = sorted(adj.get(nid, []), key=lambda x: (x[1]["relation"] in STRUCTURAL, _is_test(g, x[0]),
                                                      _REL_PRIORITY.get(x[1]["relation"], 7), x[1]["relation"]))
        for other, e, _ in nbrs:
            k = id(e)
            if k in seen_e:
                continue
            # skip structural fan-out from big files beyond the first hop
            if e["relation"] in STRUCTURAL and d > 0:
                continue
            seen_e.add(k)
            line = "- " + fmt_edge(g, e)
            used += len(line) // 4 + 1
            if used > budget:
                lines.append(f"... (budget {budget} tokens reached; narrow the question or raise --budget)")
                return "\n".join(lines)
            lines.append(line)
            if other not in seen_n and d + 1 < depth:
                seen_n.add(other)
                frontier.append((other, d + 1))
    return "\n".join(lines)


def shortest_path(g: Graph, a: str, b: str) -> str:
    sa, sb = find(g, a, 1), find(g, b, 1)
    if not sa or not sb:
        return f"Could not find {'`' + a + '`' if not sa else '`' + b + '`'} in the graph."
    src, dst = sa[0], sb[0]
    adj = g.adjacency()
    prev: dict[str, tuple[str, dict] | None] = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        if cur == dst:
            break
        for other, e, _ in adj.get(cur, []):
            if other not in prev:
                prev[other] = (cur, e)
                q.append(other)
    if dst not in prev:
        return f"No path between {fmt_node(g, src)} and {fmt_node(g, dst)}."
    chain = []
    cur = dst
    while prev[cur] is not None:
        p, e = prev[cur]
        chain.append(e)
        cur = p
    chain.reverse()
    out = [f"# path: {_short(g, src)} -> {_short(g, dst)} ({len(chain)} hops)", ""]
    out += [f"{i + 1}. {fmt_edge(g, e)}" for i, e in enumerate(chain)]
    return "\n".join(out)


def explain(g: Graph, term: str, limit: int = 60) -> str:
    hits = find(g, term, 5)
    if not hits:
        return f"No node matches '{term}'."
    nid = hits[0]
    n = g.nodes[nid]
    out = [f"# {fmt_node(g, nid)}"]
    for k in ("class_name", "extends", "type", "lang", "params", "path", "doc", "exports", "declared_in",
              "root_type", "uid", "singleton", "script_class"):
        if n.get(k) not in (None, "", []):
            v = n[k]
            if isinstance(v, list):
                v = ", ".join(v)
            out.append(f"{k}: {v}")
    cof = g.community_of()
    if nid in cof:
        out.append(f"community: {cof[nid]} - {g.community_labels.get(cof[nid], '')}")
    iss = [i for i in g.issues if i.get("file") == n.get("file") and i["severity"] != "info"
           and (n["kind"] in ("script", "scene", "resource") or i.get("line") == n.get("line"))]
    if iss:
        out.append("issues:")
        out += [f"  - {i['severity']} {i['code']} @{fmt_loc(file=i['file'], line=i['line'])}: {i['message']}" for i in iss[:10]]
    adj = g.adjacency()
    groups: dict[str, list[str]] = {}
    for other, e, direction in adj.get(nid, []):
        rel = e["relation"] + (f"({e['signal']})" if e.get("signal") else "")
        key = f"{rel} ->" if direction == "out" else f"<- {rel}"
        conf = "" if e["confidence"] == "EXTRACTED" else f" [{e['confidence']}]"
        groups.setdefault(key, []).append(f"{_short(g, other)}{conf} @{fmt_loc(file=e.get('file'), line=e.get('line'))}")
    shown = 0
    for key in sorted(groups):
        items = groups[key]
        out.append(f"{key} ({len(items)})")
        for it in items[:15]:
            out.append(f"  - {it}")
            shown += 1
        if len(items) > 15:
            out.append(f"  ... {len(items) - 15} more")
        if shown > limit:
            out.append("...")
            break
    if len(hits) > 1:
        out.append("")
        out.append("Other matches: " + "; ".join(_short(g, h) for h in hits[1:]))
    return "\n".join(out)


def scene_tree(g: Graph, scene: str, expand: int = 0) -> str:
    cands = [s for s in find(g, scene, 5, kinds={"scene"})]
    if not cands:
        return f"No scene matches '{scene}'. Scenes: " + ", ".join(
            sorted(n["label"] for n in g.nodes.values() if n["kind"] == "scene" and not n.get("external"))[:40])
    sid = cands[0]
    out: list[str] = []
    _render_scene(g, sid, out, "", expand, set())
    header = f"# {g.nodes[sid]['label']} ({sid})"
    if g.nodes[sid].get("main_scene"):
        header += "  [main scene]"
    return header + "\n" + "\n".join(out)


def _render_scene(g: Graph, sid: str, out: list[str], indent: str, expand: int, seen: set) -> None:
    seen = seen | {sid}
    outs: dict[str, list[dict]] = {}
    via_of: dict[str, list[dict]] = {}
    for e in g.edges:
        outs.setdefault(e["source"], []).append(e)
        if e.get("via"):
            via_of.setdefault(e["via"], []).append(e)
    base = [e["target"] for e in outs.get(sid, []) if e["relation"] == "inherits_scene"]
    if base:
        out.append(f"{indent}(inherits {_short(g, base[0])})")
    roots = [e["target"] for e in outs.get(sid, []) if e["relation"] == "contains"]
    roots += [e["target"] for e in outs.get(sid, []) if e["relation"] == "has_child"]

    def walk(nid: str, pre: str, last: bool, top: bool):
        n = g.nodes[nid]
        bits = [n["label"] if n["kind"] == "node" else _short(g, nid)]
        if n.get("unique"):
            bits[0] = "%" + bits[0]
        for e in outs.get(nid, []):
            r = e["relation"]
            if r == "has_script":
                bits.append(f"script={_short(g, e['target'])}")
            elif r == "instances":
                bits.append(f"instance={_short(g, e['target'])}")
            elif r == "in_group":
                bits.append(f"group={g.nodes.get(e['target'], {}).get('label', e['target'])}")
            elif r == "connected_to" and e.get("signal"):
                bits.append(f"{e['signal']}->{_short(g, e['target'])}")
        for e in via_of.get(nid, []):
            bits.append(f"{g.nodes.get(e['source'], {}).get('label', '?')}->{_short(g, e['target'])}")
        branch = "" if top else ("└─ " if last else "├─ ")
        out.append(f"{indent}{pre}{branch}{'  '.join(bits)}   :{n.get('line', '')}")
        kids = [e["target"] for e in outs.get(nid, []) if e["relation"] == "has_child"]
        child_pre = pre + ("" if top else ("   " if last else "│  "))
        for i, k in enumerate(kids):
            walk(k, child_pre, i == len(kids) - 1, False)
        if expand > 0:
            for e in outs.get(nid, []):
                if e["relation"] == "instances" and e["target"] not in seen and e["target"] in g.nodes:
                    sub: list[str] = []
                    _render_scene(g, e["target"], sub, indent + child_pre + "   ", expand - 1, seen)
                    out.extend(sub)

    for r in roots:
        walk(r, "", True, True)


def signal(g: Graph, name: str) -> str:
    hits = [nid for nid in find(g, name, 10, kinds={"signal"})]
    exact = [h for h in hits if g.nodes[h]["label"] == name]
    hits = exact or hits
    out = []
    if hits:
        for sid in hits[:5]:
            n = g.nodes[sid]
            out.append(f"# signal {n['label']}({n.get('params', '')}) declared in {_short(g, n.get('script', ''))} ({fmt_loc(n)})")
            em = [e for e in g.edges if e["target"] == sid and e["relation"] == "emits"]
            li = [e for e in g.edges if e["source"] == sid and e["relation"] == "connected_to"]
            out.append(f"emitted by ({len(em)}):")
            out += [f"  - {_short(g, e['source'])}{'' if e['confidence'] == 'EXTRACTED' else ' [' + e['confidence'] + ']'} @{fmt_loc(file=e.get('file'), line=e.get('line'))}" for e in em]
            out.append(f"listeners ({len(li)}):")
            out += [f"  - {_short(g, e['target'])}{' (lambda)' if e.get('lambda_') else ''}"
                    f"{'' if e['confidence'] == 'EXTRACTED' else ' [' + e['confidence'] + ']'} @{fmt_loc(file=e.get('file'), line=e.get('line'))}"
                    for e in li]
            out.append("")
    builtin = [e for e in g.edges if e.get("signal") == name]
    if builtin:
        out.append(f"# built-in / unresolved signal '{name}' connections ({len(builtin)}):")
        out += [f"  - {fmt_edge(g, e)}" for e in builtin[:40]]
    return "\n".join(out) if out else f"No signal named '{name}'."
