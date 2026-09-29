## The player character.
class_name Player
extends CharacterBody2D

signal hit(damage: int)
signal died

const Bullet = preload("res://scenes/Bullet.tscn")
const Hud = preload("res://Scenes/HUD.tscn")

@export var speed: float = 200.0
@onready var sprite: Sprite2D = $Sprite2D
@onready var anim: AnimationPlayer = $AnimationPlayer


func _ready() -> void:
	%Hurtbox.area_entered.connect(_on_hurtbox_area_entered)
	$Weapon.visible = false


func _physics_process(delta: float) -> void:
	if Input.is_action_pressed("jump"):
		velocity.y = -speed
	var dir := Input.get_axis("move_left", "move_right")
	if Input.is_action_just_pressed("crouch"):
		pass
	move_and_slide()


func take_damage(amount: int) -> void:
	hit.emit(amount)
	if amount > 10:
		died.emit()
		Events.player_died.emit()


func _on_hurtbox_area_entered(area: Area2D) -> void:
	take_damage(1)
	yield(get_tree(), "idle_frame")
