extends Node2D

@onready var player: Player = $Player


func _ready() -> void:
	Events.coin_collected.connect(_on_coin_collected)
	player.died.connect(_on_player_gone)
	GameState.add_score(0)


func _on_player_hit(damage: int) -> void:
	player.take_damage(damage)


func _on_coin_collected(amount: int) -> void:
	GameState.add_score(amount)
