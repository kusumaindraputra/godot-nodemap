from godot_nodemap import analyze, query
from godot_nodemap.graph import Graph


def _codes(g, severity=None):
    return {(i["code"], i["file"].replace("res://", ""), i["line"]) for i in g.issues
            if severity is None or i["severity"] == severity}


def test_planted_errors_are_found(graph):
    errors = _codes(graph, "error")
    assert errors == {
        ("autoload-class-name-conflict", "autoload/game_state.gd", 1),
        ("missing-method", "scenes/Main.tscn", 21),
        ("missing-node", "scripts/Coin.cs", 15),
        ("undeclared-action", "scripts/Coin.cs", 21),
        ("missing-handler", "scripts/main.gd", 8),
        ("missing-resource", "scripts/player.gd", 8),
        ("case-mismatch", "scripts/player.gd", 9),
        ("missing-node", "scripts/player.gd", 18),
        ("undeclared-action", "scripts/player.gd", 25),
    }


def test_warnings_and_notes(graph):
    codes = {c for c, _, _ in _codes(graph)}
    assert {"invalid-uid", "godot3-syntax", "signal-never-emitted", "unused-action"} <= codes
    assert "unknown-signal" not in codes  # C# [Signal] on its own line is understood


def test_signal_wiring(graph):
    e = {(x["source"], x["target"], x["relation"]) for x in graph.edges}
    # scene [connection] resolved to the script signal and handler
    assert ("res://scripts/player.gd::sig:hit", "res://scripts/main.gd::fn:_on_player_hit", "connected_to") in e
    # autoload signal emitted from a script and connected in two others (one lambda)
    assert ("res://scripts/player.gd::fn:take_damage", "res://autoload/events.gd::sig:player_died", "emits") in e
    assert ("res://autoload/events.gd::sig:player_died", "res://scripts/hud.gd::fn:_ready", "connected_to") in e
    assert ("res://autoload/events.gd::sig:coin_collected", "res://scripts/hud.gd::fn:_on_coin_collected", "connected_to") in e
    # C# signal
    assert ("res://scripts/Coin.cs::fn:OnBodyEntered", "res://scripts/Coin.cs::sig:Collected", "emits") in e


def test_node_paths_and_calls(graph):
    e = {(x["source"], x["target"], x["relation"]) for x in graph.edges}
    assert ("res://scripts/player.gd", "res://scenes/Player.tscn::node:Sprite2D", "node_path") in e
    assert ("res://scripts/player.gd::fn:_ready", "res://scenes/Player.tscn::node:Hurtbox", "node_path") in e
    # typed var `player: Player` resolves the call into player.gd (INFERRED)
    calls = [x for x in graph.edges if x["relation"] == "calls" and x["target"] == "res://scripts/player.gd::fn:take_damage"]
    assert {c["source"] for c in calls} >= {"res://scripts/main.gd::fn:_on_player_hit"}
    assert any(c["confidence"] == "INFERRED" for c in calls)
    # autoload call
    assert ("res://scripts/main.gd::fn:_ready", "res://autoload/game_state.gd::fn:add_score", "calls") in e
    assert ("res://scripts/main.gd", "autoload:GameState", "uses_autoload") in e


def test_scene_structure(graph):
    e = {(x["source"], x["target"], x["relation"]) for x in graph.edges}
    assert ("res://scenes/Main.tscn::node:Player", "res://scenes/Player.tscn", "instances") in e
    assert ("res://scenes/Main.tscn::node:Coins", "group:collectibles", "in_group") in e
    assert ("res://data/level_1.tres", "res://scripts/level_data.gd", "has_script") in e
    assert graph.nodes["res://scenes/Main.tscn"].get("main_scene")


def test_roundtrip_and_analysis(graph, tmp_path):
    p = tmp_path / "graph.json"
    graph.save(p)
    g2 = Graph.load(p)
    assert len(g2.nodes) == len(graph.nodes) and len(g2.edges) == len(graph.edges)
    assert g2.communities
    gods = analyze.god_nodes(g2)
    assert gods and gods[0][0] in g2.nodes
    bus = {s["name"]: s for s in analyze.signal_bus(g2)}
    assert len(bus["player_died"]["listeners"]) == 1 and len(bus["player_died"]["emitters"]) == 1


def test_query_helpers(graph):
    out = query.query(graph, "what happens when the player dies")
    assert "player_died" in out.split("Edges:")[0]
    assert "hud.gd" in out
    tree = query.scene_tree(graph, "Main", expand=1)
    assert "%Hurtbox" in tree and "instance=Player.tscn" in tree
    assert "hit->main.gd._on_player_hit()" in tree
    sig = query.signal(graph, "coin_collected")
    assert "listeners (2)" in sig
    path = query.shortest_path(graph, "Coin.cs", "Events")
    assert "hops" in path
    exp = query.explain(graph, "Player")
    assert "missing-node" in exp and "take_damage" in exp
