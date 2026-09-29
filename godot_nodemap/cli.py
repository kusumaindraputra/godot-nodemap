"""nodemap command line."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__, analyze, check, query
from .build import OUT_DIR, build
from .export_html import to_html
from .graph import Graph
from .project import find_project_root
from .report import generate

COMMANDS = {"build", "update", "query", "path", "explain", "tree", "signal", "check", "serve", "watch",
            "stats", "install", "claude", "agents", "hook-guard", "hook-check", "help"}

WATCH_EXT = {".gd", ".cs", ".tscn", ".tres", ".godot", ".uid", ".import"}


def _out(root: Path, out: str | None) -> Path:
    return Path(out).resolve() if out else root / OUT_DIR


def run_build(target: Path, include_addons: bool = False, exclude: list[str] | None = None,
              out: str | None = None, html: bool = True, quiet: bool = False) -> tuple[Graph, Path]:
    t0 = time.time()
    root = find_project_root(target)
    out_dir = _out(root, out)
    g = build(root, include_addons=include_addons, exclude=exclude, out_dir=out_dir)
    analyze.cluster(g)
    out_dir.mkdir(parents=True, exist_ok=True)
    g.save(out_dir / "graph.json")
    (out_dir / "NODEMAP_REPORT.md").write_text(generate(g), encoding="utf-8")
    if html:
        (out_dir / "nodemap.html").write_text(to_html(g), encoding="utf-8")
    gi = out_dir / ".gitignore"
    if not gi.exists():
        gi.write_text("cache/\n", encoding="utf-8")
    if not quiet:
        st = analyze.stats(g)
        sev = st["issues"]
        f = g.meta["files"]
        print(f"nodemap {__version__}: {g.meta.get('project') or root.name}")
        print(f"  {f['scripts']} scripts, {f['scenes']} scenes, {f['resources']} resources "
              f"-> {st['nodes']} nodes, {st['edges']} edges, {st['communities']} communities "
              f"({time.time() - t0:.1f}s)")
        print(f"  health: {sev.get('error', 0)} errors, {sev.get('warning', 0)} warnings, {sev.get('info', 0)} notes")
        print(f"  wrote {out_dir}/graph.json, NODEMAP_REPORT.md" + (", nodemap.html" if html else ""))
    return g, out_dir


def load_graph(out: str | None = None, auto_build: bool = True) -> Graph:
    root = find_project_root(Path.cwd())
    p = _out(root, out) / "graph.json"
    if not p.exists():
        if not auto_build:
            raise SystemExit(f"nodemap: no graph at {p}. Run `nodemap .` first.")
        print(f"nodemap: no graph yet, building {root} ...", file=sys.stderr)
        g, _ = run_build(root, out=out, quiet=True)
        return g
    return Graph.load(p)


def _rebuild_like_existing(root: Path, out: str | None) -> Graph:
    p = _out(root, out) / "graph.json"
    include_addons = False
    if p.exists():
        try:
            include_addons = bool(json.loads(p.read_text(encoding="utf-8")).get("meta", {}).get("include_addons"))
        except Exception:
            pass
    g, _ = run_build(root, include_addons=include_addons, out=out, quiet=True)
    return g


# ---------------------------------------------------------------- hooks

_EXTS = (".gd", ".cs", ".tscn", ".tres", "project.godot")


def hook_guard() -> int:
    root = find_project_root(Path.cwd())
    if (root / OUT_DIR / "graph.json").exists():
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": (
            "nodemap-out/graph.json exists. For Godot project questions prefer `nodemap query \"<question>\"`, "
            "`nodemap explain <name>`, `nodemap tree <Scene>` or `nodemap signal <name>` before grepping; "
            "grep only to find exact lines to edit.")}}))
    return 0


def hook_check() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path") or ""
    if not fp.endswith(_EXTS):
        return 0
    path = Path(fp)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or ".") / path
    root = find_project_root(path.parent)
    if not (root / "project.godot").exists():
        return 0
    g = _rebuild_like_existing(root, None)
    try:
        res = "res://" + path.resolve().relative_to(root).as_posix()
    except ValueError:
        return 0
    issues = check.filter_issues(g, [res], "warning")
    errors = [i for i in issues if i["severity"] == "error"]
    if errors:
        sys.stderr.write(f"nodemap check found problems after editing {res}:\n" + check.format_text(issues) +
                         "\nFix the errors (use `nodemap tree <Scene>` / `nodemap explain <name>` to look things up).\n")
        return 2
    if issues:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": "nodemap check warnings:\n" + check.format_text(issues)}}))
    return 0


# ---------------------------------------------------------------- watch

def _snapshot(root: Path) -> dict:
    import os
    snap = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".") and d != OUT_DIR]
        for f in fn:
            if Path(f).suffix in WATCH_EXT:
                p = Path(dp) / f
                try:
                    snap[str(p)] = p.stat().st_mtime
                except OSError:
                    pass
    return snap


def watch(root: Path, include_addons: bool, interval: float) -> None:
    root = find_project_root(root)
    run_build(root, include_addons=include_addons)
    prev = _snapshot(root)
    print(f"nodemap: watching {root} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(interval)
            cur = _snapshot(root)
            if cur != prev:
                prev = cur
                run_build(root, include_addons=include_addons)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if argv and argv[0] in ("-V", "--version"):
        print(__version__)
        return 0
    cmd = argv[0] if argv and argv[0] in COMMANDS else "build"
    rest = argv[1:] if argv and argv[0] in COMMANDS else argv
    p = argparse.ArgumentParser(prog=f"nodemap {cmd}" if cmd != "build" else "nodemap",
                                description="Godot 4 project -> knowledge graph for AI coding assistants.")
    p.add_argument("--out", help=f"output directory (default <project>/{OUT_DIR})")

    if cmd in ("build", "update", "watch"):
        p.add_argument("path", nargs="?", default=".")
        p.add_argument("--include-addons", action="store_true", help="also map res://addons/")
        p.add_argument("--exclude", action="append", default=[], help="skip a folder (repeatable), e.g. --exclude tests")
        p.add_argument("--no-html", action="store_true")
        p.add_argument("--interval", type=float, default=2.0)
        a = p.parse_args(rest)
        if cmd == "watch":
            watch(Path(a.path), a.include_addons, a.interval)
            return 0
        include_addons = a.include_addons
        if cmd == "update" and not include_addons:
            root = find_project_root(Path(a.path))
            gp = _out(root, a.out) / "graph.json"
            if gp.exists():
                include_addons = bool(json.loads(gp.read_text(encoding="utf-8")).get("meta", {}).get("include_addons"))
        run_build(Path(a.path), include_addons, a.exclude, a.out, not a.no_html, quiet=False)
        return 0
    if cmd == "query":
        p.add_argument("question", nargs="+")
        p.add_argument("--budget", type=int, default=2000)
        p.add_argument("--dfs", action="store_true")
        p.add_argument("--depth", type=int, default=2)
        a = p.parse_args(rest)
        print(query.query(load_graph(a.out), " ".join(a.question), a.budget, a.dfs, a.depth))
        return 0
    if cmd == "path":
        p.add_argument("source")
        p.add_argument("target")
        a = p.parse_args(rest)
        print(query.shortest_path(load_graph(a.out), a.source, a.target))
        return 0
    if cmd == "explain":
        p.add_argument("name", nargs="+")
        a = p.parse_args(rest)
        print(query.explain(load_graph(a.out), " ".join(a.name)))
        return 0
    if cmd == "tree":
        p.add_argument("scene")
        p.add_argument("--expand", type=int, default=0, help="also expand instanced scenes N levels deep")
        a = p.parse_args(rest)
        print(query.scene_tree(load_graph(a.out), a.scene, a.expand))
        return 0
    if cmd == "signal":
        p.add_argument("name")
        a = p.parse_args(rest)
        print(query.signal(load_graph(a.out), a.name))
        return 0
    if cmd == "check":
        p.add_argument("files", nargs="*", help="only report issues in these files")
        p.add_argument("--severity", choices=["error", "warning", "info"], default="warning")
        p.add_argument("--code", action="append", help="only these issue codes")
        p.add_argument("--json", action="store_true")
        p.add_argument("--no-rebuild", action="store_true", help="use the existing graph.json as is")
        a = p.parse_args(rest)
        root = find_project_root(Path.cwd())
        g = load_graph(a.out, auto_build=True) if a.no_rebuild else _rebuild_like_existing(root, a.out)
        issues = check.filter_issues(g, a.files, a.severity, a.code)
        print(check.format_json(issues) if a.json else check.format_text(issues))
        return 1 if any(i["severity"] == "error" for i in issues) else 0
    if cmd == "stats":
        a = p.parse_args(rest)
        print(json.dumps(analyze.stats(load_graph(a.out)), indent=1))
        return 0
    if cmd == "serve":
        p.add_argument("--graph", help="path to graph.json")
        a = p.parse_args(rest)
        from .serve import serve
        root = find_project_root(Path.cwd())
        gp = Path(a.graph) if a.graph else _out(root, a.out) / "graph.json"
        if not gp.exists():
            run_build(root, out=a.out, quiet=True)
        serve(gp)
        return 0
    if cmd == "install":
        p.add_argument("--project", action="store_true", help="install into ./.claude instead of ~/.claude")
        a = p.parse_args(rest)
        from .install import install_skill
        dest = install_skill(find_project_root(Path.cwd()) if a.project else None)
        print(f"nodemap skill installed -> {dest}\nIn a Godot project run `nodemap claude install` to add CLAUDE.md rules and hooks.")
        return 0
    if cmd in ("claude", "agents"):
        p.add_argument("action", choices=["install", "uninstall"])
        p.add_argument("--mcp", action="store_true", help="also register the MCP server in .mcp.json")
        a = p.parse_args(rest)
        from . import install as inst
        root = find_project_root(Path.cwd())
        if cmd == "agents":
            done = inst.agents_install(root) if a.action == "install" else (
                ["AGENTS.md section removed"] if inst._remove_section(root / "AGENTS.md") else [])
        else:
            done = inst.claude_install(root, a.mcp) if a.action == "install" else inst.claude_uninstall(root)
        for d in done or ["nothing to do"]:
            print("  " + d)
        return 0
    if cmd == "hook-guard":
        return hook_guard()
    if cmd == "hook-check":
        return hook_check()
    if cmd == "help":
        print(__doc__ + "\n\n" + HELP)
        return 0
    return 0


HELP = """usage:
  nodemap [path] [--include-addons] [--exclude DIR] [--no-html]   build graph + report + html
  nodemap update [path]                 rebuild (cached, fast)
  nodemap watch [path]                  rebuild on every change
  nodemap query "<question>" [--budget N] [--dfs]
  nodemap explain <name>                one script/scene/node/signal/autoload/action in detail
  nodemap path <A> <B>                  shortest chain of relations
  nodemap tree <Scene> [--expand N]     scene node tree
  nodemap signal <name>                 declarations, emitters, listeners
  nodemap check [files] [--severity error|warning|info] [--json]
  nodemap stats
  nodemap serve                         MCP stdio server
  nodemap install [--project]           Claude Code skill
  nodemap claude install [--mcp]        CLAUDE.md rules + hooks (+ .mcp.json)
  nodemap agents install                AGENTS.md rules (Codex, Cursor, Gemini CLI, ...)"""


if __name__ == "__main__":
    sys.exit(main())
