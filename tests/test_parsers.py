from godot_nodemap import godot_text as gt
from godot_nodemap.project import parse_project_godot
from godot_nodemap.scenes import parse_scene
from godot_nodemap.scripts import parse_csharp, parse_gdscript

from conftest import FIXTURE


def test_godot_text_multiline_values_and_headers():
    text = '[gd_scene format=3]\n\n[node name="A" type="Node" groups=["x", "y"]]\narr = [\n1,\n2\n]\n' \
           '[connection signal="s" from="." to="." method="m" binds= [7]]\n'
    secs = gt.parse(text)
    node = next(s for s in secs if s.tag == "node")
    assert gt.strings_in(node.attrs["groups"]) == ["x", "y"]
    assert node.props["arr"].startswith("[") and "2" in node.props["arr"]
    conn = next(s for s in secs if s.tag == "connection")
    assert conn.attrs["binds"] == "[7]"


def test_project_godot():
    cfg = parse_project_godot(FIXTURE / "project.godot")
    assert cfg.name == "Demo Game"
    assert cfg.main_scene == "res://scenes/Main.tscn"
    assert set(cfg.autoloads) == {"Events", "GameState"}
    assert cfg.autoloads["Events"]["path"] == "res://autoload/events.gd"
    assert set(cfg.input_actions) == {"jump", "move_left", "move_right", "unused_action"}
    assert cfg.layer_names["2d_physics/layer_2"] == "player"


def test_scene_tree_paths_and_connections():
    sm = parse_scene(FIXTURE / "scenes" / "Main.tscn", "res://scenes/Main.tscn")
    assert set(sm.nodes) == {".", "Player", "HUD", "Coins", "Coins/Coin"}
    assert sm.nodes["Coins"].groups == ["collectibles"]
    assert sm.nodes["Player"].instance_ext == "2_player"
    assert [(c.signal, c.method) for c in sm.connections] == [("hit", "_on_player_hit"), ("died", "_on_player_dead")]
    player = parse_scene(FIXTURE / "scenes" / "Player.tscn", "res://scenes/Player.tscn")
    assert player.nodes["Hurtbox"].unique


def test_gdscript_facts():
    m = parse_gdscript(FIXTURE / "scripts" / "player.gd", "res://scripts/player.gd")
    assert m.class_name == "Player" and m.extends == "CharacterBody2D"
    assert set(m.signals) == {"hit", "died"}
    assert {"_ready", "_physics_process", "take_damage", "_on_hurtbox_area_entered"} <= set(m.funcs)
    paths = {r["path"] for r in m.node_refs}
    assert {"Sprite2D", "AnimationPlayer", "%Hurtbox", "Weapon"} <= paths
    assert {a["name"] for a in m.actions} == {"jump", "move_left", "move_right", "crouch"}
    assert {(e["recv"], e["signal"]) for e in m.emits} == {(None, "hit"), (None, "died"), ("Events", "player_died")}
    conn = m.connects[0]
    assert conn["recv"] == "%Hurtbox" and conn["signal"] == "area_entered" and conn["handler"] == "_on_hurtbox_area_entered"
    assert any("yield" in lg["msg"] for lg in m.legacy)
    assert m.var_types["anim"] == ("path", "$AnimationPlayer")
    assert not any(c["name"] == "take_damage" and c["line"] == m.funcs["take_damage"]["line"] for c in m.calls)


def test_gdscript_lambda_and_string_connect(tmp_path):
    f = tmp_path / "x.gd"
    f.write_text('extends Node\nsignal a\nfunc _ready():\n\ta.connect(func(): print(1))\n'
                 '\tconnect("a", Callable(self, "_on_a"))\n\tget_tree().process_frame.connect(_on_a)\n'
                 '\tvar other = Node.new()\n\tother.get_node("Nope")\nfunc _on_a():\n\tpass\n', encoding="utf-8")
    m = parse_gdscript(f, "res://x.gd")
    assert m.connects[0]["lambda"] and m.connects[0]["signal"] == "a"
    assert any(c["signal"] == "a" and c["handler"] == "_on_a" for c in m.connects)
    assert any(c["signal"] == "process_frame" and c["recv"] == "?" for c in m.connects)
    assert not m.node_refs  # other.get_node() is relative to another node


def test_csharp_facts():
    m = parse_csharp(FIXTURE / "scripts" / "Coin.cs", "res://scripts/Coin.cs")
    assert m.class_name == "Coin" and m.extends == "Area2D"
    assert "Collected" in m.signals
    assert m.exports[0]["name"] == "Value"
    assert {"_Ready", "OnBodyEntered"} <= set(m.funcs)
    assert {r["path"] for r in m.node_refs} == {"Sprite2D", "Missing"}
    assert m.emits[0]["signal"] == "Collected" and m.emits[0]["func"] == "OnBodyEntered"
    assert m.connects[0]["signal"] == "BodyEntered" and m.connects[0]["handler"] == "OnBodyEntered"
    assert m.actions[0]["name"] == "interact"


def test_markdown_refs_sections_and_own_block():
    from godot_nodemap.docs import parse_markdown

    d = parse_markdown(FIXTURE / "docs" / "design.md", "res://docs/design.md")
    assert d.title == "Demo Game design"
    assert [t for _, t, _ in d.sections] == ["Demo Game design", "Player", "HUD", "Removed"]
    refs = {(r["kind"], r["text"], r["member"]) for r in d.refs}
    assert ("path", "../scripts/player.gd", "") in refs
    assert ("path", "res://scripts/dash.gd", "") in refs
    assert ("path", "notes/old.md", "") in refs
    assert ("qualified", "Player", "take_damage") in refs
    assert ("code", "jump", "") in refs  # inline code span
    assert ("qualified", "Events", "coin_collected") in refs  # fenced code block
    respawn = next(r for r in d.refs if r["member"] == "player_respawned")
    assert respawn["callish"] and respawn["section"] == "Removed"
    readme = parse_markdown(FIXTURE / "README.md", "res://README.md")
    assert not any("ghost_signal" in r["text"] or "nowhere" in r["text"] for r in readme.refs)
