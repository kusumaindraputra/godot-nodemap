"""Build the knowledge graph: extract every file, then link facts across files.

Pipeline:  walk() -> extract (cached) -> Linker.link() -> Graph
The linker is where Godot semantics live: class_name / extends chains, scene
trees (inherited + instanced scenes), node-path resolution, signal wiring,
autoloads, input actions and groups. Problems it meets on the way are
recorded as issues (see check.py for the catalogue).
"""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path, PurePosixPath

from . import __version__
from .graph import Graph
from .project import (BUILTIN_ACTIONS, VALID_UID_RE, ProjectConfig, build_uid_index, find_project_root,
                      parse_project_godot, walk)
from .scenes import ResourceModel, SceneModel, parse_resource, parse_scene
from .docs import GODOT_FILE_EXT, DocModel, parse_markdown
from .scripts import ScriptModel, parse_csharp, parse_gdscript

OUT_DIR = "nodemap-out"

# Methods every Node has; a scene connection to one of these on a script-less
# node is fine.
_NODE_BUILTIN_METHODS = {
    "queue_free", "hide", "show", "set_visible", "set_process", "set_physics_process",
    "play", "stop", "start", "popup", "popup_centered", "grab_focus", "release_focus",
    "set_disabled", "toggle", "emit_signal", "call_deferred", "set", "call", "set_deferred",
    "_on_timeout", "reset", "clear", "update", "queue_redraw", "set_text", "set_pressed",
    "restart", "emitting", "set_emitting", "seek", "pause", "advance",
}


def _fid(script: str, name: str) -> str:
    return f"{script}::fn:{name}"


def _sid(script: str, name: str) -> str:
    return f"{script}::sig:{name}"


def _nid(scene: str, path: str) -> str:
    return f"{scene}::node:{path}"


def _norm(parts: list[str]) -> list[str] | None:
    out: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if not out:
                return None
            out.pop()
        else:
            out.append(p)
    return out


class Linker:
    def __init__(self, root: Path, cfg: ProjectConfig, paths, uids: dict[str, str],
                 scenes: dict[str, SceneModel], resources: dict[str, ResourceModel],
                 scripts: dict[str, ScriptModel], docs: dict[str, DocModel] | None = None):
        self.root, self.cfg, self.paths, self.uids = root, cfg, paths, uids
        self.scenes, self.resources, self.scripts = scenes, resources, scripts
        self.docs = docs or {}
        self.g = Graph()
        self.class_names: dict[str, str] = {}
        self.autoload_script: dict[str, str] = {}
        self._tree_cache: dict[str, dict] = {}
        self._contexts: dict[str, list[tuple[str, str]]] = {}
        self._sig_index: dict[str, list[str]] = {}

    # ------------------------------------------------------------ helpers
    def res_ref(self, path: str, uid: str = "") -> str:
        """Resolve an ext_resource / literal reference the way Godot does:
        uid first, text path as fallback."""
        if uid and uid in self.uids:
            return self.uids[uid]
        if path.startswith("uid://"):
            return self.uids.get(path, path)
        if path and not path.startswith("res://"):
            return path
        return path

    def ext_target(self, model, eid: str) -> str | None:
        e = model.ext.get(eid)
        return self.res_ref(e.path, e.uid) if e else None

    def script_base(self, script: str) -> str | None:
        m = self.scripts.get(script)
        if not m or not m.extends:
            return None
        ext = m.extends
        if ext.startswith("res://") or ext.startswith("uid://"):
            ext = self.res_ref(ext)
            return ext if ext in self.scripts else None
        if ext in self.class_names:
            return self.class_names[ext]
        head = ext.split(".")[0]
        if head in self.class_names and "." in ext:
            return self.class_names[head]
        return None

    def chain(self, script: str) -> list[str]:
        out, seen = [], set()
        cur: str | None = script
        while cur and cur not in seen and cur in self.scripts:
            out.append(cur)
            seen.add(cur)
            cur = self.script_base(cur)
        return out

    def engine_base(self, script: str) -> str:
        ch = self.chain(script)
        if not ch:
            return ""
        last = self.scripts[ch[-1]]
        ext = last.extends
        if not ext or ext.startswith(("res://", "uid://")):
            return "RefCounted" if not ext else ""
        return ext

    def find_func(self, script: str, name: str) -> str | None:
        for s in self.chain(script):
            if name in self.scripts[s].funcs:
                return s
        return None

    def find_signal(self, script: str, name: str) -> str | None:
        for s in self.chain(script):
            if name in self.scripts[s].signals:
                return s
        return None

    def script_of_class(self, name: str) -> str | None:
        if name in self.class_names:
            return self.class_names[name]
        return self.autoload_script.get(name)

    # ------------------------------------------------------------ scene trees
    def tree(self, scene: str, _depth: int = 0) -> dict[str, dict]:
        """Effective node table of *scene*: path -> {id, type, script, instance, unique, name}."""
        if scene in self._tree_cache:
            return self._tree_cache[scene]
        self._tree_cache[scene] = {}  # cycle guard
        sm = self.scenes.get(scene)
        if not sm or _depth > 16:
            return {}
        t: dict[str, dict] = {}
        root = sm.root
        if root and root.instance_ext:
            base = self.ext_target(sm, root.instance_ext)
            if base in self.scenes:
                for p, e in self.tree(base, _depth + 1).items():
                    t[p] = dict(e, inherited=True)
        for p, n in sm.nodes.items():
            e = t.get(p, {})
            e = dict(e)
            e["id"] = _nid(scene, p)
            e["name"] = n.name if p != "." else (n.name or e.get("name", ""))
            if n.type:
                e["type"] = n.type
            inst = self.ext_target(sm, n.instance_ext) if n.instance_ext and p != "." else None
            if inst:
                e["instance"] = inst
                it = self.tree(inst, _depth + 1).get(".")
                if it:
                    e.setdefault("type", it.get("type", ""))
                    if it.get("script"):
                        e.setdefault("script", it["script"])
            if n.script_ext:
                e["script"] = self.ext_target(sm, n.script_ext)
            if n.unique:
                e["unique"] = True
            e.pop("inherited", None)
            t[p] = e
        self._tree_cache[scene] = t
        return t

    def resolve_path(self, scene: str, from_path: str, rel: str, _depth: int = 0):
        """Resolve a NodePath used by a script attached at (scene, from_path).
        Returns ("ok", entry) | ("missing", reason) | ("unknown", reason)."""
        if _depth > 8:
            return ("unknown", "too deep")
        rel = rel.strip()
        if rel.startswith("/root/"):
            head = rel[len("/root/"):].split("/")[0]
            if head in self.cfg.autoloads:
                return ("ok", {"id": f"autoload:{head}", "autoload": head})
            return ("unknown", "absolute path")
        if rel.startswith("/"):
            return ("unknown", "absolute path")
        t = self.tree(scene)
        if not t:
            return ("unknown", "scene not parsed")
        segs = rel.split("/")
        base: list[str] = [] if from_path == "." else from_path.split("/")
        if segs[0].startswith("%"):
            uname = segs[0][1:]
            hits = [p for p, e in t.items() if e.get("unique") and e.get("name") == uname]
            if not hits:
                return ("missing", f"no node with unique name %{uname} in {scene}")
            base = [] if hits[0] == "." else hits[0].split("/")
            segs = segs[1:]
            if not segs:
                return ("ok", t[hits[0]])
        norm = _norm(base + segs)
        if norm is None:
            return ("unknown", "path leaves the scene (../ above root)")
        key = "/".join(norm) or "."
        if key in t:
            return ("ok", t[key])
        # descend into instanced sub-scenes
        for i in range(len(norm) - 1, 0, -1):
            prefix = "/".join(norm[:i])
            e = t.get(prefix)
            if e and e.get("instance"):
                return self.resolve_path(e["instance"], ".", "/".join(norm[i:]), _depth + 1)
            if e:
                break
        parent = "/".join(norm[:-1]) or "."
        return ("missing", f"no node '{key}' in {scene}" + (
            f" (parent '{parent}' has: {', '.join(sorted(self._children(t, parent))[:8]) or 'no children'})"
            if parent in t else ""))

    @staticmethod
    def _children(t: dict, parent: str) -> list[str]:
        out = []
        for p in t:
            if p == ".":
                continue
            par = p.rsplit("/", 1)[0] if "/" in p else "."
            if par == parent:
                out.append(p.rsplit("/", 1)[-1])
        return out

    def contexts(self, script: str) -> list[tuple[str, str]]:
        """Every (scene, node_path) where *script* (or a subclass) runs."""
        return self._contexts.get(script, [])

    def _build_contexts(self) -> None:
        direct: dict[str, list[tuple[str, str]]] = {}
        instanced_at: dict[str, list[tuple[str, str]]] = {}
        inherited_by: dict[str, list[str]] = {}
        for sres, sm in self.scenes.items():
            for p, e in self.tree(sres).items():
                if e.get("script"):
                    direct.setdefault(e["script"], []).append((sres, p))
                if e.get("instance"):
                    instanced_at.setdefault(e["instance"], []).append((sres, p))
            root = sm.root
            if root and root.instance_ext:
                b = self.ext_target(sm, root.instance_ext)
                if b:
                    inherited_by.setdefault(b, []).append(sres)
        subclasses: dict[str, list[str]] = {}
        for s in self.scripts:
            b = self.script_base(s)
            if b:
                subclasses.setdefault(b, []).append(s)

        def expand(ctx: tuple[str, str], seen: set) -> list[tuple[str, str]]:
            out = [ctx]
            scene, path = ctx
            if path == ".":
                for parent_scene, at in instanced_at.get(scene, []):
                    if (parent_scene, at) not in seen and len(seen) < 64:
                        seen.add((parent_scene, at))
                        out.append((parent_scene, at))
            for derived in inherited_by.get(scene, []):
                if (derived, path) not in seen and len(seen) < 64:
                    seen.add((derived, path))
                    out.extend(expand((derived, path), seen))
            return out

        for s in self.scripts:
            todo, seen_s, ctxs = [s], set(), []
            while todo:
                cur = todo.pop()
                if cur in seen_s:
                    continue
                seen_s.add(cur)
                for c in direct.get(cur, []):
                    ctxs.extend(expand(c, {c}))
                todo.extend(subclasses.get(cur, []))
            self._contexts[s] = list(dict.fromkeys(ctxs))

    # ------------------------------------------------------------ main
    def link(self) -> Graph:
        g = self.g
        cfg = self.cfg
        # class_name table
        for res, m in sorted(self.scripts.items()):
            if m.class_name:
                if m.class_name in self.class_names and m.lang == "gdscript":
                    other = self.class_names[m.class_name]
                    if self.scripts[other].lang == "gdscript":
                        g.issue("error", "duplicate-class-name", res, m.class_line,
                                f"class_name {m.class_name} is also declared in {other}",
                                "Every class_name must be unique across the project.")
                    continue
                self.class_names.setdefault(m.class_name, res)
        # autoloads
        for name, a in cfg.autoloads.items():
            target = self.res_ref(a["path"])
            aid = g.node(f"autoload:{name}", "autoload", name, file="res://project.godot",
                         line=a["line"], path=target, singleton=a["singleton"])
            if target in self.scripts:
                self.autoload_script[name] = target
            elif target in self.scenes:
                root = self.tree(target).get(".", {})
                if root.get("script"):
                    self.autoload_script[name] = root["script"]
            if not self.paths.exists(target):
                g.issue("error", "missing-autoload", "res://project.godot", a["line"],
                        f"Autoload {name} points to missing file {target}")
            g.edge(aid, target, "autoloads", file="res://project.godot", line=a["line"])
            sc = self.autoload_script.get(name)
            if sc and self.scripts[sc].class_name == name and self.scripts[sc].lang == "gdscript":
                g.issue("error", "autoload-class-name-conflict", sc, self.scripts[sc].class_line,
                        f"class_name {name} has the same name as the autoload {name}",
                        "Godot 4 refuses this ('Class hides an autoload singleton'). Remove the class_name line.")
        self._build_contexts()
        for res, m in self.scripts.items():
            for sname in m.signals:
                self._sig_index.setdefault(sname, []).append(res)

        self._link_actions_decl()
        for res, sm in sorted(self.scenes.items()):
            self._link_scene(res, sm)
        for res, rm in sorted(self.resources.items()):
            self._link_resource(res, rm)
        for res, m in sorted(self.scripts.items()):
            self._link_script(res, m)
        self._post_checks()
        if self.docs:
            self._link_docs()
        self._materialize_targets()
        main = cfg.main_scene
        if main:
            main_res = self.res_ref(main)
            if main_res in g.nodes:
                g.nodes[main_res]["main_scene"] = True
            elif not self.paths.exists(main_res):
                g.issue("error", "missing-main-scene", "res://project.godot", 0,
                        f"run/main_scene points to missing {main}")
        return g

    # ------------------------------------------------------------ actions
    def _link_actions_decl(self) -> None:
        g = self.g
        self.declared_actions: dict[str, tuple[str, int]] = {}
        for a, line in self.cfg.input_actions.items():
            self.declared_actions[a] = ("res://project.godot", line)
        for res, m in self.scripts.items():
            for d in m.action_decls:
                self.declared_actions.setdefault(d["name"], (res, d["line"]))
        registrars = {(res, f): idx for res, m in self.scripts.items() for f, idx in m.action_registrars}
        self.dynamic_actions = bool(registrars)
        if registrars:
            names = {f: (res, idx) for (res, f), idx in registrars.items()}
            for res, m in self.scripts.items():
                for c in m.calls:
                    if c["name"] in names and c["lits"]:
                        _, idx = names[c["name"]]
                        for i, v in c["lits"]:
                            if i == idx:
                                self.declared_actions.setdefault(v, (res, c["line"]))
        for a, (file, line) in self.declared_actions.items():
            g.node(f"action:{a}", "action", a, file=file, line=line,
                   declared_in="project.godot" if file == "res://project.godot" else "code")

    # ------------------------------------------------------------ scenes
    def _link_scene(self, res: str, sm: SceneModel) -> None:
        g = self.g
        root = sm.root
        g.node(res, "scene", PurePosixPath(res).name, file=res, line=1, uid=sm.uid,
               root_type=root.type if root else "", nodes=len(sm.nodes))
        self._check_ext(res, sm)
        t = self.tree(res)
        for p, n in sm.nodes.items():
            nid = _nid(res, p)
            e = t.get(p, {})
            typ = n.type or e.get("type", "")
            g.node(nid, "node", f"{n.name} ({typ})" if typ else n.name, file=res, line=n.line,
                   scene=res, path=p, type=typ, unique=n.unique or None)
            if p == ".":
                g.edge(res, nid, "contains", file=res, line=n.line)
                if n.instance_ext:
                    base = self.ext_target(sm, n.instance_ext)
                    g.edge(res, base, "inherits_scene", file=res, line=n.line)
            else:
                parent = _nid(res, n.parent or ".")
                if (n.parent or ".") not in sm.nodes:
                    # child of a node that lives in an instanced / inherited scene
                    parent = res
                g.edge(parent, nid, "has_child", file=res, line=n.line)
                if n.instance_ext:
                    inst = self.ext_target(sm, n.instance_ext)
                    g.edge(nid, inst, "instances", file=res, line=n.line)
            if n.script_ext:
                sc = self.ext_target(sm, n.script_ext)
                g.edge(nid, sc, "has_script", file=res, line=n.line)
            for grp in n.groups:
                g.node(f"group:{grp}", "group", grp)
                g.edge(nid, f"group:{grp}", "in_group", file=res, line=n.line)
            for eid in n.ext_refs:
                tgt = self.ext_target(sm, eid)
                if tgt:
                    g.edge(nid, tgt, "uses", file=res, line=n.line)
        for eid, line in sm.sub_scripts:
            sc = self.ext_target(sm, eid)
            if sc:
                g.edge(res, sc, "uses", file=res, line=line)
        for c in sm.connections:
            src = t.get(c.source if c.source else ".")
            dst = t.get(c.target if c.target else ".")
            where = f"[connection signal=\"{c.signal}\" from=\"{c.source}\" to=\"{c.target}\"]"
            if src is None:
                g.issue("error", "connection-missing-node", res, c.line,
                        f"{where}: emitter node '{c.source}' does not exist in the scene")
                continue
            if dst is None:
                g.issue("error", "connection-missing-node", res, c.line,
                        f"{where}: target node '{c.target}' does not exist in the scene")
                continue
            sig_node = None
            if src.get("script"):
                owner = self.find_signal(src["script"], c.signal)
                if owner:
                    sig_node = _sid(owner, c.signal)
            target_id = dst["id"]
            conf = "EXTRACTED"
            if dst.get("script"):
                owner = self.find_func(dst["script"], c.method)
                if owner:
                    target_id = _fid(owner, c.method)
                else:
                    sev = "warning" if c.method in _NODE_BUILTIN_METHODS or not c.method.startswith("_") else "error"
                    base = self.engine_base(dst["script"])
                    if c.method.startswith("_on_"):
                        sev = "error"
                    g.issue(sev, "missing-method", res, c.line,
                            f"{where}: method {c.method}() is not defined in {dst['script']}"
                            + (f" (or its base classes; engine base {base})" if base else ""),
                            f"Add `func {c.method}(...)` to {PurePosixPath(dst['script']).name} or fix the connection.")
            elif c.method not in _NODE_BUILTIN_METHODS:
                g.issue("warning", "missing-method", res, c.line,
                        f"{where}: target node '{c.target}' has no script, so method {c.method}() must be a built-in {dst.get('type', '')} method")
            if sig_node:
                g.edge(sig_node, target_id, "connected_to", conf, file=res, line=c.line,
                       via=src["id"])
            else:
                g.edge(src["id"], target_id, "connected_to", conf, file=res, line=c.line, signal=c.signal)

    def _check_ext(self, res: str, model) -> None:
        g = self.g
        bad = [u for u in [model.uid] + [e.uid for e in model.ext.values()] if u and not VALID_UID_RE.match(u)]
        if bad:
            g.issue("warning", "invalid-uid", res, 1,
                    f"{len(bad)} uid(s) are not valid Godot uids and are ignored by the engine: {', '.join(bad[:4])}"
                    + (" ..." if len(bad) > 4 else ""),
                    "Godot generates uids itself (e.g. uid://c3k8x2...). Delete invented uid=\"...\" "
                    "attributes and let the editor assign real ones.")
        for e in model.ext.values():
            target = self.res_ref(e.path, e.uid)
            if e.uid and e.uid in self.uids:
                real = self.uids[e.uid]
                if e.path and real != e.path:
                    g.issue("warning", "stale-path", res, e.line,
                            f"ext_resource id={e.id}: uid {e.uid} belongs to {real}, but path says {e.path}",
                            "Godot loads by uid, so it works, but the path is stale. Re-save the scene in the editor.")
                continue
            if e.uid and e.path and self.paths.exists(e.path) and VALID_UID_RE.match(e.uid):
                g.issue("warning", "unknown-uid", res, e.line,
                        f"ext_resource id={e.id}: uid {e.uid} does not match any file (falling back to {e.path})",
                        "Invented or stale uid. Remove the uid=\"...\" attribute or re-save in the editor.")
            self._check_path(res, e.line, target, f"ext_resource id={e.id}")

    def _check_path(self, file: str, line: int, target: str, what: str) -> bool:
        if target.startswith("uid://"):  # res_ref() already mapped every known uid
            self.g.issue("error", "unknown-uid", file, line, f"{what}: {target} does not match any file")
            return False
        if not target.startswith("res://"):
            return True
        if self.paths.exists(target):
            return True
        ci = self.paths.case_insensitive_match(target)
        if ci:
            self.g.issue("error", "case-mismatch", file, line,
                         f"{what}: {target} differs in case from the real file {ci}",
                         "Works in the editor on Windows/macOS but breaks in exported builds. Fix the casing.")
            return False
        if any(target.startswith(d + "/") for d in self.paths.ignored_dirs):
            return True
        self.g.issue("error", "missing-resource", file, line, f"{what}: {target} does not exist")
        return False

    # ------------------------------------------------------------ resources
    def _link_resource(self, res: str, rm: ResourceModel) -> None:
        g = self.g
        g.node(res, "resource", PurePosixPath(res).name, file=res, line=1, type=rm.type,
               script_class=rm.script_class, uid=rm.uid)
        self._check_ext(res, rm)
        for eid, e in rm.ext.items():
            tgt = self.ext_target(rm, eid)
            if not tgt:
                continue
            rel = "has_script" if eid == rm.script_ext else "uses"
            g.edge(res, tgt, rel, file=res, line=e.line)

    # ------------------------------------------------------------ scripts
    def _script_of_recv(self, script: str, m: ScriptModel, recv: str | None) -> tuple[list[str], str]:
        """Scripts a receiver expression may refer to, with confidence."""
        if recv is None:
            return [script], "EXTRACTED"
        if recv == "?":
            return [], ""
        if recv in self.cfg.autoloads:
            s = self.autoload_script.get(recv)
            return ([s] if s else []), "EXTRACTED"
        if recv in self.class_names:
            return [self.class_names[recv]], "EXTRACTED"
        if recv.startswith(("$", "%")):
            return self._scripts_at_path(script, recv[1:] if recv[0] == "$" else recv), "INFERRED"
        head = recv.split(".")[0]
        vt = m.var_types.get(head)
        if vt and head == recv:
            kind, val = vt
            if kind == "class":
                s = self.script_of_class(val)
                return ([s] if s else []), "INFERRED"
            path = val
            for pre in ("get_node_or_null(", "get_node("):
                if path.startswith(pre):
                    path = path[len(pre):].strip("^&)\" ")
            path = path.lstrip("$").strip("\"")
            return self._scripts_at_path(script, path), "INFERRED"
        return [], ""

    def _scripts_at_path(self, script: str, path: str) -> list[str]:
        out = []
        for scene, at in self.contexts(script):
            st, e = self.resolve_path(scene, at, path)
            if st == "ok" and e.get("script"):
                out.append(e["script"])
            elif st == "ok" and e.get("autoload"):
                s = self.autoload_script.get(e["autoload"])
                if s:
                    out.append(s)
        return list(dict.fromkeys(out))

    def _resolve_signal(self, script: str, m: ScriptModel, recv: str | None, sig: str):
        """-> (list of signal-owner scripts, confidence)."""
        scripts, conf = self._script_of_recv(script, m, recv)
        owners = [o for o in (self.find_signal(s, sig) for s in scripts) if o]
        if owners:
            return list(dict.fromkeys(owners)), conf
        if recv is not None and not scripts:
            cands = self._sig_index.get(sig, [])
            if len(cands) == 1:
                return cands, "INFERRED"
            if 1 < len(cands) <= 3:
                return cands, "AMBIGUOUS"
        return [], ""

    def _link_script(self, res: str, m: ScriptModel) -> None:
        g = self.g
        label = m.class_name if m.class_name and m.lang == "gdscript" else PurePosixPath(res).name
        if m.lang == "csharp":
            label = m.class_name or PurePosixPath(res).stem
        g.node(res, "script", label, file=res, line=1, lang=m.lang, class_name=m.class_name,
               extends=m.extends, doc=m.doc, lines=m.lines, tool=m.is_tool or None,
               exports=[f"{e['name']}: {e['type']}" if e["type"] else e["name"] for e in m.exports])
        base = self.script_base(res)
        if base:
            g.edge(res, base, "extends", file=res, line=m.extends_line)
        elif m.extends.startswith(("res://", "uid://")):
            self._check_path(res, m.extends_line, self.res_ref(m.extends), "extends")
        for fname, f in m.funcs.items():
            g.node(_fid(res, fname), "function", f"{fname}()", file=res, line=f["line"],
                   script=res, params=", ".join(f["params"]), static=f["static"] or None)
            g.edge(res, _fid(res, fname), "defines", file=res, line=f["line"])
            # overrides of base-class methods
            if base:
                owner = self.find_func(base, fname)
                if owner:
                    g.edge(_fid(res, fname), _fid(owner, fname), "overrides", file=res, line=f["line"])
        for sname, s in m.signals.items():
            g.node(_sid(res, sname), "signal", sname, file=res, line=s["line"], script=res,
                   params=s["params"])
            g.edge(res, _sid(res, sname), "declares", file=res, line=s["line"])

        def src(func: str) -> str:
            return _fid(res, func) if func and func in m.funcs else res

        # --- autoload / class usage
        for ident, line in m.idents.items():
            if ident in self.cfg.autoloads and ident != m.class_name:
                if self.autoload_script.get(ident) != res:
                    g.edge(res, f"autoload:{ident}", "uses_autoload", file=res, line=line)
            elif ident in self.class_names and self.class_names[ident] != res and self.class_names[ident] != base:
                g.edge(res, self.class_names[ident], "uses_class", file=res, line=line)

        # --- calls
        for c in m.calls:
            scripts, conf = self._script_of_recv(res, m, c["recv"])
            for s in scripts:
                owner = self.find_func(s, c["name"])
                if owner:
                    g.edge(src(c["func"]), _fid(owner, c["name"]), "calls",
                           conf, file=res, line=c["line"])

        # --- signals
        for e in m.emits:
            owners, conf = self._resolve_signal(res, m, e["recv"], e["signal"])
            for o in owners:
                g.edge(src(e["func"]), _sid(o, e["signal"]), "emits", conf, file=res, line=e["line"])
            if not owners and e["recv"] is None and e["signal"] not in m.local_names:
                g.issue("warning", "unknown-signal", res, e["line"],
                        f"{e['signal']}.emit(): no signal '{e['signal']}' is declared in this script or its base classes",
                        f"Declare `signal {e['signal']}` or check the spelling.")
        for c in m.connects:
            owners, conf = self._resolve_signal(res, m, c["recv"], c["signal"])
            target = None
            if c["lambda"]:
                target = src(c["func"])
            elif c["handler"]:
                hs, hconf = self._script_of_recv(res, m, c["hrecv"])
                for s in hs:
                    owner = self.find_func(s, c["handler"])
                    if owner:
                        target = _fid(owner, c["handler"])
                        break
                if target is None and c["hrecv"] is None and c["handler"] not in m.local_names:
                    base_cls = self.engine_base(res)
                    sev = "error" if c["handler"].startswith("_on_") else "warning"
                    if c["handler"] not in _NODE_BUILTIN_METHODS:
                        g.issue(sev, "missing-handler", res, c["line"],
                                f"{c['signal']}.connect({c['handler']}): {c['handler']}() is not defined in this script"
                                + (f" or its base classes (engine base {base_cls})" if base_cls else ""),
                                f"Add `func {c['handler']}(...)` or fix the name.")
            if target is None:
                target = src(c["func"])
            for o in owners:
                g.edge(_sid(o, c["signal"]), target, "connected_to", conf, file=res, line=c["line"],
                       lambda_=c["lambda"] or None)
            if not owners:
                g.edge(src(c["func"]), target, "connected_to", "EXTRACTED", file=res, line=c["line"],
                       signal=c["signal"])

        # --- node paths
        ctxs = self.contexts(res)
        seen_paths: set[tuple[str, str]] = set()
        for r in m.node_refs:
            path = r["path"]
            if not path or (path, r["func"]) in seen_paths:
                continue
            seen_paths.add((path, r["func"]))
            if not ctxs:
                if path.startswith("/root/"):
                    head = path[len("/root/"):].split("/")[0]
                    if head in self.cfg.autoloads:
                        g.edge(src(r["func"]), f"autoload:{head}", "node_path", file=res, line=r["line"], path=path)
                continue
            ok, missing, unknown = [], [], []
            for scene, at in ctxs:
                st, info = self.resolve_path(scene, at, path)
                (ok if st == "ok" else missing if st == "missing" else unknown).append((scene, at, info))
            for scene, at, e in ok:
                g.edge(src(r["func"]), e["id"], "node_path", file=res, line=r["line"], path=path)
            if any(seg.lstrip("%") in m.runtime_names for seg in path.split("/")):
                continue  # node is created and named in code
            if not ok and missing and not unknown:
                sev = "info" if r["optional"] else "warning" if m.builds_nodes else "error"
                _, _, why = missing[0]
                g.issue(sev, "missing-node", res, r["line"],
                        f"node path '{path}' does not resolve: {why}",
                        "Check the scene tree with `nodemap tree <scene>`." +
                        (" (script adds children at runtime, so this may be intentional)" if m.builds_nodes else ""))

        # --- resources
        for r in m.res_refs:
            target = self.res_ref(r["path"])
            dynamic = target.endswith("/") or "." not in PurePosixPath(target).name or "%" in target \
                or "{" in target
            if dynamic:
                continue
            if self._check_path(res, r["line"], target, f"{r['kind']}()" if r["kind"] != "references" else "path"):
                rel = {"preload": "preloads", "load": "loads"}.get(r["kind"], "references")
                if target not in g.nodes:
                    kind = {".tscn": "scene", ".gd": "script", ".cs": "script", ".tres": "resource"}.get(
                        PurePosixPath(target).suffix, "asset")
                    if kind == "asset":
                        g.node(target, "asset", PurePosixPath(target).name, file=target)
                g.edge(src(r["func"]), target, rel, file=res, line=r["line"])

        # --- input actions
        for a in m.actions:
            aid = f"action:{a['name']}"
            if a["name"] not in self.declared_actions:
                if a["name"] in BUILTIN_ACTIONS:
                    g.node(aid, "action", a["name"], builtin=True)
                else:
                    g.node(aid, "action", a["name"], declared_in="nowhere")
                    g.issue("warning" if self.dynamic_actions else "error", "undeclared-action", res, a["line"],
                            f"input action '{a['name']}' is not declared in project.godot [input]"
                            + (" nor registered with InputMap.add_action()" if self.dynamic_actions else ""),
                            "Add it in Project Settings > Input Map (or register it at startup).")
            g.edge(src(a["func"]), aid, "uses_action", file=res, line=a["line"])

        # --- groups
        for gr in m.groups:
            gid = f"group:{gr['name']}"
            g.node(gid, "group", gr["name"])
            rel = {"add": "adds_to_group", "remove": "adds_to_group"}.get(gr["op"], "queries_group")
            g.edge(res, gid, rel, file=res, line=gr["line"])

        # --- legacy syntax
        for lg in m.legacy:
            g.issue("warning", "godot3-syntax", res, lg["line"], lg["msg"])

    # ------------------------------------------------------------ docs
    _DOC_SKIP = {"the", "and", "for", "not", "true", "false", "null", "self", "var", "func", "signal",
                 "extends", "class_name", "return", "if", "else", "elif", "for", "while", "in", "pass",
                 "await", "const", "static", "void", "int", "float", "bool", "string", "new", "this",
                 "public", "private", "override", "using", "namespace", "class", "get", "set", "name", "value"}

    # engine members any script has; `Foo.new()` in a doc is not stale
    _BUILTIN_MEMBERS = {"new", "free", "queue_free", "duplicate", "instantiate", "get", "set", "call",
                        "call_deferred", "emit", "emit_signal", "connect", "disconnect", "is_connected",
                        "get_node", "add_child", "remove_child", "get_parent", "get_tree", "has_method",
                        "set_deferred", "notification", "to_string", "get_class", "is_class", "load"}

    def _resolve_doc_path(self, doc: str, text: str) -> tuple[str | None, str]:
        """-> (res path, confidence) for a path written in a doc."""
        t = text.strip().strip("<>").replace("\\", "/")
        if t.startswith("res://"):
            return (t if self.paths.exists(t) else None), "EXTRACTED"
        doc_dir = PurePosixPath(doc[len("res://"):]).parent
        for cand in (PurePosixPath(t.lstrip("./")) if not t.startswith("../") else None, doc_dir / t):
            if cand is None:
                continue
            parts = _norm(list(cand.parts))
            if parts is None:
                continue
            res = "res://" + "/".join(parts)
            if self.paths.exists(res):
                return res, "EXTRACTED"
        if "/" not in t:
            hits = [f for f in self._basename_index.get(t, [])]
            if len(hits) == 1:
                return hits[0], "INFERRED"
        return None, ""

    def _link_docs(self) -> None:
        g = self.g
        self._basename_index: dict[str, list[str]] = {}
        for f in self.paths.all_files:
            self._basename_index.setdefault(PurePosixPath(f).name, []).append(f)
        scene_by_stem: dict[str, list[str]] = {}
        for sres in self.scenes:
            scene_by_stem.setdefault(PurePosixPath(sres).stem, []).append(sres)
        func_index: dict[str, list[str]] = {}
        for sres, m in self.scripts.items():
            for fname in m.funcs:
                func_index.setdefault(fname.split(".")[-1], []).append(_fid(sres, fname))
        groups = {n["label"] for n in g.nodes.values() if n["kind"] == "group"}

        def named(ident: str) -> list[str]:
            """Class / autoload / scene called *ident*."""
            if ident in self.cfg.autoloads:
                return [f"autoload:{ident}"]
            if ident in self.class_names:
                return [self.class_names[ident]]
            hits = scene_by_stem.get(ident, [])
            return hits if len(hits) == 1 else []

        for res, d in sorted(self.docs.items()):
            g.node(res, "doc", d.title, file=res, line=1, lines=d.lines,
                   sections=[f"{'#' * lv} {t} :{ln}" for lv, t, ln in d.sections[:40]])
            for r in d.refs:
                kind, text, line, sec = r["kind"], r["text"], r["line"], r["section"]
                targets: list[str] = []
                conf = "EXTRACTED"
                if kind == "path":
                    tgt, conf = self._resolve_doc_path(res, text)
                    if tgt:
                        targets = [tgt]
                    elif text.endswith(tuple("." + e for e in GODOT_FILE_EXT + ("md",))):
                        # docs often describe planned work or use example paths: a note, not a warning
                        g.issue("info", "stale-doc-reference", res, line,
                                f"doc mentions {text}, which does not exist" + (f" (section '{sec}')" if sec else ""),
                                "Update the doc, or the file was renamed/deleted.")
                elif kind == "qualified":
                    base = named(text)
                    member = r["member"]
                    if member in GODOT_FILE_EXT or member in ("md", "png", "wav", "ogg", "import", "uid"):
                        continue
                    script = self.autoload_script.get(text) if text in self.cfg.autoloads else (
                        base[0] if base and base[0] in self.scripts else None)
                    found = None
                    if script:
                        owner = self.find_signal(script, member)
                        if owner:
                            found = _sid(owner, member)
                        else:
                            owner = self.find_func(script, member)
                            if owner:
                                found = _fid(owner, member)
                    if found:
                        targets = [found]
                    else:
                        targets = base
                        if script and r["callish"] and member not in self._BUILTIN_MEMBERS:
                            g.issue("info", "stale-doc-reference", res, line,
                                    f"doc mentions {text}.{member}, but {text} has no signal or function '{member}'"
                                    + (f" (section '{sec}')" if sec else ""),
                                    "Update the doc, or the member was renamed/removed.")
                elif kind == "call":
                    hits = func_index.get(text, [])
                    if 1 <= len(hits) <= 3:
                        targets, conf = hits, "INFERRED" if len(hits) == 1 else "AMBIGUOUS"
                elif kind in ("code", "prose"):
                    if len(text) < 3 or text.lower() in self._DOC_SKIP:
                        continue
                    targets = named(text)
                    if kind == "prose":
                        conf = "INFERRED"
                    elif not targets:
                        sigs = self._sig_index.get(text, [])
                        if 1 <= len(sigs) <= 3:
                            targets = [_sid(s, text) for s in sigs]
                            conf = "INFERRED" if len(sigs) == 1 else "AMBIGUOUS"
                        elif text in self.declared_actions:
                            targets = [f"action:{text}"]
                        elif text in groups:
                            targets = [f"group:{text}"]
                for t in targets:
                    if t != res:
                        g.edge(res, t, "mentions", conf, file=res, line=line, section=sec or None)

    def _materialize_targets(self) -> None:
        """Give every file an edge points at (textures, audio, missing files) a node."""
        g = self.g
        for e in g.edges:
            t = e["target"]
            if t in g.nodes or not t.startswith("res://"):
                continue
            name = PurePosixPath(t).name
            if self.paths.exists(t):
                kind = {".tscn": "scene", ".gd": "script", ".cs": "script", ".tres": "resource"}.get(
                    PurePosixPath(t).suffix, "asset")
                g.node(t, kind, name, file=t, external=True if kind != "asset" else None)
            else:
                g.node(t, "missing", name, file=t)

    # ------------------------------------------------------------ project-wide
    def _post_checks(self) -> None:
        g = self.g
        emitted, heard = set(), set()
        for e in g.edges:
            if e["relation"] == "emits":
                emitted.add(e["target"])
            elif e["relation"] == "connected_to" and "::sig:" in e["source"]:
                heard.add(e["source"])
        for nid, n in g.nodes.items():
            if n["kind"] != "signal":
                continue
            if nid not in emitted:
                g.issue("info", "signal-never-emitted", n["file"], n["line"],
                        f"signal {n['label']} is declared but never emitted")
            elif nid not in heard:
                g.issue("info", "signal-never-connected", n["file"], n["line"],
                        f"signal {n['label']} is emitted but nothing connects to it")
        used = {e["target"] for e in g.edges if e["relation"] == "uses_action"}
        for a, (file, line) in self.declared_actions.items():
            if f"action:{a}" not in used and file == "res://project.godot":
                g.issue("info", "unused-action", file, line, f"input action '{a}' is never used by a script")


# ---------------------------------------------------------------- cache

def _cache_key(fp: Path) -> tuple[float, int]:
    st = fp.stat()
    return (st.st_mtime, st.st_size)


def _cache_version() -> str:
    """Cache is only valid for the exact extractor code that produced it."""
    h = hashlib.sha1(__version__.encode())
    here = Path(__file__).parent
    for name in ("godot_text.py", "scenes.py", "scripts.py", "docs.py"):
        h.update((here / name).read_bytes())
    return h.hexdigest()


def _extract_all(root: Path, sources: list[Path], paths, cache_path: Path | None):
    cache: dict = {}
    if cache_path and cache_path.is_file():
        try:
            with cache_path.open("rb") as fh:
                data = pickle.load(fh)
            if data.get("version") == _cache_version():
                cache = data.get("files", {})
        except Exception:
            cache = {}
    scenes, resources, scripts, docs = {}, {}, {}, {}
    new_cache = {}
    hits = 0
    for fp in sources:
        res = paths.to_res(fp)
        key = _cache_key(fp)
        hit = cache.get(res)
        if hit and hit[0] == key:
            model = hit[1]
            hits += 1
        else:
            try:
                if fp.suffix == ".tscn":
                    model = parse_scene(fp, res)
                elif fp.suffix == ".tres":
                    model = parse_resource(fp, res)
                elif fp.suffix == ".gd":
                    model = parse_gdscript(fp, res)
                elif fp.suffix == ".md":
                    model = parse_markdown(fp, res)
                else:
                    model = parse_csharp(fp, res)
            except Exception as exc:  # never let one odd file kill the build
                model = None
                print(f"nodemap: failed to parse {res}: {exc}")
        new_cache[res] = (key, model)
        if model is None:
            continue
        if isinstance(model, SceneModel):
            scenes[res] = model
        elif isinstance(model, ResourceModel):
            resources[res] = model
        elif isinstance(model, DocModel):
            docs[res] = model
        else:
            scripts[res] = model
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as fh:
            pickle.dump({"version": _cache_version(), "files": new_cache}, fh)
    return scenes, resources, scripts, docs, hits


def build(target: Path, include_addons: bool = False, exclude: list[str] | None = None,
          out_dir: Path | None = None, use_cache: bool = True, include_docs: bool = True) -> Graph:
    root = find_project_root(target)
    cfg = parse_project_godot(root / "project.godot")
    paths, sources = walk(root, include_addons=include_addons, exclude=exclude, include_docs=include_docs)
    uids, dup_uids = build_uid_index(paths)
    out = out_dir or (root / OUT_DIR)
    cache_path = out / "cache" / ("extract-addons.pkl" if include_addons else "extract.pkl") if use_cache else None
    scenes, resources, scripts, docs, hits = _extract_all(root, sources, paths, cache_path)
    linker = Linker(root, cfg, paths, uids, scenes, resources, scripts, docs)
    g = linker.link()
    for uid, files in dup_uids.items():
        g.issue("error", "duplicate-uid", files[-1], 1, f"{uid} is used by several files: {', '.join(files)}",
                "Duplicated files keep the original uid. Delete the .uid / uid= line and let Godot regenerate it.")
    fingerprint = hashlib.sha1("".join(sorted(f"{k}{v[0]}" for k, v in
                                              ((paths.to_res(s), _cache_key(s)) for s in sources))).encode()).hexdigest()
    g.meta = {
        "tool": "godot-nodemap", "version": __version__, "project": cfg.name, "project_version": cfg.version,
        "root": str(root), "engine_features": cfg.engine_features, "main_scene": cfg.main_scene,
        "renderer": cfg.renderer, "csharp": bool(cfg.csharp_assembly) or any(s.lang == "csharp" for s in scripts.values()),
        "autoloads": {k: v["path"] for k, v in cfg.autoloads.items()},
        "input_actions": sorted(linker.declared_actions),
        "layer_names": cfg.layer_names, "plugins": cfg.plugins,
        "include_addons": include_addons, "docs": include_docs,
        "files": {"scenes": len(scenes), "resources": len(resources), "scripts": len(scripts), "docs": len(docs)},
        "fingerprint": fingerprint, "cache_hits": hits,
    }
    return g
