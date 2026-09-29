# Demo Game design

## Player

The `Player` (see [the player script](../scripts/player.gd)) is spawned by Main.tscn.
When hit, `Player.take_damage()` emits `died`, and `Events.player_died.emit()` tells the HUD.
Jumping uses the `jump` input action. Coins live in the `collectibles` group.

```gdscript
Events.coin_collected.connect(_on_coin_collected)
```

## HUD

The HUD listens to `coin_collected` and writes the score into its label.

## Removed

The old dash lived in res://scripts/dash.gd.
Respawning used `Events.player_respawned.emit()`.
See [the old notes](notes/old.md).
