import json
import subprocess
import sys

from godot_nodemap.cli import main
from godot_nodemap.serve import Server


def test_build_writes_outputs(game, capsys):
    assert main([str(game)]) == 0
    out = game / "nodemap-out"
    assert (out / "graph.json").exists() and (out / "NODEMAP_REPORT.md").exists() and (out / "nodemap.html").exists()
    report = (out / "NODEMAP_REPORT.md").read_text(encoding="utf-8")
    assert "## Signal bus" in report and "`Events`" in report and "Main.tscn" in report
    html = (out / "nodemap.html").read_text(encoding="utf-8")
    assert "vis-network" in html and "</script>" in html


def test_check_exit_code_and_filter(game, monkeypatch, capsys):
    monkeypatch.chdir(game)
    assert main(["check"]) == 1
    capsys.readouterr()
    assert main(["check", "scripts/hud.gd"]) == 0
    assert "no issues" in capsys.readouterr().out
    main(["check", "--json", "--severity", "error"])
    data = json.loads(capsys.readouterr().out)
    assert all(i["severity"] == "error" for i in data) and len(data) == 9


def _hook(game, file, cwd):
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(file)}, "cwd": str(cwd)})
    return subprocess.run([sys.executable, "-m", "godot_nodemap", "hook-check"], input=payload,
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd)


def test_hook_check_blocks_on_errors(game):
    r = _hook(game, game / "scripts" / "player.gd", game)
    assert r.returncode == 2 and "missing-node" in r.stderr
    r = _hook(game, game / "scripts" / "hud.gd", game)
    assert r.returncode == 0
    # fixing the file clears the error on the next edit
    p = game / "scripts" / "player.gd"
    p.write_text(p.read_text(encoding="utf-8").replace("\t$Weapon.visible = false\n", ""), encoding="utf-8")
    r = _hook(game, p, game)
    assert "Weapon" not in r.stderr
    r = _hook(game, game / "README.md", game)
    assert r.returncode == 0 and not r.stderr


def test_hook_guard(game):
    r = subprocess.run([sys.executable, "-m", "godot_nodemap", "hook-guard"], capture_output=True, text=True, encoding="utf-8", cwd=game)
    assert r.stdout == ""
    main([str(game), "--no-html"])
    r = subprocess.run([sys.executable, "-m", "godot_nodemap", "hook-guard"], capture_output=True, text=True, encoding="utf-8", cwd=game)
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_claude_install_roundtrip(game, monkeypatch):
    monkeypatch.chdir(game)
    (game / "CLAUDE.md").write_text("# My game\n\nKeep this.\n", encoding="utf-8")
    assert main(["claude", "install", "--mcp"]) == 0
    assert main(["claude", "install"]) == 0  # idempotent
    text = (game / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count("## godot-nodemap") == 1 and "Keep this." in text
    settings = json.loads((game / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert len(settings["hooks"]["PostToolUse"]) == 1 and len(settings["hooks"]["PreToolUse"]) == 1
    assert (game / ".claude" / "skills" / "nodemap" / "SKILL.md").exists()
    assert json.loads((game / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["nodemap"]["args"] == ["serve"]
    assert main(["claude", "uninstall"]) == 0
    assert "godot-nodemap" not in (game / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Keep this." in (game / "CLAUDE.md").read_text(encoding="utf-8")
    assert "nodemap" not in (game / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_mcp_server(game):
    main([str(game), "--no-html"])
    srv = Server(game / "nodemap-out" / "graph.json")
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "godot-nodemap"
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {"query_graph", "get_scene_tree", "find_signal", "check_project"} <= {t["name"] for t in tools}
    r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "get_scene_tree", "arguments": {"scene": "Player"}}})
    assert "%Hurtbox" in r["result"]["content"][0]["text"]
    r = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "check_project", "arguments": {"files": ["scripts/player.gd"]}}})
    assert "case-mismatch" in r["result"]["content"][0]["text"]
    r = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    assert r["result"]["isError"]
    # stdio transport end to end
    msgs = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "find_signal", "arguments": {"name": "hit"}}},
    ]) + "\n"
    p = subprocess.run([sys.executable, "-m", "godot_nodemap", "serve"], input=msgs, capture_output=True, text=True, encoding="utf-8", cwd=game)
    replies = [json.loads(x) for x in p.stdout.splitlines()]
    assert replies[1]["result"]["content"][0]["text"].startswith("# signal hit")
