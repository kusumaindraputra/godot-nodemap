using Godot;

public partial class Coin : Area2D
{
    [Signal]
    public delegate void CollectedEventHandler(int amount);

    [Export]
    public int Value = 1;

    public override void _Ready()
    {
        BodyEntered += OnBodyEntered;
        var sprite = GetNode<Sprite2D>("Sprite2D");
        GetNode<Node>("Missing");
    }

    private void OnBodyEntered(Node2D body)
    {
        EmitSignal(SignalName.Collected, Value);
        if (Input.IsActionJustPressed("interact"))
        {
            QueueFree();
        }
    }
}
