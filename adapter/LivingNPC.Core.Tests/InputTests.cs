using System.Collections.Generic;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public sealed class InputTests
{
    private const int PttKey = 78, WhisperKey = 66;
    [Theory][InlineData(false, false)][InlineData(true, false)][InlineData(false, true)][InlineData(true, true)]
    public void CancelClearsSelectionWithOrWithoutBoundInput(bool bound, bool paused)
    {
        var game = new FakeGame(); var input = new ConversationInput(); var controller = ControllerTests.Controller(game);
        string? selected = game.Actor.ActorId, canceledScene = null;
        if (bound)
        {
            Assert.True(input.TryBind(game.Actor, game.World, 7));
            Assert.True(input.StartPushToTalk(PttKey, false));
        }
        var scene = input.SceneId;
        var action = ControllerTests.Action(); action.Payload["scene_id"] = scene ?? "scene-1";
        controller.Commands.TryAdd(action); controller.Tick(ControllerTests.Now);
        Assert.Equal(1, game.Starts);
        game.World.Paused = paused;
        input.Cancel(controller, canceled => canceledScene = canceled, () => selected = null);
        Assert.Null(canceledScene); Assert.False(input.Bound); Assert.False(input.PushToTalkHeld);
        Assert.Null(selected); Assert.Equal(paused ? 0 : 1, game.Releases);
    }

    [Fact] public void IdleSelectionClearsWithoutAControllerOrScene()
    {
        var input = new ConversationInput(); string? selected = "npc"; var serviceCanceled = false;
        input.Cancel(null, scene =>
        {
            Assert.Null(scene);
            var wire = JObject.Parse(Wire.Encode(new EventEnvelope { Kind = "scene.cancel", Payload = new { scene_id = scene } }));
            Assert.Empty((JObject)wire["payload"]!); serviceCanceled = true;
        }, () => selected = null);
        Assert.Null(selected); Assert.True(serviceCanceled);
    }

    [Fact] public void ExplicitCancelAlsoStopsUnsolicitedWorkOutsideTheBoundScene()
    {
        var game = new FakeGame(); var input = new ConversationInput(); var controller = ControllerTests.Controller(game);
        Assert.True(input.TryBind(game.Actor, game.World, 7));
        controller.Commands.TryAdd(ControllerTests.Action()); controller.Tick(ControllerTests.Now);
        controller.Commands.TryAdd(ControllerTests.Action("queued-action"));
        string? canceledScene = "not-sent";
        input.Cancel(controller, scene => canceledScene = scene, () => { });
        Assert.Equal(1, game.Releases); Assert.Equal(0, controller.Commands.Count);
        Assert.Null(canceledScene); Assert.Equal(1, controller.CanceledSceneCount);
    }

    [Fact] public void CancelStopsActiveAndQueuedWorkBeforeResettingTheScene()
    {
        var game = new FakeGame { SubtitleRendered = false }; var input = new ConversationInput();
        var controller = ControllerTests.Controller(game);
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(PttKey, false));
        var scene = input.SceneId;
        var action = ControllerTests.Action(); action.Payload["scene_id"] = scene;
        controller.Commands.TryAdd(action);
        for (var n = 0; n < 2; n++)
        {
            var subtitle = ControllerTests.Action("subtitle-" + n); subtitle.Kind = "dialogue.chunk";
            subtitle.Payload = JObject.FromObject(new { text = "Hello " + n, speaker = "Neighbor", scene_id = scene, turn_id = "turn-1", chunk_index = n, final = false });
            controller.Commands.TryAdd(subtitle);
        }
        controller.Tick(ControllerTests.Now);
        Assert.Equal(1, game.Starts); Assert.Equal("Hello 0", game.LastSubtitle);
        var queued = ControllerTests.Action("queued-action"); queued.Payload["scene_id"] = scene;
        controller.Commands.TryAdd(queued);
        var sentCancellation = false; string? selected = game.Actor.ActorId;
        input.Cancel(controller, canceled =>
        {
            Assert.Null(canceled);
            Assert.Equal(scene, input.SceneId); Assert.Equal(0, controller.Commands.Count);
            Assert.Equal("", game.LastSubtitle); Assert.Equal(1, game.Releases);
            sentCancellation = true;
        }, () => selected = null);
        Assert.True(sentCancellation); Assert.False(input.Bound); Assert.False(input.PushToTalkHeld); Assert.Null(selected);
        var acks = new List<Ack>(); while (controller.Acks.TryTake(out var ack)) acks.Add(ack);
        Assert.Equal(4, acks.FindAll(ack => ack.Status == "canceled").Count);
        controller.Tick(ControllerTests.Now.AddMilliseconds(100)); Assert.Equal(1, game.Starts); Assert.Equal("", game.LastSubtitle);
    }

    [Theory][InlineData("typed")][InlineData("ptt")][InlineData("whisper")][InlineData("pin")]
    public void StartingAnotherInputKeepsTheSelectionAndCancelsTheOldScene(string mode)
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7)); Assert.True(input.StartPushToTalk(PttKey, false));
        var oldScene = input.SceneId; string? canceledScene = null, selected = game.Actor.ActorId;
        input.Cancel(null, canceled => canceledScene = canceled, () => selected = null, clearTarget: false);
        Assert.Equal(oldScene, canceledScene); Assert.Equal(game.Actor.ActorId, selected);
        Assert.False(input.Bound); Assert.False(input.PushToTalkHeld);
        var actor = game.Resolve(selected!);
        if (mode == "pin") { Assert.Null(PinPolicy.Check(actor, game.World, 7)); return; }
        Assert.True(input.TryBind(actor, game.World, 7)); Assert.NotEqual(oldScene, input.SceneId);
        if (mode == "typed") Assert.NotNull(input.TypedPayload("Hello"));
        else Assert.True(input.StartPushToTalk(mode == "whisper" ? WhisperKey : PttKey, mode == "whisper"));
    }

    [Theory]
    [InlineData(false, false, false, false)][InlineData(false, true, false, true)]
    [InlineData(false, false, true, false)][InlineData(false, true, true, false)]
    [InlineData(true, false, false, false)][InlineData(true, true, false, false)]
    [InlineData(true, false, true, true)][InlineData(true, true, true, true)]
    public void CancelKeyKeepsEscapeForPauseOutsideTyping(bool typing, bool configuredCancel, bool escape, bool expected)
    {
        Assert.Equal(expected, ConversationInput.ShouldCancel(typing, configuredCancel, escape));
    }

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
    [InlineData("mission")][InlineData("dead")][InlineData("combat")][InlineData("ownership")][InlineData("protected")]
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
