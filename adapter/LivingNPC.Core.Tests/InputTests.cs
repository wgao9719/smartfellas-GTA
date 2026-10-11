using System.Collections.Generic;
using LivingNPC.Core;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class InputTests
{
    private const int PttKey = 78, WhisperKey = 66;
    [Fact] public void PushToTalkKeepsSceneAndInstanceUntilCancellation()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7));
        var scene = input.SceneId;
        Assert.True(input.StartPushToTalk(PttKey, false)); Assert.False(input.StartPushToTalk(PttKey, false));
        Assert.False(input.TryBind(game.Actor, game.World, 7));
        Assert.True(input.StopPushToTalk(PttKey)); Assert.False(input.StopPushToTalk(PttKey));
        Assert.Equal(scene, input.SceneId); Assert.Equal("instance-1", input.RuntimeInstanceId);
        input.Reset(); Assert.False(input.StopPushToTalk(PttKey)); Assert.False(input.Bound);
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.NotEqual(scene, input.SceneId);
    }

    [Theory]
    [InlineData("instance")][InlineData("actor")][InlineData("generation")][InlineData("distance")]
    [InlineData("mission")][InlineData("dead")][InlineData("combat")][InlineData("ownership")]
    public void BoundInputInvalidatesWhenActorOrWorldChanges(string change)
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(PttKey, false));
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
        }
        Assert.False(input.IsCurrent(game.Actor, game.World, 7));
        input.Reset(); Assert.False(input.PushToTalkHeld);
    }

    [Theory][InlineData(-1)][InlineData(9)][InlineData(float.NaN)][InlineData(float.PositiveInfinity)]
    public void InvalidOrDistantTargetCannotStartInput(float distance)
    {
        var game = new FakeGame(); game.Actor.Distance = distance;
        var input = new ConversationInput(); Assert.False(input.TryBind(game.Actor, game.World, 7));
        Assert.False(input.StartPushToTalk(PttKey, false)); Assert.False(input.Bound);
    }

    [Fact] public void SnapshotReceiptGatesStartAndReleasePreventsLateMicrophoneOpening()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(PttKey, false));
        Assert.False(input.TryQueuePushToTalkStart(false));
        Assert.True(input.StopPushToTalk(PttKey)); Assert.False(input.TryQueuePushToTalkStart(true));
        input.Reset(); Assert.True(input.TryBind(game.Actor, game.World, 7));
        Assert.True(input.StartPushToTalk(PttKey, false)); Assert.True(input.TryQueuePushToTalkStart(true));
        Assert.False(input.TryQueuePushToTalkStart(true)); Assert.True(input.StopPushToTalk(PttKey));
        Assert.True(input.PushToTalkStartQueued);
    }

    [Fact] public void WhisperIsLockedAtKeyDownIntoStartAndStopPayloads()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.Null(input.ControlPayload());
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(WhisperKey, true));
        var start = input.ControlPayload()!;
        Assert.True(input.StopPushToTalk(WhisperKey));
        Assert.Equal(start, input.ControlPayload());
        Assert.Equal(new Dictionary<string, object> { ["scene_id"] = input.SceneId!, ["runtime_instance_id"] = "instance-1", ["whisper"] = true }, start);
        input.Reset(); Assert.False(input.Whisper); Assert.Equal(0, input.PushToTalkKey);
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(PttKey, false));
        Assert.False(input.ControlPayload()!.ContainsKey("whisper"));
    }

    [Fact] public void OnlyTheStartingKeyStopsItsCapture()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(PttKey, false));
        Assert.False(input.StartPushToTalk(WhisperKey, true)); Assert.False(input.Whisper);
        Assert.False(input.StopPushToTalk(WhisperKey)); Assert.True(input.PushToTalkHeld);
        Assert.True(input.StopPushToTalk(PttKey)); Assert.False(input.PushToTalkHeld);
    }

    [Theory]
    [InlineData("/w hi", "hi", true)][InlineData("/W hi", "hi", true)][InlineData("//w hi", "/w hi", false)]
    [InlineData("hi /w", "hi /w", false)][InlineData("/w", "", true)][InlineData("/what", "/what", false)]
    public void TypedWhisperPrefixIsParsedOnlyInTheAdapter(string raw, string text, bool whisper)
    {
        var typed = TypedInput.Parse(raw);
        Assert.Equal(text, typed.Text); Assert.Equal(whisper, typed.Whisper);
    }

    [Fact] public void TypedPayloadRequiresBindingAndText()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.Null(input.TypedPayload("hello"));
        Assert.True(input.TryBind(game.Actor, game.World, 7));
        Assert.Null(input.TypedPayload("")); Assert.Null(input.TypedPayload("/w")); Assert.Null(input.TypedPayload("/w   "));
        Assert.Equal(new Dictionary<string, object> { ["text"] = "hello", ["input_mode"] = "typed", ["scene_id"] = input.SceneId! }, input.TypedPayload("hello"));
        var whisper = input.TypedPayload("/w Please look at me.")!;
        Assert.Equal("Please look at me.", whisper["text"]); Assert.Equal(true, whisper["whisper"]);
    }

    [Fact] public void EchoShowsTheWhisperLabel()
    {
        Assert.Equal("You (whispering): hi", TypedInput.Echo("/w hi")); Assert.Equal("You (whispering): ", TypedInput.Echo("/w"));
        Assert.Equal("You: /w hi", TypedInput.Echo("//w hi")); Assert.Equal("You: a b", TypedInput.Echo("a~r~ b"));
    }
}
