# godot-nodemap

**Turn a Godot 4 project into a knowledge graph your AI coding assistant can query, and lint the
mistakes AI makes in Godot code.**

Inspired by [graphify](https://github.com/Graphify-Labs/graphify), built specifically for Godot
(GDScript **and** C#). Fully offline, no LLM calls, pure Python with a single dependency
(`networkx`).

```
$ nodemap .
nodemap 0.1.0: Demo Game
  7 scripts, 4 scenes, 1 resources, 2 docs -> 56 nodes, 90 edges, 6 communities (0.0s)
  health: 9 errors, 3 warnings, 7 notes
  wrote nodemap-out/graph.json, NODEMAP_REPORT.md, nodemap.html
```

---

## Contents

- [Why](#why)
- [What you get](#what-you-get)
- [Install](#install)
- [Quick start (5 minutes)](#quick-start-5-minutes)
- [A tour of the commands](#a-tour-of-the-commands)
- [Markdown docs](#markdown-docs)
- [Using it with AI assistants](#using-it-with-ai-assistants)
- [`nodemap check` reference](#nodemap-check-reference)
- [The graph model](#the-graph-model)
- [Options](#options)
- [How it works](#how-it-works)
- [Compared with graphify](#compared-with-graphify)
- [Limitations and FAQ](#limitations-and-faq)
- [Development](#development)

---

## Why

Most of a Godot project's structure does not live in the code. It lives in `.tscn` scene trees,
signal connections made in the editor, `project.godot` (autoloads, Input Map, layers) and `uid://`
references. Generic code-graph tools and plain grep only see `.gd` text. An AI assistant working
from text alone keeps making the same mistakes:

| What the AI does | Why it happens | What nodemap gives it |
|---|---|---|
| Writes `$Player/Weapon` for a node that doesn't exist | It can't see the scene tree | `nodemap tree Player` shows the real tree, and `check` flags the bad path |
| Connects `died` to `_on_player_dead` when the method is `_on_player_died` | Editor connections live in `.tscn` | `[connection]`s are resolved to functions, and a missing method is an error |
| Uses `Input.is_action_pressed("crouch")` | It doesn't know the Input Map | The report lists every action, and `check` flags undeclared ones |
| Writes `uid="uid://my_player_script"` in a `.tscn` | It invents ids | `check` flags invalid, unknown and duplicate uids |
| Types `res://Scenes/HUD.tscn` for `res://scenes/HUD.tscn` | It doesn't notice case | `check` flags it (works in the editor on Windows, breaks in exports) |
| Writes `yield(...)`, `export var` or `KinematicBody2D` | Godot 3 habits | `check` flags Godot 3 syntax |
| Greps 20 files to learn who reacts to `player_died` | Signal wiring is spread across code and scenes | `nodemap signal player_died` gives emitters and listeners with `file:line` |
| Ignores (or trusts outdated) design docs | Nothing links the GDD / ADRs to the code | Markdown docs are linked to the scripts, signals and scenes they mention; `explain` shows them, and stale references are flagged |

## What you get

Running `nodemap .` in a Godot project writes three files to `nodemap-out/`:

| File | For | Contents |
|---|---|---|
| `NODEMAP_REPORT.md` | Humans and LLMs (one-page overview) | Main scene, autoloads and their signals, input actions, groups, named layers, the health check, god nodes, communities (subsystems), the signal bus, every scene tree, surprising cross-subsystem links, docs and what they cover, suggested questions |
| `graph.json` | Tools, the MCP server, your own scripts | All nodes, edges, communities and issues |
| `nodemap.html` | Exploring visually | Interactive graph: search, filter by kind, colour by community, click a node to see its connections, and a browsable issue list |

A `cache/` folder is also created (ignored by the generated `.gitignore`), so later runs only
re-parse the files that changed.

## Install

Requires Python 3.10+. Godot does **not** need to be installed. nodemap reads the project files
directly.

```bash
pipx install git+https://github.com/kusumaindraputra/godot-nodemap
# or
uv tool install git+https://github.com/kusumaindraputra/godot-nodemap
# or, inside a virtualenv
pip install git+https://github.com/kusumaindraputra/godot-nodemap
```

Check it works:

```bash
nodemap --version
```

## Quick start (5 minutes)

The repository ships a small demo game with some bugs planted on purpose, so you can try every
command before pointing nodemap at your own project.

```bash
git clone https://github.com/kusumaindraputra/godot-nodemap
cd godot-nodemap/tests/fixtures/demo_game

nodemap .                          # 1. build the graph
open nodemap-out/nodemap.html      # 2. look around (xdg-open on Linux, start on Windows)
nodemap tree Main --expand 1       # 3. see a scene tree
nodemap check                      # 4. see what's broken
nodemap query "what happens when the player dies"   # 5. ask a question
```

For your own game:

```bash
cd path/to/your-godot-project      # any folder inside it works, nodemap finds project.godot
nodemap .
nodemap claude install             # if you use Claude Code (see below)
```

## A tour of the commands

| Command | What it answers |
|---|---|
| `nodemap [path]` | Build or refresh the graph, report and HTML |
| `nodemap update` | Incremental rebuild (keeps `--include-addons` from the last build) |
| `nodemap watch` | Rebuild whenever a project file changes |
| `nodemap tree <Scene> [--expand N]` | "What nodes does this scene have?" |
| `nodemap signal <name>` | "Who emits this signal, and who listens?" |
| `nodemap explain <name>` | "Tell me everything about this script/scene/node/function/signal/autoload/action/doc", including which docs mention it |
| `nodemap query "<question>" [--budget N] [--dfs]` | "What's relevant to this question?" |
| `nodemap path <A> <B>` | "How does A reach B?" |
| `nodemap check [files] [--severity] [--json]` | "What's broken?" (exit code 1 when there are errors) |
| `nodemap stats` | Counts by node kind, relation and confidence |
| `nodemap serve` | MCP server over stdio |

Names are matched loosely. `Main`, `Main.tscn` and `res://scenes/Main.tscn` all work, and a query
for "player dies" finds `player_died`.

### `nodemap tree`: scene trees, including instanced scenes

```
$ nodemap tree Main --expand 1
# Main.tscn (res://scenes/Main.tscn)  [main scene]
Main (Node2D)  script=main.gd   :8
├─ Player (CharacterBody2D)  instance=Player.tscn  hit->main.gd._on_player_hit()  died->Main.tscn:.   :11
│     Player (CharacterBody2D)  script=Player  group=player   :8
│     ├─ Sprite2D (Sprite2D)   :11
│     ├─ AnimationPlayer (AnimationPlayer)   :13
│     ├─ CollisionShape2D (CollisionShape2D)   :15
│     └─ %Hurtbox (Area2D)   :18
├─ HUD (CanvasLayer)  instance=HUD.tscn   :14
│     HUD (CanvasLayer)  script=hud.gd   :5
│     └─ ScoreLabel (Label)   :8
└─ Coins (Node2D)  group=collectibles   :16
   └─ Coin (Area2D)  instance=Coin.tscn   :18
         Coin (Area2D)  script=Coin   :5
         └─ Sprite2D (Sprite2D)   :8
```

Every line ends with the line number in the `.tscn` file. The tree also shows `%Unique` names,
groups and editor signal connections (`hit->main.gd._on_player_hit()`). A connection whose target
method doesn't exist falls back to the node (`died->Main.tscn:.`), which is a hint that
something is wrong.

### `nodemap signal`: the signal bus

```
$ nodemap signal coin_collected
# signal coin_collected(amount: int) declared in events.gd (autoload/events.gd:5)
emitted by (0):
listeners (2):
  - hud.gd._on_coin_collected() @scripts/hud.gd:7
  - main.gd._on_coin_collected() @scripts/main.gd:7
```

Signal wiring is collected from `.tscn` `[connection]`s and from every Godot 4 code form:
`sig.connect(f)`, `Autoload.sig.connect(f)`, `$Node.sig.connect(f)`, `typed_var.sig.connect(f)`,
lambdas, `connect("sig", Callable(...))`, `sig.emit()`, `emit_signal("sig")`, and in C#
`Sig += Handler`, `Connect(SignalName.X, ...)` and `EmitSignal(SignalName.X)`.

### `nodemap explain`: everything about one thing

```
$ nodemap explain Player
# [script] Player (scripts/player.gd:1)
class_name: Player
extends: CharacterBody2D
lang: gdscript
doc: The player character.
exports: speed: float
issues:
  - error missing-node @scripts/player.gd:18: node path 'Weapon' does not resolve: no node 'Weapon' in res://scenes/Player.tscn (parent '.' has: AnimationPlayer, CollisionShape2D, Hurtbox, Sprite2D)
  ...
<- has_script (1)
  - Player.tscn:. @scenes/Player.tscn:8
declares -> (2)
  - Player::hit @scripts/player.gd:5
  - Player::died @scripts/player.gd:6
node_path -> (2)
  - Player.tscn:Sprite2D @scripts/player.gd:12
  - Player.tscn:AnimationPlayer @scripts/player.gd:13
uses_autoload -> (1)
  - Events @scripts/player.gd:34
```

### `nodemap query`: a scoped subgraph for a question

```
$ nodemap query "what happens when the player dies" --budget 400
Seeds:
- [signal] died in Player (scripts/player.gd:6)
- [signal] player_died in events.gd (autoload/events.gd:4)
- [script] Player (scripts/player.gd:1)
Edges:
- Player.take_damage() --emits--> Player::died  @scripts/player.gd:33
- Player.take_damage() --emits--> events.gd::player_died  @scripts/player.gd:34
- events.gd::player_died --connected_to--> hud.gd._ready()  @scripts/hud.gd:8
- Player --node_path--> Player.tscn:Sprite2D  @scripts/player.gd:12
...
```

Seeds are the entities that best match the question. The command then walks outward from them,
listing signal, call and node-path edges first and structural edges last, and stops at
`--budget` tokens so the output fits in an LLM context.

### `nodemap check`: the linter

```
$ nodemap check
scenes/Main.tscn:21: error [missing-method] [connection signal="died" from="Player" to="."]: method _on_player_dead() is not defined in res://scripts/main.gd (or its base classes; engine base Node2D)
    hint: Add `func _on_player_dead(...)` to main.gd or fix the connection.
scripts/player.gd:9: error [case-mismatch] preload(): res://Scenes/HUD.tscn differs in case from the real file res://scenes/HUD.tscn
    hint: Works in the editor on Windows/macOS but breaks in exported builds. Fix the casing.
scripts/player.gd:18: error [missing-node] node path 'Weapon' does not resolve: no node 'Weapon' in res://scenes/Player.tscn (parent '.' has: AnimationPlayer, CollisionShape2D, Hurtbox, Sprite2D)
scripts/Coin.cs:21: error [undeclared-action] input action 'interact' is not declared in project.godot [input]
scenes/Player.tscn:1: warning [invalid-uid] 1 uid(s) are not valid Godot uids and are ignored by the engine: uid://my_player_script
scripts/player.gd:39: warning [godot3-syntax] Godot 3 `yield()` - use `await signal` in Godot 4
...
nodemap check: 9 errors, 3 warnings, 0 notes
```

- `nodemap check scripts/player.gd` limits the report to issues in (or pointing at) that file.
- `--severity info` also shows notes.
- `--json` gives machine-readable output.
- The exit code is 1 when there are errors, so `check` can gate CI.

## Markdown docs

Game projects keep a lot of knowledge in Markdown: game design documents, ADRs, feature specs,
READMEs, TODO lists. nodemap maps every `*.md` in the project (also inside `.gdignore` folders,
where docs usually live) and links each doc to the code it talks about, fully offline and without
an LLM:

| Written in the doc | Linked to | Confidence |
|---|---|---|
| A path: `res://scripts/player.gd`, `scripts/player.gd`, `../scripts/player.gd`, `[link](../scripts/player.gd)` | That file | EXTRACTED |
| A bare file name: `Main.tscn` | That file, if the name is unique | INFERRED |
| `Class.member` / `Autoload.member`: `Player.take_damage()`, `Events.player_died.emit()` | That function or signal | EXTRACTED |
| A `class_name`, autoload or scene name in `` `code` `` | That script / autoload / scene | EXTRACTED |
| The same name in prose (CamelCase only, e.g. HUD, GameState) | That script / autoload / scene | INFERRED |
| A signal, input action or group name in `` `code` `` | That signal / action / group | INFERRED (AMBIGUOUS if several signals share the name) |
| `some_function()` in `` `code` `` | Functions with that name (at most 3) | INFERRED / AMBIGUOUS |
| A link to another `.md` | That doc | EXTRACTED |

Every link is a `mentions` edge that records the doc line and the heading it sits under:

```
$ nodemap explain take_damage
...
<- mentions (1)
  - Demo Game design § Player @docs/design.md:6

$ nodemap explain design.md
# [doc] Demo Game design (docs/design.md:1)
sections:
  # Demo Game design :1
  ## Player :3
  ## HUD :13
  ## Removed :17
mentions -> (10)
  - Player § Player @docs/design.md:5
  - Player.take_damage() § Player @docs/design.md:6
  - events.gd::player_died § Player @docs/design.md:6
  - jump § Player @docs/design.md:7
  ...
```

**Stale references.** A path that doesn't exist, or a `Class.method()` / `Autoload.signal.emit()`
whose member is gone, is reported as `stale-doc-reference`:

```
$ nodemap check --severity info --code stale-doc-reference
docs/design.md:19: info [stale-doc-reference] doc mentions res://scripts/dash.gd, which does not exist (section 'Removed')
docs/design.md:20: info [stale-doc-reference] doc mentions Events.player_respawned, but Events has no signal or function 'player_respawned' (section 'Removed')
docs/design.md:21: info [stale-doc-reference] doc mentions notes/old.md, which does not exist (section 'Removed')
```

These are **notes**, not warnings. Docs often describe planned work or use example paths, so they
never fail `check`. The report shows a per-doc count, and the Claude Code hook shows them when you
edit that doc.

**Kept out of the way:**

- Docs don't count toward god nodes or surprising connections.
- Each doc joins the community of the code it mentions most, so docs don't split the community list.
- Plain lowercase words in prose are never matched. Only `code`, paths and CamelCase names are.
- The block that `nodemap claude install` writes into `CLAUDE.md` / `AGENTS.md` is ignored.
- `--no-docs` turns the feature off; `update` and the hooks remember the choice.

## Using it with AI assistants

### Claude Code

```bash
nodemap install            # optional: a global /nodemap skill in ~/.claude/skills/nodemap
nodemap claude install     # per project (run inside the Godot project)
nodemap claude install --mcp   # same, plus the MCP server in .mcp.json
```

`nodemap claude install` sets up:

1. **A section in `CLAUDE.md`.** It tells the agent to query the graph before grepping, check
   `nodemap tree` before writing node paths, never invent uids, and run `nodemap check` after
   edits. Your existing `CLAUDE.md` content is kept.
2. **A PreToolUse hook on Grep/Glob.** It reminds the agent that the graph exists.
3. **A PostToolUse hook on Edit/Write/MultiEdit.** This is the key piece: every edit is checked
   right away.

```
Claude edits scripts/player.gd
  -> hook: nodemap refreshes the graph (only changed files are re-parsed)
  -> hook: runs `check` for player.gd
  -> errors?  yes -> fed back to Claude (exit code 2), which fixes them before moving on
              no  -> silent (warnings are passed along as extra context)
```

4. **The project-level `/nodemap` skill** in `.claude/skills/nodemap/SKILL.md`.

`nodemap claude uninstall` removes everything it added.

### Any MCP client (Claude Desktop, Cursor, ...)

```json
{
  "mcpServers": {
    "nodemap": { "command": "nodemap", "args": ["serve"] }
  }
}
```

Start the client from the project folder (or pass `"args": ["serve", "--graph", "/path/to/nodemap-out/graph.json"]`).
The server reloads `graph.json` whenever it changes.

| MCP tool | Same as |
|---|---|
| `query_graph` | `nodemap query` |
| `get_node` | `nodemap explain` |
| `shortest_path` | `nodemap path` |
| `get_scene_tree` | `nodemap tree` |
| `find_signal` | `nodemap signal` |
| `check_project` | `nodemap check` |
| `project_info` | autoloads, actions, groups, layers, god nodes, communities |
| `graph_stats` | `nodemap stats` |

### Codex, Cursor, Gemini CLI, Copilot and others

```bash
nodemap agents install     # writes the same rules into AGENTS.md
```

Pair it with `nodemap watch` in a terminal so the graph stays fresh while you work.

### Recommended workflow

1. Run `nodemap .` once and skim `NODEMAP_REPORT.md` (or have your assistant summarise it).
2. Ask questions through `query`, `explain`, `tree` and `signal` instead of letting the assistant
   read whole files.
3. Let the edit hook (or `nodemap check` before each commit) catch broken paths, connections and
   ids.
4. Optionally add `nodemap check --severity error` to CI.

## `nodemap check` reference

| Code | Severity | Meaning |
|---|---|---|
| `missing-node` | error | `$Path`, `%Name` or `get_node("...")` does not exist in any scene the script runs in. Resolution goes through instanced and inherited scenes, `%Unique` names, subclasses and parent scenes |
| `missing-method` | error / warning | A `.tscn` `[connection]` targets a method the target script (or its base classes) doesn't define |
| `connection-missing-node` | error | A `[connection]` `from`/`to` node is not in the scene |
| `missing-handler` | error / warning | `sig.connect(handler)` where `handler()` is not defined |
| `unknown-signal` | warning | `sig.emit()` for a signal the script never declares |
| `undeclared-action` | error / warning | An input action is used but not in `project.godot` `[input]` or registered with `InputMap.add_action` |
| `missing-resource` | error | A `preload`, `load`, `ext_resource`, `extends "res://..."` or path literal points to a missing file |
| `case-mismatch` | error | A `res://` path differs in case from the file on disk |
| `invalid-uid` | warning | A `uid://` value that isn't a valid Godot uid (Godot silently ignores it) |
| `unknown-uid` | warning / error | A valid-looking uid that no file owns |
| `stale-path` | warning | The uid resolves to a different file than the `path=` says |
| `duplicate-uid` | error | Two files share a uid (common after copying files outside the editor) |
| `duplicate-class-name` | error | Two scripts declare the same `class_name` |
| `autoload-class-name-conflict` | error | An autoload's script has `class_name` equal to the autoload name |
| `missing-autoload`, `missing-main-scene` | error | `project.godot` points at a missing file |
| `godot3-syntax` | warning | `yield`, `export var`, `onready var`, `tool`, `setget`, `.instance()`, `change_scene()`, `connect("s", self, "m")`, `rand_range`, `Pool*Array`, `KinematicBody2D`/`Spatial`/..., `move_and_slide(velocity)`, ... |
| `signal-never-emitted`, `signal-never-connected` | info | Dead signal wiring |
| `unused-action` | info | An Input Map action no script uses |
| `stale-doc-reference` | info | A Markdown doc points at a missing file, or at a `Class.method()` / `Autoload.signal` that no longer exists |

To avoid false positives:

- Nodes created at runtime and named in code (`child.name = "HitArea"`) are not reported.
- `get_node_or_null()` and `has_node()` lookups are only notes.
- Scripts that `add_child()` get warnings instead of errors.
- Paths built from variables or format strings (`"res://levels/%d.tscn"`) are skipped.

## The graph model

**Node kinds**

| Kind | Id example |
|---|---|
| `scene` | `res://scenes/Main.tscn` |
| `node` (inside a scene) | `res://scenes/Main.tscn::node:Coins/Coin` |
| `script` | `res://scripts/player.gd` |
| `function` | `res://scripts/player.gd::fn:take_damage` |
| `signal` | `res://autoload/events.gd::sig:player_died` |
| `autoload` | `autoload:Events` |
| `action` | `action:jump` |
| `group` | `group:collectibles` |
| `resource` (`.tres`), `asset` (textures, audio, ...), `missing` | `res://data/level_1.tres` |
| `doc` (Markdown) | `res://docs/design.md` |

**Relations**

| Group | Relations |
|---|---|
| Scene structure | `contains`, `has_child`, `instances`, `inherits_scene`, `has_script`, `in_group` |
| Code structure | `extends`, `defines`, `declares`, `overrides`, `calls`, `uses_class`, `uses_autoload` |
| Signals | `emits` (function to signal), `connected_to` (signal to handler; carries `signal=` when the signal is built-in, like `pressed`) |
| Node paths | `node_path` (function/script to the scene node it resolves to) |
| Resources | `preloads`, `loads`, `references`, `uses` |
| Project | `autoloads`, `uses_action`, `adds_to_group`, `queries_group` |
| Docs | `mentions` (doc to anything it talks about; carries `section=`) |

**Confidence** (as in graphify)

- `EXTRACTED` means the relation is literally in the source: a `[connection]`,
  `Events.player_died.emit()`, or `$Sprite2D` resolved in the attached scene.
- `INFERRED` means it was resolved through a typed variable (`var player: Player`, then
  `player.died.connect(...)`) or through a signal name that only one script declares.
- `AMBIGUOUS` means several scripts declare a signal with that name.

**`graph.json` shape**

```json
{
  "meta": {"project": "Demo Game", "engine_features": ["4.3", "Forward Plus"], "autoloads": {...}, "input_actions": [...], ...},
  "nodes": [{"id": "res://scripts/player.gd", "kind": "script", "label": "Player", "file": "res://scripts/player.gd", "line": 1, "class_name": "Player", "extends": "CharacterBody2D", "community": 0}],
  "edges": [{"source": "res://scripts/player.gd::fn:take_damage", "target": "res://autoload/events.gd::sig:player_died", "relation": "emits", "confidence": "EXTRACTED", "file": "res://scripts/player.gd", "line": 34}],
  "communities": {"0": {"label": "scenes (Player)", "nodes": ["..."]}},
  "issues": [{"severity": "error", "code": "missing-node", "file": "res://scripts/player.gd", "line": 18, "message": "...", "hint": "..."}]
}
```

- **Communities** are Louvain clusters over the file-level graph, labelled by dominant folder
  and hub file (test files don't drive the labels).
- **God nodes** are ranked by how many *other files* touch them.

## Options

| Flag | Meaning |
|---|---|
| `--include-addons` | Also map `res://addons/` (skipped by default; plugins add noise) |
| `--exclude DIR` | Skip a folder, repeatable (e.g. `--exclude tests --exclude prototypes`) |
| `--out DIR` | Write somewhere other than `<project>/nodemap-out` |
| `--no-html` | Skip `nodemap.html` |
| `--no-docs` | Don't map Markdown docs |

Folders with a `.gdignore` file are skipped, as are `.godot/` and nested Godot projects. You can
commit `nodemap-out/` so teammates and CI get the report, or add it to `.gitignore`. Either way
`cache/` is ignored.

**Performance.** Extraction is regex- and indentation-based and cached per file by mtime. A
project with a few hundred scripts builds in about a second, and the edit hook usually re-parses a
single file.

## How it works

```
find project.godot -> walk files -> extract per file (cached) -> link across files -> cluster -> report / html / json
```

| Module | Role |
|---|---|
| `godot_text.py` | Parser for Godot's ConfigFile format (`.tscn`, `.tres`, `project.godot`), multi-line values included |
| `project.py` | Project discovery, `project.godot` settings, file index, uid index (scene headers, `.uid` sidecars, `.import` files) |
| `scenes.py` | Scene trees, instances, inheritance, `[connection]`s, groups, unique names, `.tres` scripts |
| `scripts.py` | GDScript and C# extraction: declarations, node paths, connect/emit, calls, typed variables, `preload`/`load`, input actions, groups, Godot 3 leftovers |
| `docs.py` | Markdown extraction: title, headings, and candidate references (paths, links, `code`, `Class.member`, CamelCase names) |
| `build.py` | The Godot semantics. Resolves uids like the engine (uid first, then path), follows `class_name`/`extends` chains, computes effective scene trees and the contexts each script runs in, resolves node paths, signals and calls, and collects issues |
| `analyze.py` | Communities, god nodes, surprising connections, signal bus, suggested questions |
| `query.py`, `report.py`, `export_html.py` | Text answers, the report, the viewer |
| `serve.py` | Dependency-free MCP stdio server |
| `install.py`, `cli.py` | Assistant integration and the command line |

## Compared with graphify

| | graphify | godot-nodemap |
|---|---|---|
| Scope | Any codebase or corpus (code, docs, papers, media) | Godot 4 projects |
| Code parsing | tree-sitter, 30+ languages | GDScript + C#, Godot-aware |
| Scenes / `.tscn` | Not modelled | Scene trees, instances, inheritance, connections, groups, `%Unique` |
| Signals | Not modelled | Declared / emitted / connected, code and editor |
| Engine config | Not modelled | Autoloads, Input Map, layers, main scene, uids |
| Docs | Extracted with an LLM (concepts, rationale) | Linked to code by name and path, offline; stale references flagged |
| Linting | No | `nodemap check` + a post-edit hook |
| LLM usage | Optional, for docs and media | None |
| Outputs | `graph.json`, `GRAPH_REPORT.md`, `graph.html` | `graph.json`, `NODEMAP_REPORT.md`, `nodemap.html` |
| Assistant integration | Skill, hooks, MCP for many assistants | Skill, hooks, MCP, AGENTS.md |

They can be used together: graphify for your design docs and wiki, nodemap for the game itself.

## Limitations and FAQ

**Does it run my game or need the Godot editor?** No. It only reads files.

**Godot 3?** No, the tool targets Godot 4.x. It does flag Godot 3 syntax inside Godot 4 projects.

**Does it understand what my docs *say*?** No. It links docs to the code they name and tells you
when those names go stale, but it doesn't summarise or interpret prose. Pair it with graphify if you
want LLM-extracted concepts from your design docs.

**What can't it see?**

- Nodes created purely in code.
- `get_node(some_variable)`.
- Signals connected by names built at runtime.
- Engine-class APIs: there's no list of built-in signals and methods yet, so connections to
  built-in signals like `pressed` are recorded by name without further checks.

**False positive?** Run `nodemap explain <script>` to see which scenes nodemap thinks the script is
attached to (`has_script` edges). Most false `missing-node` reports come from nodes added at
runtime. Name them in code (`node.name = "X"`) or look them up with `get_node_or_null`.

**Big projects?** Use `--exclude` for folders you don't care about and keep `--include-addons` off.

## Development

```bash
git clone https://github.com/kusumaindraputra/godot-nodemap
cd godot-nodemap
pip install -e ".[dev]"
pytest
```

`tests/fixtures/demo_game` is a small Godot 4 project (GDScript + C#) with deliberately planted
bugs. It is used by the tests and by the examples in this README. New checks should come with a
planted case there and an assertion in `tests/test_build.py`.

## License

MIT
