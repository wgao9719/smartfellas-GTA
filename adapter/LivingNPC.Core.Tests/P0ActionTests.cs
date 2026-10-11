using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public class P0ActionTests
{
    public static System.Collections.Generic.IEnumerable<object[]> SafetyCases()
    {
        foreach (var action in new[] { "follow_player", "go_to_safe_point", "wander_nearby", "flee_from_threat", "play_verified_gesture" })
            foreach (var state in new[] { "mission", "combat", "protected", "service", "ownership", "instance", "generation", "distance", "cancel" })
                yield return new object[] { action, state };
    }
    [Theory][MemberData(nameof(SafetyCases))]
    public void EveryNewActionUsesSharedAbortAndReleaseRules(string action, string state)
    {
        var game = new FakeGame();
        var controller = new CommandController(game, "ses", 7, "p1", ActionRegistry.Default.ActionIds);
        var command = ControllerTests.Action(); command.Payload["action_id"] = action;
        command.Payload["arguments"] = action == "play_verified_gesture" ? JObject.Parse("{'gesture_id':'wave'}") : new JObject();
        command.Payload["consent"] = true;
        controller.Commands.TryAdd(command); controller.Tick(ControllerTests.Now);
        Assert.True(controller.Acks.TryTake(out var started)); Assert.Equal("started", started.Status);
        switch (state)
        {
            case "mission": game.World.MissionActive = true; break;
            case "combat": game.Actor.InCombat = true; break;
            case "protected": game.Actor.Policy = ControlPolicy.Protected; break;
            case "service": game.Actor.Policy = ControlPolicy.ServiceLimited; break;
            case "ownership": game.Actor.Ownership = Ownership.GameScriptOwned; break;
            case "instance": game.Actor.RuntimeInstanceId = "replacement"; break;
            case "generation": game.Actor.Generation++; break;
            case "distance": game.Actor.Distance = 31; break;
            case "cancel": controller.Cancel(); break;
        }
        controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.True(controller.Acks.TryTake(out var terminal)); Assert.Equal("canceled", terminal.Status);
        Assert.Equal(state == "distance" || state == "cancel" ? 1 : 0, game.Releases);
        controller.Cancel(); Assert.False(controller.Acks.TryTake(out _));
    }
    [Theory][InlineData("follow_player")][InlineData("go_to_safe_point")][InlineData("wander_nearby")][InlineData("flee_from_threat")]
    public void MovementRequiresMobileActorAndExplicitConsent(string action)
    {
        var game = new FakeGame(); var command = ControllerTests.Action();
        command.Payload["action_id"] = action; command.Payload["arguments"] = new JObject();
        var governor = new SafetyGovernor(ActionRegistry.Default.ActionIds);
        Assert.Equal("consent_required", governor.Check(command, "ses", 7, "p1", game.World, game.Actor, ControllerTests.Now));
        command.Payload["consent"] = true; game.Actor.CanMove = false;
        Assert.Equal("actor_cannot_move", governor.Check(command, "ses", 7, "p1", game.World, game.Actor, ControllerTests.Now));
    }
    [Fact] public void NewTaskCompletionReleasesBeforeForgettingAndStopCannotReplaceWait()
    {
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", ActionRegistry.Default.ActionIds);
        var wait = ControllerTests.Action(); wait.Payload["action_id"] = "wait_here";
        controller.Commands.TryAdd(wait); controller.Tick(ControllerTests.Now); controller.Acks.TryTake(out _);
        var stop = ControllerTests.Action("stop"); stop.Payload["action_id"] = "stop_following"; stop.Payload["arguments"] = new JObject();
        controller.Commands.TryAdd(stop); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.True(controller.Acks.TryTake(out var rejected)); Assert.Equal("no_mod_follow_task", rejected.Reason);
        Assert.Equal(1, game.Starts); Assert.Equal(0, game.Releases);
        game.Observation = new EffectObservation { Status = EffectStatus.Completed };
        controller.Tick(ControllerTests.Now.AddMilliseconds(200)); Assert.Equal(1, game.Releases);
    }
    [Fact] public void ContinuingFollowPreservesBindingAndRepeatedPushToTalkStartsFreshCapture()
    {
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", ActionRegistry.Default.ActionIds);
        var input = new ConversationInput(); Assert.True(input.TryBind(game.Actor, game.World, 7));
        var scene = input.SceneId;
        var follow = ControllerTests.Action(); follow.Payload["action_id"] = "follow_player";
        follow.Payload["arguments"] = new JObject(); follow.Payload["consent"] = true;
        controller.Commands.TryAdd(follow); controller.Tick(ControllerTests.Now);
        Assert.True(input.CanContinueFollow(game.Actor, game.World, 7, controller));
        for (var i = 0; i < 2; i++)
        {
            Assert.True(input.StartPushToTalk(78, false));
            Assert.True(input.TryQueuePushToTalkStart(true));
            Assert.True(input.StopPushToTalk(78));
        }
        Assert.Equal(scene, input.SceneId);
        game.Actor.RuntimeInstanceId = "replacement";
        Assert.False(input.CanContinueFollow(game.Actor, game.World, 7, controller));
    }
    [Theory]
    [InlineData("follow_player", "{}")]
    [InlineData("go_to_safe_point", "{}")]
    [InlineData("wander_nearby", "{}")]
    [InlineData("flee_from_threat", "{}")]
    [InlineData("play_verified_gesture", "{ 'gesture_id': 'wave' }")]
    public void AdvertisedP0ActionsReachTheExecutor(string action, string arguments)
    {
        var game = new FakeGame();
        var controller = new CommandController(game, "ses", 7, "p1", ActionRegistry.Default.ActionIds);
        var command = ControllerTests.Action();
        command.Payload["action_id"] = action;
        command.Payload["arguments"] = JObject.Parse(arguments);
        command.Payload["consent"] = true;
        controller.Commands.TryAdd(command); controller.Tick(ControllerTests.Now);
        Assert.True(controller.Acks.TryTake(out var ack));
        Assert.Equal("started", ack.Status);
        Assert.Equal(1, game.Starts);
    }

    [Fact]
    public void StopFollowingReplacesOnlyTheActiveFollow()
    {
        var game = new FakeGame();
        var controller = new CommandController(game, "ses", 7, "p1", ActionRegistry.Default.ActionIds);
        var follow = ControllerTests.Action();
        follow.Payload["action_id"] = "follow_player";
        follow.Payload["arguments"] = new JObject(); follow.Payload["consent"] = true;
        controller.Commands.TryAdd(follow); controller.Tick(ControllerTests.Now);
        Assert.True(controller.Acks.TryTake(out var first)); Assert.Equal("started", first.Status);
        var stop = ControllerTests.Action("stop");
        stop.Payload["action_id"] = "stop_following"; stop.Payload["arguments"] = new JObject();
        controller.Commands.TryAdd(stop); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.Equal(2, game.Starts);
        Assert.True(controller.Acks.TryTake(out var canceled)); Assert.Equal("canceled", canceled.Status);
        Assert.True(controller.Acks.TryTake(out var started)); Assert.Equal("started", started.Status);
        Assert.False(controller.Acks.TryTake(out _));
    }
}
