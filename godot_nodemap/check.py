"""`nodemap check`: the Godot-aware linter.

Issue codes (severity):
  missing-node (error/warning/info)   $Path / get_node() does not exist in any scene the script runs in
  missing-method (error/warning)      [connection] in a .tscn targets a method the script lacks
  connection-missing-node (error)     [connection] from/to a node that is not in the scene
  missing-handler (error/warning)     sig.connect(handler) where handler() is not defined
  unknown-signal (warning)            sig.emit() for a signal the script never declares
  undeclared-action (error/warning)   Input.is_action_*("x") where x is not in the Input Map
  missing-resource (error)            preload/load/ext_resource/autoload path that does not exist
  case-mismatch (error)               res:// path differs in case from the file (breaks exports)
  invalid-uid / unknown-uid / stale-path / duplicate-uid   uid:// problems
  duplicate-class-name (error)        two scripts declare the same class_name
  autoload-class-name-conflict (error) class_name equal to an autoload name
  missing-autoload / missing-main-scene (error)
  godot3-syntax (warning)             Godot 3 API/syntax in a Godot 4 project
  signal-never-emitted / signal-never-connected / unused-action (info)
"""
from __future__ import annotations

import json

from .graph import Graph

SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


def filter_issues(g: Graph, files: list[str] | None = None, min_severity: str = "warning",
                  codes: list[str] | None = None) -> list[dict]:
    limit = SEV_ORDER[min_severity]
    out = []
    wanted = None
    if files:
        wanted = set()
        for f in files:
            f = f.replace("\\", "/")
            wanted.add(f if f.startswith("res://") else "res://" + f.lstrip("./"))
    for i in g.issues:
        if SEV_ORDER.get(i["severity"], 2) > limit:
            continue
        if codes and i["code"] not in codes:
            continue
        if wanted is not None and i["file"] not in wanted and not _mentions(i, wanted):
            continue
        out.append(i)
    out.sort(key=lambda i: (SEV_ORDER[i["severity"]], i["file"], i["line"]))
    return out


def _mentions(issue: dict, files: set[str]) -> bool:
    # a scene connection to a method in foo.gd is relevant when foo.gd changes
    return any(f in issue["message"] for f in files)


def format_text(issues: list[dict], root: str = "") -> str:
    if not issues:
        return "nodemap check: no issues."
    lines = []
    for i in issues:
        path = i["file"].replace("res://", "")
        lines.append(f"{path}:{i['line']}: {i['severity']} [{i['code']}] {i['message']}")
        if i.get("hint"):
            lines.append(f"    hint: {i['hint']}")
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in SEV_ORDER}
    lines.append("")
    lines.append(f"nodemap check: {counts['error']} errors, {counts['warning']} warnings, {counts['info']} notes")
    return "\n".join(lines)


def format_json(issues: list[dict]) -> str:
    return json.dumps(issues, indent=1)
