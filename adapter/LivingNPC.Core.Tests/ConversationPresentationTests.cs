using System;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class ConversationPresentationTests
{
    private static readonly DateTimeOffset Now = ControllerTests.Now;

    [Theory][InlineData("Typing")][InlineData("Thinking")][InlineData("Talking")]
    public void CurrentConversationStateReplacesCancellationAndExpires(string state)
    {
        var status = new ConversationStatus(); status.Show("Conversation canceled.", Now);
        status.Show(state, Now.AddSeconds(1));
        Assert.Equal(state, status.Read(Now.AddSeconds(2)));
        Assert.Equal("", status.Read(Now.AddSeconds(6)));
    }

    [Fact] public void CancellationAloneExpiresAndResetClearsImmediately()
    {
        var status = new ConversationStatus(); status.Show("Conversation canceled.", Now);
        Assert.Equal("Conversation canceled.", status.Read(Now.AddSeconds(4)));
        Assert.Equal("", status.Read(Now.AddSeconds(5)));
        status.Show("Previous session", Now); status.Clear(); Assert.Equal("", status.Read(Now));
    }

    [Theory][InlineData("Available")][InlineData("Busy")][InlineData("Out of range")][InlineData("Not available")]
    public void SelectedTargetLabelPersistsAfterTransientStatusAndTracksAvailability(string state)
    {
        var game = new FakeGame(); game.Actor.Name = "Nearby resident";
        if (state == "Busy") game.Actor.Busy = true;
        if (state == "Out of range") game.Actor.Distance = 10;
        if (state == "Not available") game.Actor.Policy = ControlPolicy.Protected;
        var status = new ConversationStatus(); status.Show("Conversation canceled.", Now);
        Assert.Equal("", status.Read(Now.AddMinutes(1)));
        Assert.Equal("Target (blue marker): Nearby resident (" + state + ")", TargetPresentation.Label(game.Actor, game.World, 7));
    }

    [Theory][InlineData("missing")][InlineData("despawned")][InlineData("dead")][InlineData("generation")][InlineData("instance")][InlineData("unsafe")]
    public void InvalidTargetNeverGetsAnIndicator(string change)
    {
        var game = new FakeGame(); ActorState? actor = game.Actor;
        if (change == "missing") actor = null;
        if (change == "despawned") game.Actor.Exists = false;
        if (change == "dead") game.Actor.Alive = false;
        if (change == "generation") game.Actor.Generation++;
        if (change == "instance") game.Actor.RuntimeInstanceId = "";
        if (change == "unsafe") game.World.Paused = true;
        Assert.Null(TargetPresentation.Label(actor, game.World, 7));
    }
}
