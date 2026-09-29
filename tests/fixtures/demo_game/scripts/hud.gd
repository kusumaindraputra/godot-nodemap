extends CanvasLayer

@onready var label: Label = $ScoreLabel


func _ready() -> void:
	Events.coin_collected.connect(_on_coin_collected)
	Events.player_died.connect(func(): label.text = "Game over")


func _on_coin_collected(amount: int) -> void:
	label.text = str(amount)
