"""Project discovery: find project.godot, walk files, parse project settings,
and map between filesystem paths, res:// paths and uid:// ids."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import godot_text as gt

SCENE_EXT = {".tscn"}
RESOURCE_EXT = {".tres"}
GDSCRIPT_EXT = {".gd"}
CSHARP_EXT = {".cs"}
DOC_EXT = {".md"}
SOURCE_EXT = SCENE_EXT | RESOURCE_EXT | GDSCRIPT_EXT | CSHARP_EXT

SKIP_DIRS = {".godot", ".import", ".git", ".svn", ".hg", "node_modules", "bin", "obj",
             "nodemap-out", "__pycache__", ".mono", ".vs", ".idea", ".vscode"}

# Actions every Godot 4 project gets for free (InputMap defaults).
BUILTIN_ACTIONS = {
    "ui_accept", "ui_select", "ui_cancel", "ui_focus_next", "ui_focus_prev", "ui_left",
    "ui_right", "ui_up", "ui_down", "ui_page_up", "ui_page_down", "ui_home", "ui_end",
    "ui_cut", "ui_copy", "ui_paste", "ui_undo", "ui_redo", "ui_text_completion_query",
    "ui_text_completion_accept", "ui_text_completion_replace", "ui_text_newline",
    "ui_text_newline_blank", "ui_text_newline_above", "ui_text_indent",
    "ui_text_dedent", "ui_text_backspace", "ui_text_backspace_word",
    "ui_text_backspace_all_to_left", "ui_text_delete", "ui_text_delete_word",
    "ui_text_delete_all_to_right", "ui_text_caret_left", "ui_text_caret_word_left",
    "ui_text_caret_right", "ui_text_caret_word_right", "ui_text_caret_up",
    "ui_text_caret_down", "ui_text_caret_line_start", "ui_text_caret_line_end",
    "ui_text_caret_page_up", "ui_text_caret_page_down", "ui_text_caret_document_start",
    "ui_text_caret_document_end", "ui_text_caret_add_below", "ui_text_caret_add_above",
    "ui_text_scroll_up", "ui_text_scroll_down", "ui_text_select_all",
    "ui_text_select_word_under_caret", "ui_text_add_selection_for_next_occurrence",
    "ui_text_skip_selection_for_next_occurrence", "ui_text_clear_carets_and_selection",
    "ui_text_toggle_insert_mode", "ui_menu", "ui_text_submit", "ui_graph_duplicate",
    "ui_graph_delete", "ui_filedialog_up_one_level", "ui_filedialog_refresh",
    "ui_filedialog_show_hidden", "ui_swap_input_direction", "ui_unicode_start",
    "ui_colorpicker_delete_preset", "ui_accessibility_drag_and_drop",
    "ui_graph_follow_left", "ui_graph_follow_right", "ui_focus_mode",
}


@dataclass
class ProjectConfig:
    name: str = ""
    version: str = ""
    engine_features: list[str] = field(default_factory=list)
    main_scene: str = ""
    autoloads: dict[str, dict] = field(default_factory=dict)  # name -> {path, singleton, line}
    input_actions: dict[str, int] = field(default_factory=dict)  # name -> line
    layer_names: dict[str, str] = field(default_factory=dict)  # "2d_physics/layer_1" -> name
    global_groups: dict[str, str] = field(default_factory=dict)
    plugins: list[str] = field(default_factory=list)
    csharp_assembly: str = ""
    renderer: str = ""


def find_project_root(start: Path) -> Path:
    """Locate the directory holding project.godot: *start* itself, an ancestor,
    or (if there is exactly one) a descendant. Falls back to *start*."""
    start = start.resolve()
    for p in [start, *start.parents]:
        if (p / "project.godot").is_file():
            return p
    found = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if "project.godot" in filenames:
            found.append(Path(dirpath))
            dirnames[:] = []
    if len(found) == 1:
        return found[0]
    return start


def parse_project_godot(path: Path) -> ProjectConfig:
    cfg = ProjectConfig()
    if not path.is_file():
        return cfg
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for sec in gt.parse(text):
        p = sec.props
        if sec.tag == "application":
            cfg.name = gt.unquote(p.get("config/name")) or ""
            cfg.version = gt.unquote(p.get("config/version")) or ""
            cfg.main_scene = gt.unquote(p.get("run/main_scene")) or ""
            cfg.engine_features = gt.strings_in(p.get("config/features"))
        elif sec.tag == "autoload":
            for k, v in p.items():
                raw = gt.unquote(v) or ""
                cfg.autoloads[k] = {"path": raw.lstrip("*"), "singleton": raw.startswith("*"),
                                    "line": _find_key_line(lines, sec.line, k)}
        elif sec.tag == "input":
            for k in p:
                cfg.input_actions[k] = _find_key_line(lines, sec.line, k)
        elif sec.tag == "layer_names":
            for k, v in p.items():
                cfg.layer_names[k] = gt.unquote(v) or ""
        elif sec.tag == "global_group":
            for k, v in p.items():
                cfg.global_groups[k] = gt.unquote(v) or ""
        elif sec.tag == "editor_plugins":
            cfg.plugins = gt.strings_in(p.get("enabled"))
        elif sec.tag == "dotnet":
            cfg.csharp_assembly = gt.unquote(p.get("project/assembly_name")) or ""
        elif sec.tag == "rendering":
            cfg.renderer = gt.unquote(p.get("renderer/rendering_method")) or ""
    return cfg


def _find_key_line(lines: list[str], start: int, key: str) -> int:
    for i in range(max(start, 0), len(lines)):
        if lines[i].startswith(key + "=") or lines[i].startswith(key + " ="):
            return i + 1
    return start


class Paths:
    """res:// <-> filesystem mapping plus an index of every file in the project
    (used for existence and case-sensitivity checks)."""

    def __init__(self, root: Path):
        self.root = root
        self.all_files: set[str] = set()  # res paths
        self.lower_index: dict[str, str] = {}
        self.ignored_dirs: set[str] = set()

    def to_res(self, p: Path) -> str:
        try:  # paths from walk() are already under the resolved root; resolve() is slow
            rel = p.relative_to(self.root).as_posix()
        except ValueError:
            rel = p.resolve().relative_to(self.root).as_posix()
        return "res://" + (rel if rel != "." else "")

    def to_fs(self, res: str) -> Path:
        return self.root / res[len("res://"):] if res.startswith("res://") else Path(res)

    def exists(self, res: str) -> bool:
        return res in self.all_files or (res.endswith("/") and self.to_fs(res).is_dir()) \
            or self.to_fs(res).is_dir()

    def case_insensitive_match(self, res: str) -> str | None:
        hit = self.lower_index.get(res.lower())
        return hit if hit and hit != res else None


def walk(root: Path, include_addons: bool = False, exclude: list[str] | None = None,
         include_docs: bool = True) -> tuple[Paths, list[Path]]:
    """Return a Paths index and the list of source files to extract."""
    paths = Paths(root)
    sources: list[Path] = []
    exclude_res = [e.rstrip("/") for e in (exclude or [])]
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        res_dir = paths.to_res(d)
        if ".gdignore" in filenames and d != root:
            paths.ignored_dirs.add(res_dir)
        # Godot ignores .gdignore folders, but design docs often live in one
        in_ignored = any(res_dir == i or res_dir.startswith(i + "/") for i in paths.ignored_dirs)
        if in_ignored and not include_docs:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(x for x in dirnames if x not in SKIP_DIRS and not x.startswith("."))
        # nested Godot projects are separate projects
        if d != root and "project.godot" in filenames:
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            fp = d / fn
            res = paths.to_res(fp)
            paths.all_files.add(res)
            paths.lower_index.setdefault(res.lower(), res)
            if fp.suffix in DOC_EXT:
                if not include_docs:
                    continue
            elif fp.suffix not in SOURCE_EXT or in_ignored:
                continue
            rel = res[len("res://"):]
            if not include_addons and rel.startswith("addons/"):
                continue
            if any(rel == e or rel.startswith(e + "/") for e in exclude_res):
                continue
            sources.append(fp)
    return paths, sources


_UID_LINE_RE = re.compile(r'uid="?(uid://[^"\s\]]+)"?')
# Godot uids only use a-z and 0-9 (ResourceUID::text_to_id); anything else is invalid and
# silently ignored by the engine (typically an id invented by hand or by an AI).
VALID_UID_RE = re.compile(r"^uid://[a-z0-9]+$")


def build_uid_index(paths: Paths) -> tuple[dict[str, str], dict[str, list[str]]]:
    """uid:// -> res:// for every file whose uid we can learn without the
    editor cache: scene/resource headers, *.uid sidecars and *.import files."""
    uids: dict[str, str] = {}
    dupes: dict[str, list[str]] = {}

    def put(uid: str, res: str):
        if uid in uids and uids[uid] != res:
            dupes.setdefault(uid, [uids[uid]]).append(res)
            return
        uids[uid] = res

    for res in sorted(paths.all_files):
        fp = paths.to_fs(res)
        if res.endswith((".tscn", ".tres")):
            try:
                with fp.open(encoding="utf-8", errors="replace") as fh:
                    first = fh.readline()
            except OSError:
                continue
            m = _UID_LINE_RE.search(first)
            if m:
                put(m.group(1), res)
        elif res.endswith(".uid"):
            try:
                uid = fp.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if uid.startswith("uid://"):
                put(uid, res[: -len(".uid")])
        elif res.endswith(".import"):
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _UID_LINE_RE.search(text)
            if m:
                put(m.group(1), res[: -len(".import")])
    return uids, dupes
