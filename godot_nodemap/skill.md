---
name: nodemap
description: "Use for any question about a Godot 4 project (GDScript or C#): scene trees, node paths, signals, autoloads, input actions, which script does what, or what breaks if something changes. Especially when nodemap-out/ exists, query the nodemap graph first instead of grepping. Also use before writing $NodePath/get_node() code, wiring signals, or editing .tscn files, and run `nodemap check` after edits."
---

# /nodemap

Godot 4 project -> knowledge graph. Understands what generic code tools miss: `.tscn` scene trees,
instanced and inherited scenes, `[connection]` signal wiring, `$NodePath` / `%Unique` resolution,
autoloads, `class_name`/`extends` chains, `preload()`, input actions, groups and uids, plus the
Markdown docs (GDD, ADRs, specs) and which code they talk about.
Outputs `nodemap-out/graph.json`, `nodemap-out/NODEMAP_REPORT.md` and `nodemap-out/nodemap.html`.

## Usage

```
/nodemap                         # build (or refresh) the graph for the current project
/nodemap <path>                  # build for a specific project folder
/nodemap query "<question>"      # scoped subgraph for a question (BFS; --dfs to trace)
/nodemap explain "<name>"        # one script / scene / node / signal / autoload / action in detail
/nodemap path "<A>" "<B>"        # shortest chain of relations between two things
/nodemap tree <Scene>            # node tree of a scene (types, scripts, instances, groups, connections)
/nodemap signal <name>           # who declares, emits and listens to a signal
/nodemap check [files...]        # Godot-aware lint (node paths, connections, actions, res:// paths, uids, Godot 3 syntax)
```

## What you must do when invoked

If the user passed `--help`, print the Usage block above and stop.

**Fast path:** if `nodemap-out/graph.json` exists and the user asked a question about the project,
run `nodemap query "<question>"` (or `explain` / `tree` / `signal` / `path` when the question is about
one thing) and answer from its output. Do not rebuild first; the graph is kept fresh by hooks.

Otherwise:

1. Run `nodemap <path>` (default `.`). It finds `project.godot` itself. Add `--include-addons` only if
   the user cares about `addons/`.
2. Read `nodemap-out/NODEMAP_REPORT.md` and give the user a short summary: autoloads, main scene,
   god nodes, the biggest communities, and the health check (errors first).
3. Tell the user they can open `nodemap-out/nodemap.html` in a browser for the interactive graph.

## Rules while coding in a Godot project

- Before writing `$Path`, `%Name` or `get_node("...")`, run `nodemap tree <Scene>` for the scene the
  script is attached to (`nodemap explain <script>` shows `has_script` edges from scene nodes).
  Never guess node paths.
- Before connecting or emitting a signal, run `nodemap signal <name>` to see its parameters and
  existing listeners. Prefer `signal.connect(callable)` / `signal.emit(args)` (Godot 4 syntax).
- Use autoloads by their registered name (see the report); do not give an autoload script a
  `class_name` equal to its autoload name.
- Do not invent `uid://` values in `.tscn` / `.tres` files. Omit `uid="..."` on `ext_resource`
  lines you write by hand; Godot falls back to the path and assigns a real uid on next save.
- After editing `.gd`, `.cs`, `.tscn` or `.tres` files run `nodemap check <changed files>` and fix
  every error it reports before you finish. `nodemap update` refreshes the graph (cached, fast).
- Before changing a system, `nodemap explain <name>` also lists the design docs that mention it
  (`<- mentions`). Read the relevant section, and update the doc if your change makes it stale.
- `INFERRED` / `AMBIGUOUS` edges are best guesses (resolved through a typed variable or a unique
  signal name). Verify them in the source before relying on them.
