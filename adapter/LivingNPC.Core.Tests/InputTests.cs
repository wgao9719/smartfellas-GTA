using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class InputTests
{
    [Fact] public void PushToTalkKeepsSceneAndInstanceUntilCancellation()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7));
        var scene = input.SceneId;
        Assert.True(input.StartPushToTalk()); Assert.False(input.StartPushToTalk());
        Assert.False(input.TryBind(game.Actor, game.World, 7));
        Assert.True(input.StopPushToTalk()); Assert.False(input.StopPushToTalk());
        Assert.Equal(scene, input.SceneId); Assert.Equal("instance-1", input.RuntimeInstanceId);
        input.Reset(); Assert.False(input.StopPushToTalk()); Assert.False(input.Bound);
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.NotEqual(scene, input.SceneId);
    }

    [Theory]
    [InlineData("instance")][InlineData("actor")][InlineData("generation")][InlineData("distance")]
    [InlineData("mission")][InlineData("dead")][InlineData("combat")][InlineData("ownership")][InlineData("protected")]
    public void BoundInputInvalidatesWhenActorOrWorldChanges(string change)
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk());
        switch (change)
        {
            case "instance": game.Actor.RuntimeInstanceId = "new-instance"; break;
            case "actor": game.Actor.ActorId = "new-actor"; break;
            case "generation": game.Actor.Generation++; break;
            case "distance": game.Actor.Distance = 16; break;
            case "mission": game.World.MissionActive = true; break;
            case "dead": game.Actor.Alive = false; break;
            case "combat": game.Actor.InCombat = true; break;
            case "ownership": game.Actor.Ownership = Ownership.GameScriptOwned; break;
            case "protected": game.Actor.Policy = ControlPolicy.Protected; game.Actor.Role = PedRole.Prisoner; break;
        }
        Assert.False(input.IsCurrent(game.Actor, game.World, 7));
        input.Reset(); Assert.False(input.PushToTalkHeld);
    }

    [Theory][InlineData(-1)][InlineData(9)][InlineData(float.NaN)][InlineData(float.PositiveInfinity)]
    public void InvalidOrDistantTargetCannotStartInput(float distance)
    {
        var game = new FakeGame(); game.Actor.Distance = distance;
        var input = new ConversationInput(); Assert.False(input.TryBind(game.Actor, game.World, 7));
        Assert.False(input.StartPushToTalk()); Assert.False(input.Bound);
    }

    [Fact] public void SnapshotReceiptGatesStartAndReleasePreventsLateMicrophoneOpening()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk());
        Assert.False(input.TryQueuePushToTalkStart(false));
        Assert.True(input.StopPushToTalk()); Assert.False(input.TryQueuePushToTalkStart(true));
        input.Reset(); Assert.True(input.TryBind(game.Actor, game.World, 7));
        Assert.True(input.StartPushToTalk()); Assert.True(input.TryQueuePushToTalkStart(true));
        Assert.False(input.TryQueuePushToTalkStart(true)); Assert.True(input.StopPushToTalk());
        Assert.True(input.PushToTalkStartQueued);
    }
}
