using System;
using System.Linq;
using LivingNPC.Core;
using Newtonsoft.Json.Linq;
using Xunit;

namespace LivingNPC.Core.Tests;

public class NativeActionTests
{
    private static Command Command(string action, object args, bool consent = true, string id = "action")
    {
        var command = ControllerTests.Action(id);
        command.Payload["action_id"] = action; command.Payload["arguments"] = JObject.FromObject(args);
        command.Payload["consent"] = consent; return command;
    }
    private static Ack[] Drain(CommandController controller)
    {
        var result = new System.Collections.Generic.List<Ack>();
        while (controller.Acks.TryTake(out var ack)) result.Add(ack);
        return result.ToArray();
    }
    [Fact] public void CatalogIsExplicitAndDoesNotGrantMissingCapabilities()
    {
        Assert.Equal(ActionRegistry.Default.ActionIds.OrderBy(x => x), NativeActions.DevelopmentCapabilities.OrderBy(x => x));
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", Array.Empty<string>());
        controller.Commands.TryAdd(Command("follow_player", new { })); controller.Tick(ControllerTests.Now);
        Assert.Equal("capability_unverified", Assert.Single(Drain(controller)).Reason); Assert.Equal(0, game.Starts);
    }
    [Theory]
    [InlineData("follow_player", "{}")]
    [InlineData("run_to_point", "{\"point\":{\"x\":3,\"y\":0,\"z\":0}}")]
    [InlineData("enter_vehicle", "{\"target_id\":\"car\",\"seat\":\"passenger\"}")]
    [InlineData("fight_target", "{\"target_id\":\"player\"}")]
    public void ConsentIsRecheckedAtTheAdapter(string action, string json)
    {
        var actor = new FakeGame().Actor; var args = JObject.Parse(json);
        Assert.Equal("consent_required", NativeActions.Check(action, args, actor, false));
        Assert.Null(NativeActions.Check(action, args, actor, true));
    }
    [Fact] public void CurrentVehicleWeaponAndPointBoundsAreRequired()
    {
        var actor = new FakeGame().Actor;
        Assert.Equal("no_equipped_firearm", NativeActions.Check("shoot_at_target", JObject.FromObject(new { target_id = "player" }), actor, true));
        Assert.Equal("not_in_vehicle", NativeActions.Check("cruise_vehicle", new JObject(), actor, true));
        actor.InVehicle = true;
        Assert.Equal("not_driver", NativeActions.Check("cruise_vehicle", new JObject(), actor, true));
        Assert.Equal("vehicle_moving", NativeActions.Check("leave_vehicle", new JObject(), actor, true));
        actor.IsDriver = true; Assert.Null(NativeActions.Check("cruise_vehicle", new JObject(), actor, true));
        actor.InVehicle = false;
        Assert.Equal("point_outside_local_area", NativeActions.Check("run_to_point", JObject.FromObject(new { point = new { x = 16, y = 0, z = 0 } }), actor, true));
        Assert.Equal("argument_out_of_range", NativeActions.Check("play_verified_gesture", JObject.FromObject(new { gesture_id = "arbitrary_animation" }), actor, true));
    }
    [Fact] public void StoppingFollowReplacesOnlyTheOwnedFollowAction()
    {
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", NativeActions.DevelopmentCapabilities);
        controller.Commands.TryAdd(Command("follow_player", new { }, id: "follow")); controller.Tick(ControllerTests.Now); Drain(controller);
        game.Actor.ModFollowing = true;
        controller.Commands.TryAdd(Command("stop_following", new { }, false, "stop")); controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        var results = Drain(controller);
        Assert.Contains(results, a => a.CommandId == "follow" && a.Status == "canceled");
        Assert.Contains(results, a => a.CommandId == "stop" && a.Status == "started"); Assert.Equal(2, game.Starts);
    }
    [Fact] public void OwnedCombatCanBeObservedAndCanceled()
    {
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", NativeActions.DevelopmentCapabilities);
        controller.Commands.TryAdd(Command("fight_target", new { target_id = "player" })); controller.Tick(ControllerTests.Now); Drain(controller);
        game.Actor.InCombat = true; controller.Tick(ControllerTests.Now.AddMilliseconds(100)); Assert.Empty(Drain(controller));
        controller.Cancel(); Assert.Equal(1, game.Releases); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void LeavingRadiusWithdrawsOwnedTask()
    {
        var game = new FakeGame(); var controller = new CommandController(game, "ses", 7, "p1", NativeActions.DevelopmentCapabilities);
        controller.Commands.TryAdd(Command("wander_nearby", new { })); controller.Tick(ControllerTests.Now); Drain(controller);
        game.Actor.Distance = 31; controller.Tick(ControllerTests.Now.AddMilliseconds(100));
        Assert.Equal(1, game.Releases); Assert.Equal("canceled", Assert.Single(Drain(controller)).Status);
    }
    [Fact] public void DevelopmentConsentReachesTypedAndVoiceInputOnlyWhenEnabled()
    {
        var game = new FakeGame(); var input = new ConversationInput();
        Assert.True(input.TryBind(game.Actor, game.World, 7));
        Assert.False(input.TypedPayload("follow me")!.ContainsKey("consent"));
        Assert.False(input.ControlPayload()!.ContainsKey("consent"));
        input.AllowActions = true;
        Assert.Equal(true, input.TypedPayload("follow me")!["consent"]);
        Assert.Equal(true, input.ControlPayload()!["consent"]);
    }
}
