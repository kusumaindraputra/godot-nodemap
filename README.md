# godot-nodemap

**Turn a Godot 4 project into a knowledge graph your AI coding assistant can query.**
Inspired by [graphify](https://github.com/Graphify-Labs/graphify), built specifically for Godot.

Generic code-graph tools read `.gd` files as text. They miss where a Godot project's structure
actually lives: `.tscn` scene trees, instanced and inherited scenes, signal connections made in the
editor, `$NodePath` lookups, autoloads, the Input Map, groups and `uid://` references. So when you
vibe-code a Godot game, the AI guesses node paths, wires signals to methods that don't exist,
invents `uid://` values and mixes Godot 3 syntax into Godot 4 code. `nodemap` gives the assistant
the real structure and checks its edits.

```
nodemap .
  380 scripts, 16 scenes, 134 resources -> 5697 nodes, 15377 edges, 23 communities (1.3s)
  health: 1 errors, 5 warnings, 14 notes
  wrote nodemap-out/graph.json, NODEMAP_REPORT.md, nodemap.html
```

- **`nodemap-out/NODEMAP_REPORT.md`**: a one-page map covering the main scene, autoloads and their
  signals, input actions, groups, the health check, god nodes, communities (subsystems), the signal
  bus (who emits and who listens), every scene tree, surprising cross-subsystem links and
  suggested questions.
- **`nodemap-out/graph.json`**: the full graph (nodes, edges, communities, issues).
- **`nodemap-out/nodemap.html`**: an interactive viewer. You can search, filter by kind, colour by
  community, click any node to see its connections, and browse issues.

It works fully offline with no LLM calls. It is pure Python, and its only dependency is `networkx`.
GDScript and C# are both supported.

## Install

```bash
pipx install git+https://github.com/kusumaindraputra/godot-nodemap
# or: uv tool install git+https://github.com/kusumaindraputra/godot-nodemap
```

Then, inside your Godot project:

```bash
nodemap .                    # build graph + report + html (finds project.godot itself)
nodemap claude install       # CLAUDE.md rules + Claude Code hooks (add --mcp for the MCP server)
nodemap install              # optional: global /nodemap skill in ~/.claude/skills
nodemap agents install       # AGENTS.md rules for Codex, Cursor, Gemini CLI, Copilot, ...
```

## Commands

| Command | What it gives you |
|---|---|
| `nodemap [path] [--include-addons] [--exclude DIR]` | Build (cached, ~1s on a 400-script project) |
| `nodemap update` | Rebuild with the same options (only changed files are re-parsed) |
| `nodemap watch` | Rebuild on every change |
| `nodemap query "what happens when the player dies"` | Best-matching entities + their neighbourhood, each edge with `file:line` (`--budget`, `--dfs`) |
| `nodemap explain PlayerController` | One script / scene / node / function / signal / autoload / action with all its relations and issues |
| `nodemap path Coin HUD` | Shortest chain of relations between two things |
| `nodemap tree Main --expand 1` | Scene tree: types, scripts, instances, `%unique` names, groups, signal connections |
| `nodemap signal player_died` | Where a signal is declared, who emits it, who listens (code **and** `.tscn`) |
| `nodemap check [files] [--severity info] [--json]` | Godot-aware lint (exit code 1 on errors) |
| `nodemap serve` | MCP stdio server |

Example (`nodemap query "what happens when the player dies"`):

```
Seeds:
- [signal] player_died in health_and_damage.gd (src/systems/health_and_damage.gd:62)
Edges:
- health_and_damage.gd::player_died --connected_to--> game_state_manager.gd._on_player_died()  @src/core/game_state_manager.gd:92
- health_and_damage.gd::player_died --connected_to--> PlayerController._on_player_died()  @src/gameplay/player_controller.gd:181
- health_and_damage.gd::player_died --connected_to--> CombatHUD._on_player_died()  @src/ui/combat_hud.gd:344
- health_and_damage.gd.apply_damage() --emits--> health_and_damage.gd::player_died  @src/systems/health_and_damage.gd:319
```

## `nodemap check`: catches the mistakes AI makes in Godot

| Code | Severity | Example |
|---|---|---|
| `missing-node` | error | `$Weapon` but the scene the script is attached to has no `Weapon` (resolves through instanced and inherited scenes, `%Unique` names and subclass attachments) |
| `missing-method` | error | `.tscn` `[connection ... method="_on_player_dead"]` but the script has no such function |
| `connection-missing-node` | error | `[connection from="Foo/Bar"]` where the node is gone |
| `missing-handler` | error | `died.connect(_on_player_gone)` with no `_on_player_gone()` |
| `unknown-signal` | warning | `hitt.emit()` for a signal that is never declared |
| `undeclared-action` | error | `Input.is_action_pressed("crouch")` and `crouch` is not in the Input Map (also understands actions registered with `InputMap.add_action`) |
| `missing-resource` | error | `preload("res://scenes/Bullet.tscn")` that doesn't exist |
| `case-mismatch` | error | `res://Scenes/HUD.tscn` vs `res://scenes/HUD.tscn`: works in the editor on Windows, **breaks exported builds** |
| `invalid-uid` / `unknown-uid` / `stale-path` / `duplicate-uid` | warning / error | Hand-written or AI-invented `uid://b_player_scene`, which Godot silently ignores |
| `autoload-class-name-conflict` | error | Autoload `GameState` whose script also says `class_name GameState` |
| `duplicate-class-name`, `missing-autoload`, `missing-main-scene` | error | |
| `godot3-syntax` | warning | `yield`, `export var`, `onready var`, `setget`, `.instance()`, `KinematicBody2D`, `move_and_slide(velocity)`, ... |
| `signal-never-emitted`, `signal-never-connected`, `unused-action` | info | Dead wiring |

Nodes created at runtime (`child.name = "HitArea"`) and `get_node_or_null()` / `has_node()` lookups
are downgraded, so dynamic code doesn't drown you in false positives.

## Claude Code integration

`nodemap claude install` does what `graphify claude install` does, adapted for Godot:

1. **`CLAUDE.md` section**: tells the agent to query the graph first, check the scene tree before
   writing node paths, never invent uids, and run `nodemap check` after edits.
2. **PreToolUse hook (Grep|Glob)**: reminds the agent that the graph exists before it greps.
3. **PostToolUse hook (Edit|Write|MultiEdit)**: after every edit to a `.gd` / `.cs` / `.tscn` / `.tres`
   file it refreshes the graph (incremental) and runs `check` on that file. Errors are fed straight
   back to the agent (exit code 2), so it fixes a broken `$NodePath` or signal connection *before*
   you run the game.
4. `--mcp` also registers the MCP server in `.mcp.json`.

`nodemap claude uninstall` removes all of it and leaves the rest of your `CLAUDE.md` alone.

### MCP tools

`query_graph`, `get_node`, `shortest_path`, `get_scene_tree`, `find_signal`, `check_project`,
`project_info`, `graph_stats`. The server reloads `graph.json` when it changes, so hook rebuilds are
picked up live.

```json
{ "mcpServers": { "nodemap": { "command": "nodemap", "args": ["serve"] } } }
```

## The graph

**Node kinds:** `scene`, `node` (a node inside a scene), `script`, `function`, `signal`,
`autoload`, `action` (input action), `group`, `resource` (`.tres`), `asset` (textures, audio, ...),
`missing`.

**Relations:** `contains`, `has_child`, `instances`, `inherits_scene`, `has_script`, `extends`,
`defines`, `declares`, `overrides`, `calls`, `emits`, `connected_to`, `node_path`, `uses_autoload`,
`uses_class`, `preloads`, `loads`, `references`, `uses`, `uses_action`, `in_group`,
`adds_to_group`, `queries_group`, `autoloads`.

**Confidence** (same idea as graphify):

- `EXTRACTED` means the relation is written in the source: a `[connection]`, `Events.died.emit()`, or
  `$Sprite2D` resolved in the attached scene.
- `INFERRED` means it was resolved through a typed variable (`var player: Player`, then
  `player.died.connect(...)`) or through a unique signal name.
- `AMBIGUOUS` means several scripts declare a signal with that name.

Communities are Louvain clusters over the file-level graph, labelled by dominant folder and hub.
God nodes are ranked by how many *other files* touch them.

## How it works

```
walk() -> extract per file (cached by mtime) -> link across files -> cluster -> report / html / json
```

- `godot_text.py` parses Godot's ConfigFile format (`.tscn`, `.tres`, `project.godot`), including
  multi-line values.
- `scenes.py` extracts scene trees, instances, inheritance, `[connection]`s, groups and unique names.
- `scripts.py` does regex- and indentation-based extraction for GDScript and C# (declarations, node
  paths, connect/emit in every Godot 4 form, `preload`/`load`, input actions, groups, calls, typed
  variables and Godot 3 leftovers).
- `build.py` holds the Godot semantics: uid resolution, `class_name`/`extends` chains, the effective
  scene tree (inherited and instanced scenes), the contexts a script runs in, node-path resolution
  and signal and call resolution. It also collects the issues.

## Limitations

- Static analysis: nodes created purely in code, `get_node(some_variable)` and signals connected by
  name strings built at runtime can't be resolved.
- Engine classes aren't modelled (no built-in signal or method list). Connections to built-in
  signals like `pressed` are recorded by name.
- `res://addons/` is skipped unless you pass `--include-addons`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

`tests/fixtures/demo_game` is a small Godot project (GDScript + C#) with deliberately planted bugs.

## License

MIT
