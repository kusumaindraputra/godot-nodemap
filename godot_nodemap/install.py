"""Install nodemap into AI coding assistants.

  nodemap install [--project]     skill file for Claude Code (~/.claude/skills/nodemap/SKILL.md)
  nodemap claude install [--mcp]  CLAUDE.md section + PreToolUse/PostToolUse hooks (+ .mcp.json)
  nodemap agents install          AGENTS.md section (Codex, Cursor, Gemini CLI, Copilot, ...)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SKILL_SRC = Path(__file__).with_name("skill.md")
MARK_START = "<!-- nodemap:start -->"
MARK_END = "<!-- nodemap:end -->"

ALWAYS_ON = """## godot-nodemap

This Godot project has a knowledge graph at nodemap-out/ (scenes, node trees, scripts, signals,
autoloads, input actions, groups, resources) built by godot-nodemap.

Rules:
- For questions about the project, first run `nodemap query "<question>"`. Use `nodemap explain <name>`
  for one script/scene/signal/autoload, `nodemap tree <Scene>` for a scene's node tree,
  `nodemap signal <name>` for who emits/listens, `nodemap path <A> <B>` for how two things connect.
  These return small, file:line-annotated answers; prefer them to grepping or reading whole files.
- Never guess node paths: check `nodemap tree <Scene>` before writing `$Path`, `%Name` or `get_node()`.
- Do not invent `uid://` values in .tscn/.tres files; omit `uid=` on hand-written ext_resource lines.
- After editing .gd/.cs/.tscn/.tres files run `nodemap check <files>` and fix all errors.
- Read nodemap-out/NODEMAP_REPORT.md only for a broad overview.
"""


def _write_section(target: Path, body: str) -> None:
    block = f"{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    if MARK_START in text and MARK_END in text:
        text = re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", block, text, flags=re.S)
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    target.write_text(text, encoding="utf-8")


def _remove_section(target: Path) -> bool:
    if not target.exists():
        return False
    text = target.read_text(encoding="utf-8")
    new = re.sub(r"\n*" + re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", "\n", text, flags=re.S)
    if new != text:
        target.write_text(new.strip() + "\n" if new.strip() else "", encoding="utf-8")
        return True
    return False


def install_skill(project_dir: Path | None = None) -> Path:
    base = (project_dir / ".claude") if project_dir else (Path.home() / ".claude")
    dest = base / "skills" / "nodemap" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(SKILL_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _hooks() -> dict:
    return {
        "PreToolUse": [{"matcher": "Grep|Glob",
                        "hooks": [{"type": "command", "command": "nodemap hook-guard", "timeout": 10}]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit",
                         "hooks": [{"type": "command", "command": "nodemap hook-check", "timeout": 60}]}],
    }


def _load_json(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            raise SystemExit(f"nodemap: {p} is not valid JSON; fix it first.")
    return {}


def claude_install(project_dir: Path, mcp: bool = False) -> list[str]:
    done = []
    skill = install_skill(project_dir)
    done.append(f"skill     -> {skill.relative_to(project_dir)}")
    _write_section(project_dir / "CLAUDE.md", ALWAYS_ON)
    done.append("CLAUDE.md -> godot-nodemap section")
    settings_p = project_dir / ".claude" / "settings.json"
    settings = _load_json(settings_p)
    hooks = settings.setdefault("hooks", {})
    for event, entries in _hooks().items():
        cur = [h for h in hooks.get(event, []) if "nodemap" not in json.dumps(h)]
        hooks[event] = cur + entries
    settings_p.parent.mkdir(parents=True, exist_ok=True)
    settings_p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    done.append(".claude/settings.json -> PreToolUse (Grep|Glob nudge) + PostToolUse (check after edits)")
    if mcp:
        mp = project_dir / ".mcp.json"
        cfg = _load_json(mp)
        cfg.setdefault("mcpServers", {})["nodemap"] = {"command": "nodemap", "args": ["serve"]}
        mp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        done.append(".mcp.json -> nodemap MCP server")
    return done


def claude_uninstall(project_dir: Path) -> list[str]:
    done = []
    if _remove_section(project_dir / "CLAUDE.md"):
        done.append("CLAUDE.md section removed")
    settings_p = project_dir / ".claude" / "settings.json"
    if settings_p.exists():
        settings = _load_json(settings_p)
        hooks = settings.get("hooks", {})
        for event in list(hooks):
            hooks[event] = [h for h in hooks[event] if "nodemap" not in json.dumps(h)]
            if not hooks[event]:
                del hooks[event]
        settings_p.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        done.append("hooks removed from .claude/settings.json")
    skill = project_dir / ".claude" / "skills" / "nodemap" / "SKILL.md"
    if skill.exists():
        skill.unlink()
        done.append("project skill removed")
    mp = project_dir / ".mcp.json"
    if mp.exists():
        cfg = _load_json(mp)
        if cfg.get("mcpServers", {}).pop("nodemap", None) is not None:
            mp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            done.append("nodemap removed from .mcp.json")
    return done


def agents_install(project_dir: Path) -> list[str]:
    _write_section(project_dir / "AGENTS.md", ALWAYS_ON)
    return ["AGENTS.md -> godot-nodemap section"]
